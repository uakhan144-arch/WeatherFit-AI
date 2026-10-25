import unittest
from datetime import datetime

from chat_assistant import (check_grounding, handle_message, new_state, parse_message)
from location import LocationResult, resolve_location
from tests_support import KEY, FakeSession, make_payload, timeout_error
from weather_service import WeatherClient

NOW = datetime(2026, 10, 24, 10, 0)
PROFILE = {"occasion": "Daily casual", "style": "Smart casual", "personality": "Calm", "budget": "Budget-friendly",
           "colours": "navy, beige", "avoid": ""}
NO_LOC = resolve_location(None, "")
GPS = resolve_location({"latitude": 22.7196, "longitude": 75.8577}, "")

INDORE = make_payload("Indore", localtime="2026-10-24 10:00")
BHOPAL_RAIN = make_payload("Bhopal", localtime="2026-10-24 10:00", temp=lambda d, h: 24.0,
                           rain=lambda d, h: 85 if 12 <= h <= 20 else 10, precip=lambda d, h: 2.0 if 12 <= h <= 20 else 0.0,
                           condition=lambda d, h: "Moderate rain" if 12 <= h <= 20 else "Cloudy", lat=23.26, lon=77.41)


def setup(routes=None):
    session = FakeSession(routes or {"indore": INDORE, "bhopal": BHOPAL_RAIN, "22.7196,75.8577": INDORE})
    return WeatherClient(KEY, session=session), session


def ask(text, client, state=None, location=GPS, planner=None, llm=None, history=None):
    return handle_message(text, state if state is not None else new_state(), PROFILE, location, client,
                          planner=planner, llm=llm, history=history, now=NOW)


class ParsingTests(unittest.TestCase):
    def test_example_questions(self):
        i = parse_message("I live in Indore and need to travel to Bhopal. What should I wear?", NOW)
        self.assertEqual((i.origin, i.dest, i.travel), ("Indore", "Bhopal", True))
        self.assertTrue(parse_message("Will I need an umbrella or jacket?", NOW).wants_umbrella)
        self.assertTrue(parse_message("Will I need an umbrella or jacket?", NOW).wants_jacket)
        i = parse_message("What should I wear for college, office or a party today?", NOW)
        self.assertEqual(i.occasions, ["College", "Office", "Party"])
        self.assertIsNone(i.dest)

    def test_time_and_duration(self):
        i = parse_message("Indore to Bhopal tomorrow at 6 pm, takes 4 hours", NOW)
        self.assertEqual((i.origin, i.dest, i.duration_h), ("Indore", "Bhopal", 4.0))
        self.assertEqual(i.depart, datetime(2026, 10, 25, 18, 0))
        self.assertEqual(parse_message("leaving in 2 hours to Pune", NOW).depart, datetime(2026, 10, 24, 12, 0))

    def test_occasion_words_are_not_mistaken_for_cities(self):
        for q in ("what should I wear to college", "going to the office", "need to travel", "what to wear to work today"):
            self.assertIsNone(parse_message(q, NOW).dest, q)


