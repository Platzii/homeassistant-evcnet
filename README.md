# EVC-net (Last Mile Solutions) Charging Station Integration for Home Assistant

This custom integration allows you to monitor and control your EVC-net (Last Mile Solutions) charging station through Home Assistant.

## Disclaimer

**Important Notice**: This integration was developed with the assistance of AI tools, as I have little to no prior experience with Python or Home Assistant integration development. While the code has been tested and appears to function correctly, please use it at your own discretion and report any issues you encounter.

**Testing Environment**: This integration has been primarily tested on the 50five (BELUX) endpoint (`50five-sbelux.evc-net.com`) in combination with a Shell Recharge/NewMotion-Enovates EV charger (Home Advanced 3.0). Compatibility with other EVC-net endpoints or charging station models may vary. Users have confirmed that the 50five Germany (`50five-sde.evc-net.com`), 50five UK (`50five-suk.evc-net.com`), 50five (`50five.evc-net.com`) and 50five NL (`50five-snl.evc-net.com`) endpoints work as well.

## Features

- **Sensors**: Monitor charging status, power consumption, and energy usage
- **Switch**: Start and stop charging sessions
- **Buttons**: Control charging station operations (soft/hard reset, unlock connector, block/unblock, refresh status)
- **Real-time updates**: Automatic polling every 30 seconds
- **Action calls**: Start/stop a charging session using an action (allows to define a specific RFID card), reset, unlock connector, block/unblock is also available.

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
4. Enter your credentials:
   - **Base URL**: Default is `https://50five-sbelux.evc-net.com`
   - **Email**: Your EVC-net account email
   - **Password**: Your EVC-net account password
5. (Optional) Configure your RFID card ID:
   - **RFID Card ID**: Your charging card ID (e.g., `ABC12DEF34`)
   - **Customer ID**: Your customer ID (usually optional)

A list of known base URLs are:

- 50five: `https://50five.evc-net.com`
- 50five Germany: `https://50five-sde.evc-net.com`
- 50five UK: `https://50five-suk.evc-net.com`
- 50five NL: `https://50five-snl.evc-net.com`
- 50five Belgium: `https://50five-sbelux.evc-net.com`

### Finding Your Card ID

You have two options to find your RFID card ID:

**Option 1: From Browser**
1. Log in to the EVC-net platform (e.g.: https://50five-sbelux.evc-net.com) using your browser
2. Navigate to Cards
3. Find your Card ID in the table
4. Use this when configuring the integration

**Option 2: Auto-detection**
1. Leave the card ID field blank during setup
2. Enable debug logging (see Troubleshooting section)
3. Start a charging session manually (with your RFID card)
4. Wait 30 seconds for the integration to update
5. Check the logs - you'll see: `Auto-detected card_id: YOUR_CARD_ID`
6. The card ID is now cached and you can control charging from HA

## Available Entities

For each charging station, the integration creates:

### Sensors
- **Status**: Current charging station status
- **Status Code**: Raw status code from the charging station
- **Total Energy**: Total energy consumed (kWh)
- **Software Version**: Charging station software version
- **Current Power**: Active power draw in kilowatts
- **Session Energy**: Energy consumed in current session (kWh)
- **Session Time**: Duration of current charging session in hours

### Switch
- **Charging**: Turn on to start charging, off to stop

### Buttons
- **Soft Reset**: Perform a soft reset on the charging station
- **Hard Reset**: Perform a hard reset on the charging station
- **Unlock Connector**: Unlock the connector on the charging station
- **Block**: Block the charging station from use
- **Unblock**: Unblock the charging station to allow use

## Using Multiple RFID Cards

The integration supports using different RFID cards for different charging sessions. This is useful when you have multiple vehicles with different charging cards.

### Using the Action

You can use the `evcnet.start_charging` action to specify which RFID card to use:

```yaml
action: evcnet.start_charging
target:
  entity_id: switch.your_charging_station_charging # or device_id: 516934b04b9345cb26086fdb88de6467
data:
  card_id: "ABC12DEF34"  # Your RFID card ID
```

**Note**: If you don't specify a `card_id` in the action, the integration will use the default card configured in the integration settings.

Other options are:

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
action: evcnet.block
```

```yaml
action: evcnet.unblock
```

```yaml
action: evcnet.refresh_status
```

## Configuration Options

After initial setup, you can modify configuration through the Home Assistant UI:

### Quick Settings (Card ID & Customer ID)

1. Go to **Settings** → **Devices & Services**
2. Find your **EVC-net** integration
3. Click the **Configure** button (⚙️)
4. Update your settings:
   - **RFID Card ID**: Your charging card ID
   - **Customer ID**: Your customer ID (optional)

### Change Connection Credentials (URL, Username, Password)

To change your base URL, username, or password:

1. Go to **Settings** → **Devices & Services**
2. Find your **EVC-net** integration
3. Click the **Configure** button (⚙️)
4. Click **"Reconfigure"** at the bottom
5. Update your credentials:
   - **Base URL**: Your EVC-net endpoint
   - **Email**: Your account email
   - **Password**: Leave blank to keep current password

### Configuration Priority

The integration uses configuration in this order:
1. **Options** (set via Configure button) - highest priority
2. **Initial setup** (set during integration setup)
3. **Auto-detection** (detected from API responses) - fallback

## Troubleshooting

Enable debug logging by adding this to your `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.evcnet: debug
```

## Support

Report issues at: https://github.com/Platzii/homeassistant-evcnet/issues

## License

MIT License
