# Ambient Weather Local

A self-contained Home Assistant integration for Ambient Weather consoles
(WS-2902 / WS-2000 / WS-5000, AMBWeatherPro firmware) that receive-and-forward
locally via the console's **Custom Server** feature.

Unlike the older add-on + integration approach, this is **one integration** that:

- **Runs its own listener** — no companion add-on. The console pushes straight to
  Home Assistant (its own aiohttp server, like HA's built-in Ecowitt integration).
- **Self-heals the console** — these consoles wipe their Custom Server settings on
  reboot/firmware update. This integration reads the console's config on a schedule
  and, if it has drifted or been wiped, **re-applies it automatically** via the
  console's local web API. A reboot self-corrects within minutes instead of causing
  a silent multi-day outage.
- **Surfaces staleness** — a *Last update* diagnostic timestamp, a *Console
  configuration* problem sensor, and entities that go `unavailable` if the station
  stops reporting, so a failure is impossible to miss.
- **Rich entities** — temperature, humidity, wind (speed/gust/max/direction), solar
  radiation, UV, pressure (rel/abs), rain (rate/event/daily/weekly/monthly/yearly),
  indoor temp/humidity, battery, derived dew point & feels-like, and a weather entity
  — all with correct device/state classes for long-term statistics.

## Installation (HACS)

1. HACS → Integrations → ⋮ → Custom repositories → add
   `https://github.com/jameskorzekwa/ha-ambient-local` (category: Integration).
2. Install **Ambient Weather Local** and restart Home Assistant.
3. Settings → Devices & Services → Add Integration → **Ambient Weather Local**.
4. Enter the console's IP address. Everything else is auto-configured.

## How it works

```
console  --(HTTP push: GET /?tempf=..&humidity=..)-->  HA listener (:8099)  -->  entities
   ^                                                                              |
   |  POST /set_ws_settings  (re-applied on a schedule if drifted)  <------------ coordinator
```

The console's `Protocol` is left on its native `amb_protocol`; the integration
enforces `Customized=enable`, the correct HA IP, port and `path=/?`.

## Console recovery / re-provisioning

These consoles sometimes fully reset and drop to **AP mode** (broadcasting an open
`AMBWeatherPro-<MAC>` network at `http://192.168.4.1`), losing their Wi-Fi. This
integration can recover that **without a phone** — go to the integration →
**Configure → Recover / re-provision console**:

- **If Home Assistant OS has a *spare* Wi-Fi radio** (one that isn't the primary
  connection), HA borrows it via the Supervisor network API, scans for the
  console's setup AP, lets you pick the target 2.4 GHz network + password, then
  joins the AP and pushes back your Wi-Fi, AmbientWeather.net email, and Custom
  Server settings — then releases the radio. Ethernet stays primary throughout,
  so HA never drops off the network.
- **If there's no spare radio** (or the console isn't detected), the same screen
  shows **exact manual instructions** (which AP to join, the URL, and every value
  to enter), pre-filled from the cached config.

The integration proactively caches the console's config (Wi-Fi **password
excluded**) so recovery can restore everything.

## Service

`ambient_local.reapply_console_settings` — force a config re-apply immediately.

## License

MIT
