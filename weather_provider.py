"""Chooses the weather client from settings (see config.py). No Streamlit dependency.

WEATHER_PROVIDER=auto (default): WeatherAPI.com when WEATHERAPI_KEY is set (your existing behaviour), otherwise the
free key-less Open-Meteo service. Force one with WEATHER_PROVIDER=openmeteo or WEATHER_PROVIDER=weatherapi.
"""
from __future__ import annotations

import config
from openmeteo_service import ATTRIBUTION, ATTRIBUTION_URL, OpenMeteoClient
from weather_service import WeatherClient


def make_weather_client(provider: str | None = None, api_key: str | None = None, cache_seconds: float | None = None):
    provider = provider or config.weather_provider()
    ttl = cache_seconds if cache_seconds is not None else config.weather_cache_seconds()
    if provider == "openmeteo":
        return OpenMeteoClient(cache_ttl=ttl)
    key = api_key if api_key is not None else config.get_str("WEATHERAPI_KEY")
    client = WeatherClient(key, cache_ttl=ttl)
    client.provider = "WeatherAPI.com"
    return client


def provider_name(client) -> str:
    return getattr(client, "provider", "WeatherAPI.com")


def attribution_markdown(client) -> str:
    """Attribution text the provider's terms ask us to show next to the data."""
    if provider_name(client) == "Open-Meteo":
        return f"[{ATTRIBUTION}]({ATTRIBUTION_URL}) (CC BY 4.0, free for non-commercial use)"
    return "Weather data by [WeatherAPI.com](https://www.weatherapi.com/)"
