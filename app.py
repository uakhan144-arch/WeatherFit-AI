"""WeatherFit AI — personal AI weather & fashion assistant (Streamlit UI).

Logic lives in importable modules (weather_service, location, trip, plan_service, stylist, chat_assistant,
preferences, outfit_images, ai_features) so it can be tested without running Streamlit.
"""
from __future__ import annotations

import os
from datetime import datetime, time as dtime, timedelta

import streamlit as st
from dotenv import load_dotenv

from agent import build_stylist_context, fallback_chat
import config
from ai_features import fashionclip_rank, gemini_text, generate_outfit_image, make_llm
from chat_assistant import handle_message, new_state
from location import resolve_location
from outfit_images import list_outfit_images, outfit_query, rank_images
from plan_service import build_plan
from preferences import delete_preferences, load_preferences, save_preferences
from stylist import build_trip_advice
from security import redact
from trip import ForecastUnavailable
from ui_wardrobe import (COMFORT_CHOICES, GENDER_CHOICES, get_db, history_tab, outfit_actions, outfit_tab, run_recommendation, stylist_chat_tab, wardrobe_tab)
from weather_provider import make_weather_client, provider_name
from weather_service import WeatherClient, WeatherError
from ui_voice import voice_tab
from ui_tryon import tryon_tab
import ui_dashboard as dash
import ui_theme

load_dotenv()
config.apply_streamlit_secrets()   # optional: .streamlit/secrets.toml (environment / .env values win)
st.set_page_config(page_title="WeatherFit AI | Personal Stylist", page_icon="👕", layout="wide", initial_sidebar_state="expanded")
ui_theme.apply_theme()

try:  # optional component that asks the *browser* for permission to read its location
    from streamlit_geolocation import streamlit_geolocation
except Exception:  # not installed / incompatible -> manual city entry still works
    streamlit_geolocation = None

OCCASIONS = ["Daily casual", "College", "Office", "Travel", "Party", "Date", "Wedding", "Outdoor activity"]
STYLES = ["Casual", "Minimal", "Sporty", "Formal", "Streetwear", "Smart casual", "Ethnic fusion"]
PERSONALITIES = ["Confident", "Calm", "Creative", "Classic", "Bold", "Playful", "Professional"]
BUDGETS = ["Budget-friendly", "Mid-range", "Premium"]
CHOICES = {"occasion": OCCASIONS, "style": STYLES, "personality": PERSONALITIES, "budget": BUDGETS, "gender": GENDER_CHOICES, "comfort": COMFORT_CHOICES}
QUICK_QUESTIONS = ["What should I wear today?", "Will I need an umbrella or jacket?",
                   "What should I wear for college, office or a party today?"]


@st.cache_resource
def get_weather_client(api_key: str, provider: str = "", cache_seconds: float = 0.0):
    """One shared weather client (and cache) per provider/key. provider ''/auto -> WeatherAPI if a key exists, else free Open-Meteo."""
    return make_weather_client(provider or config.weather_provider(), api_key, cache_seconds or config.weather_cache_seconds())


def fetch_weather(city: str, api_key: str):
    """Backward-compatible helper: current weather dict for a city (raises ValueError subclasses)."""
    return get_weather_client(api_key).get_current(city)


def _init_preferences() -> None:
    if st.session_state.get("prefs_loaded"):
        return
    saved = load_preferences()
    for key, value in saved.items():
        if key in CHOICES and value not in CHOICES[key]:
            continue
        if key == "repeat_days":
            try:
                value = max(1, min(30, int(value)))
            except ValueError:
                continue
        st.session_state[f"pref_{key}"] = value
    st.session_state.setdefault("pref_colours", "Navy, white, beige")
    st.session_state["prefs_from_file"] = bool(saved)
    st.session_state["prefs_loaded"] = True


DEFAULT_CITY = "Indore"


def goto(page: str) -> None:
    """Button callback: switch the sidebar page menu (runs before the script reruns, so changing the widget's state is allowed)."""
    st.session_state["nav_page"] = page


