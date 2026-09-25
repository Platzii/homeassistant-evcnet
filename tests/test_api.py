"""Exercise HTTP semantics using a real local server, without EVC-net credentials."""
import asyncio
import json
from email.utils import parsedate_to_datetime
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web

from custom_components.evcnet.api import (
    ApiError,
    AuthenticationError,
    EvcNetApiClient,
    InvalidOtp,
    TwoFactorRequired,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def server():
    state = {"calls": [], "otp": "012345", "two_factor": True, "authenticated": False,
             "ajax": [[{"IDX": "42"}]], "rotate": False, "bad_token": False}

    async def handle(request):
        data = dict(await request.post()) if request.method == "POST" else {}
        state["calls"].append((request.path, data, dict(request.cookies), request.content_type))
        if state.get("override"):
            response = state["override"](request)
            if response is not None:
                return response
        if request.path == "/Login/Login":
            state["authenticated"] = not state["two_factor"]
            response = web.Response(status=302, headers={"Location": "/2fa" if state["two_factor"] else "/Overview"})
            response.set_cookie("PHPSESSID", "pending", max_age=86400, httponly=True, samesite="Lax")
            response.set_cookie("SERVERID", "node-a")
            response.set_cookie("extra_session", "extra", path="/api")
            return response
        if request.path == "/2fa":
            return web.Response(text="<form></form>" if state["bad_token"] else
                                '<input value="csrf&amp;token" name="_token" type="hidden">')
        if request.path == "/2fa_check":
            assert data["_token"] == "csrf&token"
            assert data["VerifyOtp"] == "Verify"
            assert request.cookies["PHPSESSID"] == "pending"
            assert request.content_type == "multipart/form-data"
            state["authenticated"] = data["_auth_code"] == state["otp"]
            response = web.Response(status=302, headers={"Location": "/" if state["authenticated"] else "/2fa"})
            if state["authenticated"]:
                response.set_cookie("PHPSESSID", "verified", max_age=86400)
            return response
        if request.path == "/Overview":
            return (web.Response(text="<html>Dashboard</html>") if state["authenticated"]
                    else web.Response(status=302, headers={"Location": "/2fa"}))
        if request.path == "/api/ajax":
            if not state["authenticated"]:
                return web.Response(status=302, headers={"Location": "/2fa"})
            response = web.json_response(state["ajax"])
            if state["rotate"]:
                response.set_cookie("PHPSESSID", "rotated", max_age=86400)
                response.del_cookie("SERVERID")
            return response
        return web.Response(status=404)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    # Bind explicitly on IPv4 to avoid different ephemeral ports on IPv6.
    url = f"http://localhost:{port}"
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        client = EvcNetApiClient(url, "test@example.invalid", "test-password", session)
        yield client, state, session
    await runner.cleanup()


async def login(client):
    with pytest.raises(TwoFactorRequired):
        await client.authenticate()
    assert not client._is_authenticated
    await client.verify_otp("012345")


async def test_otp_and_validation(server, caplog):
    client, state, _ = server
    caplog.set_level("DEBUG", logger="custom_components.evcnet.api")
    await login(client)
    assert client._is_authenticated
    assert [c[0] for c in state["calls"]] == ["/Login/Login", "/2fa", "/2fa_check", "/Overview", "/api/ajax"]
    assert "012345" not in caplog.text
    assert "test-password" not in caplog.text
    assert "csrf&token" not in caplog.text
    assert "verified" not in caplog.text


async def test_legacy_login(server):
    client, state, _ = server
    state["two_factor"] = False
    assert await client.authenticate()
    assert "/2fa" not in [c[0] for c in state["calls"]]


async def test_wrong_otp_then_retry(server):
    client, state, _ = server
    with pytest.raises(TwoFactorRequired):
        await client.authenticate()
    with pytest.raises(InvalidOtp):
        await client.verify_otp("999999")
    assert not client._is_authenticated
    assert state["calls"][-1][0] == "/2fa"
    await client.verify_otp("012345")


@pytest.mark.parametrize("code", ["12345", "1234567", "１２３４５６", "123 45", "abcdef"])
async def test_otp_format(server, code):
    client, state, _ = server
    with pytest.raises(InvalidOtp):
        await client.verify_otp(code)
    assert not state["calls"]


async def test_missing_token(server):
    client, state, _ = server
    state["bad_token"] = True
    with pytest.raises(ApiError, match="CSRF"):
        await client.authenticate()


async def test_cookie_rotation_persistence_and_restart(server):
    client, state, session = server
    client.save_cookies = AsyncMock()
    await login(client)
    state["rotate"] = True
    await client.get_charge_spots()
    saved = client.save_cookies.call_args.args[0]
    assert {c["name"] for c in saved} == {"PHPSESSID", "extra_session"}
    cookie = next(c for c in saved if c["name"] == "PHPSESSID")
    assert cookie["value"] == "rotated"
    assert "max-age" not in cookie["attrs"]
    assert parsedate_to_datetime(cookie["attrs"]["expires"]).timestamp() > 0
    restarted = EvcNetApiClient(client.base_url, "test", "test", session)
    restarted.restore_cookies(json.loads(json.dumps(saved)))
    await restarted.get_charge_spots()
    assert state["calls"][-1][2] == {"PHPSESSID": "rotated", "extra_session": "extra"}
    assert len([c for c in state["calls"] if c[0] == "/Login/Login"]) == 1


async def test_expired_persisted_cookie_not_resurrected(server):
    client, _, _ = server
    client.restore_cookies([{"name": "PHPSESSID", "value": "old", "attrs": {
        "expires": "Wed, 01 Jan 2020 00:00:00 GMT", "path": "/"}}])
    assert not client._is_authenticated


@pytest.mark.parametrize("status,location,body", [
    (302, "/2fa", ""), (302, "/Login", ""), (401, None, ""), (403, None, ""),
    (200, None, '<input name="passwordField">'),
])
async def test_auth_expiry_does_not_replay_actions(server, status, location, body):
    client, state, _ = server
    await login(client)
    client.save_cookies = AsyncMock()
    client.auth_expired = Mock()
    state["override"] = lambda req: web.Response(status=status, headers={"Location": location} if location else {}, text=body) if req.path == "/api/ajax" else None
    before = len(state["calls"])
    with pytest.raises(AuthenticationError):
        await client.stop_charging("42", "1")
    assert len(state["calls"]) == before + 1
    client.auth_expired.assert_called_once()
    client.save_cookies.assert_awaited_with([])


async def test_empty_data_with_2fa_dashboard_is_not_accepted(server):
    client, state, _ = server
    await login(client)
    state["override"] = lambda req: web.json_response([[]]) if req.path == "/api/ajax" else None
    state["authenticated"] = False
    with pytest.raises(AuthenticationError):
        await client.get_charge_spots()


async def test_legitimately_empty_account(server):
    client, state, _ = server
    state["ajax"] = [[]]
    await login(client)
    assert await client.get_charge_spots() == [[]]


async def test_foreign_redirect_never_followed(server):
    client, state, _ = server
    state["override"] = lambda req: web.Response(status=302, headers={"Location": "https://other.invalid/2fa"})
    with pytest.raises(ApiError, match="cross-origin"):
        await client.authenticate()
    assert len(state["calls"]) == 1


async def test_transient_error_preserves_auth(server):
    client, state, _ = server
    await login(client)
    state["override"] = lambda req: web.Response(status=503)
    with pytest.raises(ApiError):
        await client.get_charge_spots()
    assert client._is_authenticated


async def test_accounts_are_isolated(server):
    client, state, session = server
    await login(client)
    second = EvcNetApiClient(client.base_url, "other", "other", session)
    assert not second.export_cookies()
    assert not list(session.cookie_jar)


async def test_service_payloads_unchanged(server):
    client, state, _ = server
    await login(client)
    await client.start_charging("42", "customer", "card", "2")
    payload = json.loads(state["calls"][-1][1]["requests"])["0"]
    assert payload["params"] == {"action": "StartTransaction", "rechargeSpotId": "42", "clickedButtonId": 0, "channel": "2", "customer": "customer", "card": "card"}
    await client.stop_charging("42", "2")
    assert json.loads(state["calls"][-1][1]["requests"])["0"]["params"]["action"] == "StopTransaction"
    await client.get_status("42")
    assert json.loads(state["calls"][-1][1]["requests"])["0"]["params"]["action"] == "GetStatus"


async def test_concurrent_calls_serialize_cookie_rotation(server):
    client, state, _ = server
    await login(client)
    state["rotate"] = True
    await asyncio.gather(client.get_charge_spots(), client.get_charge_spots())
    assert state["calls"][-1][2]["PHPSESSID"] == "rotated"


@pytest.mark.parametrize("status,location", [(200, None), (403, None), (302, "/Login")])
async def test_rejected_password_is_not_success(server, status, location):
    client, state, _ = server
    state["override"] = lambda req: web.Response(status=status, headers={"Location": location} if location else {})
    with pytest.raises(AuthenticationError):
        await client.authenticate()
    assert not client.is_authenticated


async def test_expired_otp_challenge(server):
    client, state, _ = server
    with pytest.raises(TwoFactorRequired):
        await client.authenticate()
    state["override"] = lambda req: web.Response(status=302, headers={"Location": "/Login"}) if req.path == "/2fa_check" else None
    with pytest.raises(AuthenticationError):
        await client.verify_otp("012345")
    assert not client.is_authenticated


async def test_cookie_lifetime_is_not_reset_on_restore(server):
    client, _, session = server
    client.restore_cookies([{"name": "PHPSESSID", "value": "old", "attrs": {
        "expires": "Fri, 01 Jan 2100 00:00:00 GMT", "path": "/", "secure": True, "httponly": True, "samesite": "Lax"}}])
    exported = client.export_cookies()
    restored = EvcNetApiClient(client.base_url, "test", "test", session)
    restored.restore_cookies(exported)
    assert restored.export_cookies() == exported
    assert exported[0]["attrs"]["expires"] == "Fri, 01 Jan 2100 00:00:00 GMT"
    # Secure cookie must not go over the HTTP test connection.
    assert not restored._jar.filter_cookies(__import__("yarl").URL(client.base_url))


async def test_malformed_json_checks_for_login_redirect(server):
    client, state, _ = server
    await login(client)
    state["authenticated"] = False
    state["override"] = lambda req: web.Response(text="<html>Sign in</html>") if req.path == "/api/ajax" else None
    with pytest.raises(AuthenticationError):
        await client.get_charge_spots()


async def test_cookie_updates_only_save_changed_state(server):
    client, state, _ = server
    client.save_cookies = AsyncMock()
    await login(client)
    client.save_cookies.reset_mock()
    await client.get_charge_spots()
    client.save_cookies.assert_not_awaited()
    state["rotate"] = True
    await client.get_charge_spots()
    client.save_cookies.assert_awaited_once()


@pytest.mark.parametrize("cookies", [None, [{}], [{"name": "PHPSESSID", "value": "x", "attrs": {"invalid": "x"}}]])
async def test_corrupt_cookie_storage_requests_fresh_auth(server, cookies):
    client, _, _ = server
    client.restore_cookies(cookies)
    assert not client.is_authenticated
    assert client.export_cookies() == []


async def test_server_error_has_safe_diagnostics(server, caplog):
    client, state, _ = server
    caplog.set_level("DEBUG", logger="custom_components.evcnet.api")
    state["override"] = lambda req: web.Response(status=500, text="private-response-body", headers={"Set-Cookie": "PHPSESSID=private-cookie", "Location": "https://other.invalid/?token=private-query"})
    with pytest.raises(ApiError) as error:
        await client.authenticate()
    assert error.value.code == "server_error"
    assert client.last_response == {"method": "POST", "path": "/Login/Login", "status": 500, "redirect": "different_origin", "redirect_origin": "https://other.invalid"}
    for secret in ("private-response-body", "private-cookie", "private-query", "test-password", "test@example.invalid"):
        assert secret not in caplog.text


async def test_missing_token_has_specific_error(server):
    client, state, _ = server
    state["bad_token"] = True
    with pytest.raises(ApiError) as error:
        await client.authenticate()
    assert error.value.code == "missing_otp_token"
    assert client.last_response["path"] == "/2fa"


async def test_cross_origin_has_specific_error(server):
    client, state, _ = server
    state["override"] = lambda req: web.Response(status=302, headers={"Location": "https://other.invalid/2fa"})
    with pytest.raises(ApiError) as error:
        await client.authenticate()
    assert error.value.code == "cross_origin_redirect"
