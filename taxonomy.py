"""Shared clothing vocabulary: categories (with slot / warmth / formality), colours, patterns, seasons, occasions.

Everything that validates or scores clothing metadata uses this one module so the database, the label
suggesters, the dataset importer and the recommender always agree on the words they use.
"""
from __future__ import annotations

import re

# category -> (slot, warmth 1-5, formality 1-3)   slot: top | bottom | onepiece | outer | footwear | accessory
CATEGORIES: dict[str, tuple[str, int, int]] = {
    "t-shirt": ("top", 1, 1), "polo": ("top", 1, 2), "shirt": ("top", 2, 2), "kurta": ("top", 2, 2),
    "blouse": ("top", 1, 2), "top": ("top", 1, 1), "sweater": ("top", 4, 2), "hoodie": ("top", 4, 1),
    "trousers": ("bottom", 2, 2), "chinos": ("bottom", 2, 2), "jeans": ("bottom", 3, 1), "joggers": ("bottom", 2, 1),
    "shorts": ("bottom", 1, 1), "skirt": ("bottom", 1, 2),
    "dress": ("onepiece", 2, 2), "jumpsuit": ("onepiece", 2, 2),
    "jacket": ("outer", 3, 2), "blazer": ("outer", 3, 3), "coat": ("outer", 5, 3), "cardigan": ("outer", 3, 2),
    "windbreaker": ("outer", 2, 1),
    "sneakers": ("footwear", 0, 1), "formal shoes": ("footwear", 0, 3), "loafers": ("footwear", 0, 2),
    "sandals": ("footwear", 0, 1), "boots": ("footwear", 0, 2),
    "watch": ("accessory", 0, 2), "sunglasses": ("accessory", 0, 1), "cap": ("accessory", 0, 1),
    "umbrella": ("accessory", 0, 1), "bag": ("accessory", 0, 2), "backpack": ("accessory", 0, 1),
    "scarf": ("accessory", 1, 2), "beanie": ("accessory", 1, 1), "belt": ("accessory", 0, 2), "tie": ("accessory", 0, 3),
}
ALIASES = {
    "tee": "t-shirt", "tshirt": "t-shirt", "t shirt": "t-shirt", "tees": "t-shirt", "pants": "trousers", "trouser": "trousers",
    "pant": "trousers", "denim": "jeans", "jean": "jeans", "shoe": "sneakers", "shoes": "sneakers", "trainers": "sneakers",
    "sneaker": "sneakers", "formal shoe": "formal shoes", "oxford": "formal shoes", "derby": "formal shoes",
    "flip flops": "sandals", "sandal": "sandals", "boot": "boots", "loafer": "loafers", "tank top": "top", "tank": "top",
    "sweatshirt": "hoodie", "pullover": "sweater", "jumper": "sweater", "kurti": "kurta", "shirts": "shirt",
    "blouse, shirt": "shirt", "shirt, blouse": "shirt", "top, t-shirt, sweatshirt": "t-shirt", "jackets": "jacket",
    "glasses": "sunglasses", "hat": "cap", "handbag": "bag", "purse": "bag", "wristwatch": "watch", "trench": "coat",
    "parka": "coat", "overcoat": "coat", "vest": "top", "bomber": "jacket", "denim jacket": "jacket", "raincoat": "windbreaker",
    "shorts, short pants": "shorts", "dress shirt": "shirt", "chino": "chinos", "track pants": "joggers", "sweatpants": "joggers",
}
SLOTS = ("top", "bottom", "onepiece", "outer", "footwear", "accessory")

COLOURS = ["black", "white", "grey", "navy", "blue", "light blue", "teal", "green", "olive", "yellow", "mustard", "orange",
           "red", "maroon", "pink", "purple", "brown", "beige", "cream", "khaki", "tan", "multicolour"]
NEUTRALS = {"black", "white", "grey", "navy", "beige", "cream", "khaki", "tan", "brown", "olive"}
FAMILY = {"navy": "blue", "light blue": "blue", "blue": "blue", "teal": "teal", "olive": "green", "green": "green",
          "mustard": "yellow", "yellow": "yellow", "maroon": "red", "red": "red", "tan": "brown", "brown": "brown",
          "khaki": "beige", "cream": "beige", "beige": "beige"}
# Chromatic pairs that look deliberate together (everything with a neutral is also fine).
GOOD_PAIRS = {frozenset(p) for p in [("blue", "orange"), ("blue", "yellow"), ("blue", "red"), ("blue", "pink"), ("green", "pink"),
                                     ("green", "yellow"), ("purple", "yellow"), ("teal", "pink"), ("red", "teal"), ("blue", "green")]}