def keyed_container(key: str, **kwargs):
    """st.container(key=...) gives a CSS hook (class st-key-<key>); older Streamlit versions just get a plain container."""
    try:
        return st.container(key=key, **kwargs)
    except TypeError:
        return st.container(**kwargs)


def top_bar():
    """City search + live weather chip + refresh. Returns (typed city, placeholder to fill with the chip once the weather is known)."""
    c1, c2, c3 = st.columns([5, 2.6, 1.7], vertical_alignment="center") if _has_vertical_alignment() else st.columns([5, 2.6, 1.7])
    city = c1.text_input("Search city", DEFAULT_CITY, key="city_search", placeholder="Search city (e.g. Indore, Mumbai, Delhi...)",
                         label_visibility="collapsed")
    chip_slot = c2.empty()
    c3.button("↻ Refresh weather", key="top_refresh")
    return city, chip_slot


def _has_vertical_alignment() -> bool:
    import inspect
    try:
        return "vertical_alignment" in inspect.signature(st.columns).parameters
    except (TypeError, ValueError):
        return False


def sidebar(city: str = DEFAULT_CITY):
    _init_preferences()
    st.session_state.setdefault("nav_page", "Home")
    with st.sidebar:
        ui_theme.html_block(dash.sidebar_brand())
        for page_name in dash.PAGES:   # the page menu: one button per page, the active one is highlighted
            st.button(f"{dash.PAGE_ICONS[page_name]}   {page_name}", key=dash.PAGE_KEYS[page_name], on_click=goto, args=(page_name,))
        st.markdown(dash.nav_active_css(st.session_state.get("nav_page", "Home")), unsafe_allow_html=True)
        st.divider()
        st.markdown('<div class="wf-sh">Your Preferences</div>', unsafe_allow_html=True)
        gender = st.selectbox("Gender", GENDER_CHOICES, key="pref_gender", help="Gender presentation, used for clothing suggestions.")
        style = st.selectbox("Style", STYLES, key="pref_style")
        occasion = st.selectbox("Occasion", OCCASIONS, key="pref_occasion")
        avoid = st.text_input("Clothing to avoid (optional)", key="pref_avoid", placeholder="e.g. jeans, wool")
        with st.expander("More preferences"):
            personality = st.selectbox("Style personality", PERSONALITIES, key="pref_personality")
            budget = st.selectbox("Budget", BUDGETS, key="pref_budget")
            comfort = st.selectbox("Comfort vs polish", COMFORT_CHOICES, index=1, key="pref_comfort")
            repeat_days = st.number_input("Avoid repeating outfits for (days)", 1, 30, 7, key="pref_repeat_days")
            colours = st.text_input("Favourite colours (optional)", key="pref_colours")
        profile = {"occasion": occasion, "style": style, "personality": personality, "budget": budget,
                   "colours": colours, "avoid": avoid, "gender": gender, "comfort": comfort, "repeat_days": int(repeat_days)}

        consent = st.checkbox("Remember these preferences on this computer",
                              value=st.session_state.get("prefs_from_file", False),
                              help="Saved only to a local file (user_preferences.json) when you press Save. Location and chats are never saved.")
        if st.button("Save Preferences", key="save_prefs"):
            if save_preferences(profile, consent):
                st.success("Preferences saved locally.")
            else:
                st.warning("Tick the consent box first - nothing was saved.")
        if st.button("Forget saved preferences", key="forget_prefs"):
            delete_preferences()
            st.session_state["prefs_from_file"] = False
            st.info("Saved preferences deleted.")

        with st.expander("📍 Location"):
            browser = None
            if streamlit_geolocation:
                st.caption("Press the button and allow location access when your browser asks.")
                browser = streamlit_geolocation()
            else:
                st.caption("Automatic detection needs `pip install streamlit-geolocation`. Type your city in the search bar instead.")
            prefer_manual = st.checkbox("Use the typed city instead of my detected location",
                                        help="A city typed in the search bar (other than the default) is used automatically.")
            st.caption("The city comes from the search bar at the top of the page.")
        typed_other = bool(city.strip()) and city.strip().lower() != DEFAULT_CITY.lower()
        location = resolve_location(browser, city, prefer_manual or typed_other)

        with st.expander("🧳 Trip (optional)"):
            dest = st.text_input("Destination city", "", placeholder="e.g. Bhopal").strip()
            default_dep = (datetime.now() + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            dep_date = st.date_input("Departure date", value=default_dep.date())
            dep_time = st.time_input("Departure time", value=dtime(default_dep.hour, 0))
            duration = st.number_input("Journey time (hours)", min_value=0.25, max_value=24.0, value=3.0, step=0.25,
                                       help="Used to work out your arrival time. I don't guess routes or traffic.")
            stay = st.number_input("Time at destination (hours)", min_value=1.0, max_value=12.0, value=3.0, step=0.5)
            st.caption("Departure time = local time where you start.")
        st.caption("Keep any API keys in your local .env file (or Streamlit secrets). Never paste keys into chat.")
        ui_theme.html_block(dash.sidebar_footer())
    planner = {"dest": dest, "depart": datetime.combine(dep_date, dep_time), "duration_h": float(duration),
               "stay_h": float(stay)} if dest else None
    return location, profile, planner


def show_location(location) -> None:
    if location.source == "browser":
        st.success(f"📍 {location.label} — detected from your browser (you granted permission).")
    elif location.source == "manual":
        st.info(f"✍️ Using the city you typed: **{location.label}** (not auto-detected). {location.message}")
    else:
        st.warning(f"📍 {location.message}")


def window_panel(title: str, w) -> None:
    with st.container(border=True):
        st.markdown(f"**{title}**")
        st.write(f"{w.main_condition} · {w.start[5:]} → {w.end[11:]}")
        c = st.columns(4)
        same = round(w.feels_min) == round(w.feels_max)
        c[0].metric("Feels like", f"{w.feels_min:.0f} °C" if same else f"{w.feels_min:.0f}–{w.feels_max:.0f} °C")
        c[1].metric("Rain chance", f"{w.rain_chance_max:.0f}%")
        c[2].metric("Humidity", f"{w.humidity_avg:.0f}%")
        c[3].metric("Wind", f"{w.wind_max_kph:.0f} km/h")
        if w.daytime:
            st.caption(f"UV index up to {w.uv_max:.0f} · expected rain {w.precip_total_mm:.1f} mm")
        if w.partial:
            st.caption("⚠️ The forecast covers only part of this time window.")


def show_plan_classic(plan_state: dict) -> None:
    plan, advice, profile = plan_state["plan"], plan_state["advice"], plan_state["profile"]
    cur = plan.origin_fc["current"]
    for msg in plan.warnings:
        st.warning(msg)
    st.subheader(f"Current weather · {cur['city']} · {cur['condition']}")
    if cur.get("icon"):
        st.image(cur["icon"], width=64)
    m = st.columns(4)
    m[0].metric("Temperature", f"{cur['temp_c']:.1f} °C", f"Feels like {cur['feelslike_c']:.1f} °C", delta_color="off")
    m[1].metric("Humidity", f"{cur['humidity']}%")
    m[2].metric("Rain (now)", f"{cur['precip_mm']:.1f} mm")
    m[3].metric("Wind", f"{cur['wind_kph']:.1f} km/h")
    alerts = plan.origin_fc.get("alerts", [])
    if alerts:
        for a in alerts[:3]:
            st.error(f"⚠️ {a.get('headline') or a.get('event')} ({a.get('severity', 'n/a')})")
    else:
        st.caption("No weather alerts were returned by the weather provider for this area (alert coverage depends on provider, region and plan).")

    if plan.dest_w:
        st.subheader("Origin vs destination")
        a, b = st.columns(2)
        with a:
            window_panel(f"When you leave · {plan.origin_w.place}", plan.origin_w)
        with b:
            window_panel(f"When you arrive · {plan.dest_w.place}", plan.dest_w)
        if advice.comparison:
            st.write(advice.comparison)
    else:
        window_panel(f"Forecast · {plan.origin_w.place}", plan.origin_w)

    st.subheader("Your personalised outfit")
    with st.container(border=True):
        st.markdown(f"### {advice.title}")
        st.write("**Wear:** " + advice.top + "; " + advice.bottom)
        st.write("**Footwear:** " + advice.footwear)
        st.write("**Why this works:** " + advice.outfit_reason)
    with st.container(border=True):
        st.markdown("### Carry & accessories")
        for c in advice.carry:
            st.write(f"{'✅' if c.needed else '➖'} **{c.item}** — {c.reason}")
    for alt in advice.alternatives:
        with st.container(border=True):
            st.markdown(f"### {alt['title']}")
            st.write("**Wear:** " + alt["pieces"])
            st.write("**Extras:** " + alt["extras"])
            st.write("**Why:** " + alt["reason"])
    for note in advice.notes:
        st.caption(note)
    for note in advice.assumptions:
        st.caption("Assumption: " + note)
    st.caption(f"Personalisation: {profile['style']} style · {profile['personality']} personality · "
               f"{profile['budget']} budget · colours: {profile['colours'] or 'open to suggestions'}")


def show_plan(plan_state: dict, include_current: bool = True) -> None:
    """Light-theme cards. If anything in the card rendering fails, the original layout is shown instead.

    ``include_current=False`` skips the current-weather card, warnings and alerts (the dashboard shows those elsewhere)."""
    try:
        plan, advice, profile = plan_state["plan"], plan_state["advice"], plan_state["profile"]
        if include_current:
            for msg in plan.warnings:
                st.warning(msg)
            cur = plan.origin_fc["current"]
            ui_theme.html_block(ui_theme.current_weather_card(cur))
            for a in plan.origin_fc.get("alerts", [])[:3]:
                st.error(f"⚠️ {a.get('headline') or a.get('event')} ({a.get('severity', 'n/a')})")
        if plan.dest_w:
            st.subheader("Leaving and arriving")
            a, b = st.columns(2)
            a.markdown(ui_theme.window_card(f"When you leave · {plan.origin_w.place}", plan.origin_w), unsafe_allow_html=True)
            b.markdown(ui_theme.window_card(f"When you arrive · {plan.dest_w.place}", plan.dest_w), unsafe_allow_html=True)
            if advice.comparison:
                st.write(advice.comparison)
        else:
            ui_theme.html_block(ui_theme.window_card(f"Forecast · {plan.origin_w.place}", plan.origin_w))
        st.subheader("Your outfit")
        ui_theme.html_block(ui_theme.outfit_card(advice.title, advice.top, advice.bottom, advice.footwear, advice.outfit_reason, advice.carry))
        if advice.alternatives:
            names = [alt["title"] for alt in advice.alternatives]
            pick = st.radio("Other options for these conditions", names, horizontal=True, key="alt_pick")
            alt = advice.alternatives[names.index(pick)]
            with st.container(border=True):
                st.markdown(f"**Wear:** {alt['pieces']}")
                st.markdown(f"**Extras:** {alt['extras']}")
                st.caption(alt["reason"])
        for note in advice.notes:
            st.caption(note)
        for note in advice.assumptions:
            st.caption("Assumption: " + note)
        st.caption(f"Personalisation: {profile['style']} style · {profile['personality']} personality · "
                   f"{profile['budget']} budget · colours: {profile['colours'] or 'open to suggestions'}")
    except Exception:  # never lose the outfit because of a styling problem
        show_plan_classic(plan_state)


def chat_tab(location, profile, planner, client, gemini_key: str) -> None:
    st.write("Ask things like *“I live in Indore and need to travel to Bhopal. What should I wear?”* "
             "I use live forecasts, never guesses.")
    st.session_state.setdefault("chat_history", [])
    st.session_state.setdefault("chat_state", new_state())
    cols = st.columns(len(QUICK_QUESTIONS))
    for col, q in zip(cols, QUICK_QUESTIONS):
        if col.button(q, key=f"quick_{q}"):
            st.session_state["queued_q"] = q
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            for w in msg.get("warnings", []):
                st.caption("⚠️ " + w)
            if msg.get("label"):
                st.caption(msg["label"])
            if msg.get("data"):
                with st.expander("Weather data behind this answer"):
                    st.json(msg["data"])
    question = st.chat_input("e.g. Will I need an umbrella in Bhopal tomorrow at 6 pm?") or st.session_state.pop("queued_q", None)
    if not question:
        return
    history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.chat_history]
    st.session_state.chat_history.append({"role": "user", "content": question})
    llm = make_llm() if gemini_key else None
    try:
        with st.spinner("Checking live weather..."):
            reply = handle_message(question, st.session_state.chat_state, profile, location, client,
                                   planner=planner, llm=llm, history=history)
        label = ("✨ Written by Gemini from the live weather data" if reply.used_llm
                 else ("Answer built directly from live weather data" if reply.ok and not reply.followup else ""))
        st.session_state.chat_history.append({"role": "assistant", "content": reply.text, "warnings": reply.warnings,
                                              "label": label, "data": reply.grounded or None})
    except Exception as exc:  # last resort: the original local fallback, only if a plan exists
        plan_state = st.session_state.get("plan")
        if plan_state:
            ctx = build_stylist_context(plan_state["plan"].origin_fc["current"], profile["occasion"], profile["style"],
                                        profile["personality"], profile["budget"])
            text = fallback_chat(ctx, question) + f"\n\n(Assistant error: {type(exc).__name__})"
        else:
            text = f"Sorry, something went wrong ({type(exc).__name__}). Please try again."
        st.session_state.chat_history.append({"role": "assistant", "content": text})
    st.rerun()


