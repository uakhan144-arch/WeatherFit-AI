"""Streamlit screen for the virtual try-on and avatar. All logic lives in ``tryon_engine`` (importable and testable)."""
from __future__ import annotations

import io
import os

import streamlit as st
from PIL import Image

import taxonomy as tx
import tryon_engine as engine
import wardrobe as W
from db import Database

SLOT_LABELS = {"top": "Top", "outer": "Jacket / coat", "bottom": "Bottom", "onepiece": "Dress / jumpsuit", "footwear": "Footwear"}
UPLOAD_CATEGORIES = sorted(c for c, (slot, _w, _f) in tx.CATEGORIES.items() if slot in SLOT_LABELS)


@st.cache_data(show_spinner=False, max_entries=16)
def _avatar(skin: str, hair: str, build: str):
    return engine.make_avatar(skin, hair, build)


@st.cache_data(show_spinner=False, max_entries=64)
def _garment(slot: str, category: str, data: bytes) -> engine.Garment:
    return engine.make_garment(slot, category, data)


def _prefill_from_todays_outfit() -> None:
    """Button callback: copy the wardrobe pieces of the first suggested outfit into the pickers."""
    result = st.session_state.get("rec_result")
    recs = getattr(result, "wardrobe", None) or []
    for slot in SLOT_LABELS:
        st.session_state[f"tryon_pick_{slot}"] = None
    if recs:
        for piece in recs[0].pieces:
            if piece.owned and piece.item_id and piece.role in SLOT_LABELS:
                st.session_state[f"tryon_pick_{piece.role}"] = piece.item_id


def _choose_person(db: Database):
    """Step 1: returns (PIL image, Anchors) or None when something is wrong (message already shown)."""
    source = st.radio("Dress", ["Illustrated avatar", "My photo"], horizontal=True, key="tryon_source")
    if source == "Illustrated avatar":
        saved = engine.load_avatar_profile(db)
        c1, c2, c3 = st.columns(3)
        skin = c1.selectbox("Skin tone", list(engine.SKIN_TONES), index=list(engine.SKIN_TONES).index(saved["skin"]), key="tryon_skin")
        hair = c2.selectbox("Hair colour", list(engine.HAIR_COLOURS), index=list(engine.HAIR_COLOURS).index(saved["hair"]), key="tryon_hair")
        build = c3.selectbox("Build", list(engine.BUILDS), index=list(engine.BUILDS).index(saved["build"]), key="tryon_build")
        if st.button("Save this avatar", key="tryon_save_avatar"):
            engine.save_avatar_profile(db, {"skin": skin, "hair": hair, "build": build})
            st.success("Avatar saved on this computer.")
        return _avatar(skin, hair, build)

    st.caption("Use a clear, front-facing photo with your arms relaxed. It stays on this computer (EXIF/GPS data is removed) and "
               "is only sent anywhere if you set up your own try-on model that does so.")
    upload = st.file_uploader("Your photo", type=["jpg", "jpeg", "png", "webp"], key="tryon_photo")
    data = upload.getvalue() if upload else engine.load_person_photo()
    if not data:
        st.info("Upload a photo to dress, or switch to the illustrated avatar.")
        return None
    try:
        clean, _ = W.validate_and_normalise_image(data)
    except W.InvalidImage as exc:
        st.error(str(exc))
        return None
    photo = Image.open(io.BytesIO(clean)).convert("RGB")
    a, b = st.columns(2)
    if upload and a.button("Save as my avatar photo", key="tryon_save_photo"):
        try:
            engine.save_person_photo(data)
            st.success("Photo saved on this computer.")
        except engine.TryOnError as exc:
            st.error(str(exc))
    if not upload:
        a.caption("Using your saved photo.")
    if engine.load_person_photo() is not None and b.button("Delete my saved photo", key="tryon_del_photo"):
        engine.delete_person_photo()
        st.info("Saved photo deleted.")
    preset = st.radio("What does the photo show?", engine.PHOTO_PRESETS, horizontal=True, key="tryon_preset")
    full = preset == engine.PHOTO_PRESETS[0]
    with st.expander("Match the body lines to your photo (turn on guides to see them)", expanded=True):
        neck = st.slider("Neck / collar line (% from top)", 5, 70, 20 if full else 32, key=f"tryon_neck_{preset}")
        shoulders = st.slider("Shoulder width (% of photo width)", 15, 90, 32 if full else 54, key=f"tryon_sh_{preset}")
        centre = st.slider("Body centre (% from left)", 20, 80, 50, key=f"tryon_cx_{preset}")
        ankle = st.slider("Ankle line (% from top)", 60, 99, 92, key="tryon_ankle") if full else None
    return photo, engine.photo_anchors(photo.width, photo.height, preset, neck, shoulders, centre, ankle)


