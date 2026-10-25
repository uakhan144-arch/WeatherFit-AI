"""Streamlit screens for the wardrobe, wardrobe-based outfits, outfit history and the AI stylist chat.

All logic lives in importable modules (wardrobe, outfit_engine, outfit_history, outfit_visuals, stylist_agent); this file only draws.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any

import streamlit as st

import dataset_import
import outfit_engine as engine
import outfit_history as history
import taxonomy as tx
import wardrobe as W
from gemini_guard import clear_cache as clear_gemini_cache
from ai_features import (fashionclip_category, fashionclip_embed_images, fashionclip_embed_text, gemini_label_item, generate_outfit_image)
from chat_assistant import new_state
from db import Database, init_db
from outfit_models import INSPIRATION_NOTE, Recommendation
from outfit_visuals import generate_look_image
from security import redact
from stylist_agent import AgentContext, respond

GENDER_CHOICES = ["Any", "Men", "Women"]
COMFORT_CHOICES = ["Comfort first", "Balanced", "Polished first"]
QUICK_STYLIST_QUESTIONS = ["Aaj kya pehnu?", "Kal maine kya pehna tha? Aaj kuch different suggest karo.", "Mere wardrobe mein blue shirt ke saath kya match hoga?",
                           "Mere paas suitable clothes nahi hain; ek naya look dikhao."]


@st.cache_resource
def get_db() -> Database:
    return init_db()


def _img(rel: str | None) -> bytes | None:
    return W.read_image_bytes(rel)


def show_pieces(rec: Recommendation) -> None:
    """Item-by-item cards with their labels and an explicit owned / not-owned tag."""
    pieces = rec.all_pieces
    cols = st.columns(min(4, max(1, len(pieces))))
    for i, pc in enumerate(pieces):
        with cols[i % len(cols)]:
            data = _img(pc.image_path) or _img(pc.reference_path)
            if data:
                st.image(data, use_container_width=True)
            else:
                st.markdown(f"<div style='height:80px;border-radius:8px;border:1px solid #ccc;background:{_swatch(pc.colour)}'></div>", unsafe_allow_html=True)
            st.caption(f"**{pc.label}** · {pc.role}\n\n{'✅ in your wardrobe' if pc.owned else '💡 suggestion - not in your wardrobe'}")


def _swatch(colour: str) -> str:
    r, g, b = W._REFERENCE_RGB.get(colour, (200, 200, 200))
    return f"rgb({r},{g},{b})"


def outfit_actions(db: Database, outfit_id: int | None, key: str) -> None:
    if not outfit_id:
        return
    o = history.get_outfit(db, outfit_id)
    if not o:
        return
    c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
    if c1.button("✅ Worn", key=f"worn_{key}", disabled=o["status"] == "worn"):
        history.set_status(db, outfit_id, "worn")
        st.rerun()
    if c2.button("⏭️ Skipped", key=f"skip_{key}", disabled=o["status"] == "skipped"):
        history.set_status(db, outfit_id, "skipped")
        st.rerun()
    fav = bool(o["favourite"])
    if c3.button("💔 Unfavourite" if fav else "⭐ Favourite", key=f"fav_{key}"):
        history.set_favourite(db, outfit_id, not fav)
        st.rerun()
    c4.caption(f"Status: **{o['status']}**{' · ⭐ favourite' if fav else ''}")


def show_recommendation(db: Database, rec: Recommendation, outfit_id: int | None, key: str, needs=None, profile=None, gemini_key: str = "") -> None:
    st.markdown(f"#### {'🧥' if rec.kind == 'wardrobe' else '💡'} {rec.label}")
    if rec.kind == "inspiration":
        st.info(INSPIRATION_NOTE)
    show_pieces(rec)
    for w in rec.why:
        st.write("• " + w)
    for n in rec.reuse_notes:
        st.caption("♻️ " + n)
    for m in rec.missing:
        st.warning(m)
    for w in rec.warnings:
        st.caption("⚠️ " + w)
    outfit_actions(db, outfit_id, key)
    with st.expander("Preview the complete look"):
        if st.button("Show item collage / generate look", key=f"viz_{key}"):
            gen = (lambda prompt, refs: generate_outfit_image(prompt, refs)) if gemini_key else None
            with st.spinner("Preparing the preview..."):
                v = generate_look_image(rec, needs, profile or {}, gen)
            st.session_state[f"visual_{key}"] = v
        v = st.session_state.get(f"visual_{key}")
        if v:
            if v.used_ai:
                st.image(v.ai_image, caption=v.caption, use_container_width=True)
            st.image(v.collage, caption="Item-by-item collage" + ("" if rec.kind == "wardrobe" else " (colour ideas, not products)"), use_container_width=True)
            if not v.used_ai:
                st.caption(v.caption)
            for n in v.notes:
                st.warning(n)


# ------------------------------------------------------------------ wardrobe tab
def wardrobe_tab(db: Database, gemini_key: str) -> None:
    st.write("Your clothes live in a private local database. Photos stay on this computer (`data/wardrobe/`); "
             "they are sent to Gemini **only** if you press *Suggest labels with AI*.")
    files = st.file_uploader("Add clothing photos (JPG/PNG/WEBP, several at once)", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True, key="wardrobe_upload")
    if files:
        st.caption("Check each label - suggestions are only a starting point.")
    for n, f in enumerate(files or []):
        data = f.getvalue()
        sug_key = f"sug_{f.name}_{len(data)}"
        try:
            W.validate_and_normalise_image(data)
        except W.InvalidImage as exc:
            st.error(f"{f.name}: {exc}")
            continue
        with st.container(border=True):
            c1, c2 = st.columns([1, 3])
            c1.image(data, width=150)
            if sug_key not in st.session_state:
                st.session_state[sug_key] = W.suggest_labels(data)  # offline colour guess only; no data leaves the computer
            if gemini_key and c2.button("✨ Suggest labels with AI (sends this photo to Gemini)", key=f"ai_{sug_key}"):
                with st.spinner("Asking Gemini..."):
                    st.session_state[sug_key] = W.suggest_labels(data, vision=gemini_label_item)
            if c2.button("🔎 Guess category with FashionCLIP (runs locally, optional install)", key=f"fc_{sug_key}"):
                with st.spinner("Loading FashionCLIP (first run downloads the model)..."):
                    st.session_state[sug_key] = W.suggest_labels(data, classifier=fashionclip_category)
            sug = st.session_state[sug_key]
            for note in sug["notes"]:
                c2.caption("ℹ️ " + note)
            fld = sug["fields"]
            with c2.form(f"form_{sug_key}"):
                cats = sorted(tx.CATEGORIES)
                guess = tx.normalize_category(fld.get("category"))
                category = st.selectbox("Category", cats, index=cats.index(guess) if guess in cats else cats.index("shirt"), key=f"cat_{sug_key}")
                colour = st.text_input("Colour", fld.get("colour", ""), key=f"col_{sug_key}")
                colour2 = st.text_input("Secondary colour (optional)", fld.get("secondary_colour", ""), key=f"col2_{sug_key}")
                a, b, c = st.columns(3)
                pattern = a.selectbox("Pattern", tx.PATTERNS, index=tx.PATTERNS.index(tx.normalize_pattern(fld.get("pattern"))), key=f"pat_{sug_key}")
                fabric = b.selectbox("Fabric", tx.FABRICS, index=tx.FABRICS.index(tx.normalize_choice(fld.get("fabric"), tx.FABRICS, "other")), key=f"fab_{sug_key}")
                style = c.selectbox("Style", tx.STYLES, key=f"sty_{sug_key}")
                season = st.multiselect("Season", tx.SEASONS, default=[s for s in tx.normalize_multi(fld.get("season"), tx.SEASONS, "all").split(",") if s], key=f"sea_{sug_key}")
                occasion = st.multiselect("Occasions", tx.OCCASION_TAGS + ["any"], default=[o for o in tx.normalize_multi(fld.get("occasion"), tx.OCCASION_TAGS + ["any"], "casual").split(",") if o], key=f"occ_{sug_key}")
                d, e = st.columns(2)
                gender = d.selectbox("Gender presentation", tx.GENDERS, index=tx.GENDERS.index(tx.normalize_choice(fld.get("gender_presentation"), tx.GENDERS, "unisex")), key=f"gen_{sug_key}")
                comfort = e.slider("Comfort", 1, 5, 3, key=f"cmf_{sug_key}")
                name = st.text_input("Name (optional)", key=f"nm_{sug_key}")
                if st.form_submit_button("Save to my wardrobe"):
                    try:
                        W.add_item(db, {"name": name, "category": category, "colour": colour, "secondary_colour": colour2, "pattern": pattern, "fabric": fabric,
                                        "style": style, "season": season, "occasion": occasion, "gender_presentation": gender, "comfort": comfort},
                                   data, label_source="ai_corrected" if sug["source"] == "ai" else "manual")
                        st.success(f"Saved {name or colour + ' ' + category}. Remove it from the uploader to add the next one.")
                    except W.DuplicateItem:
                        st.warning("This photo is already in your wardrobe.")
                    except (W.InvalidItem, W.InvalidImage) as exc:
                        st.error(str(exc))

    items = W.list_items(db, "wardrobe")
    st.subheader(f"My wardrobe ({len(items)})")
    if not items:
        st.info("Your wardrobe is empty. Upload photos above - until then you'll only get New Outfit Inspiration.")
    cols = st.columns(4)
    for i, it in enumerate(items):
        with cols[i % 4].container(border=True):
            data = _img(it["image_path"])
            if data:
                st.image(data, use_container_width=True)
            st.markdown(f"**{W.display_name(it)}**")
            st.caption(f"{it['category']} · {it['colour'] or 'colour?'} · worn {it['usage_count']}× · last worn {it['last_worn_date'] or 'never'}")
            with st.expander("Edit / delete"):
                cat = st.selectbox("Category", sorted(tx.CATEGORIES), index=sorted(tx.CATEGORIES).index(it["category"]), key=f"ecat_{it['id']}")
                col = st.text_input("Colour", it["colour"], key=f"ecol_{it['id']}")
                if st.button("Save changes", key=f"esave_{it['id']}"):
                    W.update_item(db, it["id"], {"category": cat, "colour": col})
                    st.rerun()
                if st.button("🗑️ Delete this item and its photo", key=f"edel_{it['id']}"):
                    W.delete_item(db, it["id"])
                    st.rerun()
    if items:
        with st.expander("Danger zone"):
            if st.checkbox("I understand this permanently deletes all my wardrobe photos and labels", key="confirm_del_all") and st.button("Delete my whole wardrobe"):
                W.delete_all_wardrobe(db)
                st.rerun()

    if items:
        with st.expander("🔎 Visual search with FashionCLIP (pretrained model - not trained on your wardrobe)"):
            st.caption("Embeddings come from the pretrained `patrickjohncyh/fashion-clip` model. Nothing is trained or fine-tuned on your clothes.")
            if st.button("Index my wardrobe photos"):
                try:
                    with st.spinner("Computing embeddings..."):
                        st.success(f"Indexed {W.index_item_embeddings(db, fashionclip_embed_images, items)} new item(s).")
                except Exception as exc:
                    st.warning(f"FashionCLIP isn't available: {redact(exc)[:200]} See README (optional install).")
            q = st.text_input("Describe what you're looking for", placeholder="e.g. light blue linen shirt")
            if q and st.button("Search"):
                try:
                    hits = W.similar_items(db, fashionclip_embed_text(q), k=4, ownership="wardrobe")
                    st.write("No indexed items yet - press *Index my wardrobe photos* first." if not hits else "")
                    for hit, score in hits:
                        st.image(_img(hit["image_path"]), width=120, caption=f"{W.display_name(hit)} (similarity {score:.2f})")
                except Exception as exc:
                    st.warning(f"FashionCLIP isn't available: {redact(exc)[:200]}")

    with st.expander("📚 Reference dataset (external inspiration images - never counted as clothes you own)"):
        refs = W.list_items(db, "inspiration")
        st.write(f"{len(refs)} reference item(s) imported. Licences and how to import a larger permitted dataset: see `DATASETS.md`.")
        if st.button("Import the small synthetic sample dataset (CC0)"):
            st.success(dataset_import.import_sample(db).summary())
        if refs and st.button("Remove all reference images"):
            for s in {r["source"] for r in refs}:
                dataset_import.remove_dataset(db, s)
            st.rerun()


# ------------------------------------------------------------------ outfit tab
def run_recommendation(db: Database, plan_state: dict, profile: dict[str, Any], repeat_days: int, again: bool = False) -> None:
    """Pick outfits for the current forecast and remember them (so tomorrow avoids them). Results go to session_state."""
    plan = plan_state["plan"]
    needs = engine.needs_from_windows(plan.origin_w, plan.dest_w)
    prof = {**profile, "occasion": plan_state["occasion"]}
    st.session_state.setdefault("variant", 0)
    st.session_state["variant"] += 1 if again else 0
    res = engine.recommend(db, needs, prof, date.today(), repeat_days=repeat_days, variant=st.session_state["variant"])
    ids: dict[str, int | None] = {}
    try:  # store what we show as the main pick, so tomorrow avoids it
        if res.wardrobe:
            ids["w0"], _ = history.save_recommendation(db, res.wardrobe[0], date.today(), prof["occasion"], needs, "app")
        if res.inspiration:
            ids["insp"], _ = history.save_recommendation(db, res.inspiration, date.today(), prof["occasion"], needs, "app")
    except Exception as exc:
        st.warning(f"Couldn't save to outfit history: {type(exc).__name__}")
    st.session_state["rec_result"], st.session_state["rec_ids"], st.session_state["rec_needs"] = res, ids, needs
    st.session_state["main_opt"] = 0


def outfit_tab(db: Database, plan_state: dict | None, profile: dict[str, Any], repeat_days: int, gemini_key: str, show_controls: bool = True) -> None:
    """Full outfit list with Worn / Skipped / Favourite actions. ``show_controls=False`` when the page already has the suggest buttons."""
    if not plan_state:
        st.info("Press **Get started** first - outfits are chosen for the real forecast.")
        return
    plan = plan_state["plan"]
    needs = engine.needs_from_windows(plan.origin_w, plan.dest_w)
    prof = {**profile, "occasion": plan_state["occasion"]}
    if show_controls:
        c1, c2 = st.columns(2)
        go = c1.button("👕 Suggest an outfit from my wardrobe", type="primary")
        again = c2.button("🔄 Suggest another")
        if go or again:
            run_recommendation(db, plan_state, profile, repeat_days, again)
    res = st.session_state.get("rec_result")
    if not res:
        return
    ids = st.session_state.get("rec_ids", {})
    if res.yesterday:
        st.caption(f"Yesterday ({res.yesterday['date']}): {', '.join(res.yesterday['items'])} ({res.yesterday['status']}). That exact combination won't be suggested today.")
    for m in res.messages:
        (st.warning if res.status in ("limited", "insufficient") else st.info)(m)
    for i, rec in enumerate(res.wardrobe):
        with st.container(border=True):
            st.caption(f"Option {i + 1}")
            show_recommendation(db, rec, ids.get(f"w{i}"), f"w{i}", needs, prof, gemini_key)
            if i > 0 and st.button("I'll wear this one", key=f"wear_opt_{i}"):
                oid, _ = history.save_recommendation(db, rec, date.today(), prof["occasion"], needs, "app")
                history.set_status(db, oid, "worn")
                st.session_state["rec_ids"][f"w{i}"] = oid
                st.rerun()
    if res.inspiration:
        with st.container(border=True):
            show_recommendation(db, res.inspiration, ids.get("insp"), "insp", needs, prof, gemini_key)


def history_tab(db: Database) -> None:
    only_fav = st.checkbox("Favourites only")
    rows = history.list_history(db, 60, only_fav)
    if not rows:
        st.info("No outfits stored yet.")
    for o in rows:
        label = ", ".join(f"{i['colour']} {i['category']}".strip() for i in o["items"] if i["role"] != "accessory")
        st.write(f"**{o['outfit_date']}** · {'🧥 wardrobe' if o['kind'] == 'wardrobe' else '💡 inspiration'} · {o['status']}{' · ⭐' if o['favourite'] else ''} — {label}")
    if rows and st.checkbox("I understand this deletes all outfit history", key="confirm_clear_hist") and st.button("Clear outfit history"):
        history.clear_history(db)
        st.rerun()


# ------------------------------------------------------------------ stylist chat
def stylist_chat_tab(db: Database, location, profile, planner, client, llm, repeat_days: int, gemini_key: str) -> None:
    st.write("Ask in English, Hindi or Hinglish - e.g. *“Aaj Gwalior mein party ke liye kya pehnu?”*. Answers use live weather, your wardrobe database and your outfit history.")
    st.session_state.setdefault("chat_history", [])
    st.session_state.setdefault("chat_state", new_state())
    if not gemini_key:
        st.info("Live AI is off (no `GEMINI_API_KEY`, or `ENABLE_GEMINI=false`). The stylist still answers from your data - just without Gemini's wording.")
    cols = st.columns(2)
    for i, q in enumerate(QUICK_STYLIST_QUESTIONS):
        if cols[i % 2].button(q, key=f"sq_{i}"):
            st.session_state["queued_q"] = q
    for m in st.session_state.chat_history:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            if m.get("status_msg"):
                st.warning(m["status_msg"])
            for w in m.get("warnings", []):
                st.caption("⚠️ " + w)
    if st.session_state.chat_history and st.session_state.chat_history[-1].get("retryable") and st.button("🔁 Retry with AI"):
        clear_gemini_cache()   # a retry must really ask again instead of re-reading the cached (rejected) answer
        st.session_state["queued_q"] = st.session_state.chat_history[-2]["content"]
        st.session_state.chat_history = st.session_state.chat_history[:-2]
    question = st.chat_input("Ask your stylist...") or st.session_state.pop("queued_q", None)
    if not question:
        return
    hist = [{"role": m["role"], "content": m["content"]} for m in st.session_state.chat_history]
    st.session_state.chat_history.append({"role": "user", "content": question})
    ctx = AgentContext(db, profile, location, client, st.session_state.chat_state, planner=planner, llm=llm, history=hist, repeat_days=repeat_days)
    try:
        with st.spinner("Your stylist is checking the weather, wardrobe and history..."):
            r = respond(question, ctx)
        st.session_state.chat_history.append({"role": "assistant", "content": r.text, "warnings": r.warnings, "retryable": r.retryable,
                                              "status_msg": r.llm_message if r.llm_status not in ("live", "not_used") else ""})
        st.session_state["last_agent_reply"] = r
    except Exception as exc:  # never show a traceback or a key
        st.session_state.chat_history.append({"role": "assistant", "content": f"Sorry, something went wrong ({type(exc).__name__}: {redact(exc)[:120]}). Please try again.", "retryable": True})
    st.rerun()