def images_tab(plan_state, profile) -> None:
    st.write("Rank outfit photos against today's recommendation using FashionCLIP (falls back to keyword matching if it isn't installed).")
    if not plan_state:
        st.info("Get weather & outfit ideas first, then come back to rank images.")
        return
    adv = plan_state["advice"]
    query = outfit_query(adv.top, adv.bottom, adv.footwear, profile["style"], plan_state["occasion"])
    st.caption(f"Matching images to: *{query}*")
    uploads = st.file_uploader("Add your own outfit/wardrobe photos (optional)", type=["jpg", "jpeg", "png", "webp"],
                               accept_multiple_files=True, key="rank_uploads")
    items = list_outfit_images() + [(u.name, u.getvalue()) for u in (uploads or [])]
    st.caption(f"{len(items)} image(s) available (folder `assets/outfits/` + uploads).")
    if st.button("Rank outfit images", key="rank_btn"):
        with st.spinner("Ranking images (the first FashionCLIP run downloads the model)..."):
            ranked, note = rank_images(query, items)
        if note:
            st.info(note)
        for i, r in enumerate(ranked, 1):
            with st.container(border=True):
                st.image(r.data, caption=f"#{i} · {r.label}", width=260)
                st.caption(f"Ranked by {'FashionCLIP visual similarity' if r.method == 'fashionclip' else 'keyword match'} "
                           f"to today's recommendation. Score {r.score:.2f} (only meaningful relative to the others).")


