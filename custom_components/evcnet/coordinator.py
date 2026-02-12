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

    def __init__(self, hass: HomeAssistant, client: EvcNetApiClient, max_channels: int = 1) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.client = client
        self.charge_spots: list[dict[str, Any]] = []
        self.max_channels = max(1, int(max_channels))
        # Auto-detected channel count per spot_id
        self.spot_channels: dict[str, int] = {}

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
                # The spot ID is in the IDX field; normalize to str for consistent dict keys
                raw_id = spot.get("IDX")
                if raw_id is None:
                    continue
                spot_id = str(raw_id)
                try:
                    # Get status
                    status = await self.client.get_spot_overview(spot_id)
                    total_energy_usage = await self.client.get_spot_total_energy_usage(spot_id)
                    # Determine number of channels from status payload if possible
                    detected_channels = 1
                    try:
                        if (isinstance(status, list) and len(status) > 0 and
                            isinstance(status[0], list) and len(status[0]) > 0):
                            detected_channels = max(1, len(status[0]))
                    except Exception:
                        detected_channels = 1

                    # Store detected channels and compute effective max to fetch
                    self.spot_channels[spot_id] = detected_channels
                    effective_max = max(self.max_channels, detected_channels)

                    # Fetch logs per channel (1..effective_max)
                    channels: dict[int, dict[str, Any]] = {}
                    for ch in range(1, effective_max + 1):
                        ch_str = str(ch)
                        try:
                            ch_log = await self.client.get_spot_log(spot_id, ch_str)
                        except Exception as log_err:
                            _LOGGER.debug(
                                "Failed to fetch log for spot %s channel %s: %s (continuing)",
                                spot_id,
                                ch_str,
                                log_err,
                            )
                            # Preserve previous channel log if available
                            ch_log = (
                                self.data.get(spot_id, {})
                                .get("channels", {})
                                .get(ch, {})
                                .get("log", [])
                                if self.data else []
                            )
                        channels[ch] = {"log": ch_log}

                    # Keep top-level 'log' for backwards compatibility (channel 1)
                    log_data = channels.get(1, {}).get("log", [])

                    _LOGGER.debug("Status for spot %s: %s", spot_id, status)
                    _LOGGER.debug("Total energy usage for spot %s: %s", spot_id, total_energy_usage)
                    _LOGGER.debug("Log data for spot %s: %s", spot_id, log_data)

                    data[spot_id] = {
                        "info": spot,
                        "status": status,
                        "total_energy_usage": total_energy_usage,
                        "log": log_data,
                        "channels": channels,
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
                            "channels": {},
                        }

            return data

        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