COLOUR_ALIASES = {"dark blue": "navy", "navy blue": "navy", "sky blue": "light blue", "pale blue": "light blue", "baby blue": "light blue",
                  "grey": "grey", "gray": "grey", "charcoal": "grey", "off-white": "cream", "off white": "cream", "ivory": "cream",
                  "burgundy": "maroon", "wine": "maroon", "dark green": "green", "bottle green": "green", "lime": "green",
                  "gold": "mustard", "camel": "tan", "coffee": "brown", "violet": "purple", "magenta": "pink", "rust": "orange",
                  "multi": "multicolour", "multicolor": "multicolour", "colorful": "multicolour", "colourful": "multicolour"}
PATTERNS = ["solid", "striped", "checked", "printed", "floral", "polka-dot", "textured"]
PATTERN_ALIASES = {"plain": "solid", "stripe": "striped", "stripes": "striped", "check": "checked", "plaid": "checked",
                   "checkered": "checked", "gingham": "checked", "print": "printed", "graphic": "printed", "dots": "polka-dot",
                   "polka dot": "polka-dot", "dotted": "polka-dot", "no pattern": "solid", "none": "solid"}
FABRICS = ["cotton", "linen", "denim", "wool", "knit", "polyester", "silk", "leather", "synthetic", "canvas", "suede", "other"]
SEASONS = ["summer", "winter", "monsoon", "all"]
OCCASION_TAGS = ["casual", "college", "office", "travel", "party", "date", "wedding", "outdoor", "formal"]
STYLES = ["Casual", "Minimal", "Sporty", "Formal", "Streetwear", "Smart casual", "Ethnic fusion"]
GENDERS = ["men", "women", "unisex"]
OWNERSHIPS = ("wardrobe", "inspiration")

# App occasion -> tags an item may carry to be acceptable ("any" items match everything)
OCCASION_MAP = {
    "Daily casual": {"casual", "college", "travel"}, "College": {"college", "casual"}, "Office": {"office", "formal"},
    "Travel": {"travel", "casual"}, "Party": {"party", "date"}, "Date": {"date", "party", "casual"},
    "Wedding": {"wedding", "formal", "party"}, "Outdoor activity": {"outdoor"},
}


def slot_of(category: str) -> str:
    return CATEGORIES[category][0]


def warmth_of(category: str) -> int:
    return CATEGORIES[category][1]


def formality_of(category: str) -> int:
    return CATEGORIES[category][2]


def normalize_category(value: str | None) -> str | None:
    """Return a canonical category or None when the text isn't recognised."""
    if not value:
        return None
    v = re.sub(r"\s+", " ", str(value).strip().lower())
    if v in CATEGORIES:
        return v
    if v in ALIASES:
        return ALIASES[v]
    singular = v[:-1] if v.endswith("s") else v
    if singular in CATEGORIES:
        return singular
    return ALIASES.get(singular)


def normalize_colour(value: str | None) -> str:
    """Canonical colour name; unknown colours are kept (lower-cased, trimmed) rather than rejected."""
    if not value or not str(value).strip():
        return ""
    v = re.sub(r"\s+", " ", str(value).strip().lower())[:40]
    return COLOUR_ALIASES.get(v, v)


def normalize_pattern(value: str | None) -> str:
    v = (value or "").strip().lower()
    v = PATTERN_ALIASES.get(v, v)
    return v if v in PATTERNS else "solid"


def normalize_choice(value: str | None, allowed: list[str], default: str) -> str:
    v = (value or "").strip().lower()
    return v if v in allowed else default


def normalize_multi(value: str | list[str] | None, allowed: list[str], default: str) -> str:
    parts = re.split(r"[,;/]", value) if isinstance(value, str) else list(value or [])
    out = []
    for p in parts:
        p = p.strip().lower()
        if p in allowed and p not in out:
            out.append(p)
    return ",".join(out) if out else default


def colour_family(colour: str) -> str:
    return FAMILY.get(colour, colour)


def is_neutral(colour: str) -> bool:
    return colour in NEUTRALS or not colour


def colour_harmony(a: str, b: str) -> float:
    """0..1 heuristic: neutrals go with everything, same family is safe, a few chromatic pairs are classic."""
    if not a or not b:
        return 0.7
    if is_neutral(a) or is_neutral(b) or a == "multicolour" or b == "multicolour":
        return 1.0
    fa, fb = colour_family(a), colour_family(b)
    if fa == fb:
        return 0.85
    if frozenset((fa, fb)) in GOOD_PAIRS:
        return 0.7
    return 0.25
