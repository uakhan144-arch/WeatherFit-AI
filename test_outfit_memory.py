"""Outfit history + recommender: yesterday exclusion, recent diversity, wardrobe-only picks, ownership labels, constraints."""
from datetime import timedelta

import pytest

import outfit_engine as E
import outfit_history as H
import wardrobe as W
from conftest import MILD, PROFILE, TODAY
from outfit_models import INSPIRATION_LABEL, WARDROBE_LABEL, WeatherNeeds

YESTERDAY, DAYS_AGO = TODAY - timedelta(days=1), lambda n: TODAY - timedelta(days=n)


def wear(db, rec, day):
    oid, _ = H.save_recommendation(db, rec, day, "Daily casual", MILD)
    H.set_status(db, oid, "worn")
    return oid


def pick(db, **kw):
    return E.recommend(db, MILD, {**PROFILE, **kw.pop("profile", {})}, TODAY, **kw)


# ---------------------------------------------------------------- yesterday's outfit
def test_yesterdays_exact_combination_is_never_recommended(db, basic_wardrobe):
    first = pick(db).wardrobe[0]
    wear(db, first, YESTERDAY)
    res = pick(db, max_options=20)
    assert res.wardrobe and all(r.signature != first.signature for r in res.wardrobe)


def test_yesterday_recommended_but_unconfirmed_is_also_excluded(db, basic_wardrobe):
    first = pick(db).wardrobe[0]
    H.save_recommendation(db, first, YESTERDAY, "Daily casual", MILD)  # status stays 'recommended'
    assert all(r.signature != first.signature for r in pick(db, max_options=20).wardrobe)


def test_skipped_outfit_does_not_count_as_worn(db, basic_wardrobe):
    first = pick(db).wardrobe[0]
    oid, _ = H.save_recommendation(db, first, YESTERDAY, "Daily casual", MILD)
    H.set_status(db, oid, "skipped")
    assert first.signature not in H.yesterday_signatures(db, TODAY)


def test_only_combination_is_not_repeated_even_when_wardrobe_is_tiny(db, seed):
    for c in [("shirt", "blue"), ("jeans", "navy"), ("sneakers", "white")]:
        seed(*c)
    only = pick(db).wardrobe[0]
    wear(db, only, YESTERDAY)
    res = pick(db)
    assert res.wardrobe == [] and res.status == "limited" and "yesterday" in " ".join(res.messages).lower()
    assert res.inspiration is not None


# ---------------------------------------------------------------- recent diversity
def test_recent_combinations_are_avoided_when_alternatives_exist(db, basic_wardrobe):
    worn_sigs = set()
    for n in (2, 3, 4):
        rec = pick(db, max_options=20).wardrobe
        rec = next(r for r in rec if r.signature not in worn_sigs)
        wear(db, rec, DAYS_AGO(n))
        worn_sigs.add(rec.signature)
    res = pick(db, repeat_days=7, max_options=3)
    assert res.status == "ok" and all(r.signature not in worn_sigs for r in res.wardrobe)


def test_recently_worn_items_are_deprioritised(db, basic_wardrobe):
    base = pick(db).wardrobe[0]
    wear(db, base, DAYS_AGO(2))
    nxt = pick(db).wardrobe[0]
    assert len(set(nxt.item_ids) & set(base.item_ids)) < len(base.item_ids)


def test_limited_wardrobe_reuses_oldest_and_explains(db, seed):
    seed("shirt", "blue"); seed("jeans", "navy"); seed("sneakers", "white"); seed("t-shirt", "white")
    older, yest = pick(db, max_options=10).wardrobe  # exactly two combinations exist
    wear(db, older, DAYS_AGO(5))
    wear(db, yest, YESTERDAY)
    res = pick(db)
    # yesterday's combo is excluded; the only other one was used 5 days ago (inside the 7-day window) -> reused, with an explanation
    assert res.status == "limited" and [r.signature for r in res.wardrobe] == [older.signature]
    assert "limited" in " ".join(res.messages).lower()
    assert res.wardrobe[0].reuse_notes and "reused because" in res.wardrobe[0].reuse_notes[0]


