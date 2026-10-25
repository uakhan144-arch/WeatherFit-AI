"""Virtual try-on preview and avatar (pure Pillow - no network, no new dependencies).

What this is: a **2-D fit preview**. Your garment photo is cut out from its plain background and placed on an avatar (or on
your own photo) using body anchor lines (neck, waist, hip, knee, ankle). It shows colours, proportions and layering; it does
NOT bend cloth, estimate your pose or guess how fabric drapes. The UI says so.

* The illustrated avatar knows its own geometry, so garments land exactly where they should.
* On a photo, the anchors come from a preset plus sliders you adjust; nothing is detected automatically.
* Nothing is ever drawn above the neck line, so garments never cover the face.
* A trained try-on model can be plugged in with ``TRYON_ADAPTER=your_module:your_function`` (see ``load_adapter``).
"""
from __future__ import annotations

import importlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from PIL import Image, ImageChops, ImageDraw, ImageFilter

import taxonomy as tx
import wardrobe as W
from db import Database

CANVAS = (600, 1200)                      # avatar size (width, height)
CUTOUT_MAX_SIDE = 500                     # garment photos are shrunk before cut-out (speed)
SKIN_TONES = {"Fair": (246, 214, 190), "Light": (236, 196, 160), "Medium": (205, 155, 115),
              "Tan": (176, 125, 88), "Brown": (141, 94, 66), "Deep": (96, 62, 44)}
HAIR_COLOURS = {"Black": (28, 24, 24), "Dark brown": (72, 48, 36), "Brown": (110, 76, 52), "Blonde": (206, 170, 98),
                "Grey": (170, 170, 175), "Auburn": (130, 55, 40)}
BUILDS = {"Slim": (100, 74, 84), "Regular": (112, 88, 98), "Broad": (126, 104, 114)}   # shoulder, waist, hip half-widths
PHOTO_PRESETS = ("Full body", "Upper body (waist up)")
SLOT_ORDER = ("footwear", "bottom", "top", "onepiece", "outer")   # drawing order, back to front
AVATAR_SETTING = "avatar_profile"
PERSON_PHOTO_REL = "avatar/person.jpg"
_ADAPTER_SPEC = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")

# vertical landmarks of the avatar (pixels on the 600 x 1200 canvas)
_NECK, _WAIST, _HIP, _KNEE, _ANKLE, _FEET = 272, 560, 650, 880, 1085, 1135


class TryOnError(RuntimeError):
    """Readable problem (bad image, adapter missing, ...)."""


@dataclass(frozen=True)
class Anchors:
    """Body landmark lines in pixels of the image the garments are drawn on."""
    width: int
    height: int
    centre_x: float
    neck_y: float
    waist_y: float
    hip_y: float
    knee_y: float
    ankle_y: float
    feet_y: float
    shoulder_half: float
    hip_half: float


@dataclass
class Garment:
    slot: str            # top | bottom | onepiece | outer | footwear
    category: str
    image: Image.Image   # RGBA, already cut out
    cutout_ok: bool = True


# ------------------------------------------------------------------ anchors
def _anchors_from_lines(width: int, height: int, centre_x: float, neck_y: float, ankle_y: float, shoulder_half: float) -> Anchors:
    """Place every landmark between ``neck_y`` and ``ankle_y`` using the avatar's body proportions."""
    span = (ankle_y - neck_y) / (_ANKLE - _NECK)
    at = lambda v: neck_y + (v - _NECK) * span          # noqa: E731
    return Anchors(width, height, centre_x, neck_y, at(_WAIST), at(_HIP), at(_KNEE), ankle_y, at(_FEET), shoulder_half, shoulder_half * 0.875)


def photo_anchors(width: int, height: int, preset: str = "Full body", neck_pct: float | None = None, shoulder_pct: float | None = None,
                  centre_pct: float = 50.0, ankle_pct: float | None = None) -> Anchors:
    """Anchors for the user's own photo. All percentages are of the photo size; defaults depend on the preset."""
    full = preset == PHOTO_PRESETS[0]
    neck = (neck_pct if neck_pct is not None else (20.0 if full else 32.0)) / 100 * height
    shoulders = (shoulder_pct if shoulder_pct is not None else (32.0 if full else 54.0)) / 100 * width
    if full:
        ankle = (ankle_pct if ankle_pct is not None else 92.0) / 100 * height
    else:   # waist-up photo: the hips sit near the bottom edge of the frame; lower body is outside the picture
        scale = (0.96 * height - neck) / (_HIP - _NECK)
        ankle = neck + (_ANKLE - _NECK) * scale
    ankle = max(ankle, neck + 50)
    return _anchors_from_lines(width, height, centre_pct / 100 * width, neck, ankle, shoulders / 2)


