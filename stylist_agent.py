"""WeatherFit stylist agent: answers English / Hindi / Hinglish outfit questions from REAL data.

Data sources (nothing else is allowed to enter an answer):
  * weather      - WeatherAPI forecasts via ``chat_assistant.handle_message`` / ``plan_service`` (never invented),
  * wardrobe     - rows in the SQL ``clothing_items`` table (ownership='wardrobe'),
  * history      - rows in ``outfits`` (what was recommended / worn, e.g. yesterday),
  * preferences  - the sidebar profile (gender presentation, style, colours, budget, comfort, occasion).

A deterministic answer is always built first. Gemini (if configured) only *rephrases* it in the user's language; its text is
rejected when it quotes numbers that are not in the weather data or claims you own an inspiration item. Failures (missing key,
quota, timeout) are retried where sensible, then reported clearly - the deterministic answer is still shown.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import hinglish
import outfit_engine as engine
import outfit_history as history
import taxonomy as tx
from chat_assistant import ChatReply, _llm_prompt, _summary_dict, check_grounding, handle_message
from db import Database
from gemini_guard import GeminiUnavailable
from outfit_models import INSPIRATION_LABEL, WARDROBE_LABEL, Recommendation, RecommendationResult, WeatherNeeds
from security import redact
from wardrobe import list_items

LLM = Callable[[str, str], str]

AGENT_SYSTEM_PROMPT = (
    "You are WeatherFit AI, a friendly personal stylist. Reply in the SAME language and script style as the user's latest question "
    "(English, Hindi, or Hinglish in Roman letters). Use ONLY the facts in DATA. Never invent weather, temperatures, places, clothing items, "
    "colours or history. Keep two clearly separate sections with these headings: 'From Your Wardrobe' (only items listed under "
    "from_your_wardrobe - these are owned) and 'New Outfit Inspiration' (items listed under new_outfit_inspiration - NEVER say the user owns "
    "them; say they are suggestions). If wardrobe_status is empty/insufficient/limited, say so honestly and explain why using wardrobe_messages. "
    "If an item is reused, mention the reuse_notes. For yesterday questions use yesterday_outfit; if it is null say you have no record. "
    "Give exact colours and categories for clothes, footwear and accessories and a short reason tied to the weather numbers in DATA. "
    "Under 250 words, short bullets, temperatures in °C and rain chance in %.")


# ------------------------------------------------------------------ LLM errors & retry
class LLMUnavailable(RuntimeError):
    def __init__(self, kind: str, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.kind, self.message, self.retryable = kind, message, retryable


def classify_llm_error(exc: Exception) -> LLMUnavailable:
    if isinstance(exc, GeminiUnavailable):   # already classified (and already retried where sensible) by gemini_guard
        kind = {"disabled": "disabled", "no_key": "no_key", "cooldown": "quota", "local_limit": "quota", "sdk_missing": "no_key"}.get(exc.kind, exc.kind)
        return LLMUnavailable(kind, exc.message, exc.retryable and kind not in ("disabled", "no_key"))
    text = redact(exc)
    low = text.lower()
    if "add gemini_api_key" in low or "gemini_api_key" in low and "add" in low:
        return LLMUnavailable("no_key", "Live AI is off: add GEMINI_API_KEY to your local .env file.", False)
    if any(k in low for k in ("429", "quota", "resource_exhausted", "rate limit", "rate-limit")):
        return LLMUnavailable("quota", "Gemini is rate-limited or out of quota right now. Try again in a minute.", True)
    if any(k in low for k in ("401", "403", "api key not valid", "api_key_invalid", "permission", "unauthenticated")):
        return LLMUnavailable("auth", "Gemini rejected the API key or this model isn't available to it. Check GEMINI_API_KEY and model access in .env.", False)
    if any(k in low for k in ("timeout", "timed out", "503", "500", "502", "504", "unavailable", "overloaded", "connection", "network")):
        return LLMUnavailable("transient", "Gemini didn't respond in time (temporary problem).", True)
    return LLMUnavailable("error", f"Gemini returned an error ({text[:120]}).", True)


def call_llm_with_retry(llm: LLM, prompt: str, system: str, retries: int = 2, base_delay: float = 1.0,
                        sleep: Callable[[float], None] = time.sleep) -> str:
    """Call ``llm`` and retry only temporary failures (timeouts / 5xx) with exponential backoff. Raises LLMUnavailable."""
    last: LLMUnavailable | None = None
    for attempt in range(retries + 1):
        try:
            out = llm(prompt, system)
            if not out or not str(out).strip():
                raise RuntimeError("Gemini returned an empty answer.")
            return str(out).strip()
        except Exception as exc:  # noqa: BLE001 - classified below
            last = classify_llm_error(exc)
            # gemini_guard has already retried temporary errors, so never stack a second round of retries on top of it
            if last.kind != "transient" or attempt == retries or isinstance(exc, GeminiUnavailable):
                break
            sleep(base_delay * (2 ** attempt))
    assert last is not None
    raise last


# ------------------------------------------------------------------ context / reply
@dataclass
class AgentContext:
    db: Database
    profile: dict[str, Any]
    location: Any
    client: Any
    state: dict[str, Any]
    planner: dict[str, Any] | None = None
    llm: LLM | None = None
    history: list[dict[str, str]] = field(default_factory=list)
    today: date | None = None
    now: datetime | None = None
    repeat_days: int = 7
    data_dir: Path | None = None
    sleep: Callable[[float], None] = time.sleep


@dataclass
class AgentReply:
    text: str
    ok: bool = True
    followup: bool = False
    language: str = "en"
    intent: str = "other"
    used_llm: bool = False
    llm_status: str = "not_used"           # live | no_key | quota | auth | transient | error | rejected | not_used
    llm_message: str = ""
    retryable: bool = False
    result: RecommendationResult | None = None
    pair_recs: list[Recommendation] = field(default_factory=list)
    needs: WeatherNeeds | None = None
    mode: str = "none"                     # both | inspiration | match | none
    outfit_ids: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    grounded: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------ deterministic text
H = {
    "en": {"weather": "Weather", "wardrobe": f"{WARDROBE_LABEL} (clothes you own)", "insp": f"{INSPIRATION_LABEL} (AI suggestion - you may not own these)",
           "why": "Why it works", "also": "Also carry", "yesterday": "Yesterday you had", "no_yesterday": "I don't have a saved outfit for yesterday, so I can't say what you wore.",
           "pairs": "Matches from your wardrobe", "new_look": "You asked for a new look, so here is inspiration (not from your wardrobe).",
           "different": "Today's pick is different from yesterday's."},
    "hinglish": {"weather": "Mausam", "wardrobe": f"Aapki Wardrobe Se ({WARDROBE_LABEL})", "insp": f"Naya Outfit Idea ({INSPIRATION_LABEL} - ho sakta hai aapke paas na ho)",
                 "why": "Kyun kaam karega", "also": "Saath rakhein", "yesterday": "Kal aapne ye pehna tha", "no_yesterday": "Kal ka koi saved outfit mere paas nahi hai, isliye main nahi bata sakta ki aapne kya pehna tha.",
                 "pairs": "Aapki wardrobe se match", "new_look": "Aapne naya look maanga tha, isliye ye inspiration hai (aapki wardrobe se nahi).",
                 "different": "Aaj ka outfit kal se alag hai."},
}


def _lang_key(language: str) -> str:
    return "en" if language == "en" else "hinglish"


def _piece_line(pc) -> str:
    return f"{pc.role.title() if pc.role != 'onepiece' else 'One-piece'}: **{pc.label}**"


def _weather_line(plan) -> str:
    o, d = plan.origin_w, plan.dest_w

    def one(w) -> str:
        feels = f"{w.feels_min:.0f}°C" if round(w.feels_min) == round(w.feels_max) else f"{w.feels_min:.0f}–{w.feels_max:.0f}°C"
        return f"{w.place.split(',')[0]} ({w.start[11:]}–{w.end[11:]}): feels like {feels}, {w.main_condition.lower()}, rain chance up to {w.rain_chance_max:.0f}%"
    return one(o) + (f" → {one(d)}" if d else "")


def _rec_block(rec: Recommendation, lang: dict[str, str], heading: str) -> list[str]:
    lines = [f"**{heading}**"] + [f"- {_piece_line(pc)}" for pc in rec.pieces]
    if rec.accessories:
        lines.append("- Accessories: " + ", ".join(f"**{a.label}**" for a in rec.accessories))
    if rec.why:
        lines.append(f"_{lang['why']}:_")
        lines += [f"  - {w}" for w in rec.why]
    lines += [f"- ♻️ {n}" for n in rec.reuse_notes]
    lines += [f"- ⚠️ {m}" for m in rec.missing]
    lines += [f"- ⚠️ {w}" for w in rec.warnings]
    return lines


def compose_text(parsed: hinglish.Parsed, plan, result: RecommendationResult | None, mode: str, intent_prefix: list[str]) -> str:
    lang = H[_lang_key(parsed.language)]
    lines = list(intent_prefix)
    if plan is not None:
        lines += ["", f"**{lang['weather']}:** {_weather_line(plan)}"]
    if result is not None:
        if mode != "inspiration":
            lines.append("")
            if result.wardrobe:
                lines += _rec_block(result.wardrobe[0], lang, lang["wardrobe"])
                if len(result.wardrobe) > 1:
                    lines.append("- Other wardrobe options: " + " | ".join(", ".join(p.label for p in r.pieces) for r in result.wardrobe[1:]))
            else:
                lines.append(f"**{lang['wardrobe']}**")
            lines += [f"- ℹ️ {m}" for m in result.messages]
        if result.inspiration is not None:
            lines.append("")
            lines += _rec_block(result.inspiration, lang, lang["insp"])
    return "\n".join(l for l in lines if l is not None).strip()


def _match_owned(items: list[dict[str, Any]], colour: str | None, category: str | None) -> list[dict[str, Any]]:
    """Wardrobe items matching the described piece: exact colour first, then same colour family (e.g. 'blue' ~ 'light blue')."""
    pool = [it for it in items if not category or it["category"] == category]
    if not colour:
        return pool
    c = tx.normalize_colour(colour)
    exact = [it for it in pool if it["colour"] == c]
    return exact or [it for it in pool if tx.colour_family(it["colour"]) == tx.colour_family(c)]


GENERAL_PAIRINGS = {
    "top": "trousers or chinos in navy, beige, grey, black or white, with white or brown shoes",
    "bottom": "a top in white, light blue, grey or a soft contrasting colour, with matching shoes",
    "outer": "a plain top in white or grey with dark trousers or jeans",
    "onepiece": "neutral shoes (white, tan or black) and a light layer if it's cool",
    "footwear": "neutral bottoms (navy, beige, black) and a plain top",
}


# ------------------------------------------------------------------ main entry point
def respond(text: str, ctx: AgentContext) -> AgentReply:
    today = ctx.today or (ctx.now.date() if ctx.now else date.today())
    parsed = hinglish.parse(text)
    st = ctx.state
    st.setdefault("variant", 0)
    if parsed.wants_different or (st.get("last_question") == text.strip().lower()):
        st["variant"] += 1
    st["last_question"] = text.strip().lower()
    reply = AgentReply("", language=parsed.language, intent=parsed.intent)
    prefix: list[str] = []
    lang = H[_lang_key(parsed.language)]

    # ---- yesterday
    if parsed.intent == "yesterday":
        y = engine.yesterday_summary(ctx.db, today)
        prefix.append(f"**{lang['yesterday']}:** " + ", ".join(y["items"]) + f" ({y['status']})." if y else lang["no_yesterday"])
        if not parsed.wants_different and not re.search(r"\b(today|aaj|suggest|recommend)\b", text.lower()):
            reply.text, reply.mode = "\n".join(prefix), "none"
            reply.grounded = {"yesterday_outfit": y}
            return _maybe_llm(reply, text, parsed, ctx, None, None)

    # ---- pairing question (no weather needed)
    if parsed.intent == "match_item":
        return _match_item(text, parsed, ctx, reply, today)

    # ---- everything else needs weather -> reuse the existing, tested weather/trip pipeline
    english = parsed.english if parsed.language != "en" else text
    weather_reply: ChatReply = handle_message(english, ctx.state, ctx.profile, ctx.location, ctx.client, planner=ctx.planner,
                                              llm=None, history=ctx.history, now=ctx.now)
    if weather_reply.followup or not weather_reply.ok or weather_reply.plan is None:
        reply.text = "\n".join(prefix + ([""] if prefix else [])) + hinglish.localise_followup(weather_reply.text, parsed.language)
        reply.ok, reply.followup, reply.warnings = weather_reply.ok, weather_reply.followup, weather_reply.warnings
        return reply
    if parsed.intent == "other":  # umbrella / jacket / weather-only questions keep the original behaviour
        reply.text, reply.grounded, reply.warnings = weather_reply.text, weather_reply.grounded, weather_reply.warnings
        reply.needs = None
        return _maybe_llm(reply, text, parsed, ctx, None, weather_reply, weather_only=True)

    plan = weather_reply.plan
    needs = engine.needs_from_windows(plan.origin_w, plan.dest_w)
    occasion = (weather_reply.intent.occasion if weather_reply.intent and weather_reply.intent.occasion else None) or ctx.profile.get("occasion") or "Daily casual"
    profile = {**ctx.profile, "occasion": occasion}
    result = engine.recommend(ctx.db, needs, profile, today, repeat_days=ctx.repeat_days, variant=st["variant"])
    reply.needs, reply.result, reply.warnings = needs, result, list(weather_reply.warnings)
    mode = "inspiration" if parsed.intent == "new_look" else "both"
    reply.mode = mode
    if parsed.intent == "new_look":
        prefix.append(lang["new_look"])
    elif parsed.intent == "yesterday" and result.wardrobe:
        prefix.append(lang["different"])
    _record(ctx, reply, result, needs, occasion, today, mode)
    reply.text = compose_text(parsed, plan, result, mode, prefix)
    reply.grounded = _grounded(plan, weather_reply, result, profile, mode)
    return _maybe_llm(reply, text, parsed, ctx, plan, weather_reply)


def _record(ctx: AgentContext, reply: AgentReply, result: RecommendationResult, needs: WeatherNeeds, occasion: str, today: date, mode: str) -> None:
    """Store the primary recommendation(s) so tomorrow's picks can avoid them. Alternatives are stored only if the user chooses them."""
    try:
        if mode != "inspiration" and result.wardrobe:
            reply.outfit_ids["wardrobe"], _ = history.save_recommendation(ctx.db, result.wardrobe[0], today, occasion, needs, "chat")
        if result.inspiration is not None:
            reply.outfit_ids["inspiration"], _ = history.save_recommendation(ctx.db, result.inspiration, today, occasion, needs, "chat")
    except Exception as exc:  # history must never break the chat
        reply.warnings.append(f"I couldn't save this suggestion to your outfit history ({type(exc).__name__}).")


