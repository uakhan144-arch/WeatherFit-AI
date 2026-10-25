"""Voice assistant logic: no network, no Gemini, no browser. Covers the example question, Hinglish/Hindi, follow-ups and Gemini fallbacks."""
from datetime import datetime

import pytest

import gemini_guard as G
import voice_assistant as V
from chat_assistant import new_state
from gemini_guard import GeminiUnavailable
from location import resolve_location
from openmeteo_service import OpenMeteoClient
from tests_support_openmeteo import OMSession, make_forecast

NOW = datetime(2026, 10, 25, 10, 15)
PROFILE = {"occasion": "Daily casual", "style": "Casual", "budget": "Mid-range", "colours": "", "avoid": ""}
LOC = resolve_location(None, "Bhopal", True)
EXAMPLE = "Today I need to travel from Bhopal to Indore. What should I wear according to the weather?"


def make_client(**forecasts):
    fc = {"bhopal": make_forecast(temp=lambda d, h: 31), "indore": make_forecast(temp=lambda d, h: 27, rain=lambda d, h: 70 if 12 <= h <= 18 else 5,
                                                                              code=lambda d, h: 61 if 12 <= h <= 18 else 2)}
    fc.update(forecasts)
    s = OMSession(fc)
    return OpenMeteoClient(session=s, sleep=lambda x: None), s


def ask(q, client=None, state=None, **kw):
    client = client or make_client()[0]
    return V.answer(q, state if state is not None else new_state(), PROFILE, LOC, client, now=NOW, **kw)


def test_example_question_uses_both_cities_and_needs_no_gemini():
    a = ask(EXAMPLE)
    assert a.ok and not a.followup and a.gemini_status == "not_used" and not a.used_gemini
    assert "Bhopal" in a.spoken and "Indore" in a.spoken and "umbrella" in a.spoken.lower()
    assert "Open-Meteo" in a.spoken and "updated at" in a.spoken       # data source + timestamp are spoken
    assert "Open-Meteo" in a.stamp and "2026" in a.stamp                 # and shown
    assert "3 hours" in a.spoken                                          # assumed duration is disclosed
    assert "°" not in a.spoken and "%" not in a.spoken and "*" not in a.spoken   # sounds right when read aloud


def test_given_duration_is_used_and_not_called_an_assumption():
    a = ask(EXAMPLE.replace("What", "It takes 4 hours. What"))
    assert "assumed" not in a.spoken


def test_destination_missing_asks_for_it_then_uses_the_reply():
    st = new_state()
    a = ask("I need to travel today. What should I wear?", state=st)
    assert a.followup and "travelling to" in a.spoken
    b = ask("Indore", state=st)
    assert b.ok and not b.followup and "Indore" in b.spoken


def test_origin_missing_asks_for_city():
    from location import LocationResult
    nowhere = LocationResult("none", "", "Location not set", False)
    a = V.answer("What should I wear today?", new_state(), PROFILE, nowhere, make_client()[0], now=NOW)
    assert a.followup and "city" in a.spoken.lower()


def test_unknown_city_is_explained_not_invented():
    a = ask("What should I wear in Atlantis today?")
    assert not a.ok and "Atlantis" in a.display and "temperature" not in a.display.lower()


def test_network_failure_is_reported(monkeypatch):
    import requests
    client, s = make_client(bhopal=requests.ConnectionError("boom"))
    a = ask("What should I wear today?", client=client)
    assert not a.ok and "Could not connect" in a.display


def test_destination_failure_falls_back_to_origin_with_warning():
    import requests
    client, _ = make_client(indore=requests.ConnectionError("x"))
    a = ask(EXAMPLE, client=client)
    assert a.ok and any("couldn't get destination weather" in n for n in a.notes)


def test_hinglish_reply_in_hinglish():
    a = ask("Aaj Bhopal se Indore jaana hai, 4 ghante lagenge, kya pehnu? chhata chahiye?")
    assert a.language == "hinglish" and a.speak_lang == "en-IN" and "Chhata" in a.spoken and "safar" in a.spoken and "assumed" not in a.spoken


