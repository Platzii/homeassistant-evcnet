"""Flow, storage and coordinator tests against Home Assistant classes."""
import asyncio
import importlib
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
import pytest_asyncio
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.evcnet import config_flow, session
from custom_components.evcnet.api import (
    ApiError,
    AuthenticationError,
    InvalidOtp,
    TwoFactorRequired,
)
from custom_components.evcnet.coordinator import EvcNetCoordinator

pytestmark = pytest.mark.asyncio
DATA = {"base_url": "https://test.evc-net.com", "username": "test@example.invalid", "password": "saved-password"}


@pytest_asyncio.fixture
async def flow(tmp_path, monkeypatch):
    result = config_flow.EvcNetConfigFlow()
    result.hass = HomeAssistant(str(tmp_path))
    result.hass.config_entries = Mock()
    result.context = {"source": "user"}
    result.handler = "evcnet"
    result.flow_id = "test"
    result.async_set_unique_id = AsyncMock()
    result._abort_if_unique_id_configured = Mock()
    client = Mock()
    client.authenticate = AsyncMock(side_effect=TwoFactorRequired())
    client.verify_otp = AsyncMock(return_value=True)
    client.export_cookies.return_value = [{"name": "PHPSESSID", "value": "test-session", "attrs": {}}]
    save = AsyncMock()
    monkeypatch.setattr(config_flow, "create_client", Mock(return_value=(client, Mock(), save)))
    yield result, client, save


async def test_new_flow_otp_and_optional_card(flow):
    result, client, save = flow
    step = await result.async_step_user(DATA)
    assert step["step_id"] == "otp"
    save.assert_not_awaited()
    step = await result.async_step_otp({"otp": "012345"})
    assert step["step_id"] == "card_config"
    client.verify_otp.assert_awaited_once_with("012345")
    save.assert_awaited_once()
    step = await result.async_step_card_config({"card_id": "card"})
    assert step["type"] == "create_entry"
    assert step["data"] == {**DATA, "card_id": "card"}
    assert "otp" not in step["data"]


async def test_reauth_keeps_entry_and_saved_password(flow):
    result, client, save = flow
    entry = Mock(data={**DATA, "card_id": "existing-card"}, entry_id="existing")
    result._get_reauth_entry = Mock(return_value=entry)
    result.context = {"source": "reauth", "entry_id": "existing"}
    step = await result.async_step_reauth(entry.data)
    assert step["step_id"] == "reauth_confirm"
    client.authenticate.assert_not_awaited()
    step = await result.async_step_reauth_confirm({"password": ""})
    assert step["step_id"] == "otp"
    assert config_flow.create_client.call_args.args[1] == entry.data
    step = await result.async_step_otp({"otp": "012345"})
    assert step["reason"] == "reauth_successful"
    save.assert_awaited_once()
    result.hass.config_entries.async_schedule_reload.assert_called_once_with("existing")
    updates = result.hass.config_entries.async_update_entry.call_args.kwargs
    assert updates["data"]["card_id"] == "existing-card"
    assert updates["data"]["password"] == "saved-password"


async def test_reconfigure_uses_otp(flow):
    result, client, _ = flow
    entry = Mock(data=DATA, entry_id="existing")
    result._get_reconfigure_entry = Mock(return_value=entry)
    result.context = {"source": "reconfigure", "entry_id": "existing"}
    step = await result.async_step_reconfigure({**DATA, "password": "replacement"})
    assert step["step_id"] == "otp"
    step = await result.async_step_otp({"otp": "012345"})
    assert step["reason"] == "reconfigure_successful"
    result.hass.config_entries.async_schedule_reload.assert_called_once_with("existing")


@pytest.mark.parametrize("exception,error", [(InvalidOtp(), "invalid_otp"), (ApiError(), "invalid_response"), (asyncio.TimeoutError(), "cannot_connect")])
async def test_otp_errors_keep_form(flow, exception, error):
    result, client, save = flow
    await result.async_step_user(DATA)
    client.verify_otp.side_effect = exception
    step = await result.async_step_otp({"otp": "123456"})
    assert step["step_id"] == "otp"
    assert step["errors"] == {"base": error}
    save.assert_not_awaited()


async def test_expired_challenge_aborts(flow):
    result, client, save = flow
    await result.async_step_user(DATA)
    client.verify_otp.side_effect = AuthenticationError()
    step = await result.async_step_otp({"otp": "123456"})
    assert step["reason"] == "challenge_expired"
    save.assert_not_awaited()