def _rec_data(rec: Recommendation) -> dict[str, Any]:
    pieces = lambda ps: [{"id": p.item_id, "role": p.role, "category": p.category, "colour": p.colour, "name": p.label, "owned": p.owned} for p in ps]
    return {"items": pieces(rec.pieces), "accessories": pieces(rec.accessories), "why": rec.why, "reuse_notes": rec.reuse_notes,
            "not_in_wardrobe": rec.missing, "warnings": rec.warnings}


def _grounded(plan, weather_reply: ChatReply, result: RecommendationResult | None, profile: dict[str, Any], mode: str) -> dict[str, Any]:
    g = {k: weather_reply.grounded.get(k) for k in ("now_local", "origin", "destination", "current", "journey_hours", "departure", "assumptions", "alerts")}
    g["profile"] = {k: profile.get(k) for k in ("gender", "style", "colours", "budget", "occasion", "comfort", "avoid") if profile.get(k)}
    if result is not None:
        g["wardrobe_status"] = result.status
        g["wardrobe_messages"] = result.messages
        g["from_your_wardrobe"] = [] if mode == "inspiration" else [_rec_data(r) for r in result.wardrobe]
        g["yesterday_outfit"] = result.yesterday
        g["new_outfit_inspiration"] = _rec_data(result.inspiration) if result.inspiration else None
    return g


