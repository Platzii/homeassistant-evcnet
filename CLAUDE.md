# CLAUDE.md

Home Assistant custom integration (HACS) `evcnet` for EV chargers managed via the **EVC-net** white-label portal by Last Mile Solutions (mostly 50five: `50five-sbelux`, `-snl`, `-sde`, `-suk`, `50five.evc-net.com`, also others). No official API: the integration logs in to the web portal and calls its internal AJAX endpoint.

## Layout

`custom_components/evcnet/`
- `api.py` – `EvcNetApiClient`: portal login (password + email OTP), cookie handling, AJAX calls.
- `session.py` – per-account cookie persistence in HA `Store` (`.storage/evcnet.session.<hash>`).
- `coordinator.py` – `DataUpdateCoordinator`, polls every 30 s: `networkOverview` (spot list, cached), per spot `overview` + `totalUsage` + `log` per channel.
- `config_flow.py` – steps `user` / `reconfigure` / `reauth_confirm` → `otp` → `card_config`; options flow (card id, customer id, max channels).
- `sensor.py`, `switch.py` (charging on/off), `button.py` (reset/unlock/block/refresh) – entities. Unique IDs `"{spot_id}_{key}"` (+ channel suffix when max_channels > 1). **Never change unique IDs** – breaks users' history/automations.
- `__init__.py` – setup/unload, service registration (`start_charging`, `stop_charging`, `refresh_status`, `soft_reset`, `hard_reset`, `unlock_connector`, `block`, `unblock`).
- `const.py`, `services.yaml`, `translations/{en,nl}.json`, `manifest.json` (version lives here).
- `tests/` – pytest, uses a local fake portal server.

## Portal API

- AJAX: `POST {base}/api/ajax`, form field `requests=<json>`; json `{"0": {"handler": "\\LMS\\EV\\AsyncServices\\<Service>", "method": ..., "params": {...}}}`. Services: `DashboardAsyncService` (`networkOverview`, `totalUsage`), `RechargeSpotsAsyncService` (`overview`, `log`, `action` with `StartTransaction`/`StopTransaction`/`GetStatus`/`SoftReset`/`HardReset`/`UnlockConnector`/`Block`/`Unblock`).
- Login: `GET /Login/Login` (CSRF `_token`) → `POST /Login/Login` (`emailField`, `passwordField`, `_token`) → 302.
  - `Location` → `Login/Login` = bad credentials; → `/2fa` = OTP required (BELUX); → `/Overview` then GET bounces to `/2fa` (NL).
- 2FA (since 2026-07, mandatory for 50five since ~2026-09): `GET /2fa` form (`_token`, `_auth_code`, trust-device checkbox) → `POST /2fa_check` (`_token`, `_auth_code`, `VerifyOtp`). Back to `/2fa` = wrong code; to `Login/Login` = challenge lost.
- Cookies: `PHPSESSID` (rotates after login and 2FA), `SERVERID` (LB pinning). Per-account cookie jar, never the shared HA session.

## Gotchas

- An expired or half-authenticated session returns **HTTP 200 with body `[]`**, not an error. For data calls that means "log in again". `action` calls legitimately return `[]` as their ack.
- NL portal redirects downgrade to `http://` on the same host: rewrite to https.
- Every new password login sends a new email code and invalidates the previous one. Never auto-login in a loop when OTP is required: raise `ConfigEntryAuthFailed` → reauth flow.
- Expiry handling (`api.py` `_make_ajax_request`): one silent password login; if that needs OTP → `_needs_reauth` + `auth_expired()` → reauth flow. No further logins until reauth succeeds (each login = new email).
- The jar is never fully cleared on login/expiry (only `PHPSESSID`), so a trusted-device cookie survives.
- 50five support says sharing credentials with third parties is against ToS; a reverse-engineered app GraphQL API (no OTP, 50five-only) exists – see PR #43, not used.

## Workflow

- Branches: features/fixes → PR into `develop`; release = merge `develop` → `main`, bump `manifest.json` version + `CHANGELOG.md`, push tag `vX.Y.Z` → `release.yaml` zips the component into `homeassistant-evcnet.zip`. Tags with a `b`/`-` (e.g. `v1.1.0b1`) become GitHub prereleases → HACS shows them only to users with "Show beta versions" enabled.
- CI: `validate.yaml` (HACS + hassfest), `test.yml` (pytest).
- Run tests: `python -m pip install -r requirements-test.txt && python -m pytest`.
- Never log credentials, OTP codes or full session ids.