def generate_tab(plan_state, profile) -> None:
    st.write("Optional: generate a visual concept from today's recommendation. This is separate from weather retrieval.")
    if not plan_state:
        st.info("Get weather & outfit ideas first.")
        return
    cur, adv = plan_state["plan"].origin_fc["current"], plan_state["advice"]
    extra = st.text_input("Extra image details (optional)", "full outfit, head-to-toe, realistic fashion editorial")
    if st.button("Generate my weather-fit image", key="generate_image"):
        prompt = ("Create a photorealistic fashion editorial image showing a complete head-to-toe outfit. "
                  f"Weather: {cur['condition']}, {cur['temp_c']}°C, feels like {cur['feelslike_c']}°C, "
                  f"humidity {cur['humidity']}%, wind {cur['wind_kph']} km/h. "
                  f"Occasion: {plan_state['occasion']}; style: {profile['style']}; personality vibe: {profile['personality']}; "
                  f"budget: {profile['budget']}; preferred colours: {profile['colours']}. "
                  f"Outfit guidance: {adv.pieces}. Accessories: {', '.join(c.item for c in adv.carry if c.needed) or 'none'}. "
                  f"Visual details: {extra}. Show tasteful coordinated clothing and accessories, no text, no logos.")
        try:
            with st.spinner("Generating outfit image with AI..."):
                data = generate_outfit_image(prompt)
            st.image(data, caption="AI-generated outfit concept. Use it as inspiration; availability may vary.")
            st.download_button("Download outfit concept", data=data, file_name="weatherfit_outfit.png", mime="image/png")
        except Exception as exc:
            st.error(f"Could not generate image: {redact(exc)}")


