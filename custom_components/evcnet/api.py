"""API client for the 50five / Last Mile Solutions GraphQL API.

This module has no Home Assistant dependencies so it can be used and tested standalone.
"""
import asyncio
import logging
import time
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

GRAPHQL_URL = "https://lms.servicelayer.platform-01.plugz.dev/graphql"
APPLICATION_ID = "com.plugz.app50five"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Seconds before token expiry at which we proactively log in again
TOKEN_EXPIRY_MARGIN = 300

STATION_FIELDS = """
    id
    name
    commId
    reference
    state
    softwareVersion
    physicalNumber
    gpsAddress
    gpsCity
    lastCommunicationDate
    manufacturerType { vendor model }
    accessOptions { accessType authorizationMode chargeCard publishedOnMap }
    channels {
        id
        channelNo
        evseId
        globalStatus
        statuses
        lastStatusChangeDate
        power
        amp
        phases
        connectorType
        capabilities
    }
"""

TRANSACTION_FIELDS = """
    transactionId
    transactionStatus
    energyDelivered
    durationCharging
    durationIdle
    startDate
    endDate
    updateDate
    channelId
    evseId
    loyaltyCardIdentifier
    loyaltyCardType
    totalAmount
    currency
"""

# Everything the coordinator needs on each poll, in a single request
POLL_QUERY = f"""
query Poll($start: Float!, $end: Float!) {{
    getCustomerChargeStations {{ {STATION_FIELDS} }}
    lmsActiveTransaction {{ {TRANSACTION_FIELDS} }}
    lmsTransactions(startResult: $start, endResult: $end) {{
        hasMore
        items {{ {TRANSACTION_FIELDS} }}
    }}
}}
"""

TRANSACTIONS_QUERY = f"""
query Transactions($start: Float!, $end: Float!) {{
    lmsTransactions(startResult: $start, endResult: $end) {{
        hasMore
        items {{ {TRANSACTION_FIELDS} }}
    }}
}}
"""

CARDS_QUERY = """
query Cards {
    getCards(filters: {itemsPerPage: 50}) {
        items { id internalId externalId contractId state type info }
    }
}
"""

LOGIN_MUTATIONS = (
    "mutation Login($email: String!, $password: String!) "
    "{ loginFiftyFive(email: $email, password: $password) { access_token expires_in token_type } }",
    # Deprecated, but kept as fallback in case loginFiftyFive is unavailable for an account
    "mutation Login($email: String!, $password: String!) "
    "{ login(email: $email, password: $password) { access_token expires_in token_type } }",
)


class EvcNetError(Exception):
    """Base error for the API client."""


class EvcNetAuthError(EvcNetError):
    """Authentication failed (invalid credentials or rejected token)."""


class EvcNetConnectionError(EvcNetError):
    """The API could not be reached."""


def _is_auth_error(message: str) -> bool:
    message = message.lower()
    return "not authorized" in message or "unauthenticated" in message or "invalid credentials" in message


