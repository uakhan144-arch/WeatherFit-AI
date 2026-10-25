"""AI stylist chat: grounded context, Hinglish intents, ownership guard, API-failure fallback, retry."""
from datetime import datetime, timedelta

import pytest

import outfit_engine as E
import outfit_history as H
import stylist_agent as A
from chat_assistant import new_state
from location import resolve_location
from plan_service import build_plan
from tests_support import KEY, FakeSession, make_payload
from weather_service import WeatherClient

NOW = datetime(2026, 10, 24, 10, 0)
PROFILE = {"occasion": "Daily casual", "style": "Smart casual", "budget": "Mid-range", "colours": "Blue", "avoid": "", "gender": "Men", "comfort": "Balanced"}
GWALIOR = make_payload("Gwalior", localtime="2026-10-24 10:00", temp=lambda d, h: 29.0)
KANARIA = make_payload("Kanaria", localtime="2026-10-24 10:00", temp=lambda d, h: 28.0)


@pytest.fixture()
def client():
    return WeatherClient(KEY, session=FakeSession({"gwalior": GWALIOR, "kanaria": KANARIA}))


@pytest.fixture()
def wardrobe(seed):
    for c in [("shirt", "blue"), ("shirt", "white"), ("jeans", "navy"), ("chinos", "beige"), ("sneakers", "white"), ("loafers", "brown"), ("watch", "brown")]:
        seed(*c)


def ctx(db, client, llm=None, **kw):
    return A.AgentContext(db, PROFILE, resolve_location(None, "Gwalior"), client, new_state(), llm=llm, today=NOW.date(), now=NOW, sleep=lambda s: None, **kw)


def wear_yesterday(db, client):
    needs = E.needs_from_windows(build_plan(client, "Gwalior", now=NOW).origin_w)
    rec = E.recommend(db, needs, PROFILE, NOW.date() - timedelta(days=1)).wardrobe[0]
    oid, _ = H.save_recommendation(db, rec, NOW.date() - timedelta(days=1), "Daily casual", needs)
    H.set_status(db, oid, "worn")
    return rec


# ---------------------------------------------------------------- context & grounding
def test_party_question_in_hinglish_uses_real_weather_and_wardrobe(db, client, wardrobe):
    r = A.respond("Aaj Gwalior mein party ke liye kya pehnu?", ctx(db, client))
    assert (r.intent, r.language, r.mode) == ("outfit", "hinglish", "both")
    assert "Gwalior" in r.text and "feels like 30°C" in r.text and "Aapki Wardrobe Se" in r.text and "Naya Outfit Idea" in r.text
    assert r.needs.feels_avg == pytest.approx(30, abs=1) and r.outfit_ids.keys() == {"wardrobe", "inspiration"}
    assert all(p.owned for p in r.result.wardrobe[0].all_pieces) and not any(p.owned for p in r.result.inspiration.all_pieces)


def test_answer_is_recorded_in_history(db, client, wardrobe):
    r = A.respond("What should I wear in Gwalior today?", ctx(db, client))
    assert H.get_outfit(db, r.outfit_ids["wardrobe"])["status"] == "recommended"
    r2 = A.respond("What should I wear in Gwalior today?", ctx(db, client))
    assert len(H.list_history(db)) >= 2  # re-asking never crashes on duplicates


def test_yesterday_question_reports_real_history_and_suggests_something_different(db, client, wardrobe):
    yest = wear_yesterday(db, client)
    r = A.respond("Kal maine kya pehna tha? Aaj kuch different suggest karo.", ctx(db, client))
    assert r.intent == "yesterday" and "Kal aapne ye pehna tha" in r.text
    for it in (f"{p.colour} {p.category}" for p in yest.pieces):
        assert it in r.text.split("Mausam")[0]
    assert r.result.wardrobe[0].signature != yest.signature


def test_yesterday_without_history_says_so_instead_of_inventing(db, client, wardrobe):
    r = A.respond("What did I wear yesterday?", ctx(db, client))
    assert "don't have a saved outfit" in r.text and "Gwalior" not in r.text


def test_match_item_uses_only_owned_items(db, client, wardrobe):
    r = A.respond("Mere wardrobe mein blue shirt ke saath kya match hoga?", ctx(db, client))
    assert r.mode == "match" and r.pair_recs
    names = {p.category for rec in r.pair_recs for p in rec.pieces}
    assert names <= {"shirt", "jeans", "chinos", "sneakers", "loafers"} and all("blue shirt" in " ".join(f"{p.colour} {p.category}" for p in rec.pieces) for rec in r.pair_recs)


def test_match_item_not_owned_is_admitted_not_invented(db, client, wardrobe):
    r = A.respond("Mere wardrobe mein red kurta ke saath kya match hoga?", ctx(db, client))
    assert r.pair_recs == [] and "nahi mila" in r.text and r.grounded["owned_matches"] == []


def test_new_look_shows_inspiration_only_and_labels_it(db, client, wardrobe):
    r = A.respond("Mere paas suitable clothes nahi hain; ek naya look dikhao.", ctx(db, client))
    assert r.mode == "inspiration" and "inspiration hai" in r.text and "Aapki Wardrobe Se" not in r.text
    assert r.grounded["from_your_wardrobe"] == [] and set(r.outfit_ids) == {"inspiration"}


def test_travel_question_combines_both_cities(db, client, wardrobe):
    r = A.respond("Kanaria se Gwalior travel ke liye outfit aur accessories suggest karo.", ctx(db, client))
    assert "Kanaria" in r.text and "Gwalior" in r.text and r.result.wardrobe[0].accessories


