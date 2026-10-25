"""Voice assistant logic (no Streamlit, no browser code) - fully unit-testable.

How one question is answered
  1. The question (typed or spoken; English, Hindi in Devanagari, or Hinglish) is understood with the existing rule-based
     parsers (``hinglish.py`` and ``chat_assistant.py``). NO Gemini call is made for this.
  2. Weather for the origin and (if travelling) the destination comes from the app's weather client
     (Open-Meteo by default, or WeatherAPI if you configured it). Cities are looked up with the provider's geocoder.
  3. The outfit, footwear, umbrella/jacket/sun items come from the deterministic rules in ``stylist.py``.
  4. A short spoken version and a readable on-screen version are written from those facts in the user's language.
  5. Gemini is used only if (a) it is switched on and has a key, AND (b) either you ticked "make the wording more natural"
     or the question is not about weather/outfits at all. If Gemini is off, paused or out of quota the rule-based answer
     is used - the assistant never crashes and never invents weather.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import hinglish
from chat_assistant import SYSTEM_PROMPT, _CITY, _clean_city, _llm_prompt, check_grounding, handle_message, parse_message
from gemini_guard import GeminiUnavailable
from openmeteo_service import fetched_at_text
from security import redact
from stylist import TripAdvice
from trip import WindowSummary
from weather_provider import provider_name

LLM = Callable[[str, str], str]
MAX_QUESTION_CHARS = 400

# ------------------------------------------------------------------ language helpers
SPEAK_LANG = {"en": "en-IN", "hinglish": "en-IN", "hi": "hi-IN"}   # voice used for the spoken answer

_HINDI_ITEMS = {
    "light": "हल्का", "cotton": "सूती", "linen": "लिनन", "t-shirt": "टी-शर्ट", "shirt": "शर्ट", "lightweight": "हल्की", "chinos": "चिनोज़",
    "trousers": "ट्राउज़र", "breathable": "हवादार", "sneakers": "स्नीकर्स", "sandals": "सैंडल", "jeans": "जींस", "overshirt": "ओवरशर्ट",
    "closed-toe": "बंद", "closed": "बंद", "shoes": "जूते", "footwear": "जूते", "long-sleeve": "पूरी बाजू का", "top": "टॉप", "sweater": "स्वेटर",
    "jacket": "जैकेट", "coat": "कोट", "warm": "गर्म", "full-length": "पूरी लंबाई के", "thermal/base": "थर्मल", "layer": "लेयर", "formal": "फ़ॉर्मल",
    "blazer": "ब्लेज़र", "tailored": "सिले हुए", "comfortable": "आरामदायक", "walking": "वॉकिंग", "stretch": "स्ट्रेच", "water-resistant": "वाटर-रेसिस्टेंट",
    "dark": "गहरे रंग की", "smart": "स्मार्ट", "stylish": "स्टाइलिश", "fitted": "फिटिंग वाले", "well-fitted": "फिटिंग वाले", "clean": "साफ़",
    "smart-casual": "स्मार्ट-कैज़ुअल", "moisture-wicking": "पसीना सोखने वाला", "sports": "स्पोर्ट्स", "activewear": "एक्टिववियर",
    "flexible": "लचीले", "supportive": "सपोर्ट वाले", "activity": "एक्टिविटी", "easy": "आसान", "everyday": "रोज़ के", "fit": "फिट", "fleece": "फ्लीस",
    "knit": "निट", "with": "के साथ", "and": "और", "or": "या", "a": "", "an": "", "the": "", "of": "", "for": "के लिए",
}
_COND_WORDS = [  # (regex on lower-case English condition, hinglish, hindi)
    (r"thunder", "bijli ke saath toofan", "बिजली के साथ तूफ़ान"), (r"snow|sleet|ice", "barf", "बर्फ़"),
    (r"rain|drizzle|shower", "baarish", "बारिश"), (r"fog|mist|haze", "kohra", "कोहरा"),
    (r"overcast", "poore badal", "घने बादल"), (r"cloud", "badal", "बादल"), (r"sunny|clear", "saaf mausam", "साफ़ मौसम"),
]
_UNIT = {"en": ("degrees Celsius", "percent", "kilometres per hour"),
         "hinglish": ("degree Celsius", "pratishat", "kilometre prati ghanta"),
         "hi": ("डिग्री सेल्सियस", "प्रतिशत", "किलोमीटर प्रति घंटा")}
_CARRY = {  # english name -> (hinglish, hindi)
    "umbrella": ("chhata", "छाता"), "jacket": ("jacket", "जैकेट"), "light jacket": ("halki jacket", "हल्की जैकेट"),
    "sunglasses": ("dhoop ka chashma", "धूप का चश्मा"), "cap": ("topi", "टोपी"), "sunscreen": ("sunscreen", "सनस्क्रीन"),
    "windbreaker": ("windbreaker", "विंडब्रेकर"), "water bottle": ("paani ki bottle", "पानी की बोतल"),
    "scarf and beanie": ("muffler aur topi", "मफलर और टोपी"),
}

PH: dict[str, dict[str, str]] = {
    "en": {
        "trip": "Trip from {o} to {d}, leaving {when}.", "single": "Weather for {o}, from {when}.",
        "place": "{p}: feels like {feels}, {cond}, rain chance up to {rain} {pct}, wind up to {wind} {kmh}.",
        "wear": "Wear {top}, with {bottom}. Footwear: {shoes}.",
        "take": "Take: {items}.", "take_none": "You don't need any special extras.",
        "no_umb": "No umbrella needed.", "umb": "Take an umbrella (rain chance up to {rain} {pct}).",
        "warmer": "{d} will feel about {n} degrees warmer than {o}.", "cooler": "{d} will feel about {n} degrees cooler than {o}.",
        "same": "Both places feel about the same.",
        "assume_dur": "I assumed the journey takes about {h} hours. Tell me the real travel time for a more accurate answer.",
        "stamp": "Weather data from {prov}, updated at {t} local time.", "stale": "Live weather could not be reached, so this uses data from about {m} minutes ago.",
        "alert": "A weather alert is active for this area. Please check it before leaving.",
        "no_rain_prob": "Rain probability was not provided for this place, so the umbrella advice may be incomplete.",
        "today": "today", "tomorrow": "tomorrow", "to": "to", "feels_one": "{a} {u}", "feels_rng": "between {a} and {b} {u}",
        "ask_dest": "Where are you travelling to? If you know roughly how long the journey takes, tell me that too.",
        "ask_origin": "Which city are you in? Type it in the box, or say, for example, I am in Bhopal.",
        "empty": "I didn't catch anything. Please tap the microphone and try again, or type your question.",
        "help": "I can help with the weather and what to wear. Try: Today I need to travel from Bhopal to Indore, what should I wear?",
        "err": "Sorry, something went wrong while preparing the answer. Please try again.",
        "partial": "Note: the forecast covers only part of your time window.",
    },
    "hinglish": {
        "trip": "{o} se {d} ka safar, {when} nikalna hai.", "single": "{o} ka mausam, {when} se.",
        "place": "{p}: mehsoos hoga {feels}, {cond}, baarish ki sambhavna {rain} {pct} tak, hawa {wind} {kmh} tak.",
        "wear": "Pehnein: {top}, saath mein {bottom}. Footwear: {shoes}.",
        "take": "Saath rakhein: {items}.", "take_none": "Koi khaas cheez saath rakhne ki zaroorat nahi.",
        "no_umb": "Chhata ki zaroorat nahi.", "umb": "Chhata saath rakhein (baarish ki sambhavna {rain} {pct} tak).",
        "warmer": "{d} {o} se lagbhag {n} degree zyada garam lagega.", "cooler": "{d} {o} se lagbhag {n} degree zyada thanda lagega.",
        "same": "Dono jagah lagbhag ek jaisa mausam hai.",
        "assume_dur": "Maine maana ki safar lagbhag {h} ghante ka hai. Sahi samay bata dein to jawab aur sahi hoga.",
        "stamp": "Mausam ka data {prov} se, {t} baje (local time) update hua.", "stale": "Live mausam nahi mil paya, isliye lagbhag {m} minute purana data use kiya hai.",
        "alert": "Is ilaake ke liye weather alert active hai. Nikalne se pehle zaroor dekh lein.",
        "no_rain_prob": "Is jagah ke liye baarish ki sambhavna nahi mili, isliye chhate ki salah adhoori ho sakti hai.",
        "today": "aaj", "tomorrow": "kal", "to": "se", "feels_one": "{a} {u}", "feels_rng": "{a} se {b} {u}",
        "ask_dest": "Aap kahan travel kar rahe hain? Agar journey kitne ghante ki hai pata ho to wo bhi bata dein.",
        "ask_origin": "Aap kis shehar mein hain? Box mein shehar ka naam likh dein, ya bolein: main Bhopal mein hoon.",
        "empty": "Mujhe kuch sunai nahi diya. Kripya microphone dabakar dobara bolein, ya sawaal type kar dein.",
        "help": "Main mausam aur kya pehnna hai, isme madad kar sakta hoon. Poochiye: Aaj Bhopal se Indore jaana hai, kya pehnu?",
        "err": "Maaf kijiye, jawab banate waqt kuch gadbad ho gayi. Kripya dobara try karein.",
        "partial": "Dhyan dein: forecast aapke poore time window ko cover nahi karta.",
    },
    "hi": {
        "trip": "{o} से {d} की यात्रा, {when} रवाना।", "single": "{o} का मौसम, {when} से।",
        "place": "{p}: महसूस होगा {feels}, {cond}, बारिश की संभावना {rain} {pct} तक, हवा {wind} {kmh} तक।",
        "wear": "पहनें: {top}, साथ में {bottom}। जूते: {shoes}।",
        "take": "साथ रखें: {items}।", "take_none": "कोई ख़ास चीज़ साथ रखने की ज़रूरत नहीं है।",
        "no_umb": "छाते की ज़रूरत नहीं है।", "umb": "छाता साथ रखें (बारिश की संभावना {rain} {pct} तक)।",
        "warmer": "{d} में {o} से लगभग {n} डिग्री ज़्यादा गर्मी लगेगी।", "cooler": "{d} में {o} से लगभग {n} डिग्री ज़्यादा ठंड लगेगी।",
        "same": "दोनों जगह लगभग एक जैसा मौसम है।",
        "assume_dur": "मैंने माना है कि सफ़र लगभग {h} घंटे का है। सही समय बता दें तो जवाब और सटीक होगा।",
        "stamp": "मौसम का डेटा {prov} से, {t} बजे (स्थानीय समय) अपडेट हुआ।", "stale": "लाइव मौसम नहीं मिल पाया, इसलिए लगभग {m} मिनट पुराना डेटा इस्तेमाल किया है।",
        "alert": "इस इलाक़े के लिए मौसम अलर्ट सक्रिय है। निकलने से पहले ज़रूर देख लें।",
        "no_rain_prob": "इस जगह के लिए बारिश की संभावना उपलब्ध नहीं थी, इसलिए छाते की सलाह अधूरी हो सकती है।",
        "today": "आज", "tomorrow": "कल", "to": "से", "feels_one": "{a} {u}", "feels_rng": "{a} से {b} {u}",
        "ask_dest": "आप कहाँ यात्रा कर रहे हैं? अगर सफ़र कितने घंटे का है पता हो तो वह भी बताइए।",
        "ask_origin": "आप किस शहर में हैं? बॉक्स में शहर का नाम लिखें, या बोलें: मैं भोपाल में हूँ।",
        "empty": "मुझे कुछ सुनाई नहीं दिया। कृपया माइक्रोफ़ोन दबाकर फिर बोलें, या सवाल टाइप करें।",
        "help": "मैं मौसम और क्या पहनना है, इसमें मदद कर सकता हूँ। पूछिए: आज भोपाल से इंदौर जाना है, क्या पहनूं?",
        "err": "माफ़ कीजिए, जवाब बनाते समय कुछ गड़बड़ हो गई। कृपया फिर कोशिश करें।",
        "partial": "ध्यान दें: फ़ोरकास्ट आपके पूरे समय को कवर नहीं करता।",
    },
}

_WEATHER_WORDS = re.compile(
    r"\b(wear\w*|outfit\w*|cloth\w*|dress\w*|jacket|sweater|hoodie|coat|umbrella|raincoat|rain\w*|weather|temperature|forecast|"
    r"hot|cold|warm|chilly|humid|sunny|sunscreen|sunglasses|shoes|footwear|travel\w*|trip|journey|drive|driving|commut\w*|going|"
    r"pehn\w*|pahn\w*|kapde|kapdey|mausam|mosam|baarish|barish|garmi|sardi|thand\w*|chhata|chhatri|chata|safar|yatra|jaana|jana|jaa|"
    r"party|office|college|wedding|shaadi|gym)\b", re.I)


@dataclass
class VoiceAnswer:
    ok: bool
    display: str                                   # readable (markdown) answer for the transcript
    spoken: str                                    # plain sentences for text-to-speech
    language: str = "en"                           # en | hinglish | hi
    speak_lang: str = "en-IN"
    followup: bool = False                         # we asked the user a question
    used_gemini: bool = False
    source: str = "rules"                          # rules | gemini
    gemini_status: str = "not_used"                # not_used | live | cached | disabled | no_key | quota | cooldown | rejected | ...
    gemini_note: str = ""
    notes: list[str] = field(default_factory=list)
    stamp: str = ""                                # weather data timestamp line
    details: str = ""                              # longer English details (existing template answer)
    data: dict[str, Any] | None = None


# ------------------------------------------------------------------ small formatters
def speakable(text: str) -> str:
    """Turn markdown / symbols into plain text that sounds right when read aloud."""
    t = re.sub(r"[*_`#>]+", "", text or "")
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.M)
    t = re.sub(r"(-?\d+(?:\.\d+)?)\s*°\s*C?", r"\1 degrees Celsius", t)
    t = re.sub(r"(\d)\s*%", r"\1 percent", t)
    t = t.replace("km/h", "kilometres per hour").replace("→", " to ").replace("–", " to ").replace("—", ", ")
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


def _t(lang: str, key: str, **kw: Any) -> str:
    return PH[lang][key].format(**kw)


def _num(x: float) -> str:
    return str(int(round(x)))


def _range_text(lang: str, lo: float, hi: float) -> str:
    u = _UNIT[lang][0]
    a, b = _num(lo), _num(hi)
    return _t(lang, "feels_one", a=a, u=u) if a == b else _t(lang, "feels_rng", a=a, b=b, u=u)


def _cond_text(cond: str, lang: str) -> str:
    if lang == "en":
        return (cond or "unknown conditions").lower()
    low = (cond or "").lower()
    for rx, hing, hind in _COND_WORDS:
        if re.search(rx, low):
            return hing if lang == "hinglish" else hind
    return (cond or "").lower()


def _clock(dt: datetime, lang: str) -> str:
    h12 = dt.hour % 12 or 12
    if lang == "hi":
        period = "सुबह" if 5 <= dt.hour < 12 else "दोपहर" if 12 <= dt.hour < 16 else "शाम" if 16 <= dt.hour < 20 else "रात"
        return f"{period} {h12}:{dt.minute:02d}"
    return f"{h12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def _when_text(dt: datetime, now: datetime, lang: str) -> str:
    days = (dt.date() - now.date()).days
    if days == 0:
        day = PH[lang]["today"]
    elif days == 1:
        day = PH[lang]["tomorrow"]
    else:
        day = f"{dt:%A %d %B}"
    return f"{day} {_clock(dt, lang)}"


_HI_CITIES = {v: k for k, v in hinglish.DEVANAGARI_WORDS.items() if v[:1].isupper()}


def _short(name: str, lang: str = "en") -> str:
    short = name.split(",")[0].strip()
    return _HI_CITIES.get(short, short) if lang == "hi" else short


def _hindi_items(text: str) -> str:
    out = []
    for tok in re.findall(r"[A-Za-z][A-Za-z/-]*|\S", text or ""):
        low = tok.lower()
        out.append(_HINDI_ITEMS.get(low, tok) if re.match(r"[A-Za-z]", tok) else tok)
    return re.sub(r"\s+", " ", " ".join(out)).replace(" ,", ",").replace(" .", ".").strip()


def _item(text: str, lang: str) -> str:
    """Clothing phrases come from the English rule engine. English/Hinglish keep them as they are (that is how people speak);
    Hindi gets a word-by-word translation of the common terms (unknown words stay in English)."""
    t = (text or "").strip().rstrip(".")
    if lang == "en":
        return t[:1].lower() + t[1:] if t else t
    if lang == "hi":
        return _hindi_items(t)
    return t[:1].lower() + t[1:] if t else t


def _carry_name(name: str, lang: str) -> str:
    if lang == "en":
        return name.lower()
    pair = _CARRY.get(name.lower())
    return (pair[0] if lang == "hinglish" else pair[1]) if pair else name.lower()


# ------------------------------------------------------------------ composing the answer
def _place_line(w: WindowSummary, lang: str) -> str:
    u = _UNIT[lang]
    return _t(lang, "place", p=_short(w.place, lang), feels=_range_text(lang, w.feels_min, w.feels_max), cond=_cond_text(w.main_condition, lang),
              rain=_num(w.rain_chance_max), pct=u[1], wind=_num(w.wind_max_kph), kmh=u[2])


def _obs(cur: dict[str, Any], lang: str = "en") -> tuple[str, str]:
    """(display text, spoken clock text) for when a place's weather was observed (local time at that place)."""
    raw = str(cur.get("last_updated") or cur.get("localtime") or "")
    try:
        upd = datetime.fromisoformat(raw.replace(" ", "T"))
        return f"{upd:%d %b %Y, %H:%M}", _clock(upd, lang)
    except ValueError:
        return raw or "unknown time", raw or "unknown time"


