# Changelog

All notable changes to this project will be documented in this file.

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
