"""Small helpers that keep secrets out of logs, error messages and the UI."""
from __future__ import annotations

import os
import re

_SECRET_ENV = ("GEMINI_API_KEY", "WEATHERAPI_KEY")
_PATTERNS = [re.compile(r"(?i)((?:[?&]|\s)key=)[^&\s'\"]+"), re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
             re.compile(r"(?i)(api[_-]?key['\"]?\s*[:=]\s*['\"]?)[^\s'\",]+")]


def redact(text: object) -> str:
    """Remove API keys (from the environment or URL/JSON patterns) from any text before showing or logging it."""
    out = str(text)
    for name in _SECRET_ENV:
        secret = os.getenv(name, "").strip()
        if len(secret) >= 6:
            out = out.replace(secret, "[redacted]")
    out = _PATTERNS[0].sub(r"\1[redacted]", out)
    out = _PATTERNS[1].sub("[redacted]", out)
    out = _PATTERNS[2].sub(r"\1[redacted]", out)
    return out
