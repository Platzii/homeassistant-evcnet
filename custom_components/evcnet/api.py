"""API client with isolated cookies and explicit EVC-net authentication."""
import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from html.parser import HTMLParser
from http.cookies import CookieError, SimpleCookie
from typing import Any

import aiohttp
from yarl import URL

from .const import AJAX_ENDPOINT, LOGIN_ENDPOINT

_LOGGER = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """The user must authenticate again."""


class TwoFactorRequired(AuthenticationError):
    """An email verification code is required."""


class InvalidOtp(AuthenticationError):
    """The verification code was rejected."""


class ApiError(Exception):
    """Unexpected server response with a safe, user-visible diagnostic code."""

    def __init__(self, message="Unexpected server response", *, code="invalid_response"):
        super().__init__(message)
        self.code = code


class _TokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if (tag == "input" and fields.get("name") == "_token"
                and fields.get("type", "").lower() == "hidden"):
            self.token = fields.get("value")


class EvcNetApiClient:
    """Use a separate cookie jar for each account, never HA's shared jar."""

    def __init__(self, base_url, username, password, session):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = session
        self._jar = aiohttp.CookieJar()
        self._is_authenticated = False
        self._token = None
        self._lock = asyncio.Lock()
        self.save_cookies = None
        self.auth_expired = None
        self._last_saved = None
        self.last_response = None

    @property
    def is_authenticated(self) -> bool:
        """Whether a session is available; the next API call still validates it."""
        return self._is_authenticated

    def export_cookies(self):
        """JSON-safe cookies; Max-Age has already become an absolute expiry."""
        return [{"name": c.key, "value": c.value,
                 "attrs": {k: v for k, v in c.items() if v}}
                for c in self._jar]

    def restore_cookies(self, cookies):
        """Restore without restarting the original lifetime."""
        self._jar.clear()
        self._is_authenticated = False
        try:
            for item in cookies:
                cookie = SimpleCookie()
                cookie[item["name"]] = item["value"]
                for key, value in item["attrs"].items():
                    cookie[item["name"]][key] = value
                self._jar.update_cookies(cookie, URL(self.base_url))
        except (KeyError, TypeError, ValueError, CookieError, AttributeError):
            self._jar.clear()
            _LOGGER.warning("Stored EVC-net cookies are invalid; authenticate again")
            return
        self._is_authenticated = bool(self._jar.filter_cookies(URL(self.base_url)).get("PHPSESSID"))

    async def _persist(self):
        cookies = self.export_cookies()
        if self.save_cookies and cookies != self._last_saved:
            await self.save_cookies(cookies)
            self._last_saved = cookies

    async def _expired(self):
        self._is_authenticated = False
        self._jar.clear()
        await self._persist()
        if self.auth_expired:
            self.auth_expired()
        _LOGGER.info("EVC-net session expired; authentication is required")
        raise AuthenticationError("EVC-net session expired")

    async def _request(self, method, path, **kwargs):
        """Capture every Set-Cookie, including redirects, without leaking secrets."""
        url = URL(self.base_url + path)
        async with self.session.request(
            method, url, cookies=self._jar.filter_cookies(url),
            allow_redirects=False, timeout=aiohttp.ClientTimeout(total=30), **kwargs
        ) as response:
            set_cookie_names = list(response.cookies.keys())
            for source in response.cookies.values():
                cookie = SimpleCookie()
                cookie[source.key] = source.value
                target = cookie[source.key]
                for key, value in source.items():
                    target[key] = value
                # Scope persisted cookies to this origin. No cross-origin redirects.
                if target["domain"] and not (
                    url.host == target["domain"].lstrip(".")
                    or url.host.endswith("." + target["domain"].lstrip("."))
                ):
                    continue
                target["domain"] = ""
                if target["max-age"]:
                    try:
                        expiry = time.time() + int(target["max-age"])
                        target["expires"] = format_datetime(
                            datetime.fromtimestamp(max(0, expiry), timezone.utc), usegmt=True)
                        target["max-age"] = ""
                    except (ValueError, OverflowError):
                        target["max-age"] = ""
                self._jar.update_cookies(cookie, url)
            body = await response.text()
            location = response.headers.get("Location")
            status = response.status
        redirect_kind = "none"
        redirect_origin = None
        if location:
            try:
                target_url = URL(self.base_url + "/").join(URL(location))
                redirect_origin = f"{target_url.scheme}://{target_url.host}"
                if target_url.port:
                    redirect_origin += f":{target_url.port}"
                if target_url.origin() != URL(self.base_url).origin():
                    redirect_kind = "different_origin"
                elif target_url.path.rstrip("/") == "/2fa":
                    redirect_kind = "otp"
                elif target_url.path.lower().startswith("/login"):
                    redirect_kind = "login"
                elif target_url.path.rstrip("/") in ("", "/Overview"):
                    redirect_kind = "dashboard"
                else:
                    redirect_kind = "other"
            except ValueError:
                redirect_kind = "invalid"
        self.last_response = {"method": method, "path": path, "status": status,
                              "redirect": redirect_kind,
                              "redirect_origin": redirect_origin,
                              "set_cookie_names": set_cookie_names}
        _LOGGER.debug("EVC-net response metadata: %s", self.last_response)
        if self._is_authenticated:
            await self._persist()
        if status >= 500 or status == 429:
            raise ApiError(f"EVC-net temporarily unavailable (HTTP {status})",
                           code="rate_limited" if status == 429 else "server_error")
        return status, location, body

    def _redirect(self, location):
        if not location:
            raise ApiError("Missing redirect destination", code="missing_redirect")
        url = URL(self.base_url + "/").join(URL(location))
        if (url.host == URL(self.base_url).host and url.scheme == "http"
                and url.port in (None, 80)):
            url = url.with_scheme("https")
        if url.origin() != URL(self.base_url).origin():
            raise ApiError("Unexpected cross-origin redirect", code="cross_origin_redirect")
        return url.path.rstrip("/") or "/"

    async def _get_token(self):
        status, location, body = await self._request("GET", "/2fa")
        if location or status in (401, 403):
            raise AuthenticationError("Verification session expired; restart login")
        if status != 200:
            raise ApiError("Unable to load verification form", code="otp_page_unavailable")
        parser = _TokenParser()
        parser.feed(body)
        if not parser.token:
            raise ApiError("Verification form has no CSRF token", code="missing_otp_token")
        self._token = parser.token

    async def authenticate(self):
        async with self._lock:
            self._is_authenticated = False
            self._jar.clear()
            self._token = None
            login_data = aiohttp.FormData()
            login_data.add_field("emailField", self.username, content_type="text/plain")
            login_data.add_field("passwordField", self.password, content_type="text/plain")
            login_data.add_field("Login", "Login", content_type="text/plain")
            login_page_status, _, login_page = await self._request("GET", LOGIN_ENDPOINT)
            if login_page_status == 200:
                parser = _TokenParser()
                parser.feed(login_page)
                if parser.token:
                    login_data.add_field("_token", parser.token, content_type="text/plain")
            status, location, _ = await self._request(
                "POST", LOGIN_ENDPOINT, data=login_data,
                headers={"Origin": self.base_url, "Referer": f"{self.base_url}/Login/Login"},
            )
            if status not in (302, 303):
                raise AuthenticationError("Login rejected")
            path = self._redirect(location)
            if path == "/2fa":
                await self._get_token()
                raise TwoFactorRequired("Email verification required")
            if path not in ("/", "/Overview"):
                raise AuthenticationError("Login rejected")
            status, location, _ = await self._request("GET", "/Overview")
            if status in (301, 302, 303, 307, 308):
                next_path = self._redirect(location)
                if next_path == "/2fa":
                    await self._get_token()
                    raise TwoFactorRequired("Email verification required")
            await self._validate()
            return True

    async def verify_otp(self, code):
        if not re.fullmatch(r"[0-9]{6}", code):
            raise InvalidOtp("Enter six digits")
        async with self._lock:
            if not self._token:
                raise AuthenticationError("Restart login to request a verification code")
            form = aiohttp.FormData()
            for name, value in (("_token", self._token), ("_auth_code", code), ("VerifyOtp", "Verify")):
                form.add_field(name, value, content_type="text/plain")
            status, location, _ = await self._request(
                "POST", "/2fa_check", data=form,
                headers={"Origin": self.base_url, "Referer": f"{self.base_url}/2fa"},
            )
            if status in (302, 303):
                path = self._redirect(location)
                if path in ("/", "/Overview"):
                    await self._validate()
                    self._token = None
                    return True
                if path != "/2fa":
                    raise AuthenticationError("Verification session expired; restart login")
            elif status not in (200, 400, 403, 422):
                raise ApiError("Unexpected verification response")
            await self._get_token()
            raise InvalidOtp("Verification code rejected or expired")

    async def _check_page(self):
        path = "/Overview"
        for _ in range(4):
            status, location, body = await self._request("GET", path)
            if status in (301, 302, 303, 307, 308):
                path = self._redirect(location)
                if path.lower().startswith("/login") or path == "/2fa":
                    await self._expired()
                if path not in ("/", "/Overview"):
                    raise ApiError("Unexpected dashboard redirect")
                continue
            if status in (401, 403) or re.search(
                r'emailField|passwordField|_auth_code|action=[\"\']/?2fa_check', body, re.I
            ):
                await self._expired()
            if status != 200 or not body.strip():
                raise ApiError("Unable to validate dashboard")
            return
        raise ApiError("Too many dashboard redirects")

    async def _validate(self):
        await self._check_page()
        if not self._jar.filter_cookies(URL(self.base_url)).get("PHPSESSID"):
            raise AuthenticationError("Session cookie missing")
        await self._ajax(self._spots_payload())
        self._is_authenticated = True
        await self._persist()
        _LOGGER.info("EVC-net authentication validated")

    @staticmethod
    def _spots_payload():
        return {"0": {"handler": "\\LMS\\EV\\AsyncServices\\DashboardAsyncService",
                      "method": "networkOverview", "params": {"mode": "id"}}}

    async def _ajax(self, payload):
        status, location, body = await self._request(
            "POST", AJAX_ENDPOINT, data={"requests": json.dumps(payload)})
        if status in (401, 403):
            await self._expired()
        if location:
            path = self._redirect(location)
            if path == "/2fa" or path.lower().startswith("/login") or path == "/":
                await self._expired()
            raise ApiError("Unexpected API redirect")
        if status != 200:
            raise ApiError(f"API request failed (HTTP {status})")
        try:
            result = json.loads(body)
        except ValueError as err:
            if re.search(r"emailField|passwordField|_auth_code|/2fa", body, re.I):
                await self._expired()
            await self._check_page()
            raise ApiError("API returned invalid JSON") from err
        # The AJAX batch protocol returns a list. Never accept an error object as data.
        if not isinstance(result, list) or not result:
            await self._check_page()
            raise ApiError("Invalid AJAX batch response")
        if not result[0]:
            # Empty data also occurs for a pre-authenticated/expired session.
            await self._check_page()
        if payload.get("0", {}).get("method") == "networkOverview":
            if not isinstance(result[0], list) or any(
                not isinstance(spot, dict) or "IDX" not in spot for spot in result[0]
            ):
                raise ApiError("Invalid charge spots response")
        return result

    async def _make_ajax_request(self, requests_payload, _retry_count=0):
        async with self._lock:
            if not self._is_authenticated:
                await self._expired()
            return await self._ajax(requests_payload)

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