# ------------------------------------------------------------------ pairing ("what goes with my blue shirt?")
def _match_item(text: str, parsed: hinglish.Parsed, ctx: AgentContext, reply: AgentReply, today: date) -> AgentReply:
    lang = H[_lang_key(parsed.language)]
    owned = _match_owned(list_items(ctx.db, "wardrobe"), parsed.item_colour, parsed.item_category)
    what = " ".join(x for x in (parsed.item_colour, parsed.item_category) if x) or "item"
    reply.mode = "match"
    if not owned:
        slot = tx.slot_of(parsed.item_category) if parsed.item_category in tx.CATEGORIES else "top"
        reply.text = (f"I couldn't find a **{what}** in your wardrobe, so I can't build outfits from it. Add its photo in the Wardrobe tab.\n\n"
                      f"**{INSPIRATION_LABEL} (general idea, not from your wardrobe):** a {what} usually goes well with {GENERAL_PAIRINGS.get(slot, GENERAL_PAIRINGS['top'])}.")
        if parsed.language != "en":
            reply.text = (f"Mujhe aapki wardrobe mein **{what}** nahi mila, isliye usse outfit nahi bana sakta. Wardrobe tab mein uski photo add karein.\n\n"
                          f"**{lang['insp']}:** {what} ke saath aam taur par {GENERAL_PAIRINGS.get(slot, GENERAL_PAIRINGS['top'])} achha lagta hai.")
        reply.grounded = {"owned_matches": [], "searched_for": what}
        return _maybe_llm(reply, text, parsed, ctx, None, None)
    anchor = owned[0]
    recs, notes = engine.recommend_with_item(ctx.db, anchor["id"], None, ctx.profile, today, repeat_days=ctx.repeat_days)
    reply.pair_recs = recs
    lines = [f"**{lang['pairs']} - {what}** ({len(owned)} found in your wardrobe)"] if parsed.language == "en" else [f"**{lang['pairs']} - {what}** (wardrobe mein {len(owned)} mile)"]
    for i, r in enumerate(recs, 1):
        lines.append(f"{i}. " + " + ".join(f"**{p.label}**" for p in r.pieces) + (" | " + ", ".join(a.label for a in r.accessories) if r.accessories else ""))
        lines += [f"   - {w}" for w in r.why[1:3]]
    lines += [f"- ℹ️ {n}" for n in notes]
    reply.text = "\n".join(lines)
    reply.grounded = {"owned_matches": [{"id": it["id"], "name": it["name"] or f"{it['colour']} {it['category']}"} for it in owned],
                      "from_your_wardrobe": [_rec_data(r) for r in recs], "notes": notes}
    return _maybe_llm(reply, text, parsed, ctx, None, None)


