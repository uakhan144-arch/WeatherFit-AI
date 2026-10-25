"""Protects your Gemini quota. Every Gemini call in this project goes through ``run_guarded``.

What it does (no Streamlit and no Gemini SDK needed, so it is easy to unit-test):

* ENABLE_GEMINI=false (or a missing key) -> raises ``GeminiUnavailable`` immediately: no network call at all.
* Identical text requests are answered from an in-memory cache (GEMINI_CACHE_MINUTES, default 30) - no API call.
* A quota / rate-limit error (HTTP 429, RESOURCE_EXHAUSTED) is NEVER retried. Gemini is paused for a cool-down
  (GEMINI_QUOTA_COOLDOWN_MINUTES, default 15; longer if Google says so) and callers fall back to rule-based answers.
* Only temporary errors (timeouts, 500/502/503/504) are retried, at most GEMINI_MAX_RETRIES times (default 2),
  with exponential back-off (1 s, 2 s ... plus a little random jitter).
* A burst limiter (GEMINI_MAX_CALLS_PER_MINUTE, default 10) and an optional daily cap (GEMINI_MAX_CALLS_PER_DAY, default off)
  protect against accidental loops.
* "Model not found" errors try the next model in the configured list (see config.model_chain) - this does not use quota.
  The model that worked is remembered for the rest of the session.

Counters live in memory: restarting the app resets them. The real quota is counted by Google, not by this module.
"""
from __future__ import annotations

import hashlib
import random
import re
import threading
import time
from collections import OrderedDict, deque
from typing import Any, Callable

import config
from security import redact

CLOCK: Callable[[], float] = time.time      # tests replace these
SLEEP: Callable[[float], None] = time.sleep
_MAX_CACHE = 128
_MAX_COOLDOWN_S = 6 * 3600


