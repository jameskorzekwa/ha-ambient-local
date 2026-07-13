"""Shared base entity for Ambient Weather Local."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AmbientCoordinator


class AmbientEntity(CoordinatorEntity[AmbientCoordinator]):
    """Base entity tying everything to one station device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: AmbientCoordinator, device_name: str, entry_id: str
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=device_name,
            manufacturer="Ambient Weather",
            model="Weather Station (local)",
            configuration_url=f"http://{coordinator.client.ip}",
        )
