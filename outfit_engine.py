"""Weather-aware outfit recommender that prefers clothes you actually own, remembers history, and never mixes up
"you own this" with "you might like this".

``recommend()`` returns:
  * ``wardrobe``    - up to N combinations built ONLY from rows in the wardrobe table (``owned=True`` pieces),
  * ``inspiration`` - one head-to-toe idea with exact colours/categories that you may NOT own (``owned=False``),
  * ``status`` / ``messages`` - ok | limited | insufficient | empty, with a plain-language reason.

History rules (see outfit_history): yesterday's combination is excluded outright; combinations used in the last
``repeat_days`` days are avoided whenever an alternative exists; when the wardrobe is too small the app says why an
earlier item/outfit is reused.
"""
from __future__ import annotations

import itertools
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import taxonomy as tx
from db import Database
from outfit_history import item_last_worn, recent_signatures, outfits_on, today_signatures, yesterday_signatures
from outfit_models import OutfitPiece, Recommendation, RecommendationResult, WeatherNeeds, signature_for
from trip import WindowSummary, combine
from wardrobe import display_name, list_items

POOL_SIZE = 7
COMFORT_WEIGHT = {"comfort first": 1.0, "balanced": 0.6, "polished first": 0.3}


def _rng(lo: float, hi: float) -> str:
    return f"{lo:.0f}°C" if round(lo) == round(hi) else f"{lo:.0f}-{hi:.0f}°C"


# ------------------------------------------------------------------ weather
def needs_from_windows(origin: WindowSummary, dest: WindowSummary | None = None) -> WeatherNeeds:
    """One outfit must work at both ends of a trip, so use the combined (worst-case) view of the forecast windows."""
    w = combine([origin] + ([dest] if dest else []))
    return WeatherNeeds(feels_min=w.feels_min, feels_max=w.feels_max, feels_avg=w.feels_avg, temp_max=w.temp_max,
                        rain_chance_max=w.rain_chance_max, precip_mm=w.precip_total_mm, wind_max_kph=w.wind_max_kph,
                        uv_max=w.uv_max, daytime=w.daytime, place=w.place, condition=w.main_condition, departure=w.start)


def warmth_bounds(feels_avg: float) -> tuple[int, int]:
    """Allowed total warmth (top/one-piece + outer layer) for a feels-like temperature."""
    if feels_avg >= 32:
        return 1, 2
    if feels_avg >= 25:
        return 1, 3
    if feels_avg >= 18:
        return 2, 4
    if feels_avg >= 10:
        return 4, 7
    return 6, 10


def outer_need(n: WeatherNeeds) -> str:
    if n.feels_min <= 12:
        return "required"
    if n.feels_min < 22 or n.swing >= 8 or n.rain_chance_max >= 60 or n.wind_max_kph >= 28:
        return "recommended"
    return "none"


def weather_seasons(n: WeatherNeeds) -> set[str] | None:
    """Seasons an item may be tagged with to suit the weather; None = mild, anything goes."""
    s: set[str] = set()
    if n.feels_avg >= 27:
        s.add("summer")
    elif n.feels_avg <= 16:
        s.add("winter")
    if n.rain_chance_max >= 60:
        s.add("monsoon")
    return s or None


# ------------------------------------------------------------------ profile helpers
def _profile(profile: dict[str, Any]) -> dict[str, Any]:
    gender = str(profile.get("gender") or "any").strip().lower()
    gender = {"man": "men", "male": "men", "woman": "women", "female": "women"}.get(gender, gender)
    prefs = [tx.normalize_colour(c) for c in str(profile.get("colours") or "").replace(";", ",").split(",") if c.strip()]
    avoid = {a.strip().lower() for a in str(profile.get("avoid") or "").replace(";", ",").split(",") if a.strip()}
    return {"occasion": profile.get("occasion") or "Daily casual", "style": str(profile.get("style") or "Casual"),
            "budget": profile.get("budget") or "Mid-range", "gender": gender if gender in ("men", "women") else "any",
            "pref_colours": [c for c in prefs if c], "pref_families": {tx.colour_family(c) for c in prefs if c},
            "avoid": avoid, "comfort_w": COMFORT_WEIGHT.get(str(profile.get("comfort") or "balanced").lower(), 0.6)}


def _tags(value: str) -> set[str]:
    return {t for t in str(value or "").split(",") if t}


