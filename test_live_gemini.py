"""LIVE Gemini tests - skipped unless you opt in.

Requirements: GEMINI_API_KEY in .env (or the environment), internet access, google-genai installed, and quota.
Run from the project folder (PowerShell):
    $env:WEATHERFIT_LIVE_TESTS = "1"
    python -m pytest tests_live -v -s
These call the real API and may cost quota. The normal test suite never does.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
pytestmark = pytest.mark.skipif(os.getenv("WEATHERFIT_LIVE_TESTS") != "1" or not os.getenv("GEMINI_API_KEY"),
                                reason="set WEATHERFIT_LIVE_TESTS=1 and GEMINI_API_KEY to run live Gemini tests")


def test_live_text_reply():
    from ai_features import gemini_text
    assert gemini_text("Reply with the single word: ready").strip()


def test_live_label_suggestion_returns_valid_vocabulary():
    import io
    from PIL import Image
    from ai_features import gemini_label_item
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), (30, 60, 200)).save(buf, "JPEG")
    labels = gemini_label_item(buf.getvalue())
    assert isinstance(labels, dict) and "category" in labels


def test_live_image_generation_with_reference():
    import io
    from PIL import Image
    from ai_features import generate_outfit_image
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), (30, 60, 200)).save(buf, "JPEG")
    data = generate_outfit_image("Full-body photo of a person wearing a plain blue shirt, jeans and white sneakers, neutral background.", [buf.getvalue()])
    Image.open(io.BytesIO(data)).verify()