def upload_tab(plan_state, profile) -> None:
    st.write("Upload a photo of your current outfit or a clothing item. Analysis is optional and uses Gemini when configured.")
    if not plan_state:
        st.info("Get weather & outfit ideas first.")
        return
    cur = plan_state["plan"].origin_fc["current"]
    upload = st.file_uploader("Choose an outfit photo", type=["jpg", "jpeg", "png", "webp"])
    if not upload:
        return
    image_bytes = upload.getvalue()
    st.image(image_bytes, caption="Uploaded photo", width=320)
    if st.button("Analyse my outfit", key="analyse_photo"):
        labels = ["casual t-shirt and jeans outfit", "formal office outfit", "sporty activewear outfit", "smart casual outfit",
                  "streetwear outfit", "traditional or ethnic outfit", "rainy weather outfit", "cold weather layered outfit"]
        try:
            with st.spinner("Checking outfit with FashionCLIP..."):
                ranked = fashionclip_rank(image_bytes, labels)
            st.write("**FashionCLIP visual matches:**")
            for label, score in ranked[:3]:
                st.write(f"- {label.title()} — {score:.0%} similarity score")
        except Exception as exc:
            st.caption(f"FashionCLIP is optional and wasn't available: {exc}")
        try:
            prompt = ("You are a helpful, respectful personal fashion stylist. Analyse only visible clothing and accessories in this uploaded image. "
                      f"Current weather is {cur['condition']}, {cur['temp_c']}°C (feels like {cur['feelslike_c']}°C), "
                      f"rain {cur['precip_mm']} mm and wind {cur['wind_kph']} km/h. User profile: {profile}. "
                      "Give: (1) visible outfit summary, (2) what works, (3) weather suitability, (4) 3 specific improvements, "
                      "(5) matching accessories/shoes, (6) colour coordination. Do not infer sensitive traits or identity. Be constructive and concise.")
            with st.spinner("AI stylist is reviewing your outfit..."):
                st.markdown(gemini_text(prompt, image_bytes, upload.type or "image/jpeg"))
        except Exception as exc:
            st.warning(f"AI photo feedback isn't available: {redact(exc)}")