# ------------------------------------------------------------------ eligibility & scoring
def _eligible(item: dict[str, Any], n: WeatherNeeds, p: dict[str, Any], occasion_tags: set[str] | None) -> bool:
    cat = item["category"]
    if cat not in tx.CATEGORIES:
        return False
    if p["gender"] != "any" and item["gender_presentation"] not in (p["gender"], "unisex"):
        return False
    if p["avoid"] & {cat, item["fabric"], item["colour"], (item["name"] or "").lower()}:
        return False
    seasons, wanted = _tags(item["season"]), weather_seasons(n)
    if wanted is not None and "all" not in seasons and not (seasons & wanted):
        return False
    if occasion_tags is not None:
        tags = _tags(item["occasion"])
        if "any" not in tags and not (tags & occasion_tags):
            return False
    if cat == "shorts" and n.feels_avg < 22:
        return False
    if cat == "sandals" and (n.rain_chance_max >= 60 or n.feels_avg < 18):
        return False
    if cat == "boots" and n.feels_avg >= 30:
        return False
    if tx.slot_of(cat) == "footwear" and n.rain_chance_max >= 60 and item["fabric"] in ("suede", "canvas"):
        return False
    return True


def _item_score(item: dict[str, Any], n: WeatherNeeds, p: dict[str, Any], worn: dict[int, str], today: date, window: int) -> float:
    s = item["comfort"] / 5 * p["comfort_w"]
    style = item["style"].lower()
    if style == p["style"].lower():
        s += 0.5
    elif {style, p["style"].lower()} <= {"casual", "smart casual", "minimal"}:
        s += 0.2
    if p["pref_families"] and tx.colour_family(item["colour"]) in p["pref_families"]:
        s += 0.6
    fab = item["fabric"]
    if n.feels_avg >= 28:
        s += 0.3 if fab in ("cotton", "linen") else (-0.8 if fab in ("wool", "knit", "leather") else 0)
    elif n.feels_avg <= 16 and fab in ("wool", "knit"):
        s += 0.3
    last = worn.get(item["id"])
    if last:
        days = max(1, (today - date.fromisoformat(last)).days)
        s -= 0.8 * max(0.0, 1 - days / (window + 1))
    s -= 0.02 * item["usage_count"]
    return s


def _combo_score(parts: list[tuple[OutfitPiece, dict[str, Any], float]], n: WeatherNeeds, p: dict[str, Any], need: str) -> float:
    items = [x[1] for x in parts]
    base = sum(x[2] for x in parts) / len(parts)
    colours = [i["colour"] for i in items]
    pairs = list(itertools.combinations(colours, 2))
    harmony = sum(tx.colour_harmony(a, b) for a, b in pairs) / len(pairs) if pairs else 1.0
    score = base + 1.5 * harmony
    if sum(1 for i in items if i["pattern"] != "solid" and tx.slot_of(i["category"]) != "footwear") >= 2:
        score -= 0.6
    form = [tx.formality_of(i["category"]) for i in items]
    if max(form) - min(form) >= 2:
        score -= 0.4
    has_outer = any(tx.slot_of(i["category"]) == "outer" for i in items)
    if need == "recommended" and has_outer:
        score += 0.5
    if n.rain_chance_max >= 60 and any(i["category"] in ("windbreaker", "jacket", "coat") for i in items):
        score += 0.4
    if p["occasion"] in ("Office", "Wedding") and sum(form) / len(form) >= 2:
        score += 0.3
    return score


def _piece(item: dict[str, Any], role: str, owned: bool = True) -> OutfitPiece:
    return OutfitPiece(role=role, category=item["category"], colour=item["colour"], name=display_name(item), item_id=item["id"],
                       image_path=item["image_path"], owned=owned, pattern=item["pattern"])


