"""Theme helpers return safe, well-formed HTML (no Streamlit needed)."""
from datetime import datetime

import ui_theme as T
from openmeteo_service import OpenMeteoClient
from plan_service import build_plan
from stylist import build_trip_advice
from tests_support_openmeteo import OMSession, make_forecast


def _plan():
    c = OpenMeteoClient(session=OMSession({"bhopal": make_forecast(), "indore": make_forecast(rain=lambda d, h: 80)}), sleep=lambda x: None)
    now = datetime(2026, 10, 25, 10, 15)
    return build_plan(c, "Bhopal", "Indore", now, 3.0, 3.0, now)


def test_everything_dynamic_is_escaped():
    cur = {"city": "<script>x</script>, India", "condition": "<b>Rain</b>", "temp_c": 20, "feelslike_c": 20, "humidity": 50, "wind_kph": 5, "precip_mm": 0}
    out = T.current_weather_card(cur)
    assert "<script>" not in out and "<b>Rain" not in out and "&lt;script&gt;" in out


def test_cards_contain_the_real_numbers():
    plan = _plan()
    card = T.window_card("When you arrive", plan.dest_w)
    assert "80%" in card and "width:80%" in card and "When you arrive" in card
    adv = build_trip_advice(plan.origin_w, plan.dest_w, "Travel", "Casual", "Mid-range", "", "", [])
    out = T.outfit_card(adv.title, adv.top, adv.bottom, adv.footwear, adv.outfit_reason, adv.carry)
    assert "✓ Umbrella" in out and "Footwear" in out and out.count('class="wf-piece"') == 3


def test_icons_tints_and_theme_files():
    assert T.condition_icon("Slight rain") == "🌧️" and T.condition_icon("Clear sky", False) == "🌙" and T.condition_icon("???") == "🌡️"
    assert T.tint_for(10) != T.tint_for(30)
    css = open(".streamlit/config.toml", encoding="utf-8").read()
    assert 'base = "light"' in css and "#FAF8FF" in css
    assert "prefers-reduced-motion" in T.CSS and "focus-visible" in T.CSS
