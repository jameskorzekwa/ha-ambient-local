"""Coordinator: holds pushed data, self-heals the console, tracks staleness."""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .console import ConsoleClient, ConsoleError, detect_local_ip
from .const import (
    CONSOLE_PATH,
    DEFAULT_UPLOAD_SECONDS,
    DOMAIN,
    STALE_INTERVAL_FACTOR,
    STALE_MIN_SECONDS,
    STORE_KEY,
    STORE_VERSION,
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

        # The console's IP is learned from its data push (request source IP),
        # so the user never supplies it. May be None until the first push.
        self.console_ip: str | None = client.ip
        self.on_ip_discovered = None  # callback(ip) to persist to the entry

        self.sensors: dict = {}
        self.last_push: datetime | None = None
        self.station_mac: str | None = None
        self.settings_ok: bool | None = None

        # Persisted snapshot of the console config (Wi-Fi password stripped) so
        # AP-mode recovery / manual instructions can restore everything.
        self._store: Store = Store(hass, STORE_VERSION, STORE_KEY)
        self.cached: dict = {}
        self.ha_ip: str | None = None

    async def async_load_cache(self) -> None:
        self.cached = await self._store.async_load() or {}
        # Restore last-known console IP so self-heal works before the first push.
        if not self.console_ip and self.cached.get("ip"):
            self.console_ip = self.cached["ip"]
            self.client.set_ip(self.console_ip)

    @callback
    def handle_push(self, raw: dict, source_ip: str | None = None) -> None:
        """Called from the listener whenever the console sends data."""
        self.sensors = parse_payload(raw)
        self.last_push = dt_util.utcnow()
        if source_ip and source_ip != self.console_ip:
            _LOGGER.info("Discovered console IP from its data push: %s", source_ip)
            self.console_ip = source_ip
            self.client.set_ip(source_ip)
            if self.on_ip_discovered is not None:
                self.on_ip_discovered(source_ip)
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
        if not self.console_ip:
            # IP not learned yet (no data push received). Nothing to check.
            return
        try:
            settings = await self.client.get_settings()
        except ConsoleError as err:
            self.settings_ok = None
            _LOGGER.debug("Console not reachable for config check: %s", err)
            return

        self.station_mac = settings.get("sta_mac")
        await self._snapshot_config(settings)
        ha_ip = await self.hass.async_add_executor_job(detect_local_ip, self.client.ip)
        if not ha_ip:
            _LOGGER.debug("Could not determine local IP toward console")
            return
        self.ha_ip = ha_ip

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

    async def _snapshot_config(self, ws_settings: dict) -> None:
        """Persist ws/network/device config (Wi-Fi password stripped)."""
        snap: dict = {"ws": ws_settings}
        with contextlib.suppress(ConsoleError):
            net = dict(await self.client.get_network_info())
            net.pop("wifi_pwd", None)  # don't persist the secret
            snap["network"] = net
        with contextlib.suppress(ConsoleError):
            snap["device"] = await self.client.get_device_info()
        if not self.station_mac:
            self.station_mac = (snap.get("network") or {}).get("mac")
        if self.console_ip:
            snap["ip"] = self.console_ip
        self.cached = snap
        try:
            await self._store.async_save(snap)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not persist console cache: %s", err)

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
