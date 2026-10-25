"""Clothing inventory: safe image storage + SQL metadata.

Privacy: wardrobe photos live under ``data/wardrobe/`` with random file names, are re-encoded (EXIF/GPS stripped),
never leave the machine except when YOU press a Gemini action, and can be deleted per item or all at once.
External dataset images live under ``data/external/`` and have ``ownership='inspiration'`` - they are never
treated as clothes you own.
"""
from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Callable

from PIL import Image, UnidentifiedImageError

import taxonomy as tx
from db import Database, now_iso

DATA_DIR = Path(__file__).resolve().parent / "data"


def get_data_dir() -> Path:
    """Private image folder; WEATHERFIT_DATA_DIR is read at call time."""
    return Path(os.getenv("WEATHERFIT_DATA_DIR") or DATA_DIR)
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_SIDE = 1600
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
ITEM_COLUMNS = ("name", "category", "colour", "secondary_colour", "pattern", "fabric", "season", "style", "occasion",
                "gender_presentation", "comfort")


class InvalidImage(ValueError):
    pass


class InvalidItem(ValueError):
    pass


# ------------------------------------------------------------------ images
def validate_and_normalise_image(data: bytes) -> tuple[bytes, str]:
    """Verify ``data`` is a real, reasonably sized JPEG/PNG/WEBP; return (clean JPEG bytes, sha256 of the original)."""
    if not data:
        raise InvalidImage("The file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise InvalidImage(f"The image is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    try:
        probe = Image.open(io.BytesIO(data))
        fmt = probe.format
        probe.verify()  # structural check only
        img = Image.open(io.BytesIO(data))
        width, height = img.size
        if width * height > MAX_PIXELS:
            raise InvalidImage("The image has too many pixels (possible decompression bomb).")
        if fmt not in ALLOWED_FORMATS:
            raise InvalidImage(f"Unsupported image type {fmt or 'unknown'}. Use JPG, PNG or WEBP.")
        img.load()
    except InvalidImage:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise InvalidImage("This file is not a valid image.") from exc
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)  # re-encoding drops EXIF / GPS metadata
    return out.getvalue(), hashlib.sha256(data).hexdigest()


def resolve_image_path(rel: str | None, data_dir: Path | None = None) -> Path | None:
    """Absolute path of a stored image; refuses anything outside the data directory."""
    if not rel:
        return None
    base = (data_dir or get_data_dir()).resolve()
    p = (base / rel).resolve()
    try:
        p.relative_to(base)
    except ValueError:
        return None
    return p


def read_image_bytes(rel: str | None, data_dir: Path | None = None) -> bytes | None:
    p = resolve_image_path(rel, data_dir)
    try:
        return p.read_bytes() if p and p.is_file() else None
    except OSError:
        return None


def _store_image(clean_jpeg: bytes, subdir: str, data_dir: Path) -> str:
    folder = data_dir / subdir
    folder.mkdir(parents=True, exist_ok=True)
    rel = f"{subdir}/{uuid.uuid4().hex}.jpg"
    (data_dir / rel).write_bytes(clean_jpeg)
    return rel


# ------------------------------------------------------------------ metadata
def clean_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Validate/normalise user- or AI-supplied metadata. Only ``category`` is required; the rest gets safe defaults."""
    category = tx.normalize_category(fields.get("category"))
    if not category or category not in tx.CATEGORIES:
        raise InvalidItem(f"Unknown clothing category: {fields.get('category')!r}. Pick one of: {', '.join(sorted(tx.CATEGORIES))}.")
    try:
        comfort = int(fields.get("comfort", 3) or 3)
    except (TypeError, ValueError):
        comfort = 3
    style = str(fields.get("style") or "Casual").strip()
    style = next((s for s in tx.STYLES if s.lower() == style.lower()), "Casual")
    return {
        "name": str(fields.get("name") or "").strip()[:80],
        "category": category,
        "colour": tx.normalize_colour(fields.get("colour")),
        "secondary_colour": tx.normalize_colour(fields.get("secondary_colour")),
        "pattern": tx.normalize_pattern(fields.get("pattern")),
        "fabric": tx.normalize_choice(fields.get("fabric"), tx.FABRICS, "other"),
        "season": tx.normalize_multi(fields.get("season"), tx.SEASONS, "all"),
        "style": style,
        "occasion": tx.normalize_multi(fields.get("occasion"), tx.OCCASION_TAGS + ["any"], "casual"),
        "gender_presentation": tx.normalize_choice(fields.get("gender_presentation"), tx.GENDERS, "unisex"),
        "comfort": max(1, min(5, comfort)),
    }


def display_name(item: dict[str, Any]) -> str:
    return item.get("name") or " ".join(x for x in (item.get("colour"), item.get("category")) if x).strip().title() or "Item"


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


class DuplicateItem(InvalidItem):
    pass


def add_item(db: Database, fields: dict[str, Any], image_bytes: bytes | None = None, *, ownership: str = "wardrobe",
             source: str = "user", source_ref: str | None = None, license_: str = "", attribution: str = "",
             label_source: str = "manual", data_dir: Path | None = None, subdir: str | None = None) -> int:
    """Insert one clothing item (and its image). The image file is removed again if the database insert fails."""
    if ownership not in tx.OWNERSHIPS:
        raise InvalidItem("ownership must be 'wardrobe' or 'inspiration'.")
    data_dir = data_dir or get_data_dir()
    clean = clean_fields(fields)
    rel = sha = None
    if image_bytes is not None:
        jpeg, sha = validate_and_normalise_image(image_bytes)
        rel = _store_image(jpeg, subdir or ("wardrobe" if ownership == "wardrobe" else f"external/{source}"), data_dir)
    try:
        with db.tx() as c:
            cur = c.execute(
                """INSERT INTO clothing_items (name, image_path, image_sha256, category, colour, secondary_colour, pattern, fabric,
                   season, style, occasion, gender_presentation, comfort, ownership, source, source_ref, license, attribution,
                   label_source, uploaded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (clean["name"], rel, sha, clean["category"], clean["colour"], clean["secondary_colour"], clean["pattern"],
                 clean["fabric"], clean["season"], clean["style"], clean["occasion"], clean["gender_presentation"], clean["comfort"],
                 ownership, source, source_ref, license_, attribution, label_source, now_iso()))
            return int(cur.lastrowid)
    except sqlite3.IntegrityError as exc:
        _remove_file(rel, data_dir)
        raise DuplicateItem("This item already exists (same photo or same dataset record).") from exc
    except BaseException:
        _remove_file(rel, data_dir)
        raise


