"""Tests for the config flow: on-network detect, verified AP provisioning, manual."""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ambient_local.const import DOMAIN
from custom_components.ambient_local.provision import (
    JOIN_FAILED,
    OK,
    UNREACHABLE,
    ProvisionResult,
)

CF = "custom_components.ambient_local.config_flow"
MAC = "08:F9:E0:51:35:AE"

SETUP_INPUT = {
    "target_ssid": "IoT",
    "target_psk": "pw",
    "device_name": "Home",
    "listen_port": 7080,
    "amb_email": "a@b.com",
}


def _init(hass):
    return hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


def _sup():
    sup = MagicMock()
    sup.spare_wifi_interface = AsyncMock(return_value="wlo1")
    sup.scan = AsyncMock(return_value=[{"ssid": "AMBWeatherPro-5135AE"}])
    sup.info = AsyncMock(
        return_value={
            "interfaces": [{"primary": True, "ipv4": {"address": ["192.168.1.126/23"]}}]
        }
    )
    return sup


def _enter_reach_setup(stack, cache, sup, ap):
    """Enter the patches that drive the flow to the 'setup' form (AP found+joined)."""
    for p in (
        patch(f"{CF}._load_cache", AsyncMock(return_value=cache)),
        patch(f"{CF}.supervisor_available", return_value=True),
        patch(f"{CF}.SupervisorNetwork", return_value=sup),
        patch(f"{CF}.join_and_reach", AsyncMock(return_value=True)),
        patch(f"{CF}.ConsoleClient", return_value=ap),
    ):
        stack.enter_context(p)


# --- on-network / no-radio / ap-not-found paths (unchanged behaviour) ---------


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
        assert result["step_id"] == "manual"
        result2 = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result2["type"] is FlowResultType.CREATE_ENTRY


async def test_ap_not_found_shows_retry(hass):
    sup = _sup()
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


# --- verified provisioning paths ----------------------------------------------


async def _submit_setup_and_wait(hass, flow_id):
    """Submit the setup form and drive the verify progress step to completion.

    The verify step is a progress task; with the underlying call mocked it can
    resolve within a single ``async_configure``, so we only loop while it's
    genuinely still showing progress.
    """
    result = await hass.config_entries.flow.async_configure(flow_id, SETUP_INPUT)
    while result["type"] is FlowResultType.SHOW_PROGRESS:
        assert result["step_id"] == "verify"
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    return result


async def test_provisioning_verified_creates_entry(hass):
    """Console reaches HA -> entry is committed and its IP saved for self-heal."""
    cache = {"network": {"mac": MAC, "ssid": "IoT"}, "ws": {"ambEmail": "a@b.com"}}
    sup, ap = _sup(), MagicMock(scan_ssids=AsyncMock(return_value=[{"ssid": "IoT"}]))
    with contextlib.ExitStack() as stack:
        _enter_reach_setup(stack, cache, sup, ap)
        stack.enter_context(
            patch(
                f"{CF}.provision_and_verify",
                AsyncMock(return_value=ProvisionResult(OK, console_ip="192.168.1.50")),
            )
        )
        save_ip = stack.enter_context(patch(f"{CF}._save_console_ip", AsyncMock()))
        stack.enter_context(
            patch(
                "custom_components.ambient_local.async_setup_entry",
                AsyncMock(return_value=True),
            )
        )
        result = await _init(hass)
        assert result["step_id"] == "setup"
        result = await _submit_setup_and_wait(hass, result["flow_id"])

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "device_name": "Home",
        "listen_port": 7080,
        "scan_minutes": 5,
    }
    save_ip.assert_awaited_once_with(hass, "192.168.1.50")  # for immediate self-heal


async def test_provisioning_join_failed_shows_retry(hass):
    """Wrong password etc -> recoverable: explain and offer to try again."""
    cache = {"network": {"mac": MAC, "ssid": "IoT"}, "ws": {}}
    sup, ap = _sup(), MagicMock(scan_ssids=AsyncMock(return_value=[{"ssid": "IoT"}]))
    with contextlib.ExitStack() as stack:
        _enter_reach_setup(stack, cache, sup, ap)
        stack.enter_context(
            patch(
                f"{CF}.provision_and_verify",
                AsyncMock(return_value=ProvisionResult(JOIN_FAILED, detail="bad psk")),
            )
        )
        result = await _init(hass)
        result = await _submit_setup_and_wait(hass, result["flow_id"])

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "retry"
    # the reason names the network so the user knows what to fix
    assert "IoT" in result["description_placeholders"]["reason"]

    # pressing "Try again" re-finds the AP and returns to the setup form
    # (not straight back into verify with the stale input)
    with contextlib.ExitStack() as stack:
        _enter_reach_setup(stack, cache, sup, ap)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"retry": True}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "setup"


async def test_provisioning_unreachable_aborts_with_instructions(hass):
    """Console joined an isolated network -> abort cleanly, tell the user what to do."""
    cache = {"network": {"mac": MAC, "ssid": "IoT"}, "ws": {}}
    sup, ap = _sup(), MagicMock(scan_ssids=AsyncMock(return_value=[{"ssid": "IoT"}]))
    with contextlib.ExitStack() as stack:
        _enter_reach_setup(stack, cache, sup, ap)
        stack.enter_context(
            patch(
                f"{CF}.provision_and_verify",
                AsyncMock(return_value=ProvisionResult(UNREACHABLE)),
            )
        )
        result = await _init(hass)
        result = await _submit_setup_and_wait(hass, result["flow_id"])

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "console_unreachable"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 0  # nothing committed


# --- options-flow recovery ("re-provision an existing device") ----------------


class _NoopListener:
    def __init__(self, port, on_data):
        pass

    async def start(self):
        pass

    async def stop(self):
        pass


async def test_options_recovery_reprovisions_and_verifies(hass):
    """Configure an existing entry -> Recover -> verified back online."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"device_name": "Home", "listen_port": 7080, "scan_minutes": 5},
        unique_id=MAC.lower(),
    )
    entry.add_to_hass(hass)
    with patch("custom_components.ambient_local.PushListener", _NoopListener):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coord = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    coord.ha_ip = "192.168.1.126"  # skip the socket-based local-IP probe in tests
    coord.station_mac = MAC

    sup = _sup()
    with contextlib.ExitStack() as stack:
        for p in (
            patch(f"{CF}.supervisor_available", return_value=True),
            patch(f"{CF}.SupervisorNetwork", return_value=sup),
            patch(f"{CF}.join_and_reach", AsyncMock(return_value=True)),
            patch(
                f"{CF}.provision_and_verify",
                AsyncMock(return_value=ProvisionResult(OK, console_ip="192.168.1.50")),
            ),
        ):
            stack.enter_context(p)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "provision"}
        )
        assert result["step_id"] == "pick"  # found AP, offering the Wi-Fi picker
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"target_ssid": "IoT", "target_psk": "pw"}
        )
        while result["type"] is FlowResultType.SHOW_PROGRESS:
            await hass.async_block_till_done()
            result = await hass.config_entries.options.async_configure(
                result["flow_id"]
            )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "provision_done"  # console came back online

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
