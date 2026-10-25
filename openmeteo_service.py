"""Open-Meteo client: free weather + geocoding, no API key.

Drop-in alternative to ``weather_service.WeatherClient``: it has the same ``get_current`` / ``get_forecast`` methods and
returns the same normalised dictionaries, so ``plan_service``, ``chat_assistant``, ``stylist`` and the UI work unchanged.

Usage terms (checked 2026-10): the free API is for NON-COMMERCIAL use, fewer than 10,000 calls/day, 5,000/hour and
600/minute, and the data is CC BY 4.0 - show "Weather data by Open-Meteo.com" with a link wherever it is displayed
(the app does this). For commercial use you need an Open-Meteo subscription. https://open-meteo.com/en/terms

* No Streamlit dependency (unit-testable).
* Every request has a timeout and one retry (with a short back-off) on timeouts / 5xx. HTTP 429 is NOT retried.
* Results are cached (TTL) and city lookups are cached for a week. If the service is unreachable, a recent cached copy is
  returned flagged ``stale=True``. Nothing is ever invented: a missing value stays missing and is reported.
* Error messages never contain request URLs.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import requests

from weather_service import (WeatherClient, WeatherConnectionError, WeatherDataError, WeatherNotFound, WeatherRateLimit,
                             WeatherServiceError, WeatherTimeout)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
ATTRIBUTION = "Weather data by Open-Meteo.com"
ATTRIBUTION_URL = "https://open-meteo.com/"

CURRENT_VARS = "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,wind_speed_10m"
HOURLY_VARS = ("temperature_2m,apparent_temperature,relative_humidity_2m,precipitation_probability,precipitation,"
               "weather_code,wind_speed_10m,uv_index,is_day")
DAILY_VARS = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum"

# WMO weather interpretation codes -> short English text (wording keeps the words "rain/drizzle/shower/thunder"
# that the outfit rules look for).
WMO_TEXT = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle", 56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain", 66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers", 85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}
_COORDS = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


def weather_text(code: Any) -> str:
    try:
        return WMO_TEXT.get(int(code), "Unknown")
    except (TypeError, ValueError):
        return "Unknown"


@dataclass
class Place:
    name: str
    admin1: str
    country: str
    lat: float
    lon: float
    timezone: str
    others: list[str] = field(default_factory=list)   # other places with the same name (for "did you mean...")

    @property
    def label(self) -> str:
        return ", ".join(p for p in (self.name, self.admin1, self.country) if p)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
        return v if v == v else default   # NaN -> default
    except (TypeError, ValueError):
        return default


def _at(arr: Any, i: int) -> Any:
    try:
        return arr[i]
    except (TypeError, IndexError, KeyError):
        return None


def normalize_open_meteo(payload: dict[str, Any], place: Place, with_hours: bool = True) -> dict[str, Any]:
    """Convert an Open-Meteo forecast payload to the dict shape used everywhere in this app."""
    try:
        c = payload["current"]
        tz_id = str(payload.get("timezone", "") or place.timezone)
        cur_time = str(c["time"]).replace("T", " ")
        current = {
            "city": f"{place.name}, {place.country}" if place.country else place.name,
            "region": place.admin1, "lat": place.lat, "lon": place.lon, "tz_id": tz_id,
            "localtime": cur_time, "temp_c": float(c["temperature_2m"]),
            "feelslike_c": _num(c.get("apparent_temperature"), float(c["temperature_2m"])),
            "humidity": int(round(_num(c.get("relative_humidity_2m")))),
            "precip_mm": _num(c.get("precipitation")), "wind_kph": _num(c.get("wind_speed_10m")),
            "uv": 0.0, "condition": weather_text(c.get("weather_code")), "icon": "",
            "is_day": bool(c.get("is_day", 1)), "last_updated": cur_time,
            "place_label": place.label, "provider": "Open-Meteo",
        }
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise WeatherDataError("Weather service returned an unexpected response.") from exc

    hours: list[dict[str, Any]] = []
    days: list[dict[str, Any]] = []
    missing_rain_probability = False
    if with_hours:
        try:
            h = payload["hourly"]
            for i, t in enumerate(h["time"]):
                temp = _at(h.get("temperature_2m"), i)
                if temp is None:
                    continue   # no value for that hour -> skip it rather than invent one
                prob = _at(h.get("precipitation_probability"), i)
                if prob is None:
                    missing_rain_probability = True
                hours.append({
                    "time": str(t), "temp_c": float(temp),
                    "feelslike_c": _num(_at(h.get("apparent_temperature"), i), float(temp)),
                    "chance_of_rain": _num(prob), "precip_mm": _num(_at(h.get("precipitation"), i)),
                    "humidity": _num(_at(h.get("relative_humidity_2m"), i)), "wind_kph": _num(_at(h.get("wind_speed_10m"), i)),
                    "uv": _num(_at(h.get("uv_index"), i)), "is_day": bool(_at(h.get("is_day"), i) if _at(h.get("is_day"), i) is not None else 1),
                    "condition": weather_text(_at(h.get("weather_code"), i)),
                })
            d = payload.get("daily") or {}
            for i, date in enumerate(d.get("time", []) or []):
                days.append({"date": str(date), "max_c": _num(_at(d.get("temperature_2m_max"), i)),
                             "min_c": _num(_at(d.get("temperature_2m_min"), i)),
                             "chance_of_rain": _num(_at(d.get("precipitation_probability_max"), i)),
                             "total_precip_mm": _num(_at(d.get("precipitation_sum"), i)),
                             "condition": weather_text(_at(d.get("weather_code"), i))})
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise WeatherDataError("Weather service returned an unexpected forecast.") from exc
        if not hours:
            raise WeatherDataError("Weather service returned no hourly forecast data.")
        # current UV: take the hour that matches the current time, if the forecast has it
        match = next((x for x in hours if x["time"][:13] == current["localtime"].replace(" ", "T")[:13]), None)
        if match:
            current["uv"] = match["uv"]
    return {"current": current, "hours": hours, "days": days, "alerts": [], "alerts_supported": False,
            "rain_probability_missing": missing_rain_probability, "place_others": place.others}


class OpenMeteoClient(WeatherClient):
    """Same interface as WeatherClient; ``query`` may be a city name ("Bhopal", "Bhopal, India") or "lat,lon"."""

    provider = "Open-Meteo"

    def __init__(self, session: Any = None, cache_ttl: float = 600, stale_ttl: float = 3600, geo_ttl: float = 7 * 86400,
                 timeout: tuple[float, float] = (5, 15), retries: int = 1, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        super().__init__("", session=session, cache_ttl=cache_ttl, stale_ttl=stale_ttl, timeout=timeout, retries=retries, clock=clock)
        self.geo_ttl, self._sleep = geo_ttl, sleep
        self._geo: dict[str, tuple[float, Place]] = {}

    # ------------------------------------------------------------------ public
    def get_current(self, query: str) -> dict[str, Any]:
        q = self._check(query)
        return self._cached(("current", q.lower()), lambda: self._load(q, days=1, with_hours=False))

    def get_forecast(self, query: str, days: int = 3) -> dict[str, Any]:
        q = self._check(query)
        days = max(1, min(int(days), 16))
        return self._cached(("forecast", q.lower(), days), lambda: self._load(q, days=days, with_hours=True))

    def geocode(self, query: str) -> Place:
        """Look up a city name (cached). Accepts "City", "City, Country" or "City, State"."""
        q = self._check(query)
        key = q.lower()
        hit = self._geo.get(key)
        now = self._clock()
        if hit and now - hit[0] <= self.geo_ttl:
            return hit[1]
        m = _COORDS.match(q)
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise WeatherNotFound(f"'{q}' is not a valid latitude, longitude pair.")
            place = Place("Your location", "", "", lat, lon, "")
        else:
            place = self._geocode_name(q)
        self._geo[key] = (now, place)
        return place

    # --------------------------------------------------------------- internals
    def _check(self, query: str) -> str:   # no API key needed
        q = (query or "").strip()
        if not q:
            raise WeatherNotFound("Please enter a city name.")
        return q

    def _geocode_name(self, q: str) -> Place:
        parts = [p.strip() for p in q.split(",") if p.strip()]
        name, qualifiers = parts[0], [p.lower() for p in parts[1:]]
        payload = self._get_json(GEOCODE_URL, {"name": name, "count": 10, "language": "en", "format": "json"})
        results = payload.get("results") or []
        if qualifiers:
            def ok(r: dict[str, Any]) -> bool:
                fields = [str(r.get(k, "")).lower() for k in ("country", "country_code", "admin1", "admin2")]
                return all(any(qual == f or (len(qual) >= 3 and f.startswith(qual)) for f in fields) for qual in qualifiers)
            results = [r for r in results if ok(r)]
        if not results:
            hint = "" if qualifiers else " Check the spelling or try 'City, Country'."
            where = f" in '{', '.join(parts[1:])}'" if qualifiers else ""
            raise WeatherNotFound(f"I couldn't find a place called '{name}'{where}.{hint}")
        try:
            top = results[0]
            place = Place(str(top["name"]), str(top.get("admin1", "") or ""), str(top.get("country", "") or ""),
                          float(top["latitude"]), float(top["longitude"]), str(top.get("timezone", "") or ""))
        except (KeyError, TypeError, ValueError) as exc:
            raise WeatherDataError("The location service returned an unexpected response.") from exc
        seen = {(place.country, place.admin1)}
        for r in results[1:]:
            tag = (str(r.get("country", "") or ""), str(r.get("admin1", "") or ""))
            if tag not in seen and len(place.others) < 3:
                seen.add(tag)
                place.others.append(", ".join(p for p in (str(r.get("name", name)), tag[1], tag[0]) if p))
        return place

    def _load(self, q: str, days: int, with_hours: bool) -> dict[str, Any]:
        place = self.geocode(q)
        params: dict[str, Any] = {"latitude": f"{place.lat:.4f}", "longitude": f"{place.lon:.4f}", "current": CURRENT_VARS,
                                  "timezone": "auto", "wind_speed_unit": "kmh", "forecast_days": days}
        if with_hours:
            params.update({"hourly": HOURLY_VARS, "daily": DAILY_VARS})
        payload = self._get_json(FORECAST_URL, params)
        if not place.timezone and payload.get("timezone"):
            place.timezone = str(payload["timezone"])
        return normalize_open_meteo(payload, place, with_hours)

    def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            if attempt:
                self._sleep(0.5 * (2 ** (attempt - 1)))
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
            except requests.Timeout:
                last = WeatherTimeout("Weather request timed out. Check your internet connection and try again.")
                continue
            except requests.RequestException:   # not echoing the exception: it can contain the URL
                last = WeatherConnectionError("Could not connect to Open-Meteo. Check your internet connection.")
                continue
            if resp.status_code == 429:
                raise WeatherRateLimit("Open-Meteo says too many requests were made. Wait a minute and try again.")
            if resp.status_code >= 500:
                last = WeatherServiceError("Open-Meteo is having problems right now. Try again shortly.")
                continue
            try:
                payload = resp.json()
            except ValueError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            if resp.status_code >= 400 or payload.get("error") is True:
                raise WeatherServiceError(str(payload.get("reason", "Weather service request failed."))[:200])
            return payload
        assert last is not None
        raise last


def fetched_at_text(data: dict[str, Any], now: datetime | None = None) -> str:
    """'fetched 3 min ago' style text from a result's ``age_s`` (0 = just fetched)."""
    age = int(data.get("age_s", 0) or 0)
    if age < 60:
        return "fetched just now"
    return f"fetched about {age // 60} min ago" + (" (cached copy; the live service was unreachable)" if data.get("stale") else " (cached)")
