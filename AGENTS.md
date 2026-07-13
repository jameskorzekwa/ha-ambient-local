# AGENTS.md — ha-ambient-local

OVERVIEW: Self-contained, self-healing Home Assistant custom integration for
local Ambient Weather consoles (WS-2902/2000/5000, AMBWeatherPro fw). Runs its
OWN aiohttp push listener (no companion add-on), auto-discovers the console IP
from the data push, re-applies the console's "Custom Server" config when it
drifts/wipes, and can re-provision a factory-reset console over its setup AP.
Stack: Python 3 / HA `DataUpdateCoordinator` + config/options flow, aiohttp,
Supervisor network API. Distributed via HACS.

## STRUCTURE
```
custom_components/ambient_local/
  __init__.py        setup: bind listener, start coordinator, register service
  config_flow.py     initial setup + options; AP-mode recovery wizard (2 flows)
  coordinator.py     live snapshot + self-heal loop + config cache + staleness
  console.py         HTTP client for console local API; detect_local_ip() helper
  listener.py        standalone aiohttp server for the console's GET/? push
  supervisor.py      HA Supervisor /network wrapper (borrow spare wifi radio)
  provision.py       AP-mode re-provisioning + manual-instructions text builder
  parser.py          raw query-string -> normalized keys; derives dew pt/feels-like
  const.py           ports, paths, AP constants, store keys, staleness tuning
  entity.py          AmbientEntity base (DeviceInfo, one device per entry)
  sensor.py          weather readings (SENSORS table) + Last-update diagnostic
  binary_sensor.py   Battery + "Console configuration" problem sensor
  weather.py         aggregate WeatherEntity + best-effort condition
  brand/             icon.png/logo.png — brand images (HA brands repo mirror)
  strings.json / translations/en.json / icons.json / services.yaml
hacs.json            HACS metadata (content_in_root:false)
manifest.json        domain=ambient_local, iot_class=local_push, no requirements
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Add a sensor | `parser.py` FIELD_MAP + `sensor.py` SENSORS tuple | add raw->key map AND an EntityDescription; unique_id = `{entry_id}_{key}` |
| Change setup/provisioning flow | `config_flow.py` | `AmbientConfigFlow` (add) + `AmbientOptionsFlow` (recover); AP logic in `provision.py` |
| Adjust self-heal / drift check | `coordinator.py::_ensure_console` | drift = Customized/ip/path/port mismatch |
| Change the listener/port | `listener.py` + `const.py DEFAULT_LISTEN_PORT` | binds 0.0.0.0; default 7080 |
| Change what config is cached | `coordinator.py::_snapshot_config` | wifi_pwd stripped before persist |
| Wifi radio borrow logic | `supervisor.py::spare_wifi_interface` | wireless iface where `primary` is falsy |
| Derived values (dew pt/feels) | `parser.py` `_dew_point_f`/`_feels_like_f` | Magnus / NWS heat-index+wind-chill |
| Weather condition mapping | `weather.py::condition` | from rain_rate + solar_rad + hour |

## CODE MAP — data flow
1. **listener.py** `PushListener` binds `0.0.0.0:<port>`, accepts ANY method/path
   (`/{tail:.*}`), reads query (or POST body), calls `on_data(dict, request.remote)`.
2. **coordinator.py** `handle_push` = the on_data cb: `parse_payload` -> `sensors`,
   stamps `last_push`, and **learns console IP from `request.remote`** (source IP of
   the push — the user never types it). `async_set_updated_data` fans out to entities.
3. Periodic tick `_async_update_data` -> `_ensure_console`: GET `/get_ws_settings`,
   snapshot config, compute drift, POST `/set_ws_settings` to re-apply. NEVER raises.
4. **entities** read `coordinator.sensors[key]`; `available` gated on
   `coordinator.data_is_fresh` (except Last-update, always available).

**Provisioning (factory-reset console, no phone):**
- `config_flow.async_step_provision` / options `async_step_provision`:
  `supervisor.spare_wifi_interface()` -> `sup.scan` -> `find_setup_ap` (open SSID
  `AMBWeatherPro-<mac6>`) -> `sup.join(open)` -> poll `ConsoleClient(192.168.4.1)` ->
  push `set_network_info` (target wifi) + `set_ws_settings` (email + our server) ->
  `sup.disable(iface)` releases the radio. Console reboots onto wifi.
- No spare radio -> `async_step_manual` shows `provision.manual_instructions` text.

**console.py** `ConsoleClient` endpoints: `get/set_ws_settings`, `get/set_network_info`,
`get_device_info`, `usr_scan_ssid_list`. Unauthenticated HTTP. `detect_local_ip(target)`
opens a UDP socket (sends nothing) to learn which local IP the console POSTs back to;
call inside an executor.

## CONVENTIONS
- One HA **device per config entry**; `DeviceInfo.identifiers = {(DOMAIN, entry_id)}`,
  `configuration_url = http://<console_ip>`.
