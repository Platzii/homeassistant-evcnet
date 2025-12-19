"""DataUpdateCoordinator for EVC-net."""
from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EvcNetApiClient
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class EvcNetCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching EVC-net data."""

    def __init__(self, hass: HomeAssistant, client: EvcNetApiClient) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.client = client
        self.charge_spots: list[dict[str, Any]] = []

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API."""
        try:
            # Get list of charging spots if not already fetched
            if not self.charge_spots:
                spots_response = await self.client.get_charge_spots()
                _LOGGER.debug("Raw charge spots response: %s", spots_response)

                if isinstance(spots_response, list) and len(spots_response) > 0:
                    first_item = spots_response[0]
                    if isinstance(first_item, list) and len(first_item) > 0:
                        self.charge_spots = first_item
                    else:
                        _LOGGER.warning("Unexpected charge spots data structure: %s", spots_response)
                        self.charge_spots = []
                else:
                    _LOGGER.warning("No charge spots data received or invalid format: %s", spots_response)
                    self.charge_spots = []

                _LOGGER.info("Found %d charging spot(s)", len(self.charge_spots))
                _LOGGER.debug("Charging spots: %s", self.charge_spots)

            if not self.charge_spots:
                _LOGGER.warning("No charging spots found in response")
                return {}

            # Get status for all charging spots
            data = {}
            for spot in self.charge_spots:
                # The spot ID is in the IDX field
                spot_id = spot.get("IDX")
                if spot_id:
                    try:
                        # Get status overview for the spot
                        status = await self.client.get_spot_overview(str(spot_id))
                        total_energy_usage = await self.client.get_spot_total_energy_usage(str(spot_id))
                        try:
                            # Attempt to fetch log for the first detected channel below; fallback to CHANNEL in info
                            fallback_channel = str(spot.get("CHANNEL", "1"))
                            log_data = await self.client.get_spot_log(str(spot_id), fallback_channel)
                        except Exception as log_err:
                            _LOGGER.debug(
                                "Failed to fetch log for spot %s: %s (continuing without log)",
                                spot_id,
                                log_err,
                            )
                            log_data = self.data.get(spot_id, {}).get("log", []) if self.data else []

                        _LOGGER.debug("Status for spot %s: %s", spot_id, status)
                        _LOGGER.debug("Total energy usage for spot %s: %s", spot_id, total_energy_usage)
                        _LOGGER.debug("Log data for spot %s: %s", spot_id, log_data)

                        # Derive per-channel status mapping if multiple channels exist
                        channels: list[str] = []
                        channel_status: dict[str, Any] = {}

                        if isinstance(status, list) and status and isinstance(status[0], list):
                            for item in status[0]:
                                if isinstance(item, dict):
                                    ch = item.get("CHANNEL")
                                    if ch is not None:
                                        ch_str = str(ch)
                                        channel_status[ch_str] = item
                                        if ch_str not in channels:
                                            channels.append(ch_str)

                        # Fallback: derive channels from spot info
                        if not channels:
                            ch_info = spot.get("CHANNEL")
                            if isinstance(ch_info, list):
                                channels = [str(c) for c in ch_info]
                            elif isinstance(ch_info, str):
                                # Split common delimiters
                                for sep in [",", ";", "|", "/"]:
                                    if sep in ch_info:
                                        channels = [s.strip() for s in ch_info.split(sep) if s.strip()]
                                        break
                                if not channels:
                                    channels = [ch_info]
                            elif ch_info is not None:
                                channels = [str(ch_info)]

                        if not channels:
                            channels = ["1"]

                        data[spot_id] = {
                            "info": spot,
                            "status": status,
                            "total_energy_usage": total_energy_usage,
                            "log": log_data,
                            "channels": channels,
                            "channel_status": channel_status,
                        }
                    except Exception as err:
                        _LOGGER.debug(
                            "Failed to fetch data for spot %s: %s (will retry next update)",
                            spot_id, err
                        )
                        # Keep existing data if available, otherwise use basic info
                        if spot_id in self.data:
                            data[spot_id] = self.data[spot_id]
                        else:
                            data[spot_id] = {
                                "info": spot,
                                "status": [],
                                "total_energy_usage": [],
                                "log": [],
                            }

            return data

        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
