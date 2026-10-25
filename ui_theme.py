"""Light theme + small HTML components (weather cards, outfit card, header) for WeatherFit AI.

Pure functions return HTML strings (everything dynamic is escaped), so they can be unit-tested without Streamlit.
``apply_theme()`` injects the CSS. Selectors target Streamlit's data-testid attributes; they are best-effort and may need a small
tweak after a major Streamlit upgrade - the app still works (just less styled) if a selector stops matching.
"""
from __future__ import annotations

import html
import re
from typing import Any

INK, SLATE, MIST, CLOUD, LINE = "#231F4F", "#6B6A8E", "#FAF8FF", "#FFFFFF", "#ECE8F7"
SKY, RAIN, SUN = "#5B8DEF", "#4FA3D9", "#E8A33D"
PINK, LAVENDER, SKYSOFT = "#FFE3EE", "#ECE4FF", "#DDEEFF"
ACCENT, ACCENT_DEEP = "#9B7BEA", "#7656D6"

CSS = f"""
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');
:root {{ --ink:{INK}; --slate:{SLATE}; --mist:{MIST}; --cloud:{CLOUD}; --line:{LINE}; --sky:{SKY}; --rain:{RAIN}; --sun:{SUN};
  --pink:{PINK}; --lav:{LAVENDER}; --skysoft:{SKYSOFT}; --accent:{ACCENT}; --accent-deep:{ACCENT_DEEP};
  --shadow:0 10px 30px -18px rgba(118,86,214,.35); }}
html, body, .stApp, [data-testid="stAppViewContainer"] {{ background: linear-gradient(135deg, #FFF3F8 0%, #F6F1FF 45%, #EAF4FF 100%) fixed; color: var(--ink);
  font-family: 'Plus Jakarta Sans', 'Segoe UI', system-ui, -apple-system, sans-serif; }}
h1, h2, h3, h4 {{ font-family: inherit; color: var(--ink); letter-spacing: -0.02em; font-weight: 700; }}
[data-testid="stHeader"] {{ background: rgba(250,247,255,.7); backdrop-filter: blur(8px); }}
.block-container {{ max-width: 1180px; padding-top: 2.2rem; padding-bottom: 4rem; }}
p, li, label, span {{ line-height: 1.55; }}
a {{ color: var(--sky); }}

/* sidebar */
[data-testid="stSidebar"] {{ background: linear-gradient(180deg, #FFFFFF 0%, #FBF7FF 60%, #F3F8FF 100%); border-right: 1px solid var(--line); box-shadow: 8px 0 30px -24px rgba(118,86,214,.4); }}
[data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {{ font-size: 1.05rem; margin-top: 1.2rem; }}

/* navigation bar = the tab list */
.stTabs [data-baseweb="tab-list"] {{ position: sticky; top: 3.4rem; z-index: 30; gap: 4px; padding: 6px; overflow-x: auto; scrollbar-width: none;
  background: rgba(255,255,255,.85); backdrop-filter: blur(10px); border: 1px solid var(--line); border-radius: 18px;
  box-shadow: var(--shadow); }}
.stTabs [data-baseweb="tab-list"]::-webkit-scrollbar {{ display: none; }}
.stTabs [data-baseweb="tab"] {{ height: 40px; padding: 0 14px; border-radius: 11px; color: var(--slate); font-weight: 600; white-space: nowrap;
  transition: background .18s ease, color .18s ease; }}
.stTabs [data-baseweb="tab"]:hover {{ background: var(--lav); color: var(--ink); }}
.stTabs [aria-selected="true"] {{ background: linear-gradient(135deg, #F7A8C8 0%, #A98CF0 55%, #7FB2F5 100%) !important; color: #fff !important; box-shadow: 0 8px 18px -10px rgba(118,86,214,.7); }}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {{ display: none; }}
.stTabs [data-baseweb="tab-panel"] {{ padding-top: 1.4rem; }}

/* cards: bordered containers, metrics, expanders, chat bubbles, forms */
[data-testid="stVerticalBlockBorderWrapper"] {{ background: var(--cloud); border: 1px solid var(--line) !important; border-radius: 22px;
  box-shadow: var(--shadow); }}
[data-testid="stMetric"] {{ background: linear-gradient(160deg, #FFFFFF 0%, var(--lav) 160%); border: 1px solid var(--line); border-radius: 18px; padding: 14px 16px; box-shadow: var(--shadow); }}
[data-testid="stMetricLabel"] {{ color: var(--slate); }}
[data-testid="stExpander"] {{ background: var(--cloud); border: 1px solid var(--line); border-radius: 14px; }}
[data-testid="stChatMessage"] {{ background: var(--cloud); border: 1px solid var(--line); border-radius: 16px; }}
[data-testid="stForm"] {{ background: var(--cloud); border: 1px solid var(--line); border-radius: 16px; }}

/* inputs and buttons */
.stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {{ border-radius: 12px; border: 1px solid var(--line);
  background: var(--cloud); color: var(--ink); font-weight: 600; transition: border-color .15s ease, box-shadow .15s ease, transform .15s ease; }}
.stButton > button:hover {{ border-color: var(--accent); box-shadow: 0 6px 16px -8px rgba(118,86,214,.55); }}
.stButton > button:active {{ transform: translateY(1px); }}
.stButton > button[kind="primary"] {{ background: linear-gradient(135deg, #F48FB8 0%, #A98CF0 60%, #7FB2F5 100%); color: #fff; border: 0; box-shadow: 0 10px 22px -12px rgba(118,86,214,.8); }}
.stButton > button[kind="primary"]:hover {{ filter: brightness(1.05); }}
button:focus-visible, input:focus-visible, [role="tab"]:focus-visible {{ outline: 3px solid rgba(155,123,234,.5) !important; outline-offset: 2px; }}
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"] {{ border-radius: 12px !important; background: var(--cloud) !important; }}
[role="radiogroup"][aria-orientation="horizontal"] label, [data-testid="stRadio"] [role="radiogroup"] {{ gap: 6px; }}

/* brand header */
.wf-brand {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; margin: 0 0 1.1rem; }}
.wf-brand-left {{ display: flex; align-items: center; gap: 14px; }}
.wf-logo {{ width: 48px; height: 48px; border-radius: 16px; background: linear-gradient(135deg, #FFD3E4 0%, #D9C9FF 55%, #C4E0FF 100%); display: grid; place-items: center; font-size: 25px; box-shadow: var(--shadow); }}
.wf-brand h1 {{ font-size: 1.75rem; margin: 0; padding: 0; line-height: 1.1; }}
.wf-brand p {{ margin: 2px 0 0; color: var(--slate); font-size: .95rem; }}
.wf-source {{ font-size: .82rem; color: var(--slate); background: var(--cloud); border: 1px solid var(--line); border-radius: 999px; padding: 7px 14px; white-space: nowrap; box-shadow: var(--shadow); }}

/* weather cards */
.wf-hero {{ display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); gap: 16px; margin: .4rem 0 1rem; }}
.wf-now {{ border-radius: 28px; padding: 26px 28px; border: 1px solid var(--line); display: flex; gap: 22px; align-items: center;
  background: linear-gradient(150deg, var(--tint, #DDEEFF) 0%, #FFFFFF 75%); box-shadow: var(--shadow); }}
.wf-now-icon {{ font-size: 64px; line-height: 1; }}
.wf-temp {{ font-size: 3.6rem; font-weight: 800; letter-spacing: -0.04em; line-height: 1; }}
.wf-cond {{ font-size: 1.08rem; font-weight: 600; margin-top: 6px; }}
.wf-sub {{ color: var(--slate); font-size: .95rem; margin-top: 2px; }}
.wf-tiles {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
.wf-tile {{ background: var(--cloud); border: 1px solid var(--line); border-radius: 20px; padding: 14px 16px; box-shadow: var(--shadow); }}
.wf-tile:nth-child(1) {{ background: linear-gradient(160deg, #FFFFFF 0%, var(--skysoft) 160%); }}
.wf-tile:nth-child(2) {{ background: linear-gradient(160deg, #FFFFFF 0%, var(--lav) 160%); }}
.wf-tile:nth-child(3) {{ background: linear-gradient(160deg, #FFFFFF 0%, var(--skysoft) 160%); }}
.wf-tile:nth-child(4) {{ background: linear-gradient(160deg, #FFFFFF 0%, var(--pink) 160%); }}
.wf-tile .v {{ font-size: 1.45rem; font-weight: 700; letter-spacing: -0.02em; }}
.wf-tile .l {{ color: var(--slate); font-size: .85rem; }}
.wf-win {{ background: var(--cloud); border: 1px solid var(--line); border-radius: 24px; padding: 20px 22px; height: 100%; box-shadow: var(--shadow); }}
.wf-win h4 {{ margin: 0 0 2px; font-size: 1.05rem; }}
.wf-win .when {{ color: var(--slate); font-size: .88rem; margin-bottom: 12px; }}
.wf-win .big {{ font-size: 1.9rem; font-weight: 800; letter-spacing: -0.03em; }}
.wf-meter {{ height: 8px; border-radius: 999px; background: var(--lav); overflow: hidden; margin: 8px 0 4px; }}
.wf-meter > i {{ display: block; height: 100%; border-radius: 999px; background: linear-gradient(90deg, #7FB2F5, #A98CF0); }}
.wf-row {{ display: flex; justify-content: space-between; font-size: .9rem; color: var(--slate); margin-top: 8px; }}
.wf-row b {{ color: var(--ink); font-weight: 600; white-space: nowrap; }}
.wf-win + .wf-win {{ margin-top: 12px; }}

/* outfit card */
.wf-outfit {{ background: var(--cloud); border: 1px solid var(--line); border-radius: 28px; padding: 24px 26px; box-shadow: var(--shadow); }}
.wf-outfit h3 {{ margin: 0 0 14px; font-size: 1.25rem; }}
.wf-pieces {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }}
.wf-piece {{ background: var(--lav); border-radius: 18px; padding: 14px 16px; }}
.wf-piece:nth-child(1) {{ background: var(--pink); }}
.wf-piece:nth-child(3) {{ background: var(--skysoft); }}
.wf-piece .k {{ color: var(--slate); font-size: .85rem; margin-bottom: 4px; }}
.wf-piece .t {{ font-weight: 600; }}
.wf-chips {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }}
.wf-chip {{ border-radius: 999px; padding: 7px 13px; font-size: .9rem; font-weight: 600; border: 1px solid var(--line); background: var(--cloud); color: var(--slate); }}
.wf-chip.on {{ background: var(--lav); border-color: #D3C4F5; color: var(--accent-deep); }}
.wf-why {{ margin-top: 16px; color: var(--slate); font-size: .95rem; max-width: 70ch; }}

@media (max-width: 800px) {{
  .wf-hero {{ grid-template-columns: 1fr; }} .wf-pieces {{ grid-template-columns: 1fr; }}
  .wf-now {{ padding: 20px; }} .wf-temp {{ font-size: 3rem; }} .wf-brand {{ flex-direction: column; align-items: flex-start; }}
}}
@media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; animation: none !important; }} }}
"""