# ------------------------------------------------------------------ optional Gemini rephrasing (with guards)
def check_ownership_claims(answer: str, result: RecommendationResult | None) -> list[str]:
    """Phrases where the answer says the user owns an inspiration item (e.g. 'your white sneakers')."""
    if result is None or result.inspiration is None:
        return []
    owned_labels = {f"{p.colour} {p.category}".strip() for r in result.wardrobe for p in r.all_pieces}
    bad = []
    for pc in result.inspiration.all_pieces:
        label = f"{pc.colour} {pc.category}".strip()
        if label in owned_labels:
            continue
        if re.search(rf"\b(your|aapki|aapka|aapke|tumhari|tumhara)\s+{re.escape(label)}\b", answer, re.I):
            bad.append(label)
    return bad


def _maybe_llm(reply: AgentReply, question: str, parsed: hinglish.Parsed, ctx: AgentContext, plan, weather_reply: ChatReply | None,
               weather_only: bool = False) -> AgentReply:
    if ctx.llm is None:
        reply.llm_status = "no_key"
        reply.llm_message = "Live AI is unavailable (no GEMINI_API_KEY, or ENABLE_GEMINI=false), so this answer was built directly from your weather, wardrobe and history data."
        return reply
    data = reply.grounded or (weather_reply.grounded if weather_reply else {})
    try:
        answer = call_llm_with_retry(ctx.llm, _llm_prompt(question, data, ctx.history), AGENT_SYSTEM_PROMPT, sleep=ctx.sleep)
    except LLMUnavailable as exc:
        reply.llm_status, reply.llm_message, reply.retryable = exc.kind, exc.message + " Showing the answer built from your data instead.", exc.retryable
        return reply
    bad_numbers = check_grounding(answer, data) if data.get("origin") or data.get("current") else []
    bad_claims = check_ownership_claims(answer, reply.result)
    if bad_numbers or bad_claims:
        reply.llm_status = "rejected"
        reply.llm_message = "The AI's wording contained details that aren't in your data, so I'm showing the data-based answer instead."
        reply.retryable = True
        return reply
    reply.text, reply.used_llm, reply.llm_status = answer, True, "live"
    return reply
