"""AP-mode recovery: join the console's setup access point and re-provision it.

Requires a spare Wi-Fi radio on the host (not the primary connection). When the
console fully resets it broadcasts an OPEN SSID ``AMBWeatherPro-<macsuffix>`` and
serves its web UI at 192.168.4.1. We borrow the spare radio, join that AP, push
the saved config (Wi-Fi creds, AmbientWeather.net email, our custom server),
then release the radio.
"""

from __future__ import annotations

import asyncio
import base64
import logging

from .console import ConsoleClient, ConsoleError
from .const import AP_HOST, AP_SSID_PREFIX, CONSOLE_PATH
from .supervisor import SupervisorNetwork

_LOGGER = logging.getLogger(__name__)


class ProvisionError(Exception):
    """Recoverable provisioning failure with a human-readable message."""


def b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def find_setup_ap(accesspoints: list[dict], mac: str | None = None) -> str | None:
    """Return the console's setup-AP SSID from a scan list, if present."""
    want = None
    if mac:
        want = AP_SSID_PREFIX + mac.replace(":", "")[-6:].upper()
    for ap in accesspoints:
        ssid = ap.get("ssid") or ""
        if ssid == want or ssid.upper().startswith(AP_SSID_PREFIX.upper()):
            return ssid
    return None


def build_network_payload(current: dict, target_ssid: str, target_psk: str) -> dict:
    """set_network_info body: join target network via DHCP."""
    return {
        "ssid": target_ssid,
        "wifi_pwd": b64(target_psk),
        "staIpType": "0",  # DHCP
        "wifi_DNS": current.get("wifi_DNS", ""),
        "wifi_ip": current.get("wifi_ip", ""),
        "wifi_mask": current.get("wifi_mask", ""),
        "wifi_gateway": current.get("wifi_gateway", ""),
    }


def build_ws_payload(cached_ws: dict, ha_ip: str, listen_port: int) -> dict:
    """set_ws_settings body: restore AmbientWeather.net email + our custom server."""
    return {
        "ost_interval": cached_ws.get("ost_interval", "1"),
        "Customized": "enable",
        "Protocol": "ecowitt",  # stored as amb_protocol on this platform
        "ambEmail": cached_ws.get("ambEmail", ""),
        "ecowitt_ip": ha_ip,
        "ecowitt_path": CONSOLE_PATH,
        "ecowitt_port": str(listen_port),
        "ecowitt_upload": str(cached_ws.get("ecowitt_upload", "60")),
    }


async def provision_via_ap(
    session,
    sup: SupervisorNetwork,
    interface: str,
    target_ssid: str,
    target_psk: str,
    cached: dict,
    ha_ip: str,
    listen_port: int,
) -> None:
    """Borrow ``interface``, join the console's AP, push config, then release it.

    ``cached`` holds the last-known network/ws snapshots. Raises ProvisionError.
    """
    mac = (cached.get("network") or {}).get("mac")
    aps = await sup.scan(interface)
    ap_ssid = find_setup_ap(aps, mac)
    if not ap_ssid:
        raise ProvisionError(
            "The console's setup network wasn't found. Put it in AP mode (hold "
            "the Wi-Fi button ~6s until 'AP' shows) and try again."
        )

    _LOGGER.info("Joining console setup AP '%s' on %s", ap_ssid, interface)
    try:
        await sup.join(interface, ap_ssid, psk=None)  # setup AP is open
    except Exception as err:
        raise ProvisionError(f"Could not join the setup AP: {err}") from err

    try:
        ap_console = ConsoleClient(session, AP_HOST)
        # wait for the AP link + DHCP, confirmed by reaching the console
        for _ in range(15):
            try:
                await ap_console.get_device_info()
                break
            except ConsoleError:
                await asyncio.sleep(2)
        else:
            raise ProvisionError(
                "Joined the AP but couldn't reach the console at " + AP_HOST
            )

        cur_net = cached.get("network") or {}
        await ap_console.set_network_info(
            build_network_payload(cur_net, target_ssid, target_psk)
        )
        try:
            await ap_console.set_settings(
                build_ws_payload(cached.get("ws") or {}, ha_ip, listen_port)
            )
        except ConsoleError as err:
            # network is the essential part; ws settings self-heal once it's back.
            _LOGGER.warning("Restored Wi-Fi but ws-settings push failed: %s", err)
        _LOGGER.info(
            "Console re-provisioned to '%s'; it will reboot onto Wi-Fi", target_ssid
        )
    finally:
        try:
            await sup.disable(interface)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to release %s after provisioning: %s", interface, err)


def manual_instructions(
    cached: dict, ha_ip: str, listen_port: int, mac: str | None
) -> str:
    """Human steps for setting the console up by hand (no spare radio / fallback)."""
    ap = AP_SSID_PREFIX + (mac.replace(":", "")[-6:].upper() if mac else "XXXXXX")
    ws = cached.get("ws") or {}
    email = ws.get("ambEmail") or "(your AmbientWeather.net email)"
    net = cached.get("network") or {}
    ssid = net.get("ssid") or "(your 2.4 GHz Wi-Fi name)"
    return (
        "**Set the weather station up by hand:**\n\n"
        "1. On the console, hold the **Wi-Fi/Sensor** button ~6 s until **AP** shows.\n"
        f"2. On a phone/laptop, join the open Wi-Fi **{ap}**.\n"
        f"3. Browse to **http://{AP_HOST}** (login `admin`, no password).\n"
        f"4. **Wi-Fi:** select **{ssid}** and enter its password (must be 2.4 GHz).\n"
        "5. **Weather Services →** set:\n"
        f"   - AmbientWeather.net email: **{email}**\n"
        "   - Customized: **Enable**, protocol **Ambient/Ecowitt**\n"
        f"   - Server/IP **{ha_ip}**, Port **{listen_port}**, "
        f"Path **{CONSOLE_PATH}**, Interval **60**\n"
        "6. Save. The console reboots onto your Wi-Fi and data resumes within a minute."
    )