# ------------------------------------------------------------------ main entry
def recommend(db: Database, needs: WeatherNeeds, profile: dict[str, Any], today: date | None = None, *, repeat_days: int = 7,
              max_options: int = 3, exclude_signatures: Iterable[str] = (), variant: int = 0) -> RecommendationResult:
    today = today or date.today()
    p = _profile(profile)
    result = RecommendationResult(yesterday=yesterday_summary(db, today))
    all_items = list_items(db, "wardrobe")
    result.inspiration = build_inspiration(db, needs, profile, today, repeat_days=repeat_days, variant=variant)
    if not all_items:
        result.status = "empty"
        result.messages.append("Your wardrobe is empty, so I can only show New Outfit Inspiration. Add photos in the Wardrobe tab to get looks built from your own clothes.")
        return result

    worn = item_last_worn(db, today, repeat_days)
    recent = recent_signatures(db, today, repeat_days, "wardrobe")
    yest = yesterday_signatures(db, today, "wardrobe")
    excluded = set(exclude_signatures) | today_signatures(db, today, "wardrobe") if variant else set(exclude_signatures)

    strict_tags = tx.OCCASION_MAP.get(p["occasion"], {"casual"})
    combos, relaxed = _build_combos(all_items, needs, p, worn, today, repeat_days, strict_tags), False
    completed = False
    if not combos:
        combos, relaxed = _build_combos(all_items, needs, p, worn, today, repeat_days, None), True
    if not combos:
        combos = _build_completed_combos(all_items, needs, p, worn, today, repeat_days, strict_tags, result.inspiration)
        completed = bool(combos)
    if not combos:
        combos = _build_completed_combos(all_items, needs, p, worn, today, repeat_days, None, result.inspiration)
        relaxed, completed = True, bool(combos)
    if not combos:
        result.status = "insufficient"
        result.messages += _diagnose(all_items, needs, p)
        return result
    if completed:
        result.status = "limited"
        result.messages.append("Your wardrobe is missing one or more suitable pieces, so I completed the outfit with clearly marked suggestions.")
    if relaxed:
        result.messages.append(f"None of your clothes are tagged for '{p['occasion']}', so these are the closest matches from your wardrobe - they may be too casual or too formal.")

    allowed = [c for c in combos if c[0].signature not in yest and c[0].signature not in excluded]
    if not allowed:
        result.status = "limited"
        result.messages.append("The only outfit your wardrobe can make for these conditions is the one from yesterday (or one you've already seen today), so I'm not repeating it. "
                               "Add another top, bottom or pair of shoes - or use the New Outfit Inspiration below.")
        return result
    fresh = [c for c in allowed if c[0].signature not in recent]
    chosen: list[tuple[Recommendation, int]]
    if fresh:
        chosen = _pick_diverse(fresh, max_options)
    else:
        result.status = "limited"
        oldest = sorted(allowed, key=lambda c: (recent[c[0].signature]["last_date"], -c[0].score))
        chosen = _pick_diverse(oldest, max_options, keep_order=True)
        result.messages.append(f"Your wardrobe is limited: every suitable combination was already recommended or worn in the last {repeat_days} days "
                               f"(yesterday's outfit is still excluded). I picked the one used longest ago.")
    for rec, pools in chosen:
        _finish(db, rec, pools, needs, p, worn, recent, today, repeat_days, all_items)
        if relaxed:
            rec.warnings.append(f"Not tagged for {p['occasion']} - closest available match.")
        if completed and any(not pc.owned for pc in rec.pieces):
            rec.warnings.append("Suggested pieces are not in your wardrobe; each piece is labelled accordingly.")
        result.wardrobe.append(rec)
    return result


def _build_combos(items: list[dict[str, Any]], n: WeatherNeeds, p: dict[str, Any], worn: dict[int, str], today: date, window: int,
                  occasion_tags: set[str] | None, force_ids: set[int] | None = None) -> list[tuple[Recommendation, dict[str, int]]]:
    pools: dict[str, list[tuple[dict[str, Any], float]]] = {s: [] for s in ("top", "bottom", "onepiece", "outer", "footwear")}
    for it in items:
        if it["category"] not in tx.CATEGORIES:
            continue
        slot = tx.slot_of(it["category"])
        if slot in pools and _eligible(it, n, p, occasion_tags):
            pools[slot].append((it, _item_score(it, n, p, worn, today, window)))
    counts = {s: len(v) for s, v in pools.items()}
    for s in pools:
        pools[s].sort(key=lambda t: (-t[1], t[0]["id"]))
        full, keep = pools[s], pools[s][: (4 if s == "outer" else POOL_SIZE)]
        keep += [t for t in full[len(keep):] if force_ids and t[0]["id"] in force_ids]  # an anchor item must stay in the pool
        pools[s] = keep
    need = outer_need(n)
    lo, hi = warmth_bounds(n.feels_avg)
    base_sets: list[list[tuple[dict[str, Any], float, str]]] = []
    for (t, ts), (b, bs) in itertools.product(pools["top"], pools["bottom"]):
        base_sets.append([(t, ts, "top"), (b, bs, "bottom")])
    for d, ds in pools["onepiece"]:
        base_sets.append([(d, ds, "onepiece")])
    out: list[tuple[Recommendation, dict[str, int]]] = []
    outer_opts: list[tuple[dict[str, Any], float] | None] = [None] + pools["outer"] if need != "none" or n.feels_avg < 25 else [None]
    for base, (shoe, ss), outer in itertools.product(base_sets, pools["footwear"], outer_opts):
        parts = list(base) + [(shoe, ss, "footwear")] + ([(outer[0], outer[1], "outer")] if outer else [])
        upper = [x for x in parts if x[2] in ("top", "onepiece")][0][0]
        warmth = tx.warmth_of(upper["category"]) + (tx.warmth_of(outer[0]["category"]) if outer else 0)
        if not lo <= warmth <= hi:
            continue
        if need == "required" and not outer and tx.warmth_of(upper["category"]) < 4:
            continue
        pieces = [_piece(it, role) for it, _, role in parts]
        triples = [(pc, it, sc) for pc, (it, sc, _) in zip(pieces, parts)]
        rec = Recommendation(kind="wardrobe", pieces=pieces, score=_combo_score(triples, n, p, need))
        out.append((rec, counts))
    out.sort(key=lambda c: (-c[0].score, c[0].signature))
    return out


