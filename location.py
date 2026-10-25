"""Location resolution with an honest source label.

``source`` is one of:
  * "browser" - coordinates really came from the browser's geolocation API
  * "manual"  - the user typed a city
  * "none"    - nothing usable yet (permission denied / not shared / no city)

The app must only say "detected automatically" when ``detected`` is True.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LocationResult:
    source: str            # "browser" | "manual" | "none"
    query: str             # string passed to WeatherAPI ("lat,lon" or city); "" if none
    label: str             # human readable
    detected: bool         # True only for real browser coordinates
    message: str = ""      # explanation for the UI (e.g. why we fell back)
    lat: float | None = None
    lon: float | None = None

    @property
    def usable(self) -> bool:
        return bool(self.query)


def parse_browser_payload(payload: Any) -> tuple[float, float] | None:
    """Return (lat, lon) only for a well-formed, in-range, non-error payload."""
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    try:
        lat, lon = float(payload["latitude"]), float(payload["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def resolve_location(browser_payload: Any, manual_city: str = "", prefer_manual: bool = False) -> LocationResult:
    manual = (manual_city or "").strip()
    coords = parse_browser_payload(browser_payload)

    if manual and (prefer_manual or coords is None):
        note = ""
        if coords is None and not prefer_manual:
            note = "Browser location is not available (permission not granted or not shared), so your typed city is used."
        return LocationResult("manual", manual, manual, False, note)

    if coords is not None:
        lat, lon = coords
        return LocationResult("browser", f"{lat:.4f},{lon:.4f}", f"Your current location ({lat:.2f}, {lon:.2f})",
                              True, "Detected from your browser location (you granted permission).", lat, lon)

    return LocationResult(
        "none", "", "Location not set", False,
        "Location was not detected. Click the location button and allow access, or type your city manually.")