# ------------------------------------------------------------------ avatar
def clean_avatar_profile(raw: dict | None) -> dict[str, str]:
    raw = raw or {}
    return {"skin": raw.get("skin") if raw.get("skin") in SKIN_TONES else "Medium",
            "hair": raw.get("hair") if raw.get("hair") in HAIR_COLOURS else "Dark brown",
            "build": raw.get("build") if raw.get("build") in BUILDS else "Regular"}


def load_avatar_profile(db: Database) -> dict[str, str]:
    try:
        return clean_avatar_profile(json.loads(db.get_setting(AVATAR_SETTING, "{}") or "{}"))
    except (ValueError, TypeError):
        return clean_avatar_profile(None)


def save_avatar_profile(db: Database, profile: dict) -> dict[str, str]:
    clean = clean_avatar_profile(profile)
    db.set_setting(AVATAR_SETTING, json.dumps(clean))
    return clean


def make_avatar(skin: str = "Medium", hair: str = "Dark brown", build: str = "Regular") -> tuple[Image.Image, Anchors]:
    """Flat illustrated mannequin in a plain grey base t-shirt and shorts (so the figure is never undressed)."""
    p = clean_avatar_profile({"skin": skin, "hair": hair, "build": build})
    sk, hr = SKIN_TONES[p["skin"]], HAIR_COLOURS[p["hair"]]
    s, w, h = BUILDS[p["build"]]
    cx = CANVAS[0] // 2
    img = Image.new("RGB", CANVAS, (241, 243, 247))
    d = ImageDraw.Draw(img)
    base = (214, 218, 226)
    # legs and feet
    for sign in (-1, 1):
        d.polygon([(cx + sign * 4, _HIP - 10), (cx + sign * h, _HIP - 10), (cx + sign * (h - 22), _ANKLE), (cx + sign * 34, _ANKLE)], fill=sk)
        d.ellipse([cx + sign * 60 - 38, _ANKLE - 8, cx + sign * 60 + 38, _FEET + 12], fill=sk)
    # arms (behind the torso) and hands
    for sign in (-1, 1):
        d.line([(cx + sign * (s - 8), 292), (cx + sign * (s + 26), 640)], fill=sk, width=40)
        d.ellipse([cx + sign * (s + 26) - 22, 636, cx + sign * (s + 26) + 22, 684], fill=sk)
    # neck, head, hair, face
    d.rectangle([cx - 22, 225, cx + 22, _NECK + 6], fill=sk)
    d.ellipse([cx - 68, 58, cx + 68, 205], fill=hr)
    d.ellipse([cx - 60, 84, cx + 60, 236], fill=sk)
    d.pieslice([cx - 61, 80, cx + 61, 166], 180, 360, fill=hr)
    d.ellipse([cx - 28, 148, cx - 18, 158], fill=(60, 50, 50))
    d.ellipse([cx + 18, 148, cx + 28, 158], fill=(60, 50, 50))
    d.arc([cx - 20, 170, cx + 20, 202], 20, 160, fill=(150, 90, 90), width=3)
    # base t-shirt and base shorts
    d.polygon([(cx - s - 18, 280), (cx + s + 18, 280), (cx + w + 4, _WAIST), (cx + h + 2, _HIP + 20), (cx - h - 2, _HIP + 20), (cx - w - 4, _WAIST)], fill=base)
    d.polygon([(cx - s - 18, 280), (cx - s - 40, 410), (cx - s - 4, 420), (cx - s + 8, 300)], fill=base)
    d.polygon([(cx + s + 18, 280), (cx + s + 40, 410), (cx + s + 4, 420), (cx + s - 8, 300)], fill=base)
    d.polygon([(cx - h - 2, _HIP), (cx + h + 2, _HIP), (cx + h + 8, 790), (cx + 6, 790), (cx, 700), (cx - 6, 790), (cx - h - 8, 790)], fill=(188, 194, 206))
    return img, _anchors_from_lines(CANVAS[0], CANVAS[1], cx, _NECK, _ANKLE, s)


