"""API client for EVC-net charging stations."""
import asyncio
import json
import logging
import re
import time
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

import aiohttp

from .const import AJAX_ENDPOINT, LOGIN_ENDPOINT

_LOGGER = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
}

# While an MFA code is pending, don't log in again for this long: a new login
# sends a new code and makes the one the user is about to enter useless.
MFA_PENDING_TIMEOUT = 15 * 60

TOKEN_PATTERNS = (
    re.compile(r"""name=['"]_token['"][^>]*value=['"]([^'"]+)['"]"""),
    re.compile(r"""value=['"]([^'"]+)['"][^>]*name=['"]_token['"]"""),
)


class MfaRequiredError(Exception):
    """EVC-net is waiting for an MFA (OTP) code."""


REDIRECT_STATUSES = (301, 302, 303, 307, 308)


class _FormParser(HTMLParser):
    """Collect the forms on a page with their input fields."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: (value or "") for key, value in attrs}
        if tag == "form":
            self._current = {"action": attr.get("action", ""), "fields": []}
            self.forms.append(self._current)
        elif tag in ("input", "button") and self._current is not None and attr.get("name"):
            default_type = "text" if tag == "input" else "submit"
            self._current["fields"].append(
                {
                    "name": attr["name"],
                    "value": attr.get("value", ""),
                    "type": (attr.get("type") or default_type).lower(),
                }
            )

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._current = None


def _parse_mfa_form(html: str) -> tuple[str, dict[str, str], str] | None:
    """Find the MFA form. Returns (action, fields to post, name of the code field)."""
    parser = _FormParser()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - broken HTML, fall back below
        return None

    for form in parser.forms:
        names = [field["name"] for field in form["fields"]]
        code_field = next((name for name in names if "auth_code" in name), None)
        if code_field is None:
            continue

        data: dict[str, str] = {}
        submits = []
        for field in form["fields"]:
            name, value, ftype = field["name"], field["value"], field["type"]
            if name == code_field:
                continue
            if ftype in ("submit", "button", "image"):
                submits.append((name, value))
            elif ftype in ("checkbox", "radio"):
                # Tick "trust this device", so EVC-net asks for a code less often
                if "trust" in name.lower():
                    data[name] = value or "on"
            else:
                data[name] = value

        # Only send the "verify" button, never a "resend code" button
        for name, value in submits:
            if "resend" in name.lower():
                continue
            if "verify" in name.lower() or len(submits) == 1:
                data[name] = value or "1"
                break

        return form["action"], data, code_field

    return None


class EvcNetApiClient:
    """API client for EVC-net."""

    def __init__(self, base_url: str, username: str, password: str, session: aiohttp.ClientSession) -> None:
        """Initialize the API client."""
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = session
        self._is_authenticated = False
        self._phpsessid: str | None = None
        self._serverid: str | None = None
        self._auth_lock = asyncio.Lock()  # Prevent concurrent authentication
        self._last_auth_attempt = 0.0
        self._auth_backoff = 30  # Minimum seconds between auth attempts
        self._mfa_required = False
        self._mfa_since = 0.0
        self._mfa_token: str | None = None
        # Set by the integration: show / clear the "enter your code" notification
        self.on_mfa_required: Callable[[], None] | None = None
        self.on_authenticated: Callable[[], None] | None = None

    def _notify(self, notify: Callable[[], None] | None) -> None:
        """Call a notification callback without letting it break the login."""
        if notify is None:
            return
        try:
            notify()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Error while updating the EVC-net MFA notification")

    @property
    def mfa_required(self) -> bool:
        """True while EVC-net waits for an MFA code."""
        return self._mfa_required

    # ------------------------------------------------------------------ cookies

    def _capture_cookies(self, response: aiohttp.ClientResponse) -> None:
        """Remember PHPSESSID/SERVERID from a response and the redirects before it.

        EVC-net gives out a new PHPSESSID after login and after the MFA check,
        so the latest one must always be used for the next requests.
        """
        for resp in (*response.history, response):
            for name, morsel in resp.cookies.items():
                value = morsel.value
                if not value or value == "deleted":
                    continue
                if name == "PHPSESSID":
                    if value != self._phpsessid:
                        _LOGGER.debug("Received a new PHPSESSID")
                    self._phpsessid = value
                elif name == "SERVERID":
                    self._serverid = value

    def _cookies(self) -> dict[str, str]:
        """Cookies to send with the next request."""
        cookies: dict[str, str] = {}
        if self._phpsessid:
            cookies["PHPSESSID"] = self._phpsessid
        if self._serverid:
            cookies["SERVERID"] = self._serverid
        return cookies

    def _url(self, path: str) -> str:
        """Make an absolute URL from a path or a Location header."""
        if path.startswith("http"):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    def _same_site(self, location: str) -> str:
        """Turn a Location header into a URL on our own (https) base URL.

        EVC-net redirects to http://, which can lose the session; only keep the path.
        """
        parts = urlsplit(location)
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        return self._url(path)

    async def _get(self, url: str, headers: dict[str, str] | None = None) -> tuple[str, str]:
        """GET a page, following redirects ourselves so every hop stays on https.

        Returns (final url, html).
        """
        for _ in range(6):
            async with self.session.get(
                url,
                headers=headers or BROWSER_HEADERS,
                cookies=self._cookies(),
                allow_redirects=False,
            ) as response:
                self._capture_cookies(response)
                location = response.headers.get("Location")
                if response.status in REDIRECT_STATUSES and location:
                    url = self._same_site(urljoin(url, location))
                    continue
                return str(response.url), await response.text()
        raise aiohttp.ClientError(f"Too many redirects, last one to {url}")

    # ------------------------------------------------------------------- login

    async def authenticate(self) -> bool:
        """Log in to EVC-net.

        Returns True when logged in OR when EVC-net now asks for an MFA code
        (check `mfa_required`), False when the login was refused.
        """
        async with self._auth_lock:
            now = time.time()

            if self._is_authenticated and now - self._last_auth_attempt < self._auth_backoff:
                return True

            if self._mfa_required and now - self._mfa_since < MFA_PENDING_TIMEOUT:
                _LOGGER.debug("Waiting for an MFA code, not logging in again")
                return True

            self._last_auth_attempt = now
            self._is_authenticated = False
            self._mfa_required = False
            self._phpsessid = None
            self._serverid = None

            login_url = self._url(LOGIN_ENDPOINT)
            headers = {**BROWSER_HEADERS, "Referer": login_url, "Origin": self.base_url}
            data = {"emailField": self.username, "passwordField": self.password}

            try:
                async with self.session.post(
                    login_url, data=data, headers=headers, allow_redirects=False
                ) as response:
                    self._capture_cookies(response)
                    status = response.status
                    location = response.headers.get("Location", "")
                    body = "" if status == 302 else await response.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                _LOGGER.error("Error during login: %s", err)
                return False

            _LOGGER.debug("LOGIN STATUS=%s LOCATION=%s", status, location)

            if status != 302:
                _LOGGER.error(
                    "Login failed with status %s (expected 302). Response starts with: %s",
                    status,
                    body[:300],
                )
                return False

            if "Login/Login" in location:
                _LOGGER.error(
                    "Login refused: EVC-net sent the login page back. Check e-mail and password"
                )
                return False

            # Password accepted. Now see whether EVC-net still wants an MFA code:
            # open the MFA page ourselves (over https), the Overview redirect
            # alone sends a half-logged-in session back to the login page.
            try:
                mfa_url, mfa_html = await self.get_mfa_page()
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                _LOGGER.error("Error opening the MFA page after login: %s", err)
                return False

            _LOGGER.debug(
                "LOGIN MFA PAGE=%s HAS_AUTH_CODE=%s", mfa_url, "_auth_code" in mfa_html
            )

            if "_auth_code" in mfa_html:
                self._mfa_required = True
                self._mfa_since = time.time()
                _LOGGER.info(
                    "EVC-net asks for an MFA code. Enter it via Settings > Repairs"
                )
                self._notify(self.on_mfa_required)
                return True

            # No MFA form: check that the session really reaches the Overview.
            try:
                final_url, html = await self._get(
                    self._same_site(location or "/Overview"), headers=headers
                )
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                _LOGGER.error("Error following the login redirect: %s", err)
                return False

            _LOGGER.debug("LOGIN LANDED ON=%s", final_url)

            if "passwordField" in html or "Login/Login" in final_url:
                _LOGGER.error(
                    "Password was accepted, but the session is not valid (no MFA form found and "
                    "Overview sends back to the login page)"
                )
                return False

            if not self._phpsessid:
                _LOGGER.error("Login seemed to work but no session cookie (PHPSESSID) was received")
                return False

            self._is_authenticated = True
            _LOGGER.info("Successfully authenticated with EVC-net")
            self._notify(self.on_authenticated)
            return True

    # --------------------------------------------------------------------- MFA

    async def get_mfa_page(self) -> tuple[str, str]:
        """Fetch the MFA page. Returns (final url, html)."""
        return await self._get(self._url("/2fa"))

    async def _get_mfa_form(self) -> tuple[str, str, dict[str, str], str]:
        """Open the MFA page and read its form.

        Returns (page url, post url, fields, name of the code field).
        """
        page_url, html = await self.get_mfa_page()
        _LOGGER.debug("MFA PAGE URL=%s HAS_AUTH_CODE=%s", page_url, "_auth_code" in html)

        if "auth_code" not in html:
            # No MFA form: the pending login has expired, start over next time.
            self._mfa_required = False
            raise MfaRequiredError(
                "No pending MFA login (session expired). Reload the integration to get a new code"
            )

        form = _parse_mfa_form(html)
        if form is None:
            # Fallback: the form layout we used before
            token = next(
                (m.group(1) for p in TOKEN_PATTERNS if (m := p.search(html))), None
            )
            if token is None:
                raise Exception("MFA form and token not found on the MFA page")
            form = ("/2fa_check", {"_token": token, "VerifyOtp": "1"}, "_auth_code")

        action, fields, code_field = form
        post_url = self._same_site(urljoin(page_url, action or page_url))
        _LOGGER.debug(
            "MFA FORM POST=%s FIELDS=%s CODE FIELD=%s", post_url, sorted(fields), code_field
        )
        self._mfa_token = fields.get("_token")
        return page_url, post_url, fields, code_field

    async def get_mfa_token(self) -> str:
        """Get the form token from the MFA page."""
        _page_url, _post_url, fields, _code_field = await self._get_mfa_form()
        if not fields.get("_token"):
            raise Exception("MFA token not found on the MFA page")
        return fields["_token"]

    async def request_mfa_code(self) -> bool:
        """Ask EVC-net to send a new MFA code."""
        token = await self.get_mfa_token()
        async with self.session.post(
            self._url("/Login/CustomerOtp/Scheb/Resend"),
            data={"_token": token, "ResendOtp": "1"},
            headers={**BROWSER_HEADERS, "Referer": self._url("/2fa"), "Origin": self.base_url},
            cookies=self._cookies(),
            allow_redirects=False,
        ) as response:
            self._capture_cookies(response)
            ok = response.status in (200, *REDIRECT_STATUSES)
        if ok:
            self._mfa_since = time.time()
        return ok

    async def submit_mfa_code(self, code: str) -> bool:
        """Submit the MFA code. Returns True when EVC-net accepted it."""
        page_url, post_url, fields, code_field = await self._get_mfa_form()

        data = {**fields, code_field: str(code).strip()}
        headers = {**BROWSER_HEADERS, "Referer": page_url, "Origin": self.base_url}

        # Don't follow the redirect automatically: EVC-net redirects to http://,
        # which loses the session. Look at where it sends us, then go there over https.
        async with self.session.post(
            post_url,
            data=data,
            headers=headers,
            cookies=self._cookies(),
            allow_redirects=False,
        ) as response:
            self._capture_cookies(response)
            status = response.status
            location = response.headers.get("Location", "")
            body = "" if status in REDIRECT_STATUSES else await response.text()

        _LOGGER.debug("MFA CHECK STATUS=%s LOCATION=%s", status, location)

        if status in REDIRECT_STATUSES:
            if "Login/Login" in location:
                _LOGGER.error(
                    "MFA failed: EVC-net sent us back to the login page. A new code will be requested"
                )
                self._mfa_required = False
                return False
            if "/2fa" in location and "2fa_check" not in location:
                _LOGGER.error("MFA failed: EVC-net asks for the code again (wrong or expired code?)")
                return False
            final_url, html = await self._get(self._same_site(urljoin(post_url, location)), headers)
        else:
            final_url, html = page_url, body

        _LOGGER.debug("MFA LANDED ON=%s", final_url)

        if "auth_code" in html:
            _LOGGER.error("MFA failed: EVC-net still asks for a code (wrong or expired code?)")
            return False

        if "Login/Login" in final_url or "passwordField" in html:
            _LOGGER.error(
                "MFA failed: EVC-net sent the login page back. A new code will be requested"
            )
            self._mfa_required = False
            return False

        self._mfa_required = False
        self._is_authenticated = True
        self._last_auth_attempt = time.time()
        _LOGGER.info("MFA code accepted, logged in to EVC-net")
        self._notify(self.on_authenticated)
        return True

    # -------------------------------------------------------------------- AJAX

    async def _make_ajax_request(self, requests_payload: dict, _retry_count: int = 0) -> dict[str, Any]:
        """Make an AJAX request to the EVC-net API.

        Args:
            requests_payload: The payload to send
            _retry_count: Internal retry counter to prevent infinite recursion
        """
        if _retry_count > 1:
            raise Exception("Max retries exceeded for API request")

        if not self._is_authenticated:
            if not await self.authenticate():
                raise Exception("Failed to authenticate")
            if self._mfa_required:
                raise MfaRequiredError(
                    "MFA verification required: enter the code with the evcnet.submit_mfa action"
                )

        url = self._url(AJAX_ENDPOINT)
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {
            "requests": json.dumps(requests_payload),
            "metricKey": "EndUserRechargeSpotListView_99",
        }

        handler = requests_payload.get("0", {}).get("handler", "unknown")
        method = requests_payload.get("0", {}).get("method", "unknown")
        _LOGGER.debug("AJAX request: POST %s [handler=%s, method=%s]", url, handler, method)

        try:
            async with self.session.post(
                url, headers=headers, cookies=self._cookies(), data=data
            ) as response:
                self._capture_cookies(response)
                content_type = response.headers.get("Content-Type", "")
                _LOGGER.debug("AJAX response: %s.%s -> %s", handler, method, response.status)

                if response.status == 200:
                    if "application/json" not in content_type and "text/html" not in content_type:
                        raise Exception(f"Unexpected content type: {content_type}")

                    response_text = await response.text()
                    stripped = response_text.strip()

                    if stripped.startswith(("[", "{")):
                        _LOGGER.debug("AJAX RESPONSE=%s", stripped[:2000])
                        return json.loads(stripped)

                    # HTML instead of JSON: the session is no longer valid
                    _LOGGER.warning(
                        "Received HTML instead of JSON, session likely expired. Re-authenticating (retry %d)",
                        _retry_count,
                    )
                    self._is_authenticated = False

                elif response.status in (401, 302):
                    _LOGGER.info(
                        "Session expired (status %s), re-authenticating (retry %d)",
                        response.status,
                        _retry_count,
                    )
                    self._is_authenticated = False

                else:
                    response_text = await response.text()
                    _LOGGER.error(
                        "Request failed with status %s, response: %s",
                        response.status,
                        response_text[:200],
                    )
                    raise Exception(f"Request failed with status {response.status}")

        except asyncio.TimeoutError as err:
            _LOGGER.error("Request timeout: %s", err)
            raise Exception("Request timeout") from err
        except aiohttp.ClientConnectorError as err:
            _LOGGER.error("Connection error: %s", err)
            raise Exception("Cannot connect to EVC-net") from err
        except aiohttp.ClientError as err:
            _LOGGER.error("HTTP client error: %s", err)
            raise Exception(f"HTTP error: {err}") from err

        # Only reached when the session expired: log in again and retry once.
        if not await self.authenticate():
            raise Exception("Re-authentication failed")
        if self._mfa_required:
            raise MfaRequiredError(
                "MFA verification required: enter the code with the evcnet.submit_mfa action"
            )
        return await self._make_ajax_request(requests_payload, _retry_count + 1)

    # ----------------------------------------------------------------- actions

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
        response = await self._make_ajax_request(requests_payload)
        _LOGGER.debug("GET_CHARGE_SPOTS RESPONSE=%s", response)
        return response

    async def get_spot_total_energy_usage(self, recharge_spot_id: str) -> dict[str, Any]:
        """Get total energy usage of a specific charging spot."""
        requests_payload = {
            "0": {
                "handler": "\\LMS\\EV\\AsyncServices\\DashboardAsyncService",
                "method": "totalUsage",
                "params": {
                    "mode": "rechargeSpot",
                    "rechargeSpotIds": [recharge_spot_id],
                    "maxCache": 3600
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

    async def get_status(self, recharge_spot_id: str) -> dict[str, Any]:
        """Request fresh status from the charging spot (GetStatus action)."""
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