def test_single_suitable_item_reuse_note_says_it_is_the_only_one(db, seed):
    seed("shirt", "blue"); seed("shirt", "white"); seed("jeans", "navy"); seed("sneakers", "white")
    combos = pick(db, max_options=10).wardrobe
    for n, rec in enumerate(combos, start=2):
        wear(db, rec, DAYS_AGO(n))
    res = pick(db)
    notes = " ".join(res.wardrobe[0].reuse_notes)
    assert "only suitable bottom" in notes and "only suitable pair of shoes" in notes


def test_combinations_compared_by_items_not_names(db, seed):
    a, b = seed("shirt", "blue", name="Same Name"), seed("shirt", "blue", name="Same Name")
    j, s = seed("jeans", "navy"), seed("sneakers", "white")
    recs = pick(db, max_options=10).wardrobe
    sigs = {r.signature for r in recs}
    assert len(sigs) == 2 and sigs == {f"w:{min(a, j, s)}-{sorted([a, j, s])[1]}-{max(a, j, s)}", f"w:{'-'.join(map(str, sorted([b, j, s])))}"}


# ---------------------------------------------------------------- history rows
def test_saving_same_outfit_twice_does_not_duplicate(db, basic_wardrobe):
    rec = pick(db).wardrobe[0]
    id1, c1 = H.save_recommendation(db, rec, TODAY, "Daily casual", MILD)
    id2, c2 = H.save_recommendation(db, rec, TODAY, "Daily casual", MILD)
    assert (id1 == id2, c1, c2) == (True, True, False)
    assert len(H.list_history(db)) == 1
    o = H.get_outfit(db, id1)
    assert sorted(i["item_id"] for i in o["items"]) == rec.item_ids
    assert o["occasion"] == "Daily casual" and o["weather"]["feels_avg"] == 27 and o["colours"] and o["categories"]


def test_worn_updates_usage_and_is_idempotent_and_reversible(db, basic_wardrobe):
    rec = pick(db).wardrobe[0]
    oid, _ = H.save_recommendation(db, rec, YESTERDAY, "Daily casual", MILD)
    H.set_status(db, oid, "worn"); H.set_status(db, oid, "worn")
    it = W.get_item(db, rec.item_ids[0])
    assert (it["usage_count"], it["last_worn_date"]) == (1, YESTERDAY.isoformat())
    H.set_status(db, oid, "skipped")
    it = W.get_item(db, rec.item_ids[0])
    assert (it["usage_count"], it["last_worn_date"]) == (0, None)


def test_only_one_worn_outfit_per_day(db, basic_wardrobe):
    r1, r2 = pick(db, max_options=2).wardrobe[:2]
    o1, o2 = (H.save_recommendation(db, r, YESTERDAY, "x", MILD)[0] for r in (r1, r2))
    H.set_status(db, o1, "worn"); H.set_status(db, o2, "worn")
    assert H.get_outfit(db, o1)["status"] == "recommended" and H.get_outfit(db, o2)["status"] == "worn"


def test_favourite_feedback_and_clear_history(db, basic_wardrobe):
    rec = pick(db).wardrobe[0]
    oid, _ = H.save_recommendation(db, rec, TODAY, "x", MILD)
    H.set_favourite(db, oid); H.set_feedback(db, oid, "loved it")
    assert H.list_history(db, favourites_only=True)[0]["feedback"] == "loved it"
    wear(db, rec, YESTERDAY)
    assert H.clear_history(db) == 2 and H.list_history(db) == []
    assert all(i["usage_count"] == 0 for i in W.list_items(db))


def test_deleting_an_item_keeps_history_snapshot(db, basic_wardrobe):
    rec = pick(db).wardrobe[0]
    oid, _ = H.save_recommendation(db, rec, YESTERDAY, "x", MILD)
    W.delete_item(db, rec.item_ids[0])
    o = H.get_outfit(db, oid)
    assert any(i["item_id"] is None and i["category"] for i in o["items"])


