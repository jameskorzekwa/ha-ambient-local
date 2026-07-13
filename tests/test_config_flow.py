"""Tests for the config flow: on-network detect, AP provisioning, manual fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ambient_local.const import DOMAIN

CF = "custom_components.ambient_local.config_flow"
MAC = "08:F9:E0:51:35:AE"


def _init(hass):
    return hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_detects_console_already_on_network(hass):
    """Cached IP is reachable -> add directly, no questions, no IP entered."""
    cc = MagicMock()
    cc.get_settings = AsyncMock(return_value={"sta_mac": MAC})
    with (
        patch(
            f"{CF}._load_cache",
            AsyncMock(return_value={"ip": "192.168.0.50", "network": {"mac": MAC}}),
        ),
        patch(f"{CF}._save_console_ip", AsyncMock()) as save_ip,
        patch(f"{CF}.ConsoleClient", return_value=cc),
        patch(
            "custom_components.ambient_local.async_setup_entry",
            AsyncMock(return_value=True),
        ),
    ):
        result = await _init(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "device_name": "Home",
        "listen_port": 7080,
        "scan_minutes": 5,
    }
    assert "console_ip" not in result["data"]  # never asked for / stored
    save_ip.assert_awaited_once()


async def test_no_spare_radio_shows_manual_then_creates(hass):
    with (
        patch(f"{CF}._load_cache", AsyncMock(return_value={})),
        patch(f"{CF}.supervisor_available", return_value=False),
        patch(
            "custom_components.ambient_local.async_setup_entry",
            AsyncMock(return_value=True),
        ),
    ):
        result = await _init(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "manual"
        result2 = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert result2["data"]["listen_port"] == 7080


async def test_ap_provisioning_flow(hass):
    """Spare radio + AP found -> setup form -> provision over AP -> create entry."""
    cache = {"network": {"mac": MAC, "ssid": "IoT"}, "ws": {"ambEmail": "a@b.com"}}
    sup = MagicMock()
    sup.spare_wifi_interface = AsyncMock(return_value="wlo1")
    sup.scan = AsyncMock(return_value=[{"ssid": "AMBWeatherPro-5135AE"}])
    sup.join = AsyncMock()
    sup.disable = AsyncMock()
    sup.info = AsyncMock(
        return_value={
            "interfaces": [{"primary": True, "ipv4": {"address": ["192.168.1.126/23"]}}]
        }
    )
    ap = MagicMock()
    ap.get_device_info = AsyncMock(return_value={"apName": "x"})
    ap.scan_ssids = AsyncMock(return_value=[{"ssid": "IoT"}, {"ssid": "Guest"}])
    ap.set_network_info = AsyncMock()
    ap.set_settings = AsyncMock()

    with (
        patch(f"{CF}._load_cache", AsyncMock(return_value=cache)),
        patch(f"{CF}.supervisor_available", return_value=True),
        patch(f"{CF}.SupervisorNetwork", return_value=sup),
        patch(f"{CF}.ConsoleClient", return_value=ap),
        patch(
            "custom_components.ambient_local.async_setup_entry",
            AsyncMock(return_value=True),
        ),
    ):
        result = await _init(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "setup"
        assert result["description_placeholders"]["ap"] == "AMBWeatherPro-5135AE"

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "target_ssid": "IoT",
                "target_psk": "pw",
                "device_name": "Home",
                "listen_port": 7080,
                "amb_email": "a@b.com",
            },
        )

    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert result2["data"] == {
        "device_name": "Home",
        "listen_port": 7080,
        "scan_minutes": 5,
    }
    sup.join.assert_awaited_once()  # joined the AP
    ap.set_network_info.assert_awaited_once()  # pushed Wi-Fi creds
    ap.set_settings.assert_awaited_once()  # pushed custom server + email
    sup.disable.assert_awaited()  # released the radio


async def test_ap_not_found_shows_retry(hass):
    """Radio present but no setup AP broadcasting -> retry form."""
    sup = MagicMock()
    sup.spare_wifi_interface = AsyncMock(return_value="wlo1")
    sup.scan = AsyncMock(return_value=[{"ssid": "IoT"}])  # no AMBWeatherPro AP
    with (
        patch(f"{CF}._load_cache", AsyncMock(return_value={"network": {"mac": MAC}})),
        patch(f"{CF}.supervisor_available", return_value=True),
        patch(f"{CF}.SupervisorNetwork", return_value=sup),
    ):
        result = await _init(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "provision"
    assert result["errors"] == {"base": "ap_not_found"}
