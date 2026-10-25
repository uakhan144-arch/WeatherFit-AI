"""Shared planning step: fetch forecasts and slice them into origin / destination time windows.

Used by both the chat assistant and the UI's trip planner so they always agree.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from trip import ForecastUnavailable, WindowSummary, arrival_time, parse_local, summarize_window
from weather_service import WeatherError

DEFAULT_JOURNEY_H = 3.0


def short_name(name: str) -> str:
    return name.split(",")[0].strip()


@dataclass
class Plan:
    origin_fc: dict[str, Any]
    dest_fc: dict[str, Any] | None
    origin_w: WindowSummary
    dest_w: WindowSummary | None
    depart: datetime
    now_local: datetime
    duration_h: float | None
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _stale_note(fc: dict[str, Any]) -> str:
    return f"Live weather for {short_name(fc['current']['city'])} is unreachable; using data cached about {fc['age_s'] // 60} min ago."


def build_plan(client: Any, origin_q: str, dest_q: str | None = None, depart: datetime | None = None,
               duration_h: float | None = None, stay_h: float = 3.0, now: datetime | None = None) -> Plan:
    """Raises WeatherError if the ORIGIN forecast can't be fetched and ForecastUnavailable if the time is out of range.
    A failing destination is reported in ``warnings`` and the plan falls back to the origin only."""
    fc_o = client.get_forecast(origin_q, days=3)
    now_local = now or parse_local(fc_o["current"]["localtime"])
    depart = depart or now_local
    plan_warnings: list[str] = [_stale_note(fc_o)] if fc_o.get("stale") else []
    assumptions: list[str] = []
    o_name = short_name(fc_o["current"]["city"])
    fc_d = dest_w = None
    if dest_q:
        origin_w = summarize_window(fc_o, depart, depart + timedelta(hours=1), o_name)
        if duration_h is None:
            duration_h = DEFAULT_JOURNEY_H
            assumptions.append("Journey time wasn't given, so I assumed about 3 hours. Tell me the real duration for a more accurate answer.")
        try:
            fc_d = client.get_forecast(dest_q, days=3)
            if fc_d.get("stale"):
                plan_warnings.append(_stale_note(fc_d))
            arrive = arrival_time(depart, duration_h, fc_o["current"].get("tz_id", ""), fc_d["current"].get("tz_id", ""))
            dest_w = summarize_window(fc_d, arrive, arrive + timedelta(hours=stay_h), short_name(fc_d["current"]["city"]))
        except (WeatherError, ForecastUnavailable) as exc:
            fc_d = dest_w = None
            plan_warnings.append(f"I couldn't get destination weather for {dest_q}: {exc} Showing {o_name} only.")
    else:
        duration_h = None
        end = depart.replace(hour=22, minute=0)
        if end - depart < timedelta(hours=3):
            end = depart + timedelta(hours=3)
        origin_w = summarize_window(fc_o, depart, end, o_name)
        assumptions.append("I looked at the rest of today." if depart.date() == now_local.date()
                           else f"I looked at {depart:%a %d %b} from {depart:%H:%M}.")
    return Plan(fc_o, fc_d, origin_w, dest_w, depart, now_local, duration_h, assumptions, plan_warnings)
