"""Virtual try-on + avatar tests (no network, no models)."""
import io
import os
import sys
import types

import pytest
from PIL import Image, ImageChops, ImageDraw
from streamlit.testing.v1 import AppTest

import tryon_engine as T
import wardrobe as W
from db import Database


def png(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def shirt_photo(colour=(40, 80, 170), bg=(250, 250, 250)) -> Image.Image:
    im = Image.new("RGB", (400, 450), bg)
    ImageDraw.Draw(im).polygon([(110, 40), (290, 40), (390, 130), (340, 190), (300, 150), (300, 420), (100, 420), (100, 150), (60, 190), (10, 130)], fill=colour)
    return im


def pants_photo(colour=(190, 170, 130)) -> Image.Image:
    im = Image.new("RGB", (300, 500), (245, 245, 245))
    ImageDraw.Draw(im).polygon([(40, 20), (260, 20), (270, 480), (160, 480), (150, 150), (140, 480), (30, 480)], fill=colour)
    return im


def garment(slot, cat, img):
    return T.make_garment(slot, cat, png(img))


# ------------------------------------------------------------------ avatar
def test_avatar_size_and_builds_differ():
    img, a = T.make_avatar("Tan", "Black", "Regular")
    assert img.size == T.CANVAS and a.neck_y < a.waist_y < a.hip_y < a.knee_y < a.ankle_y < a.feet_y
    assert T.make_avatar(build="Broad")[1].shoulder_half > T.make_avatar(build="Slim")[1].shoulder_half


def test_avatar_profile_is_validated_and_persisted(tmp_path):
    db = Database(tmp_path / "t.db")
    db.migrate()
    assert T.load_avatar_profile(db) == {"skin": "Medium", "hair": "Dark brown", "build": "Regular"}
    saved = T.save_avatar_profile(db, {"skin": "Deep", "hair": "<script>", "build": "Broad"})
    assert saved == {"skin": "Deep", "hair": "Dark brown", "build": "Broad"} and T.load_avatar_profile(db) == saved
    db.set_setting(T.AVATAR_SETTING, "not json")
    assert T.load_avatar_profile(db)["skin"] == "Medium"


# ------------------------------------------------------------------ cut-out
def test_cutout_removes_plain_background():
    cut, ok, note = T.cut_out_garment(shirt_photo())
    assert ok and not note and cut.mode == "RGBA"
    assert cut.getpixel((2, 2))[3] < 40                                    # corner is transparent
    assert cut.getpixel((cut.width // 2, cut.height // 2))[3] > 200       # shirt body is opaque


def test_cutout_fails_honestly_when_garment_matches_background():
    _, ok, note = T.cut_out_garment(shirt_photo(colour=(250, 250, 250)))
    assert not ok and "same colour" in note


def test_cutout_fails_on_busy_background():
    import random
    random.seed(1)
    noisy = Image.effect_noise((300, 300), 90).convert("RGB")
    _, ok, note = T.cut_out_garment(noisy)
    assert not ok and note


def test_cutout_keeps_real_transparency():
    rgba = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(rgba).rectangle([50, 50, 150, 150], fill=(200, 0, 0, 255))
    cut, ok, _ = T.cut_out_garment(rgba)
    assert ok and cut.size == (101, 101)


def test_make_garment_rejects_non_images():
    with pytest.raises(T.TryOnError):
        T.make_garment("top", "shirt", b"not an image")


# ------------------------------------------------------------------ rendering
def test_render_without_garments_returns_the_person():
    av, a = T.make_avatar()
    out, notes = T.render_tryon(av, a, [])
    assert notes == [] and ImageChops.difference(out, av).getbbox() is None


def test_garments_never_cover_the_face_even_when_moved_up():
    av, a = T.make_avatar()
    g = [garment("top", "shirt", shirt_photo())]
    for dy in (0, -10):
        out, _ = T.render_tryon(av, a, g, dy=dy, scale=1.4)
        neck = int(a.neck_y)
        above_new = out.crop((0, 0, out.width, neck))
        above_old = av.crop((0, 0, av.width, neck))
        assert ImageChops.difference(above_new, above_old).getbbox() is None     # nothing drawn above the neck line
    changed = ImageChops.difference(T.render_tryon(av, a, g)[0], av).getbbox()
    assert changed and changed[1] >= int(a.neck_y) - 1


def test_top_is_drawn_over_bottom_and_colours_are_visible():
    av, a = T.make_avatar()
    out, notes = T.render_tryon(av, a, [garment("bottom", "chinos", pants_photo((0, 160, 0))), garment("top", "shirt", shirt_photo((200, 0, 0)))])
    assert not notes
    assert out.getpixel((int(a.centre_x), int(a.neck_y) + 60))[0] > 150       # red shirt on the chest
    assert out.getpixel((int(a.centre_x) - 60, int(a.knee_y)))[1] > 120       # green trousers on the legs


def test_bottoms_and_shoes_are_skipped_on_a_waist_up_photo():
    photo = Image.new("RGB", (600, 800), (200, 200, 200))
    a = T.photo_anchors(600, 800, T.PHOTO_PRESETS[1])
    _, notes = T.render_tryon(photo, a, [garment("bottom", "jeans", pants_photo()), garment("footwear", "sneakers", shirt_photo()), garment("top", "shirt", shirt_photo())])
    assert sum("outside your photo" in n for n in notes) == 2


def test_uncut_garment_is_flagged():
    av, a = T.make_avatar()
    _, notes = T.render_tryon(av, a, [garment("top", "shirt", shirt_photo(colour=(250, 250, 250)))])
    assert any("could not be cut out" in n for n in notes)


def test_guides_draw_lines():
    av, a = T.make_avatar()
    assert ImageChops.difference(T.render_tryon(av, a, [], guides=True)[0], av).getbbox() is not None


def test_photo_anchors_follow_sliders():
    a1, a2 = T.photo_anchors(500, 1000, neck_pct=20), T.photo_anchors(500, 1000, neck_pct=30)
    assert a2.neck_y > a1.neck_y and a1.neck_y < a1.waist_y < a1.hip_y < a1.ankle_y
    assert T.photo_anchors(500, 1000, shoulder_pct=40).shoulder_half == 100   # 40% of 500 px is the full shoulder width


# ------------------------------------------------------------------ private photo
def test_person_photo_save_load_delete(tmp_path):
    rel = T.save_person_photo(png(Image.new("RGB", (80, 120), (9, 9, 9))), tmp_path)
    assert rel == "avatar/person.jpg" and T.load_person_photo(tmp_path)
    assert T.delete_person_photo(tmp_path) and T.load_person_photo(tmp_path) is None
    assert not T.delete_person_photo(tmp_path)


def test_person_photo_rejects_garbage(tmp_path):
    with pytest.raises(T.TryOnError):
        T.save_person_photo(b"hello", tmp_path)
    assert T.load_person_photo(tmp_path) is None


# ------------------------------------------------------------------ optional trained model
def test_adapter_spec_is_validated():
    for bad in ("", "os.system", "a b:c", "x:y;import os", "../x:y"):
        with pytest.raises(T.TryOnError):
            T.load_adapter(bad)


def test_adapter_missing_module_and_function():
    with pytest.raises(T.TryOnError, match="Could not load"):
        T.load_adapter("no_such_module_xyz:run")
    with pytest.raises(T.TryOnError, match="Could not load"):
        T.load_adapter("tryon_engine:nope")


def test_adapter_runs_and_failures_are_readable(monkeypatch):
    mod = types.ModuleType("fake_tryon_model")
    mod.ok = lambda person, garment, category: Image.new("RGB", person.size, (1, 2, 3))
    mod.boom = lambda person, garment, category: (_ for _ in ()).throw(ValueError("bad weights"))
    mod.wrong = lambda person, garment, category: "not an image"
    monkeypatch.setitem(sys.modules, "fake_tryon_model", mod)
    person, g = Image.new("RGB", (50, 80)), Image.new("RGB", (20, 20))
    assert T.run_adapter(T.load_adapter("fake_tryon_model:ok"), person, g, "shirt").getpixel((0, 0)) == (1, 2, 3)
    with pytest.raises(T.TryOnError, match="bad weights"):
        T.run_adapter(T.load_adapter("fake_tryon_model:boom"), person, g, "shirt")
    with pytest.raises(T.TryOnError, match="PIL.Image"):
        T.run_adapter(T.load_adapter("fake_tryon_model:wrong"), person, g, "shirt")


# ------------------------------------------------------------------ the real app
@pytest.fixture(autouse=True)
def _fresh_streamlit_caches():
    """The app caches its database handle per process; give every test (and the tests after them) a clean slate."""
    import streamlit as st
    st.cache_resource.clear()
    st.cache_data.clear()
    yield
    st.cache_resource.clear()
    st.cache_data.clear()


def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("WEATHERFIT_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("WEATHERFIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WEATHERAPI_KEY", "")
    monkeypatch.setenv("WEATHER_PROVIDER", "openmeteo")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.delenv("TRYON_ADAPTER", raising=False)


def _app():
    return AppTest.from_file(os.path.join(os.path.dirname(__file__), "app.py"), default_timeout=60)


def test_tab_exists_and_shows_avatar_with_empty_wardrobe(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    at = _app().run()
    assert not at.exception
    assert any("Virtual try-on" in t.label for t in at.tabs)
    assert any("approximate 2-D fit preview" in c.value for c in at.caption)
    assert any("no clothing photos" in i.value for i in at.info)


def test_tab_dresses_avatar_from_wardrobe_photos(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    db = Database(tmp_path / "t.db")
    db.migrate()
    shirt_id = W.add_item(db, {"category": "shirt", "colour": "blue"}, png(shirt_photo()), data_dir=tmp_path / "data")
    pants_id = W.add_item(db, {"category": "chinos", "colour": "beige"}, png(pants_photo()), data_dir=tmp_path / "data")
    at = _app().run()
    at.selectbox(key="tryon_pick_top").select(shirt_id)
    at.selectbox(key="tryon_pick_bottom").select(pants_id)
    at.run()
    assert not at.exception
    assert any(d.proto.label == "Download preview" for d in at.get("download_button"))
    assert not at.warning


def test_tab_survives_a_deleted_item_in_session(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    at = _app().run()
    at.session_state["tryon_pick_top"] = 999
    at.run()
    assert not at.exception
