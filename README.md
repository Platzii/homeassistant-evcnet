# EVC-net (Last Mile Solutions) Charging Station Integration for Home Assistant

This custom integration allows you to monitor and control your EVC-net (Last Mile Solutions) charging station through Home Assistant.

> **Version 2.0.0** switched from the old EVC-net web portal (`*.evc-net.com`) to the GraphQL API that is also used by the 50five app, because login to the old portal no longer works. You now log in with your 50five account (email + password); a base URL is no longer needed. See [Upgrading from 1.x](#upgrading-from-1x).

## Disclaimer

**Important Notice**: This integration was developed with the assistance of AI tools, as I have little to no prior experience with Python or Home Assistant integration development. While the code has been tested and appears to function correctly, please use it at your own discretion and report any issues you encounter.

**Testing Environment**: Version 2.0.0 has been tested with a 50five NL account and a Shell Recharge/NewMotion Home Advanced charger. Other 50five countries use the same API and are expected to work, but have not been confirmed yet.

## Features

- **Sensors**: Monitor charging status, power, energy usage, and recent charging sessions (summary, last session time and status)
- **Switch**: Start and stop charging sessions
- **Buttons**: Control charging station operations (soft/hard reset, unlock connector, refresh status)
- **Multi-channel**: Optional support for stations with multiple connectors (per-channel sensors and switches)
- **Updates**: Automatic polling every 30 seconds; use the Refresh Status button or action for an immediate update
- **Action calls**: Start/stop charging, refresh status, reset, unlock connector (with optional charge card for start)

## Installation

### HACS default repository (Recommended)

1. Make sure [HACS](https://hacs.xyz/) is installed
2. In HACS, search for "EVC-net (Last Mile Solutions)"
3. Open the overflow menu (⋮) and click "Download"
4. In the pop-up, you can select a specific version to install, or leave it empty to install the latest version
5. Click "Download" to install the integration
6. Restart Home Assistant

### HACS custom repository

1. Make sure [HACS](https://hacs.xyz/) is installed
2. Add this repository as a custom repository in HACS:
   - Go to HACS → Integrations → ⋮ (top right) → Custom repositories
   - Add `https://github.com/Platzii/homeassistant-evcnet` as Integration
3. Click Install
4. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/evcnet` folder to your Home Assistant's `custom_components` directory
2. Restart Home Assistant

## Configuration

1. Go to Settings → Devices & Services
2. Click "+ Add Integration"
3. Search for "EVC-net (Last Mile Solutions)"
4. Enter the credentials of your 50five account (the same as in the 50five app):
   - **Email**
   - **Password**
5. (Optional) Enter a **charge card ID** to start sessions with (e.g. `NL-TNM-C12345-6`). Leave it blank to use the card linked to your charging station, or the first active card on your account.

You can find your card ID in the 50five app, or in the `card_id` attribute of the Charging switch after setup.

## Available Entities

For each charging station, the integration creates:

### Sensors
- **Status**: Channel status (`Available`, `Charging`, `Reserved`, `Unavailable`, `Faulty`, ...)
- **Status Code**: Detailed channel statuses (e.g. `Transaction`, `ChargingComplete`)
- **Total Energy**: Total energy charged at this station (kWh), summed from all sessions plus the running session
- **Software Version**: Charging station software version
- **Current Power**: Charging power in kW. The API does not report live power, so this is **estimated** from the increase of the session energy between meter updates (roughly every 15 minutes). Directly after a restart it shows the average power of the running session.
- **Session Energy**: Energy charged in the current session (kWh)
- **Session Time**: Duration of the current session in hours (charging + idle)
- **Log Summary**: Number of recent sessions; attributes include a markdown table of recent sessions (for use in a Markdown card)
- **Last Log Notification** / **Last Log Time**: Status and start time of the most recent session

On stations with multiple connectors (when "Max channels" > 1 in options), sensors are created per channel (e.g. "Ch 1 Status", "Ch 2 Status").

### Switch
- **Charging**: On while a session is active. Turn on to start charging, off to stop

### Buttons
- **Refresh Status**: Fetch the latest status immediately
- **Soft Reset**: Perform a soft reset on the charging station
- **Hard Reset**: Perform a hard reset on the charging station
- **Unlock Connector**: Unlock the connector on the charging station

## Using Multiple Charge Cards

The integration supports using different charge cards for different charging sessions. This is useful when you have multiple vehicles with different charging cards.

### Using the Action

You can use the `evcnet.start_charging` action to specify which charge card to use:

```yaml
action: evcnet.start_charging
target:
  entity_id: switch.your_charging_station_charging # or device_id: 516934b04b9345cb26086fdb88de6467
data:
  card_id: "NL-TNM-C12345-6"  # Your charge card ID
```

**Note**: If you don't specify a `card_id` in the action, the integration uses the card configured in the integration settings, or else the card linked to the charging station.

### Viewing charging log in a card

To show recent charging sessions in a Markdown card, use the log summary sensor's `log_markdown` attribute. Add a Markdown card with:

```yaml
type: markdown
title: EVC-net Log
content: >-
  {{ state_attr('sensor.your_spot_log_summary', 'log_markdown') }}
```

Replace `your_spot_log_summary` with your actual log summary sensor entity ID (e.g. `sensor.charge_spot_12345_log_summary`).

Other actions are:

```yaml
action: evcnet.stop_charging
```

```yaml
action: evcnet.soft_reset
```

```yaml
action: evcnet.hard_reset
```

```yaml
action: evcnet.unlock_connector
```

```yaml
action: evcnet.refresh_status
```

## Configuration Options

After initial setup, you can modify configuration through the Home Assistant UI:

### Quick Settings (Card ID, Max Channels)

1. Go to **Settings** → **Devices & Services**
2. Find your **EVC-net** integration
3. Click the **Configure** button (⚙️)
4. Update your settings:
   - **Charge card ID**: Card used to start sessions (blank = card linked to the station)
   - **Max channels**: For stations with multiple connectors, set to 2 or more to create per-channel sensors and switches (e.g. "Ch 1 Charging", "Ch 2 Charging"). Leave at 1 for single-connector stations. Changing this option reloads the integration.

### Change Credentials (Email, Password)

1. Go to **Settings** → **Devices & Services**
2. Find your **EVC-net** integration
3. Click the **Configure** button (⚙️)
4. Click **"Reconfigure"** at the bottom
5. Update your email and/or password (leave password blank to keep the current one)

If your password is rejected later on (e.g. after changing it in the 50five app), Home Assistant shows a re-authentication prompt.

## Upgrading from 1.x

- Your existing configuration is migrated automatically: the base URL and customer ID are no longer used and are removed.
- Login uses your 50five account. If your old EVC-net portal login differs from your 50five app login, use **Reconfigure** to enter the 50five credentials.
- The **Block** / **Unblock** buttons and the `evcnet.block` / `evcnet.unblock` actions have been removed, because the new API does not offer them. Remove them from your automations.
- Sensor values are now based on the new API: **Status** shows values like `Charging` / `Available`, **Status Code** shows the detailed status names instead of a hexadecimal code, and the log sensors show charging sessions instead of portal log events. **Last Log Time** is now a timestamp sensor.
- If the charging station ID in the new API differs from the old portal, new entities are created and the old ones become unavailable; you can delete those.

## Troubleshooting

Enable debug logging by adding this to your `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.evcnet: debug
```

### Testing the API without Home Assistant

`scripts/test_api.py` logs in and prints your stations, sessions and cards (read-only):

```powershell
pip install aiohttp
$env:EVCNET_EMAIL = "you@example.com"; $env:EVCNET_PASSWORD = "..."
python scripts/test_api.py
```

## Support

Report issues at: https://github.com/Platzii/homeassistant-evcnet/issues

## License

MIT License
