"""Quota protection: no calls when off, cache, never retry 429, cool-down, retries only for temporary errors, model fallback."""
import pytest

import config
import gemini_guard as G
from gemini_guard import GeminiUnavailable, run_guarded


class Boom(Exception):
    def __init__(self, msg, code=None, status=""):
        super().__init__(msg)
        self.code, self.status = code, status


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    G.reset_all_for_tests()
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("ENABLE_GEMINI", "true")
    for k in ("GEMINI_TEXT_MODEL", "GEMINI_MAX_CALLS_PER_MINUTE", "GEMINI_MAX_CALLS_PER_DAY", "GEMINI_CACHE_MINUTES"):
        monkeypatch.delenv(k, raising=False)
    now = {"t": 10_000.0}
    monkeypatch.setattr(G, "CLOCK", lambda: now["t"])
    monkeypatch.setattr(G, "SLEEP", lambda s: now.__setitem__("t", now["t"] + s))
    yield now
    G.reset_all_for_tests()


def test_disabled_makes_no_call(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI", "false")
    calls = []
    with pytest.raises(GeminiUnavailable) as e:
        run_guarded("text", lambda m: calls.append(m))
    assert e.value.kind == "disabled" and calls == [] and config.gemini_api_key() == ""


def test_missing_key_makes_no_call(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    with pytest.raises(GeminiUnavailable) as e:
        run_guarded("text", lambda m: "x")
    assert e.value.kind == "no_key"


def test_identical_requests_use_cache():
    calls = []
    key = G.text_cache_key("hello", "sys")
    for _ in range(3):
        out, info = run_guarded("text", lambda m: calls.append(m) or "answer", cache_key=key)
        assert out == "answer"
    assert len(calls) == 1 and info["cached"] is True
    G.clear_cache()
    run_guarded("text", lambda m: calls.append(m) or "answer", cache_key=key)
    assert len(calls) == 2


def test_quota_error_is_never_retried_and_starts_cooldown():
    calls = []

    def fail(m):
        calls.append(m)
        raise Boom("429 RESOURCE_EXHAUSTED: quota exceeded. Please retry in 23s.", code=429, status="RESOURCE_EXHAUSTED")
    with pytest.raises(GeminiUnavailable) as e:
        run_guarded("text", fail)
    assert e.value.kind == "quota" and len(calls) == 1
    with pytest.raises(GeminiUnavailable) as e2:   # next requests make NO API call while paused
        run_guarded("text", fail)
    assert e2.value.kind == "cooldown" and len(calls) == 1
    assert G.cooldown_remaining("text") >= 15 * 60 - 1
    assert G.cooldown_remaining("image") == 0   # text and image are separate
    G.reset_cooldown()
    with pytest.raises(GeminiUnavailable):
        run_guarded("text", fail)
    assert len(calls) == 2


def test_cooldown_ends_with_time(fresh):
    def fail(m):
        raise Boom("429", code=429)
    with pytest.raises(GeminiUnavailable):
        run_guarded("text", fail)
    fresh["t"] += 16 * 60
    assert run_guarded("text", lambda m: "ok")[0] == "ok"


def test_daily_quota_wording_pauses_longer():
    with pytest.raises(GeminiUnavailable):
        run_guarded("text", lambda m: (_ for _ in ()).throw(Boom("Quota exceeded for metric ... PerDay", code=429)))
    assert G.cooldown_remaining("text") >= 3600 - 1


def test_transient_errors_retry_with_backoff_then_succeed(fresh):
    n = {"c": 0}
    sleeps = []

    def flaky(m):
        n["c"] += 1
        if n["c"] < 3:
            raise Boom("503 UNAVAILABLE overloaded", code=503, status="UNAVAILABLE")
        return "fine"
    G.SLEEP = lambda s: sleeps.append(s)
    assert run_guarded("text", flaky)[0] == "fine"
    assert n["c"] == 3 and len(sleeps) == 2 and sleeps[1] > sleeps[0]   # exponential back-off


def test_transient_errors_stop_after_max_retries():
    n = {"c": 0}

    def down(m):
        n["c"] += 1
        raise TimeoutError("timed out")
    with pytest.raises(GeminiUnavailable) as e:
        run_guarded("text", down, max_retries=2)
    assert e.value.kind == "transient" and n["c"] == 3
    n["c"] = 0
    G.reset_cooldown()
    with pytest.raises(GeminiUnavailable):
        run_guarded("text", down, max_retries=0)
    assert n["c"] == 1


def test_auth_errors_not_retried():
    n = {"c": 0}

    def bad(m):
        n["c"] += 1
        raise Boom("API key not valid", code=400)
    with pytest.raises(GeminiUnavailable) as e:
        run_guarded("text", bad)
    assert e.value.kind == "auth" and n["c"] == 1


def test_model_not_found_tries_next_model_and_remembers():
    tried = []

    def call(m):
        tried.append(m)
        if m == config.DEFAULT_TEXT_MODELS[0]:
            raise Boom("404 NOT_FOUND: model is not found for API version", code=404, status="NOT_FOUND")
        return "ok"
    out, info = run_guarded("text", call)
    assert out == "ok" and tried == list(config.DEFAULT_TEXT_MODELS) and info["model"] == config.DEFAULT_TEXT_MODELS[1]
    tried.clear()
    run_guarded("text", call)
    assert tried == [config.DEFAULT_TEXT_MODELS[1]]   # remembered


def test_all_models_missing_gives_clear_message():
    def call(m):
        raise Boom("404 NOT_FOUND model not found", code=404, status="NOT_FOUND")
    with pytest.raises(GeminiUnavailable) as e:
        run_guarded("text", call)
    assert e.value.kind == "model" and "GEMINI_TEXT_MODEL" in e.value.message


def test_user_model_setting_is_used_as_given(monkeypatch):
    monkeypatch.setenv("GEMINI_TEXT_MODEL", "my-model")
    seen = []
    run_guarded("text", lambda m: seen.append(m) or "x")
    assert seen == ["my-model"]


def test_burst_limiter(monkeypatch):
    monkeypatch.setenv("GEMINI_MAX_CALLS_PER_MINUTE", "2")
    calls = []
    run_guarded("text", lambda m: calls.append(1) or "a")
    run_guarded("text", lambda m: calls.append(1) or "b")
    with pytest.raises(GeminiUnavailable) as e:
        run_guarded("text", lambda m: calls.append(1) or "c")
    assert e.value.kind == "local_limit" and len(calls) == 2


def test_daily_cap(monkeypatch):
    monkeypatch.setenv("GEMINI_MAX_CALLS_PER_DAY", "1")
    run_guarded("text", lambda m: "a")
    with pytest.raises(GeminiUnavailable) as e:
        run_guarded("text", lambda m: "b")
    assert e.value.kind == "local_limit"


def test_error_messages_never_contain_the_key():
    def call(m):
        raise Boom("something failed for key=test-key-not-real at https://x/?key=test-key-not-real")
    with pytest.raises(GeminiUnavailable) as e:
        run_guarded("text", call)
    assert "test-key-not-real" not in e.value.message and "test-key-not-real" not in str(e.value)


def test_status_never_exposes_key():
    assert "test-key-not-real" not in str(G.status())
