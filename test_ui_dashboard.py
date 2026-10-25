"""Dashboard builders return safe HTML built only from real data (no Streamlit needed)."""
from datetime import datetime

import ui_dashboard as D
from openmeteo_service import OpenMeteoClient
from outfit_models import OutfitPiece, Recommendation
from tests_support_openmeteo import OMSession, make_forecast


def _fc():
    c = OpenMeteoClient(session=OMSession({"indore": make_forecast(temp=lambda d, h: 20 + h // 2, rain=lambda d, h: 40)}), sleep=lambda x: None)
    return c.get_forecast("Indore", days=3)


def test_hourly_slots_come_from_the_forecast():
    fc = _fc()
    slots = D.hourly_slots(fc, datetime(2026, 10, 25, 10, 15))
    assert slots[0]["label"] == "Now" and slots[0]["now"]
    assert [s["label"] for s in slots[1:]] == ["1 PM", "4 PM", "7 PM", "10 PM"]
    # each later slot equals the real forecast row for that hour
    row = next(r for r in fc["hours"] if r["time"].endswith("T13:00") and r["time"].startswith("2026-10-25"))
    assert slots[1]["temp"] == row["temp_c"]


def test_hourly_slots_stop_at_the_end_of_the_forecast():
    fc = _fc()
    fc["hours"] = fc["hours"][:14]   # forecast ends early: no invented hours
    assert len(D.hourly_slots(fc, datetime(2026, 10, 25, 10, 15))) <= 2


def test_location_card_uses_real_numbers_and_escapes():
    fc = _fc()
    fc["current"]["city"] = "<b>Indore</b>, Madhya Pradesh, India"
    html = D.location_card(fc, "Wear <i>light</i> layers")
    assert "&lt;b&gt;Indore" in html and "<b>Indore</b>" not in html and "&lt;i&gt;light" in html
    assert "40%" in html and "Today, 25 Oct 2026" in html
    assert "Press <b>Get started</b>" in D.location_card(None)


def test_top_chip_without_and_with_weather():
    assert "Press Get started" in D.top_weather_chip(None)
    cur = {"city": "Indore, India", "temp_c": 28.2, "condition": "Partly cloudy", "is_day": True}
    chip = D.top_weather_chip(cur)
    assert "28°C" in chip and "Partly cloudy" in chip and "Indore" in chip


def _rec():
    pieces = [OutfitPiece("top", "t-shirt", "white", "White T-Shirt", 1, owned=True), OutfitPiece("outer", "jacket", "olive", "Olive Jacket", 2, owned=True),
              OutfitPiece("bottom", "chinos", "beige", "", 3, owned=False), OutfitPiece("footwear", "sneakers", "white", "", 4, owned=True)]
    return Recommendation("wardrobe", pieces, [OutfitPiece("accessory", "watch", "black", "", 5, owned=True)], score=3.7)


def test_outfit_rows_ring_and_options():
    rec = _rec()
    rows = D.rec_rows(rec)
    assert [r[0] for r in rows] == ["Top", "Outer", "Bottom", "Footwear", "Accessories"]
    assert rows[2][3].startswith("Suggestion") and rows[0][3] == "In your wardrobe"
    html = D.outfit_details("From Your Wardrobe", "Chosen for Indore", rows, rec.score, best=True)
    assert "3.7" in html and "Best Match" in html and "--p:74" in html
    assert "wf-ring" not in D.outfit_details("x", "y", rows, None)
    assert "Match 3.7" in D.option_row(rec) and "White T-Shirt" in D.option_row(rec)


def test_look_visual_without_wardrobe_makes_no_claims():
    html = D.look_visual(None, None)
    assert "Add clothes to My Wardrobe" in html and "data:image" not in html
    assert "data:image" in D.look_visual(_rec(), None)   # a real collage of the pieces


def test_misc_builders():
    assert "Dress Smart" in D.hero_markup() and "Live data from WeatherAPI.com" in D.feature_strip("WeatherAPI.com")
    assert D.nav_active_css("Try-On").count("st-key-nav_tryon") == 2
    assert D.data_uri(b"not an image", 100) == "" and D.data_uri(None) == ""
    assert D.clock_label(datetime(2026, 1, 1, 0)) == "12 AM" and D.clock_label(datetime(2026, 1, 1, 13)) == "1 PM"
    assert "wf-opt" in D.alt_row({"title": "Rain-ready", "pieces": "Light jacket"})
    for page in D.PAGES:
        assert page in D.PAGE_KEYS and page in D.PAGE_ICONS
    assert D.DASH_CSS.count("{") == D.DASH_CSS.count("}")
