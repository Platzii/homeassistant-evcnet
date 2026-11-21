"""Private, account-scoped session persistence in Home Assistant storage."""
import hashlib

import aiohttp
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.storage import Store

from .api import EvcNetApiClient


def _store(hass, data):
    identity = data["base_url"].rstrip("/") + "\0" + data["username"]
    key = hashlib.sha256(identity.encode()).hexdigest()
    return Store(hass, 1, f"evcnet.session.{key}", private=True)


def create_client(hass, data):
    """Create a client and its store without putting cookies in config entries.

    The caller owns the HTTP session and must close it (client.session.close()).
    """
    store = _store(hass, data)
    client = EvcNetApiClient(
        data["base_url"], data["username"], data["password"],
        async_create_clientsession(hass, auto_cleanup=False, cookie_jar=aiohttp.DummyCookieJar()),
    )

    async def save(cookies):
        await store.async_save({"cookies": cookies})

    return client, store, save


async def async_remove_session(hass, data):
    """Delete the persisted cookies of an account."""
    await _store(hass, data).async_remove()