def _build_completed_combos(items: list[dict[str, Any]], n: WeatherNeeds, p: dict[str, Any], worn: dict[int, str], today: date,
                            window: int, occasion_tags: set[str] | None, inspiration: Recommendation | None,
                            force_ids: set[int] | None = None) -> list[tuple[Recommendation, dict[str, int]]]:
    """Complete an outfit with labelled suggestions when suitable wardrobe pieces are unavailable."""
    if inspiration is None:
        return []
    pools: dict[str, list[tuple[dict[str, Any], float]]] = {s: [] for s in ("top", "bottom", "onepiece", "outer", "footwear")}
    for item in items:
        if item["category"] in tx.CATEGORIES:
            slot = tx.slot_of(item["category"])
            if slot in pools and _eligible(item, n, p, occasion_tags):
                pools[slot].append((item, _item_score(item, n, p, worn, today, window)))
    counts = {slot: len(pool) for slot, pool in pools.items()}
    for slot, pool in pools.items():
        pool.sort(key=lambda entry: (-entry[1], entry[0]["id"]))
        pools[slot] = pool[: (4 if slot == "outer" else POOL_SIZE)]
        pools[slot] += [entry for entry in pool[len(pools[slot]):] if force_ids and entry[0]["id"] in force_ids]

    suggestions = {piece.role: piece for piece in inspiration.pieces}
    generic = _inspiration_categories(n, "Daily casual", p["gender"], p["style"])
    default_suggestions = {
        "top": (generic["top"], "white"),
        "bottom": (generic["bottom"], "navy"),
        "footwear": (generic["shoes"], "white"),
    }
    if generic.get("outer"):
        default_suggestions["outer"] = (generic["outer"], "grey")
    for role, (category, colour) in default_suggestions.items():
        suggestions.setdefault(role, OutfitPiece(role=role, category=category, colour=colour))

    def choices(slot: str) -> list[tuple[OutfitPiece, dict[str, Any], float]]:
        out = [(_piece(item, slot), item, score) for item, score in pools[slot]]
        suggested = suggestions.get(slot)
        if suggested:
            item = {"category": suggested.category, "colour": suggested.colour, "pattern": suggested.pattern}
            piece = OutfitPiece(role=slot, category=suggested.category, colour=suggested.colour,
                                pattern=suggested.pattern, note=suggested.note)
            out.append((piece, item, -0.5))
        return out

    tops, bottoms, shoes = choices("top"), choices("bottom"), choices("footwear")
    onepieces = choices("onepiece")
    bases: list[list[tuple[OutfitPiece, dict[str, Any], float]]] = [
        [top, bottom] for top, bottom in itertools.product(tops, bottoms)
    ]
    bases.extend([[onepiece] for onepiece in onepieces])
    outer_choices: list[tuple[OutfitPiece, dict[str, Any], float] | None] = [None]
    if outer_need(n) != "none" or n.feels_avg < 25:
        outer_choices.extend(choices("outer"))

    lo, hi = warmth_bounds(n.feels_avg)
    out: list[tuple[Recommendation, dict[str, int]]] = []
    for base, shoe, outer in itertools.product(bases, shoes, outer_choices):
        parts = list(base) + [shoe] + ([outer] if outer else [])
        if not any(piece.owned for piece, _, _ in parts):
            continue
        upper = next(item for piece, item, _ in parts if piece.role in ("top", "onepiece"))
        warmth = tx.warmth_of(upper["category"]) + (tx.warmth_of(outer[1]["category"]) if outer else 0)
        if not lo <= warmth <= hi or (outer_need(n) == "required" and not outer and tx.warmth_of(upper["category"]) < 4):
            continue
        rec = Recommendation(
            kind="wardrobe",
            pieces=[piece for piece, _, _ in parts],
            score=_combo_score(parts, n, p, outer_need(n)),
        )
        for piece in rec.pieces:
            if not piece.owned:
                rec.missing.append(f"Suggested {piece.category} - not in your wardrobe; add a suitable {piece.role} to build this look from your clothes.")
        out.append((rec, counts))
    out.sort(key=lambda combo: (-combo[0].score, combo[0].signature))
    return out


