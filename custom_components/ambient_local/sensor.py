"""Sensor platform for Ambient Weather Local."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    DEGREE,
    PERCENTAGE,
    EntityCategory,
    UnitOfIrradiance,
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfVolumetricFlux,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME, DOMAIN
from .entity import AmbientEntity

MEAS = SensorStateClass.MEASUREMENT
TOTAL_INC = SensorStateClass.TOTAL_INCREASING


@dataclass(frozen=True, kw_only=True)
class AmbientSensorDescription(SensorEntityDescription):
    """A sensor backed by a normalized key in the coordinator snapshot."""

    diagnostic: bool = False


SENSORS: tuple[AmbientSensorDescription, ...] = (
    AmbientSensorDescription(
        key="temp",
        name="Temperature",
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=MEAS,
    ),
    AmbientSensorDescription(
        key="feels_like",
        name="Feels like",
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=MEAS,
    ),
    AmbientSensorDescription(
        key="dew_point",
        name="Dew point",
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=MEAS,
    ),
    AmbientSensorDescription(
        key="humidity",
        name="Humidity",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=MEAS,
    ),
    AmbientSensorDescription(
        key="wind_speed",
        name="Wind speed",
        native_unit_of_measurement=UnitOfSpeed.MILES_PER_HOUR,
        device_class=SensorDeviceClass.WIND_SPEED,
        state_class=MEAS,
    ),
    AmbientSensorDescription(
        key="wind_gust",
        name="Wind gust",
        native_unit_of_measurement=UnitOfSpeed.MILES_PER_HOUR,
        device_class=SensorDeviceClass.WIND_SPEED,
        state_class=MEAS,
    ),
    AmbientSensorDescription(
        key="max_daily_gust",
        name="Max daily gust",
        native_unit_of_measurement=UnitOfSpeed.MILES_PER_HOUR,
        device_class=SensorDeviceClass.WIND_SPEED,
        state_class=MEAS,
    ),
    AmbientSensorDescription(
        key="wind_dir",
        name="Wind direction",
        native_unit_of_measurement=DEGREE,
        icon="mdi:compass",
        state_class=MEAS,
    ),
    AmbientSensorDescription(
        key="solar_rad",
        name="Solar radiation",
        native_unit_of_measurement=UnitOfIrradiance.WATTS_PER_SQUARE_METER,
        device_class=SensorDeviceClass.IRRADIANCE,
        state_class=MEAS,
    ),
    AmbientSensorDescription(
        key="uv_index",
        name="UV index",
        native_unit_of_measurement="Index",
        icon="mdi:weather-sunny-alert",
        state_class=MEAS,
    ),
    AmbientSensorDescription(
        key="rel_pressure",
        name="Relative pressure",
        native_unit_of_measurement=UnitOfPressure.INHG,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=MEAS,
    ),
    AmbientSensorDescription(
        key="abs_pressure",
        name="Absolute pressure",
        native_unit_of_measurement=UnitOfPressure.INHG,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=MEAS,
        diagnostic=True,
    ),
    AmbientSensorDescription(
        key="rain_rate",
        name="Rain rate",
        native_unit_of_measurement=UnitOfVolumetricFlux.INCHES_PER_HOUR,
        device_class=SensorDeviceClass.PRECIPITATION_INTENSITY,
        state_class=MEAS,
    ),
    AmbientSensorDescription(
        key="event_rain",
        name="Event rain",
        native_unit_of_measurement=UnitOfPrecipitationDepth.INCHES,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=TOTAL_INC,
    ),
    AmbientSensorDescription(
        key="daily_rain",
        name="Daily rain",
        native_unit_of_measurement=UnitOfPrecipitationDepth.INCHES,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=TOTAL_INC,
    ),
    AmbientSensorDescription(
        key="weekly_rain",
        name="Weekly rain",
        native_unit_of_measurement=UnitOfPrecipitationDepth.INCHES,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=TOTAL_INC,
    ),
    AmbientSensorDescription(
        key="monthly_rain",
        name="Monthly rain",
        native_unit_of_measurement=UnitOfPrecipitationDepth.INCHES,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=TOTAL_INC,
    ),
    AmbientSensorDescription(
        key="yearly_rain",
        name="Yearly rain",
        native_unit_of_measurement=UnitOfPrecipitationDepth.INCHES,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=TOTAL_INC,
    ),
    AmbientSensorDescription(
        key="inside_temp",
        name="Inside temperature",
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=MEAS,
        diagnostic=True,
    ),
    AmbientSensorDescription(
        key="inside_humidity",
        name="Inside humidity",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=MEAS,
        diagnostic=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    device_name = entry.options.get(
        CONF_DEVICE_NAME, entry.data.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME)
    )
    entities: list[SensorEntity] = [
        AmbientSensor(coordinator, device_name, entry.entry_id, desc)
        for desc in SENSORS
    ]
    entities.append(AmbientLastUpdate(coordinator, device_name, entry.entry_id))
    async_add_entities(entities)


class AmbientSensor(AmbientEntity, SensorEntity):
    """A single weather reading."""

    entity_description: AmbientSensorDescription

    def __init__(self, coordinator, device_name, entry_id, description):
        super().__init__(coordinator, device_name, entry_id)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"
        if description.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        return self.coordinator.sensors.get(self.entity_description.key)

    @property
    def available(self) -> bool:
        return (
            self.coordinator.data_is_fresh
            and self.entity_description.key in self.coordinator.sensors
        )


class AmbientLastUpdate(AmbientEntity, SensorEntity):
    """Diagnostic timestamp of the last data received from the console."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Last update"

    def __init__(self, coordinator, device_name, entry_id):
        super().__init__(coordinator, device_name, entry_id)
        self._attr_unique_id = f"{entry_id}_last_update"

    @property
    def native_value(self):
        return self.coordinator.last_push

    @property
    def available(self) -> bool:
        # Always available so you can see *when* it last reported, even if stale.
        return True
