"""Import PERMITTED fashion-dataset annotations into the clothing schema as ``ownership='inspiration'`` items.

Dataset items are reference material only. They live in ``data/external/<source>/`` (separate from your private wardrobe photos
in ``data/wardrobe/``), are never treated as clothes you own, and must not be redistributed (the folder is git-ignored and excluded
from the project ZIP). See DATASETS.md for licence details.

Usage (PowerShell, from the project folder):
    python dataset_import.py sample
    python dataset_import.py fashionpedia --annotations C:\\data\\fashionpedia\\instances_attributes_val2020.json --images-dir C:\\data\\fashionpedia\\val_test2020\\test --limit 200 --accept-terms
    python dataset_import.py deepfashion  --anno-dir C:\\data\\DeepFashion\\Anno_coarse --images-dir C:\\data\\DeepFashion --limit 200 --agreement-signed

Nothing is downloaded by this script: you obtain the files yourself, from the official source, after reading the terms.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

import taxonomy as tx
from db import Database, init_db
from wardrobe import DATA_DIR, DuplicateItem, InvalidImage, InvalidItem, add_item, dominant_colour_name

SAMPLE_DIR = Path(__file__).resolve().parent / "sample_dataset"

FASHIONPEDIA_TERMS = ("Fashionpedia: annotations/ontology are CC BY 4.0 (attribution required); the IMAGES are not owned by Fashionpedia and remain under "
                      "their original sources' terms (Flickr, Unsplash, Pexels, Burst, Freestocks, Kaboompics). You are responsible for your use. "
                      "https://fashionpedia.github.io/home/data_license.html")
DEEPFASHION_TERMS = ("DeepFashion: NON-COMMERCIAL research use only; a signed release agreement is required for the encrypted image data; no copying, "
                     "publishing or redistribution of images or derived data. https://mmlab.ie.cuhk.edu.hk/projects/DeepFashion.html")
FASHIONPEDIA_ATTRIBUTION = "Fashionpedia (Jia et al., ECCV 2020) - https://fashionpedia.github.io - annotations CC BY 4.0; image rights remain with original sources"
DEEPFASHION_ATTRIBUTION = "DeepFashion (Liu et al., CVPR 2016) - MMLAB, CUHK - non-commercial research use"

# Fashionpedia main-apparel categories -> our categories. Parts (sleeve, collar...) and decorations are intentionally absent.
FASHIONPEDIA_MAP = {
    "shirt, blouse": "shirt", "top, t-shirt, sweatshirt": "t-shirt", "sweater": "sweater", "cardigan": "cardigan", "jacket": "jacket",
    "vest": "top", "pants": "trousers", "shorts": "shorts", "skirt": "skirt", "coat": "coat", "dress": "dress", "jumpsuit": "jumpsuit",
    "cape": "coat", "glasses": "sunglasses", "hat": "cap", "tie": "tie", "watch": "watch", "belt": "belt", "shoe": "sneakers",
    "bag, wallet": "bag", "scarf": "scarf", "umbrella": "umbrella",
}
FASHIONPEDIA_PATTERN_HINTS = {"stripe": "striped", "check": "checked", "plaid": "checked", "floral": "floral", "polka": "polka-dot",
                              "dot": "polka-dot", "plain": "solid", "geometric": "printed", "abstract": "printed", "camouflage": "printed",
                              "animal": "printed", "paisley": "printed", "graphic": "printed"}
# DeepFashion (Category and Attribute Prediction benchmark) 50 categories -> ours
DEEPFASHION_MAP = {
    "anorak": "jacket", "blazer": "blazer", "blouse": "blouse", "bomber": "jacket", "button-down": "shirt", "cardigan": "cardigan", "flannel": "shirt",
    "halter": "top", "henley": "t-shirt", "hoodie": "hoodie", "jacket": "jacket", "jersey": "t-shirt", "parka": "coat", "peacoat": "coat",
    "poncho": "coat", "sweater": "sweater", "tank": "top", "tee": "t-shirt", "top": "top", "turtleneck": "sweater", "capris": "trousers",
    "chinos": "chinos", "culottes": "skirt", "cutoffs": "shorts", "gauchos": "trousers", "jeans": "jeans", "jeggings": "jeans", "jodhpurs": "trousers",
    "joggers": "joggers", "leggings": "trousers", "sarong": "skirt", "shorts": "shorts", "skirt": "skirt", "sweatpants": "joggers",
    "sweatshorts": "shorts", "trunks": "shorts", "caftan": "dress", "cape": "coat", "coat": "coat", "coverup": "top", "dress": "dress",
    "jumpsuit": "jumpsuit", "kaftan": "dress", "kimono": "jacket", "nightdress": "dress", "onesie": "jumpsuit", "robe": "coat", "romper": "jumpsuit",
    "shirtdress": "dress", "sundress": "dress",
}


@dataclass
class ImportReport:
    source: str
    imported: int = 0
    duplicates: int = 0
    skipped_unmapped: int = 0
    skipped_missing_image: int = 0
    skipped_invalid: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"[{self.source}] imported {self.imported}, duplicates {self.duplicates}, unmapped categories {self.skipped_unmapped}, "
                f"missing images {self.skipped_missing_image}, invalid {self.skipped_invalid}")


def _crop(image_bytes: bytes, bbox: list[float] | None) -> bytes:
    """Crop to the annotated box (COCO x,y,w,h). Falls back to the whole image when the box is unusable."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    if bbox and len(bbox) == 4:
        x, y, w, h = bbox
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(img.width, int(x + w)), min(img.height, int(y + h))
        if x1 - x0 >= 32 and y1 - y0 >= 32:
            img = img.crop((x0, y0, x1, y1))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def import_coco_annotations(db: Database, annotations_path: Path | str, images_dir: Path | str, *, source: str, license_: str, attribution: str,
                            category_map: dict[str, str] = FASHIONPEDIA_MAP, limit: int | None = 200, data_dir: Path | None = None) -> ImportReport:
    """Fashionpedia/COCO layout: images[], categories[], attributes[], annotations[] (bbox = x,y,w,h). One bad record never stops the import."""
    report = ImportReport(source)
    doc = json.loads(Path(annotations_path).read_text(encoding="utf-8"))
    images = {i["id"]: i for i in doc.get("images", [])}
    cats = {c["id"]: c["name"] for c in doc.get("categories", [])}
    attrs = {a["id"]: str(a.get("name", "")).lower() for a in doc.get("attributes", [])}
    for ann in doc.get("annotations", []):
        if limit is not None and report.imported >= limit:
            break
        ours = category_map.get(cats.get(ann.get("category_id"), ""))
        if not ours:
            report.skipped_unmapped += 1
            continue
        meta = images.get(ann.get("image_id"))
        path = Path(images_dir) / meta["file_name"] if meta else None
        if not path or not path.is_file():
            report.skipped_missing_image += 1
            continue
        try:
            crop = _crop(path.read_bytes(), ann.get("bbox"))
            pattern = "solid"
            for aid in ann.get("attribute_ids", []):
                name = attrs.get(aid, "")
                hit = next((v for k, v in FASHIONPEDIA_PATTERN_HINTS.items() if k in name), None)
                if hit:
                    pattern = hit
                    break
            add_item(db, {"category": ours, "colour": dominant_colour_name(crop), "pattern": pattern, "style": "Casual",
                          "occasion": "casual", "gender_presentation": "unisex", "name": f"{ours} (dataset reference)"},
                     crop, ownership="inspiration", source=source, source_ref=str(ann["id"]), license_=license_, attribution=attribution,
                     label_source="dataset", data_dir=data_dir)
            report.imported += 1
        except DuplicateItem:
            report.duplicates += 1
        except (InvalidImage, InvalidItem, UnidentifiedImageError, OSError, KeyError, ValueError) as exc:
            report.skipped_invalid += 1
            report.errors.append(f"annotation {ann.get('id')}: {type(exc).__name__}")
    return report