def _pick_diverse(cands: list[tuple[Recommendation, dict[str, int]]], k: int, keep_order: bool = False):
    chosen: list[tuple[Recommendation, dict[str, int]]] = []
    for c in cands:
        ids = set(c[0].item_ids)
        if all(len(ids ^ set(x[0].item_ids)) >= 4 for x in chosen):  # differ by >= 2 items from the ones already picked
            chosen.append(c)
        if len(chosen) == k:
            return chosen
    for c in cands:  # not enough diverse options: fill up with the next best
        if c not in chosen:
            chosen.append(c)
        if len(chosen) == k:
            break
    return chosen


# ------------------------------------------------------------------ accessories, explanations, reuse notes
ACCESSORY_NEEDS = {"umbrella": ("rain", "Umbrella"), "sunglasses": ("sun", "Sunglasses"), "cap": ("sun", "Cap"),
                   "scarf": ("cold", "Scarf"), "beanie": ("cold", "Beanie")}


def _wanted_accessories(n: WeatherNeeds, occasion: str) -> tuple[list[str], list[str]]:
    """(must-have, nice-to-have) accessory categories for the conditions."""
    must: list[str] = []
    if n.rain_chance_max >= 30:
        must.append("umbrella")
    if n.daytime and (n.uv_max >= 6 or n.temp_max >= 30):
        must.append("sunglasses")
        if occasion not in ("Office", "Wedding", "Date", "Party"):
            must.append("cap")
    if n.feels_min <= 8:
        must += ["scarf", "beanie"]
    nice = {"Office": ["watch", "belt"], "Travel": ["backpack", "watch"], "Party": ["watch"], "Date": ["watch"], "Wedding": ["watch"]}.get(occasion, ["watch"])
    return must, nice


def _finish(db: Database, rec: Recommendation, counts: dict[str, int], n: WeatherNeeds, p: dict[str, Any], worn: dict[int, str],
            recent: dict[str, dict[str, str]], today: date, window: int, all_items: list[dict[str, Any]]) -> None:
    core_colours = [pc.colour for pc in rec.pieces]
    acc_items = [i for i in all_items if i["category"] in tx.CATEGORIES and tx.slot_of(i["category"]) == "accessory"
                 and (p["gender"] == "any" or i["gender_presentation"] in (p["gender"], "unisex")) and not (p["avoid"] & {i["category"], i["colour"]})]
    must, nice = _wanted_accessories(n, p["occasion"])
    for cat in must + nice:
        cands = [i for i in acc_items if i["category"] == cat]
        if cands:
            best = max(cands, key=lambda i: (sum(tx.colour_harmony(i["colour"], c) for c in core_colours), i["comfort"], -i["id"]))
            rec.accessories.append(_piece(best, "accessory"))
        elif cat in must:
            rec.missing.append(f"{cat.title()} - not in your wardrobe ({_why_acc(cat, n)}); consider carrying one.")
    # ---- why it works
    lo, hi = warmth_bounds(n.feels_avg)
    upper = next(pc for pc in rec.pieces if pc.role in ("top", "onepiece"))
    outer = next((pc for pc in rec.pieces if pc.role == "outer"), None)
    warm = tx.warmth_of(upper.category) + (tx.warmth_of(outer.category) if outer else 0)
    rec.why.append(f"Weather: feels like {_rng(n.feels_min, n.feels_max)} ({n.condition.lower() or 'forecast'}); this layering (warmth {warm}) suits that range.")
    if outer:
        rec.why.append(f"The {outer.label.lower()} covers the cooler/windier moments and can be taken off.")
    if n.rain_chance_max >= 60:
        rec.why.append(f"Rain chance reaches {n.rain_chance_max:.0f}%, so sandals and suede/canvas shoes were left out.")
    pcs = [pc for pc in rec.pieces]
    pair = [tx.colour_harmony(a.colour, b.colour) for a, b in itertools.combinations(pcs, 2)]
    pref = [pc for pc in pcs if p["pref_families"] and tx.colour_family(pc.colour) in p["pref_families"]]
    rec.why.append("Colours: " + ", ".join(f"{pc.colour or 'unspecified'} {pc.category}" for pc in pcs) +
                   (" - neutrals and related shades that work together." if (sum(pair) / len(pair) if pair else 1) >= 0.85 else " - a bolder colour mix; keep the rest simple."))
    if pref:
        rec.why.append(f"Includes your preferred colour ({', '.join(sorted({pc.colour for pc in pref}))}).")
    rec.why.append(f"Occasion: items are tagged for {p['occasion'].lower()}.")
    owned_pieces = [pc for pc in pcs if pc.owned and pc.item_id is not None]
    if owned_pieces:
        rec.why.append("Comfort: your comfort ratings for these wardrobe pieces average "
                       f"{sum(next(i for i in all_items if i['id'] == pc.item_id)['comfort'] for pc in owned_pieces) / len(owned_pieces):.1f}/5.")
    if rec.signature in recent:
        rec.why.append(f"Previously used on {recent[rec.signature]['last_date']} (within the last {window} days).")
    else:
        rec.why.append(f"This exact combination hasn't been recommended or worn in the last {window} days.")
    # ---- reuse notes (honest explanation when something recent comes back)
    slot_of_role = {"top": "top", "bottom": "bottom", "onepiece": "one-piece", "outer": "outer layer", "footwear": "pair of shoes"}
    for pc in pcs:
        last = worn.get(pc.item_id) if pc.item_id is not None else None
        if not last:
            continue
        days = (today - date.fromisoformat(last)).days
        pool_n = counts.get(pc.role, 0)
        if pc.role in counts and pool_n <= 1:
            reason = f"it's the only suitable {slot_of_role.get(pc.role, pc.role)} in your wardrobe for these conditions"
        elif rec.signature in recent:
            reason = "every other suitable combination has been used recently too"
        else:
            reason = "it was the best match - the full combination is still new"
        rec.reuse_notes.append(f"{pc.label} was last worn {days} day{'s' if days != 1 else ''} ago ({last}); reused because {reason}.")


