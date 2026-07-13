"""Tests for the coordinator: push handling, IP discovery, self-heal, cache."""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.util import dt as dt_util

from custom_components.ambient_local.console import ConsoleError
from custom_components.ambient_local.coordinator import AmbientCoordinator

CONSOLE_MOD = "custom_components.ambient_local.coordinator"


def make_client(ip=None):
    c = MagicMock()
    c.ip = ip
    c.set_ip = MagicMock(side_effect=lambda v: setattr(c, "ip", v))
    c.get_settings = AsyncMock()
    c.get_network_info = AsyncMock(return_value={})
    c.get_device_info = AsyncMock(return_value={})
    c.set_settings = AsyncMock()
    return c


async def test_handle_push_parses_and_discovers_ip(hass, raw_payload):
    coord = AmbientCoordinator(hass, make_client(), 7080, 5)
    coord.handle_push(raw_payload, "192.168.0.50")
    assert coord.sensors["temp"] == 70.9
    assert coord.sensors["dew_point"] == pytest.approx(46.0, abs=0.5)
    assert coord.console_ip == "192.168.0.50"  # learned from the push
    assert coord.client.ip == "192.168.0.50"
    assert coord.last_push is not None
    assert coord.data_is_fresh is True


async def test_handle_push_without_source_ip_keeps_ip(hass, raw_payload):
    coord = AmbientCoordinator(hass, make_client(ip="10.0.0.1"), 7080, 5)
    coord.handle_push(raw_payload)
    assert coord.console_ip == "10.0.0.1"


async def test_ensure_console_noop_without_ip(hass):
    client = make_client()  # ip unknown
    coord = AmbientCoordinator(hass, client, 7080, 5)
    await coord._ensure_console()
    client.get_settings.assert_not_called()
    assert coord.settings_ok is None


async def test_ensure_console_no_drift(hass, monkeypatch):
    client = make_client(ip="192.168.0.50")
    client.get_settings.return_value = {
        "sta_mac": "08:F9:E0:51:35:AE",
        "Customized": "enable",
        "ecowitt_ip": "192.168.1.126",
        "ecowitt_path": "/?",
        "ecowitt_port": "7080",
    }
    monkeypatch.setattr(f"{CONSOLE_MOD}.detect_local_ip", lambda _t: "192.168.1.126")
    coord = AmbientCoordinator(hass, client, 7080, 5)
    await coord._ensure_console()
    assert coord.settings_ok is True
    client.set_settings.assert_not_called()  # nothing to fix


async def test_ensure_console_reapplies_on_port_drift(hass, monkeypatch):
    client = make_client(ip="192.168.0.50")
    client.get_settings.return_value = {
        "sta_mac": "MAC",
        "Customized": "enable",
        "ecowitt_ip": "192.168.1.126",
        "ecowitt_path": "/?",
        "ecowitt_port": "9999",
    }
    monkeypatch.setattr(f"{CONSOLE_MOD}.detect_local_ip", lambda _t: "192.168.1.126")
    coord = AmbientCoordinator(hass, client, 7080, 5)
    await coord._ensure_console()
    client.set_settings.assert_called_once()
    assert coord.settings_ok is True


async def test_ensure_console_unreachable_sets_unknown(hass):
    client = make_client(ip="192.168.0.50")
    client.get_settings.side_effect = ConsoleError("boom")
    coord = AmbientCoordinator(hass, client, 7080, 5)
    await coord._ensure_console()
    assert coord.settings_ok is None


async def test_snapshot_strips_wifi_pwd_and_persists_ip(hass):
    client = make_client(ip="192.168.0.50")
    client.get_network_info.return_value = {
        "mac": "MAC",
        "ssid": "IoT",
        "wifi_pwd": "c2VjcmV0",
    }
    client.get_device_info.return_value = {"ntp_server": "pool.ntp.org"}
    coord = AmbientCoordinator(hass, client, 7080, 5)
    await coord._snapshot_config({"ambEmail": "a@b.com"})
    assert "wifi_pwd" not in coord.cached["network"]  # secret never persisted
    assert coord.cached["network"]["ssid"] == "IoT"
    assert coord.cached["ws"]["ambEmail"] == "a@b.com"
    assert coord.cached["ip"] == "192.168.0.50"
    assert coord.station_mac == "MAC"


async def test_load_cache_restores_ip(hass):
    client = make_client()  # ip unknown
    coord = AmbientCoordinator(hass, client, 7080, 5)
    await coord._store.async_save({"ip": "192.168.0.77", "network": {}})
    await coord.async_load_cache()
    assert coord.console_ip == "192.168.0.77"
    assert client.ip == "192.168.0.77"


async def test_data_is_fresh_false_when_stale(hass, raw_payload):
    coord = AmbientCoordinator(hass, make_client(), 7080, 5)
    assert coord.data_is_fresh is False  # never pushed
    coord.handle_push(raw_payload, "192.168.0.50")
    coord.last_push = dt_util.utcnow() - datetime.timedelta(seconds=400)
    assert coord.data_is_fresh is False  # beyond the grace window
