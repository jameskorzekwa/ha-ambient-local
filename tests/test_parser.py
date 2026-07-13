"""Unit tests for the payload parser (pure, no Home Assistant)."""

from __future__ import annotations

import pytest

from custom_components.ambient_local.parser import (
    FIELD_MAP,
    _dew_point_f,
    _feels_like_f,
    parse_payload,
)


def test_maps_and_floats_known_fields(raw_payload):
    out = parse_payload(raw_payload)
    assert out["temp"] == 70.9
    assert out["humidity"] == 41.0
    assert out["rel_pressure"] == 29.982
    assert out["inside_temp"] == 64.4
    assert out["wind_dir"] == 241.0
    # every mapped field is a float
    for src, dst in FIELD_MAP.items():
        if src in raw_payload:
            assert isinstance(out[dst], float)


def test_ignores_unmapped_fields(raw_payload):
    out = parse_payload(raw_payload)
    for junk in ("PASSKEY", "stationtype", "dateutc"):
        assert junk not in out
        assert FIELD_MAP.get(junk) not in out  # sanity: not aliased in either


def test_battery_decode():
    assert parse_payload({"battout": "1"})["battery_low"] is False
    assert parse_payload({"battout": "0"})["battery_low"] is True
    assert parse_payload({"battout": ""})["battery_low"] is True
    assert parse_payload({"battout": " 0 "})["battery_low"] is True  # stripped
    assert "battery_low" not in parse_payload({"tempf": "70"})  # absent -> no key


def test_non_numeric_mapped_value_kept_as_string():
    out = parse_payload({"winddir": "N/A"})
    assert out["wind_dir"] == "N/A"


def test_derived_only_with_temp_and_humidity():
    assert "dew_point" not in parse_payload({"tempf": "70"})  # humidity missing
    assert "dew_point" not in parse_payload({"humidity": "40"})  # temp missing
    out = parse_payload({"tempf": "70", "humidity": "40"})
    assert "dew_point" in out and "feels_like" in out


def test_dew_point_value(raw_payload):
    out = parse_payload(raw_payload)
    # 70.9F / 41% RH -> ~46F
    assert out["dew_point"] == pytest.approx(46.0, abs=0.5)


def test_feels_like_mild_equals_temp(raw_payload):
    # mild + calm -> feels-like is just the temperature
    assert parse_payload(raw_payload)["feels_like"] == pytest.approx(70.9, abs=0.1)


def test_feels_like_heat_index_when_hot():
    fl = _feels_like_f(95.0, 60.0, 0.0)
    assert fl > 95.0  # heat index adds apparent heat


def test_feels_like_wind_chill_when_cold():
    fl = _feels_like_f(30.0, 50.0, 20.0)
    assert fl < 30.0  # wind chill subtracts


def test_feels_like_cold_but_calm_is_actual():
    # wind <= 3 mph -> no wind chill applied
    assert _feels_like_f(30.0, 50.0, 1.0) == 30.0


def test_dew_point_clamps_zero_humidity():
    # rh<=0 is clamped to 0.1 instead of raising on log(0)
    val = _dew_point_f(70.0, 0.0)
    assert isinstance(val, float)


def test_wind_defaults_to_zero_for_feels_like():
    # feels_like should not blow up when wind_speed is absent
    out = parse_payload({"tempf": "40", "humidity": "50"})
    assert out["feels_like"] == pytest.approx(40.0, abs=0.1)
