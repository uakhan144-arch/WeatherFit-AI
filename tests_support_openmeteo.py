"""Fakes for Open-Meteo tests: realistic geocoding + forecast payloads and a fake HTTP session (no network needed)."""
from __future__ import annotations

from datetime import datetime, timedelta

PLACES = {
    "bhopal": {"name": "Bhopal", "latitude": 23.2599, "longitude": 77.4126, "country": "India", "country_code": "IN", "admin1": "Madhya Pradesh", "timezone": "Asia/Kolkata"},
    "indore": {"name": "Indore", "latitude": 22.7179, "longitude": 75.8333, "country": "India", "country_code": "IN", "admin1": "Madhya Pradesh", "timezone": "Asia/Kolkata"},
    "gwalior": {"name": "Gwalior", "latitude": 26.2183, "longitude": 78.1828, "country": "India", "country_code": "IN", "admin1": "Madhya Pradesh", "timezone": "Asia/Kolkata"},
}


def make_forecast(now="2026-10-25T10:15", days=3, temp=lambda d, h: 30.0, feels=None, rain=lambda d, h: 0, precip=lambda d, h: 0.0,
                  wind=lambda d, h: 10.0, code=lambda d, h: 1, uv=lambda d, h: 6.0 if 8 <= h <= 17 else 0.0, tz="Asia/Kolkata", null_rain=False):
    start = datetime.fromisoformat(now).replace(hour=0, minute=0)
    feels = feels or (lambda d, h: temp(d, h) + 1)
    times, tt, ft, pp, pr, hu, wc, ws, uvs, isd = ([] for _ in range(10))
    for d in range(days):
        for h in range(24):
            times.append(f"{(start + timedelta(days=d)):%Y-%m-%d}T{h:02d}:00")
            tt.append(temp(d, h)); ft.append(feels(d, h)); pp.append(None if null_rain else rain(d, h)); pr.append(precip(d, h))
            hu.append(50); wc.append(code(d, h)); ws.append(wind(d, h)); uvs.append(uv(d, h)); isd.append(1 if 6 <= h <= 18 else 0)
    ch = int(now[11:13])
    return {"timezone": tz, "utc_offset_seconds": 19800,
            "current": {"time": now, "temperature_2m": temp(0, ch), "relative_humidity_2m": 50, "apparent_temperature": feels(0, ch), "is_day": 1,
                        "precipitation": 0.0, "weather_code": code(0, ch), "wind_speed_10m": wind(0, ch)},
            "hourly": {"time": times, "temperature_2m": tt, "apparent_temperature": ft, "relative_humidity_2m": hu, "precipitation_probability": pp,
                       "precipitation": pr, "weather_code": wc, "wind_speed_10m": ws, "uv_index": uvs, "is_day": isd},
            "daily": {"time": [f"{(start + timedelta(days=d)):%Y-%m-%d}" for d in range(days)], "temperature_2m_max": [33] * days,
                      "temperature_2m_min": [21] * days, "precipitation_probability_max": [10] * days, "precipitation_sum": [0.0] * days,
                      "weather_code": [1] * days}}


class Resp:
    def __init__(self, status=200, payload=None, bad_json=False):
        self.status_code, self._p, self._bad = status, payload, bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._p


class OMSession:
    """Routes geocoding by name and forecasts by rounded latitude. ``forecasts`` maps lower-case city -> payload (or Exception/Resp)."""

    def __init__(self, forecasts=None, geocode=None):
        self.forecasts = forecasts or {}
        self.geocode = geocode if geocode is not None else PLACES
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params), "timeout": timeout})
        if "geocoding" in url:
            name = params["name"].lower()
            hits = [self.geocode[name]] if name in self.geocode else []
            extra = self.geocode.get(name + "#2")
            if extra:
                hits.append(extra)
            return Resp(200, {"results": hits} if hits else {"generationtime_ms": 0.3})
        for city, place in self.geocode.items():
            if abs(float(params["latitude"]) - place["latitude"]) < 0.01:
                r = self.forecasts.get(city.split("#")[0])
                if r is None:
                    break
                if isinstance(r, Exception):
                    raise r
                return r if isinstance(r, Resp) else Resp(200, r)
        return Resp(400, {"error": True, "reason": "Cannot initialize WeatherVariable from invalid String value."})

    @property
    def urls(self):
        return [c["url"] for c in self.calls]