def test_hindi_devanagari_reply_in_hindi_with_hindi_voice():
    a = ask("आज भोपाल से इंदौर जाना है, क्या पहनूं?")
    assert a.language == "hi" and a.speak_lang == "hi-IN" and "भोपाल" in a.spoken and "छाता" in a.spoken


def test_named_place_in_umbrella_question_is_respected():
    a = ask("Will I need an umbrella in Indore tomorrow at 6 pm?")
    assert a.ok and "Indore" in a.spoken and "Bhopal" not in a.spoken and "tomorrow" in a.spoken


def test_english_me_is_not_mistaken_for_hinglish_city():
    a = ask("tell me a joke")
    assert a.followup and "weather" in a.spoken.lower()      # friendly help text, no weather lookup


def test_weather_is_cached_between_questions():
    client, s = make_client()
    ask(EXAMPLE, client=client); n = len(s.calls)
    ask(EXAMPLE, client=client)
    assert len(s.calls) == n


def test_empty_input():
    a = ask("   ")
    assert not a.ok and a.followup


def test_missing_rain_probability_is_disclosed():
    a = ask("What should I wear today?", client=make_client(bhopal=make_forecast(null_rain=True))[0])
    assert any("Rain probability was not provided" in n for n in a.notes)


# ---------------------------------------------------------------- Gemini is optional
class Counter:
    def __init__(self, reply="Wear a light cotton shirt; it is 31 degrees." , exc=None):
        self.n, self.reply, self.exc = 0, reply, exc

    def __call__(self, prompt, system):
        self.n += 1
        if self.exc:
            raise self.exc
        return self.reply


def test_gemini_not_called_for_normal_weather_questions_even_if_available():
    llm = Counter()
    a = ask(EXAMPLE, llm=llm)
    assert llm.n == 0 and a.gemini_status == "not_used"


def test_gemini_wording_only_when_requested_and_checked_against_data():
    good = Counter("Bhopal feels like 32 degrees Celsius, so wear a light shirt.")
    a = ask(EXAMPLE, llm=good, use_gemini_wording=True)
    assert good.n == 1 and a.used_gemini and a.source == "gemini" and a.details
    bad = Counter("It will be 77 degrees, wear shorts.")
    b = ask(EXAMPLE, llm=bad, use_gemini_wording=True)
    assert not b.used_gemini and b.gemini_status == "rejected" and "Bhopal" in b.spoken


@pytest.mark.parametrize("kind", ["quota", "cooldown", "disabled", "transient"])
def test_gemini_unavailable_falls_back_to_rules(kind):
    a = ask(EXAMPLE, llm=Counter(exc=GeminiUnavailable(kind, f"msg {kind}")), use_gemini_wording=True)
    assert a.ok and not a.used_gemini and a.gemini_status == kind and "Indore" in a.spoken


def test_gemini_crash_never_breaks_the_answer():
    a = ask(EXAMPLE, llm=Counter(exc=ValueError("weird")), use_gemini_wording=True)
    assert a.ok and a.gemini_status == "error"


def test_non_weather_question_uses_gemini_when_available_else_help():
    llm = Counter("Linen is breathable and suits hot days.")
    a = ask("Why do people say linen is good for summer", llm=llm)
    assert a.used_gemini and llm.n == 1
    off = ask("Why do people say linen is good for summer", llm=None)
    assert not off.used_gemini and "weather" in off.spoken.lower()
    limited = ask("Why do people say linen is good for summer", llm=Counter(exc=GeminiUnavailable("quota", "paused")))
    assert not limited.used_gemini and limited.gemini_status == "quota" and "weather" in limited.spoken.lower()


def test_freeform_gemini_answer_with_made_up_weather_numbers_is_rejected():
    a = ask("Why do people say linen is good for summer", llm=Counter("Today it is 35 degrees so linen is great."))
    assert not a.used_gemini and a.gemini_status == "rejected"


def test_speakable():
    assert V.speakable("**Wear** 24–29°C, 60% rain; 10 km/h") == "Wear 24 to 29 degrees Celsius, 60 percent rain; 10 kilometres per hour"
