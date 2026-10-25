"""Open-Meteo client: geocoding, normalisation, caching, errors. Uses fake HTTP (no network)."""
import pytest
import requests

from openmeteo_service import OpenMeteoClient, weather_text
from plan_service import build_plan
from tests_support import Clock
from tests_support_openmeteo import OMSession, Resp, make_forecast
from weather_service import WeatherNotFound, WeatherRateLimit, WeatherServiceError, WeatherTimeout


def client(forecasts=None, geocode=None, **kw):
    s = OMSession(forecasts or {"bhopal": make_forecast(), "indore": make_forecast()}, geocode)
    return OpenMeteoClient(session=s, sleep=lambda x: None, **kw), s


def test_forecast_has_same_shape_as_weatherapi_client():
    c, s = client()
    fc = c.get_forecast("Bhopal")
    assert fc["current"]["city"] == "Bhopal, India" and fc["current"]["tz_id"] == "Asia/Kolkata"
    assert len(fc["hours"]) == 72 and len(fc["days"]) == 3 and fc["alerts"] == []
    for k in ("city", "temp_c", "feelslike_c", "humidity", "precip_mm", "wind_kph", "condition", "icon", "is_day", "localtime", "last_updated"):
        assert k in fc["current"]
    assert fc["current"]["localtime"] == "2026-10-25 10:15" and fc["hours"][0]["time"] == "2026-10-25T00:00"
    forecast_call = [x for x in s.calls if "geocoding" not in x["url"]][0]["params"]
    assert "precipitation_probability" in forecast_call["hourly"] and "apparent_temperature" in forecast_call["current"]
    assert forecast_call["timezone"] == "auto" and "key" not in forecast_call


def test_works_with_plan_service_for_a_trip():
    c, _ = client()
    plan = build_plan(c, "Bhopal", "Indore", None, 4.0)
    assert plan.dest_w is not None and plan.origin_w.place == "Bhopal"


def test_results_are_cached_and_geocoding_is_cached_too():
    clock = Clock()
    c, s = client(clock=clock)
    c.get_forecast("Bhopal"); c.get_forecast("bhopal")
    assert len(s.calls) == 2   # one geocode + one forecast, second request served from cache
    clock.t += 601
    c.get_forecast("Bhopal")
    assert len(s.calls) == 3   # forecast refreshed, city lookup reused


def test_stale_copy_used_when_service_down():
    clock = Clock()
    c, s = client(clock=clock)
    c.get_forecast("Bhopal")
    s.forecasts["bhopal"] = requests.ConnectionError("down https://api.open-meteo.com/?x=1")
    clock.t += 700
    fc = c.get_forecast("Bhopal")
    assert fc["stale"] is True and fc["age_s"] >= 700


def test_unknown_city_and_qualifiers():
    c, _ = client()
    with pytest.raises(WeatherNotFound):
        c.get_forecast("Atlantis")
    with pytest.raises(WeatherNotFound):
        c.get_forecast("Bhopal, France")
    assert c.get_forecast("Bhopal, India")["current"]["city"] == "Bhopal, India"
    assert c.get_forecast("Bhopal, Madhya Pradesh")["current"]["region"] == "Madhya Pradesh"
    with pytest.raises(WeatherNotFound):
        c.get_forecast("   ")


def test_same_name_in_other_country_is_reported():
    geo = {"bhopal": {"name": "Bhopal", "latitude": 23.26, "longitude": 77.41, "country": "India", "admin1": "Madhya Pradesh", "timezone": "Asia/Kolkata"},
           "bhopal#2": {"name": "Bhopal", "latitude": 5.0, "longitude": 6.0, "country": "Elsewhere", "admin1": "North", "timezone": "UTC"}}
    c, _ = client({"bhopal": make_forecast()}, geo)
    assert c.get_forecast("Bhopal")["place_others"] == ["Bhopal, North, Elsewhere"]


def test_coordinates_query_skips_geocoding_and_bad_ones_rejected():
    c, s = client({"bhopal": make_forecast()})
    fc = c.get_forecast("23.2599,77.4126")
    assert fc["current"]["city"] == "Your location" and all("geocoding" not in u for u in s.urls)
    with pytest.raises(WeatherNotFound):
        c.get_forecast("123.0,500.0")


def test_errors_map_and_retry_rules():
    c, s = client({"bhopal": requests.Timeout("t https://x?key=abc")})
    with pytest.raises(WeatherTimeout) as e:
        c.get_forecast("Bhopal")
    assert "http" not in str(e.value)
    assert len([x for x in s.calls if "geocoding" not in x["url"]]) == 2   # one retry only
    c, s = client({"bhopal": Resp(503, {})})
    with pytest.raises(WeatherServiceError):
        c.get_forecast("Bhopal")
    c, s = client({"bhopal": Resp(429, {})})
    with pytest.raises(WeatherRateLimit):
        c.get_forecast("Bhopal")
    assert len([x for x in s.calls if "geocoding" not in x["url"]]) == 1   # 429 is never retried


def test_missing_rain_probability_is_flagged_not_invented():
    c, _ = client({"bhopal": make_forecast(null_rain=True)})
    fc = c.get_forecast("Bhopal")
    assert fc["rain_probability_missing"] is True


def test_weather_codes():
    assert weather_text(61) == "Slight rain" and weather_text(95) == "Thunderstorm" and weather_text(None) == "Unknown" and weather_text(1234) == "Unknown"
