"""Streamlit panel for the live-camera (AR) try-on. Logic is in ``ar_tryon`` (testable); the camera runs in the browser component
``ar_tryon_component/``. Video never reaches the Streamlit server."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import streamlit as st
import streamlit.components.v1 as components

import ar_tryon as ar
from db import Database

try:  # declared once per process; if it can't be declared the 2-D preview still works
    _ar_widget = components.declare_component("weatherfit_ar_tryon", path=str(Path(__file__).resolve().parent / "ar_tryon_component"))
except Exception:  # pragma: no cover - depends on the Streamlit version
    _ar_widget = None


def ar_tryon_panel(db: Database, plan_state: dict | None, choose_garments: Callable[[Database], list]) -> None:
    st.write("See your chosen clothes on **you, live through your camera**. Pick items, press **Start camera**, and stand back so your head, torso and feet are in view.")
    st.caption("This is a **lightweight 2-D camera overlay**: garments are cut-out photos drawn over the video. It does not bend cloth, hide the "
               "clothes behind your arms or hair, or tell you whether something fits, so use it to judge colours and combinations.")
    if plan_state:
        cur = plan_state["plan"].origin_fc["current"]
        st.caption(f"Today in {cur['city']}: {cur['condition']}, feels like {cur['feelslike_c']:.0f} °C.")

    tracking = st.checkbox("Follow my body automatically (optional)", value=False, key="ar_tracking",
                           help="Uses Google MediaPipe Pose, running inside your browser. The first time, your browser downloads the tracker "
                                "(about 10 MB of code and model from cdn.jsdelivr.net and storage.googleapis.com). Your video is still not uploaded. "
                                "Off = line yourself up with the guide lines and use the sliders.")
    if tracking:
        st.caption("Body tracking will download the MediaPipe Pose library and model once when you press Start camera.")

    payload = ar.prepare(choose_garments(db))
    for note in payload.notes:
        st.caption("⚠️ " + note)

    if _ar_widget is None:
        st.info("The camera widget couldn't be loaded in this Streamlit version. The 2-D preview mode still works.")
    else:
        _ar_widget(key="weatherfit_ar_widget", default=None, **ar.component_args(payload, tracking))

    with st.expander("ℹ️ Camera, privacy and browser support"):
        st.markdown(
            "- **Your video stays in your browser.** It is drawn on a canvas in this page; it is not uploaded, recorded, sent to Gemini or stored. "
            "A snapshot is saved to your computer only when you click **Save snapshot**.\n"
            "- The camera turns off when you press Stop, close the page, or leave this view hidden for about 10 seconds.\n"
            "- Works in current Chrome, Edge, Firefox and Safari on `http://localhost` or an `https://` address. The first time, the browser asks for camera permission.\n"
            "- If you blocked the camera, click the lock/camera icon in the address bar, set Camera to **Allow**, and press Start camera again.\n"
            "- Garments appear only if their photo could be cut out from a plain background (the same rule as the 2-D preview).\n"
            "- Body tracking needs internet once (to download the tracker) and a reasonably recent computer; if it can't load, the manual guide is used.")
