"""API client for EVC-net charging stations."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import time
from typing import Any

import aiohttp
from yarl import URL

from .const import (
    AJAX_ENDPOINT,
    GRAPHQL_ENDPOINT,
    LOGIN_ENDPOINT,
    PLUGZ_APPLICATION_ID,
    EvcNetException,
)

_LOGGER = logging.getLogger(__name__)


class EvcNetApiClient:
    """API client for EVC-net."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialize the API client."""
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = session
        self._is_authenticated = False
        self._phpsessid: str | None = None
        self._serverid: str | None = None
        self._plugz_token: str | None = None
        self._plugz_token_expiry: float = 0.0

    def _graphql_endpoints(self) -> list[str]:
        """Return preferred GraphQL endpoints in failover order."""
        endpoints = [GRAPHQL_ENDPOINT, f"{self.base_url}/graphql"]
        unique: list[str] = []
        for endpoint in endpoints:
            if endpoint not in unique:
                unique.append(endpoint)
        return unique

    async def _post_graphql_json(
        self,
        endpoint: str,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> tuple[int, dict[str, Any], str, str]:
        """POST a GraphQL payload and parse JSON body robustly.

        Returns (status, payload, content_type, raw_text).
        """
        try:
            async with self.session.post(
                endpoint,
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                status = response.status
                content_type = response.headers.get("Content-Type", "")
                raw_text = await response.text()
        except asyncio.TimeoutError as err:
            raise EvcNetException(
                f"GraphQL request timeout at {endpoint}"
            ) from err

        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as err:
            raise EvcNetException(
                "GraphQL returned non-JSON "
                f"(status {status}, content-type: {content_type})"
            ) from err

        return status, payload, content_type, raw_text

    async def authenticate(self) -> bool:
        """Authenticate with EVC-net using the web portal."""
        _LOGGER.debug("Start authentication process")

        if await self._standard_login():
            return True

        _LOGGER.info("Standard login failed, switching to browser emulation fallback")
        return await self._browser_emulation_login()

    async def _standard_login(self) -> bool:
        """Standard authentication with the EVC-net API."""
        url = f"{self.base_url}{LOGIN_ENDPOINT}"
        data = {
            "emailField": self.username,
            "passwordField": self.password,
        }

        try:
            async with self.session.post(
                url,
                data=data,
                allow_redirects=False,
            ) as response:
                _LOGGER.debug("Login response status: %s", response.status)

                if response.status != 302:
                    _LOGGER.error(
                        "Authentication failed with status %s (expected 302)",
                        response.status,
                    )
                    response_text = await response.text()
                    _LOGGER.debug("Response: %s", response_text[:200])
                    return False

                if hasattr(self.session, "cookie_jar"):
                    cookies = self.session.cookie_jar.filter_cookies(URL(self.base_url))
                    for cookie in cookies.values():
                        if cookie.key == "PHPSESSID":
                            self._phpsessid = cookie.value
                        elif cookie.key == "SERVERID":
                            self._serverid = cookie.value

                if self._phpsessid:
                    self._is_authenticated = True
                    _LOGGER.info("Successfully authenticated with EVC-net")
                    return True

                _LOGGER.error("No PHPSESSID found in response cookies")
                return False

        except aiohttp.ClientError as err:
            _LOGGER.error("Error during authentication: %s", err)
            return False

    async def _browser_emulation_login(self) -> bool:
        """Fallback login using a browser-like flow."""
        url_login = f"{self.base_url}{LOGIN_ENDPOINT}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url_login) as resp:
                    await resp.text()

                data = aiohttp.FormData()
                data.add_field("emailField", self.username)
                data.add_field("passwordField", self.password)
                data.add_field("Login", "Aanmelden")

                headers = {
                    "Origin": self.base_url,
                    "Referer": url_login,
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
                }

                async with session.post(
                    url_login,
                    data=data,
                    headers=headers,
                    allow_redirects=False,
                ) as resp:
                    if resp.status not in (302, 307):
                        return False

                    cookies = session.cookie_jar.filter_cookies(URL(url_login))
                    sid = cookies.get("SERVERID")
                    php = cookies.get("PHPSESSID")
                    if sid and php:
                        self._serverid = sid.value
                        self._phpsessid = php.value
                        self._is_authenticated = True
                        _LOGGER.info("Successfully completed browser-emulation login")
                        return True

        except aiohttp.ClientError as err:
            _LOGGER.error("Critical error during browser-emulation login: %s", err)
            return False

        _LOGGER.error("Browser-emulation login failed: no valid cookies received")
        return False

    async def _make_ajax_request(self, requests_payload: dict) -> dict[str, Any]:
        """Make an AJAX request to the EVC-net API."""
        if not self._is_authenticated:
            if not await self.authenticate():
                raise EvcNetException("Failed to authenticate")

        url = f"{self.base_url}{AJAX_ENDPOINT}"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        cookies = {
            "PHPSESSID": self._phpsessid,
            "SERVERID": self._serverid if self._serverid else "",
        }
        data = {"requests": json.dumps(requests_payload)}

        try:
            async with self.session.post(
                url,
                headers=headers,
                cookies=cookies,
                data=data,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                content_type = response.headers.get("Content-Type", "")
                response_text = await response.text()

                if response.status in (401, 302):
                    _LOGGER.info(
                        "Session expired (status %s), re-authenticating",
                        response.status,
                    )
                    self._is_authenticated = False
                    if await self.authenticate():
                        return await self._make_ajax_request(requests_payload)
                    raise EvcNetException("Re-authentication failed")

                if response.status != 200:
                    _LOGGER.error(
                        "Request failed with status %s, response: %s",
                        response.status,
                        response_text[:200],
                    )
                    raise EvcNetException(
                        f"Request failed with status {response.status}"
                    )

                if (
                    "application/json" not in content_type
                    and not response_text.strip().startswith("[")
                    and not response_text.strip().startswith("{")
                ):
                    _LOGGER.warning(
                        "Unexpected response (content-type: %s), session may be expired",
                        content_type,
                    )
                    self._is_authenticated = False
                    if await self.authenticate():
                        return await self._make_ajax_request(requests_payload)
                    raise EvcNetException(
                        "Re-authentication failed or still getting non-JSON response"
                    )

                try:
                    return json.loads(response_text)
                except json.JSONDecodeError as err:
                    _LOGGER.error("Failed to decode JSON response: %s", err)
                    _LOGGER.debug("Response text: %s", response_text[:500])
                    raise EvcNetException("Invalid JSON response") from err

        except TimeoutError as err:
            _LOGGER.error("Request timeout: %s", err)
            raise EvcNetException("Request timeout") from err
        except aiohttp.ClientConnectorError as err:
            _LOGGER.error("Connection error: %s", err)
            raise EvcNetException("Cannot connect to EVC-net") from err
        except aiohttp.ClientError as err:
            _LOGGER.error("HTTP client error: %s", err)
            raise EvcNetException(f"HTTP error: {err}") from err

    @staticmethod
    def _parse_jwt_expiry(token: str) -> float:
        """Return monotonic time when this JWT expires (60 s margin)."""
        import base64  # noqa: PLC0415
        import datetime  # noqa: PLC0415
        try:
            payload_b64 = token.split(".")[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.b64decode(payload_b64))
            exp_unix = payload.get("exp", 0)
            if exp_unix:
                remaining = exp_unix - datetime.datetime.now(datetime.timezone.utc).timestamp()
                return time.monotonic() + max(0.0, remaining - 60.0)
        except Exception:  # noqa: BLE001
            pass
        return time.monotonic() + 3600.0

    async def _plugz_authenticate(self) -> bool:
        """Obtain a Plugz platform JWT using email + password via GraphQL."""
        if self._plugz_token and time.monotonic() < self._plugz_token_expiry:
            return True

        _LOGGER.debug("Authenticating with Plugz platform")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "PlugzApp/406 CFNetwork/3860.500.112 Darwin/25.4.0",
            "plugz-application-id": PLUGZ_APPLICATION_ID,
        }

        # Try to discover available mutations via introspection (unauthenticated).
        # Kept only in debug builds; remove or comment out for production.
        if _LOGGER.isEnabledFor(logging.DEBUG):
            for endpoint in self._graphql_endpoints():
                try:
                    status, intro, _, _ = await self._post_graphql_json(
                        endpoint,
                        {"query": "{__schema{mutationType{fields{name}}}}"},
                        headers,
                    )
                    if status == 200 and intro.get("data") and intro["data"].get("__schema"):
                        names = [
                            f["name"]
                            for f in intro["data"]["__schema"]["mutationType"]["fields"]
                        ]
                        _LOGGER.debug(
                            "Plugz mutations available at %s: %s",
                            endpoint,
                            names,
                        )
                        break
                except Exception:  # noqa: BLE001
                    continue

        # Candidate mutations (most likely first)
        candidates = [
            {
                "query": (
                    "mutation L($e:String!,$p:String!){"
                    "login(email:$e,password:$p){access_token}}"
                ),
                "variables": {"e": self.username, "p": self.password},
                "path": ["login", "access_token"],
            },
        ]

        for endpoint in self._graphql_endpoints():
            for attempt in candidates:
                try:
                    status, data, _, _ = await self._post_graphql_json(
                        endpoint,
                        {
                            "query": attempt["query"],
                            "variables": attempt["variables"],
                        },
                        headers,
                    )
                    if status != 200:
                        _LOGGER.debug(
                            "Plugz auth status %s at %s",
                            status,
                            endpoint,
                        )
                        continue

                    if "errors" in data:
                        _LOGGER.debug(
                            "Plugz mutation attempt failed at %s: %s",
                            endpoint,
                            data["errors"],
                        )
                        continue

                    result = data.get("data", {})
                    for key in attempt["path"]:
                        result = result.get(key) if isinstance(result, dict) else None
                    if result:
                        self._plugz_token = result
                        self._plugz_token_expiry = self._parse_jwt_expiry(result)
                        _LOGGER.info(
                            "Successfully authenticated with Plugz platform via %s",
                            endpoint,
                        )
                        return True
                except EvcNetException as err:
                    _LOGGER.debug(
                        "Plugz auth attempt error at %s: %s",
                        endpoint,
                        err,
                    )
                except aiohttp.ClientError as err:
                    _LOGGER.debug(
                        "Plugz auth client error at %s: %s",
                        endpoint,
                        err,
                    )

        _LOGGER.warning(
            "All Plugz authentication attempts failed — "
            "check HA logs (debug level) for available mutations"
        )
        return False

    async def _graphql_query(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a GraphQL query against the LMS/Plugz platform API."""
        if not await self._plugz_authenticate():
            raise EvcNetException("Plugz authentication failed")

        headers = {
            "Authorization": f"Bearer {self._plugz_token}",
            "Content-Type": "application/json",
            "User-Agent": "PlugzApp/406 CFNetwork/3860.500.112 Darwin/25.4.0",
            "plugz-application-id": PLUGZ_APPLICATION_ID,
        }
        body: dict[str, Any] = {"query": query}
        if variables:
            body["variables"] = variables

        transient_statuses = {429, 502, 503, 504}
        max_attempts = 3
        retry_delay = 1.0
        last_error: Exception | None = None

        for endpoint in self._graphql_endpoints():
            attempts = 0
            while attempts < max_attempts:
                attempts += 1
                try:
                    status, payload, content_type, raw_text = await self._post_graphql_json(
                        endpoint,
                        body,
                        headers,
                    )

                    if status == 401:
                        self._plugz_token = None
                        self._plugz_token_expiry = 0.0
                        if not await self._plugz_authenticate():
                            raise EvcNetException(
                                "Plugz re-authentication failed after 401"
                            )
                        headers["Authorization"] = f"Bearer {self._plugz_token}"
                        continue

                    if status in transient_statuses:
                        if attempts < max_attempts:
                            _LOGGER.debug(
                                "GraphQL transient status %s from %s (attempt %s/%s), retrying",
                                status,
                                endpoint,
                                attempts,
                                max_attempts,
                            )
                            await asyncio.sleep(retry_delay)
                            retry_delay = min(retry_delay * 2, 4.0)
                            continue
                        raise EvcNetException(
                            "GraphQL transient failure "
                            f"(status {status}, endpoint: {endpoint}, content-type: {content_type})"
                        )

                    if status >= 400:
                        raise EvcNetException(
                            f"GraphQL HTTP error {status} from {endpoint}"
                        )

                    if payload.get("errors"):
                        _LOGGER.debug(
                            "GraphQL responded with errors at %s: %s",
                            endpoint,
                            payload.get("errors"),
                        )

                    return payload

                except EvcNetException as err:
                    last_error = err
                    # Retry only for transient-like non-JSON gateway failures.
                    if (
                        attempts < max_attempts
                        and "non-JSON" in str(err)
                        and "status 502" in str(err)
                    ):
                        _LOGGER.debug(
                            "GraphQL non-JSON gateway response from %s (attempt %s/%s), retrying",
                            endpoint,
                            attempts,
                            max_attempts,
                        )
                        await asyncio.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, 4.0)
                        continue

                    _LOGGER.debug(
                        "GraphQL attempt failed at %s (attempt %s/%s): %s",
                        endpoint,
                        attempts,
                        max_attempts,
                        err,
                    )
                    break
                except aiohttp.ClientError as err:
                    last_error = err
                    if attempts < max_attempts:
                        _LOGGER.debug(
                            "GraphQL client error at %s (attempt %s/%s): %s",
                            endpoint,
                            attempts,
                            max_attempts,
                            err,
                        )
                        await asyncio.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, 4.0)
                        continue
                    break

            # Reset retry delay when switching endpoint.
            retry_delay = 1.0

        if last_error:
            raise EvcNetException(f"GraphQL request error: {last_error}") from last_error
        raise EvcNetException("GraphQL request failed without a specific error")

    async def get_hcc_tariff(self, spot_id: str) -> dict[str, Any]:
        """Fetch Home Charging Compensation tariff for a charge station."""
        query = """
        query GetChargeStationOverview($id: ID!) {
          getChargeStationById(id: $id) {
            id
            homeChargingCompensation {
              hccEnabled
              hccTariff
            }
          }
        }
        """
        return await self._graphql_query(query, {"id": spot_id})

    async def get_active_transaction(self) -> dict[str, Any]:
        """Fetch the current active charging transaction (if any)."""
        query = """
        query LmsActiveTransaction {
          lmsActiveTransaction {
            updateDate
            startDate
            energyDelivered
            currency
            totalAmount
            vat
            durationCharging
            priceElements {
              type
              price
            }
            tariffId
            channelVisibleId
          }
        }
        """
        return await self._graphql_query(query)

    async def get_recent_transactions(self, spot_id: str) -> dict[str, Any]:
        """Fetch recent transactions for a charge station for VAT fallback."""
        now = datetime.now(timezone.utc)
        date_from = (now - timedelta(days=30)).isoformat()
        date_to = now.isoformat()
        query = """
        query GetChargingHistory($filters: TransactionFilter, $sort: TransactionSort) {
            getTransactions(filters: $filters, sort: $sort) {
                hasNext
                items {
                    id
                    startDate
                    transactionPrices {
                        type
                        vatPercentage
                        totalCost
                    }
                }
            }
        }
        """
        variables = {
            "filters": {
                "dateFrom": date_from,
                "dateTo": date_to,
                "itemsPerPage": 20,
                "page": 1,
                "chargeStation": {"id": spot_id},
            },
            "sort": {"lastUpdateDate": "desc"},
        }
        return await self._graphql_query(query, variables)

    async def get_charge_spots(self) -> dict[str, Any]:
        """Get list of charging spots."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\DashboardAsyncService",
                "method": "networkOverview",
                "params": {"mode": "id"},
            }
        }
        return await self._make_ajax_request(requests_payload)

    async def get_spot_total_energy_usage(self, recharge_spot_id: str) -> dict[str, Any]:
        """Get total energy usage of a specific charging spot."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\DashboardAsyncService",
                "method": "totalUsage",
                "params": {
                    "mode": "rechargeSpot",
                    "rechargeSpotIds": [recharge_spot_id],
                    "maxCache": 3600,
                },
            }
        }
        return await self._make_ajax_request(requests_payload)

    async def get_spot_overview(self, recharge_spot_id: str) -> dict[str, Any]:
        """Get detailed overview of a charging spot."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "overview",
                "params": {"rechargeSpotId": recharge_spot_id},
            }
        }
        return await self._make_ajax_request(requests_payload)

    async def get_customer_id(self, recharge_spot_id: str) -> dict[str, Any]:
        """Get customer id details for a charging spot."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "userAccess",
                "params": {"rechargeSpotId": recharge_spot_id},
            }
        }
        return await self._make_ajax_request(requests_payload)

    async def get_card_id(self, recharge_spot_id: str, customer_id: str) -> dict[str, Any]:
        """Get card details for a charging spot and customer."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "cardAccess",
                "params": {
                    "rechargeSpotId": recharge_spot_id,
                    "customerId": customer_id,
                },
            }
        }
        return await self._make_ajax_request(requests_payload)

    async def start_charging(
        self,
        recharge_spot_id: str,
        customer_id: str,
        card_id: str,
        channel: str,
    ) -> dict[str, Any]:
        """Start a charging session."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "action",
                "params": {
                    "action": "StartTransaction",
                    "rechargeSpotId": recharge_spot_id,
                    "clickedButtonId": 0,
                    "channel": channel,
                    "customer": customer_id,
                    "card": card_id,
                },
            }
        }
        return await self._make_ajax_request(requests_payload)

    async def stop_charging(self, recharge_spot_id: str, channel: str) -> dict[str, Any]:
        """Stop a charging session."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "action",
                "params": {
                    "action": "StopTransaction",
                    "rechargeSpotId": recharge_spot_id,
                    "clickedButtonId": 0,
                    "channel": channel,
                },
            }
        }
        return await self._make_ajax_request(requests_payload)

    async def soft_reset(self, recharge_spot_id: str, channel: str) -> dict[str, Any]:
        """Perform a soft reset on a charging station."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "action",
                "params": {
                    "action": "SoftReset",
                    "rechargeSpotId": recharge_spot_id,
                    "clickedButtonId": 0,
                    "channel": channel,
                },
            }
        }
        return await self._make_ajax_request(requests_payload)

    async def hard_reset(self, recharge_spot_id: str, channel: str) -> dict[str, Any]:
        """Perform a hard reset on a charging station."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "action",
                "params": {
                    "action": "HardReset",
                    "rechargeSpotId": recharge_spot_id,
                    "clickedButtonId": 0,
                    "channel": channel,
                },
            }
        }
        return await self._make_ajax_request(requests_payload)

    async def unlock_connector(self, recharge_spot_id: str, channel: str) -> dict[str, Any]:
        """Unlock the connector on a charging station."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "action",
                "params": {
                    "action": "UnlockConnector",
                    "rechargeSpotId": recharge_spot_id,
                    "clickedButtonId": 0,
                    "channel": channel,
                },
            }
        }
        return await self._make_ajax_request(requests_payload)

    async def block(self, recharge_spot_id: str, channel: str) -> dict[str, Any]:
        """Block a charging station."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "action",
                "params": {
                    "action": "Block",
                    "rechargeSpotId": recharge_spot_id,
                    "clickedButtonId": 0,
                    "channel": channel,
                },
            }
        }
        return await self._make_ajax_request(requests_payload)

    async def unblock(self, recharge_spot_id: str, channel: str) -> dict[str, Any]:
        """Unblock a charging station."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "action",
                "params": {
                    "action": "Unblock",
                    "rechargeSpotId": recharge_spot_id,
                    "clickedButtonId": 0,
                    "channel": channel,
                },
            }
        }
        return await self._make_ajax_request(requests_payload)

    async def retrieve_status(
        self, recharge_spot_id: str, channel: str | None = None
    ) -> dict[str, Any]:
        """Trigger a backend status retrieval action for a charging station."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "action",
                "params": {
                    "action": "GetStatus",
                    "rechargeSpotId": recharge_spot_id,
                    "clickedButtonId": 1,
                },
            }
        }
        return await self._make_ajax_request(requests_payload)

    async def get_spot_log(
        self,
        recharge_spot_id: str,
        channel: str,
        detailed: bool = False,
        log_id: str | None = None,
        extend: bool = False,
    ) -> dict[str, Any]:
        """Retrieve the log entries for a charging station."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "log",
                "params": {
                    "rechargeSpotId": recharge_spot_id,
                    "channel": channel,
                    "detailed": detailed,
                    "id": log_id,
                    "extend": extend,
                },
            }
        }
        return await self._make_ajax_request(requests_payload)
