"""The Ambient Weather Local integration.

Runs its own HTTP listener for the console's data push (no add-on required) and
keeps the console's "Custom Server" settings pointed at us, re-applying them if
the console wipes them on reboot.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .console import ConsoleClient
from .const import (
    CONF_CONSOLE_IP,
    CONF_LISTEN_PORT,
    CONF_SCAN_MINUTES,
    DEFAULT_LISTEN_PORT,
    DEFAULT_SCAN_MINUTES,
    DOMAIN,
)
from .coordinator import AmbientCoordinator
from .listener import PushListener

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.WEATHER]
SERVICE_REAPPLY = "reapply_console_settings"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    client = ConsoleClient(session, entry.data[CONF_CONSOLE_IP])
    port = entry.options.get(
        CONF_LISTEN_PORT, entry.data.get(CONF_LISTEN_PORT, DEFAULT_LISTEN_PORT)
    )
    scan_minutes = entry.options.get(
        CONF_SCAN_MINUTES, entry.data.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES)
    )

    coordinator = AmbientCoordinator(hass, client, port, scan_minutes)
    listener = PushListener(port, coordinator.handle_push)
    try:
        await listener.start()
    except OSError as err:
        # Port busy (e.g. another service or a stale bind). Retry later rather
        # than failing permanently.
        raise ConfigEntryNotReady(
            f"Could not bind listener on port {port}: {err}"
        ) from err

    try:
        # Non-raising: the listener must stay up even if the console is momentarily
        # unreachable during setup. _async_update_data never raises.
        await coordinator.async_refresh()

        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
            "coordinator": coordinator,
            "listener": listener,
        }

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        # Never leak the bound port if the rest of setup fails.
        await listener.stop()
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        raise

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    if not hass.services.has_service(DOMAIN, SERVICE_REAPPLY):

        async def _handle_reapply(call: ServiceCall) -> None:
            for data in hass.data.get(DOMAIN, {}).values():
                await data["coordinator"].async_reapply_settings()

        hass.services.async_register(DOMAIN, SERVICE_REAPPLY, _handle_reapply)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    data = hass.data[DOMAIN].pop(entry.entry_id, None)
    if data is not None:
        await data["listener"].stop()
    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, SERVICE_REAPPLY)
    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