def _why_acc(cat: str, n: WeatherNeeds) -> str:
    return {"umbrella": f"rain chance up to {n.rain_chance_max:.0f}%", "sunglasses": f"UV up to {n.uv_max:.0f}", "cap": f"UV up to {n.uv_max:.0f}",
            "scarf": f"feels as cold as {n.feels_min:.0f}°C", "beanie": f"feels as cold as {n.feels_min:.0f}°C"}.get(cat, "useful today")


def _diagnose(items: list[dict[str, Any]], n: WeatherNeeds, p: dict[str, Any]) -> list[str]:
    by_slot: dict[str, int] = {}
    for it in items:
        if it["category"] in tx.CATEGORIES:
            by_slot[tx.slot_of(it["category"])] = by_slot.get(tx.slot_of(it["category"]), 0) + 1
    msgs = [f"I couldn't build a complete outfit from your wardrobe for {p['occasion'].lower()} at feels-like {n.feels_min:.0f}-{n.feels_max:.0f}°C "
            f"with {n.rain_chance_max:.0f}% rain chance."]
    if not by_slot.get("footwear"):
        msgs.append("You have no footwear saved yet.")
    if not by_slot.get("onepiece") and not (by_slot.get("top") and by_slot.get("bottom")):
        msgs.append("An outfit needs a top and a bottom (or a dress/one-piece) - one of these is missing.")
    if n.feels_min <= 12 and not by_slot.get("outer"):
        msgs.append("It will be cold and you have no jacket or coat saved.")
    msgs.append("Items may also have been ruled out by the weather (e.g. shorts when it's cool), your 'avoid' list or the gender/season tags. "
                "See New Outfit Inspiration below, or add suitable pieces in the Wardrobe tab.")
    return msgs


def yesterday_summary(db: Database, today: date) -> dict[str, Any] | None:
    from datetime import timedelta
    y = today - timedelta(days=1)
    rows = outfits_on(db, y, ["worn"], "wardrobe") or outfits_on(db, y, ["recommended"], "wardrobe")
    if not rows:
        return None
    o = rows[0]
    return {"date": y.isoformat(), "status": o["status"], "items": [f"{i['colour']} {i['category']}".strip() for i in o["items"]], "outfit_id": o["id"]}