# ---------------------------------------------------------------- wardrobe-only / ownership
def test_wardrobe_recommendations_only_use_wardrobe_rows(db, basic_wardrobe, data_dir):
    W.add_item(db, {"category": "jacket", "colour": "red"}, __import__("conftest").png_bytes(), ownership="inspiration", source="sample", source_ref="x", data_dir=data_dir)
    res = pick(db, max_options=20)
    ids = {i["id"] for i in W.list_items(db, "wardrobe")}
    for r in res.wardrobe:
        assert r.kind == "wardrobe" and r.label == WARDROBE_LABEL
        assert all(p.owned and p.item_id in ids for p in r.all_pieces)


def test_inspiration_is_never_marked_owned(db, basic_wardrobe):
    insp = pick(db).inspiration
    assert insp.kind == "inspiration" and insp.label == INSPIRATION_LABEL
    assert insp.pieces and all(not p.owned and p.item_id is None for p in insp.all_pieces)
    assert any("may not own" in w for w in insp.warnings)
    assert insp.signature.startswith("i:")


def test_empty_wardrobe_gives_inspiration_only_with_clear_message(db):
    res = pick(db)
    assert res.status == "empty" and res.wardrobe == [] and res.inspiration and "empty" in res.messages[0].lower()


def test_missing_footwear_is_completed_with_a_not_owned_suggestion(db, seed):
    seed("shirt", "blue"); seed("jeans", "navy")
    res = pick(db)
    assert res.status == "limited" and res.wardrobe
    rec = res.wardrobe[0]
    assert {p.role for p in rec.pieces} >= {"top", "bottom", "footwear"}
    assert any(p.role == "footwear" and not p.owned for p in rec.pieces)
    assert any("not in your wardrobe" in m for m in rec.missing)
    assert any(p.owned for p in rec.pieces)


def test_single_wardrobe_item_is_completed_into_a_full_look(db, seed):
    shirt_id = seed("shirt", "blue")
    res = pick(db)
    assert res.wardrobe and res.status == "limited"
    rec = res.wardrobe[0]
    assert {p.role for p in rec.pieces} >= {"top", "bottom", "footwear"}
    assert shirt_id in rec.item_ids
    assert any(not p.owned for p in rec.pieces)


def test_missing_weather_accessory_is_flagged_as_not_owned(db, basic_wardrobe):
    rainy = WeatherNeeds(22, 25, 23, temp_max=25, rain_chance_max=80)
    rec = E.recommend(db, rainy, PROFILE, TODAY).wardrobe[0]
    assert any("Umbrella" in m and "not in your wardrobe" in m for m in rec.missing)
    assert all(a.category != "umbrella" for a in rec.accessories)


def test_owned_accessory_is_used(db, basic_wardrobe, seed):
    seed("umbrella", "black")
    rainy = WeatherNeeds(22, 25, 23, temp_max=25, rain_chance_max=80)
    assert any(a.category == "umbrella" and a.owned for a in E.recommend(db, rainy, PROFILE, TODAY).wardrobe[0].accessories)


# ---------------------------------------------------------------- weather / occasion / preference constraints
def cats(res):
    return {p.category for r in res.wardrobe for p in r.pieces}


def test_hot_weather_excludes_warm_layers(db, basic_wardrobe, seed):
    seed("sweater", "grey"); seed("jacket", "black"); seed("boots", "brown")
    hot = WeatherNeeds(33, 38, 36, temp_max=38, rain_chance_max=0, uv_max=9)
    res = E.recommend(db, hot, PROFILE, TODAY, max_options=30)
    assert res.wardrobe and not ({"sweater", "jacket", "boots"} & cats(res))


def test_cold_weather_requires_warm_layering_and_no_shorts(db, seed):
    for c in [("t-shirt", "white"), ("shirt", "blue"), ("shorts", "tan"), ("jeans", "navy"), ("sneakers", "white")]:
        seed(*c)
    cold = WeatherNeeds(6, 10, 8, temp_max=10)
    res = E.recommend(db, cold, PROFILE, TODAY)
    assert res.wardrobe and res.status == "limited"
    assert any(p.category == "coat" and not p.owned for p in res.wardrobe[0].pieces)
    assert "shorts" not in cats(res)
    seed("coat", "grey")
    res = E.recommend(db, cold, PROFILE, TODAY, max_options=30)
    assert res.wardrobe and all("coat" in {p.category for p in r.pieces} for r in res.wardrobe) and "shorts" not in cats(res)


