"""Travel-aware, explainable outfit advice.

Reuses ``recommendations.build_recommendations`` (the original rule engine) for the base outfit and
adds: umbrella / jacket / sun-protection decisions, origin-vs-destination comparison, budget and
"avoid" preferences. Every suggestion carries a reason that cites real forecast numbers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any

from recommendations import build_recommendations
from trip import WindowSummary, combine

# Thresholds (documented in README)
RAIN_UMBRELLA_YES = 60      # % chance -> carry an umbrella
RAIN_UMBRELLA_MAYBE = 30    # % chance -> compact umbrella "just in case"
JACKET_LIGHT_BELOW = 22.0   # feels-like min (°C) -> light jacket
SWING_LAYER = 8.0           # °C difference between warmest/coolest moment -> removable layer
UV_PROTECT = 6.0
HOT_FEELS = 33.0
WIND_STRONG = 28.0

BUDGET_NOTES = {
    "Budget-friendly": "Budget tip: build this from versatile basics you may already own; a single good layer goes further than many new pieces.",
    "Mid-range": "Budget tip: spend on the pieces you'll re-wear most (footwear and the outer layer).",
    "Premium": "Budget tip: invest in quality fabrics (merino, linen, technical shells) that handle weather swings well.",
}


@dataclass
class CarryItem:
    item: str
    needed: bool
    reason: str


@dataclass
class TripAdvice:
    title: str
    top: str
    bottom: str
    footwear: str
    pieces: str
    outfit_reason: str
    carry: list[CarryItem] = field(default_factory=list)
    comparison: str = ""
    notes: list[str] = field(default_factory=list)
    alternatives: list[dict[str, str]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    alerts: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def item(self, name: str) -> CarryItem | None:
        return next((c for c in self.carry if c.item.lower() == name.lower()), None)


def apply_avoid(text: str, avoid: str) -> str:
    """Honour simple 'avoid' preferences (e.g. 'jeans, wool')."""
    words = [w.strip().lower() for w in re.split(r"[,;/]| and ", avoid or "") if w.strip()]
    if "jeans" in words:
        text = re.sub(r"jeans or chinos", "chinos", text, flags=re.I)
        text = re.sub(r"comfortable jeans", "comfortable chinos", text, flags=re.I)
        text = re.sub(r"dark jeans", "dark chinos", text, flags=re.I)
        text = re.sub(r"\bjeans\b", "chinos", text, flags=re.I)
    if "wool" in words or "sweater" in words:
        text = re.sub(r"warm sweater", "fleece or cotton knit", text, flags=re.I)
        text = re.sub(r"sweater", "fleece or cotton knit", text, flags=re.I)
    return text


def _range(lo: float, hi: float, unit: str = "°C") -> str:
    return f"{lo:.0f}{unit}" if round(lo) == round(hi) else f"{lo:.0f}–{hi:.0f}{unit}"


def compare_places(origin: WindowSummary, dest: WindowSummary) -> str:
    d_feels = dest.feels_avg - origin.feels_avg
    parts = [
        f"{origin.place}: feels like {_range(origin.feels_min, origin.feels_max)}, rain chance up to {origin.rain_chance_max:.0f}% ({origin.main_condition}).",
        f"{dest.place}: feels like {_range(dest.feels_min, dest.feels_max)}, rain chance up to {dest.rain_chance_max:.0f}% ({dest.main_condition}).",
    ]
    if abs(d_feels) < 2:
        parts.append("The two places feel about the same.")
    else:
        parts.append(f"{dest.place} will feel about {abs(d_feels):.0f}°C {'warmer' if d_feels > 0 else 'cooler'} than {origin.place}.")
    if abs(dest.rain_chance_max - origin.rain_chance_max) >= 30:
        wetter = dest if dest.rain_chance_max > origin.rain_chance_max else origin
        parts.append(f"{wetter.place} is noticeably more likely to be wet.")
    return " ".join(parts)


def build_trip_advice(
    origin: WindowSummary,
    dest: WindowSummary | None,
    occasion: str = "Daily casual",
    style: str = "Casual",
    budget: str = "Mid-range",
    colours: str = "",
    avoid: str = "",
    assumptions: list[str] | None = None,
) -> TripAdvice:
    windows = [origin] + ([dest] if dest else [])
    allw = combine(windows)
    base = build_recommendations(allw.to_weather_dict(), occasion, style)
    main = base[0]
    parts = [p.strip() for p in main["pieces"].split(";")]
    top, bottom, footwear = (parts + ["", "", ""])[:3]
    top, bottom, footwear = (apply_avoid(x, avoid) for x in (top, bottom, footwear))

    swing = allw.feels_max - allw.feels_min
    reasons = [main["reason"]]
    reasons.append(f"Dressing for a feels-like average of {allw.feels_avg:.0f}°C (range {_range(allw.feels_min, allw.feels_max)}).")
    if colours.strip():
        reasons.append(f"Keep to your preferred colours ({colours.strip()}).")

    carry: list[CarryItem] = []
    notes: list[str] = []

    # Umbrella
    rc = allw.rain_chance_max
    wet_place = max(windows, key=lambda w: w.rain_chance_max).place
    if rc >= RAIN_UMBRELLA_YES:
        carry.append(CarryItem("Umbrella", True, f"Rain chance reaches {rc:.0f}% ({wet_place}), so carry one."))
        footwear = f"water-resistant {footwear.lower()}" if "water" not in footwear.lower() else footwear
    elif rc >= RAIN_UMBRELLA_MAYBE:
        carry.append(CarryItem("Umbrella", True, f"Rain chance reaches {rc:.0f}% ({wet_place}); a compact umbrella is a cheap precaution."))
    else:
        carry.append(CarryItem("Umbrella", False, f"Rain chance stays at or below {rc:.0f}%, so you can leave it."))

    # Jacket
    if allw.feels_min <= 12:
        carry.append(CarryItem("Jacket", True, f"It will feel as cold as {allw.feels_min:.0f}°C; wear a proper warm jacket or coat."))
    elif allw.feels_min < JACKET_LIGHT_BELOW or swing >= SWING_LAYER:
        why = (f"feels-like drops to {allw.feels_min:.0f}°C" if allw.feels_min < JACKET_LIGHT_BELOW
               else f"temperatures swing by about {swing:.0f}°C")
        carry.append(CarryItem("Light jacket", True, f"A light, removable layer helps because {why}."))
    else:
        carry.append(CarryItem("Jacket", False, f"Lowest feels-like is {allw.feels_min:.0f}°C, so a jacket isn't needed."))

    # Sun protection (only for daytime exposure)
    sunny = allw.daytime and (allw.uv_max >= UV_PROTECT or (allw.temp_max >= 30 and rc < RAIN_UMBRELLA_MAYBE))
    if sunny:
        why = f"UV index reaches {allw.uv_max:.0f}" if allw.uv_max >= UV_PROTECT else f"it gets up to {allw.temp_max:.0f}°C in daylight"
        carry.append(CarryItem("Sunglasses", True, f"Bright daytime conditions ({why})."))
        carry.append(CarryItem("Cap", True, f"Shade for your face and head ({why})."))
        if allw.uv_max >= UV_PROTECT:
            carry.append(CarryItem("Sunscreen", True, f"UV index reaches {allw.uv_max:.0f}."))
    # Wind / heat / cold extras
    if allw.wind_max_kph >= WIND_STRONG:
        carry.append(CarryItem("Windbreaker", True, f"Gusts up to {allw.wind_max_kph:.0f} km/h."))
    if allw.feels_max >= HOT_FEELS:
        carry.append(CarryItem("Water bottle", True, f"It will feel as hot as {allw.feels_max:.0f}°C."))
    if allw.feels_min <= 8:
        carry.append(CarryItem("Scarf and beanie", True, f"It will feel as cold as {allw.feels_min:.0f}°C."))

    if allw.partial:
        notes.append("The forecast covers only part of your time window, so treat the later hours with caution.")
    if allw.alerts:
        notes.append("Weather alert(s) are active for this area - check them before you leave.")
    if budget in BUDGET_NOTES:
        notes.append(BUDGET_NOTES[budget])

    pieces = "; ".join([top, bottom, footwear])
    comparison = compare_places(origin, dest) if dest else ""
    return TripAdvice(
        title=f"{style} {occasion.lower()} outfit" + (f" for {origin.place} → {dest.place}" if dest else f" for {origin.place}"),
        top=top, bottom=bottom, footwear=footwear, pieces=pieces,
        outfit_reason=" ".join(reasons),
        carry=carry, comparison=comparison, notes=notes,
        alternatives=[{k: apply_avoid(v, avoid) for k, v in alt.items()} for alt in base[1:]],
        assumptions=list(assumptions or []), alerts=list(allw.alerts),
    )