# ------------------------------------------------------------------ inspiration (items the user may NOT own)
PALETTES = [
    {"top": "white", "bottom": "navy", "shoes": "white", "outer": "grey", "accent": "brown"},
    {"top": "light blue", "bottom": "beige", "shoes": "brown", "outer": "navy", "accent": "brown"},
    {"top": "olive", "bottom": "black", "shoes": "white", "outer": "black", "accent": "black"},
    {"top": "cream", "bottom": "khaki", "shoes": "tan", "outer": "olive", "accent": "tan"},
    {"top": "grey", "bottom": "navy", "shoes": "black", "outer": "black", "accent": "black"},
    {"top": "maroon", "bottom": "beige", "shoes": "white", "outer": "navy", "accent": "brown"},
    {"top": "blue", "bottom": "black", "shoes": "white", "outer": "grey", "accent": "grey"},
    {"top": "mustard", "bottom": "navy", "shoes": "brown", "outer": "grey", "accent": "brown"},
]


def _inspiration_categories(n: WeatherNeeds, occasion: str, gender: str, style: str) -> dict[str, str]:
    t = n.feels_avg
    rainy = n.rain_chance_max >= 60
    if t >= 32:
        c = {"top": "t-shirt", "bottom": "chinos", "shoes": "sneakers", "fabric": "lightweight linen/cotton"}
    elif t >= 25:
        c = {"top": "shirt", "bottom": "chinos", "shoes": "sneakers", "fabric": "breathable cotton"}
    elif t >= 18:
        c = {"top": "shirt", "bottom": "jeans", "shoes": "sneakers", "fabric": "cotton"}
    elif t >= 10:
        c = {"top": "sweater", "bottom": "trousers", "shoes": "sneakers", "outer": "jacket", "fabric": "knit/wool blend"}
    else:
        c = {"top": "sweater", "bottom": "trousers", "shoes": "boots", "outer": "coat", "fabric": "thick wool/knit"}
    if occasion == "Office":
        c.update(top="shirt", bottom="trousers", shoes="formal shoes")
    elif occasion == "Party" or occasion == "Date":
        c.update(top="shirt" if gender != "women" else "top", bottom="jeans" if t < 25 else "trousers", shoes="loafers")
    elif occasion == "Wedding" or style == "Ethnic fusion":
        c.update(top="kurta", bottom="trousers", shoes="loafers")
    elif occasion == "Outdoor activity":
        c.update(top="t-shirt", bottom="joggers", shoes="sneakers", fabric="quick-dry synthetic")
    elif occasion == "Travel":
        c.update(shoes="sneakers", bottom="chinos" if t >= 18 else "trousers")
    if gender == "women" and occasion in ("Party", "Date", "Wedding") and t >= 18:
        c.update(top="dress", bottom="", shoes="sandals" if not rainy and t >= 25 else "loafers")
    if (rainy or n.wind_max_kph >= 28) and "outer" not in c and (outer_need(n) != "none"):
        c["outer"] = "windbreaker"
    elif "outer" not in c and (n.feels_min < 22 or n.swing >= 8) and occasion not in ("Outdoor activity",):
        c["outer"] = "jacket" if occasion != "Office" else "blazer"
    if rainy:
        c["fabric"] = c.get("fabric", "") + "; water-resistant shoes"
    return c


