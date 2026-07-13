"""Tests for the console HTTP client (no Home Assistant)."""

from __future__ import annotations

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.ambient_local.console import (
    ConsoleClient,
    ConsoleError,
    detect_local_ip,
)

IP = "192.168.0.50"


async def test_get_settings_ok():
    async with aiohttp.ClientSession() as s:
        with aioresponses() as m:
            m.get(f"http://{IP}/get_ws_settings", payload={"Customized": "enable"})
            c = ConsoleClient(s, IP)
            assert (await c.get_settings())["Customized"] == "enable"


async def test_get_settings_error_wrapped():
    async with aiohttp.ClientSession() as s:
        with aioresponses() as m:
            m.get(f"http://{IP}/get_ws_settings", status=500)
            with pytest.raises(ConsoleError, match="get_ws_settings"):
                await ConsoleClient(s, IP).get_settings()


async def test_get_settings_connection_error_wrapped():
    async with aiohttp.ClientSession() as s:
        with aioresponses() as m:
            m.get(f"http://{IP}/get_ws_settings", exception=aiohttp.ClientError())
            with pytest.raises(ConsoleError):
                await ConsoleClient(s, IP).get_settings(timeout_s=2)


async def test_set_settings_posts_payload():
    async with aiohttp.ClientSession() as s:
        with aioresponses() as m:
            m.post(f"http://{IP}/set_ws_settings", status=200)
            await ConsoleClient(s, IP).set_settings({"a": 1})
            m.assert_called_once()


async def test_network_and_device_and_scan():
    async with aiohttp.ClientSession() as s:
        with aioresponses() as m:
            m.get(f"http://{IP}/get_network_info", payload={"ssid": "IoT"})
            m.get(f"http://{IP}/get_device_info", payload={"apName": "AMB-1"})
            m.get(
                f"http://{IP}/usr_scan_ssid_list",
                payload={"list": [{"ssid": "IoT"}, {"ssid": "Guest"}]},
            )
            c = ConsoleClient(s, IP)
            assert (await c.get_network_info())["ssid"] == "IoT"
            assert (await c.get_device_info())["apName"] == "AMB-1"
            ssids = await c.scan_ssids()
            assert [x["ssid"] for x in ssids] == ["IoT", "Guest"]


async def test_scan_ssids_missing_list_key():
    async with aiohttp.ClientSession() as s:
        with aioresponses() as m:
            m.get(f"http://{IP}/usr_scan_ssid_list", payload={"status": "0"})
            assert await ConsoleClient(s, IP).scan_ssids() == []


async def test_ip_property_and_setter():
    async with aiohttp.ClientSession() as s:
        c = ConsoleClient(s, IP)
        assert c.ip == IP
        c.set_ip("10.0.0.9")
        assert c.ip == "10.0.0.9"


class _FakeSock:
    def __init__(self, *, name=None, err=None):
        self._name = name
        self._err = err

    def connect(self, addr):
        if self._err:
            raise self._err

    def getsockname(self):
        return (self._name, 12345)

    def close(self):
        pass


def test_detect_local_ip_returns_source_ip(monkeypatch):
    monkeypatch.setattr(
        "custom_components.ambient_local.console.socket.socket",
        lambda *a, **k: _FakeSock(name="192.168.1.50"),
    )
    assert detect_local_ip("1.2.3.4") == "192.168.1.50"


def test_detect_local_ip_returns_none_on_oserror(monkeypatch):
    monkeypatch.setattr(
        "custom_components.ambient_local.console.socket.socket",
        lambda *a, **k: _FakeSock(err=OSError("no route")),
    )
    assert detect_local_ip("1.2.3.4") is None
