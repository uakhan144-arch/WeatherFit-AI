"""The Streamlit side of the live-camera try-on (needs streamlit's AppTest; the camera itself is browser-only and not tested here)."""
import io
import os

from PIL import Image, ImageDraw
from streamlit.testing.v1 import AppTest

import wardrobe as W
from db import Database

AR_MODE = "📹 Live camera (AR)"


def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("WEATHERFIT_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("WEATHERFIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WEATHERAPI_KEY", "")
    monkeypatch.setenv("WEATHER_PROVIDER", "openmeteo")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.delenv("TRYON_ADAPTER", raising=False)


def _app():
    return AppTest.from_file(os.path.join(os.path.dirname(__file__), "app.py"), default_timeout=60)


def _shirt_png() -> bytes:
    im = Image.new("RGB", (400, 450), (250, 250, 250))
    ImageDraw.Draw(im).polygon([(110, 40), (290, 40), (390, 130), (340, 190), (300, 150), (300, 420), (100, 420), (100, 150), (60, 190), (10, 130)], fill=(40, 80, 170))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def test_default_mode_is_still_the_2d_preview(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    at = _app().run()
    assert not at.exception
    assert at.radio(key="tryon_mode").value.startswith("2-D preview")
    assert any("approximate 2-D fit preview" in c.value for c in at.caption)


def test_ar_mode_opens_without_error_and_tracking_is_opt_in(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    at = _app().run()
    at.radio(key="tryon_mode").set_value(AR_MODE).run()
    assert not at.exception
    assert any("lightweight 2-D camera overlay" in c.value for c in at.caption)
    assert at.checkbox(key="ar_tracking").value is False
    assert not any("approximate 2-D fit preview" in c.value for c in at.caption)   # the photo/avatar UI is not shown in AR mode


def test_ar_mode_with_a_wardrobe_item_does_not_warn(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    db = Database(tmp_path / "t.db")
    db.migrate()
    shirt_id = W.add_item(db, {"category": "shirt", "colour": "blue"}, _shirt_png(), data_dir=tmp_path / "data")
    at = _app().run()
    at.radio(key="tryon_mode").set_value(AR_MODE).run()
    at.selectbox(key="tryon_pick_top").select(shirt_id).run()
    assert not at.exception
    assert not at.warning
