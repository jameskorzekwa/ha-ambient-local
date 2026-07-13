"""Thin client for the Home Assistant Supervisor network API.

Used to drive a *spare* Wi-Fi radio on the host so we can join the console's
setup access point and re-provision it. Only usable on HA OS / Supervised, and
only when there is a wireless interface that isn't the primary connection.
"""
from __future__ import annotations

import logging
import os

import aiohttp

_LOGGER = logging.getLogger(__name__)

_BASE = "http://supervisor"
_TIMEOUT = aiohttp.ClientTimeout(total=15)


def supervisor_available() -> bool:
    """True if we're running under the Supervisor (token present)."""
    return bool(os.environ.get("SUPERVISOR_TOKEN"))


class SupervisorNetwork:
    """Minimal wrapper over /network on the Supervisor API."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._headers = {"Authorization": f"Bearer {os.environ.get('SUPERVISOR_TOKEN', '')}"}

    async def _get(self, path: str) -> dict:
        async with self._session.get(f"{_BASE}{path}", headers=self._headers, timeout=_TIMEOUT) as r:
            r.raise_for_status()
            return (await r.json()).get("data", {})

    async def _post(self, path: str, payload: dict) -> dict:
        async with self._session.post(
            f"{_BASE}{path}", headers=self._headers, json=payload, timeout=_TIMEOUT
        ) as r:
            r.raise_for_status()
            return (await r.json()).get("data", {})

    async def info(self) -> dict:
        return await self._get("/network/info")

    async def spare_wifi_interface(self) -> str | None:
        """Return the name of a wireless interface we can borrow, or None.

        A "spare" radio is a wireless interface that is not the primary
        connection (so borrowing it won't drop Home Assistant).
        """
        try:
            data = await self.info()
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("Supervisor network info failed: %s", err)
            return None
        for iface in data.get("interfaces", []):
            if iface.get("type") == "wireless" and not iface.get("primary"):
                return iface.get("interface")
        return None

    async def scan(self, interface: str) -> list[dict]:
        """Scan for access points on the given wireless interface."""
        data = await self._get(f"/network/interface/{interface}/accesspoints")
        return data.get("accesspoints", [])

    async def join(self, interface: str, ssid: str, psk: str | None = None) -> None:
        """Bring the interface up and associate to an AP (open if psk is None)."""
        wifi = {"mode": "infrastructure", "ssid": ssid}
        wifi["auth"] = "wpa-psk" if psk else "open"
        if psk:
            wifi["psk"] = psk
        await self._post(
            f"/network/interface/{interface}/update",
            {
                "enabled": True,
                "ipv4": {"method": "auto"},
                "ipv6": {"method": "disabled"},
                "wifi": wifi,
            },
        )

    async def interface_info(self, interface: str) -> dict:
        return await self._get(f"/network/interface/{interface}/info")

    async def disable(self, interface: str) -> None:
        await self._post(f"/network/interface/{interface}/update", {"enabled": False})