def build_inspiration(db: Database | None, needs: WeatherNeeds, profile: dict[str, Any], today: date, *, repeat_days: int = 7,
                      variant: int = 0) -> Recommendation:
    """A head-to-toe idea with exact colours and categories. Every piece is ``owned=False``: it is NOT claimed to be in your wardrobe."""
    p = _profile(profile)
    cats = _inspiration_categories(needs, p["occasion"], p["gender"], p["style"])
    recent: set[str] = set()
    if db is not None:
        recent = set(recent_signatures(db, today, repeat_days, "inspiration")) | yesterday_signatures(db, today, "inspiration")
    rec = Recommendation(kind="inspiration", pieces=[])
    for attempt in range(len(PALETTES)):
        pal = PALETTES[(variant + attempt + today.toordinal()) % len(PALETTES)]
        top_c = pal["top"]
        if p["pref_colours"]:
            top_c = p["pref_colours"][(variant + attempt) % len(p["pref_colours"])]
        bottom_c = pal["bottom"]
        if tx.colour_harmony(top_c, bottom_c) < 0.7:
            bottom_c = next(c for c in ("navy", "beige", "grey", "black") if tx.colour_harmony(top_c, c) >= 0.85 and c != top_c)
        pieces = [OutfitPiece("onepiece" if cats["top"] == "dress" else "top", cats["top"], top_c, note=cats.get("fabric", ""))]
        if cats.get("bottom"):
            pieces.append(OutfitPiece("bottom", cats["bottom"], bottom_c))
        pieces.append(OutfitPiece("footwear", cats["shoes"], pal["shoes"] if cats["shoes"] != "formal shoes" else "black"))
        if cats.get("outer"):
            pieces.append(OutfitPiece("outer", cats["outer"], pal["outer"]))
        rec = Recommendation(kind="inspiration", pieces=pieces)
        if rec.signature not in recent:
            break
    must, nice = _wanted_accessories(needs, p["occasion"])
    for cat in must + nice[:2]:
        rec.accessories.append(OutfitPiece("accessory", cat, "black" if cat in ("sunglasses", "umbrella") else pal["accent"]))
    extras = []
    if needs.uv_max >= 6 and needs.daytime:
        extras.append("sunscreen")
    if needs.feels_max >= 33:
        extras.append("a water bottle")
    rec.why.append(f"Built for feels-like {_rng(needs.feels_min, needs.feels_max)}"
                   f"{f' and {needs.rain_chance_max:.0f}% rain chance' if needs.rain_chance_max >= 30 else ''}: {cats.get('fabric', 'comfortable fabrics')}.")
    rec.why.append(f"Colours: {', '.join(f'{pc.colour} {pc.category}' for pc in rec.pieces)}"
                   + (f" - uses your preferred colour {p['pref_colours'][0]}." if p["pref_colours"] else "."))
    rec.why.append(f"Styled for {p['occasion'].lower()} in a {p['style'].lower()} style.")
    budget_tip = {"Budget-friendly": "Budget tip: choose versatile basics and re-wear them across outfits.",
                  "Mid-range": "Budget tip: spend most on shoes and the outer layer.", "Premium": "Budget tip: invest in quality natural fabrics."}.get(p["budget"], "")
    if budget_tip:
        rec.why.append(budget_tip)
    if extras:
        rec.why.append("Also carry: " + " and ".join(extras) + ".")
    rec.warnings.append("These are suggestions - you may not own these items. Colours and styles are guidance, not specific products.")
    if db is not None:
        _attach_references(db, rec, p["gender"])
    return rec


def _attach_references(db: Database, rec: Recommendation, gender: str) -> None:
    """Attach a dataset reference photo to a piece ONLY when category AND colour match, so the picture never contradicts its label."""
    refs = list_items(db, "inspiration")
    for pc in rec.pieces + rec.accessories:
        same = [r for r in refs if r["category"] == pc.category and r["image_path"] and r["colour"] == pc.colour and
                (gender == "any" or r["gender_presentation"] in (gender, "unisex"))]
        if same:
            pc.reference_path = same[0]["image_path"]


def recommend_with_item(db: Database, item_id: int, needs: WeatherNeeds | None, profile: dict[str, Any], today: date | None = None, *,
                        repeat_days: int = 7, max_options: int = 3) -> tuple[list[Recommendation], list[str]]:
    """"What goes with my blue shirt?" - wardrobe combinations that CONTAIN the given item. Returns (recommendations, notes).

    History rules are not applied (this is a pairing question, not a daily pick). Without weather the conditions are assumed mild
    and the note says so.
    """
    from wardrobe import get_item
    today = today or date.today()
    anchor = get_item(db, item_id)
    if not anchor or anchor["ownership"] != "wardrobe":
        return [], ["That item isn't in your wardrobe."]
    notes: list[str] = []
    if needs is None:
        needs = WeatherNeeds(feels_min=22, feels_max=28, feels_avg=25, temp_max=28)
        notes.append("I didn't use weather for this pairing (no city given) and assumed mild conditions.")
    p = _profile(profile)
    worn = item_last_worn(db, today, repeat_days)
    items = list_items(db, "wardrobe")
    if tx.slot_of(anchor["category"]) == "accessory":
        return [], ["Accessories go with almost everything - tell me the outfit you want to build around instead."]
    combos = [c for c in _build_combos(items, needs, p, worn, today, repeat_days, None, {item_id}) if item_id in c[0].item_ids]
    if not combos:
        inspiration = build_inspiration(db, needs, profile, today, repeat_days=repeat_days)
        combos = [c for c in _build_completed_combos(items, needs, p, worn, today, repeat_days, None, inspiration, {item_id})
                  if item_id in c[0].item_ids]
    if not combos:
        return [], notes + [f"I couldn't build a complete outfit around your {display_name(anchor).lower()} from the rest of your wardrobe "
                            "(you may need a matching bottom, top or shoes)."]
    out = []
    for rec, pools in _pick_diverse(combos, max_options):
        _finish(db, rec, pools, needs, p, worn, {}, today, repeat_days, items)
        if any(not pc.owned for pc in rec.pieces):
            rec.warnings.append("Suggested pieces are not in your wardrobe; each piece is labelled accordingly.")
        out.append(rec)
    return out, notes
