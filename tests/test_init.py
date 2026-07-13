"""Integration test: set up the entry, simulate a push, check entities, unload."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ambient_local.const import (
    CONF_DEVICE_NAME,
    CONF_LISTEN_PORT,
    CONF_SCAN_MINUTES,
    DOMAIN,
)

MAC = "08:f9:e0:51:35:ae"


class _FakeListener:
    """Stand-in for PushListener that captures the push callback (no real socket)."""

    instances: list = []

    def __init__(self, port, on_data):
        self.port = port
        self.on_data = on_data
        self.started = False
        self.stopped = False
        _FakeListener.instances.append(self)

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


async def _setup(hass):
    hass.config.units = US_CUSTOMARY_SYSTEM  # console reports °F/inHg; match it
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE_NAME: "Home", CONF_LISTEN_PORT: 7080, CONF_SCAN_MINUTES: 5},
        unique_id=MAC,
    )
    entry.add_to_hass(hass)
    _FakeListener.instances.clear()
    with patch("custom_components.ambient_local.PushListener", _FakeListener):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_setup_registers_service_and_listener(hass):
    await _setup(hass)
    assert _FakeListener.instances[0].started is True
    assert _FakeListener.instances[0].port == 7080
    assert hass.services.has_service(DOMAIN, "reapply_console_settings")


async def test_push_populates_entities(hass, raw_payload):
    await _setup(hass)
    listener = _FakeListener.instances[0]

    # entities exist but are unavailable until data arrives
    assert hass.states.get("sensor.home_temperature").state == "unavailable"

    # simulate the console's push (data + source IP)
    listener.on_data(raw_payload, "192.168.0.50")
    await hass.async_block_till_done()

    # sensor states are rounded to display precision; weather keeps the native value
    assert float(hass.states.get("sensor.home_temperature").state) == pytest.approx(
        70.9, abs=0.5
    )
    assert float(hass.states.get("sensor.home_humidity").state) == pytest.approx(
        41.0, abs=0.5
    )
    assert float(hass.states.get("sensor.home_dew_point").state) > 40
    assert hass.states.get("sensor.home_last_update").state not in (
        "unknown",
        "unavailable",
    )
    # battout "1" == OK -> battery problem sensor off
    assert hass.states.get("binary_sensor.home_battery").state == "off"

    weather = hass.states.get("weather.home")
    assert weather is not None
    assert weather.attributes["temperature"] == pytest.approx(70.9, abs=0.5)


async def test_unload_stops_listener(hass):
    entry = await _setup(hass)
    listener = _FakeListener.instances[0]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert listener.stopped is True
    assert not hass.services.has_service(DOMAIN, "reapply_console_settings")
