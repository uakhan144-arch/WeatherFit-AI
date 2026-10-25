"""Visual previews of a complete outfit.

1. ``build_collage`` - ALWAYS available (Pillow only): item-by-item tiles with labels. Wardrobe looks use your real photos;
   inspiration looks use colour swatches (or a dataset reference image when one exists).
2. ``generate_look_image`` - optional Gemini rendering of the whole look. Wardrobe photos are sent as visual references.
   If Gemini is unavailable / out of quota / returns no image, the collage is returned instead and the reason is shown.

Honesty rules baked into the captions: AI renderings are *inspiration*, not exact products, and fabric/pattern/colour
may not be reproduced exactly; only the collage of your own photos shows your real items.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from outfit_models import INSPIRATION_LABEL, INSPIRATION_NOTE, WARDROBE_LABEL, OutfitPiece, Recommendation, WeatherNeeds
from security import redact
from wardrobe import _REFERENCE_RGB, read_image_bytes

Generator = Callable[[str, list[bytes]], bytes]
TILE_W, TILE_H, CAPTION_H, PAD = 220, 250, 62, 12
AI_CAPTION_INSPIRATION = "AI-generated inspiration - not an exact product; fabric, pattern and colour may differ."
AI_CAPTION_WARDROBE = ("AI rendering that uses your clothing photos as references. It may not reproduce exact colours, patterns or fabrics - "
                       "the item collage shows your real pieces.")


@dataclass
class VisualResult:
    collage: bytes
    ai_image: bytes | None = None
    caption: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def used_ai(self) -> bool:
        return self.ai_image is not None


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans.ttf", "arial.ttf", "Arial.ttf", "segoeui.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _tile_image(piece: OutfitPiece, data_dir: Path | None) -> tuple[Image.Image, str]:
    """Return (tile image, source tag). Prefers the user's own photo, then a dataset reference, then a colour swatch."""
    for rel, tag in ((piece.image_path, "photo"), (piece.reference_path, "reference")):
        raw = read_image_bytes(rel, data_dir) if rel else None
        if raw:
            try:
                img = Image.open(io.BytesIO(raw)).convert("RGB")
                img.thumbnail((TILE_W, TILE_H))
                return img, tag
            except (UnidentifiedImageError, OSError):
                continue
    rgb = _REFERENCE_RGB.get(piece.colour, (200, 200, 200))
    img = Image.new("RGB", (TILE_W, TILE_H), rgb)
    d = ImageDraw.Draw(img)
    lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    d.text((10, TILE_H // 2 - 8), piece.category.upper(), fill=(0, 0, 0) if lum > 140 else (255, 255, 255), font=_font(18))
    return img, "swatch"


def build_collage(rec: Recommendation, data_dir: Path | None = None, title: str | None = None) -> bytes:
    """PNG collage: header (label), one labelled tile per piece, footer note. Never needs network access."""
    pieces = rec.all_pieces
    cols = min(4, max(1, len(pieces)))
    rows = (len(pieces) + cols - 1) // cols
    header, footer = 44, 40
    w = PAD + cols * (TILE_W + PAD)
    h = header + rows * (TILE_H + CAPTION_H + PAD) + footer
    canvas = Image.new("RGB", (w, h), (250, 248, 255))
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, 0, w, header], fill=(139, 79, 232))
    d.text((PAD, 12), title or (WARDROBE_LABEL if rec.kind == "wardrobe" else f"{INSPIRATION_LABEL} (AI suggestion)"), fill="white", font=_font(18))
    for i, pc in enumerate(pieces):
        x = PAD + (i % cols) * (TILE_W + PAD)
        y = header + PAD + (i // cols) * (TILE_H + CAPTION_H + PAD)
        img, tag = _tile_image(pc, data_dir)
        canvas.paste(img, (x + (TILE_W - img.width) // 2, y + (TILE_H - img.height) // 2))
        d.rectangle([x, y, x + TILE_W, y + TILE_H], outline=(210, 214, 224))
        tag_text = {"photo": "your photo", "reference": "dataset reference", "swatch": "colour idea"}[tag]
        d.text((x, y + TILE_H + 4), pc.label[:26], fill=(20, 20, 20), font=_font(14))
        d.text((x, y + TILE_H + 24), f"{pc.role} - {tag_text}", fill=(90, 96, 110), font=_font(11))
        d.text((x, y + TILE_H + 40), "in your wardrobe" if pc.owned else "suggestion - not in wardrobe", fill=(30, 120, 60) if pc.owned else (170, 90, 20), font=_font(11))
    note = "Items from your wardrobe." if rec.kind == "wardrobe" else INSPIRATION_NOTE
    d.text((PAD, h - footer + 12), note, fill=(90, 96, 110), font=_font(12))
    out = io.BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue()


def build_prompt(rec: Recommendation, needs: WeatherNeeds | None, profile: dict, extra: str = "") -> str:
    lines = [f"- {pc.role}: {pc.colour} {pc.category}" + (f" ({pc.pattern})" if pc.pattern and pc.pattern != "solid" else "") for pc in rec.pieces]
    lines += [f"- accessory: {pc.colour} {pc.category}" for pc in rec.accessories]
    weather = (f"Weather: {needs.condition}, feels like {needs.feels_avg:.0f}°C. " if needs else "")
    gender = profile.get("gender", "")
    who = {"men": "a man", "women": "a woman"}.get(str(gender).lower(), "a person")
    refs = (" Match the attached clothing photos as closely as you can for colour, cut and pattern." if rec.kind == "wardrobe" else "")
    return (f"Create a photorealistic full-body fashion image of {who} wearing this COMPLETE outfit, head to toe, footwear and accessories clearly visible, "
            f"plain neutral background, no text, no logos, no brand names.{refs}\n{weather}Occasion: {profile.get('occasion', 'daily')}; "
            f"style: {profile.get('style', 'casual')}.\nItems:\n" + "\n".join(lines) + (f"\n{extra}" if extra else ""))


def _looks_like_image(data: bytes) -> bool:
    try:
        Image.open(io.BytesIO(data)).verify()
        return True
    except Exception:
        return False


def _friendly_error(exc: Exception) -> str:
    text = redact(exc)
    low = text.lower()
    if any(k in low for k in ("429", "quota", "resource_exhausted", "rate limit")):
        return "Gemini image generation is rate-limited or out of quota right now."
    if any(k in low for k in ("api_key", "api key", "gemini_api_key", "permission", "401", "403")):
        return "Gemini image generation is not available (missing or invalid API key / no access to the image model)."
    if "timeout" in low or "timed out" in low:
        return "Gemini image generation timed out."
    return f"Gemini image generation failed ({text[:140]})."


def generate_look_image(rec: Recommendation, needs: WeatherNeeds | None, profile: dict, generator: Generator | None,
                        data_dir: Path | None = None, extra: str = "") -> VisualResult:
    """Collage always; plus an AI rendering when ``generator`` works. Never raises - failures become ``notes``."""
    collage = build_collage(rec, data_dir)
    result = VisualResult(collage=collage)
    result.caption = ("Item collage of your wardrobe pieces." if rec.kind == "wardrobe" else "Item collage of the suggested look (colour swatches).")
    if generator is None:
        result.notes.append("Image generation isn't configured, so you're seeing the item collage. Add GEMINI_API_KEY to .env to enable AI renderings.")
        return result
    refs: list[bytes] = []
    if rec.kind == "wardrobe":
        for pc in rec.pieces:
            raw = read_image_bytes(pc.image_path, data_dir)
            if raw:
                refs.append(raw)
    try:
        data = generator(build_prompt(rec, needs, profile, extra), refs)
        if not data or not _looks_like_image(data):
            raise RuntimeError("The image model did not return a usable image.")
        result.ai_image = data
        result.caption = AI_CAPTION_WARDROBE if rec.kind == "wardrobe" else AI_CAPTION_INSPIRATION
    except Exception as exc:
        result.notes.append(_friendly_error(exc) + " Showing the item collage instead.")
    return result
