"""Parse the raw console query-string payload into normalized values.

Also derives dew point and "feels like" from the base readings.
"""
from __future__ import annotations

import math

# raw console field -> normalized sensor key
FIELD_MAP: dict[str, str] = {
    "tempf": "temp",
    "humidity": "humidity",
    "windspeedmph": "wind_speed",
    "windgustmph": "wind_gust",
    "maxdailygust": "max_daily_gust",
    "winddir": "wind_dir",
    "uv": "uv_index",
    "solarradiation": "solar_rad",
    "hourlyrainin": "rain_rate",
    "eventrainin": "event_rain",
    "dailyrainin": "daily_rain",
    "weeklyrainin": "weekly_rain",
    "monthlyrainin": "monthly_rain",
    "yearlyrainin": "yearly_rain",
    "totalrainin": "total_rain",
    "tempinf": "inside_temp",
    "humidityin": "inside_humidity",
    "baromrelin": "rel_pressure",
    "baromabsin": "abs_pressure",
}


def _dew_point_f(temp_f: float, rh: float) -> float:
    """Magnus-formula dew point in Fahrenheit."""
    if rh <= 0:
        rh = 0.1
    t_c = (temp_f - 32.0) * 5.0 / 9.0
    a, b = 17.27, 237.7
    gamma = (a * t_c) / (b + t_c) + math.log(rh / 100.0)
    dp_c = (b * gamma) / (a - gamma)
    return dp_c * 9.0 / 5.0 + 32.0


def _feels_like_f(temp_f: float, rh: float, wind_mph: float) -> float:
    """NWS heat index (hot) / wind chill (cold) / actual (mild)."""
    if temp_f >= 80.0 and rh >= 40.0:
        t, r = temp_f, rh
        hi = (
            -42.379
            + 2.04901523 * t
            + 10.14333127 * r
            - 0.22475541 * t * r
            - 0.00683783 * t * t
            - 0.05481717 * r * r
            + 0.00122874 * t * t * r
            + 0.00085282 * t * r * r
            - 0.00000199 * t * t * r * r
        )
        return hi
    if temp_f <= 50.0 and wind_mph > 3.0:
        v16 = wind_mph**0.16
        return 35.74 + 0.6215 * temp_f - 35.75 * v16 + 0.4275 * temp_f * v16
    return temp_f


def parse_payload(raw: dict) -> dict:
    """Convert a raw console payload into normalized float values (+ derived)."""
    out: dict[str, float | str | bool] = {}

    for raw_key, value in raw.items():
        key = FIELD_MAP.get(raw_key)
        if key is None:
            continue
        try:
            out[key] = float(value)
        except (TypeError, ValueError):
            out[key] = value

    # battery: "1" == OK on Ambient/Ecowitt -> battery_low is True only if 0/blank
    if "battout" in raw:
        out["battery_low"] = str(raw["battout"]).strip() in ("0", "")

    temp = out.get("temp")
    humidity = out.get("humidity")
    if isinstance(temp, (int, float)) and isinstance(humidity, (int, float)):
        wind = out.get("wind_speed")
        wind = wind if isinstance(wind, (int, float)) else 0.0
        out["dew_point"] = round(_dew_point_f(temp, humidity), 1)
        out["feels_like"] = round(_feels_like_f(temp, humidity, wind), 1)

    return out