# ------------------------------------------------------------------ garments
def cut_out_garment(image: Image.Image, tolerance: int = 40) -> tuple[Image.Image, bool, str]:
    """Remove a plain background by flood-filling from the corners. Returns (RGBA crop, success, note).

    If the photo already has real transparency it is used as is. If the background is not plain (or is the same
    colour as the garment) the whole picture is returned with ``success=False`` so the UI can warn instead of guessing."""
    img = image.copy()
    img.thumbnail((CUTOUT_MAX_SIDE, CUTOUT_MAX_SIDE))
    if img.mode == "RGBA" and img.getchannel("A").getextrema()[0] < 250:
        box = img.getchannel("A").getbbox()
        return (img.crop(box) if box else img), True, ""
    rgb = img.convert("RGB")
    marker = (255, 0, 255)
    work = rgb.copy()
    w, h = work.size
    for pt in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if work.getpixel(pt) != marker:
            ImageDraw.floodfill(work, pt, marker, thresh=tolerance)
    diff = ImageChops.difference(work, Image.new("RGB", work.size, marker))
    r, g, b = diff.split()
    alpha = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v else 0)
    alpha = alpha.filter(ImageFilter.MedianFilter(3)).filter(ImageFilter.GaussianBlur(0.8))
    covered = sum(alpha.histogram()[128:]) / float(w * h)
    out = rgb.convert("RGBA")
    if covered < 0.04:
        return out, False, "The garment looks the same colour as its background, so it could not be cut out."
    if covered > 0.93:
        return out, False, "The photo background is not plain enough to cut the garment out."
    out.putalpha(alpha)
    box = alpha.point(lambda v: 255 if v > 40 else 0).getbbox()
    return (out.crop(box) if box else out), True, ""


def make_garment(slot: str, category: str, image_bytes: bytes) -> Garment:
    """Validate the photo (same checks as the wardrobe), cut the garment out and wrap it."""
    try:
        clean, _ = W.validate_and_normalise_image(image_bytes)
    except W.InvalidImage as exc:
        raise TryOnError(str(exc)) from exc
    cut, ok, _note = cut_out_garment(Image.open(io.BytesIO(clean)))
    return Garment(slot, category, cut, ok)


def _box_for(g: Garment, a: Anchors) -> tuple[float, float, float, float, bool] | None:
    """(left, top, width, height, bottom_aligned) of the area this garment may fill, or None if it is outside the picture."""
    cat, w2 = g.category, a.shoulder_half * 2
    if g.slot in ("top", "outer", "onepiece"):
        factor = 1.58 if g.slot == "outer" else 1.42
        bottom = {"t-shirt": a.hip_y - 8, "top": a.hip_y - 8, "blouse": a.hip_y, "polo": a.hip_y, "shirt": a.hip_y + 25, "sweater": a.hip_y + 25,
                  "hoodie": a.hip_y + 30, "kurta": (a.hip_y + a.knee_y) / 2, "jacket": a.hip_y + 35, "blazer": a.hip_y + 45,
                  "cardigan": a.hip_y + 45, "windbreaker": a.hip_y + 45, "coat": a.knee_y - 30,
                  "dress": a.knee_y + 20, "jumpsuit": a.ankle_y - 10}.get(cat, a.hip_y + 20)
        bottom = min(bottom, a.height * 0.99)
        return a.centre_x - w2 * factor / 2, a.neck_y, w2 * factor, bottom - a.neck_y, False
    if g.slot == "bottom":
        end = {"shorts": (a.hip_y + a.knee_y) / 2 + 20, "skirt": a.knee_y + 20}.get(cat, a.ankle_y)
        width = a.hip_half * 2 * 1.3
        if a.hip_y >= a.height * 0.9:        # waist-up photo: the legs are outside the frame
            return None
        return a.centre_x - width / 2, a.waist_y - 6, width, end - a.waist_y + 6, False
    if g.slot == "footwear":
        width = a.hip_half * 2 * 1.35
        if a.ankle_y - 20 >= a.height * 0.97:
            return None
        return a.centre_x - width / 2, a.ankle_y - 24, width, a.feet_y - a.ankle_y + 44, True
    return None