def esc(x: Any) -> str:
    return html.escape(str(x), quote=True)


def condition_icon(condition: str, is_day: bool = True) -> str:
    low = (condition or "").lower()
    for rx, icon in ((r"thunder", "⛈️"), (r"snow|sleet|ice", "❄️"), (r"rain|drizzle|shower", "🌧️"), (r"fog|mist|haze", "🌫️"),
                     (r"overcast", "☁️"), (r"partly|cloud", "⛅" if is_day else "☁️"), (r"sunny|clear", "☀️" if is_day else "🌙")):
        if re.search(rx, low):
            return icon
    return "🌡️"


def tint_for(feels_c: float) -> str:
    """Soft pastel card tint that follows how it feels: sky blue, lavender, pink, peach."""
    return "#DDEEFF" if feels_c < 18 else "#ECE4FF" if feels_c < 27 else "#FFE3EE" if feels_c < 34 else "#FFE9D6"


def brand_header(source: str) -> str:
    return (f'<div class="wf-brand"><div class="wf-brand-left"><div class="wf-logo" aria-hidden="true">🌤️</div>'
            f'<div><h1>WeatherFit AI</h1><p>Dress for the weather you will actually meet, wherever you are heading.</p></div></div>'
            f'<div class="wf-source">Weather from {esc(source)}</div></div>')


