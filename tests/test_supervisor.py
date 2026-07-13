"""Tests for the Supervisor network client (no Home Assistant)."""

from __future__ import annotations

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.ambient_local.supervisor import (
    SupervisorNetwork,
    supervisor_available,
)

BASE = "http://supervisor"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")


def test_supervisor_available(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    assert supervisor_available() is True
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert supervisor_available() is False


async def test_spare_wifi_interface_found():
    data = {
        "data": {
            "interfaces": [
                {"interface": "enp1s0", "type": "ethernet", "primary": True},
                {"interface": "wlo1", "type": "wireless", "primary": False},
            ]
        }
    }
    async with aiohttp.ClientSession() as s:
        with aioresponses() as m:
            m.get(f"{BASE}/network/info", payload=data)
            assert await SupervisorNetwork(s).spare_wifi_interface() == "wlo1"


async def test_spare_wifi_interface_none_when_only_ethernet():
    data = {
        "data": {
            "interfaces": [
                {"interface": "enp1s0", "type": "ethernet", "primary": True},
            ]
        }
    }
    async with aiohttp.ClientSession() as s:
        with aioresponses() as m:
            m.get(f"{BASE}/network/info", payload=data)
            assert await SupervisorNetwork(s).spare_wifi_interface() is None


async def test_spare_wifi_interface_skips_primary_wireless():
    data = {
        "data": {
            "interfaces": [
                {"interface": "wlo1", "type": "wireless", "primary": True},  # in use
            ]
        }
    }
    async with aiohttp.ClientSession() as s:
        with aioresponses() as m:
            m.get(f"{BASE}/network/info", payload=data)
            assert await SupervisorNetwork(s).spare_wifi_interface() is None


async def test_spare_wifi_interface_none_on_error():
    async with aiohttp.ClientSession() as s:
        with aioresponses() as m:
            m.get(f"{BASE}/network/info", exception=aiohttp.ClientError())
            assert await SupervisorNetwork(s).spare_wifi_interface() is None


async def test_scan_returns_accesspoints():
    async with aiohttp.ClientSession() as s:
        with aioresponses() as m:
            m.get(
                f"{BASE}/network/interface/wlo1/accesspoints",
                payload={"data": {"accesspoints": [{"ssid": "IoT"}]}},
            )
            aps = await SupervisorNetwork(s).scan("wlo1")
            assert aps == [{"ssid": "IoT"}]


async def test_join_open_ap_payload():
    captured = {}

    async def _cb(url, **kwargs):
        captured.update(kwargs.get("json") or {})

    async with aiohttp.ClientSession() as s:
        with aioresponses() as m:
            m.post(
                f"{BASE}/network/interface/wlo1/update",
                payload={"result": "ok"},
                callback=_cb,
            )
            await SupervisorNetwork(s).join("wlo1", "AMBWeatherPro-1", psk=None)
    assert captured["enabled"] is True
    assert captured["wifi"] == {
        "mode": "infrastructure",
        "ssid": "AMBWeatherPro-1",
        "auth": "open",
    }


async def test_join_wpa_ap_payload():
    captured = {}

    async def _cb(url, **kwargs):
        captured.update(kwargs.get("json") or {})

    async with aiohttp.ClientSession() as s:
        with aioresponses() as m:
            m.post(
                f"{BASE}/network/interface/wlo1/update",
                payload={"result": "ok"},
                callback=_cb,
            )
            await SupervisorNetwork(s).join("wlo1", "IoT", psk="secret")
    assert captured["wifi"]["auth"] == "wpa-psk"
    assert captured["wifi"]["psk"] == "secret"


async def test_disable_payload():
    captured = {}

    async def _cb(url, **kwargs):
        captured.update(kwargs.get("json") or {})

    async with aiohttp.ClientSession() as s:
        with aioresponses() as m:
            m.post(
                f"{BASE}/network/interface/wlo1/update",
                payload={"result": "ok"},
                callback=_cb,
            )
            await SupervisorNetwork(s).disable("wlo1")
    assert captured == {"enabled": False}