class ChatFlowTests(unittest.TestCase):
    def test_asks_for_city_only_when_no_location_known(self):
        client, session = setup()
        st = new_state()
        r = ask("What should I wear today?", client, st, location=NO_LOC)
        self.assertTrue(r.followup)
        self.assertIn("Which city", r.text)
        self.assertEqual(session.calls, [])  # nothing fetched, nothing invented
        r2 = ask("Indore", client, st, location=NO_LOC)
        self.assertFalse(r2.followup)
        self.assertEqual(session.queries, ["Indore"])
        self.assertIn("Indore", r2.text)

    def test_uses_detected_location_without_asking(self):
        client, session = setup()
        r = ask("What should I wear today?", client)
        self.assertFalse(r.followup)
        self.assertEqual(session.queries, ["22.7196,75.8577"])
        self.assertIn("Wear:", r.text)

    def test_manual_city_location_used(self):
        client, session = setup()
        loc = resolve_location(None, "Indore")
        ask("What should I wear today?", client, location=loc)
        self.assertEqual(session.queries, ["Indore"])

    def test_indore_to_bhopal_uses_both_forecasts_and_arrival_time(self):
        client, session = setup()
        r = ask("I live in Indore and need to travel to Bhopal. What should I wear?", client, location=NO_LOC)
        self.assertEqual(sorted(session.queries), ["Bhopal", "Indore"])
        g = r.grounded
        self.assertEqual(g["journey_hours"], 3.0)  # assumed, and disclosed
        self.assertTrue(any("assumed about 3 hours" in a for a in g["assumptions"]))
        self.assertEqual(g["destination"]["from"], "2026-10-24 13:00")  # 10:00 + 3h
        self.assertEqual(g["destination"]["rain_chance_max_pct"], 85)
        self.assertTrue(r.advice.item("Umbrella").needed)
        self.assertIn("Bhopal", r.text)
        self.assertIn("85%", r.text)
        self.assertNotIn("31–31", r.text)  # equal min/max is shown once

    def test_explicit_duration_and_time_are_used(self):
        client, _ = setup()
        r = ask("Indore to Bhopal today at 11 am, journey takes 4 hours", client, location=NO_LOC)
        self.assertEqual(r.grounded["journey_hours"], 4.0)
        self.assertEqual(r.grounded["destination"]["from"], "2026-10-24 15:00")
        self.assertFalse(any("assumed about 3 hours" in a for a in r.grounded["assumptions"]))

    def test_travel_without_destination_asks_then_continues(self):
        client, session = setup()
        st = new_state()
        r = ask("I need to travel today, what should I wear?", client, st)
        self.assertTrue(r.followup and "Where are you travelling" in r.text)
        self.assertEqual(session.calls, [])
        r2 = ask("Bhopal", client, st)
        self.assertFalse(r2.followup)
        self.assertIn("Bhopal", session.queries)
        self.assertIsNotNone(r2.grounded["destination"])

    def test_planner_destination_used_for_travel_questions(self):
        client, session = setup()
        planner = {"dest": "Bhopal", "depart": datetime(2026, 10, 24, 9, 0), "duration_h": 3.5, "stay_h": 3}
        r = ask("I need to travel, what should I wear?", client, planner=planner)
        self.assertIn("Bhopal", session.queries)
        self.assertEqual(r.grounded["journey_hours"], 3.5)
        self.assertEqual(r.grounded["departure"], "2026-10-24 09:00")

    def test_umbrella_question_is_answered_first(self):
        client, _ = setup()
        r = ask("Will I need an umbrella for Indore to Bhopal?", client, location=NO_LOC)
        self.assertTrue(r.text.startswith("**Umbrella: yes.**"))

    def test_dry_day_says_no_umbrella(self):
        client, _ = setup()
        r = ask("Do I need an umbrella today?", client, location=resolve_location(None, "Indore"))
        self.assertTrue(r.text.startswith("**Umbrella: no.**"))

    def test_profile_and_occasion_reach_context(self):
        client, _ = setup()
        r = ask("What should I wear to the office today?", client, location=resolve_location(None, "Indore"))
        self.assertEqual(r.grounded["occasion"], "Office")
        self.assertEqual(r.grounded["profile"]["budget"], "Budget-friendly")
        self.assertIn("navy, beige", r.advice.outfit_reason)
        self.assertIn("formal shirt", r.advice.pieces.lower())

    def test_multiple_occasions_answered(self):
        client, _ = setup()
        r = ask("What should I wear for college, office or a party today?", client, location=resolve_location(None, "Indore"))
        self.assertIn("Other occasions", r.text)
        self.assertEqual(set(r.grounded["advice_other_occasions"]), {"Office", "Party"})

    def test_home_city_remembered_within_chat(self):
        client, session = setup()
        st = new_state()
        ask("I live in Indore", client, st, location=NO_LOC)
        r = ask("Will I need a jacket today?", client, st, location=NO_LOC)
        self.assertFalse(r.followup)  # did not ask for the city again
        self.assertEqual(st["home_city"], "Indore")
        self.assertTrue(r.ok)


