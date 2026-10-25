"""Pastel dashboard building blocks (hero, top-bar chip, weather column, outfit card, feature strip).

Everything here is a pure function that returns an HTML string (all dynamic text is escaped) or a small data structure, so it can be
unit-tested without Streamlit. Nothing is invented: every number, label and picture comes from the live forecast, the outfit engine or
the user's own wardrobe; when data is missing the builders say so instead of filling the gap.
"""
from __future__ import annotations

import base64
import io
from datetime import datetime
from typing import Any

from ui_theme import condition_icon, esc

PAGES = ["Home", "Try-On", "Wardrobe", "Style Guide", "Voice Assistant"]
PAGE_KEYS = {"Home": "nav_home", "Try-On": "nav_tryon", "Wardrobe": "nav_wardrobe", "Style Guide": "nav_style", "Voice Assistant": "nav_voice"}
PAGE_ICONS = {"Home": "🏠", "Try-On": "👕", "Wardrobe": "🧺", "Style Guide": "📖", "Voice Assistant": "🎙️"}
ROLE_ICONS = {"top": "👕", "onepiece": "👗", "outer": "🧥", "bottom": "👖", "footwear": "👟", "accessory": "🎒"}
SCORE_VISUAL_MAX = 5.0   # the ring is only a visual guide: engine scores are relative points, not a percentage

