"""DataUpdateCoordinator for EVC-net."""
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import EvcNetApiClient, EvcNetAuthError, EvcNetError
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FULL_HISTORY_INTERVAL_SEC,
    HISTORY_RETRY_SEC,
    RECENT_TRANSACTIONS,
    TRANSACTION_STATUS_ACTIVE,
)

_LOGGER = logging.getLogger(__name__)


def _timestamp(value: str | None) -> float | None:
    """Parse an ISO date string from the API to a UNIX timestamp."""
    if not value:
        return None
    parsed = dt_util.parse_datetime(value)
    return parsed.timestamp() if parsed else None


class EvcNetCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching EVC-net data.

    data[station_id] = {
        "info": <station as returned by getCustomerChargeStations>,
        "channels": {channel_no: {"channel": {...}, "transaction": {...} | None,
                                  "power_kw": float | None, "log": [transactions]}},
        "total_energy_kwh": float | None,
    }
    """

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: EvcNetApiClient, max_channels: int = 1
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.client = client
        self.max_channels = max(1, int(max_channels))
        # Detected channel count per station id
        self.spot_channels: dict[str, int] = {}
        # Switch entities by unique_id, for service handlers (start/stop charging, actions)
        self.entities: dict[str, Any] = {}
        # Charge cards of the account (fetched once)
        self.cards: list[dict[str, Any]] | None = None
        # Finished transactions: id -> (station_id, Wh); used for the total energy sensor
        self._finished_wh: dict[str, tuple[str, float]] = {}
        self._history_loaded = False
        self._history_next_load = 0.0
        # Last energy sample per active transaction id: (timestamp, Wh), and derived power in kW
        self._energy_samples: dict[str, tuple[float, float]] = {}
        self._power_kw: dict[str, float] = {}

    def get_channel(self, spot_id: str, channel_no: int) -> dict[str, Any]:
        """Return the channel data dict (may be empty) for a station and channel number."""
        return (self.data or {}).get(spot_id, {}).get("channels", {}).get(channel_no, {})

    def default_card(self, spot_id: str) -> str | None:
        """Return the card to start a session with when none is configured."""
        info = (self.data or {}).get(spot_id, {}).get("info", {})
        card = (info.get("accessOptions") or {}).get("chargeCard")
        if card:
            return card
        for item in self.cards or []:
            if item.get("state") == "Active" and item.get("externalId"):
                return item["externalId"]
        return None

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API."""
        try:
            result = await self.client.poll(RECENT_TRANSACTIONS)
            if self.cards is None:
                try:
                    self.cards = await self.client.get_cards()
                except EvcNetError as err:
                    _LOGGER.debug("Could not fetch cards: %s", err)
                    self.cards = []
            if time.time() >= self._history_next_load:
                await self._load_history(result.get("getCustomerChargeStations") or [])
        except EvcNetAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except EvcNetError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err

        stations = result.get("getCustomerChargeStations") or []
        if not stations:
            _LOGGER.warning("No charge stations found for this account")
            return {}

        active = result.get("lmsActiveTransaction")
        recent = (result.get("lmsTransactions") or {}).get("items") or []
        if active and active.get("transactionId") not in {t.get("transactionId") for t in recent}:
            recent = [active, *recent]
        active_ids = {active["transactionId"]} if active and active.get("transactionId") else set()
        active_ids |= {
            t["transactionId"]
            for t in recent
            if (t.get("transactionStatus") or "").lower() == TRANSACTION_STATUS_ACTIVE and t.get("transactionId")
        }

        index = self._channel_index(stations)
        for tx in recent:
            tx_id = tx.get("transactionId")
            if not tx_id or tx_id in active_ids:
                continue
            location = self._locate(tx, index)
            if location:
                self._finished_wh[tx_id] = (location[0], float(tx.get("energyDelivered") or 0))

        data: dict[str, Any] = {}
        for station in stations:
            spot_id = str(station.get("id"))
            channels: dict[int, dict[str, Any]] = {}
            active_wh = 0.0
            for channel in sorted(station.get("channels") or [], key=lambda c: c.get("channelNo") or 0):
                channel_no = int(channel.get("channelNo") or len(channels) + 1)
                log = [t for t in recent if self._locate(t, index) == (spot_id, channel_no)]
                transaction = next((t for t in log if t.get("transactionId") in active_ids), None)
                power_kw = None
                if transaction:
                    active_wh += float(transaction.get("energyDelivered") or 0)
                    power_kw = self._estimate_power(transaction)
                if channel.get("globalStatus") != "Charging":
                    power_kw = 0.0
                channels[channel_no] = {
                    "channel": channel,
                    "transaction": transaction,
                    "power_kw": power_kw,
                    "log": log,
                }

            total_kwh = None
            if self._history_loaded:
                finished = sum(wh for sid, wh in self._finished_wh.values() if sid == spot_id)
                total_kwh = round((finished + active_wh) / 1000, 3)

            self.spot_channels[spot_id] = max(1, len(channels))
            data[spot_id] = {"info": station, "channels": channels, "total_energy_kwh": total_kwh}

        # Forget samples of transactions that are no longer active
        for tx_id in list(self._energy_samples):
            if tx_id not in active_ids:
                self._energy_samples.pop(tx_id, None)
                self._power_kw.pop(tx_id, None)

        return data

    async def _load_history(self, stations: list[dict[str, Any]]) -> None:
        """Read the full transaction history to compute total energy per station."""
        try:
            transactions = await self.client.get_all_transactions()
        except EvcNetError as err:
            _LOGGER.debug("Could not load transaction history: %s (will retry)", err)
            self._history_next_load = time.time() + HISTORY_RETRY_SEC
            return
        index = self._channel_index(stations)
        finished: dict[str, tuple[str, float]] = {}
        for tx in transactions:
            tx_id = tx.get("transactionId")
            if not tx_id or (tx.get("transactionStatus") or "").lower() == TRANSACTION_STATUS_ACTIVE:
                continue
            location = self._locate(tx, index)
            if location:
                finished[tx_id] = (location[0], float(tx.get("energyDelivered") or 0))
        self._finished_wh = finished
        self._history_loaded = True
        self._history_next_load = time.time() + FULL_HISTORY_INTERVAL_SEC
        _LOGGER.debug("Loaded %d transactions (%d at own stations)", len(transactions), len(finished))

    @staticmethod
    def _channel_index(stations: list[dict[str, Any]]) -> dict[str, tuple[str, int]]:
        """Map evseId and '<station>*<channelNo>' identifiers to (station_id, channel_no)."""
        index: dict[str, tuple[str, int]] = {}
        for station in stations:
            spot_id = str(station.get("id"))
            for channel in station.get("channels") or []:
                channel_no = int(channel.get("channelNo") or 1)
                if channel.get("evseId"):
                    index[channel["evseId"]] = (spot_id, channel_no)
                index[f"{spot_id}*{channel_no}"] = (spot_id, channel_no)
        return index

    @staticmethod
    def _locate(tx: dict[str, Any], index: dict[str, tuple[str, int]]) -> tuple[str, int] | None:
        """Return (station_id, channel_no) of a transaction, or None if it is not at an own station."""
        return index.get(tx.get("evseId") or "") or index.get(tx.get("channelId") or "")

    def _estimate_power(self, tx: dict[str, Any]) -> float | None:
        """Estimate charging power from the energy increase between two meter updates."""
        tx_id = tx["transactionId"]
        ts = _timestamp(tx.get("updateDate"))
        wh = float(tx.get("energyDelivered") or 0)
        if ts is None:
            return self._power_kw.get(tx_id)
        previous = self._energy_samples.get(tx_id)
        if previous is None:
            self._energy_samples[tx_id] = (ts, wh)
            # Until the next meter update, use the average power of the session so far
            charging_hours = (tx.get("durationCharging") or 0) / 3600
            if charging_hours > 0:
                self._power_kw[tx_id] = round(wh / 1000 / charging_hours, 3)
            return self._power_kw.get(tx_id)
        prev_ts, prev_wh = previous
        if ts > prev_ts:
            hours = (ts - prev_ts) / 3600
            self._power_kw[tx_id] = round(max(0.0, (wh - prev_wh) / 1000 / hours), 3)
            self._energy_samples[tx_id] = (ts, wh)
        return self._power_kw.get(tx_id)
