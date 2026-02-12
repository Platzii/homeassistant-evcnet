"""API client for EVC-net charging stations."""
import asyncio
import json
import logging
import time
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import AJAX_ENDPOINT, LOGIN_ENDPOINT

_LOGGER = logging.getLogger(__name__)


class EvcNetApiClient:
    """API client for EVC-net."""

    def __init__(self, base_url: str, username: str, password: str, session: aiohttp.ClientSession) -> None:
        """Initialize the API client."""
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = session
        self._is_authenticated = False
        self._phpsessid = None
        self._serverid = None
        self._auth_lock = asyncio.Lock()  # Prevent concurrent authentication
        self._last_auth_attempt = 0  # Track last authentication time
        self._auth_backoff = 30  # Minimum seconds between auth attempts

    async def authenticate(self) -> bool:
        """Authenticate with the EVC-net API."""
        # Use lock to prevent multiple concurrent authentication attempts
        async with self._auth_lock:
            # Check if we authenticated recently (backoff mechanism)
            time_since_last_auth = time.time() - self._last_auth_attempt
            if self._is_authenticated and time_since_last_auth < self._auth_backoff:
                _LOGGER.debug(
                    "Skipping authentication, last attempt was %.1f seconds ago",
                    time_since_last_auth
                )
                return True

            self._last_auth_attempt = time.time()

            url = f"{self.base_url}{LOGIN_ENDPOINT}"


            data = {
                "emailField": self.username,
                "passwordField": self.password,
            }

            # Add browser-like headers
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Referer": url,
                "Origin": self.base_url,
                "Connection": "keep-alive",
            }

            _LOGGER.debug("Login request: POST %s", url)
            _LOGGER.debug("Login request headers: %s", headers)
            # Never log credentials; redact email and password
            _LOGGER.debug(
                "Request data: %s",
                {k: ("***" if k in ("emailField", "passwordField") else v) for k, v in data.items()},
            )

            try:
                # Don't follow redirects automatically, we need to capture cookies
                async with self.session.post(
                    url,
                    data=data,
                    headers=headers,
                    allow_redirects=False  # Don't follow redirects
                ) as response:
                    _LOGGER.debug("Login response: POST %s -> %s", url, response.status)
                    _LOGGER.debug("Login response headers: %s", dict(response.headers))

                    # Login returns 302 redirect
                    if response.status == 302:
                        _LOGGER.debug("Received expected 302 redirect")
                        _LOGGER.debug("Location header: %s", response.headers.get('Location', 'Not present'))

                        # Check cookies after initial POST
                        if hasattr(self.session, 'cookie_jar'):
                            cookies = self.session.cookie_jar.filter_cookies(self.base_url)
                            _LOGGER.debug("Total cookies in jar for %s: %d", self.base_url, len(cookies))
                            for cookie in cookies.values():
                                _LOGGER.debug(
                                    "Cookie found: %s = %s",
                                    cookie.key,
                                    cookie.value[:5] + "..." if cookie.key == "PHPSESSID" and cookie.value else (cookie.value or "None"),
                                )
                                if cookie.key == 'PHPSESSID':
                                    self._phpsessid = cookie.value
                                    _LOGGER.debug(
                                        "Found PHPSESSID in cookie jar: %s...",
                                        cookie.value[:5] if cookie.value else "None",
                                    )
                                if cookie.key == 'SERVERID':
                                    self._serverid = cookie.value
                                    _LOGGER.debug(
                                        "Found SERVERID in cookie jar: %s",
                                        cookie.value or "None",
                                    )
                        else:
                            _LOGGER.error("Session does not have cookie_jar attribute!")

                        # If PHPSESSID not found, try to follow the redirect manually
                        if not self._phpsessid and 'Location' in response.headers:
                            redirect_url = response.headers['Location']
                            if not redirect_url.startswith('http'):
                                # Relative redirect
                                redirect_url = self.base_url.rstrip('/') + redirect_url
                            _LOGGER.debug("Login redirect: GET %s", redirect_url)
                            async with self.session.get(redirect_url, headers=headers, allow_redirects=False) as redirect_response:
                                _LOGGER.debug("Login redirect response: GET %s -> %s", redirect_url, redirect_response.status)
                                _LOGGER.debug("Redirect response headers: %s", dict(redirect_response.headers))
                                if hasattr(self.session, 'cookie_jar'):
                                    cookies = self.session.cookie_jar.filter_cookies(self.base_url)
                                    _LOGGER.debug("Total cookies in jar after redirect for %s: %d", self.base_url, len(cookies))
                                    for cookie in cookies.values():
                                        _LOGGER.debug(
                                            "Cookie found after redirect: %s = %s",
                                            cookie.key,
                                            cookie.value[:5] + "..." if cookie.key == "PHPSESSID" and cookie.value else (cookie.value or "None"),
                                        )
                                        if cookie.key == 'PHPSESSID':
                                            self._phpsessid = cookie.value
                                            _LOGGER.debug(
                                                "Found PHPSESSID in cookie jar after redirect: %s...",
                                                cookie.value[:5] if cookie.value else "None",
                                            )
                                        if cookie.key == 'SERVERID':
                                            self._serverid = cookie.value
                                            _LOGGER.debug(
                                                "Found SERVERID in cookie jar after redirect: %s",
                                                cookie.value or "None",
                                            )

                        if self._phpsessid:
                            self._is_authenticated = True
                            _LOGGER.info("Successfully authenticated with EVC-net")
                            _LOGGER.debug(
                                "PHPSESSID: %s... (length %d)",
                                self._phpsessid[:5] if self._phpsessid else "None",
                                len(self._phpsessid) if self._phpsessid else 0,
                            )
                            return True

                        _LOGGER.error("No PHPSESSID found after 302 redirect and manual follow-up")
                        _LOGGER.error("This suggests a cookie handling or login flow issue")
                        _LOGGER.debug("All response headers: %s", dict(response.headers))
                        return False
                    else:
                        _LOGGER.error("Authentication failed with status %s (expected 302)", response.status)
                        response_text = await response.text()
                        _LOGGER.error("Response body (first 500 chars): %s", response_text[:500])
                        _LOGGER.debug("Full response headers: %s", dict(response.headers))
                        # Check for common error patterns
                        if "invalid" in response_text.lower() or "incorrect" in response_text.lower():
                            _LOGGER.error("Response suggests invalid credentials")
                        if response.status == 200:
                            _LOGGER.error("Status 200 suggests credentials were not accepted (should be 302)")
                        return False
            except aiohttp.ClientError as err:
                _LOGGER.error("Error during authentication: %s", err)
                return False
            except Exception as err:
                _LOGGER.error("Unexpected error during authentication: %s", err, exc_info=True)
                return False

    async def _make_ajax_request(self, requests_payload: dict, _retry_count: int = 0) -> dict[str, Any]:
        """Make an AJAX request to the EVC-net API.

        Args:
            requests_payload: The payload to send
            _retry_count: Internal retry counter to prevent infinite recursion
        """
        # Prevent infinite recursion - allow only 1 retry
        if _retry_count > 1:
            raise Exception("Max retries exceeded for API request")

        if not self._is_authenticated:
            if not await self.authenticate():
                raise Exception("Failed to authenticate")

        url = f"{self.base_url}{AJAX_ENDPOINT}"

        # Prepare headers with cookie
        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }

        cookies = {
            "PHPSESSID": self._phpsessid,
            "SERVERID": self._serverid if self._serverid else ""
        }

        # Convert requests payload to JSON string and send as form data
        data = {
            "requests": json.dumps(requests_payload)
        }

        handler = requests_payload.get("0", {}).get("handler", "unknown")
        method = requests_payload.get("0", {}).get("method", "unknown")
        _LOGGER.debug("AJAX request: POST %s [handler=%s, method=%s]", url, handler, method)
        _LOGGER.debug("AJAX request payload: %s", requests_payload)
        _LOGGER.debug("PHPSESSID present: %s", bool(self._phpsessid))

        try:
            async with self.session.post(url, headers=headers, cookies=cookies, data=data) as response:
                _LOGGER.debug(
                    "EVC-net API: %s.%s -> %s",
                    handler.split("\\")[-1] if "\\" in handler else handler,
                    method,
                    response.status,
                )
                _LOGGER.debug("AJAX response: POST %s -> %s", url, response.status)
                _LOGGER.debug("Response content-type: %s", response.headers.get("Content-Type", "unknown"))
                # Check content type before trying to parse JSON
                content_type = response.headers.get('Content-Type', '')

                if response.status == 200:
                    if 'application/json' in content_type or 'text/html' in content_type:
                        # Try to parse as JSON first
                        try:
                            response_text = await response.text()

                            # Check if response looks like JSON
                            if response_text.strip().startswith('[') or response_text.strip().startswith('{'):
                                return json.loads(response_text)
                            else:
                                # It's HTML, session expired
                                _LOGGER.warning(
                                    "Received HTML instead of JSON (status %s, content-type: %s), "
                                    "session likely expired. Re-authenticating... (retry %d)",
                                    response.status,
                                    content_type,
                                    _retry_count
                                )
                                _LOGGER.debug("HTML response (first 300 chars): %s", response_text[:300])
                                self._is_authenticated = False

                                # Try to re-authenticate
                                if await self.authenticate():
                                    # Retry the request once with incremented counter
                                    return await self._make_ajax_request(requests_payload, _retry_count + 1)

                                raise Exception("Re-authentication failed or still getting HTML response")
                        except json.JSONDecodeError as err:
                            _LOGGER.error("Failed to decode JSON response: %s", err)
                            _LOGGER.debug("Response text: %s", response_text[:500])
                            raise
                    else:
                        raise Exception(f"Unexpected content type: {content_type}")

                elif response.status in [401, 302]:
                    # Session expired, re-authenticate
                    _LOGGER.info(
                        "Session expired (status %s), re-authenticating (retry %d)",
                        response.status,
                        _retry_count,
                    )
                    self._is_authenticated = False
                    if await self.authenticate():
                        # Retry the request with incremented counter
                        return await self._make_ajax_request(requests_payload, _retry_count + 1)
                    raise Exception("Re-authentication failed")
                else:
                    response_text = await response.text()
                    _LOGGER.error(
                        "Request failed with status %s, response: %s",
                        response.status,
                        response_text[:200]
                    )
                    raise Exception(f"Request failed with status {response.status}")
        except aiohttp.ClientTimeout as err:
            _LOGGER.error("Request timeout: %s", err)
            raise Exception("Request timeout") from err
        except aiohttp.ClientConnectorError as err:
            _LOGGER.error("Connection error: %s", err)
            raise Exception("Cannot connect to EVC-net") from err
        except aiohttp.ClientError as err:
            _LOGGER.error("HTTP client error: %s", err)
            raise Exception(f"HTTP error: {err}") from err

    async def get_charge_spots(self) -> dict[str, Any]:
        """Get list of charging spots."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\DashboardAsyncService",
                "method": "networkOverview",
                "params": {
                    "mode": "id"
                }
            }
        }

        return await self._make_ajax_request(requests_payload)

    async def get_spot_total_energy_usage(self, recharge_spot_id: str) -> dict[str, Any]:
        """Get total energy usage of a specific charging spot."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\DashboardAsyncService",
                "method":"totalUsage",
                "params":{
                    "mode":"rechargeSpot",
                    "rechargeSpotIds": [recharge_spot_id],
                    "maxCache":3600
                }
            }
        }

        return await self._make_ajax_request(requests_payload)

    async def get_spot_overview(self, recharge_spot_id: str) -> dict[str, Any]:
        """Get detailed overview of a charging spot."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\RechargeSpotsAsyncService",
                "method": "overview",
                "params": {
                    "rechargeSpotId": recharge_spot_id
                }
            }
        }

        return await self._make_ajax_request(requests_payload)

    async def start_charging(self, recharge_spot_id: str, customer_id: str, card_id: str, channel: str) -> dict[str, Any]:
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
                    "card": card_id
                }
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
                    "channel": channel
                }
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
                    "channel": channel
                }
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
                    "channel": channel
                }
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
                    "channel": channel
                }
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
                    "channel": channel
                }
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
                    "channel": channel
                }
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