def import_sample(db: Database, data_dir: Path | None = None) -> ImportReport:
    """The bundled synthetic CC0 dataset - safe to import anywhere, anytime."""
    ann = SAMPLE_DIR / "annotations.json"
    if not ann.is_file():
        raise FileNotFoundError("sample_dataset/annotations.json is missing. Run: python make_sample_dataset.py")
    return import_coco_annotations(db, ann, SAMPLE_DIR / "images", source="sample", license_="CC0-1.0 (synthetic, generated for this project)",
                                   attribution="WeatherFit AI synthetic sample dataset", limit=None, data_dir=data_dir)


def import_fashionpedia(db: Database, annotations_path: Path | str, images_dir: Path | str, *, accept_terms: bool, limit: int | None = 200,
                        data_dir: Path | None = None) -> ImportReport:
    if not accept_terms:
        raise PermissionError("Read the Fashionpedia terms first, then pass accept_terms=True / --accept-terms.\n" + FASHIONPEDIA_TERMS)
    return import_coco_annotations(db, annotations_path, images_dir, source="fashionpedia",
                                   license_="Annotations CC BY 4.0; images: original source terms (not redistributable here)",
                                   attribution=FASHIONPEDIA_ATTRIBUTION, limit=limit, data_dir=data_dir)


def import_deepfashion(db: Database, anno_dir: Path | str, images_root: Path | str, *, agreement_signed: bool, limit: int | None = 200,
                       data_dir: Path | None = None) -> ImportReport:
    """DeepFashion 'Category and Attribute Prediction' files: list_category_cloth.txt + list_category_img.txt."""
    if not agreement_signed:
        raise PermissionError("DeepFashion is for non-commercial research and needs a signed release agreement. Pass agreement_signed=True / "
                              "--agreement-signed only if that applies to you.\n" + DEEPFASHION_TERMS)
    report = ImportReport("deepfashion")
    anno = Path(anno_dir)
    names = [ln.split()[0].lower() for ln in (anno / "list_category_cloth.txt").read_text(encoding="utf-8").splitlines()[2:] if ln.strip()]
    for line in (anno / "list_category_img.txt").read_text(encoding="utf-8").splitlines()[2:]:
        if limit is not None and report.imported >= limit:
            break
        parts = line.split()
        if len(parts) < 2:
            continue
        rel, cat_idx = parts[0], int(parts[1])
        ours = DEEPFASHION_MAP.get(names[cat_idx - 1]) if 0 < cat_idx <= len(names) else None
        if not ours:
            report.skipped_unmapped += 1
            continue
        path = Path(images_root) / rel
        if not path.is_file():
            report.skipped_missing_image += 1
            continue
        try:
            raw = path.read_bytes()
            add_item(db, {"category": ours, "colour": dominant_colour_name(raw), "name": f"{ours} (dataset reference)"}, raw, ownership="inspiration",
                     source="deepfashion", source_ref=rel, license_="DeepFashion non-commercial research agreement", attribution=DEEPFASHION_ATTRIBUTION,
                     label_source="dataset", data_dir=data_dir)
            report.imported += 1
        except DuplicateItem:
            report.duplicates += 1
        except (InvalidImage, InvalidItem, OSError, ValueError) as exc:
            report.skipped_invalid += 1
            report.errors.append(f"{rel}: {type(exc).__name__}")
    return report


