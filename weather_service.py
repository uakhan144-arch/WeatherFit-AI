"""WeatherAPI.com client: current weather, hourly forecast, alerts.

* No Streamlit dependency, so it can be unit-tested and reused anywhere.
* Every request has a (connect, read) timeout and one retry on timeouts / 5xx.
* Successful responses are cached in memory (TTL). If the API is temporarily
  unreachable, a recent cached copy is returned and flagged ``stale=True`` so
  the UI can say so honestly. Nothing is ever invented.
* Error messages never contain the API key or the request URL.
"""
from __future__ import annotations

import copy
import threading
import time
from typing import Any, Callable

import requests

BASE_URL = "https://api.weatherapi.com/v1"
CURRENT_URL = f"{BASE_URL}/current.json"
FORECAST_URL = f"{BASE_URL}/forecast.json"


class WeatherError(ValueError):
    """Base class. Subclasses ValueError so older ``except ValueError`` code still works."""


class WeatherConfigError(WeatherError): ...
class WeatherNotFound(WeatherError): ...
class WeatherAuthError(WeatherError): ...
class WeatherRateLimit(WeatherError): ...
class WeatherTimeout(WeatherError): ...
class WeatherConnectionError(WeatherError): ...
class WeatherServiceError(WeatherError): ...
class WeatherDataError(WeatherError): ...


# Errors for which an older cached copy is better than nothing.
_STALE_OK = (WeatherTimeout, WeatherConnectionError, WeatherServiceError, WeatherRateLimit)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _icon(url: str) -> str:
    return "https:" + url if url.startswith("//") else url