def test_empty_wardrobe_still_answers_with_clear_message(db, client):
    r = A.respond("What should I wear in Gwalior today?", ctx(db, client))
    assert r.result.status == "empty" and "wardrobe is empty" in r.text and "New Outfit Inspiration" in r.text


def test_missing_city_asks_follow_up_in_hinglish_and_fetches_nothing(db, client, wardrobe):
    c = A.AgentContext(db, PROFILE, resolve_location(None, ""), client, new_state(), today=NOW.date(), now=NOW)
    r = A.respond("Aaj kya pehnu?", c)
    assert r.followup and "kis shehar" in r.text and client._session.calls == []


def test_weather_failure_is_reported_not_invented(db, wardrobe):
    bad = WeatherClient(KEY, session=FakeSession({}))
    r = A.respond("What should I wear in Atlantis today?", ctx(db, bad))
    assert not r.ok and r.result is None


def test_umbrella_questions_keep_original_weather_behaviour(db, client, wardrobe):
    r = A.respond("Will I need an umbrella in Gwalior?", ctx(db, client))
    assert r.intent == "other" and "Umbrella" in r.text and r.result is None


# ---------------------------------------------------------------- Gemini on / off / failing
def test_no_key_message_and_deterministic_answer(db, client, wardrobe):
    r = A.respond("What should I wear in Gwalior today?", ctx(db, client, llm=None))
    assert r.llm_status == "no_key" and "unavailable" in r.llm_message and r.text and not r.used_llm


def test_llm_prompt_contains_only_real_data_and_answer_is_used(db, client, wardrobe):
    seen = {}

    def fake(prompt, system):
        seen["prompt"], seen["system"] = prompt, system
        return "From Your Wardrobe: blue shirt. New Outfit Inspiration: a maroon top. It is 29°C."
    r = A.respond("Aaj Gwalior mein party ke liye kya pehnu?", ctx(db, client, llm=fake))
    assert r.used_llm and r.llm_status == "live" and r.text.startswith("From Your Wardrobe")
    assert '"owned": true' in seen["prompt"] and '"owned": false' in seen["prompt"] and "Gwalior" in seen["prompt"]
    assert "Hinglish" in seen["system"] and "NEVER say the user owns" in seen["system"]
    assert KEY not in seen["prompt"]


def test_llm_history_is_passed(db, client, wardrobe):
    seen = {}
    c = ctx(db, client, llm=lambda p, s: seen.setdefault("p", p) and "ok", history=[{"role": "user", "content": "EARLIER-MESSAGE"}])
    A.respond("What should I wear in Gwalior today?", c)
    assert "EARLIER-MESSAGE" in seen["p"]


@pytest.mark.parametrize("error,status,retryable", [("429 RESOURCE_EXHAUSTED quota", "quota", True), ("403 API key not valid", "auth", False),
                                                    ("Add GEMINI_API_KEY to your local .env file", "no_key", False), ("weird failure", "error", True)])
def test_llm_failures_fall_back_to_data_answer_with_clear_message(db, client, wardrobe, error, status, retryable):
    def boom(p, s):
        raise RuntimeError(error + f" key={KEY}")
    r = A.respond("What should I wear in Gwalior today?", ctx(db, client, llm=boom))
    assert r.llm_status == status and r.retryable is retryable and not r.used_llm
    assert "Showing the answer built from your data" in r.llm_message and "Gwalior" in r.text
    assert KEY not in r.llm_message and KEY not in r.text


def test_transient_errors_are_retried_with_backoff():
    calls, sleeps = [], []

    def flaky(p, s):
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("timed out")
        return "fine"
    assert A.call_llm_with_retry(flaky, "p", "s", retries=2, base_delay=1, sleep=sleeps.append) == "fine"
    assert len(calls) == 3 and sleeps == [1, 2]


def test_transient_error_gives_up_after_retries():
    with pytest.raises(A.LLMUnavailable) as e:
        A.call_llm_with_retry(lambda p, s: (_ for _ in ()).throw(TimeoutError("timed out")), "p", "s", retries=1, sleep=lambda s: None)
    assert e.value.kind == "transient" and e.value.retryable


def test_quota_and_auth_errors_are_not_retried():
    n = []
    for msg in ("429 quota", "401 unauthenticated"):
        with pytest.raises(A.LLMUnavailable):
            A.call_llm_with_retry(lambda p, s, m=msg: n.append(1) or (_ for _ in ()).throw(RuntimeError(m)), "p", "s", retries=3, sleep=lambda s: None)
    assert len(n) == 2


def test_invented_temperature_is_rejected(db, client, wardrobe):
    r = A.respond("What should I wear in Gwalior today?", ctx(db, client, llm=lambda p, s: "It will be 12°C, wear a coat."))
    assert not r.used_llm and r.llm_status == "rejected" and "12°C" not in r.text


def test_claiming_ownership_of_inspiration_is_rejected(db, client, wardrobe):
    insp = A.respond("What should I wear in Gwalior today?", ctx(db, client)).result.inspiration.pieces[0]
    claim = f"Wear your {insp.colour} {insp.category} today."
    r = A.respond("What should I wear in Gwalior today?", ctx(db, client, llm=lambda p, s: claim))
    if f"{insp.colour} {insp.category}" not in {f"{p.colour} {p.category}" for rec in r.result.wardrobe for p in rec.all_pieces}:
        assert r.llm_status == "rejected"


def test_llm_errors_never_leak_keys_in_classification():
    e = A.classify_llm_error(RuntimeError(f"boom https://x?key={KEY}"))
    assert KEY not in e.message