class FailureTests(unittest.TestCase):
    def test_origin_api_failure_returns_error_without_invented_weather(self):
        client, _ = setup({"indore": timeout_error()})
        r = ask("What should I wear today?", client, location=resolve_location(None, "Indore"))
        self.assertFalse(r.ok)
        self.assertIn("timed out", r.text)
        self.assertNotIn("°C", r.text)
        self.assertEqual(r.grounded, {})
        self.assertNotIn(KEY, r.text)

    def test_unknown_city_message(self):
        client, _ = setup()
        r = ask("What should I wear today?", client, location=resolve_location(None, "Atlantis"))
        self.assertFalse(r.ok)
        self.assertIn("not found", r.text)

    def test_destination_failure_falls_back_to_origin_with_warning(self):
        client, _ = setup({"indore": INDORE, "bhopal": timeout_error()})
        r = ask("Indore to Bhopal now", client, location=NO_LOC)
        self.assertTrue(r.ok)
        self.assertIsNone(r.grounded["destination"])
        self.assertTrue(any("couldn't get destination weather" in w for w in r.warnings))

    def test_forecast_out_of_range_is_reported(self):
        client, _ = setup()
        planner = {"dest": "Bhopal", "depart": datetime(2026, 12, 1, 9, 0), "duration_h": 3}
        r = ask("I need to travel", client, planner=planner)
        self.assertFalse(r.ok)
        self.assertIn("No forecast is available", r.text)

    def test_stale_cache_is_disclosed(self):
        from tests_support import Clock
        clock = Clock()
        session = FakeSession({"indore": INDORE})
        client = WeatherClient(KEY, session=session, clock=clock)
        loc = resolve_location(None, "Indore")
        ask("What should I wear today?", client, location=loc)
        session.routes["indore"] = timeout_error()
        clock.t += 900
        r = ask("What should I wear today?", client, location=loc)
        self.assertTrue(r.ok)
        self.assertTrue(any("cached" in w for w in r.warnings))


class LlmGroundingTests(unittest.TestCase):
    def test_llm_prompt_contains_real_data_and_no_key(self):
        client, _ = setup()
        seen = {}

        def llm(prompt, system):
            seen.update(prompt=prompt, system=system)
            return "Take an umbrella: rain chance reaches 85% in Bhopal. Light layers, feels around 24°C."

        r = ask("Indore to Bhopal", client, location=NO_LOC, llm=llm,
                history=[{"role": "user", "content": "hi"}])
        self.assertTrue(r.used_llm)
        self.assertIn('"rain_chance_max_pct": 85', seen["prompt"])
        self.assertIn("Bhopal", seen["prompt"])
        self.assertIn("Never invent", seen["system"])
        self.assertNotIn(KEY, seen["prompt"])

    def test_hallucinated_numbers_are_rejected(self):
        client, _ = setup()
        r = ask("Indore to Bhopal", client, location=NO_LOC, llm=lambda p, s: "It will be 45°C with a 5% chance of rain.")
        self.assertFalse(r.used_llm)
        self.assertNotIn("45°C", r.text)
        self.assertTrue(any("aren't in the weather data" in w for w in r.warnings))

    def test_llm_failure_falls_back(self):
        client, _ = setup()

        def boom(p, s):
            raise RuntimeError("quota")
        r = ask("Indore to Bhopal", client, location=NO_LOC, llm=boom)
        self.assertFalse(r.used_llm)
        self.assertIn("Wear:", r.text)
        self.assertTrue(any("RuntimeError" in w for w in r.warnings))

    def test_check_grounding_unit(self):
        g = {"origin": {"feels_like_c": [30, 31], "rain_chance_max_pct": 20}, "destination": None, "current": {"temp_c": 29.6}}
        self.assertEqual(check_grounding("About 30°C and 20% rain; 30.5 degrees", g), [])
        self.assertEqual(check_grounding("It is 12°C", g), ["12°C"])
        self.assertEqual(check_grounding("Rain chance 90%", g), ["90%"])


if __name__ == "__main__":
    unittest.main()
