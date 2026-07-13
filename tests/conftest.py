"""Shared test fixtures.

Every test imports `custom_components.ambient_local`, whose package __init__
imports Home Assistant — so the whole suite requires `homeassistant` +
`pytest-homeassistant-custom-component` (see requirements-test.txt).
"""

from __future__ import annotations

import pytest

pytest_plugins = ("pytest_homeassistant_custom_component",)


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(enable_custom_integrations):
    """Let HA load this custom integration in every test."""
    yield


# --- sample data shared across tests -----------------------------------------


@pytest.fixture
def raw_payload() -> dict:
    """A representative console push (Ecowitt/AMBWeather query-string params)."""
    return {
        "PASSKEY": "1C0C9789F2BBB01D61C92E8D89FDE343",
        "stationtype": "AMBWeatherPro_V5.2.7",
        "dateutc": "2026-07-13 03:39:35",
        "tempf": "70.9",
        "humidity": "41",
        "windspeedmph": "0.00",
        "windgustmph": "0.00",
        "maxdailygust": "8.05",
        "winddir": "241",
        "uv": "0",
        "solarradiation": "0.50",
        "hourlyrainin": "0.000",
        "eventrainin": "0.000",
        "dailyrainin": "0.000",
        "weeklyrainin": "0.000",
        "monthlyrainin": "0.012",
        "yearlyrainin": "0.012",
        "totalrainin": "0.012",
        "battout": "1",
        "tempinf": "64.4",
        "humidityin": "41",
        "baromrelin": "29.982",
        "baromabsin": "23.039",
    }


@pytest.fixture
def cached_config() -> dict:
    """A persisted console snapshot (as coordinator/store would hold it)."""
    return {
        "ip": "192.168.0.50",
        "network": {
            "mac": "08:F9:E0:51:35:AE",
            "ssid": "IoT",
            "wifi_DNS": "192.168.1.1",
            "wifi_ip": "192.168.0.50",
            "wifi_mask": "255.255.254.0",
            "wifi_gateway": "192.168.1.1",
        },
        "ws": {
            "ambEmail": "james@example.com",
            "ost_interval": "1",
            "ecowitt_upload": "60",
        },
        "device": {"apName": "AMBWeatherPro-5135AE", "ntp_server": "pool.ntp.org"},
    }
