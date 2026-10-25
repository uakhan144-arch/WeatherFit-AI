import io
import json

import pytest
from PIL import Image

import dataset_import as D
import hinglish
import outfit_engine as E
import outfit_visuals as V
import wardrobe as W
from conftest import MILD, PROFILE, TODAY, png_bytes
from security import redact


# ---------------------------------------------------------------- Hinglish
@pytest.mark.parametrize("q,lang,intent", [
    ("Aaj Gwalior mein party ke liye kya pehnu?", "hinglish", "outfit"),
    ("Kal maine kya pehna tha? Aaj kuch different suggest karo.", "hinglish", "yesterday"),
    ("Mere wardrobe mein blue shirt ke saath kya match hoga?", "hinglish", "match_item"),
    ("Kanaria se Gwalior travel ke liye outfit aur accessories suggest karo.", "hinglish", "outfit"),
    ("Mere paas suitable clothes nahi hain; ek naya look dikhao.", "hinglish", "new_look"),
    ("What should I wear in Indore today?", "en", "outfit"),
    ("What did I wear yesterday?", "en", "yesterday"),
    ("What goes with my blue shirt?", "en", "match_item"),
    ("I don't have anything suitable, show me a new look", "en", "new_look"),
    ("Will I need an umbrella in Bhopal?", "en", "other"),
    ("आज ग्वालियर में पार्टी के लिए क्या पहनूं?", "hi", "outfit"),
])
def test_language_and_intent(q, lang, intent):
    p = hinglish.parse(q)
    assert (p.language, p.intent) == (lang, intent)


def test_hinglish_entities_are_extracted():
    p = hinglish.parse("Kanaria se Gwalior travel ke liye outfit aur accessories suggest karo.")
    assert (p.origin, p.dest) == ("Kanaria", "Gwalior") and "from Kanaria to Gwalior" in p.english
    p = hinglish.parse("Aaj Gwalior mein party ke liye kya pehnu?")
    assert p.city == "Gwalior" and "in Gwalior" in p.english and "party" in p.english
    p = hinglish.parse("Mere wardrobe mein blue shirt ke saath kya match hoga?")
    assert (p.item_colour, p.item_category) == ("blue", "shirt")
    assert hinglish.parse("आज ग्वालियर में पार्टी के लिए क्या पहनूं?").city == "Gwalior"


def test_kal_means_yesterday_or_tomorrow_by_tense():
    assert hinglish.parse("Kal maine kya pehna tha?").intent == "yesterday"
    p = hinglish.parse("Kal Delhi mein shaadi ke liye kya pehnu?")
    assert p.intent == "outfit" and p.day_offset == 1 and "tomorrow" in p.english and "wedding" in p.english


# ---------------------------------------------------------------- visuals
def test_collage_always_works_for_wardrobe_and_inspiration(db, seed, data_dir):
    for c in [("shirt", "blue"), ("jeans", "navy"), ("sneakers", "white")]:
        seed(*c, image=True)
    res = E.recommend(db, MILD, PROFILE, TODAY)
    for rec in (res.wardrobe[0], res.inspiration):
        img = Image.open(io.BytesIO(V.build_collage(rec, data_dir)))
        assert img.width > 300 and img.height > 200


def test_generation_fallback_when_gemini_unavailable(db, seed, data_dir):
    for c in [("shirt", "blue"), ("jeans", "navy"), ("sneakers", "white")]:
        seed(*c, image=True)
    rec = E.recommend(db, MILD, PROFILE, TODAY).wardrobe[0]
    none = V.generate_look_image(rec, MILD, PROFILE, None, data_dir)
    assert not none.used_ai and none.collage and "GEMINI_API_KEY" in none.notes[0]

    def quota(prompt, refs):
        raise RuntimeError("429 RESOURCE_EXHAUSTED key=AIzaSyFAKEFAKEFAKEFAKEFAKEFAKE12")
    q = V.generate_look_image(rec, MILD, PROFILE, quota, data_dir)
    assert not q.used_ai and "quota" in q.notes[0] and "AIza" not in q.notes[0] and q.collage

    bad = V.generate_look_image(rec, MILD, PROFILE, lambda p, r: b"not an image", data_dir)
    assert not bad.used_ai and bad.notes


def test_generation_passes_wardrobe_photos_as_references_and_labels_honestly(db, seed, data_dir):
    for c in [("shirt", "blue"), ("jeans", "navy"), ("sneakers", "white")]:
        seed(*c, image=True)
    rec = E.recommend(db, MILD, PROFILE, TODAY).wardrobe[0]
    seen = {}

    def gen(prompt, refs):
        seen.update(prompt=prompt, refs=refs)
        return png_bytes()
    v = V.generate_look_image(rec, MILD, PROFILE, gen, data_dir)
    assert v.used_ai and len(seen["refs"]) == 3 and "COMPLETE outfit" in seen["prompt"] and "footwear" in seen["prompt"]
    assert "references" in v.caption and "may not reproduce exact" in v.caption


def test_inspiration_generation_has_no_references_and_is_labelled_ai_inspiration(db):
    insp = E.build_inspiration(db, MILD, PROFILE, TODAY)
    seen = {}
    v = V.generate_look_image(insp, MILD, PROFILE, lambda p, r: seen.update(refs=r) or png_bytes(), None)
    assert seen["refs"] == [] and "AI-generated inspiration" in v.caption and "not an exact product" in v.caption