def _stamp(reply_plan: Any, client: Any, lang: str) -> tuple[str, str]:
    """(display line, spoken sentence) saying where the weather data came from and how fresh it is."""
    fc = reply_plan.origin_fc
    cur = fc["current"]
    prov = provider_name(client)
    t_disp, t_spoken = _obs(cur, lang)
    line = f"Weather data: {prov} · {_short(cur['city'])} observed {t_disp} (local time) · {fetched_at_text(fc).strip()}"
    if reply_plan.dest_fc:
        dcur = reply_plan.dest_fc["current"]
        line += f" · {_short(dcur['city'])} observed {_obs(dcur)[0]} (local time) · {fetched_at_text(reply_plan.dest_fc).strip()}"
    spoken = _t(lang, "stamp", prov=prov, t=t_spoken)
    if fc.get("stale"):
        spoken += " " + _t(lang, "stale", m=max(1, int(fc.get("age_s", 0)) // 60))
    return line, spoken


def compose(reply: Any, lang: str, client: Any, now: datetime) -> tuple[str, str, str, list[str]]:
    """-> (display markdown, spoken text, stamp line, notes) built only from the real forecast + rule-based advice."""
    plan, advice = reply.plan, reply.advice
    ow, dw = plan.origin_w, plan.dest_w
    when = _when_text(plan.depart, plan.now_local or now, lang)
    sentences: list[str] = []
    if dw:
        sentences.append(_t(lang, "trip", o=_short(ow.place, lang), d=_short(dw.place, lang), when=when))
        sentences += [_place_line(ow, lang), _place_line(dw, lang)]
        diff = dw.feels_avg - ow.feels_avg
        if abs(diff) < 2:
            sentences.append(_t(lang, "same"))
        else:
            sentences.append(_t(lang, "warmer" if diff > 0 else "cooler", d=_short(dw.place, lang), o=_short(ow.place, lang), n=_num(abs(diff))))
    else:
        sentences += [_t(lang, "single", o=_short(ow.place, lang), when=when), _place_line(ow, lang)]
    sentences.append(_t(lang, "wear", top=_item(advice.top, lang), bottom=_item(advice.bottom, lang), shoes=_item(advice.footwear, lang)))

    umb = advice.item("Umbrella")
    others = [c for c in advice.carry if c.needed and c.item.lower() != "umbrella"]
    if umb:
        u = _UNIT[lang]
        sentences.append(_t(lang, "umb", rain=_num(plan.origin_w.rain_chance_max if not dw else max(ow.rain_chance_max, dw.rain_chance_max)), pct=u[1])
                         if umb.needed else _t(lang, "no_umb"))
    if others:
        sentences.append(_t(lang, "take", items=", ".join(_carry_name(c.item, lang) for c in others)))
    elif not (umb and umb.needed):
        sentences.append(_t(lang, "take_none"))

    notes: list[str] = []
    if plan.duration_h is not None and any("assumed about 3 hours" in a for a in plan.assumptions):
        sentences.append(_t(lang, "assume_dur", h=f"{plan.duration_h:g}"))
    if advice.alerts:
        sentences.append(_t(lang, "alert"))
    if ow.partial or (dw and dw.partial):
        notes.append(_t(lang, "partial"))
    if plan.origin_fc.get("rain_probability_missing") or (plan.dest_fc or {}).get("rain_probability_missing"):
        notes.append(_t(lang, "no_rain_prob"))
    stamp_line, stamp_spoken = _stamp(plan, client, lang)
    notes += [w for w in reply.warnings]
    if reply.intent is not None and reply.intent.time_assumption:
        notes.append(reply.intent.time_assumption)

    spoken = " ".join(sentences + notes[:2] + [stamp_spoken])
    display = "\n".join(f"- {s}" for s in sentences)
    return display, spoken, stamp_line, notes


# ------------------------------------------------------------------ question understanding
_RE_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*(ghante|ghanta|ghantey|ghanta|hours?|hrs?|घंटे|घंटा|घण्टे|minutes?|mins?|minute|मिनट)\b", re.I)
_RE_BAJE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(?:baje|bje|बजे)\b", re.I)


def is_weather_question(text: str, parsed: hinglish.Parsed | None = None) -> bool:
    parsed = parsed or hinglish.parse(text)
    if parsed.intent != "other":
        return True
    if parsed.language != "en" and (parsed.origin or parsed.dest or parsed.city):   # "X se Y" / "X mein" only make sense in Hindi/Hinglish
        return True
    low = hinglish.transliterate(text) if hinglish.DEVANAGARI.search(text) else text
    if _WEATHER_WORDS.search(low):
        return True
    it = parse_message(text)
    return bool(it.dest or it.origin or it.travel or it.wants_umbrella or it.wants_jacket)


_RE_WX_IN = re.compile(rf"\b(?:weather|temperature|forecast|rain\w*|umbrella|jacket|coat|sweater|cold|hot|warm|humid|outfit|clothes)\b[^?.!]*?\b(?:in|at|for)\s+{_CITY}", re.I)


def _ensure_place(query: str) -> str:
    """"Will I need an umbrella in Bhopal?" names a place, but the base parser only reads places after wear/dress/pack. Make the place explicit
    (and only then) so the answer is never silently given for the sidebar city instead of the one the user asked about."""
    it = parse_message(query)
    if it.origin or it.dest:
        return query
    m = _RE_WX_IN.search(query)
    city = _clean_city(m.group(1)) if m else None
    return f"{query} What to wear in {city}?" if city else query


def to_english_query(text: str, parsed: hinglish.Parsed) -> str:
    """Rewrite Hinglish/Hindi into the English phrasing the weather parser understands, keeping duration, time and focus words."""
    base = parsed.english or text
    low = (hinglish.transliterate(text) if hinglish.DEVANAGARI.search(text) else text).lower()
    extra: list[str] = []
    if parsed.language != "en":
        if re.search(r"baarish|barish|chhata|chhatri|chata|बारिश|छाता", low + " " + text):
            extra.append("Do I need an umbrella?")
        if re.search(r"jacket|thand|sardi|sweater|ठंड|जैकेट", low + " " + text):
            extra.append("Do I need a jacket?")
        m = _RE_DURATION.search(text) or _RE_DURATION.search(low)
        if m:
            val = float(m.group(1))
            hours = val / 60 if m.group(2).lower().startswith(("min", "मिन")) else val
            extra.append(f"The journey takes {hours:g} hours.")
        b = _RE_BAJE.search(low)
        if b:
            hour = int(b.group(1)) % 24
            if hour < 12 and re.search(r"\b(shaam|raat|dopahar|evening|night)\b", low):
                hour += 12
            extra.append(f"Leaving at {hour:02d}:{int(b.group(2) or 0):02d}.")
    return _ensure_place((base + " " + " ".join(extra)).strip())


def _language_of(text: str, parsed: hinglish.Parsed) -> str:
    return parsed.language if parsed.language in PH else "en"


# ------------------------------------------------------------------ Gemini (optional)
VOICE_SYSTEM_PROMPT = (SYSTEM_PROMPT + " This answer will be read aloud: write 3 to 5 short plain sentences, no markdown, no bullet points, no "
                       "symbols. Reply in the same language and script style as the user's question (English, Hindi or Hinglish in Roman letters).")
FREEFORM_SYSTEM_PROMPT = (
    "You are WeatherFit AI, a friendly voice assistant for weather-aware fashion. The user asked something that is not a plain weather lookup. "
    "Answer briefly (under 80 words, plain sentences, no markdown) in the same language and script style as the user's question. "
    "You do NOT have live weather data in this conversation: never state or guess a temperature, rain chance, forecast or current conditions; "
    "if they are needed, tell the user to ask a weather question such as 'what should I wear in Bhopal today?'. Stay on topic: clothing, style, "
    "packing, and how weather generally affects what to wear.")


def _gemini_error_status(exc: GeminiUnavailable) -> tuple[str, str]:
    return exc.kind, exc.message


def _try_gemini(llm: LLM | None, prompt: str, system: str, grounded: dict[str, Any] | None) -> tuple[str | None, str, str]:
    """-> (text or None, status, note). Never raises."""
    if llm is None:
        return None, "disabled", "Gemini is off, so the built-in rules answered."
    try:
        answer = llm(prompt, system).strip()
    except GeminiUnavailable as exc:
        kind, msg = _gemini_error_status(exc)
        return None, kind, msg
    except Exception as exc:  # noqa: BLE001 - an optional feature must never crash the assistant
        return None, "error", f"Gemini could not be used ({type(exc).__name__}: {redact(exc)[:100]}). Built-in rules answered instead."
    if not answer:
        return None, "error", "Gemini returned an empty answer, so the built-in rules answered."
    bad = check_grounding(answer, grounded or {})
    if bad:
        return None, "rejected", "Gemini's wording quoted numbers that are not in the weather data, so the data-based answer is shown instead."
    return answer, "live", ""


# ------------------------------------------------------------------ main entry point
def answer(text: str, state: dict[str, Any], profile: dict[str, Any], location: Any, client: Any, planner: dict[str, Any] | None = None,
           llm: LLM | None = None, use_gemini_wording: bool = False, now: datetime | None = None) -> VoiceAnswer:
    """Answer one spoken/typed question. ``state`` is the chat state from ``chat_assistant.new_state()`` (remembers a pending follow-up).

    ``llm`` is an optional (prompt, system) -> text callable (already guarded against quota problems); pass None to keep Gemini out."""
    text = re.sub(r"\s+", " ", (text or "")).strip()[:MAX_QUESTION_CHARS]
    if not text:
        return VoiceAnswer(False, PH["en"]["empty"], PH["en"]["empty"], "en", SPEAK_LANG["en"], followup=True)
    parsed = hinglish.parse(text)
    pending = bool(state.get("pending"))
    lang = state.get("voice_lang") if (pending and state.get("voice_lang") in PH) else _language_of(text, parsed)
    state["voice_lang"] = lang   # a one-word follow-up answer ("Indore") keeps the language of the original question
    speak_lang = SPEAK_LANG[lang]

    # --- not about weather/outfits: Gemini may help (only if available); otherwise a friendly rule-based hint
    if not pending and not is_weather_question(text, parsed):
        if llm is not None:
            out, status, note = _try_gemini(llm, f"USER QUESTION: {text}", FREEFORM_SYSTEM_PROMPT, {})
            if out:
                clean = speakable(out)
                return VoiceAnswer(True, out, clean, lang, speak_lang, used_gemini=True, source="gemini", gemini_status="live")
            help_text = PH[lang]["help"]
            return VoiceAnswer(True, help_text, help_text, lang, speak_lang, followup=True, gemini_status=status, gemini_note=note)
        help_text = PH[lang]["help"]
        return VoiceAnswer(True, help_text, help_text, lang, speak_lang, followup=True, gemini_status="disabled")

    # --- weather / outfit path: deterministic, no Gemini needed
    query = to_english_query(text, parsed) if not pending else text
    try:
        reply = handle_message(query, state, profile, location, client, planner=planner, llm=None, history=None, now=now)
    except Exception as exc:  # noqa: BLE001 - never show a traceback to a voice user
        msg = PH[lang]["err"]
        return VoiceAnswer(False, msg, msg, lang, speak_lang, notes=[f"({type(exc).__name__}: {redact(exc)[:120]})"])

    if reply.followup:
        asks_dest = "travelling to" in reply.text.lower()
        msg = PH[lang]["ask_dest" if asks_dest else "ask_origin"]
        return VoiceAnswer(True, msg, msg, lang, speak_lang, followup=True)
    if not reply.ok:
        msg = redact(reply.text)
        return VoiceAnswer(False, msg, speakable(msg), lang, speak_lang, notes=list(reply.warnings))

    display, spoken, stamp, notes = compose(reply, lang, client, reply.plan.now_local or now or datetime.now())
    ans = VoiceAnswer(True, display, spoken, lang, speak_lang, notes=notes, stamp=stamp, details=reply.text, data=reply.grounded or None)

    if use_gemini_wording:
        out, status, note = _try_gemini(llm, _llm_prompt(text, reply.grounded, None), VOICE_SYSTEM_PROMPT, reply.grounded)
        ans.gemini_status, ans.gemini_note = status, note
        if out:
            ans.display, ans.spoken, ans.used_gemini, ans.source = out, speakable(out), True, "gemini"
            ans.details = reply.text   # the rule-based details stay available
    return ans