class GeminiUnavailable(RuntimeError):
    """Gemini can't be used right now. ``kind`` is one of: disabled, no_key, sdk_missing, quota, cooldown, local_limit,
    auth, model, transient, error. The message is safe to show in the UI (no keys, no URLs)."""

    def __init__(self, kind: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.kind, self.message, self.retryable = kind, message, retryable


_lock = threading.RLock()
_cache: "OrderedDict[str, tuple[float, str]]" = OrderedDict()
_cooldown_until: dict[str, float] = {}
_recent_calls: deque = deque()
_day = {"key": "", "count": 0}
_working_model: dict[str, str] = {}


# ------------------------------------------------------------------ error classification
_RE_RETRY_IN = re.compile(r"retry in ([\d.]+)\s*s", re.I)
_RE_RETRY_DELAY = re.compile(r"retryDelay['\"]?\s*[:=]\s*['\"]?([\d.]+)s", re.I)


def classify_exception(exc: BaseException) -> tuple[str, float | None, bool]:
    """-> (kind, retry_after_seconds_if_given, looks_like_daily_limit). kind: quota|model|auth|transient|error."""
    text = redact(exc)
    low = text.lower()
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    try:
        code = int(code) if code is not None else None
    except (TypeError, ValueError):
        code = None
    status = str(getattr(exc, "status", "") or "").upper()
    name = type(exc).__name__.lower()

    if code == 429 or "RESOURCE_EXHAUSTED" in status or "resource_exhausted" in low or re.search(r"\b429\b", low) \
            or "quota" in low or "rate limit" in low or "rate-limit" in low:
        m = _RE_RETRY_IN.search(text) or _RE_RETRY_DELAY.search(text)
        daily = any(k in low for k in ("perday", "per day", "daily", "requests_per_day"))
        return "quota", (float(m.group(1)) if m else None), daily
    if code == 404 or status == "NOT_FOUND" or "not found for api version" in low or "no longer available" in low \
            or ("model" in low and "not found" in low) or "not supported for generatecontent" in low:
        return "model", None, False
    if code in (401, 403) or status in ("PERMISSION_DENIED", "UNAUTHENTICATED") or "api key not valid" in low \
            or "api_key_invalid" in low or "permission_denied" in low or "unauthenticated" in low:
        return "auth", None, False
    if code in (500, 502, 503, 504) or status in ("UNAVAILABLE", "DEADLINE_EXCEEDED", "INTERNAL") or "timeout" in name \
            or "connect" in name or any(k in low for k in ("timed out", "timeout", "overloaded", "temporarily", "connection reset",
                                                            "connection error", "service unavailable", "network")):
        return "transient", None, False
    return "error", None, False


# ------------------------------------------------------------------ cache
def _cache_key(kind: str, parts: tuple[Any, ...]) -> str:
    h = hashlib.sha256(kind.encode())
    for p in parts:
        h.update(b"\x00")
        h.update(p if isinstance(p, bytes) else str(p).encode("utf-8", "replace"))
    return h.hexdigest()


def text_cache_key(prompt: str, system: str | None = None, image: bytes | None = None, mime: str = "") -> str:
    return _cache_key("text", (prompt, system or "", mime, hashlib.sha256(image).hexdigest() if image else ""))


def cache_get(key: str) -> str | None:
    ttl = config.get_float("GEMINI_CACHE_MINUTES", 30.0) * 60
    if ttl <= 0:
        return None
    with _lock:
        hit = _cache.get(key)
        if hit and CLOCK() < hit[0]:
            _cache.move_to_end(key)
            return hit[1]
        _cache.pop(key, None)
    return None


def cache_put(key: str, text: str) -> None:
    ttl = config.get_float("GEMINI_CACHE_MINUTES", 30.0) * 60
    if ttl <= 0 or not text:
        return
    with _lock:
        _cache[key] = (CLOCK() + ttl, text)
        _cache.move_to_end(key)
        while len(_cache) > _MAX_CACHE:
            _cache.popitem(last=False)


def clear_cache() -> None:
    with _lock:
        _cache.clear()


# ------------------------------------------------------------------ cool-down / limits
def cooldown_remaining(kind: str = "text") -> float:
    with _lock:
        return max(0.0, _cooldown_until.get(kind, 0.0) - CLOCK())


def reset_cooldown() -> None:
    with _lock:
        _cooldown_until.clear()


def _start_cooldown(kind: str, retry_after: float | None, daily: bool) -> float:
    base = config.get_float("GEMINI_QUOTA_COOLDOWN_MINUTES", 15.0, minimum=1.0) * 60
    secs = max(base, retry_after or 0.0, 3600.0 if daily else 0.0)
    secs = min(secs, _MAX_COOLDOWN_S)
    with _lock:
        _cooldown_until[kind] = CLOCK() + secs
    return secs


def _check_limits(now: float) -> None:
    per_min = config.get_int("GEMINI_MAX_CALLS_PER_MINUTE", 10)
    per_day = config.get_int("GEMINI_MAX_CALLS_PER_DAY", 0)
    with _lock:
        while _recent_calls and now - _recent_calls[0] > 60:
            _recent_calls.popleft()
        day_key = time.strftime("%Y-%m-%d", time.localtime(now))
        if _day["key"] != day_key:
            _day["key"], _day["count"] = day_key, 0
        if per_min and len(_recent_calls) >= per_min:
            raise GeminiUnavailable("local_limit", f"That's more than {per_min} Gemini requests in a minute, so I'm holding back to protect "
                                                    "your quota. Using built-in rules; try again in a minute.", True)
        if per_day and _day["count"] >= per_day:
            raise GeminiUnavailable("local_limit", f"This app has already made {per_day} Gemini requests today (GEMINI_MAX_CALLS_PER_DAY), "
                                                    "so Gemini is paused until tomorrow or until you restart the app.", False)


def _record_call(now: float) -> None:
    with _lock:
        _recent_calls.append(now)
        _day["count"] += 1


# ------------------------------------------------------------------ main entry point
def _fmt_minutes(secs: float) -> str:
    m = int(secs // 60) + (1 if secs % 60 else 0)
    return f"{m} min" if m > 1 else "about a minute"


def _message_for(kind: str, tried: list[str], exc: BaseException | None, secs: float = 0.0, what: str = "text") -> str:
    var = "GEMINI_IMAGE_MODEL" if what == "image" else "GEMINI_TEXT_MODEL"
    if kind == "quota":
        return (f"Gemini's quota or rate limit was reached. I've paused Gemini for {_fmt_minutes(secs)} so no more quota is used; "
                "answers use the built-in weather rules meanwhile.")
    if kind == "auth":
        return f"Gemini rejected the API key, or your key can't use the selected model. Check GEMINI_API_KEY and {var} in your .env file."
    if kind == "model":
        return (f"None of the Gemini models I tried ({', '.join(tried) or 'none'}) is available to your key. Set {var} in your .env file to a model "
                "you can use (Voice Assistant tab -> Gemini status -> Check which models my key can use).")
    if kind == "transient":
        return "Gemini didn't respond in time (temporary problem)."
    return f"Gemini returned an error ({redact(exc)[:120] if exc else 'unknown'})."


def run_guarded(kind: str, call_model: Callable[[str], Any], cache_key: str | None = None, max_retries: int | None = None) -> tuple[Any, dict[str, Any]]:
    """Run ``call_model(model_name)`` under all the protections above.

    ``kind`` is "text" or "image" (separate cool-downs, separate model lists). Returns (result, info) where info has
    ``cached`` (bool) and ``model``. Raises ``GeminiUnavailable`` - never any other Gemini/SDK exception."""
    if not config.gemini_enabled():
        raise GeminiUnavailable("disabled", "Gemini is turned off (ENABLE_GEMINI=false), so built-in rules are used.")
    if not config.get_str("GEMINI_API_KEY"):
        raise GeminiUnavailable("no_key", "Gemini is off: add GEMINI_API_KEY to your local .env file (or Streamlit secrets).")
    if cache_key:
        hit = cache_get(cache_key)
        if hit is not None:
            return hit, {"cached": True, "model": _working_model.get(kind, "")}
    remaining = cooldown_remaining(kind)
    if remaining > 0:
        raise GeminiUnavailable("cooldown", f"Gemini is paused for about {_fmt_minutes(remaining)} after a quota error, to protect your quota. "
                                            "Built-in rules are used meanwhile.", False)

    retries = config.get_int("GEMINI_MAX_RETRIES", 2) if max_retries is None else max(0, max_retries)
    chain = config.model_chain(kind)
    known = _working_model.get(kind)
    models = ([known] + [m for m in chain if m != known]) if known and known in chain else chain
    tried: list[str] = []
    last_exc: BaseException | None = None
    for model in models:
        tried.append(model)
        attempt = 0
        while True:
            _check_limits(CLOCK())
            _record_call(CLOCK())
            try:
                result = call_model(model)
            except GeminiUnavailable:
                raise
            except Exception as exc:   # noqa: BLE001 - classified below; only GeminiUnavailable leaves this function
                last_exc = exc
                err, retry_after, daily = classify_exception(exc)
                if err == "quota":
                    secs = _start_cooldown(kind, retry_after, daily)
                    raise GeminiUnavailable("quota", _message_for("quota", tried, exc, secs, kind), False) from None
                if err == "model":
                    break   # try the next model (no quota is used by a "not found" error)
                if err == "transient" and attempt < retries:
                    SLEEP(min(8.0, 1.0 * (2 ** attempt)) + random.uniform(0, 0.5))
                    attempt += 1
                    continue
                raise GeminiUnavailable(err, _message_for(err, tried, exc, 0.0, kind), err == "transient") from None
            _working_model[kind] = model
            if cache_key and isinstance(result, str):
                cache_put(cache_key, result)
            return result, {"cached": False, "model": model}
    raise GeminiUnavailable("model", _message_for("model", tried, last_exc, 0.0, kind), False)


# ------------------------------------------------------------------ status for the UI
def status() -> dict[str, Any]:
    now = CLOCK()
    with _lock:
        calls_min = sum(1 for t in _recent_calls if now - t <= 60)
        return {
            "enabled": config.gemini_enabled(), "has_key": bool(config.get_str("GEMINI_API_KEY")),
            "off_reason": config.gemini_off_reason(),
            "cooldown_text_s": max(0.0, _cooldown_until.get("text", 0.0) - now),
            "cooldown_image_s": max(0.0, _cooldown_until.get("image", 0.0) - now),
            "calls_last_minute": calls_min, "calls_today": _day["count"], "cache_entries": len(_cache),
            "text_models": config.model_chain("text"), "image_models": config.model_chain("image"),
            "text_model_in_use": _working_model.get("text", ""), "image_model_in_use": _working_model.get("image", ""),
        }


def reset_all_for_tests() -> None:
    with _lock:
        _cache.clear()
        _cooldown_until.clear()
        _recent_calls.clear()
        _day.update({"key": "", "count": 0})
        _working_model.clear()
