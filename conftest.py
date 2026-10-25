"""Shared fixtures for the wardrobe / outfit-memory tests (temporary database + data folder; no network)."""
import io
from datetime import date

import pytest
from PIL import Image

import wardrobe as W
from db import Database
from outfit_models import WeatherNeeds

TODAY = date(2026, 10, 24)
MILD = WeatherNeeds(feels_min=24, feels_max=29, feels_avg=27, temp_max=29, rain_chance_max=5, uv_max=7, place="Gwalior", condition="Sunny")
PROFILE = {"occasion": "Daily casual", "style": "Casual", "budget": "Mid-range", "colours": "", "avoid": "", "gender": "Any", "comfort": "Balanced"}


@pytest.fixture()
def data_dir(tmp_path):
    return tmp_path / "data"


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "test.db")
    d.migrate()
    return d


def png_bytes(colour=(30, 40, 200), size=(120, 160)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", size, colour).save(b, "PNG")
    return b.getvalue()


@pytest.fixture()
def seed(db, data_dir):
    """seed("shirt", "blue", occasion="casual,party") -> item id. No image unless image=True."""
    counter = {"n": 0}

    def _add(category, colour, image=False, **kw):
        counter["n"] += 1  # unique, deterministic pixel colour per item (identical photos are rejected as duplicates)
        fields = {"category": category, "colour": colour, "occasion": "casual,college,travel,party,office,date", **kw}
        return W.add_item(db, fields, png_bytes((counter["n"] * 13 % 256, counter["n"] * 7 % 256, 160)) if image else None, data_dir=data_dir)
    return _add


@pytest.fixture()
def basic_wardrobe(seed):
    ids = {k: seed(*k) for k in [("shirt", "blue"), ("t-shirt", "white"), ("jeans", "navy"), ("chinos", "beige"), ("sneakers", "white"), ("loafers", "brown")]}
    return ids
