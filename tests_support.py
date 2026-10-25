"""Shared fakes for tests: WeatherAPI-shaped payloads and a fake HTTP session (no network needed)."""
from __future__ import annotations

from datetime import datetime, timedelta

import requests

KEY = "SECRET-TEST-KEY-123"


def make_payload(name="Indore", region="Madhya Pradesh", country="India", tz="Asia/Kolkata",
                 localtime="2026-10-24 10:00", temp=lambda d, h: 30.0, feels=None, rain=lambda d, h: 0,
                 precip=lambda d, h: 0.0, uv=lambda d, h: 7.0 if 8 <= h <= 17 else 0.0, wind=lambda d, h: 10.0,
                 condition=lambda d, h: "Sunny", days=3, alerts=None, lat=22.72, lon=75.86):
    start = datetime.fromisoformat(localtime.replace(" ", "T")).replace(hour=0, minute=0)
    feels = feels or (lambda d, h: temp(d, h) + 1)
    fdays = []
    for d in range(days):
        date = start + timedelta(days=d)
        hours = []
        for h in range(24):
            hours.append({"time": f"{date:%Y-%m-%d} {h:02d}:00", "temp_c": temp(d, h), "feelslike_c": feels(d, h),
                          "chance_of_rain": rain(d, h), "precip_mm": precip(d, h), "humidity": 50, "wind_kph": wind(d, h),
                          "uv": uv(d, h), "is_day": 1 if 6 <= h <= 18 else 0, "condition": {"text": condition(d, h)}})
        fdays.append({"date": f"{date:%Y-%m-%d}", "day": {"maxtemp_c": 33, "mintemp_c": 22, "daily_chance_of_rain": 10,
                                                         "totalprecip_mm": 0, "condition": {"text": "Sunny"}}, "hour": hours})
    h0 = localtime
    cur_h = int(localtime[11:13])
    return {
        "location": {"name": name, "region": region, "country": country, "tz_id": tz, "localtime": localtime, "lat": lat, "lon": lon},
        "current": {"temp_c": temp(0, cur_h), "feelslike_c": feels(0, cur_h), "humidity": 50, "precip_mm": 0.0, "wind_kph": 10.0,
                    "uv": 7, "condition": {"text": condition(0, cur_h), "icon": "//cdn.weatherapi.com/x.png"}, "is_day": 1,
                    "last_updated": h0},
        "forecast": {"forecastday": fdays},
        "alerts": {"alert": alerts or []},
    }


class FakeResponse:
    def __init__(self, status=200, payload=None, bad_json=False):
        self.status_code, self._p, self._bad = status, payload, bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._p


class FakeSession:
    """Maps lower-cased ``q`` -> payload (or an Exception / FakeResponse to return/raise)."""

    def __init__(self, routes=None):
        self.routes, self.calls = routes or {}, []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params), "timeout": timeout})
        r = self.routes.get(params["q"].lower())
        if r is None:
            return FakeResponse(400, {"error": {"code": 1006, "message": "No matching location found."}})
        if isinstance(r, Exception):
            raise r
        if isinstance(r, FakeResponse):
            return r
        return FakeResponse(200, r)

    @property
    def queries(self):
        return [c["params"]["q"] for c in self.calls]


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def timeout_error():
    return requests.Timeout(f"timed out https://api.weatherapi.com/v1/forecast.json?key={KEY}")


def conn_error():
    return requests.ConnectionError(f"max retries https://api.weatherapi.com/v1/forecast.json?key={KEY}")
