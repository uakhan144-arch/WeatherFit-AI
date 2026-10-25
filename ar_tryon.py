"""Live-camera (AR) virtual try-on: the Python side (pure Pillow, no new dependencies, importable without Streamlit).

What runs where
* The camera is opened and read ONLY inside the user's browser (``ar_tryon_component/``). Video frames are never sent to the
  Streamlit server, to Gemini or anywhere else.
* Python prepares the garments: it cuts them out (``tryon_engine.make_garment``), shrinks them and sends small PNG data URLs to
  the component together with the placement constants below (one source of truth for garment lengths).
* Optional body tracking (MediaPipe Pose, loaded by the browser from a CDN only if the user ticks the box) moves the garments with
  the body. Without it the user lines up with an on-screen guide and fine-tunes with sliders.

It is a 2-D overlay, like the existing preview: no cloth simulation, depth, occlusion by arms/hair, or size/fit guarantee.
"""
from __future__ import annotations

import base64
import io
import os
from dataclasses import dataclass, field

from PIL import Image

import taxonomy as tx
import tryon_engine as engine

PAYLOAD_MAX_SIDE = 360          # garment images are shrunk to this before being sent to the browser (small + fast)
MAX_PAYLOAD_BYTES = 180_000     # per garment, as base64 text; larger images are shrunk further
MAX_GARMENTS = 5                # one per slot: footwear, bottom, top, onepiece, outer
POSE_LIBRARY_VERSION = "0.10.14"   # @mediapipe/tasks-vision, pinned (no floating "latest")
POSE_MODEL = "pose_landmarker_lite"

# Where the hem of each garment ends, as a fraction of the shoulder-to-hip distance BELOW the hip line (negative = above).
# Mirrors the lengths used by the 2-D preview in tryon_engine._box_for.
TOP_LENGTHS: dict[str, float] = {
    "t-shirt": 0.0, "top": 0.0, "blouse": 0.05, "polo": 0.05, "shirt": 0.12, "sweater": 0.12, "hoodie": 0.15,
    "kurta": 0.55, "jacket": 0.18, "blazer": 0.22, "cardigan": 0.22, "windbreaker": 0.22, "coat": 0.85,
}
FACTORS = {                      # sizes relative to the measured shoulder width S
    "top_width": 1.42, "outer_width": 1.58, "bottom_width": 1.12, "shoe_width": 1.15, "shoe_aspect": 0.45,
    "neck_above_shoulder": 0.18, "top_above_shoulder": 0.10, "waist_above_hip": 0.10, "leg_to_torso": 1.6,
    "shoe_below_ankle": 0.22,
}
# Categories handled by the dedicated rules in ar_placement.js (bottoms / dress / jumpsuit / footwear)
SPECIAL = {"bottom", "footwear", "onepiece"}


class ArTryOnError(RuntimeError):
    """Readable problem preparing the AR overlay."""


@dataclass
class ArPayload:
    garments: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def placement_config() -> dict:
    """Constants shared with the browser code."""
    return {"lengths": {**TOP_LENGTHS, "default": 0.15, "dress_below_knee": 0.15}, "factors": dict(FACTORS)}


def pose_urls() -> dict[str, str]:
    """Where the browser fetches the optional tracker from (only after the user opts in)."""
    v = os.getenv("AR_POSE_LIB_VERSION", "").strip() or POSE_LIBRARY_VERSION
    if not v.replace(".", "").isdigit():      # only plain version numbers, never a URL fragment or "latest"
        v = POSE_LIBRARY_VERSION
    return {
        "module": f"https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@{v}/vision_bundle.mjs",
        "wasm": f"https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@{v}/wasm",
        "model": f"https://storage.googleapis.com/mediapipe-models/pose_landmarker/{POSE_MODEL}/float16/1/{POSE_MODEL}.task",
    }


def encode_garment(g: engine.Garment, max_side: int = PAYLOAD_MAX_SIDE) -> str:
    """Small transparent PNG as a data URL. Shrinks further if the result is bigger than MAX_PAYLOAD_BYTES."""
    img = g.image.convert("RGBA")
    side = max_side
    while True:
        im = img.copy()
        im.thumbnail((side, side), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        text = base64.b64encode(buf.getvalue()).decode("ascii")
        if len(text) <= MAX_PAYLOAD_BYTES or side <= 96:
            return "data:image/png;base64," + text
        side = int(side * 0.75)


def prepare(garments: list[engine.Garment]) -> ArPayload:
    """Choose what to overlay: one garment per slot, a dress/jumpsuit replaces top+bottom, uncut photos are left out."""
    out = ArPayload()
    by_slot: dict[str, engine.Garment] = {}
    for g in garments:
        if g.slot not in engine.SLOT_ORDER:
            out.notes.append(f"{g.category.title()} can't be shown on the camera view.")
            continue
        if not g.cutout_ok:
            out.notes.append(f"{g.category.title()}: the photo could not be cut out from its background, so it is not shown live "
                             "(a boxy photo would look wrong on the camera). Use a photo on a plain contrasting background.")
            continue
        by_slot[g.slot] = g
    if "onepiece" in by_slot and ("top" in by_slot or "bottom" in by_slot):
        by_slot.pop("top", None)
        by_slot.pop("bottom", None)
        out.notes.append("A dress/jumpsuit replaces the separate top and bottom, so those were left out.")
    for slot in engine.SLOT_ORDER:       # back-to-front draw order
        g = by_slot.get(slot)
        if g is None:
            continue
        w, h = g.image.size
        out.garments.append({"slot": slot, "category": g.category, "src": encode_garment(g), "aspect": round(w / max(h, 1), 4)})
    if len(out.garments) > MAX_GARMENTS:      # cannot happen with five slots; kept as a hard limit for the browser
        out.garments = out.garments[:MAX_GARMENTS]
    return out


def component_args(payload: ArPayload, tracking: bool) -> dict:
    """Everything the browser component needs (JSON-serialisable)."""
    return {"garments": payload.garments, "tracking": bool(tracking), "config": placement_config(), "pose": pose_urls()}


def missing_length_rules() -> list[str]:
    """Garment categories in the taxonomy that the AR placement has no length rule for (used by a test to catch drift)."""
    missing = []
    for cat, (slot, _w, _f) in tx.CATEGORIES.items():
        if slot in ("top", "outer") and cat not in TOP_LENGTHS:
            missing.append(cat)
        elif slot not in ("top", "outer") and slot not in SPECIAL and slot in engine.SLOT_ORDER:
            missing.append(cat)
    return missing
