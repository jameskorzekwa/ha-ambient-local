"""Unit + integration tests for AP-mode provisioning (no Home Assistant)."""

from __future__ import annotations

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.ambient_local.const import AP_HOST
from custom_components.ambient_local.provision import (
    ProvisionError,
    b64,
    build_network_payload,
    build_ws_payload,
    find_setup_ap,
    manual_instructions,
    provision_via_ap,
)

MAC = "08:F9:E0:51:35:AE"


def test_b64():
    assert b64("korzekwa") == "a29yemVrd2E="


def test_find_setup_ap_by_mac():
    aps = [{"ssid": "IoT"}, {"ssid": "AMBWeatherPro-5135AE"}, {"ssid": "Guest"}]
    assert find_setup_ap(aps, MAC) == "AMBWeatherPro-5135AE"


def test_find_setup_ap_by_prefix_when_mac_unknown():
    aps = [{"ssid": "IoT"}, {"ssid": "AMBWeatherPro-ABCDEF"}]
    assert find_setup_ap(aps, None) == "AMBWeatherPro-ABCDEF"


def test_find_setup_ap_none():
    assert find_setup_ap([{"ssid": "IoT"}, {"ssid": ""}], MAC) is None
    assert find_setup_ap([], MAC) is None


def test_build_network_payload(cached_config):
    p = build_network_payload(cached_config["network"], "IoT", "secret")
    assert p["ssid"] == "IoT"
    assert p["wifi_pwd"] == b64("secret")
    assert p["staIpType"] == "0"
    assert p["wifi_gateway"] == "192.168.1.1"


def test_build_network_payload_defaults_missing_fields():
    p = build_network_payload({}, "Net", "pw")
    assert p["wifi_DNS"] == "" and p["wifi_ip"] == ""


def test_build_ws_payload(cached_config):
    p = build_ws_payload(cached_config["ws"], "192.168.1.126", 7080)
    assert p["ambEmail"] == "james@example.com"
    assert p["ecowitt_ip"] == "192.168.1.126"
    assert p["ecowitt_port"] == "7080"  # stringified
    assert p["ecowitt_path"] == "/?"
    assert p["Protocol"] == "ecowitt"  # device stores it as amb_protocol
    assert p["Customized"] == "enable"


def test_manual_instructions_contains_everything(cached_config):
    txt = manual_instructions(cached_config, "192.168.1.126", 7080, MAC)
    for needle in (
        "AMBWeatherPro-5135AE",
        "192.168.4.1",
        "192.168.1.126",
        "IoT",
        "james@example.com",
        "/?",
    ):
        assert needle in txt


def test_manual_instructions_without_mac():
    txt = manual_instructions({}, "10.0.0.5", 7080, None)
    assert "AMBWeatherPro-XXXXXX" in txt


# --- provision_via_ap (integration, with fakes) ------------------------------


class _FakeSup:
    def __init__(self, aps):
        self._aps = aps
        self.joined = None
        self.disabled = False

    async def scan(self, interface):
        return self._aps

    async def join(self, interface, ssid, psk=None):
        self.joined = (interface, ssid, psk)

    async def disable(self, interface):
        self.disabled = True


async def test_provision_via_ap_happy_path(cached_config):
    sup = _FakeSup([{"ssid": "AMBWeatherPro-5135AE"}])
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.get(f"http://{AP_HOST}/get_device_info", payload={"apName": "x"})
            m.post(f"http://{AP_HOST}/set_network_info", status=200)
            m.post(f"http://{AP_HOST}/set_ws_settings", status=200)
            await provision_via_ap(
                session, sup, "wlan1", "IoT", "pw", cached_config, "192.168.1.126", 7080
            )
    assert sup.joined == ("wlan1", "AMBWeatherPro-5135AE", None)  # open AP
    assert sup.disabled is True  # radio released in finally


async def test_provision_via_ap_no_ap_found(cached_config):
    sup = _FakeSup([{"ssid": "IoT"}])  # no setup AP
    async with aiohttp.ClientSession() as session:
        with pytest.raises(ProvisionError, match="setup network"):
            await provision_via_ap(
                session, sup, "wlan1", "IoT", "pw", cached_config, "192.168.1.126", 7080
            )
    assert sup.joined is None  # never tried to join
