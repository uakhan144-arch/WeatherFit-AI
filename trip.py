"""Travel-aware forecast windows.

Origin window      = departure -> departure + 1h   ("when you leave")
Destination window = arrival   -> arrival + stay   ("while you're there")
arrival = departure + travel duration (the user supplies the duration; we do not guess routes).

All hourly rows come from the WeatherAPI forecast (local time of each place).
If the requested time is outside the forecast, ForecastUnavailable is raised -
we never fill gaps with made-up values.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

try:  # zoneinfo needs the "tzdata" package on Windows (listed in requirements.txt)
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore


class ForecastUnavailable(ValueError):
    pass


@dataclass
class WindowSummary:
    place: str
    start: str
    end: str
    hours: int
    expected_hours: int
    temp_min: float
    temp_max: float
    feels_min: float
    feels_max: float
    feels_avg: float
    rain_chance_max: float
    precip_total_mm: float
    humidity_avg: float
    wind_max_kph: float
    uv_max: float
    daytime: bool
    conditions: list[str] = field(default_factory=list)
    alerts: list[dict[str, str]] = field(default_factory=list)

    @property
    def partial(self) -> bool:
        return self.hours < self.expected_hours

    @property
    def main_condition(self) -> str:
        return self.conditions[0] if self.conditions else "Unknown"

    def to_weather_dict(self) -> dict[str, Any]:
        """Shape expected by recommendations.build_recommendations (dress for the window average)."""
        return {"city": self.place, "temp_c": (self.temp_min + self.temp_max) / 2, "feelslike_c": self.feels_avg,
                "humidity": int(round(self.humidity_avg)), "precip_mm": self.precip_total_mm,
                "wind_kph": self.wind_max_kph, "condition": self.main_condition,
                "localtime": self.start, "icon": "", "is_day": self.daytime}


def _tz(tz_id: str):
    if ZoneInfo and tz_id:
        try:
            return ZoneInfo(tz_id)
        except Exception:
            return None
    return None


def parse_local(value: str) -> datetime:
    """Parse WeatherAPI local-time strings ('2026-10-24 09:00' or ISO) to a naive datetime."""
    return datetime.fromisoformat(value.replace(" ", "T"))


def arrival_time(departure_local: datetime, duration_h: float, origin_tz: str = "", dest_tz: str = "") -> datetime:
    """Arrival as a naive datetime in the destination's local time."""
    o, d = _tz(origin_tz), _tz(dest_tz)
    arrive = departure_local + timedelta(hours=duration_h)
    if o and d and o != d:
        arrive = departure_local.replace(tzinfo=o).astimezone(d).replace(tzinfo=None) + timedelta(hours=duration_h)
    return arrive


def summarize_window(forecast: dict[str, Any], start: datetime, end: datetime, place: str | None = None) -> WindowSummary:
    """Summarise forecast hours covering [start, end] (naive local times for that place)."""
    if end <= start:
        end = start + timedelta(hours=1)
    first = start.replace(minute=0, second=0, microsecond=0)
    rows = [h for h in forecast["hours"] if first <= parse_local(h["time"]) <= end]
    expected = max(1, int((end - first).total_seconds() // 3600) + 1)
    name = place or forecast["current"]["city"]
    if not rows:
        times = [parse_local(h["time"]) for h in forecast["hours"]]
        raise ForecastUnavailable(
            f"No forecast is available for {name} at {start:%a %d %b %H:%M}. "
            f"The forecast covers {min(times):%a %d %b %H:%M} to {max(times):%a %d %b %H:%M} "
            "(the forecast only covers about 3 days).")
    feels = [r["feelslike_c"] for r in rows]
    temps = [r["temp_c"] for r in rows]
    conds: list[str] = []
    for r in sorted(rows, key=lambda r: -r["chance_of_rain"]):  # wettest condition first
        if r["condition"] not in conds:
            conds.append(r["condition"])
    alerts = forecast.get("alerts", [])
    return WindowSummary(
        place=name, start=f"{start:%Y-%m-%d %H:%M}", end=f"{end:%Y-%m-%d %H:%M}",
        hours=len(rows), expected_hours=expected,
        temp_min=min(temps), temp_max=max(temps),
        feels_min=min(feels), feels_max=max(feels), feels_avg=sum(feels) / len(feels),
        rain_chance_max=max(r["chance_of_rain"] for r in rows),
        precip_total_mm=sum(r["precip_mm"] for r in rows),
        humidity_avg=sum(r["humidity"] for r in rows) / len(rows),
        wind_max_kph=max(r["wind_kph"] for r in rows),
        uv_max=max((r["uv"] for r in rows if r["is_day"]), default=0.0),
        daytime=any(r["is_day"] for r in rows),
        conditions=conds, alerts=list(alerts),
    )


def combine(windows: list[WindowSummary]) -> WindowSummary:
    """Worst-case/aggregate view across several windows (for one outfit that must work everywhere)."""
    if len(windows) == 1:
        return windows[0]
    total_h = sum(w.hours for w in windows)
    conds: list[str] = []
    for w in sorted(windows, key=lambda w: -w.rain_chance_max):
        for c in w.conditions:
            if c not in conds:
                conds.append(c)
    alerts = [a for w in windows for a in w.alerts]
    return WindowSummary(
        place=" + ".join(w.place for w in windows), start=min(w.start for w in windows), end=max(w.end for w in windows),
        hours=total_h, expected_hours=sum(w.expected_hours for w in windows),
        temp_min=min(w.temp_min for w in windows), temp_max=max(w.temp_max for w in windows),
        feels_min=min(w.feels_min for w in windows), feels_max=max(w.feels_max for w in windows),
        feels_avg=sum(w.feels_avg * w.hours for w in windows) / total_h,
        rain_chance_max=max(w.rain_chance_max for w in windows),
        precip_total_mm=sum(w.precip_total_mm for w in windows),
        humidity_avg=sum(w.humidity_avg * w.hours for w in windows) / total_h,
        wind_max_kph=max(w.wind_max_kph for w in windows),
        uv_max=max(w.uv_max for w in windows), daytime=any(w.daytime for w in windows),
        conditions=conds, alerts=alerts,
    )
