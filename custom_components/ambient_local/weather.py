"""Weather entity aggregating the station's readings."""
from __future__ import annotations

from homeassistant.components.weather import (
    WeatherEntity,
    WeatherEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME, DOMAIN
from .entity import AmbientEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    device_name = entry.options.get(
        CONF_DEVICE_NAME, entry.data.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME)
    )
    async_add_entities([AmbientWeather(coordinator, device_name, entry.entry_id)])


class AmbientWeather(AmbientEntity, WeatherEntity):
    _attr_name = None  # use the device name
    _attr_native_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_native_pressure_unit = UnitOfPressure.INHG
    _attr_native_wind_speed_unit = UnitOfSpeed.MILES_PER_HOUR
    _attr_supported_features = WeatherEntityFeature(0)

    def __init__(self, coordinator, device_name, entry_id):
        super().__init__(coordinator, device_name, entry_id)
        self._attr_unique_id = f"{entry_id}_weather"

    def _get(self, key):
        val = self.coordinator.sensors.get(key)
        return val if isinstance(val, (int, float)) else None

    @property
    def native_temperature(self):
        return self._get("temp")

    @property
    def humidity(self):
        return self._get("humidity")

    @property
    def native_pressure(self):
        return self._get("rel_pressure")

    @property
    def native_wind_speed(self):
        return self._get("wind_speed")

    @property
    def wind_bearing(self):
        return self._get("wind_dir")

    @property
    def native_dew_point(self):
        return self._get("dew_point")

    @property
    def available(self) -> bool:
        return self.coordinator.data_is_fresh

    @property
    def condition(self) -> str | None:
        """Best-effort condition from rain, solar radiation and time of day."""
        rain_rate = self._get("rain_rate")
        solar = self._get("solar_rad")
        if rain_rate and rain_rate > 0:
            return "pouring" if rain_rate > 0.3 else "rainy"

        now = dt_util.now()
        is_day = 6 <= now.hour < 20
        if not is_day:
            return "clear-night"
        if solar is None:
            return None
        if solar >= 400:
            return "sunny"
        if solar >= 120:
            return "partlycloudy"
        return "cloudy"
