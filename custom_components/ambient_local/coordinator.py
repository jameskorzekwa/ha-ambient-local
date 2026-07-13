"""Coordinator: holds pushed data, self-heals the console, tracks staleness."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .console import ConsoleClient, ConsoleError, detect_local_ip
from .const import (
    CONSOLE_PATH,
    DEFAULT_UPLOAD_SECONDS,
    DOMAIN,
    STALE_INTERVAL_FACTOR,
    STALE_MIN_SECONDS,
)
from .parser import parse_payload

_LOGGER = logging.getLogger(__name__)


class AmbientCoordinator(DataUpdateCoordinator[dict]):
    """Owns the live sensor snapshot and keeps the console pointed at us."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: ConsoleClient,
        listen_port: int,
        scan_minutes: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=scan_minutes),
        )
        self.client = client
        self.listen_port = listen_port
        self.upload_seconds = DEFAULT_UPLOAD_SECONDS

        self.sensors: dict = {}
        self.last_push: datetime | None = None
        self.station_mac: str | None = None
        self.settings_ok: bool | None = None

    @callback
    def handle_push(self, raw: dict) -> None:
        """Called from the listener whenever the console sends data."""
        self.sensors = parse_payload(raw)
        self.last_push = dt_util.utcnow()
        self.async_set_updated_data(self._snapshot())

    def _snapshot(self) -> dict:
        return {
            "sensors": self.sensors,
            "last_push": self.last_push,
            "settings_ok": self.settings_ok,
        }

    async def _async_update_data(self) -> dict:
        """Periodic tick: ensure the console still points at us. Never raises."""
        await self._ensure_console()
        return self._snapshot()

    async def _ensure_console(self) -> None:
        """Read the console config and re-apply it if it has drifted/wiped."""
        try:
            settings = await self.client.get_settings()
        except ConsoleError as err:
            self.settings_ok = None
            _LOGGER.debug("Console not reachable for config check: %s", err)
            return

        self.station_mac = settings.get("sta_mac")
        ha_ip = await self.hass.async_add_executor_job(
            detect_local_ip, self.client.ip
        )
        if not ha_ip:
            _LOGGER.debug("Could not determine local IP toward console")
            return

        drift = (
            settings.get("Customized") != "enable"
            or settings.get("ecowitt_ip") != ha_ip
            or settings.get("ecowitt_path") != CONSOLE_PATH
            or str(settings.get("ecowitt_port")) != str(self.listen_port)
        )

        if not drift:
            self.settings_ok = True
            return

        _LOGGER.warning(
            "Console Custom Server drifted (ip=%s path=%s port=%s); re-applying "
            "ip=%s path=%s port=%s",
            settings.get("ecowitt_ip"),
            settings.get("ecowitt_path"),
            settings.get("ecowitt_port"),
            ha_ip,
            CONSOLE_PATH,
            self.listen_port,
        )
        payload = {
            "ost_interval": settings.get("ost_interval", "1"),
            "Customized": "enable",
            # On the "amb" platform this is stored as "amb_protocol".
            "Protocol": "ecowitt",
            "ambEmail": settings.get("ambEmail", ""),
            "ecowitt_ip": ha_ip,
            "ecowitt_path": CONSOLE_PATH,
            "ecowitt_port": str(self.listen_port),
            "ecowitt_upload": str(self.upload_seconds),
        }
        try:
            await self.client.set_settings(payload)
            self.settings_ok = True
            _LOGGER.info("Console Custom Server settings re-applied")
        except ConsoleError as err:
            self.settings_ok = False
            _LOGGER.error("Failed to re-apply console settings: %s", err)

    async def async_reapply_settings(self) -> None:
        """Force a config re-apply (used by the service)."""
        await self._ensure_console()
        self.async_set_updated_data(self._snapshot())

    @property
    def data_is_fresh(self) -> bool:
        if self.last_push is None:
            return False
        grace = timedelta(
            seconds=max(self.upload_seconds * STALE_INTERVAL_FACTOR, STALE_MIN_SECONDS)
        )
        return dt_util.utcnow() - self.last_push < grace