def fetch_plan(client, location, profile, planner) -> bool:
    """Fetch the live forecast and build the advice (same logic the old 'Get weather & outfit ideas' button used)."""
    if not location.usable:
        st.error("I don't have a location yet. Allow location access or type a city in the search bar.")
        return False
    try:
        with st.spinner("Checking live weather..."):
            plan = build_plan(client, location.query, planner["dest"] if planner else None,
                              planner["depart"] if planner else None, planner["duration_h"] if planner else None,
                              planner["stay_h"] if planner else 3.0)
            occasion = profile["occasion"]
            advice = build_trip_advice(plan.origin_w, plan.dest_w, occasion, profile["style"], profile["budget"],
                                       profile["colours"], profile["avoid"], plan.assumptions)
        st.session_state.plan = {"plan": plan, "advice": advice, "profile": dict(profile), "occasion": occasion}
        return True
    except (WeatherError, ForecastUnavailable) as exc:
        st.error(str(exc))
        return False


def home_page(db, plan_state, profile, repeat_days: int, gemini_key: str, provider_label: str) -> None:
    left, right = st.columns([2.35, 1], gap="large")
    plan = plan_state["plan"] if plan_state else None
    advice = plan_state["advice"] if plan_state else None

    with left:
        with keyed_container("hero"):
            ui_theme.html_block(dash.hero_markup())
            st.button("Get started  →" if not plan_state else "Refresh weather & outfit ideas  →", key="hero_start", type="primary")
        if plan_state:
            for msg in plan.warnings:
                st.warning(msg)
            for a in plan.origin_fc.get("alerts", [])[:3]:
                st.error(f"⚠️ {a.get('headline') or a.get('event')} ({a.get('severity', 'n/a')})")

        h0, h1, h2, h3 = st.columns([2.6, 1.5, 1.1, 1])
        h0.markdown(dash.section_header("✦", "Recommended Outfit", "Based on current weather, your style and preference"), unsafe_allow_html=True)
        suggest = h1.button("Suggest from my wardrobe", key="suggest_outfit", type="primary")
        again = h2.button("🔄 Another", key="suggest_another")
        h3.button("View All  →", key="view_all", on_click=lambda: st.session_state.update(show_all=not st.session_state.get("show_all", False)))
        if (suggest or again) and plan_state:
            run_recommendation(db, plan_state, profile, repeat_days, again)
        elif (suggest or again) and not plan_state:
            st.info("Press **Get started** first - outfits are chosen for the real forecast.")

        res = st.session_state.get("rec_result")
        options = (list(res.wardrobe) + ([res.inspiration] if res.inspiration else [])) if res else []
        sel = max(0, min(int(st.session_state.get("main_opt", 0)), len(options) - 1)) if options else None
        if res:
            for m in res.messages:
                (st.warning if res.status in ("limited", "insufficient") else st.info)(m)

        card_col, more_col = st.columns([1.9, 1], gap="medium")
        rec = options[sel] if sel is not None else None
        oid = None
        with card_col:
            with keyed_container("outfit_card"):
                if rec is not None:
                    needs = st.session_state.get("rec_needs")
                    sub = (f"Chosen for {needs.place or 'today'} · feels like {needs.feels_min:.0f}–{needs.feels_max:.0f}°C"
                           if needs is not None else "Chosen for today's forecast")
                    details = dash.outfit_details(rec.label, sub, dash.rec_rows(rec), rec.score if rec.kind == "wardrobe" else None,
                                                  best=bool(sel == 0 and rec.kind == "wardrobe"))
                    ids = st.session_state.get("rec_ids", {})
                    oid = ids.get(f"w{sel}") if rec.kind == "wardrobe" else ids.get("insp")
                elif advice is not None:
                    cur = plan.origin_fc["current"]
                    details = dash.outfit_details(advice.title, f"Matched to today's {float(cur['temp_c']):.0f}°C, {str(cur['condition']).lower()} in "
                                                  f"{str(cur['city']).split(',')[0]}", dash.advice_rows(advice))
                else:
                    details = dash.outfit_details("Your outfit appears here", "Press Get started to load the live forecast first.", [])
                ui_theme.html_block(dash.card_row(dash.look_visual(rec, advice), details))
                st.button("👕  Try On", key="tryon_go", on_click=goto, args=("Try-On",))
        with more_col:
            with keyed_container("more_card"):
                st.markdown("**More Options**")
                shown = 0
                for i, o in enumerate(options):
                    if i == sel:
                        continue
                    ui_theme.html_block(dash.option_row(o))
                    st.button("View this look  →", key=f"opt_{i}", on_click=lambda i=i: st.session_state.update(main_opt=i))
                    shown += 1
                if not options and advice is not None:
                    for alt in advice.alternatives[:3]:
                        ui_theme.html_block(dash.alt_row(alt))
                        shown += 1
                if not shown:
                    st.caption("Other options appear here after you press Get started." if not plan_state else "No other options for these conditions yet.")
        if oid:
            outfit_actions(db, oid, f"home_{sel}")

        if st.session_state.get("show_all"):
            with st.container(border=True):
                st.markdown("#### All options & actions")
                outfit_tab(db, plan_state, profile, repeat_days, gemini_key, show_controls=False)

        ui_theme.html_block(dash.feature_strip(provider_label))

    with right:
        fc = plan.origin_fc if plan else None
        ui_theme.html_block(dash.location_card(fc, dash.style_tip(advice) if advice else ""))
        ui_theme.html_block(dash.hourly_card(fc))
        st.markdown('<div class="wf-sh" style="margin-top:.2rem">Quick Actions</div>', unsafe_allow_html=True)
        q1, q2 = st.columns(2)
        q1.button("🎙️ Voice Assistant", key="qa_voice", on_click=goto, args=("Voice Assistant",))
        q2.button("👗 Virtual Try-On", key="qa_tryon", on_click=goto, args=("Try-On",))
        q3, q4 = st.columns(2)
        q3.button("🧺 Your Wardrobe", key="qa_wardrobe", on_click=goto, args=("Wardrobe",))
        q4.button("⭐ Style Guide", key="qa_style", on_click=goto, args=("Style Guide",))
        ui_theme.html_block('<div class="wf-hand-r">Fashion<br>meets Weather ♡</div>')

    if plan_state:   # full width, outside the columns
        with st.expander("Forecast, trip comparison & weather-based outfit guide", expanded=bool(plan.dest_w)):
            show_plan(plan_state, include_current=False)


