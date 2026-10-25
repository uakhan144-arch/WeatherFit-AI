from recommendations import build_recommendations
from agent import build_stylist_context, fallback_chat

BASE = {"city":"Indore, India", "temp_c":31.4, "feelslike_c":32.0, "humidity":45,
        "precip_mm":0.0, "wind_kph":8.0, "condition":"Sunny", "localtime":"", "icon":"", "is_day":True}

def test_hot_weather_suggests_light_clothing():
    result = build_recommendations({**BASE, "feelslike_c":35.0}, "Daily casual", "Casual")
    assert "Light cotton/linen" in result[0]["pieces"]

def test_rain_adds_rain_ready_suggestion():
    result = build_recommendations({**BASE, "condition":"Light rain", "precip_mm":1.2}, "Travel", "Sporty")
    assert len(result) == 2 and result[1]["title"] == "Rain-ready adjustment"

def test_office_preference_changes_outfit():
    result = build_recommendations(BASE, "Office", "Formal")
    assert "formal shirt" in result[0]["pieces"].lower()
    assert "Formal closed shoes" in result[0]["pieces"]

def test_windy_weather_adds_wind_adjustment():
    result = build_recommendations({**BASE, "wind_kph":35.0}, "Daily casual", "Minimal")
    assert len(result) == 2 and result[1]["title"] == "Wind-ready adjustment"

def test_agent_context_contains_personal_preferences():
    ctx = build_stylist_context(BASE, "College", "Streetwear", "Creative", "Budget-friendly")
    assert ctx["personality"] == "Creative" and ctx["budget"] == "Budget-friendly"
    assert ctx["rule_recommendations"]

def test_fallback_chat_uses_weather_and_recommendation():
    ctx = build_stylist_context(BASE, "Travel", "Casual", "Confident", "Mid-range")
    answer = fallback_chat(ctx, "what shoes?")
    assert "Indore" in answer and "Accessories" in answer and "what shoes?" in answer