def render_tryon(person: Image.Image, anchors: Anchors, garments: Iterable[Garment], dx: float = 0.0, dy: float = 0.0,
                 scale: float = 1.0, guides: bool = False) -> tuple[Image.Image, list[str]]:
    """Draw the garments on ``person``. dx/dy are shifts in % of the image size, scale a multiplier. Returns (image, notes)."""
    out = person.convert("RGBA")
    notes: list[str] = []
    ordered = sorted(garments, key=lambda g: SLOT_ORDER.index(g.slot) if g.slot in SLOT_ORDER else 99)
    for g in ordered:
        if not g.cutout_ok:
            notes.append(f"{g.category.title()}: the garment could not be cut out cleanly, so the whole photo is placed (it may look boxy). Use a photo on a plain contrasting background.")
        box = _box_for(g, anchors)
        if box is None:
            notes.append(f"{g.category.title()}: that part of the body is outside your photo, so it was skipped.")
            continue
        left, top, bw, bh, bottom_aligned = box
        gw, gh = g.image.size
        fit = min(bw / gw, bh / gh) * max(0.3, scale)
        size = (max(1, round(gw * fit)), max(1, round(gh * fit)))
        piece = g.image.resize(size, Image.LANCZOS)
        x = round(left + (bw - size[0]) / 2 + dx / 100 * anchors.width)
        y = round((top + bh - size[1] if bottom_aligned else top) + dy / 100 * anchors.height)
        if g.slot != "footwear" and y < anchors.neck_y:      # never above the collar line: the face stays visible
            cut = round(anchors.neck_y - y)
            if cut >= piece.height:
                notes.append(f"{g.category.title()}: moved above the neck line, so it was hidden.")
                continue
            piece, y = piece.crop((0, cut, piece.width, piece.height)), round(anchors.neck_y)
        layer = Image.new("RGBA", out.size, (0, 0, 0, 0))
        layer.paste(piece, (x, y), piece)
        out = Image.alpha_composite(out, layer)
    if guides:
        d = ImageDraw.Draw(out)
        for name, y in (("neck", anchors.neck_y), ("waist", anchors.waist_y), ("hip", anchors.hip_y), ("knee", anchors.knee_y), ("ankle", anchors.ankle_y)):
            d.line([(0, y), (out.width, y)], fill=(230, 60, 60, 255), width=2)
            d.text((6, y - 12), name, fill=(230, 60, 60, 255))
        d.line([(anchors.centre_x, 0), (anchors.centre_x, out.height)], fill=(60, 120, 230, 255), width=1)
    return out.convert("RGB"), notes


# ------------------------------------------------------------------ your own photo (private, local)
def save_person_photo(data: bytes, data_dir: Path | None = None) -> str:
    """Validate, strip EXIF/GPS (re-encode) and store the photo under ``data/avatar/``. Returns the relative path."""
    try:
        clean, _ = W.validate_and_normalise_image(data)
    except W.InvalidImage as exc:
        raise TryOnError(str(exc)) from exc
    base = data_dir or W.get_data_dir()
    target = base / PERSON_PHOTO_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(clean)
    return PERSON_PHOTO_REL


def load_person_photo(data_dir: Path | None = None) -> bytes | None:
    return W.read_image_bytes(PERSON_PHOTO_REL, data_dir)


def delete_person_photo(data_dir: Path | None = None) -> bool:
    p = W.resolve_image_path(PERSON_PHOTO_REL, data_dir)
    try:
        if p and p.is_file():
            p.unlink()
            return True
    except OSError:
        pass
    return False


# ------------------------------------------------------------------ optional trained model
def load_adapter(spec: str) -> Callable[[Image.Image, Image.Image, str], Image.Image]:
    """Import ``module:function`` from ``TRYON_ADAPTER``. The function must accept (person RGB image, garment RGB image,
    category text) and return the try-on result as a PIL image. Only set this to code you wrote or trust."""
    spec = (spec or "").strip()
    if not _ADAPTER_SPEC.match(spec):
        raise TryOnError("TRYON_ADAPTER must look like  your_module:your_function")
    module_name, func_name = spec.split(":")
    try:
        func = getattr(importlib.import_module(module_name), func_name)
    except (ImportError, AttributeError) as exc:
        raise TryOnError(f"Could not load the try-on adapter '{spec}': {exc}") from exc
    if not callable(func):
        raise TryOnError(f"'{spec}' is not a function.")
    return func


def run_adapter(func, person: Image.Image, garment: Image.Image, category: str) -> Image.Image:
    try:
        result = func(person.convert("RGB"), garment.convert("RGB"), category)
    except Exception as exc:     # a research model can fail in many ways; the preview still works
        raise TryOnError(f"Your try-on model failed: {type(exc).__name__}: {exc}") from exc
    if not isinstance(result, Image.Image):
        raise TryOnError("Your try-on function must return a PIL.Image.")
    return result.convert("RGB")

