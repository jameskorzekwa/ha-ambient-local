"""Tiny HTTP listener that receives the console's data push.

The console performs a periodic ``GET /?<field>=<value>&...`` (Ecowitt/AMBWeather
"Custom Server" style). We run our own aiohttp server so no separate add-on is
required. HA Core uses host networking, so binding here is reachable on the LAN.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from aiohttp import web

_LOGGER = logging.getLogger(__name__)


class PushListener:
    """Standalone aiohttp server for inbound station data."""

    def __init__(self, port: int, on_data: Callable[[dict], None]) -> None:
        self._port = port
        self._on_data = on_data
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = web.Application()
        # Accept any method/path; the console uses GET "/" with a query string.
        app.router.add_route("*", "/{tail:.*}", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._port)
        await site.start()
        _LOGGER.info("Ambient Weather listener started on port %s", self._port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            _LOGGER.info("Ambient Weather listener on port %s stopped", self._port)

    async def _handle(self, request: web.Request) -> web.Response:
        data: dict = dict(request.query)
        if not data and request.body_exists:
            try:
                data = dict(await request.post())
            except Exception:  # noqa: BLE001 - malformed body, ignore
                data = {}
        if data:
            self._on_data(data)
        else:
            _LOGGER.debug("Received request with no data: %s", request.rel_url)
        return web.Response(text="OK")
