"""Client for the Ambient Weather console's local web API.

The console exposes an (effectively unauthenticated) JSON API:
  GET  /get_ws_settings  -> current Custom Server configuration
  POST /set_ws_settings  -> apply Custom Server configuration

On the "amb" platform the Protocol value is sent as "ecowitt" but stored as
"amb_protocol"; that is expected and not a drift.
"""
from __future__ import annotations

import logging
import socket

import aiohttp

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=10)


class ConsoleError(Exception):
    """Raised when the console cannot be reached or returns an error."""


class ConsoleClient:
    """Talks to the weather-station console over HTTP."""

    def __init__(self, session: aiohttp.ClientSession, ip: str) -> None:
        self._session = session
        self._ip = ip

    @property
    def ip(self) -> str | None:
        return self._ip

    def set_ip(self, ip: str) -> None:
        self._ip = ip

    async def get_settings(self, timeout_s: float | None = None) -> dict:
        """Return the console's current weather-server settings.

        ``timeout_s`` overrides the default timeout — useful for a fast
        reachability probe in the config flow.
        """
        url = f"http://{self._ip}/get_ws_settings"
        timeout = aiohttp.ClientTimeout(total=timeout_s) if timeout_s else _TIMEOUT
        try:
            async with self._session.get(url, timeout=timeout) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ConsoleError(f"get_ws_settings failed: {err}") from err

    async def set_settings(self, payload: dict) -> None:
        """Apply weather-server settings on the console."""
        await self._post("set_ws_settings", payload)

    # --- generic helpers ---------------------------------------------------

    async def _get(self, endpoint: str) -> dict:
        url = f"http://{self._ip}/{endpoint}"
        try:
            async with self._session.get(url, timeout=_TIMEOUT) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ConsoleError(f"{endpoint} failed: {err}") from err

    async def _post(self, endpoint: str, payload: dict) -> None:
        url = f"http://{self._ip}/{endpoint}"
        try:
            async with self._session.post(url, json=payload, timeout=_TIMEOUT) as resp:
                resp.raise_for_status()
                await resp.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ConsoleError(f"{endpoint} failed: {err}") from err

    # --- network / device / scan (used for provisioning) -------------------

    async def get_network_info(self) -> dict:
        return await self._get("get_network_info")

    async def set_network_info(self, payload: dict) -> None:
        await self._post("set_network_info", payload)

    async def get_device_info(self) -> dict:
        return await self._get("get_device_info")

    async def set_device_info(self, payload: dict) -> None:
        await self._post("set_device_info", payload)

    async def scan_ssids(self) -> list[dict]:
        """Networks the console itself can see (used in AP/setup mode)."""
        data = await self._get("usr_scan_ssid_list")
        return data.get("list", [])


def detect_local_ip(target_ip: str) -> str | None:
    """Return the local IP the host would use to reach ``target_ip``.

    This is the address the console must POST back to. Uses a UDP socket, which
    does not actually send any packets. Run inside an executor.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((target_ip, 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()
