"""Consent-gated preference storage.

Preferences stay in the Streamlit session by default. They are written to a local JSON file only
after the user explicitly ticks the consent box and presses Save, and can be deleted at any time.
Location, chat messages and API keys are never stored.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(os.getenv("WEATHERFIT_PREFS_FILE", "user_preferences.json"))
ALLOWED_KEYS = ("occasion", "style", "personality", "budget", "colours", "avoid", "gender", "comfort", "repeat_days")


def clean(prefs: dict[str, Any]) -> dict[str, str]:
    return {k: str(prefs[k]).strip()[:200] for k in ALLOWED_KEYS if k in prefs and str(prefs[k]).strip()}


def save_preferences(prefs: dict[str, Any], consent: bool, path: Path | str = DEFAULT_PATH) -> bool:
    """Persist only with explicit consent. Returns True if saved."""
    if not consent:
        return False
    Path(path).write_text(json.dumps({"consent": True, "preferences": clean(prefs)}, indent=2), encoding="utf-8")
    return True


def load_preferences(path: Path | str = DEFAULT_PATH) -> dict[str, str]:
    """Load saved preferences; ignores the file unless it records consent."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("consent") is not True:
        return {}
    return clean(data.get("preferences", {}) if isinstance(data.get("preferences"), dict) else {})


def delete_preferences(path: Path | str = DEFAULT_PATH) -> bool:
    p = Path(path)
    if p.exists():
        p.unlink()
        return True
    return False
