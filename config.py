"""Central place for settings. Reads environment variables / .env first, then Streamlit secrets.

Nothing here ever prints or returns a secret except ``gemini_api_key()`` (used only to talk to Gemini).

Settings (all optional):
  GEMINI_API_KEY              your own key (never hard-coded anywhere in this project)
  ENABLE_GEMINI               true/false  - false turns every Gemini feature off (default true)
  GEMINI_TEXT_MODEL           model for text/photo analysis. Comma-separated list = "try in this order"
  GEMINI_IMAGE_MODEL          model for outfit-image generation (kept separate from the text model)
  GEMINI_CACHE_MINUTES        how long identical Gemini requests are answered from memory (default 30)
  GEMINI_QUOTA_COOLDOWN_MINUTES  pause Gemini this long after a quota/429 error (default 15)
  GEMINI_MAX_CALLS_PER_MINUTE burst limiter (default 10, 0 = off)
  GEMINI_MAX_CALLS_PER_DAY    soft daily cap for this running app (default 0 = off)
  GEMINI_TIMEOUT_SECONDS      per-request timeout (default 30)
  GEMINI_MAX_RETRIES          retries for temporary errors only, never for quota errors (default 2)
  WEATHER_PROVIDER            auto | openmeteo | weatherapi  (auto = WeatherAPI if WEATHERAPI_KEY is set, else Open-Meteo)
  WEATHERAPI_KEY              only needed for the WeatherAPI provider
  WEATHER_CACHE_MINUTES       how long weather results are reused (default 10)
"""
from __future__ import annotations

import os

_TRUE = {"1", "true", "yes", "on", "y"}
_FALSE = {"0", "false", "no", "off", "n"}
# Names we are willing to copy from Streamlit secrets into the environment.
SECRET_NAMES = (
    "GEMINI_API_KEY", "WEATHERAPI_KEY", "ENABLE_GEMINI", "GEMINI_TEXT_MODEL", "GEMINI_IMAGE_MODEL", "GEMINI_CACHE_MINUTES",
    "GEMINI_QUOTA_COOLDOWN_MINUTES", "GEMINI_MAX_CALLS_PER_MINUTE", "GEMINI_MAX_CALLS_PER_DAY", "GEMINI_TIMEOUT_SECONDS",
    "GEMINI_MAX_RETRIES", "WEATHER_PROVIDER", "WEATHER_CACHE_MINUTES",
)

DEFAULT_TEXT_MODELS = ("gemini-3.5-flash-lite", "gemini-2.5-flash")
DEFAULT_IMAGE_MODELS = ("gemini-3.1-flash-lite-image", "gemini-2.5-flash-image")


def apply_streamlit_secrets() -> list[str]:
    """Copy known settings from ``.streamlit/secrets.toml`` into os.environ (environment/.env always wins).

    Returns the NAMES that were copied (never values). Silent if Streamlit or the secrets file is missing."""
    copied: list[str] = []
    try:
        import streamlit as st
        secrets = st.secrets
        for name in SECRET_NAMES:
            if os.environ.get(name, "").strip():
                continue
            try:
                value = secrets[name]
            except Exception:  # missing key or no secrets file
                continue
            if value is not None and str(value).strip():
                os.environ[name] = str(value).strip()
                copied.append(name)
    except Exception:
        pass
    return copied


def get_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return default


def get_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(os.getenv(name, "").strip()))
    except ValueError:
        return default


def get_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(float(os.getenv(name, "").strip())))
    except ValueError:
        return default


# ------------------------------------------------------------------ Gemini
def gemini_enabled() -> bool:
    """False when ENABLE_GEMINI=false. (A key is checked separately.)"""
    return get_bool("ENABLE_GEMINI", True)


def gemini_api_key() -> str:
    """Your own key from the environment/.env/secrets, or '' when missing OR when Gemini is switched off.
    Existing UI code uses `if gemini_key:` to decide whether Gemini features are offered."""
    return get_str("GEMINI_API_KEY") if gemini_enabled() else ""


def gemini_off_reason() -> str:
    """Human-readable reason Gemini is unavailable, or '' when it is on."""
    if not gemini_enabled():
        return "Gemini is turned off (ENABLE_GEMINI=false)."
    if not get_str("GEMINI_API_KEY"):
        return "No GEMINI_API_KEY is set."
    return ""


def model_chain(kind: str) -> list[str]:
    """Models to try, in order. A user-set value (GEMINI_TEXT_MODEL / GEMINI_IMAGE_MODEL) is used as given;
    separate names with commas to allow a fallback."""
    var, default = (("GEMINI_IMAGE_MODEL", DEFAULT_IMAGE_MODELS) if kind == "image" else ("GEMINI_TEXT_MODEL", DEFAULT_TEXT_MODELS))
    custom = [m.strip() for m in get_str(var).split(",") if m.strip()]
    return custom or list(default)


# ------------------------------------------------------------------ Weather
def weather_cache_seconds() -> float:
    return get_float("WEATHER_CACHE_MINUTES", 10.0, minimum=1.0) * 60


def weather_provider() -> str:
    """'openmeteo' or 'weatherapi' after applying WEATHER_PROVIDER=auto|openmeteo|weatherapi."""
    choice = get_str("WEATHER_PROVIDER", "auto").lower().replace("-", "").replace("_", "")
    has_key = bool(get_str("WEATHERAPI_KEY"))
    if choice in ("openmeteo", "meteo"):
        return "openmeteo"
    if choice == "weatherapi":
        return "weatherapi"
    return "weatherapi" if has_key else "openmeteo"
