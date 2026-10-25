"""Streamlit tab for the voice assistant.

* Speech-to-text and text-to-speech run in the user's BROWSER (Web Speech API) inside ``voice_component/index.html`` -
  free, no API key, nothing is sent to Gemini or OpenAI for voice. (Chrome/Edge send microphone audio to their own online speech
  service; see the browser note in the tab.)
* A typed question box always works, even when the microphone or speech recognition is unavailable.
* The answer itself comes from ``voice_assistant.answer`` (rule-based; Gemini only if enabled AND requested).
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

import config
import gemini_guard
import voice_assistant as va
from ai_features import list_gemini_models, make_llm
from chat_assistant import new_state
from gemini_guard import GeminiUnavailable
from security import redact
from weather_provider import attribution_markdown

try:  # declared once per process; if it can't be declared the typed box still works
    _voice_widget = components.declare_component("weatherfit_voice", path=str(Path(__file__).resolve().parent / "voice_component"))
except Exception:  # pragma: no cover - depends on the Streamlit version
    _voice_widget = None

LANG_CHOICES = {"English / Hinglish (en-IN)": "en-IN", "हिन्दी Hindi (hi-IN)": "hi-IN"}
EXAMPLES = ["Today I need to travel from Bhopal to Indore. What should I wear?",
            "Aaj Bhopal se Indore jaana hai, kya pehnu?",
            "Will I need an umbrella in Indore tomorrow at 6 pm?"]
MAX_HISTORY = 20


def _gemini_line(llm_available: bool) -> str:
    s = gemini_guard.status()
    if not s["enabled"]:
        return "Gemini: **off** (`ENABLE_GEMINI=false`). Everything below uses the built-in weather rules."
    if not s["has_key"]:
        return "Gemini: **off** (no `GEMINI_API_KEY`). Everything below uses the built-in weather rules."
    if s["cooldown_text_s"] > 0:
        return f"Gemini: **paused for about {int(s['cooldown_text_s'] // 60) + 1} min** after a quota error. Built-in rules are answering."
    return "Gemini: **available** but not used for normal weather questions (only if you tick the box below, or ask a non-weather question)."


def _process(text: str, source: str, location, profile, planner, client, use_gemini: bool) -> None:
    llm = make_llm()
    with st.spinner("Checking the weather and preparing your answer…"):
        ans = va.answer(text, st.session_state.voice_chat_state, profile, location, client, planner=planner,
                        llm=llm, use_gemini_wording=use_gemini)
    hist = st.session_state.voice_history
    hist.append({"role": "user", "content": text, "source": source, "language": ans.language})
    hist.append({"role": "assistant", "content": ans.display, "ok": ans.ok, "answer": ans})
    del hist[:-MAX_HISTORY]
    st.session_state.voice_speak = {"id": uuid.uuid4().hex[:10], "text": ans.spoken, "lang": ans.speak_lang}
    st.session_state.voice_error = ""
    st.rerun()   # re-render so the widget receives the new answer and speaks it


def _show_message(m: dict[str, Any]) -> None:
    with st.chat_message(m["role"]):
        if m["role"] == "user":
            icon = "🎤" if m.get("source") == "voice" else "⌨️"
            st.markdown(f"{icon} {m['content']}")
            return
        ans: va.VoiceAnswer = m["answer"]
        (st.markdown if m.get("ok", True) else st.warning)(m["content"])
        for note in ans.notes:
            st.caption("⚠️ " + note)
        if ans.stamp:
            st.caption("🕒 " + ans.stamp)
        if ans.gemini_status == "live":
            if ans.source == "gemini":
                st.caption("✨ Wording written by Gemini from the live weather data.")
        elif ans.gemini_note:
            st.caption("ℹ️ " + ans.gemini_note)
        if ans.details or ans.data:
            with st.expander("Details and the weather data behind this answer"):
                if ans.details:
                    st.markdown(ans.details)
                if ans.data:
                    st.json(ans.data)


def _gemini_panel() -> None:
    with st.expander("🔧 Gemini status and quota protection"):
        s = gemini_guard.status()
        st.write(f"- Gemini enabled: **{s['enabled']}** · key present: **{s['has_key']}** (the key itself is never shown)")
        st.write(f"- Text models tried in order: `{', '.join(s['text_models'])}`" + (f" · in use: `{s['text_model_in_use']}`" if s["text_model_in_use"] else ""))
        st.write(f"- Image models tried in order: `{', '.join(s['image_models'])}`" + (f" · in use: `{s['image_model_in_use']}`" if s["image_model_in_use"] else ""))
        st.write(f"- Requests this minute: **{s['calls_last_minute']}** · since the app started: **{s['calls_today']}** · cached answers kept: **{s['cache_entries']}**")
        if s["cooldown_text_s"] > 0:
            st.warning(f"Paused after a quota error for about {int(s['cooldown_text_s'] // 60) + 1} more minute(s).")
        c1, c2 = st.columns(2)
        if c1.button("Check which models my key can use", key="voice_list_models",
                     help="Lists model names only. It does not use your generation quota."):
            try:
                names = list_gemini_models()
                st.success("Models your key can use: " + (", ".join(f"`{n}`" for n in names) if names else "none were returned."))
                st.caption("Put one of these names in GEMINI_TEXT_MODEL (text) or GEMINI_IMAGE_MODEL (images) in your .env file.")
            except GeminiUnavailable as exc:
                st.error(exc.message)
        if c2.button("Resume Gemini now (clear the pause)", key="voice_reset_cooldown"):
            gemini_guard.reset_cooldown()
            st.info("Pause cleared. If your quota is still used up, Gemini will pause itself again after the next failed request.")
        st.caption("Settings: ENABLE_GEMINI, GEMINI_CACHE_MINUTES, GEMINI_QUOTA_COOLDOWN_MINUTES, GEMINI_MAX_CALLS_PER_MINUTE, "
                   "GEMINI_MAX_CALLS_PER_DAY, GEMINI_MAX_RETRIES, GEMINI_TIMEOUT_SECONDS (see README).")


def voice_tab(location, profile, planner, client) -> None:
    ss = st.session_state
    ss.setdefault("voice_history", [])
    ss.setdefault("voice_chat_state", new_state())
    ss.setdefault("voice_last_event", "")
    ss.setdefault("voice_speak", None)
    ss.setdefault("voice_error", "")

    st.write("Ask about today's weather or a trip, for example *“Today I need to travel from Bhopal to Indore. What should I wear?”*. "
             "Speak in English, Hindi or Hinglish, or type. You get the answer as text and spoken aloud.")
    gemini_ready = bool(config.gemini_api_key())
    st.caption(_gemini_line(gemini_ready))
    if getattr(location, "usable", False):
        st.caption(f"Starting place: **{location.label}** (change it in the sidebar, or say “I am in Bhopal”).")
    else:
        st.info("I don't know your starting city yet. Type it in the sidebar, or say/type something like “I am in Bhopal”.")

    c1, c2 = st.columns([3, 2])
    lang_label = c1.radio("Microphone language", list(LANG_CHOICES), horizontal=True, key="voice_lang_choice",
                          help="English/Hinglish uses the English (India) recogniser, which writes Hinglish in Roman letters. "
                               "Choose Hindi if you want to speak Hindi; the text then arrives in Devanagari.")
    speak_on = c2.checkbox("Speak answers aloud", value=True, key="voice_speak_on")
    use_gemini = st.checkbox("Let Gemini make the wording more natural (uses your Gemini quota)", value=False, key="voice_use_gemini",
                             disabled=not gemini_ready,
                             help="Off by default. The weather facts and outfit always come from the built-in rules; Gemini may only reword them.")

    sp = ss.voice_speak or {}
    latest = next((m["answer"] for m in reversed(ss.voice_history) if m["role"] == "assistant"), None)
    event = None
    if _voice_widget is not None:
        event = _voice_widget(recognition_lang=LANG_CHOICES[lang_label], auto_speak=bool(speak_on), speak_id=sp.get("id", ""),
                              speak_text=sp.get("text", ""), speak_lang=sp.get("lang", "en-IN"),
                              replay_text=(latest.spoken if latest else ""), replay_lang=(latest.speak_lang if latest else "en-IN"),
                              key="weatherfit_voice_widget", default=None)
    else:
        st.info("The microphone widget couldn't be loaded in this Streamlit version. You can still type your question below.")

    if isinstance(event, dict) and event.get("id") and event["id"] != ss.voice_last_event:
        ss.voice_last_event = event["id"]
        if event.get("type") == "transcript" and str(event.get("text", "")).strip():
            _process(str(event["text"]), "voice", location, profile, planner, client, use_gemini)
        elif event.get("type") == "error":
            ss.voice_error = redact(event.get("message", "Speech recognition error."))
    if ss.voice_error:
        st.warning(ss.voice_error)

    with st.form("voice_text_form", clear_on_submit=True):
        typed = st.text_input("Or type your question", placeholder="e.g. Aaj Indore mein baarish hogi kya?")
        submitted = st.form_submit_button("Ask")
    queued = None
    cols = st.columns(len(EXAMPLES))
    for i, (col, ex) in enumerate(zip(cols, EXAMPLES)):
        if col.button(ex, key=f"voice_ex_{i}", use_container_width=True):
            queued = ex
    if submitted and typed.strip():
        _process(typed, "typed", location, profile, planner, client, use_gemini)
    elif queued:
        _process(queued, "typed", location, profile, planner, client, use_gemini)

    for m in ss.voice_history:
        _show_message(m)
    if ss.voice_history and st.button("Clear conversation", key="voice_clear"):
        ss.voice_history, ss.voice_chat_state, ss.voice_speak = [], new_state(), None
        st.rerun()

    with st.expander("ℹ️ Browser support and privacy"):
        st.markdown(
            "- **Microphone (speech-to-text):** works in Chrome and Edge (desktop and Android). Safari support is partial; Firefox has none. "
            "Chrome/Edge send the audio to their own online speech service, so it needs internet and is handled by the browser vendor, not by this app. "
            "The first time, the browser asks for microphone permission.\n"
            "- **Spoken answers (text-to-speech):** uses voices installed in your browser/operating system (free). Hindi needs a Hindi voice on your device "
            "(Windows: Settings → Time & language → Speech → Add voices). Without one, the answer is still shown as text.\n"
            "- Browsers only allow speech after you have clicked something on the page; use **Replay answer** if you hear nothing.\n"
            "- The microphone only works on `localhost` or HTTPS pages.\n"
            "- Nothing you say is sent to Gemini unless you tick the Gemini box or ask a non-weather question while Gemini is on.")
    _gemini_panel()
    st.caption(attribution_markdown(client))