def current_weather_card(cur: dict[str, Any]) -> str:
    city = str(cur.get("city", "")).split(",")[0]
    tiles = [("💧", f"{cur.get('humidity', 0)}%", "Humidity"), ("🌬️", f"{float(cur.get('wind_kph', 0)):.0f} km/h", "Wind"),
             ("☔", f"{float(cur.get('precip_mm', 0)):.1f} mm", "Rain right now"), ("🌡️", f"{float(cur.get('feelslike_c', 0)):.0f}°C", "Feels like")]
    tile_html = "".join(f'<div class="wf-tile"><div class="v">{icon} {esc(v)}</div><div class="l">{esc(label)}</div></div>' for icon, v, label in tiles)
    return (f'<div class="wf-hero"><div class="wf-now" style="--tint:{tint_for(float(cur.get("feelslike_c", 20)))}">'
            f'<div class="wf-now-icon" aria-hidden="true">{condition_icon(cur.get("condition", ""), bool(cur.get("is_day", True)))}</div>'
            f'<div><div class="wf-temp">{float(cur.get("temp_c", 0)):.0f}°</div><div class="wf-cond">{esc(cur.get("condition", ""))} in {esc(city)}</div>'
            f'<div class="wf-sub">Updated {esc(cur.get("last_updated") or cur.get("localtime", ""))} local time</div></div></div>'
            f'<div class="wf-tiles">{tile_html}</div></div>')