async def test_old_login_without_otp(flow):
    result, client, _ = flow
    client.authenticate.side_effect = None
    assert (await result.async_step_user(DATA))["step_id"] == "card_config"


@pytest.mark.parametrize("exception,error", [(AuthenticationError(), "invalid_auth"), (ApiError(), "invalid_response"), (aiohttp.ClientError(), "cannot_connect")])
async def test_login_errors(flow, exception, error):
    result, client, save = flow
    client.authenticate.side_effect = exception
    step = await result.async_step_user(DATA)
    assert step["step_id"] == "user"
    assert step["errors"] == {"base": error}
    save.assert_not_awaited()


async def test_cancel_flow_detaches_private_session(flow):
    result, client, _ = flow
    await result.async_step_user(DATA)
    result.async_remove()
    client.session.detach.assert_called_once()


@pytest.mark.parametrize("point", ["spots", "status", "energy", "log"])
async def test_coordinator_does_not_swallow_auth_errors(tmp_path, point):
    hass = HomeAssistant(str(tmp_path))
    client = Mock()
    client.get_charge_spots = AsyncMock(return_value=[[{"IDX": "42"}]])
    client.get_spot_overview = AsyncMock(return_value=[[{}]])
    client.get_spot_total_energy_usage = AsyncMock(return_value=[[{}]])
    client.get_spot_log = AsyncMock(return_value=[[]])
    methods = {"spots": "get_charge_spots", "status": "get_spot_overview", "energy": "get_spot_total_energy_usage", "log": "get_spot_log"}
    getattr(client, methods[point]).side_effect = AuthenticationError()
    coordinator = EvcNetCoordinator(hass, client)
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_network_errors_are_not_auth_errors(tmp_path):
    client = Mock(get_charge_spots=AsyncMock(side_effect=ApiError("temporary")))
    coordinator = EvcNetCoordinator(HomeAssistant(str(tmp_path)), client)
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_store_roundtrip_and_account_scoping(tmp_path, monkeypatch):
    hass = HomeAssistant(str(tmp_path))
    factory = Mock(return_value=Mock())
    monkeypatch.setattr(session, "async_create_clientsession", factory)
    client, store, save = session.create_client(hass, DATA)
    cookies = [{"name": "PHPSESSID", "value": "synthetic", "attrs": {"path": "/"}}]
    await save(cookies)
    restored, second_store, _ = session.create_client(hass, DATA)
    restored.restore_cookies((await second_store.async_load())["cookies"])
    assert restored._is_authenticated
    assert restored.export_cookies()[0]["value"] == "synthetic"
    assert store.path == second_store.path
    assert "test@example.invalid" not in store.path
    assert factory.call_args.kwargs["cookie_jar"].__class__ is aiohttp.DummyCookieJar
    _, other_store, _ = session.create_client(hass, {**DATA, "username": "other"})
    assert other_store.path != store.path
    assert await other_store.async_load() is None


@pytest.mark.parametrize("has_session", [True, False])
async def test_setup_restores_session_without_login(tmp_path, monkeypatch, has_session):
    # Import the actual setup module separately from the lightweight test package.
    spec = importlib.util.spec_from_file_location(
        "custom_components.evcnet.setup_test", config_flow.__file__.replace("config_flow.py", "__init__.py"), submodule_search_locations=None)
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = Mock(async_forward_entry_setups=AsyncMock())
    entry = Mock(data=DATA, options={}, entry_id="existing")
    client = Mock(is_authenticated=has_session, authenticate=AsyncMock())
    store = Mock(async_load=AsyncMock(return_value={"cookies": ["synthetic"]}))
    monkeypatch.setattr(setup, "create_client", Mock(return_value=(client, store, AsyncMock())))
    coordinator = Mock(async_config_entry_first_refresh=AsyncMock())
    monkeypatch.setattr(setup, "EvcNetCoordinator", Mock(return_value=coordinator))
    if not has_session:
        with pytest.raises(ConfigEntryAuthFailed):
            await setup.async_setup_entry(hass, entry)
        client.authenticate.assert_not_awaited()
        return
    assert await setup.async_setup_entry(hass, entry)
    client.restore_cookies.assert_called_once_with(["synthetic"])
    client.authenticate.assert_not_awaited()
    client.auth_expired()
    entry.async_start_reauth.assert_called_once_with(hass)
