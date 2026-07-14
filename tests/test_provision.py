"""Unit + integration tests for AP-mode provisioning (no Home Assistant)."""

from __future__ import annotations

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.ambient_local.const import AP_HOST
from custom_components.ambient_local.provision import (
    JOIN_FAILED,
    OK,
    PROVISION_FAILED,
    UNREACHABLE,
    b64,
    build_network_payload,
    build_ws_payload,
    classify_after_timeout,
    find_setup_ap,
    join_and_reach,
    manual_instructions,
    provision_and_verify,
    push_settings_over_ap,
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


# --- verified provisioning (integration, with fakes) -------------------------


class _FakeSup:
    """Records radio join/disable calls; join can be made to fail."""

    def __init__(self, join_raises: bool = False):
        self.join_raises = join_raises
        self.joined: list = []
        self.disabled = 0

    async def join(self, interface, ssid, psk=None):
        if self.join_raises:
            raise RuntimeError("radio busy")
        self.joined.append((interface, ssid, psk))

    async def disable(self, interface):
        self.disabled += 1


class _FakeWatcher:
    """Push watcher stub: ``wait`` yields a preset console IP (or None=timeout)."""

    def __init__(self, ip: str | None):
        self._ip = ip
        self.entered = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *exc):
        return None

    async def wait(self, timeout):  # noqa: ASYNC109
        return self._ip


@pytest.fixture
def _fast(monkeypatch):
    """Collapse retry/poll sleeps so timeout paths run instantly."""
    import custom_components.ambient_local.provision as prov

    monkeypatch.setattr(prov, "REJOIN_ATTEMPTS", 2)
    monkeypatch.setattr(prov, "REJOIN_INTERVAL_S", 0)
    monkeypatch.setattr(prov, "AP_REACH_ATTEMPTS", 2)
    monkeypatch.setattr(prov, "AP_REACH_INTERVAL_S", 0)


async def test_join_and_reach_ok(_fast):
    sup = _FakeSup()
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.get(f"http://{AP_HOST}/get_device_info", payload={"apName": "x"})
            assert await join_and_reach(session, sup, "wlan1", "AMBWeatherPro-5135AE")
    assert sup.joined == [("wlan1", "AMBWeatherPro-5135AE", None)]  # open AP


async def test_join_and_reach_join_error(_fast):
    sup = _FakeSup(join_raises=True)
    async with aiohttp.ClientSession() as session:
        assert await join_and_reach(session, sup, "wlan1", "AP") is False


async def test_join_and_reach_unreachable(_fast):
    sup = _FakeSup()
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.get(
                f"http://{AP_HOST}/get_device_info",
                exception=aiohttp.ClientError(),
                repeat=True,
            )
            assert await join_and_reach(session, sup, "wlan1", "AP") is False


async def test_push_settings_over_ap_ok(cached_config):
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(f"http://{AP_HOST}/set_network_info", status=200)
            m.post(f"http://{AP_HOST}/set_ws_settings", status=200)
            await push_settings_over_ap(
                session, cached_config, "IoT", "pw", "1.2.3.4", 7080
            )


async def test_push_settings_over_ap_network_fails(cached_config):
    from custom_components.ambient_local.console import ConsoleError

    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(f"http://{AP_HOST}/set_network_info", status=500)
            with pytest.raises(ConsoleError):  # Wi-Fi push is the essential part
                await push_settings_over_ap(
                    session, cached_config, "IoT", "pw", "1.2.3.4", 7080
                )


async def test_push_settings_over_ap_ws_failure_tolerated(cached_config):
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(f"http://{AP_HOST}/set_network_info", status=200)
            m.post(f"http://{AP_HOST}/set_ws_settings", status=500)  # self-heals
            await push_settings_over_ap(
                session, cached_config, "IoT", "pw", "1.2.3.4", 7080
            )


async def test_classify_join_failed(_fast):
    """AP is back -> the console fell back to setup mode (recoverable)."""
    sup = _FakeSup()
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.get(f"http://{AP_HOST}/get_device_info", payload={"apName": "x"})
            assert (
                await classify_after_timeout(session, sup, "wlan1", "AP") == JOIN_FAILED
            )
    assert sup.disabled == 1  # radio released


async def test_classify_unreachable(_fast):
    """AP never comes back -> console is on a network we can't reach."""
    sup = _FakeSup()
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.get(
                f"http://{AP_HOST}/get_device_info",
                exception=aiohttp.ClientError(),
                repeat=True,
            )
            assert (
                await classify_after_timeout(session, sup, "wlan1", "AP") == UNREACHABLE
            )
    assert sup.disabled == 1


async def test_provision_and_verify_ok(cached_config, _fast):
    sup = _FakeSup()
    watcher = _FakeWatcher("192.168.1.50")  # console reached us
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(f"http://{AP_HOST}/set_network_info", status=200)
            m.post(f"http://{AP_HOST}/set_ws_settings", status=200)
            result = await provision_and_verify(
                session,
                sup,
                "wlan1",
                "AP",
                "IoT",
                "pw",
                cached_config,
                "1.2.3.4",
                7080,
                watcher,
            )
    assert result.status == OK
    assert result.console_ip == "192.168.1.50"
    assert watcher.entered is True
    assert sup.disabled >= 1  # radio released before we waited


async def test_provision_and_verify_push_fails(cached_config, _fast):
    sup = _FakeSup()
    watcher = _FakeWatcher(None)
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(f"http://{AP_HOST}/set_network_info", status=500)  # console rejects
            result = await provision_and_verify(
                session,
                sup,
                "wlan1",
                "AP",
                "IoT",
                "pw",
                cached_config,
                "1.2.3.4",
                7080,
                watcher,
            )
    assert result.status == PROVISION_FAILED
    assert sup.disabled >= 1


async def test_provision_and_verify_timeout_join_failed(cached_config, _fast):
    """No push -> classify re-joins the AP and finds it back (JOIN_FAILED)."""
    sup = _FakeSup()
    watcher = _FakeWatcher(None)  # nothing reached us
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(f"http://{AP_HOST}/set_network_info", status=200)
            m.post(f"http://{AP_HOST}/set_ws_settings", status=200)
            m.get(f"http://{AP_HOST}/get_device_info", payload={"apName": "x"})
            result = await provision_and_verify(
                session,
                sup,
                "wlan1",
                "AP",
                "IoT",
                "pw",
                cached_config,
                "1.2.3.4",
                7080,
                watcher,
            )
    assert result.status == JOIN_FAILED
