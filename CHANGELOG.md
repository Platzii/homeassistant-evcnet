# Changelog

All notable changes to this project will be documented in this file.

## [1.0.0] - 2026-02-12

This release introduces multi-channel support and is versioned 1.0.0 to reflect the impact for users with multi-connector charging stations.

🙏 A big shout-out to @nikagl for creating the multi-channel support, log retrieval, refresh status and stop charging features.

### Added
- **Log retrieval**: New sensors show charging log summary, last log time, and last log notification. The log summary sensor exposes a markdown table of recent sessions (usable in a Markdown card).
- **Multi-channel support**: For charging stations with multiple connectors, you can set "Max channels" in the integration options to get per-channel sensors and switches (e.g. "Ch 1 Charging", "Ch 2 Charging"). Single-connector setups are unchanged.
- **Refresh status**: New button and `evcnet.refresh_status` action to trigger an immediate status update from the portal.
- **Stop charging**: New `evcnet.stop_charging` action to stop the current charging session via an automation or script.

### Changed
- Session energy sensor now uses the correct state class for cumulative energy (total_increasing).
- Log data (entries, table, markdown) is attached to the log summary sensor and is not stored in history, avoiding Recorder warnings while keeping the data available for the UI.
- Improved login handling (browser-like headers, retry limit and backoff) for more reliable authentication across different EVC-net endpoints.
- Reduced logging noise: only useful messages at normal level; technical details moved to debug.

## [0.2.1] - 2025-12-10

### Fixed
- Locale-aware parsing of total_energy_usage with proper unit conversion
- Add CONFIG_SCHEMA to satisfy hassfest validation

## [0.2.0] - 2025-12-09

### Added
- Button entities for charging spot control: soft & hard reset, unlock connector, block & unblock (#8, @nikagl)

### Fixed
- Respect SERVERID cookie from evcnet to maintain session persistence after Home Assistant restart (#11, @fredericvl)

## [0.1.0] - 2025-11-13

### Added
- Action call `evcnet.start_charging` which supports an optional `card_id` parameter
- Changelog

## [0.0.10] - 2025-10-24

### Fixed
- Release workflow write permissions

## [0.0.9] - 2025-10-24

### Added
- GitHub Actions release workflow
- GitHub Actions validate workflow

### Fixed
- Default value for total_energy_usage should be an integer
- Translations placement for reconfigure flow
- Manifest keys sorting

## [0.0.8] - 2025-10-22

### Changed
- Removed autodetect customer_id logic

## [0.0.7] - 2025-10-22

### Changed
- Improved logging consistency
- Removed unnecessary fallback logic

### Fixed
- Update entity state on failed charging transactions
- Default channel is 1

## [0.0.6] - 2025-10-22

### Changed
- Refactored constants, switch is_on and extra_state_attributes logic

## [0.0.5] - 2025-10-21

### Changed
- Use hours (decimal) instead of minutes for session time
- Improved logging

### Fixed
- Switch logic after renaming 'overview' to 'status'
- Password logging removed

## [0.0.4] - 2025-10-21

### Added
- Disclaimer to README

### Fixed
- Properly parse transaction time
- Deprecation warnings and error handling
- Configuration validation on setup

## [0.0.3] - 2025-10-21

### Fixed
- Deprecation warnings in reconfigure flow

## [0.0.2] - 2025-10-21

### Added
- Options flow for updating card ID and customer ID
- Translation support

### Changed
- Removed unused constants

## [0.0.1] - 2025-10-21

### Added
- Initial release
- Sensor platform for monitoring charging status
- Switch platform for starting/stopping charging sessions
- Config flow for integration setup
- Auto-detection of RFID card ID
