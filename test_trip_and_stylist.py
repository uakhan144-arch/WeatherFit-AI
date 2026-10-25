import unittest
from datetime import datetime

from stylist import apply_avoid, build_trip_advice, compare_places
from tests_support import KEY, FakeSession, make_payload
from trip import ForecastUnavailable, arrival_time, combine, summarize_window
from weather_service import WeatherClient

T0 = datetime(2026, 10, 24, 10, 0)


def fc(payload):
    return WeatherClient(KEY, session=FakeSession({"x": payload})).get_forecast("x")


DRY = fc(make_payload("Indore"))
WET_COOL = fc(make_payload("Bhopal", temp=lambda d, h: 19.0, rain=lambda d, h: 80, precip=lambda d, h: 1.5,
                           condition=lambda d, h: "Moderate rain", uv=lambda d, h: 1.0))


class WindowTests(unittest.TestCase):
    def test_window_summary_values(self):
        w = summarize_window(DRY, datetime(2026, 10, 24, 9, 30), datetime(2026, 10, 24, 12, 0))
        self.assertEqual(w.hours, 4)  # 09,10,11,12
        self.assertEqual((w.temp_min, w.temp_max, w.feels_max), (30, 30, 31))
        self.assertEqual(w.rain_chance_max, 0)
        self.assertEqual(w.uv_max, 7)

    def test_outside_forecast_raises_instead_of_inventing(self):
        with self.assertRaises(ForecastUnavailable) as cm:
            summarize_window(DRY, datetime(2026, 11, 20, 9), datetime(2026, 11, 20, 12))
        self.assertIn("No forecast is available", str(cm.exception))

    def test_partial_window_is_flagged(self):
        w = summarize_window(DRY, datetime(2026, 10, 26, 22, 0), datetime(2026, 10, 27, 3, 0))
        self.assertTrue(w.partial)

    def test_arrival_same_and_cross_timezone(self):
        self.assertEqual(arrival_time(T0, 3.5, "Asia/Kolkata", "Asia/Kolkata"), datetime(2026, 10, 24, 13, 30))
        # 10:00 IST = 05:30 UTC; +4h = 09:30 UTC; Europe/London is UTC on 24 Oct 2026 (BST ended 25 Oct)
        self.assertEqual(arrival_time(T0, 4, "Asia/Kolkata", "Europe/London"), datetime(2026, 10, 24, 9, 30))

    def test_combine_takes_extremes(self):
        a = summarize_window(DRY, T0, datetime(2026, 10, 24, 11))
        b = summarize_window(WET_COOL, T0, datetime(2026, 10, 24, 11))
        c = combine([a, b])
        self.assertEqual((c.feels_min, c.feels_max, c.rain_chance_max), (20, 31, 80))


class DestinationAdviceTests(unittest.TestCase):
    def setUp(self):
        self.o = summarize_window(DRY, T0, datetime(2026, 10, 24, 11), "Indore")
        self.d = summarize_window(WET_COOL, datetime(2026, 10, 24, 13), datetime(2026, 10, 24, 16), "Bhopal")

    def test_rainy_cool_destination_adds_umbrella_jacket_and_comparison(self):
        a = build_trip_advice(self.o, self.d, "Travel", "Casual", "Mid-range")
        self.assertTrue(a.item("Umbrella").needed)
        self.assertIn("80%", a.item("Umbrella").reason)
        self.assertIn("Bhopal", a.item("Umbrella").reason)
        self.assertTrue(a.item("Light jacket").needed)
        self.assertIn("water-resistant", a.footwear.lower())
        self.assertIn("Bhopal will feel about 11°C cooler than Indore", a.comparison)
        self.assertIn("noticeably more likely to be wet", a.comparison)
        self.assertTrue(any(x["title"] == "Rain-ready adjustment" for x in a.alternatives))

    def test_dry_sunny_origin_only_has_sun_gear_and_no_umbrella(self):
        a = build_trip_advice(self.o, None, "Daily casual", "Casual", "Budget-friendly")
        self.assertFalse(a.item("Umbrella").needed)
        self.assertTrue(a.item("Sunglasses").needed and a.item("Cap").needed and a.item("Sunscreen").needed)
        self.assertEqual(a.comparison, "")
        self.assertTrue(any("Budget tip" in n for n in a.notes))

    def test_temperature_swing_suggests_removable_layer(self):
        warm_then_cool = fc(make_payload("Pune", temp=lambda d, h: 31.0 if h < 12 else 17.0, uv=lambda d, h: 0))
        w = summarize_window(warm_then_cool, T0, datetime(2026, 10, 24, 16))
        a = build_trip_advice(w, None)
        self.assertIn("Light jacket", [c.item for c in a.carry if c.needed])

    def test_cold_weather_wants_real_jacket(self):
        cold = fc(make_payload("Shimla", temp=lambda d, h: 6.0, uv=lambda d, h: 1))
        a = build_trip_advice(summarize_window(cold, T0, datetime(2026, 10, 24, 12)), None)
        self.assertEqual(a.item("Jacket").needed, True)
        self.assertIsNotNone(a.item("Scarf and beanie"))

    def test_avoid_preference_respected(self):
        self.assertNotIn("jeans", apply_avoid("Jeans or chinos", "jeans").lower())
        a = build_trip_advice(self.o, None, "College", "Casual", "Mid-range", avoid="jeans")
        self.assertNotIn("jeans", a.pieces.lower())

    def test_colour_preference_appears_in_reason(self):
        a = build_trip_advice(self.o, None, colours="navy, white")
        self.assertIn("navy, white", a.outfit_reason)

    def test_every_carry_item_has_a_reason(self):
        a = build_trip_advice(self.o, self.d)
        self.assertTrue(all(c.reason for c in a.carry))

    def test_compare_places_similar(self):
        same = summarize_window(DRY, T0, datetime(2026, 10, 24, 11), "Dewas")
        self.assertIn("about the same", compare_places(self.o, same))


if __name__ == "__main__":
    unittest.main()
