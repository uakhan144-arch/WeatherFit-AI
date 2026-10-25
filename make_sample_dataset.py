"""Generate the tiny development dataset in ``sample_dataset/`` (synthetic flat-colour garment drawings, CC0).

The drawings are made by this script, so there are no third-party image rights to worry about. The annotation file uses the
same COCO/Fashionpedia layout as the real dataset, which means the real import path is what gets exercised.
Run:  python make_sample_dataset.py
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "sample_dataset"
W, H = 256, 320
RGB = {"white": (245, 245, 245), "black": (30, 30, 30), "navy": (28, 40, 90), "blue": (45, 95, 200), "light blue": (150, 190, 230),
       "beige": (225, 205, 170), "olive": (110, 118, 55), "red": (200, 50, 50), "grey": (130, 130, 130), "brown": (110, 72, 42),
       "maroon": (110, 28, 44), "mustard": (210, 170, 40), "green": (50, 140, 70), "pink": (235, 150, 180), "tan": (190, 150, 100)}
# (category name as in Fashionpedia, colour, attribute names)
ITEMS = [("shirt, blouse", "blue", []), ("shirt, blouse", "white", []), ("shirt, blouse", "light blue", ["stripe"]),
         ("top, t-shirt, sweatshirt", "white", []), ("top, t-shirt, sweatshirt", "olive", []), ("sweater", "maroon", []),
         ("jacket", "navy", []), ("jacket", "tan", []), ("coat", "grey", []), ("pants", "navy", []), ("pants", "beige", []),
         ("pants", "black", []), ("shorts", "khaki" if False else "tan", []), ("dress", "red", ["floral"]), ("skirt", "black", []),
         ("shoe", "white", []), ("shoe", "brown", []), ("shoe", "black", []), ("watch", "brown", []), ("glasses", "black", []),
         ("hat", "navy", []), ("bag, wallet", "brown", []), ("umbrella", "black", []), ("scarf", "mustard", [])]


def draw(cat: str, rgb: tuple[int, int, int]) -> tuple[Image.Image, list[int]]:
    img = Image.new("RGB", (W, H), (236, 238, 242))
    d = ImageDraw.Draw(img)
    o = (0, 0, 0)
    if cat in ("shirt, blouse", "top, t-shirt, sweatshirt", "sweater"):
        long_sleeve = cat != "top, t-shirt, sweatshirt"
        sl = 235 if long_sleeve else 150
        d.polygon([(90, 60), (166, 60), (215, 90), (sl, 200 if long_sleeve else 130), (205, 205 if long_sleeve else 135), (180, 110), (180, 270), (76, 270), (76, 110), (51, 205 if long_sleeve else 135), (21, 200 if long_sleeve else 130), (41, 90)], fill=rgb, outline=o)
        d.polygon([(110, 60), (146, 60), (128, 88)], fill=(236, 238, 242), outline=o)
        bbox = [21, 60, 235 - 21, 270 - 60]
    elif cat in ("jacket", "coat"):
        bot = 295 if cat == "coat" else 255
        d.polygon([(90, 55), (166, 55), (222, 90), (240, 215), (208, 220), (186, 120), (186, bot), (70, bot), (70, 120), (48, 220), (16, 215), (34, 90)], fill=rgb, outline=o)
        d.line([(128, 75), (128, bot)], fill=o, width=3)
        bbox = [16, 55, 224, bot - 55]
    elif cat == "pants":
        d.polygon([(72, 50), (184, 50), (200, 290), (140, 290), (128, 130), (116, 290), (56, 290)], fill=rgb, outline=o)
        bbox = [56, 50, 144, 240]
    elif cat == "shorts":
        d.polygon([(72, 80), (184, 80), (196, 190), (140, 190), (128, 140), (116, 190), (60, 190)], fill=rgb, outline=o)
        bbox = [60, 80, 136, 110]
    elif cat == "skirt":
        d.polygon([(90, 80), (166, 80), (210, 250), (46, 250)], fill=rgb, outline=o)
        bbox = [46, 80, 164, 170]
    elif cat == "dress":
        d.polygon([(100, 40), (156, 40), (170, 120), (215, 285), (41, 285), (86, 120)], fill=rgb, outline=o)
        bbox = [41, 40, 174, 245]
    elif cat == "shoe":
        d.polygon([(40, 190), (120, 190), (150, 220), (220, 232), (225, 262), (40, 262)], fill=rgb, outline=o)
        d.rectangle([40, 258, 225, 270], fill=(250, 250, 250), outline=o)
        bbox = [40, 190, 185, 80]
    elif cat == "watch":
        d.rectangle([108, 40, 148, 280], fill=(70, 70, 70), outline=o)
        d.ellipse([78, 110, 178, 210], fill=rgb, outline=o)
        d.ellipse([92, 124, 164, 196], fill=(240, 240, 240), outline=o)
        bbox = [78, 40, 100, 240]
    elif cat == "glasses":
        d.ellipse([30, 110, 120, 190], fill=rgb, outline=o)
        d.ellipse([136, 110, 226, 190], fill=rgb, outline=o)
        d.line([(120, 140), (136, 140)], fill=o, width=5)
        bbox = [30, 110, 196, 80]
    elif cat == "hat":
        d.pieslice([46, 100, 210, 260], 180, 360, fill=rgb, outline=o)
        d.rectangle([46, 178, 230, 190], fill=rgb, outline=o)
        bbox = [46, 100, 184, 90]
    elif cat == "bag, wallet":
        d.rectangle([48, 120, 208, 270], fill=rgb, outline=o)
        d.arc([88, 60, 168, 160], 180, 360, fill=o, width=6)
        bbox = [48, 60, 160, 210]
    elif cat == "umbrella":
        d.pieslice([20, 50, 236, 250], 180, 360, fill=rgb, outline=o)
        d.line([(128, 150), (128, 290)], fill=o, width=6)
        bbox = [20, 50, 216, 240]
    else:  # scarf
        d.polygon([(60, 70), (196, 70), (196, 120), (150, 120), (150, 290), (100, 290), (100, 120), (60, 120)], fill=rgb, outline=o)
        bbox = [60, 70, 136, 220]
    return img, bbox


def main() -> None:
    (OUT / "images").mkdir(parents=True, exist_ok=True)
    cats = {n: i + 1 for i, n in enumerate(sorted({c for c, _, _ in ITEMS}))}
    attr_names = sorted({a for _, _, attrs in ITEMS for a in attrs})
    doc = {"info": {"description": "WeatherFit AI synthetic sample dataset (Fashionpedia-style layout)", "license": "CC0-1.0", "year": 2026},
           "licenses": [{"id": 1, "name": "CC0-1.0", "url": "https://creativecommons.org/publicdomain/zero/1.0/"}],
           "categories": [{"id": i, "name": n, "supercategory": "synthetic"} for n, i in cats.items()],
           "attributes": [{"id": i + 1, "name": n, "supercategory": "textile pattern"} for i, n in enumerate(attr_names)],
           "images": [], "annotations": []}
    for n, (cat, colour, attrs) in enumerate(ITEMS, start=1):
        img, bbox = draw(cat, RGB[colour])
        name = f"sample_{n:03d}.png"
        img.save(OUT / "images" / name, optimize=True)
        doc["images"].append({"id": n, "file_name": name, "width": W, "height": H, "license": 1})
        doc["annotations"].append({"id": n, "image_id": n, "category_id": cats[cat], "bbox": [max(0, bbox[0]), max(0, bbox[1]), bbox[2], bbox[3]],
                                   "attribute_ids": [attr_names.index(a) + 1 for a in attrs]})
    (OUT / "annotations.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    (OUT / "LICENSE.txt").write_text("The images and annotations in this folder were generated by make_sample_dataset.py for WeatherFit AI development.\n"
                                     "They are released under CC0 1.0 (public domain dedication): https://creativecommons.org/publicdomain/zero/1.0/\n", encoding="utf-8")
    print(f"Wrote {len(ITEMS)} images + annotations.json to {OUT}")


if __name__ == "__main__":
    main()