DASH_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Caveat:wght@500;700&display=swap');
:root { --pink2:#EC3F8E; --purple2:#8B4FE8; --sky2:#45A4F4; --mint:#E3F6EE; --grad-pb:linear-gradient(90deg,#EC3F8E 0%,#5AA7F7 100%);
  --grad-vb:linear-gradient(90deg,#8B4FE8 0%,#45A4F4 100%); --hand:'Caveat','Segoe Script','Brush Script MT',cursive; }
.block-container, [data-testid="stMainBlockContainer"] { max-width: 1480px; padding-top: 4.2rem !important; }
.stApp { background: linear-gradient(180deg,#FBF9FF 0%,#F7F5FF 55%,#F3F8FF 100%) !important; }

/* ---------- sidebar ---------- */
[data-testid="stSidebar"] > div:first-child { padding-top: .6rem; }
[data-testid="stSidebar"] { background: #FFFFFF !important; background-image: none !important; }
.wf-sbrand { display:flex; align-items:center; gap:12px; margin:.2rem 0 1rem; }
.wf-sbrand .mark { width:46px; height:46px; display:grid; place-items:center; font-size:30px; color:var(--pink2); }
.wf-sbrand b { display:block; font-size:1.45rem; letter-spacing:-.02em; color:var(--ink); line-height:1.1; }
.wf-sbrand span { font-size:.68rem; color:var(--slate); letter-spacing:.01em; }
.wf-sbrand span i { color:var(--pink2); font-style:normal; padding:0 3px; }
.wf-sh { font-weight:700; font-size:.95rem; margin:1.1rem 0 .3rem; color:var(--ink); }
[data-testid="stSidebar"] hr { margin: .9rem 0; border-color: var(--line); }
/* page menu = one button per page (the active one is highlighted by nav_active_css) */
[class*="st-key-nav_"] button { width:100%; justify-content:flex-start; border:0 !important; background:transparent !important; box-shadow:none !important;
  padding:12px 14px; border-radius:14px; }
[class*="st-key-nav_"] button p { font-weight:600; font-size:1rem; color:var(--ink); }
[class*="st-key-nav_"] button:hover { background:#F4F1FF !important; }
[data-testid="stSidebar"] [data-baseweb="select"] > div { border:1px solid var(--line) !important; border-radius:14px !important; background:#FFFFFF !important; min-height:46px; }
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p { font-size:.82rem; color:var(--slate); font-weight:600; }
[data-testid="stSidebar"] [data-testid="stExpander"] { border-radius:16px; border:1px solid var(--line); box-shadow:none; background:#FFFFFF; }
.st-key-save_prefs button { width:100%; height:48px; border:0 !important; border-radius:999px; color:#fff !important; font-weight:700;
  background: var(--grad-pb) !important; box-shadow:0 12px 24px -14px rgba(236,63,142,.8); }
.st-key-save_prefs button:hover { filter:brightness(1.06); }
.wf-hand { font-family:var(--hand); font-size:1.45rem; line-height:1.15; color:#5B54C9; transform:rotate(-6deg); margin:1.4rem 0 .2rem .4rem; }
.wf-hand small { display:block; width:150px; height:3px; border-radius:3px; background:linear-gradient(90deg,#B9A9F5,transparent); margin-top:4px; }
.wf-blobs { height:90px; margin:0 -1rem -1rem; border-radius:60% 40% 0 0 / 40% 40% 0 0;
  background: radial-gradient(circle at 20% 70%,#E3DBFF 0 38%,transparent 39%), radial-gradient(circle at 70% 90%,#FFD9EA 0 30%,transparent 31%), radial-gradient(circle at 95% 40%,#D6ECFF 0 26%,transparent 27%); }

/* ---------- top bar ---------- */
.st-key-city_search input { padding-left: 44px; height: 48px; border-radius:999px !important;
  background: #fff url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='%236B5BD6' stroke-width='2' stroke-linecap='round'><circle cx='11' cy='11' r='7'/><path d='M20 20l-3.5-3.5'/></svg>") no-repeat 16px center; }
.st-key-city_search [data-baseweb="input"] { border-radius:999px !important; border:1px solid var(--line) !important; box-shadow: var(--shadow); }
.wf-chip-top { display:flex; align-items:center; gap:10px; justify-content:flex-end; height:48px; }
.wf-chip-top .ic { font-size:30px; line-height:1; }
.wf-chip-top b { display:block; font-size:.9rem; color:var(--ink); line-height:1.2; }
.wf-chip-top span { font-size:.8rem; color:var(--slate); }
.st-key-top_refresh button { height:48px; border-radius:999px; font-weight:700; white-space:nowrap; }

/* ---------- hero ---------- */
.st-key-hero { position:relative; overflow:hidden; min-height:300px; border-radius:30px; padding:30px 34px 30px;
  background: linear-gradient(135deg,#FCE3F0 0%,#EADFFB 48%,#D6E9FD 100%); border:1px solid #F0E6FA; box-shadow: var(--shadow); }
.wf-h2-eyebrow, .wf-h2-title, .wf-h2-sub { position:relative; z-index:2; }
.st-key-hero [data-testid="stButton"] { position:relative; z-index:3; }
.wf-h2-eyebrow { letter-spacing:.32em; font-size:.78rem; font-weight:700; color:var(--ink); margin-bottom:12px; }
.wf-h2-title { font-size:3.1rem; font-weight:800; line-height:1.05; letter-spacing:-.03em; color:#221E52; margin:0 0 14px; }
.wf-h2-title i { color:var(--pink2); font-style:normal; font-size:1.7rem; vertical-align:middle; }
.wf-h2-sub { color:#3A3766; font-size:.98rem; line-height:1.75; max-width:560px; margin-bottom:6px; }
.wf-h2-sub b { color:var(--pink2); padding:0 6px; }
.wf-h2-art { position:absolute; right:0; top:0; bottom:0; width:46%; z-index:1; pointer-events:none; }
.wf-h2-art svg { position:absolute; right:0; bottom:0; width:100%; height:100%; }
.wf-h2-script { position:absolute; right:7%; top:14%; font-family:var(--hand); font-size:2rem; line-height:1.05; color:#2C2A66; transform:rotate(-8deg); text-align:left; }
.st-key-hero_start button { height:50px; padding:0 30px; border:0 !important; border-radius:999px; color:#fff !important; font-weight:700; font-size:1rem;
  background: var(--grad-pb) !important; box-shadow:0 14px 26px -14px rgba(236,63,142,.85); }
.st-key-hero_start button:hover { filter:brightness(1.06); }

/* ---------- section header ---------- */
.wf-sec { display:flex; align-items:center; gap:14px; margin: 6px 0 4px; }
.wf-sec .ic { width:46px; height:46px; border-radius:50%; background:#F6E7FA; display:grid; place-items:center; font-size:20px; color:var(--pink2); }
.wf-sec h3 { margin:0; font-size:1.3rem; }
.wf-sec p { margin:2px 0 0; color:var(--slate); font-size:.88rem; }
.st-key-view_all button, .st-key-suggest_another button { border-radius:999px; font-weight:700; height:44px; box-shadow:var(--shadow); }
.st-key-suggest_outfit button { border-radius:999px; font-weight:700; height:44px; border:0 !important; color:#fff !important; background: var(--grad-vb) !important; }

/* ---------- outfit card ---------- */
.st-key-outfit_card, .st-key-more_card { background:#fff; border:1px solid var(--line); border-radius:26px; padding:14px; box-shadow:var(--shadow); }
.wf-cardrow { display:flex; gap:18px; align-items:flex-start; }
.wf-cardrow > .L { flex:1.05; min-width:0; } .wf-cardrow > .R { flex:1; min-width:0; padding:6px 6px 0 0; }
.st-key-tryon_go { width:46%; margin:-6px 8px 4px auto; }
.wf-look { display:flex; gap:12px; }
.wf-thumbs { display:flex; flex-direction:column; gap:10px; width:70px; flex:0 0 70px; background:#FFF6EE; border-radius:18px; padding:8px; }
.wf-thumb { width:54px; height:54px; border-radius:14px; background:#fff; border:1px solid #F1E9E2; display:grid; place-items:center; overflow:hidden; font-size:24px; }
.wf-thumb img { width:100%; height:100%; object-fit:cover; }
.wf-bigimg { flex:1; min-height:360px; border-radius:20px; overflow:hidden; background:linear-gradient(160deg,#F3EEFF,#E4F1FD); display:grid; place-items:center; text-align:center; }
.wf-bigimg img { width:100%; height:100%; object-fit:contain; background:#FAF7FF; }
.wf-bigimg .emo { font-size:54px; letter-spacing:10px; padding:10px; }
.wf-bigimg .cap { color:var(--slate); font-size:.85rem; padding:0 16px 16px; }
.wf-ohead { display:flex; justify-content:space-between; gap:10px; align-items:flex-start; }
.wf-ohead h4 { margin:0; font-size:1.25rem; }
.wf-badge { display:inline-block; font-size:.7rem; font-weight:700; color:#6A4DD8; background:#EFE8FF; border-radius:999px; padding:3px 10px; margin-left:8px; vertical-align:middle; }
.wf-osub { color:var(--slate); font-size:.88rem; margin:6px 0 14px; }
.wf-ring { width:76px; height:76px; border-radius:50%; display:grid; place-items:center; flex:0 0 76px;
  background: conic-gradient(#8B4FE8 calc(var(--p) * 1%), #45A4F4 calc(var(--p) * 1%), #ECE8F7 0); }
.wf-ring > div { width:62px; height:62px; border-radius:50%; background:#fff; display:grid; place-items:center; text-align:center; line-height:1; }
.wf-ring b { font-size:1.35rem; display:block; } .wf-ring small { font-size:.62rem; color:var(--slate); }
.wf-ringcap { text-align:center; font-size:.72rem; color:var(--slate); margin-top:4px; }
.wf-orow { display:flex; gap:14px; align-items:center; padding:9px 0; }
.wf-orow .ic { width:42px; height:42px; border-radius:50%; background:#FDEBF4; display:grid; place-items:center; font-size:20px; flex:0 0 42px; }
.wf-orow:nth-child(even) .ic { background:#EEF0FF; }
.wf-orow b { display:block; font-size:.92rem; } .wf-orow span { color:var(--slate); font-size:.8rem; }
.st-key-tryon_go button { width:100%; height:50px; border:0 !important; border-radius:14px; color:#fff !important; font-weight:700; background: var(--grad-vb) !important; box-shadow:0 14px 26px -16px rgba(139,79,232,.9); }
.wf-opt { display:flex; gap:12px; align-items:center; padding:8px 4px; }
.wf-opt .th { width:70px; height:84px; border-radius:12px; overflow:hidden; background:#F3EEFF; display:grid; place-items:center; font-size:26px; flex:0 0 70px; }
.wf-opt .th img { width:100%; height:100%; object-fit:cover; }
.wf-opt b { display:block; font-size:.92rem; } .wf-opt span { color:var(--slate); font-size:.78rem; }
.wf-opt em { font-style:normal; font-size:.74rem; background:#FFF1DD; color:#B5651D; border-radius:8px; padding:2px 8px; margin-top:4px; display:inline-block; }
[class*="st-key-opt_"] button { border:0 !important; background:transparent !important; color:#6A4DD8 !important; font-weight:700; padding:0; min-height:26px; box-shadow:none; }

/* ---------- right column ---------- */
.wf-loc, .wf-hourly { background:#fff; border:1px solid var(--line); border-radius:26px; padding:20px 22px; box-shadow:var(--shadow); margin-bottom:16px; }
.wf-loc-head { display:flex; align-items:center; gap:12px; }
.wf-loc-head .pin { width:38px; height:38px; border-radius:50%; background:#EEEAFF; display:grid; place-items:center; color:#6A4DD8; }
.wf-loc-head b { font-size:1.15rem; display:block; } .wf-loc-head span { color:var(--slate); font-size:.82rem; }
.wf-loc-main { display:flex; align-items:center; gap:18px; margin:12px 0 14px; }
.wf-loc-main .big { font-size:72px; line-height:1; }
.wf-loc-main .t { font-size:2.9rem; font-weight:800; letter-spacing:-.04em; line-height:1; color:#221E52; }
.wf-loc-main .c { font-size:1.15rem; font-weight:600; margin-top:6px; }
.wf-stats { display:grid; grid-template-columns:repeat(3,1fr); }
.wf-stats > div { padding:2px 10px; border-left:1px solid var(--line); }
.wf-stats > div:first-child { border-left:0; padding-left:0; }
.wf-stats small { color:var(--slate); font-size:.78rem; display:block; } .wf-stats b { font-size:.98rem; }
.wf-tip { margin-top:16px; border-radius:18px; padding:14px 16px; background:linear-gradient(135deg,#E8EEFF,#EEE9FF); font-size:.85rem; color:#3A3766; line-height:1.55; }
.wf-tip b { display:block; margin-bottom:4px; color:var(--ink); }
.wf-hourly h4 { margin:0 0 2px; font-size:1.1rem; } .wf-hourly .sub { color:var(--slate); font-size:.8rem; margin-bottom:12px; }
.wf-hrs { display:grid; grid-template-columns:repeat(5,1fr); gap:8px; }
.wf-hr { border:1px solid var(--line); border-radius:16px; text-align:center; padding:10px 2px; background:#FBFAFF; }
.wf-hr.now { background:#F3EEFF; border-color:#D9CCFA; }
.wf-hr small { display:block; color:var(--slate); font-size:.72rem; } .wf-hr .i { font-size:24px; margin:6px 0 4px; } .wf-hr b { font-size:.95rem; }
.wf-empty { color:var(--slate); font-size:.9rem; padding:8px 0; line-height:1.6; }
.st-key-qa_title { font-weight:700; font-size:1.1rem; margin:6px 0 8px; }
[class*="st-key-qa_"] button { width:100%; min-height:92px; text-align:left; display:flex; flex-direction:column; justify-content:center; align-items:flex-start; border-radius:18px; border:1px solid var(--line); padding:14px 16px; white-space:normal; font-weight:700; }
[class*="st-key-qa_"] button p { font-weight:700; font-size:.88rem; }
[class*="st-key-qa_"] button::after { display:block; font-weight:400; font-size:.74rem; color:var(--slate); margin-top:4px; white-space:normal; }
.st-key-qa_voice button { background:linear-gradient(160deg,#F2EEFF,#E9E4FF) !important; }
.st-key-qa_voice button::after { content:"Get outfit suggestions by voice"; }
.st-key-qa_tryon button { background:linear-gradient(160deg,#FFEFF6,#FFE3EF) !important; }
.st-key-qa_tryon button::after { content:"See how it looks on you"; }
.st-key-qa_wardrobe button { background:linear-gradient(160deg,#EBF9F3,#DDF4EA) !important; }
.st-key-qa_wardrobe button::after { content:"Manage your clothes & outfits"; }
.st-key-qa_style button { background:linear-gradient(160deg,#EAF4FF,#DCEBFF) !important; }
.st-key-qa_style button::after { content:"Chat, rank & generate looks"; }

/* ---------- feature strip ---------- */
.wf-feats { display:grid; grid-template-columns:repeat(4,1fr); background:#fff; border:1px solid var(--line); border-radius:24px; padding:16px 10px; box-shadow:var(--shadow); margin-top:16px; }
.wf-feat { display:flex; align-items:center; gap:12px; padding:2px 16px; border-left:1px solid var(--line); }
.wf-feat:first-child { border-left:0; }
.wf-feat .ic { width:48px; height:48px; border-radius:50%; display:grid; place-items:center; font-size:22px; flex:0 0 48px; }
.wf-feat:nth-child(1) .ic { background:#E3F1FD; } .wf-feat:nth-child(2) .ic { background:#F9E1F2; } .wf-feat:nth-child(3) .ic { background:#E9E4FF; } .wf-feat:nth-child(4) .ic { background:#E5E7FF; }
.wf-feat b { display:block; font-size:.85rem; } .wf-feat span { font-size:.72rem; color:var(--slate); }
.wf-hand-r { font-family:var(--hand); font-size:1.5rem; color:#5B54C9; text-align:right; transform:rotate(-5deg); margin:6px 20px 0 0; }

@media (max-width: 900px) {
  .wf-cardrow { flex-direction:column; } .st-key-tryon_go { width:100%; margin:0; }
  .wf-h2-art { display:none; } .wf-h2-title { font-size:2.2rem; } .wf-look { flex-direction:column; } .wf-thumbs { flex-direction:row; width:auto; flex:none; }
  .wf-feats { grid-template-columns:repeat(2,1fr); gap:12px; } .wf-feat { border-left:0; }
}
"""


# ------------------------------------------------------------------ small helpers
def _short_place(city: str) -> str:
    return str(city or "").split(",")[0].strip()


def _place_with_region(city: str) -> str:
    parts = [p.strip() for p in str(city or "").split(",") if p.strip()]
    return ", ".join(parts[:2])


def _sniff_mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def data_uri(data: bytes | None, max_side: int | None = None) -> str:
    """base64 data URI for an image (optionally shrunk with Pillow). Returns '' when the bytes are missing or unreadable."""
    if not data:
        return ""
    try:
        if max_side:
            from PIL import Image
            img = Image.open(io.BytesIO(data))
            img.thumbnail((max_side, max_side))
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[-1])
                img = bg
            else:
                img = img.convert("RGB")
            out = io.BytesIO()
            img.save(out, "JPEG", quality=82)
            data, mime = out.getvalue(), "image/jpeg"
        else:
            mime = _sniff_mime(data)
    except Exception:
        return ""
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def clock_label(dt: datetime) -> str:
    return f"{dt.hour % 12 or 12} {'AM' if dt.hour < 12 else 'PM'}"


def _parse(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace(" ", "T"))
    except ValueError:
        return None


# ------------------------------------------------------------------ sidebar / top bar / hero
def sidebar_brand() -> str:
    return ('<div class="wf-sbrand"><div class="mark" aria-hidden="true"><svg width="44" height="44" viewBox="0 0 64 64" fill="none" stroke="#EC3F8E" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"><path d="M32 8a6 6 0 1 1 6 6c-4 1-6 4-6 8v4L8 44a5 5 0 0 0 3 9h42a5 5 0 0 0 3-9L32 26"/></svg></div><div><b>WeatherFit AI</b>'
            '<span>Your Style<i>•</i>Your Weather<i>•</i>Our AI</span></div></div>')


def sidebar_footer() -> str:
    return '<div class="wf-hand">Good style<br>brings good vibes! ♡<small></small></div><div class="wf-blobs"></div>'


def top_weather_chip(cur: dict[str, Any] | None) -> str:
    if not cur:
        return ('<div class="wf-chip-top"><div class="ic" aria-hidden="true">🌤️</div><div><b>Live weather</b>'
                '<span>Press Get started</span></div></div>')
    icon = condition_icon(cur.get("condition", ""), bool(cur.get("is_day", True)))
    return (f'<div class="wf-chip-top"><div class="ic" aria-hidden="true">{icon}</div><div><b>{esc(_short_place(cur.get("city", "")))}</b>'
            f'<span>{float(cur.get("temp_c", 0)):.0f}°C • {esc(cur.get("condition", ""))}</span></div></div>')


_SKYLINE = ("<svg viewBox='0 0 520 300' preserveAspectRatio='xMaxYMax slice' xmlns='http://www.w3.org/2000/svg' aria-hidden='true'>"
            "<defs><linearGradient id='g1' x1='0' x2='0' y1='0' y2='1'><stop offset='0' stop-color='#BFD9FA' stop-opacity='.75'/>"
            "<stop offset='1' stop-color='#D9CCF7' stop-opacity='.25'/></linearGradient></defs>"
            "<circle cx='360' cy='120' r='120' fill='#fff' opacity='.35'/><circle cx='430' cy='70' r='60' fill='#FFE3EF' opacity='.7'/>"
            "<g fill='url(#g1)'><rect x='40' y='170' width='46' height='130'/><rect x='92' y='130' width='38' height='170'/>"
            "<rect x='138' y='190' width='54' height='110'/><rect x='200' y='110' width='34' height='190'/><rect x='244' y='160' width='60' height='140'/>"
            "<rect x='312' y='140' width='40' height='160'/><rect x='360' y='180' width='58' height='120'/><rect x='426' y='120' width='44' height='180'/>"
            "<rect x='478' y='170' width='42' height='130'/></g>"
            "<g transform='translate(250 70)'><path d='M60 6a14 14 0 1 1 14 14c-8 3-14 8-14 16v8L10 78a12 12 0 0 0 7 22h86a12 12 0 0 0 7-22L74 44' "
            "fill='none' stroke='#EC3F8E' stroke-width='7' stroke-linecap='round' stroke-linejoin='round'/></g>"
            "<path d='M300 205l-26 20 14 22 12-9v52h84v-52l12 9 14-22-26-20q-30 18-84 0z' fill='#C9B6F6' stroke='#fff' stroke-width='4' stroke-linejoin='round'/>"
            "</svg>")


def hero_markup() -> str:
    return ('<div class="wf-h2-art">' + _SKYLINE + '<div class="wf-h2-script">Same you,<br>just better<br>outfits! ♡</div></div>'
            '<div class="wf-h2-eyebrow">WEATHERFIT AI</div><div class="wf-h2-title">Dress Smart,<br>Live Better <i>♡</i></div>'
            '<div class="wf-h2-sub">AI powered outfit recommendations <b>•</b> Real-time weather<br>Virtual try-on <b>•</b> Your personal style guide</div>')


def section_header(icon: str, title: str, subtitle: str) -> str:
    return (f'<div class="wf-sec"><div class="ic" aria-hidden="true">{esc(icon)}</div><div><h3>{esc(title)}</h3><p>{esc(subtitle)}</p></div></div>')


# ------------------------------------------------------------------ right column
def hourly_slots(fc: dict[str, Any], now: datetime | None = None, step: int = 3, count: int = 4) -> list[dict[str, Any]]:
    """'Now' (current conditions) followed by real forecast hours every ``step`` hours. Hours that are not in the forecast are left out."""
    cur = fc["current"]
    now = now or _parse(cur.get("localtime", "")) or datetime.now()
    slots = [{"label": "Now", "temp": float(cur.get("temp_c", 0)), "condition": cur.get("condition", ""), "is_day": bool(cur.get("is_day", True)), "now": True}]
    rows = [(r, _parse(r["time"])) for r in fc.get("hours", [])]
    rows = [(r, t) for r, t in rows if t is not None]
    hour0 = now.replace(minute=0, second=0, microsecond=0)
    start = next((i for i, (_, t) in enumerate(rows) if t >= hour0), None)
    if start is None:
        return slots
    for k in range(1, count + 1):
        idx = start + k * step
        if idx >= len(rows):
            break
        r, t = rows[idx]
        slots.append({"label": clock_label(t), "temp": float(r["temp_c"]), "condition": r.get("condition", ""), "is_day": bool(r.get("is_day", True)), "now": False})
    return slots


def rain_chance_now(fc: dict[str, Any], now: datetime | None = None) -> float | None:
    cur = fc["current"]
    now = now or _parse(cur.get("localtime", ""))
    if now is None:
        return None
    hour0 = now.replace(minute=0, second=0, microsecond=0)
    for r in fc.get("hours", []):
        t = _parse(r["time"])
        if t is not None and t >= hour0:
            return float(r.get("chance_of_rain", 0))
    return None


def location_card(fc: dict[str, Any] | None, tip: str = "") -> str:
    if not fc:
        return ('<div class="wf-loc"><div class="wf-loc-head"><div class="pin">📍</div><div><b>Your weather</b><span>Live data appears here</span></div></div>'
                '<div class="wf-empty">Press <b>Get started</b> to load the live forecast for your city.</div></div>')
    cur = fc["current"]
    now = _parse(cur.get("localtime", ""))
    date_txt = f"Today, {now.day} {now:%b %Y}" if now else "Today"
    chance = rain_chance_now(fc, now)
    rain = f"{chance:.0f}%" if chance is not None else f"{float(cur.get('precip_mm', 0)):.1f} mm"
    rain_label = "Rain chance" if chance is not None else "Rain now"
    icon = condition_icon(cur.get("condition", ""), bool(cur.get("is_day", True)))
    tip_html = f'<div class="wf-tip"><b>💡 Style Tip</b>{esc(tip)}</div>' if tip else ""
    return (f'<div class="wf-loc"><div class="wf-loc-head"><div class="pin">📍</div><div><b>{esc(_short_place(cur.get("city", "")))}</b><span>{esc(date_txt)}</span></div></div>'
            f'<div class="wf-loc-main"><div class="big" aria-hidden="true">{icon}</div><div><div class="t">{float(cur.get("temp_c", 0)):.0f}°C</div>'
            f'<div class="c">{esc(cur.get("condition", ""))}</div></div></div>'
            f'<div class="wf-stats"><div><small>💧 Humidity</small><b>{esc(cur.get("humidity", 0))}%</b></div>'
            f'<div><small>🌬️ Wind</small><b>{float(cur.get("wind_kph", 0)):.0f} km/h</b></div>'
            f'<div><small>☔ {rain_label}</small><b>{esc(rain)}</b></div></div>{tip_html}</div>')


def hourly_card(fc: dict[str, Any] | None) -> str:
    if not fc:
        return '<div class="wf-hourly"><h4>Today’s Weather</h4><div class="wf-empty">The hourly forecast appears after you press Get started.</div></div>'
    slots = hourly_slots(fc)
    cells = "".join(f'<div class="wf-hr{" now" if s["now"] else ""}"><small>{esc(s["label"])}</small><div class="i" aria-hidden="true">{condition_icon(s["condition"], s["is_day"])}</div>'
                    f'<b>{s["temp"]:.0f}°</b></div>' for s in slots)
    return (f'<div class="wf-hourly"><h4>Today’s Weather</h4><div class="sub">{esc(_place_with_region(fc["current"].get("city", "")))}</div>'
            f'<div class="wf-hrs">{cells}</div></div>')


def feature_strip(provider_label: str) -> str:
    items = [("☁️", "Real-time Weather", f"Live data from {provider_label}"), ("🧠", "AI Recommendations", "Wardrobe + weather rules, FashionCLIP matching"),
             ("👕", "Try-On Experience", "2D & AR virtual try-on"), ("🎙️", "Smart Assistant", "Voice + text support")]
    cells = "".join(f'<div class="wf-feat"><div class="ic" aria-hidden="true">{i}</div><div><b>{esc(t)}</b><span>{esc(s)}</span></div></div>' for i, t, s in items)
    return f'<div class="wf-feats">{cells}</div>'


# ------------------------------------------------------------------ outfit card
def style_tip(advice: Any, limit: int = 230) -> str:
    text = (getattr(advice, "outfit_reason", "") or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _piece_thumb(piece: Any) -> str:
    from wardrobe import _REFERENCE_RGB, read_image_bytes
    for rel in (getattr(piece, "image_path", None), getattr(piece, "reference_path", None)):
        uri = data_uri(read_image_bytes(rel), 120) if rel else ""
        if uri:
            return f'<div class="wf-thumb" title="{esc(piece.label)}"><img alt="{esc(piece.label)}" src="{uri}"></div>'
    r, g, b = _REFERENCE_RGB.get(piece.colour, (225, 225, 235))
    return f'<div class="wf-thumb" title="{esc(piece.label)}" style="background:rgb({r},{g},{b})">{ROLE_ICONS.get(piece.role, "👕")}</div>'


def rec_rows(rec: Any) -> list[tuple[str, str, str, str]]:
    """(role label, icon, title, subtitle) rows for a wardrobe / inspiration recommendation."""
    order = (("top", "Top"), ("onepiece", "Outfit"), ("outer", "Outer"), ("bottom", "Bottom"), ("footwear", "Footwear"))
    rows = []
    for role, label in order:
        for p in rec.pieces:
            if p.role == role:
                rows.append((label, ROLE_ICONS[role], p.label, "In your wardrobe" if p.owned else "Suggestion - not in your wardrobe"))
    if rec.accessories:
        rows.append(("Accessories", ROLE_ICONS["accessory"], ", ".join(a.label for a in rec.accessories[:3]),
                     "In your wardrobe" if all(a.owned for a in rec.accessories[:3]) else "Optional"))
    return rows


def advice_rows(advice: Any) -> list[tuple[str, str, str, str]]:
    rows = [("Top", ROLE_ICONS["top"], advice.top, "Rule-based pick for today's forecast"), ("Bottom", ROLE_ICONS["bottom"], advice.bottom, ""),
            ("Footwear", ROLE_ICONS["footwear"], advice.footwear, "")]
    needed = [c.item for c in advice.carry if c.needed]
    rows.append(("Accessories", ROLE_ICONS["accessory"], ", ".join(needed) if needed else "Nothing extra needed", "Carry for today's weather"))
    return [(a, b, (c[:1].upper() + c[1:]) if c else c, d) for a, b, c, d in rows]


def score_ring(score: float) -> str:
    pct = max(0.0, min(100.0, score / SCORE_VISUAL_MAX * 100.0))
    return (f'<div><div class="wf-ring" style="--p:{pct:.0f}" role="img" aria-label="Match score {score:.1f}"><div><span><b>{score:.1f}</b><small>pts</small></span></div></div>'
            f'<div class="wf-ringcap">Match score</div></div>')


def outfit_details(title: str, subtitle: str, rows: list[tuple[str, str, str, str]], score: float | None = None, best: bool = False) -> str:
    badge = '<span class="wf-badge">Best Match</span>' if best else ""
    ring = score_ring(score) if score is not None else ""
    body = "".join(f'<div class="wf-orow"><div class="ic" aria-hidden="true">{esc(i)}</div><div><b>{esc(label)}</b><span>{esc(t)}</span>'
                   f'{"<span> · " + esc(s) + "</span>" if s else ""}</div></div>' for label, i, t, s in rows)
    return (f'<div class="wf-ohead"><div><h4>{esc(title)}{badge}</h4><div class="wf-osub">{esc(subtitle)}</div></div>{ring}</div>{body}')


def look_visual(rec: Any | None, advice: Any | None) -> str:
    """Left part of the outfit card: thumbnail strip + a large picture (collage of the user's real pieces, or emoji tiles from the rules)."""
    if rec is not None:
        thumbs = "".join(_piece_thumb(p) for p in rec.all_pieces[:5])
        try:
            from outfit_visuals import build_collage
            uri = data_uri(build_collage(rec), 900)
        except Exception:
            uri = ""
        cap = "Item collage of this look" + ("" if rec.kind == "wardrobe" else " (colour ideas, not products)")
        big = f'<div class="wf-bigimg"><img alt="{esc(cap)}" src="{uri}"></div>' if uri else f'<div class="wf-bigimg"><div class="emo">{"".join(ROLE_ICONS.get(p.role, "") for p in rec.pieces)}</div></div>'
        return f'<div class="wf-look"><div class="wf-thumbs">{thumbs}</div>{big}</div>'
    thumbs = "".join(f'<div class="wf-thumb">{e}</div>' for e in ("👕", "👖", "👟", "🎒"))
    big = '<div class="wf-bigimg"><div><div class="emo">👕👖👟</div><div class="cap">Add clothes to My Wardrobe and press “Suggest an outfit” to see your own pieces here.</div></div></div>'
    return f'<div class="wf-look"><div class="wf-thumbs">{thumbs}</div>{big}</div>'


def option_row(rec: Any) -> str:
    first = next((p for p in rec.pieces if p.role in ("top", "onepiece")), rec.pieces[0] if rec.pieces else None)
    uri = ""
    if first is not None:
        from wardrobe import read_image_bytes
        for rel in (first.image_path, first.reference_path):
            uri = data_uri(read_image_bytes(rel), 140) if rel else ""
            if uri:
                break
    th = f'<img alt="" src="{uri}">' if uri else ROLE_ICONS.get(first.role if first else "top", "👕")
    names = " + ".join(p.label for p in rec.pieces if p.role in ("top", "onepiece", "bottom"))[:60] or rec.label
    return f'<div class="wf-opt"><div class="th">{th}</div><div><b>{esc(names)}</b><span>{esc(rec.label)}</span><br><em>Match {rec.score:.1f}</em></div></div>'


def alt_row(alt: dict[str, str]) -> str:
    """A rule-based alternative (from stylist.build_trip_advice) shown in 'More Options' before wardrobe outfits exist."""
    return (f'<div class="wf-opt"><div class="th">👕</div><div><b>{esc(alt.get("title", ""))}</b><span>{esc(alt.get("pieces", ""))}</span></div></div>')


def card_row(look_html: str, details_html: str) -> str:
    """Picture on the left, details on the right, as ONE element (Streamlit allows only one level of nested columns)."""
    return f'<div class="wf-cardrow"><div class="L">{look_html}</div><div class="R">{details_html}</div></div>'


def nav_active_css(page: str) -> str:
    """<style> that highlights the active page button in the sidebar menu."""
    key = PAGE_KEYS.get(page, PAGE_KEYS["Home"])
    return f"<style>.st-key-{key} button {{ background:#E6E3FC !important; }} .st-key-{key} button p {{ color:#4B3FC4 !important; }}</style>"
