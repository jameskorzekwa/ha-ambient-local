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
import contextlib
import logging
from dataclasses import dataclass
from typing import Protocol

from .console import ConsoleClient, ConsoleError
from .const import AP_HOST, AP_SSID_PREFIX, CONSOLE_PATH
from .supervisor import SupervisorNetwork

_LOGGER = logging.getLogger(__name__)

# How long to wait for the console to reboot onto the chosen Wi-Fi and reach us.
# Cold reboot + DHCP + first upload is typically 20-40 s; 90 s is a safe ceiling.
VERIFY_TIMEOUT_S = 90
# When no push arrives, probe whether the console fell back to its setup AP
# (which means the Wi-Fi join failed and it's still recoverable).
REJOIN_ATTEMPTS = 8
REJOIN_INTERVAL_S = 2
# Confirming the console answers on its AP right after we associate to it.
AP_REACH_ATTEMPTS = 15
AP_REACH_INTERVAL_S = 2

# Outcome codes from provision_and_verify(); each maps to a user-facing message.
OK = "ok"  # console reached Home Assistant — safe to commit
PROVISION_FAILED = "provision_failed"  # console rejected settings over the AP (retry)
JOIN_FAILED = "join_failed"  # console fell back to AP mode — Wi-Fi join failed (retry)
UNREACHABLE = (
    "unreachable"  # console left AP mode but can't reach HA (needs user action)
)
PORT_BUSY = "port_busy"  # our listener port is already in use (pick another, retry)


class PushWatcher(Protocol):
    """Watches for the console's first data push after it joins the Wi-Fi.

    Entered (``async with``) *before* the radio is released so no early push is
    missed; ``wait`` returns the console's source IP, or None on timeout.
    """

    async def __aenter__(self) -> PushWatcher: ...

    async def __aexit__(self, *exc: object) -> None: ...

    async def wait(self, timeout: float) -> str | None: ...  # noqa: ASYNC109


@dataclass
class ProvisionResult:
    """Outcome of a verified provisioning attempt."""

    status: str
    console_ip: str | None = None
    detail: str = ""


async def _safe_disable(sup: SupervisorNetwork, interface: str) -> None:
    """Release the borrowed radio, never raising — we must not strand it enabled."""
    with contextlib.suppress(Exception):
        await sup.disable(interface)


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


async def join_and_reach(
    session, sup: SupervisorNetwork, interface: str, ap_ssid: str
) -> bool:
    """Associate ``interface`` to the console's open setup AP and confirm it answers."""
    try:
        await sup.join(interface, ap_ssid, psk=None)
    except Exception as err:  # noqa: BLE001 - supervisor/radio errors are all "no"
        _LOGGER.debug("Could not join setup AP '%s': %s", ap_ssid, err)
        return False
    ap_console = ConsoleClient(session, AP_HOST)
    for _ in range(AP_REACH_ATTEMPTS):
        try:
            await ap_console.get_device_info()
            return True
        except ConsoleError:
            await asyncio.sleep(AP_REACH_INTERVAL_S)
    return False


async def push_settings_over_ap(
    session,
    cached: dict,
    target_ssid: str,
    target_psk: str,
    ha_ip: str,
    listen_port: int,
) -> None:
    """Push Wi-Fi creds (required) + Custom Server settings (best effort) over the AP.

    Assumes we're already associated to the console's AP. Raises ConsoleError only
    if the essential Wi-Fi push fails; a failed ws-settings push is tolerated
    because the coordinator self-heals it once the console is back on the LAN.
    """
    ap_console = ConsoleClient(session, AP_HOST)
    await ap_console.set_network_info(
        build_network_payload(cached.get("network") or {}, target_ssid, target_psk)
    )
    try:
        await ap_console.set_settings(
            build_ws_payload(cached.get("ws") or {}, ha_ip, listen_port)
        )
    except ConsoleError as err:
        _LOGGER.warning("Wi-Fi creds pushed but ws-settings push failed: %s", err)


async def classify_after_timeout(
    session, sup: SupervisorNetwork, interface: str, ap_ssid: str
) -> str:
    """No push arrived — decide whether it's recoverable, and release the radio.

    If the console's setup AP is broadcasting again, its Wi-Fi join failed and it
    fell back to AP mode (JOIN_FAILED — the user can retry). If we can't get back
    to it, the console joined a network it can reach but Home Assistant can't
    (UNREACHABLE — needs the user to re-enter setup and pick a same-LAN network).
    """
    ap_console = ConsoleClient(session, AP_HOST)
    try:
        for _ in range(REJOIN_ATTEMPTS):
            try:
                await sup.join(interface, ap_ssid, psk=None)
                await ap_console.get_device_info()
                return JOIN_FAILED
            except Exception:  # noqa: BLE001 - AP not back yet; keep probing
                await asyncio.sleep(REJOIN_INTERVAL_S)
        return UNREACHABLE
    finally:
        await _safe_disable(sup, interface)


async def provision_and_verify(
    session,
    sup: SupervisorNetwork,
    interface: str,
    ap_ssid: str,
    target_ssid: str,
    target_psk: str,
    cached: dict,
    ha_ip: str,
    listen_port: int,
    watcher: PushWatcher,
) -> ProvisionResult:
    """Provision the console, release the radio, and verify it can reach us.

    Assumes we're already associated to the console's setup AP. Order matters:
    the watcher is armed *before* the radio is released so the console's first
    push can't slip through. The borrowed radio is always released, on every
    path, so the host is never left in a bad state.
    """
    async with watcher:  # arm the push watcher first (may bind a listener)
        try:
            await push_settings_over_ap(
                session, cached, target_ssid, target_psk, ha_ip, listen_port
            )
        except ConsoleError as err:
            await _safe_disable(sup, interface)
            return ProvisionResult(PROVISION_FAILED, detail=str(err))

        # Release the radio: the console drops its AP, reboots, and joins the Wi-Fi.
        await _safe_disable(sup, interface)
        console_ip = await watcher.wait(VERIFY_TIMEOUT_S)

    if console_ip:
        return ProvisionResult(OK, console_ip=console_ip)

    # Nothing reached us in time — work out why, and clean up the radio.
    return ProvisionResult(
        await classify_after_timeout(session, sup, interface, ap_ssid)
    )


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
