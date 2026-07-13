"""Binary sensors: battery status and console-config health."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME, DOMAIN
from .entity import AmbientEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    device_name = entry.options.get(
        CONF_DEVICE_NAME, entry.data.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME)
    )
    async_add_entities(
        [
            AmbientBatteryLow(coordinator, device_name, entry.entry_id),
            AmbientConsoleProblem(coordinator, device_name, entry.entry_id),
        ]
    )


class AmbientBatteryLow(AmbientEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.BATTERY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Battery"

    def __init__(self, coordinator, device_name, entry_id):
        super().__init__(coordinator, device_name, entry_id)
        self._attr_unique_id = f"{entry_id}_battery_low"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.sensors.get("battery_low")

    @property
    def available(self) -> bool:
        return (
            self.coordinator.data_is_fresh
            and "battery_low" in self.coordinator.sensors
        )


class AmbientConsoleProblem(AmbientEntity, BinarySensorEntity):
    """On when the console isn't reachable or its Custom Server config is wrong."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Console configuration"

    def __init__(self, coordinator, device_name, entry_id):
        super().__init__(coordinator, device_name, entry_id)
        self._attr_unique_id = f"{entry_id}_console_problem"

    @property
    def is_on(self) -> bool | None:
        ok = self.coordinator.settings_ok
        if ok is None:
            return None
        return not ok
