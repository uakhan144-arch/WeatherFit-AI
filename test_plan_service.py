import unittest
from datetime import datetime

from plan_service import build_plan
from tests_support import KEY, FakeSession, make_payload, timeout_error
from trip import ForecastUnavailable
from weather_service import WeatherClient, WeatherTimeout

NOW = datetime(2026, 10, 24, 10, 0)


def client(routes):
    return WeatherClient(KEY, session=FakeSession(routes))


class PlanServiceTests(unittest.TestCase):
    def test_origin_only_looks_at_rest_of_day(self):
        p = build_plan(client({"indore": make_payload("Indore")}), "Indore", now=NOW)
        self.assertIsNone(p.dest_w)
        self.assertEqual((p.origin_w.start, p.origin_w.end), ("2026-10-24 10:00", "2026-10-24 22:00"))
        self.assertIn("rest of today", p.assumptions[0])

    def test_destination_window_starts_at_arrival(self):
        c = client({"indore": make_payload("Indore"), "bhopal": make_payload("Bhopal")})
        p = build_plan(c, "Indore", "Bhopal", datetime(2026, 10, 24, 9), 3.5, 2, NOW)
        self.assertEqual((p.dest_w.start, p.dest_w.end), ("2026-10-24 12:30", "2026-10-24 14:30"))
        self.assertEqual((p.origin_w.start, p.origin_w.end), ("2026-10-24 09:00", "2026-10-24 10:00"))
        self.assertEqual(p.assumptions, [])

    def test_destination_failure_is_warning_not_crash(self):
        c = client({"indore": make_payload("Indore"), "bhopal": timeout_error()})
        p = build_plan(c, "Indore", "Bhopal", None, 3, 3, NOW)
        self.assertIsNone(p.dest_w)
        self.assertTrue(any("couldn't get destination" in w for w in p.warnings))

    def test_origin_failure_raises(self):
        with self.assertRaises(WeatherTimeout):
            build_plan(client({"indore": timeout_error()}), "Indore", now=NOW)

    def test_out_of_range_departure_raises(self):
        with self.assertRaises(ForecastUnavailable):
            build_plan(client({"indore": make_payload("Indore")}), "Indore", depart=datetime(2026, 12, 25, 9), now=NOW)


if __name__ == "__main__":
    unittest.main()
