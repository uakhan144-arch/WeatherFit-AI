"""Understand English, Hindi (basic Devanagari) and Hinglish (Hindi in Roman letters) stylist questions.

This is a small, transparent rule-based layer - NOT a translator. It detects the language and the intent, pulls out cities, time
words, occasion and a clothing item, and rewrites the question into plain English so the existing weather/trip parser in
``chat_assistant.py`` can handle it. Anything it cannot recognise is passed through unchanged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import taxonomy as tx

DEVANAGARI = re.compile(r"[\u0900-\u097F]")
DEVANAGARI_WORDS = {"आज": "aaj", "कल": "kal", "पार्टी": "party", "क्या": "kya", "पहनूं": "pehnu", "पहनूँ": "pehnu", "पहनू": "pehnu", "पहनना": "pehnu",
                    "पहना": "pehna", "पहने": "pehne", "था": "tha", "थी": "thi", "थे": "the", "मैंने": "maine", "मैने": "maine", "कपड़े": "kapde",
                    "नहीं": "nahi", "नया": "naya", "नई": "naya", "लुक": "look", "शर्ट": "shirt", "टीशर्ट": "t-shirt", "जींस": "jeans", "ब्लू": "blue",
                    "नीली": "blue", "नीला": "blue", "सफेद": "white", "काली": "black", "काला": "black", "साथ": "saath", "के": "ke", "मैच": "match",
                    "होगा": "hoga", "यात्रा": "travel", "से": "se", "में": "mein", "ऑफिस": "office", "ऑफ़िस": "office", "शादी": "shaadi", "कॉलेज": "college",
                    "सुझाओ": "suggest", "सुझाव": "suggest", "दिखाओ": "dikhao", "लिए": "liye", "मेरे": "mere", "पास": "paas", "वार्डरोब": "wardrobe",
                    "अलग": "alag", "कुछ": "kuch", "करो": "karo", "है": "hai", "हैं": "hain", "सही": "sahi", "गर्मी": "garmi", "बारिश": "baarish",
                    "ग्वालियर": "Gwalior", "भोपाल": "Bhopal", "इंदौर": "Indore", "दिल्ली": "Delhi", "मुंबई": "Mumbai", "जयपुर": "Jaipur",
                    "लखनऊ": "Lucknow", "पुणे": "Pune", "कोलकाता": "Kolkata", "चेन्नई": "Chennai", "उज्जैन": "Ujjain", "जबलपुर": "Jabalpur",
                    "आगरा": "Agra", "कानारिया": "Kanaria", "रायपुर": "Raipur", "नागपुर": "Nagpur"}
HINGLISH_MARKERS = {"kya", "pehnu", "pehnun", "pehnoon", "pehnu?", "pehen", "pahnu", "aaj", "kal", "mein", "mai", "liye", "ke", "saath", "sath", "nahi",
                    "nahin", "hain", "hai", "mere", "mera", "meri", "maine", "tha", "thi", "the", "karo", "dikhao", "kuch", "paas", "chahiye", "batao",
                    "bataiye", "se", "ko", "hoga", "kaise", "kaun", "wala", "wali", "pehna", "pehne", "kapde", "naya", "naye", "alag", "sahi", "aur", "bhi"}
NOT_CITY = HINGLISH_MARKERS | {"travel", "safar", "yatra", "jaana", "jana", "jane", "ja", "jaa", "tak", "party", "office", "college", "wedding", "shaadi",
                               "aaj", "kal", "subah", "dopahar", "shaam", "raat", "outfit", "accessories", "suggest", "look", "mujhe", "hum", "main", "a", "i",
                               "the", "my", "to", "from", "in", "for", "and", "what", "which", "wardrobe", "clothes", "shirt", "jeans", "date", "gym"}
HINDI_COLOURS = {"neela": "blue", "neeli": "blue", "nila": "blue", "safed": "white", "safaid": "white", "kala": "black", "kaala": "black", "kali": "black",
                 "laal": "red", "lal": "red", "hara": "green", "hari": "green", "peela": "yellow", "peeli": "yellow", "bhura": "brown", "gulabi": "pink",
                 "narangi": "orange", "kesariya": "orange", "grey": "grey", "gray": "grey", "jamuni": "purple", "sunehra": "mustard"}
DAY_WORDS = {"aaj": "today", "kal": "tomorrow", "subah": "in the morning", "dopahar": "in the afternoon", "shaam": "in the evening", "raat": "tonight"}
OCCASION_WORDS = {"party": "party", "shaadi": "wedding", "shadi": "wedding", "wedding": "wedding", "office": "office", "college": "college",
                  "date": "date", "gym": "gym", "meeting": "meeting", "interview": "interview", "reception": "reception", "picnic": "picnic"}

WEAR_RE = re.compile(r"\b(pehn\w*|pahn\w*|wear\w*|outfit|kapde|kapde\s+pehn\w*|dress\s+up|look|suggest\w*|kya\s+pehnu|style|clothes)\b", re.I)
YESTERDAY_RE = re.compile(r"\b(yesterday|kal)\b", re.I)
PAST_RE = re.compile(r"\b(pehna\s+(tha|thi)|pehne\s+(the|thi)|pehni\s+thi|pahna\s+tha|wore|did\s+i\s+wear|had\s+i\s+worn|was\s+i\s+wearing|maine\s+kya\s+pehna|kya\s+pehna\s+tha)\b", re.I)
FUTURE_RE = re.compile(r"\b(pehnu|pehnun|pehnoon|pahnu|should\s+i\s+wear|will\s+i\s+wear|pehnna|pehenu)\b", re.I)
DIFFERENT_RE = re.compile(r"\b(different|alag|kuch\s+naya|kuch\s+alag|something\s+else|another|new\s+one|change|badal\w*)\b", re.I)
NEW_LOOK_RE = re.compile(r"(suitable|sahi|theek|accha|achha|proper)\s+(clothes|kapde|kapdey)?.{0,25}\b(nahi|nahin|no|don'?t|dont)\b|"
                         r"\b(nothing\s+to\s+wear|don'?t\s+have\s+(any\s+)?(suitable|clothes|anything)|naya\s+look|naye\s+kapde|new\s+look|show\s+me\s+a\s+new\s+look|"
                         r"kuch\s+naya\s+dikhao|ek\s+naya\s+look)", re.I)
MATCH_RE = re.compile(r"\b(match|goes?\s+with|go\s+with|pair|pairs|pairing|saath\s+kya|sath\s+kya|ke\s+saath|ke\s+sath|combine)\b", re.I)
RE_SE = re.compile(r"\b([a-z][a-z'.-]*)\s+se\s+([a-z][a-z'.-]*)\b", re.I)
RE_MEIN = re.compile(r"\b([a-z][a-z'.-]*)\s+(?:mein|mai|me|main)\b", re.I)
RE_TO_ENGLISH = re.compile(r"\bfrom\s+[a-z]+\s+to\s+[a-z]+|\bin\s+[A-Z][a-z]+", re.I)


@dataclass
class Parsed:
    language: str = "en"                 # en | hinglish | hi
    intent: str = "other"                # outfit | yesterday | match_item | new_look | other
    english: str = ""                    # question rewritten for the weather parser
    wants_different: bool = False
    day_offset: int = 0
    occasion_word: str | None = None
    origin: str | None = None
    dest: str | None = None
    city: str | None = None
    item_colour: str | None = None
    item_category: str | None = None
    notes: list[str] = field(default_factory=list)


def transliterate(text: str) -> str:
    """Map known Devanagari words (whole tokens only) to Roman Hindi; unknown words are left as they are."""
    toks = re.findall(r"[\u0900-\u097F]+|[A-Za-z0-9'.-]+|\S", text)
    return " ".join(DEVANAGARI_WORDS.get(t, t) for t in toks)


def detect_language(text: str) -> str:
    if DEVANAGARI.search(text):
        return "hi"
    words = re.findall(r"[a-z]+", text.lower())
    hits = sum(1 for w in words if w in HINGLISH_MARKERS)
    return "hinglish" if hits >= 2 else "en"


def _city_ok(tok: str | None) -> str | None:
    if not tok:
        return None
    low = tok.lower().strip(".,!?'")
    if low in NOT_CITY or low in tx.COLOURS or tx.normalize_category(low) or low in HINDI_COLOURS or len(low) < 3:
        return None
    return low.capitalize() if tok.islower() else tok.strip(".,!?'")


def _find_item(text: str) -> tuple[str | None, str | None]:
    low = text.lower()
    colour = None
    for word, eng in HINDI_COLOURS.items():
        if re.search(rf"\b{word}\b", low):
            colour = eng
            break
    if not colour:
        for c in sorted(tx.COLOURS + list(tx.COLOUR_ALIASES), key=len, reverse=True):
            if re.search(rf"\b{re.escape(c)}\b", low):
                colour = tx.normalize_colour(c)
                break
    category = None
    for cand in sorted(list(tx.CATEGORIES) + list(tx.ALIASES), key=len, reverse=True):
        if re.search(rf"(?<![a-z-]){re.escape(cand)}(?![a-z-])", low):
            category = tx.normalize_category(cand)
            break
    return colour, category


def parse(text: str) -> Parsed:
    raw = text.strip()
    lang = detect_language(raw)
    t = transliterate(raw) if lang == "hi" else raw
    if lang == "hi" and detect_language(t) == "en":
        lang = "hi"
    low = t.lower()
    p = Parsed(language=lang)
    p.wants_different = bool(DIFFERENT_RE.search(low))
    p.occasion_word = next((v for k, v in OCCASION_WORDS.items() if re.search(rf"\b{k}\b", low)), None)

    past = bool(YESTERDAY_RE.search(low) and (PAST_RE.search(low) or (re.search(r"\byesterday\b", low) and not FUTURE_RE.search(low))))
    if re.search(r"\bkal\b", low) and not past and not re.search(r"\b(yesterday|tomorrow)\b", low):
        p.day_offset = 1
    elif re.search(r"\btomorrow\b", low):
        p.day_offset = 1

    colour, category = _find_item(t)
    if past:
        p.intent = "yesterday"
    elif NEW_LOOK_RE.search(low):
        p.intent = "new_look"
    elif MATCH_RE.search(low) and (colour or category) and re.search(r"\b(wardrobe|mere|my|meri|mera|paas|with|saath|sath|ke)\b", low):
        p.intent, p.item_colour, p.item_category = "match_item", colour, category
    elif WEAR_RE.search(low) or p.occasion_word or detect_language(raw) != "en":
        p.intent = "outfit"

    # cities
    m = RE_SE.search(t)
    if m and _city_ok(m.group(1)) and _city_ok(m.group(2)):
        p.origin, p.dest = _city_ok(m.group(1)), _city_ok(m.group(2))
    else:
        for mm in RE_MEIN.finditer(t):
            c = _city_ok(mm.group(1))
            if c:
                p.city = c
                break
    # rewrite for the English weather parser
    if p.intent in ("outfit", "new_look", "yesterday") and (lang != "en" or p.intent != "outfit"):
        when = "tomorrow" if p.day_offset == 1 else "today"
        for k, v in DAY_WORDS.items():
            if k not in ("aaj", "kal") and re.search(rf"\b{k}\b", low):
                when += f" {v}"
        occ = f" for {p.occasion_word}" if p.occasion_word else ""
        if p.origin and p.dest:
            where = f"from {p.origin} to {p.dest}"
        elif p.city:
            where = f"in {p.city}"
        else:
            where = ""
        travel = " travelling" if re.search(r"\b(travel|safar|yatra|jaana|jana|jane)\b", low) and not (p.origin and p.dest) else ""
        p.english = f"What should I wear {where} {when}{occ}{travel}?".replace("  ", " ")
    else:
        p.english = t
    return p


# ------------------------------------------------------------------ fixed Hinglish phrases for the deterministic reply
HINGLISH_FOLLOWUPS = {
    "Which city are you in?": "Aap kis shehar mein hain? Sidebar mein location detect karein ya shehar ka naam likh dein.",
    "Where are you travelling to?": "Aap kahan travel kar rahe hain? Agar journey kitne ghante ki hai pata ho to wo bhi bata dein.",
}


def localise_followup(text: str, language: str) -> str:
    if language == "en":
        return text
    for en, hi in HINGLISH_FOLLOWUPS.items():
        if text.startswith(en):
            return hi
    return text