class EvcNetApiClient:
    """API client for the 50five GraphQL API."""

    def __init__(self, username: str, password: str, session: aiohttp.ClientSession) -> None:
        """Initialize the API client."""
        self.username = username
        self.password = password
        self.session = session
        self._access_token: str | None = None
        self._token_expires_at = 0.0
        self._auth_lock = asyncio.Lock()

    async def _post(self, query: str, variables: dict[str, Any] | None, token: str | None) -> dict[str, Any]:
        """Send a GraphQL request and return the decoded JSON body."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "plugz-application-id": APPLICATION_ID,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with self.session.post(
                GRAPHQL_URL,
                json={"query": query, "variables": variables or {}},
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                if response.status == 401:
                    raise EvcNetAuthError("Unauthorized (HTTP 401)")
                if response.status >= 400:
                    text = await response.text()
                    raise EvcNetError(f"HTTP {response.status}: {text[:200]}")
                return await response.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError) as err:
            raise EvcNetConnectionError(f"Cannot connect to API: {err}") from err

    async def authenticate(self, force: bool = False) -> bool:
        """Log in and store the access token. Returns False on invalid credentials."""
        async with self._auth_lock:
            if not force and self._access_token and time.time() < self._token_expires_at:
                return True

            variables = {"email": self.username, "password": self.password}
            last_error = "unknown error"
            for mutation in LOGIN_MUTATIONS:
                body = await self._post(mutation, variables, token=None)
                data = body.get("data") or {}
                result = data.get("loginFiftyFive") or data.get("login")
                if result and result.get("access_token"):
                    self._access_token = result["access_token"]
                    expires_in = result.get("expires_in") or 3600
                    self._token_expires_at = time.time() + max(60, int(expires_in) - TOKEN_EXPIRY_MARGIN)
                    _LOGGER.debug("Logged in, token valid for %s seconds", expires_in)
                    return True
                errors = body.get("errors") or []
                last_error = "; ".join(e.get("message", "") for e in errors) or last_error
                _LOGGER.debug("Login attempt failed: %s", last_error)
                if any(_is_auth_error(e.get("message", "")) for e in errors):
                    # Credentials were rejected; don't hammer the API with the fallback
                    break

            _LOGGER.error("Authentication failed: %s", last_error)
            self._access_token = None
            return False

    async def query(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        optional_fields: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Run an authenticated GraphQL query and return its data.

        Errors on fields listed in optional_fields are logged and those fields set to None;
        errors on any other field raise EvcNetError.
        """
        for attempt in range(2):
            if not await self.authenticate(force=attempt > 0):
                raise EvcNetAuthError("Invalid credentials")

            try:
                body = await self._post(query, variables, self._access_token)
            except EvcNetAuthError:
                if attempt == 0:
                    continue
                raise

            errors = body.get("errors") or []
            if attempt == 0 and any(_is_auth_error(e.get("message", "")) for e in errors):
                _LOGGER.debug("Token rejected, logging in again")
                continue

            data = body.get("data") or {}
            fatal = []
            for error in errors:
                path = error.get("path") or []
                field = path[0] if path else None
                if field in optional_fields:
                    _LOGGER.debug("Ignoring error on optional field %s: %s", field, error.get("message"))
                    data[field] = None
                else:
                    fatal.append(error.get("message", "unknown error"))
            if fatal:
                raise EvcNetError("; ".join(fatal))
            return data

        raise EvcNetAuthError("Token rejected after re-authentication")

    async def poll(self, transactions: int = 25) -> dict[str, Any]:
        """Fetch stations, the active transaction and the most recent transactions."""
        return await self.query(
            POLL_QUERY,
            {"start": 0, "end": transactions},
            optional_fields=("lmsActiveTransaction", "lmsTransactions"),
        )

    async def get_charge_stations(self) -> list[dict[str, Any]]:
        """Return the charge stations owned by the logged-in customer."""
        data = await self.query(f"query Stations {{ getCustomerChargeStations {{ {STATION_FIELDS} }} }}")
        return data.get("getCustomerChargeStations") or []

    async def get_active_transaction(self) -> dict[str, Any] | None:
        """Return the active transaction for the logged-in user, if any."""
        data = await self.query(f"query Active {{ lmsActiveTransaction {{ {TRANSACTION_FIELDS} }} }}")
        return data.get("lmsActiveTransaction")

    async def get_transactions(self, start: int = 0, end: int = 25) -> tuple[list[dict[str, Any]], bool]:
        """Return a page of transactions (newest first) and whether more are available."""
        data = await self.query(TRANSACTIONS_QUERY, {"start": start, "end": end})
        result = data.get("lmsTransactions") or {}
        return result.get("items") or [], bool(result.get("hasMore"))

    async def get_all_transactions(self, page_size: int = 100, max_pages: int = 50) -> list[dict[str, Any]]:
        """Return all transactions by walking through the pages."""
        items: list[dict[str, Any]] = []
        for page in range(max_pages):
            start = page * page_size
            page_items, has_more = await self.get_transactions(start, start + page_size)
            items.extend(page_items)
            if not has_more or not page_items:
                break
        return items

    async def get_cards(self) -> list[dict[str, Any]]:
        """Return the charge cards of the logged-in customer."""
        data = await self.query(CARDS_QUERY)
        return (data.get("getCards") or {}).get("items") or []

    async def _mutate(self, name: str, args: dict[str, Any]) -> bool:
        """Run a station mutation that returns a Boolean."""
        decl = ", ".join(f"${k}: {'String' if k == 'card' else 'ID!'}" for k in args)
        call = ", ".join(f"{k}: ${k}" for k in args)
        data = await self.query(f"mutation Action({decl}) {{ {name}({call}) }}", args)
        result = data.get(name)
        _LOGGER.debug("%s(%s) -> %s", name, args, result)
        if result is False:
            raise EvcNetError(f"{name} was rejected by the charge station")
        return bool(result)

    async def start_charging(self, charge_station_id: str, channel_id: str, card: str | None) -> bool:
        """Start a transaction on a channel (channel_id is the channel's internal id)."""
        return await self._mutate(
            "startTransaction",
            {"chargeStationId": charge_station_id, "channelId": channel_id, "card": card},
        )

    async def stop_charging(self, charge_station_id: str, channel_id: str) -> bool:
        """Stop the transaction on a channel."""
        return await self._mutate("stopTransaction", {"chargeStationId": charge_station_id, "channelId": channel_id})

    async def soft_reset(self, charge_station_id: str) -> bool:
        """Soft reset (non-disruptive restart) a charge station."""
        return await self._mutate("softReset", {"chargeStationId": charge_station_id})

    async def hard_reset(self, charge_station_id: str) -> bool:
        """Hard reset (full reboot) a charge station."""
        return await self._mutate("hardReset", {"chargeStationId": charge_station_id})

    async def unlock_connector(self, charge_station_id: str, channel_id: str) -> bool:
        """Unlock the connector of a channel."""
        return await self._mutate("unlockConnector", {"chargeStationId": charge_station_id, "channelId": channel_id})
