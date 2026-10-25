"""Small orchestration layer for WeatherFit AI's fashion-stylist agent."""
from __future__ import annotations
from typing import Any
from recommendations import build_recommendations


def build_stylist_context(weather: dict[str, Any], occasion: str, style: str, personality: str, budget: str) -> dict[str, Any]:
    """Collect weather + user preferences before handing work to the LLM or fallback."""
    return {
        "weather": weather,
        "occasion": occasion,
        "style": style,
        "personality": personality,
        "budget": budget,
        "rule_recommendations": build_recommendations(weather, occasion, style),
    }


def fallback_chat(context: dict[str, Any], question: str) -> str:
    """Useful local answer when no LLM key is configured or the API is unavailable."""
    w = context["weather"]
    outfit = context["rule_recommendations"][0]
    return (
        f"For {context['occasion'].lower()} in {w['city']}, it's {w['condition'].lower()} and "
        f"{w['feelslike_c']:.1f}°C feels-like. I'd start with {outfit['pieces']}. "
        f"Accessories: {outfit['extras']}. Why: {outfit['reason']} "
        f"I'll keep the look {context['style'].lower()}, with a {context['personality'].lower()} vibe "
        f"and a {context['budget'].lower()} budget. For your question (“{question.strip()}”), "
        "tell me your preferred colours or any clothing items you already own and I can narrow it down."
    )
