"""Plain data classes shared by the recommender, history, visuals, chat and UI."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

WARDROBE_LABEL = "From Your Wardrobe"
INSPIRATION_LABEL = "New Outfit Inspiration"
INSPIRATION_NOTE = "AI-suggested ideas - you may not own these items."
GARMENT_ROLES = ("top", "bottom", "onepiece", "outer", "footwear")  # these define an outfit's identity


@dataclass
class OutfitPiece:
    role: str                  # top | bottom | onepiece | outer | footwear | accessory
    category: str
    colour: str
    name: str = ""
    item_id: int | None = None
    image_path: str | None = None
    owned: bool = False        # True ONLY for rows that exist in the wardrobe table
    pattern: str = "solid"
    note: str = ""
    reference_path: str | None = None   # optional dataset image (inspiration only)

    @property
    def label(self) -> str:
        return self.name or " ".join(x for x in (self.colour, self.category) if x).title()


@dataclass
class Recommendation:
    kind: str                                   # wardrobe | inspiration
    pieces: list[OutfitPiece]                   # garments + footwear
    accessories: list[OutfitPiece] = field(default_factory=list)
    score: float = 0.0
    why: list[str] = field(default_factory=list)
    reuse_notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)   # things the weather calls for that the wardrobe lacks

    @property
    def label(self) -> str:
        return WARDROBE_LABEL if self.kind == "wardrobe" else INSPIRATION_LABEL

    @property
    def all_pieces(self) -> list[OutfitPiece]:
        return self.pieces + self.accessories

    @property
    def item_ids(self) -> list[int]:
        return sorted(p.item_id for p in self.all_pieces if p.item_id is not None)

    @property
    def signature(self) -> str:
        return signature_for(self)

    @property
    def colours(self) -> list[str]:
        return [p.colour for p in self.pieces if p.colour]

    @property
    def categories(self) -> list[str]:
        return [p.category for p in self.pieces]


def signature_for(rec: Recommendation) -> str:
    """Identity of an outfit COMBINATION (not its name): the sorted garment+footwear item IDs.

    Accessories are left out on purpose, so swapping only a watch does not make an outfit "new".
    Inspiration looks (no database IDs) use their category:colour pairs instead.
    """
    core = [p for p in rec.pieces if p.role in GARMENT_ROLES]
    if rec.kind == "wardrobe":
        ids = sorted(p.item_id for p in core if p.item_id is not None)
        signature = "w:" + "-".join(str(i) for i in ids)
        suggestions = sorted(f"{p.role}:{p.category}:{p.colour}" for p in core if not p.owned)
        return signature + ("|s:" + "|".join(suggestions) if suggestions else "")
    text = "|".join(sorted(f"{p.category}:{p.colour}" for p in core))
    return "i:" + hashlib.sha1(text.encode()).hexdigest()[:16]


@dataclass
class WeatherNeeds:
    """What the outfit has to cope with, distilled from forecast windows (origin + destination)."""
    feels_min: float
    feels_max: float
    feels_avg: float
    temp_max: float = 25.0
    rain_chance_max: float = 0.0
    precip_mm: float = 0.0
    wind_max_kph: float = 0.0
    uv_max: float = 0.0
    daytime: bool = True
    place: str = ""
    condition: str = ""
    departure: str = ""

    @property
    def swing(self) -> float:
        return self.feels_max - self.feels_min

    def to_dict(self) -> dict[str, Any]:
        return {k: (round(v, 1) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


@dataclass
class RecommendationResult:
    wardrobe: list[Recommendation] = field(default_factory=list)
    inspiration: Recommendation | None = None
    status: str = "ok"                          # ok | limited | insufficient | empty
    messages: list[str] = field(default_factory=list)
    yesterday: dict[str, Any] | None = None     # what history says was recommended/worn yesterday (for chat)
