"""Starts the real Streamlit script headlessly and checks every page of the dashboard renders without an exception."""
import os

import pytest
from streamlit.testing.v1 import AppTest

PAGE_TABS = {
    "Wardrobe": ("My wardrobe", "Outfit history"),
    "Style Guide": ("AI stylist chat", "Weather chat", "Rank outfit images", "Generate outfit image", "Upload my outfit"),
}


@pytest.fixture()
def app_test(tmp_path, monkeypatch):
    monkeypatch.setenv("WEATHERFIT_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("WEATHERFIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WEATHERAPI_KEY", "")
    monkeypatch.setenv("WEATHER_PROVIDER", "openmeteo")   # never touch the network in tests
    monkeypatch.setenv("GEMINI_API_KEY", "")
    return AppTest.from_file(os.path.join(os.path.dirname(__file__), "app.py"), default_timeout=30)


def test_home_renders_with_hero_and_empty_state(app_test):
    at = app_test.run()
    assert not at.exception
    html = " ".join(m.value for m in at.markdown)
    assert "Dress Smart" in html and "Recommended Outfit" in html and "Quick Actions" in html


@pytest.mark.parametrize("page", ["Try-On", "Wardrobe", "Style Guide", "Voice Assistant"])
def test_every_page_renders(app_test, page):
    app_test.session_state["nav_page"] = page
    at = app_test.run()
    assert not at.exception
    labels = [t.label for t in at.tabs]
    for want in PAGE_TABS.get(page, ()):
        assert any(want in l for l in labels), want


def test_empty_wardrobe_message_on_wardrobe_page(app_test):
    app_test.session_state["nav_page"] = "Wardrobe"
    at = app_test.run()
    assert any("empty" in i.value.lower() for i in at.info)