def page_title(icon: str, title: str, subtitle: str) -> None:
    st.markdown(dash.section_header(icon, title, subtitle), unsafe_allow_html=True)


def main() -> None:
    provider = config.weather_provider()
    provider_label = "Open-Meteo" if provider == "openmeteo" else "WeatherAPI.com"
    city, chip_slot = top_bar()
    location, profile, planner = sidebar(city)
    weather_key = os.getenv("WEATHERAPI_KEY", "").strip()
    gemini_key = config.gemini_api_key()   # "" when there is no key OR ENABLE_GEMINI=false
    page = st.session_state.get("nav_page", "Home")
    if provider == "weatherapi" and not weather_key:
        st.info("WEATHER_PROVIDER=weatherapi needs `WEATHERAPI_KEY=your_key` in `.env` (or remove WEATHER_PROVIDER to use the free Open-Meteo service).")
    client = get_weather_client(weather_key, provider, config.weather_cache_seconds())

    # Buttons keep their state under their key at the start of the rerun, so the forecast can be fetched before anything below is drawn.
    if st.session_state.get("hero_start") or st.session_state.get("top_refresh"):
        fetch_plan(client, location, profile, planner)
    plan_state = st.session_state.get("plan")
    chip_slot.markdown(dash.top_weather_chip(plan_state["plan"].origin_fc["current"] if plan_state else None), unsafe_allow_html=True)

    if page == "Home":
        if not gemini_key:
            st.info(f"{config.gemini_off_reason()} The Gemini chat wording, photo analysis and image generation are optional. "
                    "Weather-based outfit suggestions, the voice assistant and a data-based chat still work without it.")
        show_location(location)
    db = get_db()
    repeat_days = int(profile.get("repeat_days", 7))
    llm = make_llm() if gemini_key else None

    if page == "Home":
        home_page(db, plan_state, profile, repeat_days, gemini_key, provider_label)
        return
    show_location(location)
    if page == "Try-On":
        page_title("👗", "Virtual try-on", "See how your wardrobe pieces look together - 2-D preview or live camera (AR).")
        tryon_tab(db, plan_state)
    elif page == "Wardrobe":
        page_title("🧺", "Your wardrobe", "Your own clothes, saved privately on this computer, plus your outfit history.")
        tab_wardrobe, tab_hist = st.tabs(["🧺 My wardrobe", "🗓️ Outfit history"])
        with tab_wardrobe:
            wardrobe_tab(db, gemini_key)
        with tab_hist:
            history_tab(db)
    elif page == "Style Guide":
        page_title("📖", "Style guide", "Chat with your stylist, rank outfit photos, generate looks and review your own outfit.")
        tab_style, tab_chat, tab_rank, tab_image, tab_photo = st.tabs(
            ["🤖 AI stylist chat", "💬 Weather chat", "🖼️ Rank outfit images", "✨ Generate outfit image", "📷 Upload my outfit"])
        with tab_style:
            stylist_chat_tab(db, location, profile, planner, client, llm, repeat_days, gemini_key)
        with tab_chat:
            chat_tab(location, profile, planner, client, gemini_key)
        with tab_rank:
            images_tab(plan_state, profile)
        with tab_image:
            generate_tab(plan_state, profile)
        with tab_photo:
            upload_tab(plan_state, profile)
    elif page == "Voice Assistant":
        page_title("🎙️", "Voice assistant", "Ask about the weather or a trip by voice or text - in English, Hindi or Hinglish.")
        voice_tab(location, profile, planner, client)


if __name__ == "__main__":
    main()