def remove_dataset(db: Database, source: str, data_dir: Path | None = None) -> int:
    """Delete every imported item (and image file) of one dataset source. Wardrobe items are never touched."""
    from wardrobe import delete_item, list_items
    n = 0
    for it in [i for i in list_items(db, "inspiration") if i["source"] == source]:
        n += 1 if delete_item(db, it["id"], data_dir) else 0
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sample", help="import the bundled synthetic CC0 sample set")
    fp = sub.add_parser("fashionpedia", help="import a locally downloaded Fashionpedia subset")
    fp.add_argument("--annotations", required=True)
    fp.add_argument("--images-dir", required=True)
    fp.add_argument("--limit", type=int, default=200)
    fp.add_argument("--accept-terms", action="store_true", help="I have read the Fashionpedia terms of use")
    df = sub.add_parser("deepfashion", help="import locally available DeepFashion files (research agreement required)")
    df.add_argument("--anno-dir", required=True)
    df.add_argument("--images-dir", required=True)
    df.add_argument("--limit", type=int, default=200)
    df.add_argument("--agreement-signed", action="store_true", help="I have a signed DeepFashion release agreement and use it for non-commercial research")
    rm = sub.add_parser("remove", help="delete all imported items from one dataset source")
    rm.add_argument("source")
    args = ap.parse_args(argv)
    db = init_db()
    try:
        if args.cmd == "sample":
            print(import_sample(db).summary())
        elif args.cmd == "fashionpedia":
            print(import_fashionpedia(db, args.annotations, args.images_dir, accept_terms=args.accept_terms, limit=args.limit).summary())
        elif args.cmd == "deepfashion":
            print(import_deepfashion(db, args.anno_dir, args.images_dir, agreement_signed=args.agreement_signed, limit=args.limit).summary())
        else:
            print(f"Removed {remove_dataset(db, args.source)} item(s) from '{args.source}'.")
    except (PermissionError, FileNotFoundError) as exc:
        print(f"Import stopped: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
