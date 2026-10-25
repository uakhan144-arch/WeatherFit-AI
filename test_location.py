import unittest

from location import parse_browser_payload, resolve_location

GOOD = {"latitude": 22.7196, "longitude": 75.8577, "accuracy": 30}
DENIED = {"latitude": None, "longitude": None, "accuracy": None}


class LocationFallbackTests(unittest.TestCase):
    def test_browser_coordinates_are_labelled_as_detected(self):
        r = resolve_location(GOOD)
        self.assertEqual((r.source, r.detected, r.query), ("browser", True, "22.7196,75.8577"))

    def test_permission_denied_with_manual_city_falls_back_and_is_not_called_detected(self):
        r = resolve_location(DENIED, "Bhopal")
        self.assertEqual((r.source, r.detected, r.query), ("manual", False, "Bhopal"))
        self.assertIn("not available", r.message)

    def test_permission_denied_without_city_is_none_and_asks_for_manual_entry(self):
        r = resolve_location(DENIED, "")
        self.assertEqual((r.source, r.detected, r.usable), ("none", False, False))
        self.assertIn("type your city", r.message)

    def test_component_unavailable_none_payload(self):
        self.assertEqual(resolve_location(None, "").source, "none")
        self.assertEqual(resolve_location(None, "Indore").source, "manual")

    def test_error_payload_is_not_treated_as_detected(self):
        r = resolve_location({**GOOD, "error": "User denied Geolocation"}, "Pune")
        self.assertFalse(r.detected)
        self.assertEqual(r.source, "manual")

    def test_invalid_coordinates_rejected(self):
        for bad in ({"latitude": 123, "longitude": 10}, {"latitude": "x", "longitude": 1},
                    {"latitude": float("nan"), "longitude": 1}, {"latitude": 1}, "oops", []):
            self.assertIsNone(parse_browser_payload(bad), bad)

    def test_prefer_manual_overrides_browser(self):
        r = resolve_location(GOOD, "Delhi", prefer_manual=True)
        self.assertEqual((r.source, r.query, r.detected), ("manual", "Delhi", False))

    def test_browser_wins_over_manual_by_default(self):
        self.assertEqual(resolve_location(GOOD, "Delhi").source, "browser")


if __name__ == "__main__":
    unittest.main()
