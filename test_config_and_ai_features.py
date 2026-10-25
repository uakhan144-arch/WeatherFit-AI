"""Settings + the Gemini wrapper in ai_features, using a fake `google.genai` SDK (no network, no real key)."""
import sys
import types

import pytest

import config
import gemini_guard as G
from gemini_guard import GeminiUnavailable


def test_gemini_switch_and_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k-123456")
    monkeypatch.delenv("ENABLE_GEMINI", raising=False)
    assert config.gemini_enabled() and config.gemini_api_key() == "k-123456" and config.gemini_off_reason() == ""
    for off in ("false", "0", "No", "OFF"):
        monkeypatch.setenv("ENABLE_GEMINI", off)
        assert not config.gemini_enabled() and config.gemini_api_key() == "" and "ENABLE_GEMINI" in config.gemini_off_reason()
    monkeypatch.setenv("ENABLE_GEMINI", "true"); monkeypatch.setenv("GEMINI_API_KEY", "")
    assert "GEMINI_API_KEY" in config.gemini_off_reason()


def test_provider_choice(monkeypatch):
    monkeypatch.delenv("WEATHER_PROVIDER", raising=False); monkeypatch.setenv("WEATHERAPI_KEY", "")
    assert config.weather_provider() == "openmeteo"
    monkeypatch.setenv("WEATHERAPI_KEY", "abc")
    assert config.weather_provider() == "weatherapi"
    monkeypatch.setenv("WEATHER_PROVIDER", "open-meteo")
    assert config.weather_provider() == "openmeteo"


def test_cache_minutes_and_bad_numbers(monkeypatch):
    monkeypatch.setenv("WEATHER_CACHE_MINUTES", "30")
    assert config.weather_cache_seconds() == 1800
    monkeypatch.setenv("WEATHER_CACHE_MINUTES", "abc")
    assert config.weather_cache_seconds() == 600


def test_models_are_separate_and_overridable(monkeypatch):
    monkeypatch.delenv("GEMINI_TEXT_MODEL", raising=False); monkeypatch.delenv("GEMINI_IMAGE_MODEL", raising=False)
    assert config.model_chain("text") != config.model_chain("image")
    monkeypatch.setenv("GEMINI_TEXT_MODEL", "a, b")
    assert config.model_chain("text") == ["a", "b"] and config.model_chain("image") == list(config.DEFAULT_IMAGE_MODELS)


# ------------------------------------------------------------------ fake SDK
class FakeAPIError(Exception):
    def __init__(self, code, msg):
        super().__init__(f"{code} {msg}")
        self.code = code


@pytest.fixture()
def fake_genai(monkeypatch):
    calls = []
    plan = {"errors": []}

    class Models:
        def generate_content(self, model, contents, config=None):
            calls.append(model)
            if plan["errors"]:
                raise plan["errors"].pop(0)
            return types.SimpleNamespace(text="hello from fake", parts=[])

    class Client:
        def __init__(self, api_key=None, http_options=None):
            self.models = Models()

    t = types.SimpleNamespace(HttpOptions=lambda **kw: kw, HttpRetryOptions=lambda **kw: kw, GenerateContentConfig=lambda **kw: kw,
                              Part=types.SimpleNamespace(from_bytes=lambda **kw: kw))
    google = types.ModuleType("google"); genai = types.ModuleType("google.genai")
    genai.Client, genai.types = Client, t
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google); monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", t)
    G.reset_all_for_tests()
    monkeypatch.setenv("GEMINI_API_KEY", "k-123456"); monkeypatch.setenv("ENABLE_GEMINI", "true")
    monkeypatch.delenv("GEMINI_TEXT_MODEL", raising=False)
    monkeypatch.setattr(G, "SLEEP", lambda s: None)
    yield calls, plan
    G.reset_all_for_tests()


def test_gemini_text_caches_identical_questions(fake_genai):
    import ai_features as A
    calls, _ = fake_genai
    assert A.gemini_text("same question") == "hello from fake"
    assert A.gemini_text("same question") == "hello from fake"
    assert len(calls) == 1
    A.gemini_text("another question")
    assert len(calls) == 2


def test_gemini_text_quota_error_pauses_and_stops_calling(fake_genai):
    import ai_features as A
    calls, plan = fake_genai
    plan["errors"] = [FakeAPIError(429, "RESOURCE_EXHAUSTED quota")] * 5
    with pytest.raises(GeminiUnavailable) as e:
        A.gemini_text("q1")
    assert e.value.kind == "quota"
    for q in ("q2", "q3", "q4"):
        with pytest.raises(GeminiUnavailable):
            A.gemini_text(q)
    assert len(calls) == 1      # one request only; nothing else reached the API


def test_gemini_disabled_never_touches_sdk(fake_genai, monkeypatch):
    import ai_features as A
    calls, _ = fake_genai
    monkeypatch.setenv("ENABLE_GEMINI", "false")
    with pytest.raises(GeminiUnavailable):
        A.gemini_text("q")
    with pytest.raises(GeminiUnavailable):
        A.generate_outfit_image("q")
    assert calls == [] and A.make_llm() is None


def test_text_and_image_use_their_own_models(fake_genai, monkeypatch):
    import ai_features as A
    calls, _ = fake_genai
    monkeypatch.setenv("GEMINI_TEXT_MODEL", "text-model"); monkeypatch.setenv("GEMINI_IMAGE_MODEL", "image-model")
    A.gemini_text("hi")
    with pytest.raises(GeminiUnavailable):
        A.generate_outfit_image("draw")     # fake returns no image -> clear error
    assert calls[0] == "text-model" and calls[1] == "image-model"


def test_make_llm_is_none_without_key(monkeypatch):
    import ai_features as A
    monkeypatch.setenv("GEMINI_API_KEY", "")
    assert A.make_llm() is None
