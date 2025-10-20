"""API client for EVC-net charging stations."""
import json
import logging
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

    async def authenticate(self) -> bool:
        """Authenticate with the EVC-net API."""
        url = f"{self.base_url}{LOGIN_ENDPOINT}"

        data = {
            "emailField": self.username,
            "passwordField": self.password,
        }

        try:
            # Don't follow redirects automatically, we need to capture cookies
            async with self.session.post(
                url,
                data=data,
                allow_redirects=False  # Don't follow redirects
            ) as response:
                _LOGGER.debug("Login response status: %s", response.status)

                # Login returns 302 redirect
                if response.status == 302:
                    # Try multiple methods to get the PHPSESSID

                    # Method 1: From Set-Cookie header
                    set_cookie = response.headers.get('Set-Cookie') or response.headers.get('set-cookie', '')
                    _LOGGER.debug("Set-Cookie header: %s", set_cookie)

                    if 'PHPSESSID=' in set_cookie:
                        # Extract PHPSESSID value - format is: PHPSESSID=value; other=params
                        cookie_part = set_cookie.split('PHPSESSID=')[1]
                        # Get just the value before the first semicolon
                        self._phpsessid = cookie_part.split(';')[0].strip()

                    # Method 2: From cookie jar (if session has cookie support)
                    if not self._phpsessid and hasattr(self.session, 'cookie_jar'):
                        cookies = self.session.cookie_jar.filter_cookies(self.base_url)
                        for cookie in cookies.values():
                            if cookie.key == 'PHPSESSID':
                                self._phpsessid = cookie.value
                                _LOGGER.debug("Found PHPSESSID in cookie jar")
                                break

                    # Method 3: From response.cookies (aiohttp's built-in)
                    if not self._phpsessid and hasattr(response, 'cookies'):
                        if 'PHPSESSID' in response.cookies:
                            self._phpsessid = response.cookies['PHPSESSID'].value
                            _LOGGER.debug("Found PHPSESSID in response.cookies")

                    if self._phpsessid:
                        self._is_authenticated = True
                        _LOGGER.info("Successfully authenticated with EVC-net")
                        _LOGGER.debug("PHPSESSID: %s", self._phpsessid[:10] + "...")
                        return True

                    _LOGGER.error("No PHPSESSID found in any location")
                    _LOGGER.debug("All response headers: %s", dict(response.headers))
                    return False
                else:
                    _LOGGER.error("Authentication failed with status %s (expected 302)", response.status)
                    response_text = await response.text()
                    _LOGGER.debug("Response: %s", response_text[:200])
                    return False
        except aiohttp.ClientError as err:
            _LOGGER.error("Error during authentication: %s", err)
            return False
        except Exception as err:
            _LOGGER.error("Unexpected error during authentication: %s", err, exc_info=True)
            return False

    async def _make_ajax_request(self, requests_payload: dict) -> dict[str, Any]:
        """Make an AJAX request to the EVC-net API."""
        if not self._is_authenticated:
            if not await self.authenticate():
                raise Exception("Failed to authenticate")

        url = f"{self.base_url}{AJAX_ENDPOINT}"

        # Prepare headers with cookie
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": f"PHPSESSID={self._phpsessid}"
        }

        # Convert requests payload to JSON string and send as form data
        data = {
            "requests": json.dumps(requests_payload)
        }

        try:
            # Make request with explicit cookie header
            async with self.session.post(url, headers=headers, data=data) as response:
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
                                    "session likely expired. Re-authenticating...",
                                    response.status,
                                    content_type
                                )
                                self._is_authenticated = False

                                # Try to re-authenticate
                                if await self.authenticate():
                                    # Retry the request once
                                    return await self._make_ajax_request(requests_payload)

                                raise Exception("Re-authentication failed or still getting HTML response")
                        except json.JSONDecodeError as err:
                            _LOGGER.error("Failed to decode JSON response: %s", err)
                            _LOGGER.debug("Response text: %s", response_text[:500])
                            raise
                    else:
                        raise Exception(f"Unexpected content type: {content_type}")

                elif response.status in [401, 302]:
                    # Session expired, re-authenticate
                    _LOGGER.info("Session expired (status %s), re-authenticating", response.status)
                    self._is_authenticated = False
                    if await self.authenticate():
                        # Retry the request
                        return await self._make_ajax_request(requests_payload)
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