def normalize_current(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert a WeatherAPI payload to the flat dict the rest of the app uses."""
    try:
        c, loc = payload["current"], payload["location"]
        return {
            "city": f"{loc['name']}, {loc['country']}",
            "region": str(loc.get("region", "")),
            "lat": _num(loc.get("lat"), float("nan")),
            "lon": _num(loc.get("lon"), float("nan")),
            "tz_id": str(loc.get("tz_id", "")),
            "localtime": str(loc.get("localtime", "")),
            "temp_c": float(c["temp_c"]),
            "feelslike_c": float(c["feelslike_c"]),
            "humidity": int(c["humidity"]),
            "precip_mm": _num(c.get("precip_mm")),
            "wind_kph": _num(c.get("wind_kph")),
            "uv": _num(c.get("uv")),
            "condition": str(c["condition"].get("text", "Unknown")),
            "icon": _icon(str(c["condition"].get("icon", ""))),
            "is_day": bool(c.get("is_day", 1)),
            "last_updated": str(c.get("last_updated", "")),
        }
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise WeatherDataError("Weather service returned an unexpected response.") from exc


def normalize_forecast(payload: dict[str, Any]) -> dict[str, Any]:
    """Current conditions + hourly rows + daily rows + alerts, all JSON-serialisable."""
    current = normalize_current(payload)
    hours: list[dict[str, Any]] = []
    days: list[dict[str, Any]] = []
    try:
        for day in payload["forecast"]["forecastday"]:
            d = day.get("day", {})
            days.append({
                "date": str(day["date"]),
                "max_c": _num(d.get("maxtemp_c")),
                "min_c": _num(d.get("mintemp_c")),
                "chance_of_rain": _num(d.get("daily_chance_of_rain")),
                "total_precip_mm": _num(d.get("totalprecip_mm")),
                "condition": str(d.get("condition", {}).get("text", "")),
            })
            for h in day.get("hour", []):
                hours.append({
                    "time": str(h["time"]).replace(" ", "T"),  # local time at that place
                    "temp_c": float(h["temp_c"]),
                    "feelslike_c": _num(h.get("feelslike_c"), float(h["temp_c"])),
                    "chance_of_rain": _num(h.get("chance_of_rain")),
                    "precip_mm": _num(h.get("precip_mm")),
                    "humidity": _num(h.get("humidity")),
                    "wind_kph": _num(h.get("wind_kph")),
                    "uv": _num(h.get("uv")),
                    "is_day": bool(h.get("is_day", 1)),
                    "condition": str(h.get("condition", {}).get("text", "Unknown")),
                })
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise WeatherDataError("Weather service returned an unexpected forecast.") from exc
    if not hours:
        raise WeatherDataError("Weather service returned no hourly forecast data.")
    alerts = []
    for a in (payload.get("alerts") or {}).get("alert", []) or []:
        alerts.append({k: str(a.get(k, "")) for k in
                       ("headline", "event", "severity", "urgency", "areas", "effective", "expires", "desc")})
    return {"current": current, "hours": hours, "days": days, "alerts": alerts}


class WeatherClient:
    def __init__(
        self,
        api_key: str,
        session: Any = None,
        cache_ttl: float = 600,
        stale_ttl: float = 3600,
        timeout: tuple[float, float] = (5, 15),
        retries: int = 1,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._key = (api_key or "").strip()
        self._session = session or requests
        self.cache_ttl, self.stale_ttl = cache_ttl, stale_ttl
        self.timeout, self.retries, self._clock = timeout, retries, clock
        self._cache: dict[tuple, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ public
    def get_current(self, query: str) -> dict[str, Any]:
        q = self._check(query)
        return self._cached(("current", q.lower()), lambda: normalize_current(
            self._request(CURRENT_URL, {"q": q, "aqi": "no"}, q)))

    def get_forecast(self, query: str, days: int = 3) -> dict[str, Any]:
        """Hourly forecast. Free WeatherAPI plans return at most ~3 days."""
        q = self._check(query)
        days = max(1, min(int(days), 14))
        return self._cached(("forecast", q.lower(), days), lambda: normalize_forecast(
            self._request(FORECAST_URL, {"q": q, "days": days, "aqi": "no", "alerts": "yes"}, q)))

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    # --------------------------------------------------------------- internals
    def _check(self, query: str) -> str:
        if not self._key:
            raise WeatherConfigError("WeatherAPI key missing. Add WEATHERAPI_KEY to your local .env file.")
        q = (query or "").strip()
        if not q:
            raise WeatherNotFound("Please enter a city name.")
        return q

    def _cached(self, key: tuple, fetch: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        now = self._clock()
        with self._lock:
            hit = self._cache.get(key)
        if hit and now - hit[0] <= self.cache_ttl:
            return {**copy.deepcopy(hit[1]), "stale": False, "age_s": int(now - hit[0])}
        try:
            data = fetch()
        except _STALE_OK:
            if hit and now - hit[0] <= self.stale_ttl:
                return {**copy.deepcopy(hit[1]), "stale": True, "age_s": int(now - hit[0])}
            raise
        with self._lock:
            self._cache[key] = (now, copy.deepcopy(data))
        return {**data, "stale": False, "age_s": 0}

    def _request(self, url: str, params: dict[str, Any], q: str) -> dict[str, Any]:
        params = {"key": self._key, **params}
        last: WeatherError | None = None
        for _ in range(self.retries + 1):
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
            except requests.Timeout:
                last = WeatherTimeout("Weather request timed out. Check your internet connection and try again.")
                continue
            except requests.RequestException:  # deliberately not echoing exc: it can contain the URL + key
                last = WeatherConnectionError("Could not connect to WeatherAPI.com. Check your internet connection.")
                continue
            if resp.status_code >= 500:
                last = WeatherServiceError("WeatherAPI.com is having problems right now. Try again shortly.")
                continue
            return self._parse(resp, q)
        assert last is not None
        raise last

    @staticmethod
    def _parse(resp: Any, q: str) -> dict[str, Any]:
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if resp.status_code >= 400 or "error" in payload:
            err = payload.get("error", {}) or {}
            code = err.get("code")
            if code == 1006:
                raise WeatherNotFound(f"Location '{q}' was not found. Check the spelling or try 'City, Country'.")
            if code in (1002, 2006, 2008, 2009):
                raise WeatherAuthError("WeatherAPI key is missing, invalid or disabled. Check your local .env file.")
            if code == 2007:
                raise WeatherRateLimit("WeatherAPI request limit reached. Try again later.")
            if resp.status_code in (401, 403):
                raise WeatherAuthError("WeatherAPI rejected the key. Check your local .env file.")
            raise WeatherServiceError(str(err.get("message", "Weather service request failed."))[:200])
        return payload
