"""Weer via open-meteo — gratis, geen API-key, dus proxy-arm en altijd aan.

geocode() vertaalt plaatsnamen ("Utrecht") naar coördinaten;
forecast() levert actueel weer + dagen vooruit met NL-omschrijvingen.
"""

from __future__ import annotations

from typing import Any

import requests

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weathercodes → Nederlands
WMO = {
    0: "onbewolkt", 1: "vrijwel onbewolkt", 2: "halfbewolkt", 3: "bewolkt",
    45: "mist", 48: "aanvriezende mist",
    51: "lichte motregen", 53: "motregen", 55: "dichte motregen",
    61: "lichte regen", 63: "regen", 65: "zware regen",
    66: "ijzel", 67: "zware ijzel",
    71: "lichte sneeuw", 73: "sneeuw", 75: "zware sneeuw", 77: "korrelsneeuw",
    80: "lichte buien", 81: "buien", 82: "zware buien",
    85: "sneeuwbuien", 86: "zware sneeuwbuien",
    95: "onweer", 96: "onweer met hagel", 99: "zwaar onweer met hagel",
}

DEFAULT_LAT, DEFAULT_LON, DEFAULT_PLACE = 52.156, 5.387, "Amersfoort"


def geocode(place: str) -> dict[str, Any] | None:
    resp = requests.get(
        GEOCODE_URL,
        params={"name": place, "count": 1, "language": "nl", "format": "json"},
        timeout=15,
    )
    resp.raise_for_status()
    hits = resp.json().get("results") or []
    if not hits:
        return None
    h = hits[0]
    return {"lat": h["latitude"], "lon": h["longitude"],
            "name": h.get("name"), "country": h.get("country_code")}


def forecast(lat: float, lon: float, days: int = 3, place: str = "") -> dict[str, Any]:
    days = max(1, min(int(days), 7))
    resp = requests.get(
        FORECAST_URL,
        params={
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,apparent_temperature,weather_code,"
                       "wind_speed_10m,relative_humidity_2m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                     "precipitation_probability_max,precipitation_sum,"
                     "wind_speed_10m_max,sunrise,sunset",
            "timezone": "Europe/Amsterdam",
            "forecast_days": days,
        },
        timeout=15,
    )
    resp.raise_for_status()
    d = resp.json()
    cur = d.get("current", {})
    daily = d.get("daily", {})
    out_days = []
    for i in range(len(daily.get("time", []))):
        out_days.append({
            "datum": daily["time"][i],
            "weer": WMO.get(daily["weather_code"][i], "onbekend"),
            "min": daily["temperature_2m_min"][i],
            "max": daily["temperature_2m_max"][i],
            "neerslagkans_pct": daily["precipitation_probability_max"][i],
            "neerslag_mm": daily["precipitation_sum"][i],
            "wind_max_kmh": daily["wind_speed_10m_max"][i],
            "zonsopkomst": daily["sunrise"][i][11:16],
            "zonsondergang": daily["sunset"][i][11:16],
        })
    return {
        "locatie": place or f"{lat:.3f},{lon:.3f}",
        "nu": {
            "temperatuur": cur.get("temperature_2m"),
            "gevoelstemperatuur": cur.get("apparent_temperature"),
            "weer": WMO.get(cur.get("weather_code"), "onbekend"),
            "wind_kmh": cur.get("wind_speed_10m"),
            "luchtvochtigheid_pct": cur.get("relative_humidity_2m"),
        },
        "dagen": out_days,
    }
