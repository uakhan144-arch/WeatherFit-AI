"""Destination-aware chat assistant for WeatherFit AI.

Pipeline for each message
  1. Parse the text (origin, destination, time, duration, occasion, umbrella/jacket focus).
  2. Ask a follow-up ONLY if something essential is missing (no origin city, or travel with no destination).
  3. Fetch real forecasts from WeatherAPI for the relevant places/times (via WeatherClient).
  4. Build a *grounded* data dict + rule-based advice (stylist.py).
  5. If an LLM callable is supplied, let it phrase the answer from that data only; verify that every
     temperature / percentage it quotes exists in the data, otherwise use the deterministic answer.

No weather, forecast or location is ever invented: if a fetch fails the reply says so.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from stylist import TripAdvice, build_trip_advice, _range as fmt_range
from plan_service import build_plan
from trip import ForecastUnavailable, WindowSummary
from weather_service import WeatherError

LLM = Callable[[str, str], str]  # (prompt, system_instruction) -> text

# ----------------------------------------------------------------------------- parsing
_CITY = r"([a-z][a-z.'\-]*(?:\s+[a-z][a-z.'\-]*){0,2})"
_STOP = set("""a an the my our your his her their this that these those it its i me we you he she they and or but so
if then because please at by on in for with without after before around about next to from into onto via
today tomorrow tonight tmrw now later morning afternoon evening night noon midnight monday tuesday wednesday
thursday friday saturday sunday am pm hours hour hrs hr minutes minutes mins min
travel travelling traveling go going be get know decide see take carry bring pack dress wear wearing need needs
want find buy attend work college office school class university party wedding gym date dinner lunch breakfast
meeting interview temple market mall event function trip journey drive driving ride commute leave leaving
depart departing live stay umbrella jacket coat sweater sunglasses cap shoes outfit clothes weather rain
is are was were will would should can could do does what which how when where who whom there here""".split())

_RE_FROM_TO = re.compile(rf"\bfrom\s+{_CITY}\s+to\s+{_CITY}", re.I)
_RE_X_TO_Y = re.compile(rf"(?=\b{_CITY}\s+to\s+{_CITY})", re.I)  # lookahead: allow overlapping matches
_RE_ORIGIN = re.compile(
    rf"\b(?:i\s+live\s+in|i\s+stay\s+in|i\s+am\s+in|i'?m\s+in|currently\s+in|living\s+in|staying\s+in|based\s+in|"
    rf"leaving\s+from|starting\s+from|starting\s+in)\s+{_CITY}", re.I)
_RE_WEAR_IN = re.compile(rf"\b(?:wear|dress|pack|carry)\b[^?.!]*?\bin\s+{_CITY}", re.I)
_RE_TO = re.compile(rf"(?=\bto\s+{_CITY})", re.I)
_RE_VISIT = re.compile(rf"(?=\b(?:visit|visiting|towards|reach|reaching)\s+{_CITY})", re.I)

_RE_CLOCK12 = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)(?![a-z])", re.I)
_RE_CLOCK24 = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_RE_DEP_IN = re.compile(r"(?:leav\w*|depart\w*|start\w*|go|going|set(?:ting)?\s+off)\s+in\s+(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?)\b", re.I)
_RE_DUR = re.compile(
    r"(?:takes?|taking|for|about|approx\w*|around|duration(?:\s+of)?|is|of)\s+(?:about\s+|around\s+|roughly\s+)?(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?)\b"
    r"|(\d+(?:\.\d+)?)[\s-]*(hours?|hrs?|h|minutes?|mins?)[\s-]*(?:drive|journey|trip|ride|travel|commute)", re.I)

_OCCASIONS = [
    ("College", r"\b(college|class(?:es)?|university|campus|lecture)\b"),
    ("Office", r"\b(office|work|meeting|interview|workplace)\b"),
    ("Wedding", r"\bwedding|shaadi|reception\b"),
    ("Party", r"\b(party|club|clubbing|birthday|celebration)\b"),
    ("Date", r"\b(date|dinner date)\b"),
    ("Outdoor activity", r"\b(gym|hike|hiking|trek|trekking|run|running|cricket|football|outdoor|workout|picnic)\b"),
]
_TRAVEL_WORDS = re.compile(r"\b(travel\w*|trip|journey|commut\w*|drive|driving|road\s*trip|heading|visit\w*|going\s+to|flight|train|bus)\b", re.I)


@dataclass
class Intent:
    origin: str | None = None
    dest: str | None = None
    occasion: str | None = None
    occasions: list[str] = field(default_factory=list)
    depart: datetime | None = None
    duration_h: float | None = None
    travel: bool = False
    wants_umbrella: bool = False
    wants_jacket: bool = False
    wants_sun: bool = False
    time_assumption: str | None = None


def _clean_city(raw: str | None) -> str | None:
    if not raw:
        return None
    words: list[str] = []
    for tok in raw.strip().split():
        low = tok.lower().strip(".,'-")
        if low in _STOP or any(ch.isdigit() for ch in low) or not low:
            break
        words.append(tok.strip(".,"))
    return " ".join(w.capitalize() if w.islower() else w for w in words) or None


def _hours(value: str, unit: str) -> float:
    v = float(value)
    return v / 60 if unit.lower().startswith("m") else v


def parse_time(text: str, now: datetime) -> tuple[datetime | None, str | None]:
    m = _RE_DEP_IN.search(text)
    if m:
        return now + timedelta(hours=_hours(m.group(1), m.group(2))), None
    low = text.lower()
    day = 1 if re.search(r"\b(tomorrow|tmrw)\b", low) else 0
    explicit_day = day == 1 or bool(re.search(r"\b(today|tonight)\b", low))
    hour = minute = None
    c = _RE_CLOCK12.search(text)
    if c:
        hour, minute = int(c.group(1)), int(c.group(2) or 0)
        pm = c.group(3).lower().startswith("p")
        if hour > 12 or hour == 0:
            hour = None
        else:
            hour = (hour % 12) + (12 if pm else 0)
    if hour is None:
        c = _RE_CLOCK24.search(text)
        if c:
            hour, minute = int(c.group(1)), int(c.group(2))
    if hour is None:
        for word, h in (("morning", 8), ("afternoon", 14), ("evening", 18), ("tonight", 20), ("night", 20)):
            if re.search(rf"\b{word}\b", low):
                hour, minute = h, 0
                break
    if hour is None:
        if day:
            dt = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
            return dt, "Departure time not given, so I assumed 09:00 tomorrow."
        return None, None
    dt = (now + timedelta(days=day)).replace(hour=hour, minute=minute or 0, second=0, microsecond=0)
    note = None
    if not explicit_day and dt < now - timedelta(hours=1):
        dt += timedelta(days=1)
        note = f"{dt:%H:%M} has already passed today, so I used tomorrow."
    return dt, note


def parse_message(text: str, now: datetime | None = None) -> Intent:
    now = now or datetime.now()
    it = Intent()
    t = text.strip()
    it.travel = bool(_TRAVEL_WORDS.search(t))

    m = _RE_FROM_TO.search(t)
    if m:
        it.origin, it.dest = _clean_city(m.group(1)), _clean_city(m.group(2))
    if not (it.origin and it.dest):
        o = _RE_ORIGIN.search(t)
        if o and not it.origin:
            it.origin = _clean_city(o.group(1))
        for mm in _RE_X_TO_Y.finditer(t):
            a, b = _clean_city(mm.group(1)), _clean_city(mm.group(2))
            if a and b:
                it.origin, it.dest = it.origin or a, b
                break
    if not it.dest:
        for rx in (_RE_TO, _RE_VISIT):
            for mm in rx.finditer(t):
                c = _clean_city(mm.group(1))
                if c:
                    it.dest = c
                    break
            if it.dest:
                break
    if not it.origin and not it.dest:
        w = _RE_WEAR_IN.search(t)
        if w:
            it.origin = _clean_city(w.group(1))  # "what should I wear in Mumbai tomorrow" -> that place
    if it.dest:
        it.travel = True
    if it.origin and it.dest and it.origin.lower() == it.dest.lower():
        it.dest = None

    found = []
    for name, rx in _OCCASIONS:
        mo = re.search(rx, t, re.I)
        if mo:
            found.append((mo.start(), name))
    it.occasions = [n for _, n in sorted(found)]
    it.occasion = it.occasions[0] if it.occasions else None

    d = _RE_DUR.search(t)
    if d:
        it.duration_h = _hours(d.group(1) or d.group(3), d.group(2) or d.group(4))
    it.depart, it.time_assumption = parse_time(t, now)

    low = t.lower()
    it.wants_umbrella = bool(re.search(r"umbrella|rain|raincoat|wet|monsoon|drizzle", low))
    it.wants_jacket = bool(re.search(r"jacket|coat|sweater|hoodie|layer|cold|warm clothes|chilly", low))
    it.wants_sun = bool(re.search(r"sunglass|\bcap\b|sunscreen|\bsun\b|\bhat\b", low))
    return it


# ----------------------------------------------------------------------------- chat flow
@dataclass
class ChatReply:
    text: str
    ok: bool = True                    # False -> error (no weather data used)
    followup: bool = False             # True -> assistant asked a clarifying question
    used_llm: bool = False
    grounded: dict[str, Any] = field(default_factory=dict)
    advice: TripAdvice | None = None
    warnings: list[str] = field(default_factory=list)
    plan: Any = None                   # plan_service.Plan (forecast windows) when weather was fetched
    intent: Intent | None = None


def new_state() -> dict[str, Any]:
    return {"pending": None, "partial": None, "home_city": None}


def _short(name: str) -> str:
    return name.split(",")[0].strip()


def _summary_dict(w: WindowSummary) -> dict[str, Any]:
    return {"place": w.place, "from": w.start, "to": w.end, "feels_like_c": [round(w.feels_min), round(w.feels_max)],
            "temp_c": [round(w.temp_min), round(w.temp_max)], "rain_chance_max_pct": round(w.rain_chance_max),
            "precip_total_mm": round(w.precip_total_mm, 1), "humidity_avg_pct": round(w.humidity_avg),
            "wind_max_kph": round(w.wind_max_kph), "uv_max": round(w.uv_max), "conditions": w.conditions[:3],
            "forecast_covers_whole_window": not w.partial}


def allowed_numbers(grounded: dict[str, Any]) -> set[float]:
    nums: set[float] = set()

    def walk(v: Any) -> None:
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            nums.add(float(v))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    for key in ("origin", "destination", "current"):
        walk(grounded.get(key))
    return nums


def check_grounding(answer: str, grounded: dict[str, Any]) -> list[str]:
    """Return the temperatures/percentages quoted in ``answer`` that do not appear in the weather data."""
    allowed = allowed_numbers(grounded)
    bad: list[str] = []
    for m in re.finditer(r"(-?\d+(?:\.\d+)?)\s*(?:°\s*C?|degrees?(?:\s+celsius)?|%)", answer, re.I):
        val = float(m.group(1))
        if not any(abs(val - a) <= 1.0 for a in allowed):
            bad.append(m.group(0))
    return bad


def _fmt_window(label: str, w: WindowSummary) -> str:
    return (f"- **{label} ({_short(w.place)}, {w.start[11:]}–{w.end[11:]})**: feels like "
            f"{fmt_range(w.feels_min, w.feels_max)}, {w.main_condition.lower()}, rain chance up to {w.rain_chance_max:.0f}%, "
            f"wind up to {w.wind_max_kph:.0f} km/h, humidity about {w.humidity_avg:.0f}%.")


def template_answer(intent: Intent, origin: WindowSummary, dest: WindowSummary | None, advice: TripAdvice,
                    departure: datetime, duration_h: float | None) -> str:
    lines: list[str] = []
    umb, jac = advice.item("Umbrella"), advice.item("Jacket") or advice.item("Light jacket")
    if intent.wants_umbrella and umb:
        lines.append(f"**Umbrella: {'yes' if umb.needed else 'no'}.** {umb.reason}")
    if intent.wants_jacket and jac:
        lines.append(f"**{jac.item}: {'yes' if jac.needed else 'no'}.** {jac.reason}")
    if lines:
        lines.append("")
    if dest:
        lines.append(f"**{_short(origin.place)} → {_short(dest.place)}**, leaving {departure:%a %d %b %H:%M}"
                     + (f" (about {duration_h:g} h journey)." if duration_h else "."))
        lines += [_fmt_window("When you leave", origin), _fmt_window("When you arrive", dest), advice.comparison]
    else:
        lines.append(f"**{_short(origin.place)}**, from {departure:%a %d %b %H:%M}:")
        lines.append(_fmt_window("Forecast", origin))
    lines += ["", f"**Wear:** {advice.top}; {advice.bottom}", f"**Footwear:** {advice.footwear}"]
    needed = [c for c in advice.carry if c.needed]
    lines.append("**Carry / accessories:** " + (", ".join(c.item for c in needed) if needed else "nothing special needed"))
    lines.append(f"**Why:** {advice.outfit_reason}")
    for c in advice.carry:
        lines.append(f"- {c.item}: {'yes' if c.needed else 'no'} — {c.reason}")
    for n in advice.notes:
        lines.append(f"_{n}_")
    if advice.assumptions:
        lines.append("**Assumptions:** " + " ".join(advice.assumptions))
    return "\n".join(l for l in lines if l is not None)


SYSTEM_PROMPT = (
    "You are WeatherFit AI, a friendly personal weather-and-fashion assistant. Use ONLY the weather facts in the "
    "DATA json. Never invent or estimate temperatures, rain chances, times, places or alerts; if something is not in "
    "DATA, say you don't have it. Do not ask questions. Answer the user's actual question first (e.g. umbrella/jacket "
    "yes or no), then give outfit, footwear and accessories, and a short reason for each tied to a number in DATA. "
    "Mention assumptions listed in DATA. Respect the user's style, budget, colours and avoid-list. Be concise (under "
    "220 words), use short bullet points, and write temperatures in °C and rain chance in %."
)


def _llm_prompt(question: str, grounded: dict[str, Any], history: list[dict[str, str]] | None) -> str:
    hist = ""
    for m in (history or [])[-6:]:
        hist += f"{m['role'].upper()}: {m['content'][:400]}\n"
    return f"DATA:\n{json.dumps(grounded, ensure_ascii=False, default=str)}\n\nRECENT CHAT:\n{hist}\nUSER QUESTION: {question}"


def _extract_city_reply(text: str) -> str | None:
    t = re.sub(r"^(?:it'?s|i am|i'm|im|in|to|at|from|going to|travelling to|traveling to|the city is)\s+", "", text.strip(), flags=re.I)
    t = t.strip(" .!?")
    if not t or len(t.split()) > 4:
        return None
    return _clean_city(t.lower()) if not any(ch.isdigit() for ch in t) else None


def handle_message(
    text: str,
    state: dict[str, Any],
    profile: dict[str, str],
    location: Any,
    client: Any,
    planner: dict[str, Any] | None = None,
    llm: LLM | None = None,
    history: list[dict[str, str]] | None = None,
    now: datetime | None = None,
) -> ChatReply:
    """Produce one assistant reply. ``state`` is mutated (pending follow-up, remembered home city)."""
    pending, partial = state.get("pending"), state.get("partial")
    state["pending"] = state["partial"] = None
    intent = parse_message(text, now)

    # Answer to our own follow-up question?
    if pending and partial is not None:
        reply_city = _extract_city_reply(text) if not (intent.origin or intent.dest) else None
        if reply_city:
            if pending == "origin":
                partial.origin = reply_city
            else:
                partial.dest = reply_city
            partial.travel = partial.travel or pending == "destination"
            for attr in ("depart", "duration_h", "occasion"):
                if getattr(intent, attr) is not None:
                    setattr(partial, attr, getattr(intent, attr))
            intent = partial
        elif intent.origin or intent.dest:
            pass  # user restated the whole question
        else:
            intent = intent  # new, unrelated question

    if intent.origin and _RE_ORIGIN.search(text):
        state["home_city"] = intent.origin  # "I live in X" -> remember for later questions in this chat

    origin_q = intent.origin or (location.query if getattr(location, "usable", False) else None) or state.get("home_city")
    if not origin_q:
        state["pending"], state["partial"] = "origin", intent
        return ChatReply("Which city are you in? Use **Detect my location** in the sidebar, or just type the city name.",
                         followup=True)

    planner = planner or {}
    dest_q = intent.dest
    used_planner = False
    if not dest_q and intent.travel and planner.get("dest"):
        dest_q, used_planner = planner["dest"], True
    if not dest_q and intent.travel and not intent.dest:
        state["pending"], state["partial"] = "destination", intent
        return ChatReply("Where are you travelling to? If you know roughly how long the journey takes, tell me that too.",
                         followup=True)

    assumptions: list[str] = []
    if intent.time_assumption:
        assumptions.append(intent.time_assumption)
    planner_depart = planner.get("depart") if used_planner else None
    if intent.depart is None and not planner_depart:
        assumptions.append("No time given, so I used the current local time.")
    try:
        plan = build_plan(client, origin_q, dest_q, intent.depart or planner_depart, 
                          intent.duration_h or (planner.get("duration_h") if used_planner else None),
                          float(planner.get("stay_h", 3)) if planner else 3.0, now)
    except ForecastUnavailable as exc:
        return ChatReply(str(exc), ok=False)
    except WeatherError as exc:
        return ChatReply(f"I couldn't get the weather for {origin_q}: {exc}", ok=False)

    fc_o, origin_w, dest_w, depart, duration = plan.origin_fc, plan.origin_w, plan.dest_w, plan.depart, plan.duration_h
    now_local, warnings = plan.now_local, plan.warnings
    assumptions += plan.assumptions
    occasion = intent.occasion or profile.get("occasion") or ("Travel" if dest_q else "Daily casual")

    def advise(occ: str) -> TripAdvice:
        return build_trip_advice(origin_w, dest_w, occ, profile.get("style", "Casual"), profile.get("budget", "Mid-range"),
                                 profile.get("colours", ""), profile.get("avoid", ""), assumptions)

    advice = advise(occasion)
    extra = {o: advise(o) for o in intent.occasions[1:]} if len(intent.occasions) > 1 else {}
    grounded = {
        "now_local": f"{now_local:%Y-%m-%d %H:%M}", "occasion": occasion,
        "profile": {k: v for k, v in profile.items() if k in ("style", "personality", "budget", "colours", "avoid")},
        "origin": _summary_dict(origin_w), "destination": _summary_dict(dest_w) if dest_w else None,
        "current": {k: fc_o["current"][k] for k in ("city", "temp_c", "feelslike_c", "humidity", "wind_kph", "condition")},
        "journey_hours": duration, "departure": f"{depart:%Y-%m-%d %H:%M}",
        "advice": advice.to_dict(), "advice_other_occasions": {o: a.to_dict() for o, a in extra.items()},
        "assumptions": assumptions,
        "alerts": [{"event": a.get("event", ""), "severity": a.get("severity", ""), "headline": a.get("headline", "")} for a in advice.alerts[:3]],
    }
    text_out = template_answer(intent, origin_w, dest_w, advice, depart, duration)
    if extra:
        text_out += "\n\n**Other occasions you asked about:**\n" + "\n".join(
            f"- **{o}:** {a.top}; {a.bottom}; footwear: {a.footwear}" for o, a in extra.items())
    reply = ChatReply(text_out, grounded=grounded, advice=advice, warnings=warnings, plan=plan, intent=intent)
    if llm:
        try:
            answer = llm(_llm_prompt(text, grounded, history), SYSTEM_PROMPT).strip()
            bad = check_grounding(answer, grounded)
            if not answer:
                raise ValueError("empty answer")
            if bad:
                reply.warnings.append("The AI's wording quoted numbers that aren't in the weather data, so the data-based answer is shown instead.")
            else:
                reply.text, reply.used_llm = answer, True
        except Exception as exc:  # LLM is optional; the grounded answer stands
            reply.warnings.append(f"AI wording unavailable ({type(exc).__name__}); showing the data-based answer.")
    return reply
