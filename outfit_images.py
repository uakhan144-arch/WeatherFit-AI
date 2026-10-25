"""Rank outfit images against a recommendation.

Sources: image files in ``assets/outfits/`` and/or photos the user uploads in the app.
Ranking: FashionCLIP text-to-image similarity when installed; otherwise a clearly-labelled keyword match
on file names / optional ``catalog.json`` tags. The method actually used is always reported.
Image *generation* lives elsewhere (ai_features.generate_outfit_image) and is independent of weather retrieval.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_DIR = Path(__file__).parent / "assets" / "outfits"

Scorer = Callable[[str, list[bytes]], list[float]]


@dataclass
class RankedImage:
    label: str
    data: bytes
    score: float
    method: str  # "fashionclip" | "keyword"


def list_outfit_images(folder: Path | str = DEFAULT_DIR) -> list[tuple[str, bytes]]:
    p = Path(folder)
    if not p.is_dir():
        return []
    return [(f.name, f.read_bytes()) for f in sorted(p.iterdir()) if f.suffix.lower() in IMAGE_EXTS]


def outfit_query(top: str, bottom: str, footwear: str, style: str = "", occasion: str = "") -> str:
    """Plain-language description of the recommended outfit, used as the FashionCLIP text prompt."""
    parts = [x.strip().lower() for x in (top, bottom, footwear) if x and x.strip()]
    text = "a person wearing " + ", ".join(parts)
    if style:
        text += f", {style.lower()} style"
    if occasion:
        text += f", for {occasion.lower()}"
    return text


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]+", s.lower()) if len(t) > 2}


def _catalog_tags(folder: Path | str) -> dict[str, str]:
    f = Path(folder) / "catalog.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return {k: " ".join(v) if isinstance(v, list) else str(v) for k, v in data.items()}
    except (OSError, ValueError, AttributeError):
        return {}


def keyword_scores(query: str, labels: list[str], extra_tags: dict[str, str] | None = None) -> list[float]:
    q = _tokens(query)
    extra_tags = extra_tags or {}
    out = []
    for label in labels:
        t = _tokens(Path(label).stem.replace("_", " ").replace("-", " ") + " " + extra_tags.get(label, ""))
        out.append(len(q & t) / (len(q) or 1))
    return out


def rank_images(query: str, items: list[tuple[str, bytes]], scorer: Scorer | None = None,
                top_k: int = 4, tags: dict[str, str] | None = None) -> tuple[list[RankedImage], str]:
    """Return (ranked images, note). ``note`` explains which method ran and why a fallback was used."""
    if not items:
        return [], "No outfit images found. Add images to assets/outfits/ or upload some below."
    labels, blobs = [n for n, _ in items], [b for _, b in items]
    method, note = "fashionclip", ""
    try:
        if scorer is None:
            from ai_features import fashionclip_rank_images as scorer  # lazy: heavy optional dependency
        scores = scorer(query, blobs)
        if len(scores) != len(blobs):
            raise RuntimeError("FashionCLIP returned an unexpected number of scores.")
    except Exception as exc:
        method = "keyword"
        scores = keyword_scores(query, labels, tags)
        note = f"FashionCLIP unavailable ({exc}). Ranked by keyword match on file names/tags instead - install FashionCLIP for visual ranking."
    ranked = sorted((RankedImage(l, b, float(s), method) for l, b, s in zip(labels, blobs, scores)),
                    key=lambda r: r.score, reverse=True)
    return ranked[:top_k], note