- Sensor state classes chosen for long-term statistics: rain totals =
  `TOTAL_INCREASING`, everything else `MEASUREMENT`. Diagnostic-category sensors set
  `diagnostic=True` in the description (abs pressure, inside temp/humidity).
- Config persisted via `Store(STORE_VERSION, STORE_KEY)` — the cache SURVIVES entry
  deletion (used to pre-fill recovery). Wi-Fi password is never persisted.
- Options override data: everywhere reads `options.get(K, data.get(K, DEFAULT))`.

## ANTI-PATTERNS / GOTCHAS (this repo)
- **Do NOT default the port to 8099** — the SSH/ttyd add-on binds it; HA Core uses
  host networking so it would clash. Default is **7080** (matches the old add-on so
  cutover needs no console reconfigure). See `const.py`.
- **CONSOLE_PATH must be `"/?"`** — without the `?` the console emits a malformed
  request line (`/&field=...`). Do not "clean up" to `/`.
- **Console IP is discovered from the push**, not entered by the user. `CONF_CONSOLE_IP`
  may be absent in entry.data; `ConsoleClient` starts with ip possibly None; self-heal
  no-ops until an IP is known (from push or cached `ip`).
- **Platform quirk:** console stores `Protocol` as `amb_protocol` even though we POST
  `Protocol="ecowitt"`. That is EXPECTED — do not treat it as drift. Drift check only
  compares Customized/ecowitt_ip/ecowitt_path/ecowitt_port.
- `_ensure_console`/`_async_update_data` must never raise — the listener has to stay
  up even if the console is briefly unreachable. Catch `ConsoleError`, set
  `settings_ok`, log, return.
- `coordinator.on_ip_discovered` hook exists but is currently unset by `__init__.py`
  (IP persists via `_snapshot_config` writing `ip` into the store instead).
- `DeviceInfo` imported from `homeassistant.helpers.device_registry` (not `helpers.entity`).
- Brand icons live in `custom_components/.../brand/`; the real HA brands repo needs a
  separate PR — this dir is just a mirror.
- Supervisor features (spare-radio provisioning) only work on HA OS/Supervised
  (`SUPERVISOR_TOKEN` present); config flow degrades to manual instructions otherwise.

## COMMANDS
- No build/test suite in-repo. Distributed via HACS as a custom repository
  (Integration category). Install = copy `custom_components/ambient_local` to HA
  `config/custom_components/` and restart.
- Bump `manifest.json` `version` to release; HACS tracks GitHub releases/tags.

## NOTES
- `iot_class=local_push`, `requirements=[]`, `dependencies=[]` — pure stdlib + HA deps.
- Service `ambient_local.reapply_console_settings` forces an immediate re-apply
  (calls every coordinator's `async_reapply_settings`).
- Staleness: entities go unavailable after `max(upload_seconds*4, 300s)` with no push.