def _choose_garments(db: Database) -> list[engine.Garment]:
    """Step 2: wardrobe pickers + optional one-off garment photo."""
    items = [i for i in W.list_items(db, "wardrobe") if i.get("image_path") and i["category"] in tx.CATEGORIES]
    by_slot = {slot: [i for i in items if tx.slot_of(i["category"]) == slot] for slot in SLOT_LABELS}
    if not items:
        st.info("Your wardrobe has no clothing photos yet. Add some in **My wardrobe** (photos on a plain background work best), or upload one garment below.")
    else:
        st.button("Use today's suggested wardrobe outfit", on_click=_prefill_from_todays_outfit, key="tryon_prefill",
                  help="Press 'Suggest an outfit from my wardrobe' in the Today's outfit tab first.")
    lookup = {i["id"]: i for i in items}
    cols = st.columns(len(SLOT_LABELS))
    picked: list[tuple[str, dict]] = []
    for col, (slot, label) in zip(cols, SLOT_LABELS.items()):
        ids = [None] + [i["id"] for i in by_slot[slot]]
        if st.session_state.get(f"tryon_pick_{slot}") not in ids:   # e.g. the item was deleted since last time
            st.session_state[f"tryon_pick_{slot}"] = None
        choice = col.selectbox(label, ids, key=f"tryon_pick_{slot}", disabled=len(ids) == 1,
                               format_func=lambda v: "— none —" if v not in lookup else f"{W.display_name(lookup[v])} ({lookup[v]['category']})")
        if choice in lookup:
            picked.append((slot, lookup[choice]))
    garments: list[engine.Garment] = []
    for slot, item in picked:
        data = W.read_image_bytes(item["image_path"])
        if not data:
            st.warning(f"The photo for {W.display_name(item)} is missing on disk, so it was skipped.")
            continue
        try:
            garments.append(_garment(slot, item["category"], data))
        except engine.TryOnError as exc:
            st.warning(f"{W.display_name(item)}: {exc}")
    with st.expander("Or try a garment photo that is not in your wardrobe (not saved)"):
        up = st.file_uploader("Garment photo (plain background works best)", type=["jpg", "jpeg", "png", "webp"], key="tryon_garment_upload")
        cat = st.selectbox("What is it?", UPLOAD_CATEGORIES, index=UPLOAD_CATEGORIES.index("shirt"), key="tryon_garment_cat")
        if up:
            try:
                garments.append(_garment(tx.slot_of(cat), cat, up.getvalue()))
            except engine.TryOnError as exc:
                st.warning(str(exc))
    return garments


def _drop_conflicts(garments: list[engine.Garment]) -> tuple[list[engine.Garment], list[str]]:
    """A dress/jumpsuit replaces separate top and bottom; only the last chosen garment per slot is kept."""
    notes: list[str] = []
    by_slot: dict[str, engine.Garment] = {g.slot: g for g in garments}
    if "onepiece" in by_slot and ("top" in by_slot or "bottom" in by_slot):
        by_slot.pop("top", None)
        by_slot.pop("bottom", None)
        notes.append("A dress/jumpsuit replaces the separate top and bottom, so those were left out.")
    return list(by_slot.values()), notes


def _flatten(img: Image.Image) -> Image.Image:
    bg = Image.new("RGB", img.size, (255, 255, 255))
    bg.paste(img, mask=img.getchannel("A") if img.mode == "RGBA" else None)
    return bg


def tryon_tab(db: Database, plan_state: dict | None = None) -> None:
    mode = st.radio("Try-on mode", ["2-D preview (avatar or photo)", "📹 Live camera (AR)"], horizontal=True, key="tryon_mode")
    if mode.startswith("📹"):
        from ui_ar_tryon import ar_tryon_panel    # imported here so the 2-D preview never depends on the camera code
        ar_tryon_panel(db, plan_state, _choose_garments)
        return
    st.write("Dress your avatar (or your own photo) in clothes from your wardrobe and see how colours and layers go together.")
    st.caption("This is an **approximate 2-D fit preview**: garments are cut out of their photos and placed on body lines. "
               "It does not simulate fabric or your exact pose, so treat it as a styling aid, not a size or fit guarantee.")
    if plan_state:
        cur = plan_state["plan"].origin_fc["current"]
        st.caption(f"Today in {cur['city']}: {cur['condition']}, feels like {cur['feelslike_c']:.0f} °C.")

    st.subheader("1 · Who to dress")
    person = _choose_person(db)
    if person is None:
        return
    base_img, anchors = person

    st.subheader("2 · Pick clothes")
    garments, notes = _drop_conflicts(_choose_garments(db))

    st.subheader("3 · Fit and result")
    with st.expander("Fine-tune the fit"):
        dx = st.slider("Move sideways (%)", -10, 10, 0, key="tryon_dx")
        dy = st.slider("Move up / down (%)", -10, 10, 0, key="tryon_dy")
        size = st.slider("Garment size (%)", 60, 140, 100, key="tryon_size")
        guides = st.checkbox("Show body guide lines", key="tryon_guides")
    spec = os.getenv("TRYON_ADAPTER", "").strip()
    use_model = False
    if spec:
        use_model = st.radio("Try-on engine", ["Built-in preview", f"My trained model ({spec})"], horizontal=True, key="tryon_engine") != "Built-in preview"

    if not garments:
        st.image(engine.render_tryon(base_img, anchors, [], guides=guides)[0], width=320, caption="Pick at least one item above.")
        return
    if use_model:
        try:
            func = engine.load_adapter(spec)
            result = base_img
            for g in garments:
                result = engine.run_adapter(func, result, _flatten(g.image), g.category)
            st.image(result, width=360, caption="Result from your trained try-on model.")
            buf = io.BytesIO()
            result.save(buf, "PNG")
            st.download_button("Download result", buf.getvalue(), "tryon.png", "image/png", key="tryon_dl_model")
            return
        except engine.TryOnError as exc:
            st.error(f"{exc} Showing the built-in preview instead.")
    result, render_notes = engine.render_tryon(base_img, anchors, garments, dx, dy, size / 100, guides)
    st.image(result, width=360, caption="Approximate 2-D preview - not a fabric simulation.")
    for msg in notes + render_notes:
        st.caption("⚠️ " + msg)
    buf = io.BytesIO()
    result.save(buf, "PNG")
    st.download_button("Download preview", buf.getvalue(), "weatherfit_tryon.png", "image/png", key="tryon_dl")