# ---------------------------------------------------------------- dataset import
def test_sample_dataset_imports_as_inspiration_and_is_idempotent(db, data_dir):
    first = D.import_sample(db, data_dir)
    assert first.imported == 24 and first.skipped_unmapped == 0
    again = D.import_sample(db, data_dir)
    assert (again.imported, again.duplicates) == (0, 24)
    refs = W.list_items(db, "inspiration")
    assert len(refs) == 24 and not W.list_items(db, "wardrobe")
    assert all(r["image_path"].startswith("external/sample/") and r["license"].startswith("CC0") for r in refs)
    assert not list((data_dir / "wardrobe").glob("*")) if (data_dir / "wardrobe").exists() else True


def test_dataset_items_never_enter_wardrobe_recommendations(db, data_dir):
    D.import_sample(db, data_dir)
    res = E.recommend(db, MILD, PROFILE, TODAY)
    assert res.status == "empty" and res.wardrobe == []
    insp = res.inspiration
    assert any(p.reference_path for p in insp.pieces) and all(not p.owned for p in insp.all_pieces)


def test_remove_dataset_leaves_wardrobe_alone(db, data_dir, seed):
    seed("shirt", "blue")
    D.import_sample(db, data_dir)
    assert D.remove_dataset(db, "sample", data_dir) == 24
    assert len(W.list_items(db, "wardrobe")) == 1 and not W.list_items(db, "inspiration")


def test_fashionpedia_mapping_crop_pattern_and_bad_records(db, data_dir, tmp_path):
    imgs = tmp_path / "imgs"
    imgs.mkdir()
    (imgs / "a.jpg").write_bytes(png_bytes((200, 30, 30), (200, 300)))
    (imgs / "corrupt.jpg").write_bytes(b"garbage")
    doc = {"images": [{"id": 1, "file_name": "a.jpg"}, {"id": 2, "file_name": "missing.jpg"}, {"id": 3, "file_name": "corrupt.jpg"}],
           "categories": [{"id": 1, "name": "shirt, blouse"}, {"id": 2, "name": "sleeve"}, {"id": 3, "name": "pants"}],
           "attributes": [{"id": 1, "name": "stripe"}],
           "annotations": [{"id": 10, "image_id": 1, "category_id": 1, "bbox": [10, 10, 150, 200], "attribute_ids": [1]},
                           {"id": 11, "image_id": 1, "category_id": 2, "bbox": [0, 0, 50, 50]},       # part -> unmapped
                           {"id": 12, "image_id": 2, "category_id": 3, "bbox": [0, 0, 50, 50]},       # missing file
                           {"id": 13, "image_id": 3, "category_id": 3, "bbox": [0, 0, 50, 50]}]}      # corrupt image
    (tmp_path / "ann.json").write_text(json.dumps(doc))
    rep = D.import_fashionpedia(db, tmp_path / "ann.json", imgs, accept_terms=True, data_dir=data_dir)
    assert (rep.imported, rep.skipped_unmapped, rep.skipped_missing_image, rep.skipped_invalid) == (1, 1, 1, 1)
    it = W.list_items(db, "inspiration")[0]
    assert (it["category"], it["colour"], it["pattern"], it["source"], it["ownership"]) == ("shirt", "red", "striped", "fashionpedia", "inspiration")
    assert "CC BY 4.0" in it["license"] and "Fashionpedia" in it["attribution"]


def test_real_datasets_require_explicit_terms_acceptance(db, tmp_path, monkeypatch):
    monkeypatch.setenv("WEATHERFIT_DB", str(tmp_path / "cli.db"))
    monkeypatch.setenv("WEATHERFIT_DATA_DIR", str(tmp_path / "cli-data"))
    with pytest.raises(PermissionError, match="Fashionpedia"):
        D.import_fashionpedia(db, tmp_path / "x.json", tmp_path, accept_terms=False)
    with pytest.raises(PermissionError, match="NON-COMMERCIAL"):
        D.import_deepfashion(db, tmp_path, tmp_path, agreement_signed=False)
    assert D.main(["fashionpedia", "--annotations", "x", "--images-dir", "y"]) == 2


def test_deepfashion_category_files_are_mapped(db, data_dir, tmp_path):
    anno, root = tmp_path / "Anno", tmp_path / "root"
    (root / "img").mkdir(parents=True), anno.mkdir()
    (anno / "list_category_cloth.txt").write_text("2\ncategory_name category_type\nBlazer 1\nJeans 2\n")
    (anno / "list_category_img.txt").write_text("2\nimage_name category_label\nimg/a.jpg 1\nimg/b.jpg 2\n")
    (root / "img" / "a.jpg").write_bytes(png_bytes((20, 20, 90)))
    (root / "img" / "b.jpg").write_bytes(png_bytes((20, 90, 20)))
    rep = D.import_deepfashion(db, anno, root, agreement_signed=True, data_dir=data_dir)
    assert rep.imported == 2 and {i["category"] for i in W.list_items(db, "inspiration")} == {"blazer", "jeans"}
    assert all("non-commercial" in i["license"].lower() for i in W.list_items(db, "inspiration"))


# ---------------------------------------------------------------- secrets
def test_redact_removes_keys(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-value-123")
    out = redact("failed super-secret-value-123 at https://x/y?key=abc123&z=1 and AIzaSyABCDEFGHIJKLMNOPQRSTUV")
    assert "super-secret" not in out and "abc123" not in out and "AIzaSy" not in out


def test_no_hardcoded_keys_in_source_and_env_is_ignored_by_git():
    import pathlib
    root = pathlib.Path(__file__).parent
    for f in root.glob("*.py"):
        if f.name.startswith(("test_", "conftest", "tests_support")):
            continue  # tests contain deliberately fake keys
        assert "AIzaSy" not in f.read_text(encoding="utf-8"), f.name
    ignore = (root / ".gitignore").read_text()
    for entry in (".env", "data/", "*.db"):
        assert entry in ignore