def test_rain_rules_out_sandals_and_suede(db, basic_wardrobe, seed):
    seed("sandals", "brown"); seed("sneakers", "tan", fabric="suede")
    rainy = WeatherNeeds(22, 25, 23, temp_max=25, rain_chance_max=85)
    res = E.recommend(db, rainy, PROFILE, TODAY, max_options=30)
    shoes = {(p.category, p.colour) for r in res.wardrobe for p in r.pieces if p.role == "footwear"}
    assert ("sandals", "brown") not in shoes and ("sneakers", "tan") not in shoes and shoes


def test_season_tags_respected(db, basic_wardrobe, seed):
    seed("t-shirt", "red", season="winter")
    res = pick(db, max_options=30)
    assert ("t-shirt", "red") not in {(p.category, p.colour) for r in res.wardrobe for p in r.pieces}


def test_occasion_filters_items_strictly_when_possible(db, seed):
    seed("t-shirt", "white", occasion="casual"); seed("shirt", "white", occasion="office,formal")
    seed("trousers", "grey", occasion="office,formal"); seed("jeans", "navy", occasion="casual")
    seed("formal shoes", "black", occasion="office,formal"); seed("sneakers", "white", occasion="casual")
    res = E.recommend(db, MILD, {**PROFILE, "occasion": "Office"}, TODAY, max_options=10)
    assert res.status == "ok" and cats(res) <= {"shirt", "trousers", "formal shoes"}


def test_occasion_falls_back_with_a_clear_warning(db, seed):
    for c in [("shirt", "blue"), ("jeans", "navy"), ("sneakers", "white")]:
        seed(*c, occasion="casual")
    res = E.recommend(db, MILD, {**PROFILE, "occasion": "Wedding"}, TODAY)
    assert any("closest matches" in m for m in res.messages) and res.wardrobe[0].warnings


def test_gender_presentation_and_avoid_list(db, seed):
    seed("shirt", "blue", gender_presentation="men"); seed("blouse", "pink", gender_presentation="women")
    seed("trousers", "navy"); seed("sneakers", "white"); seed("jeans", "navy")
    women = E.recommend(db, MILD, {**PROFILE, "gender": "Women"}, TODAY, max_options=20)
    assert "shirt" not in cats(women) and "blouse" in cats(women)
    avoid = E.recommend(db, MILD, {**PROFILE, "avoid": "jeans"}, TODAY, max_options=20)
    assert "jeans" not in cats(avoid)


def test_preferred_colour_is_favoured_and_explained(db, basic_wardrobe):
    rec = pick(db, profile={"colours": "blue"}).wardrobe[0]
    assert any(p.colour == "blue" for p in rec.pieces) and any("preferred colour" in w for w in rec.why)


def test_inspiration_uses_preferred_colour_and_varies_with_history(db):
    prof = {**PROFILE, "colours": "maroon"}
    a = E.build_inspiration(db, MILD, prof, TODAY)
    assert a.pieces[0].colour == "maroon"
    H.save_recommendation(db, a, TODAY - timedelta(days=1), "x", MILD)
    b = E.build_inspiration(db, MILD, prof, TODAY)
    assert b.signature != a.signature


def test_why_lines_cite_the_weather(db, basic_wardrobe):
    rec = pick(db).wardrobe[0]
    assert "feels like 24-29°C" in rec.why[0]


def test_pairing_with_an_owned_item(db, basic_wardrobe):
    recs, notes = E.recommend_with_item(db, basic_wardrobe[("shirt", "blue")], None, PROFILE, TODAY)
    assert recs and all(basic_wardrobe[("shirt", "blue")] in r.item_ids for r in recs) and notes and "weather" in notes[0]


def test_pairing_completes_missing_slots_with_suggestions(db, seed):
    shirt_id = seed("shirt", "blue")
    seed("jeans", "navy")
    recs, _ = E.recommend_with_item(db, shirt_id, MILD, PROFILE, TODAY)
    assert recs and shirt_id in recs[0].item_ids
    assert {p.role for p in recs[0].pieces} >= {"top", "bottom", "footwear"}
    assert any(not p.owned for p in recs[0].pieces)
