from typing import Any

def build_recommendations(weather: dict[str, Any], occasion: str, style: str) -> list[dict[str, str]]:
    """Generate explainable suggestions from weather and user preferences."""
    temp = weather["feelslike_c"]
    condition = weather["condition"].lower()
    rainy = weather["precip_mm"] >= 0.5 or any(
        word in condition for word in ("rain", "drizzle", "shower", "thunder")
    )
    windy = weather["wind_kph"] >= 28

    if temp >= 32:
        top, bottom, shoes = "Light cotton/linen T-shirt", "Breathable light trousers", "Breathable sneakers or sandals"
        reason = "It feels hot, so breathable and lightweight fabrics can improve comfort."
    elif temp >= 25:
        top, bottom, shoes = "Cotton T-shirt or lightweight shirt", "Chinos or lightweight trousers", "Comfortable sneakers"
        reason = "Warm conditions suit a light top and breathable bottoms."
    elif temp >= 18:
        top, bottom, shoes = "T-shirt with an overshirt", "Jeans or chinos", "Closed-toe sneakers"
        reason = "Mild temperatures make a removable layer useful."
    elif temp >= 10:
        top, bottom, shoes = "Long-sleeve top with a sweater or light jacket", "Full-length trousers", "Closed shoes"
        reason = "Cool conditions call for an insulating layer and full-length trousers."
    else:
        top, bottom, shoes = "Thermal/base layer with a warm sweater and coat", "Warm full-length trousers", "Warm closed shoes"
        reason = "Cold conditions call for multiple warm layers."

    extras: list[str] = []
    if rainy:
        extras += ["Water-resistant outer layer", "Water-resistant footwear or an umbrella"]
    if windy:
        extras.append("Wind-resistant jacket")
    if weather["humidity"] >= 75 and temp >= 25:
        extras.append("Light, moisture-wicking fabric")

    if occasion == "Office":
        top = "Breathable formal shirt" if temp >= 18 else "Formal shirt with a warm blazer or coat"
        bottom = "Tailored trousers"
        shoes = "Formal closed shoes"
    elif occasion == "College":
        top = top + " (easy everyday fit)"
        shoes = "Comfortable sneakers"
    elif occasion == "Travel":
        bottom = "Stretch trousers or comfortable jeans"
        shoes = "Comfortable walking shoes"
    elif occasion == "Party":
        top = "A smart, breathable shirt or stylish top"
        bottom = "Well-fitted trousers or dark jeans"
        shoes = "Clean smart-casual shoes"
    elif occasion == "Outdoor activity":
        top = "Moisture-wicking sports top"
        bottom = "Flexible activewear trousers"
        shoes = "Supportive activity shoes"

    style_note = {
        "Minimal": "Keep colours neutral and the outfit simple.",
        "Sporty": "Choose flexible pieces and athletic-inspired layers.",
        "Formal": "Prefer clean lines and coordinated, polished pieces.",
        "Streetwear": "Try a relaxed fit and one statement layer.",
        "Casual": "Keep the fit comfortable and easy to mix and match.",
    }.get(style, "Choose comfortable pieces that suit your personal taste.")

    items = [{
        "title": f"Recommended {style.lower()} {occasion.lower()} outfit",
        "pieces": f"{top}; {bottom}; {shoes}",
        "extras": ", ".join(extras) if extras else "No special weather accessory needed",
        "reason": f"{reason} {style_note}",
    }]
    if rainy:
        items.append({
            "title": "Rain-ready adjustment",
            "pieces": "Quick-drying layers, water-resistant jacket, and water-resistant shoes",
            "extras": "Umbrella if useful; avoid delicate suede or easily soaked footwear",
            "reason": "Rain or storm conditions are reported, so prioritise staying dry.",
        })
    if windy and not rainy:
        items.append({
            "title": "Wind-ready adjustment",
            "pieces": "Your main outfit plus a lightweight wind-resistant outer layer",
            "extras": "Secure loose scarves and hats outdoors",
            "reason": "Wind can make conditions feel cooler and affect loose layers.",
        })
    return items