def get_item(db: Database, item_id: int) -> dict[str, Any] | None:
    with db.read() as c:
        return _row(c.execute("SELECT * FROM clothing_items WHERE id=?", (item_id,)).fetchone())


def list_items(db: Database, ownership: str | None = "wardrobe", category: str | None = None, slot: str | None = None) -> list[dict[str, Any]]:
    q, args = "SELECT * FROM clothing_items WHERE 1=1", []
    if ownership:
        q += " AND ownership=?"
        args.append(ownership)
    if category:
        q += " AND category=?"
        args.append(category)
    q += " ORDER BY id"
    with db.read() as c:
        rows = [dict(r) for r in c.execute(q, args)]
    if slot:
        rows = [r for r in rows if r["category"] in tx.CATEGORIES and tx.slot_of(r["category"]) == slot]
    return rows


def update_item(db: Database, item_id: int, fields: dict[str, Any], label_source: str | None = None) -> None:
    """Manual correction of labels. Only the metadata columns can change."""
    current = get_item(db, item_id)
    if not current:
        raise InvalidItem("That item no longer exists.")
    merged = clean_fields({**{k: current[k] for k in ITEM_COLUMNS}, **fields})
    sets = ", ".join(f"{k}=?" for k in ITEM_COLUMNS)
    ls = label_source or ("ai_corrected" if current["label_source"] in ("ai", "ai_corrected") else "manual")
    with db.tx() as c:
        c.execute(f"UPDATE clothing_items SET {sets}, label_source=? WHERE id=?", (*[merged[k] for k in ITEM_COLUMNS], ls, item_id))


def _remove_file(rel: str | None, data_dir: Path | None = None) -> None:
    p = resolve_image_path(rel, data_dir)
    try:
        if p and p.is_file():
            p.unlink()
    except OSError:
        pass


def delete_item(db: Database, item_id: int, data_dir: Path | None = None) -> bool:
    """Delete the row, its embedding, and the image file. Outfit history keeps a text snapshot (category/colour)."""
    item = get_item(db, item_id)
    if not item:
        return False
    with db.tx() as c:
        c.execute("DELETE FROM clothing_items WHERE id=?", (item_id,))
    _remove_file(item["image_path"], data_dir)
    return True


def delete_all_wardrobe(db: Database, data_dir: Path | None = None) -> int:
    items = list_items(db, "wardrobe")
    with db.tx() as c:
        c.execute("DELETE FROM clothing_items WHERE ownership='wardrobe'")
    for it in items:
        _remove_file(it["image_path"], data_dir)
    return len(items)


# ------------------------------------------------------------------ label suggestions
Vision = Callable[[bytes], dict[str, Any]]


def dominant_colour_name(image_bytes: bytes) -> str:
    """Cheap, offline colour guess (centre of the image, background-ish pixels ignored) mapped to our colour names."""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception:
        return ""
    w, h = img.size
    img = img.crop((int(w * 0.25), int(h * 0.25), int(w * 0.75), int(h * 0.75)))
    r, g, b = img.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
    return rgb_to_colour_name((r, g, b))


_REFERENCE_RGB = {"black": (25, 25, 25), "white": (245, 245, 245), "grey": (128, 128, 128), "navy": (25, 35, 80), "blue": (40, 90, 190),
                  "light blue": (150, 190, 230), "teal": (30, 130, 130), "green": (50, 140, 60), "olive": (110, 115, 50),
                  "yellow": (240, 220, 60), "mustard": (210, 170, 40), "orange": (235, 130, 40), "red": (200, 40, 40),
                  "maroon": (110, 25, 40), "pink": (235, 150, 180), "purple": (120, 60, 150), "brown": (110, 70, 40),
                  "beige": (225, 205, 170), "cream": (245, 238, 215), "khaki": (190, 175, 120), "tan": (190, 150, 100)}


