import unittest

import requests

from tests_support import KEY, Clock, FakeResponse, FakeSession, conn_error, make_payload, timeout_error
from weather_service import (WeatherAuthError, WeatherClient, WeatherConfigError, WeatherConnectionError,
                             WeatherDataError, WeatherError, WeatherNotFound, WeatherRateLimit,
                             WeatherServiceError, WeatherTimeout)


def client(routes, **kw):
    s = FakeSession(routes)
    return WeatherClient(KEY, session=s, **kw), s


class WeatherServiceTests(unittest.TestCase):
    def test_forecast_normalised_with_alerts_and_request_options(self):
        alert = {"headline": "Heat wave", "event": "Heat", "severity": "Severe", "desc": "x"}
        c, s = client({"indore": make_payload(alerts=[alert])})
        fc = c.get_forecast("Indore")
        self.assertEqual(len(fc["hours"]), 72)
        self.assertEqual(fc["current"]["city"], "Indore, India")
        self.assertEqual(fc["alerts"][0]["headline"], "Heat wave")
        call = s.calls[0]
        self.assertEqual(call["params"]["alerts"], "yes")
        self.assertEqual(call["params"]["key"], KEY)
        self.assertEqual(call["timeout"], (5, 15))
        self.assertTrue(fc["current"]["icon"].startswith("https://"))

    def test_current_has_legacy_fields(self):
        c, _ = client({"indore": make_payload()})
        cur = c.get_current("Indore")
        for k in ("city", "temp_c", "feelslike_c", "humidity", "precip_mm", "wind_kph", "condition", "icon", "is_day", "localtime"):
            self.assertIn(k, cur)

    def test_timeout_retries_once_then_raises_without_leaking_key(self):
        c, s = client({"indore": timeout_error()})
        with self.assertRaises(WeatherTimeout) as cm:
            c.get_forecast("Indore")
        self.assertEqual(len(s.calls), 2)
        self.assertNotIn(KEY, str(cm.exception))

    def test_connection_error_does_not_leak_key(self):
        c, _ = client({"indore": conn_error()})
        with self.assertRaises(WeatherConnectionError) as cm:
            c.get_current("Indore")
        self.assertNotIn(KEY, str(cm.exception))
        self.assertNotIn("http", str(cm.exception))

    def test_server_error(self):
        c, s = client({"indore": FakeResponse(503, {})})
        with self.assertRaises(WeatherServiceError):
            c.get_current("Indore")
        self.assertEqual(len(s.calls), 2)

    def test_error_codes_map_to_specific_errors(self):
        cases = [(1006, WeatherNotFound), (2006, WeatherAuthError), (2007, WeatherRateLimit), (9999, WeatherServiceError)]
        for code, exc in cases:
            c, _ = client({"indore": FakeResponse(400, {"error": {"code": code, "message": "m"}})})
            with self.assertRaises(exc, msg=code):
                c.get_current("Indore")

    def test_unknown_city(self):
        c, _ = client({})
        with self.assertRaises(WeatherNotFound):
            c.get_forecast("Nowhereville")

    def test_malformed_payload_and_bad_json(self):
        c, _ = client({"a": {"location": {}}, "b": FakeResponse(200, None, bad_json=True)})
        with self.assertRaises(WeatherDataError):
            c.get_current("a")
        with self.assertRaises(WeatherError):
            c.get_current("b")

    def test_missing_key_and_blank_city(self):
        with self.assertRaises(WeatherConfigError):
            WeatherClient("", session=FakeSession()).get_current("Indore")
        with self.assertRaises(WeatherNotFound):
            WeatherClient(KEY, session=FakeSession()).get_current("  ")

    def test_errors_are_valueerrors_for_backward_compatibility(self):
        self.assertTrue(issubclass(WeatherTimeout, ValueError))

    def test_cache_hit_then_expiry(self):
        clock = Clock()
        c, s = client({"indore": make_payload()}, cache_ttl=600, clock=clock)
        c.get_forecast("Indore"); c.get_forecast("indore ")
        self.assertEqual(len(s.calls), 1)
        clock.t += 601
        c.get_forecast("Indore")
        self.assertEqual(len(s.calls), 2)

    def test_stale_cache_served_when_api_down_and_flagged(self):
        clock = Clock()
        c, s = client({"indore": make_payload()}, cache_ttl=600, stale_ttl=3600, clock=clock)
        c.get_forecast("Indore")
        s.routes["indore"] = timeout_error()
        clock.t += 900
        fc = c.get_forecast("Indore")
        self.assertTrue(fc["stale"])
        self.assertEqual(fc["age_s"], 900)
        clock.t += 4000  # too old even for stale
        with self.assertRaises(WeatherTimeout):
            c.get_forecast("Indore")

    def test_not_found_is_never_masked_by_stale_cache(self):
        clock = Clock()
        c, s = client({"indore": make_payload()}, clock=clock)
        c.get_forecast("Indore")
        s.routes["indore"] = FakeResponse(400, {"error": {"code": 1006, "message": "x"}})
        clock.t += 700
        with self.assertRaises(WeatherNotFound):
            c.get_forecast("Indore")

    def test_cached_data_is_not_mutated_by_callers(self):
        c, _ = client({"indore": make_payload()})
        c.get_forecast("Indore")["hours"].clear()
        self.assertEqual(len(c.get_forecast("Indore")["hours"]), 72)


if __name__ == "__main__":
    unittest.main()
