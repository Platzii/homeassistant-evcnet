# EVC-net (Last Mile Solutions) Charging Station Integration for Home Assistant

This custom integration allows you to monitor and control your EVC-net (Last Mile Solutions) charging station through Home Assistant.

## Features

- **Sensors**: Monitor charging status, power consumption, and energy usage
- **Switch**: Start and stop charging sessions remotely
- **Real-time updates**: Automatic polling every 30 seconds

## Installation

### HACS (Recommended)

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

### Finding Your Card ID

You have two options to find your RFID card ID:

**Option 1: From Browser**
1. Log in to https://50five-sbelux.evc-net.com in your browser
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
- **Current Power**: Active power draw in watts
- **Total Energy**: Total energy consumed (kWh)
- **Session Energy**: Energy consumed in current session (kWh)

### Switch
- **Charging**: Turn on to start charging, off to stop

## Notes

⚠️ **Important**: To start charging, the integration needs `customer_id` and `card_id`. These should be automatically detected from your account. If starting charging fails, you may need to manually configure these IDs.

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