def rgb_to_colour_name(rgb: tuple[float, float, float]) -> str:
    return min(_REFERENCE_RGB, key=lambda n: sum((a - b) ** 2 for a, b in zip(rgb, _REFERENCE_RGB[n])))


def suggest_labels(image_bytes: bytes, vision: Vision | None = None, classifier: Callable[[bytes], str | None] | None = None) -> dict[str, Any]:
    """Best-effort labels for an uploaded photo: ``{"fields": {...}, "source": "ai"|"fashionclip"|"heuristic"|"none", "notes": [..]}``.

    ``vision`` (Gemini) and ``classifier`` (FashionCLIP) are injected so tests never touch the network. Every failure is
    caught: suggestions are only a starting point and the user can always correct them in the form.
    """
    fields: dict[str, Any] = {}
    notes: list[str] = []
    source = "none"
    if vision:
        try:
            raw = vision(image_bytes) or {}
            fields = {k: v for k, v in raw.items() if v not in (None, "")}
            if fields:
                source = "ai"
        except Exception as exc:  # network, quota, bad JSON ...
            from security import redact
            notes.append(f"AI label suggestion unavailable ({redact(exc)[:160]}).")
    if not tx.normalize_category(fields.get("category")) and classifier:
        try:
            guess = classifier(image_bytes)
            if guess:
                fields["category"] = guess
                source = "fashionclip" if source == "none" else source
        except Exception as exc:
            notes.append(f"FashionCLIP category guess unavailable ({str(exc)[:120]}).")
    if not fields.get("colour"):
        colour = dominant_colour_name(image_bytes)
        if colour:
            fields["colour"] = colour
            source = "heuristic" if source == "none" else source
            notes.append("Colour is a rough automatic guess from the image centre - please check it.")
    if not tx.normalize_category(fields.get("category")):
        notes.append("I couldn't tell the clothing category - please pick it.")
    return {"fields": fields, "source": source, "notes": notes}


# ------------------------------------------------------------------ embeddings / similarity
Embedder = Callable[[list[bytes]], list[list[float]]]
EMBED_MODEL = "patrickjohncyh/fashion-clip"


def _pack(vec: list[float]) -> bytes:
    import array
    return array.array("f", vec).tobytes()


def _unpack(blob: bytes) -> list[float]:
    import array
    a = array.array("f")
    a.frombytes(blob)
    return list(a)


def _normalise(v: list[float]) -> list[float]:
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v]


def index_item_embeddings(db: Database, embedder: Embedder, items: list[dict[str, Any]] | None = None,
                          data_dir: Path | None = None, model: str = EMBED_MODEL) -> int:
    """Store pretrained-model image embeddings for items that don't have one yet (no training happens here)."""
    items = items if items is not None else list_items(db, None)
    with db.read() as c:
        have = {r["item_id"] for r in c.execute("SELECT item_id FROM item_embeddings WHERE model=?", (model,))}
    todo = [(it["id"], read_image_bytes(it["image_path"], data_dir)) for it in items if it["id"] not in have]
    todo = [(i, b) for i, b in todo if b]
    if not todo:
        return 0
    vecs = embedder([b for _, b in todo])
    if len(vecs) != len(todo):
        raise RuntimeError("Embedder returned the wrong number of vectors.")
    with db.tx() as c:
        for (iid, _), v in zip(todo, vecs):
            v = _normalise([float(x) for x in v])
            c.execute("INSERT OR REPLACE INTO item_embeddings(item_id, model, dim, vector) VALUES (?,?,?,?)", (iid, model, len(v), _pack(v)))
    return len(todo)


def similar_items(db: Database, query_vec: list[float], k: int = 5, ownership: str | None = None, model: str = EMBED_MODEL,
                  exclude_id: int | None = None) -> list[tuple[dict[str, Any], float]]:
    """Cosine-similarity search over stored embeddings (pure Python; fine for a personal wardrobe-sized collection)."""
    q = _normalise([float(x) for x in query_vec])
    sql = ("SELECT c.*, e.vector AS _vec, e.dim AS _dim FROM item_embeddings e JOIN clothing_items c ON c.id=e.item_id "
           "WHERE e.model=?" + (" AND c.ownership=?" if ownership else ""))
    args: list[Any] = [model] + ([ownership] if ownership else [])
    out = []
    with db.read() as c:
        for r in c.execute(sql, args):
            d = dict(r)
            vec = _unpack(d.pop("_vec"))
            d.pop("_dim")
            if d["id"] == exclude_id or len(vec) != len(q):
                continue
            out.append((d, sum(a * b for a, b in zip(q, vec))))
    return sorted(out, key=lambda t: -t[1])[:k]