def window_card(title: str, w: Any) -> str:
    same = round(w.feels_min) == round(w.feels_max)
    feels = f"{w.feels_min:.0f}°C" if same else f"{w.feels_min:.0f}–{w.feels_max:.0f}°C"
    rain = max(0.0, min(100.0, float(w.rain_chance_max)))
    partial = '<div class="wf-row"><span>The forecast covers only part of this window.</span></div>' if getattr(w, "partial", False) else ""
    return (f'<div class="wf-win"><h4>{esc(title)}</h4><div class="when">{esc(w.main_condition)}, {esc(w.start[5:])} to {esc(w.end[11:])}</div>'
            f'<div class="big">{esc(feels)}</div><div class="when">Feels like</div>'
            f'<div class="wf-row"><span>Chance of rain</span><b>{rain:.0f}%</b></div>'
            f'<div class="wf-meter" role="img" aria-label="Chance of rain {rain:.0f} percent"><i style="width:{rain:.0f}%"></i></div>'
            f'<div class="wf-row"><span>Wind up to</span><b>{w.wind_max_kph:.0f} km/h</b></div>'
            f'<div class="wf-row"><span>Humidity</span><b>{w.humidity_avg:.0f}%</b></div>{partial}</div>')


def outfit_card(title: str, top: str, bottom: str, footwear: str, reason: str, carry: list[Any]) -> str:
    pieces = "".join(f'<div class="wf-piece"><div class="k">{k}</div><div class="t">{esc(v[:1].upper() + v[1:])}</div></div>'
                     for k, v in (("Top", top), ("Bottom", bottom), ("Footwear", footwear)))
    chips = "".join(f'<span class="wf-chip{" on" if c.needed else ""}" title="{esc(c.reason)}">{"✓ " if c.needed else ""}{esc(c.item if c.needed else "No " + c.item.lower())}</span>'
                    for c in carry)
    return (f'<div class="wf-outfit"><h3>{esc(title)}</h3><div class="wf-pieces">{pieces}</div><div class="wf-chips">{chips}</div>'
            f'<div class="wf-why">{esc(reason)}</div></div>')


def apply_theme() -> None:
    import streamlit as st
    from ui_dashboard import DASH_CSS   # imported here: ui_dashboard itself imports helpers from this module
    st.markdown(f"<style>{CSS}\n{DASH_CSS}</style>", unsafe_allow_html=True)


def html_block(markup: str) -> None:
    import streamlit as st
    st.markdown(markup, unsafe_allow_html=True)
