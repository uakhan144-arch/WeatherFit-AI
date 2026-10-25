import io

import pytest
from PIL import Image

import wardrobe as W
from conftest import png_bytes


def test_add_item_stores_metadata_and_private_image(db, data_dir):
    iid = W.add_item(db, {"category": "Shirt", "colour": "Dark Blue", "pattern": "plaid", "comfort": 9, "season": ["summer", "bogus"]}, png_bytes(), data_dir=data_dir)
    it = W.get_item(db, iid)
    assert (it["category"], it["colour"], it["pattern"], it["comfort"], it["season"]) == ("shirt", "navy", "checked", 5, "summer")
    assert it["ownership"] == "wardrobe" and it["usage_count"] == 0 and it["last_worn_date"] is None
    assert it["image_path"].startswith("wardrobe/") and (data_dir / it["image_path"]).is_file()
    assert W.read_image_bytes(it["image_path"], data_dir)


def test_missing_metadata_gets_safe_defaults(db):
    it = W.get_item(db, W.add_item(db, {"category": "jeans"}))
    assert (it["pattern"], it["season"], it["occasion"], it["gender_presentation"], it["comfort"], it["style"]) == ("solid", "all", "casual", "unisex", 3, "Casual")


def test_unknown_category_rejected(db):
    with pytest.raises(W.InvalidItem):
        W.add_item(db, {"category": "spaceship"})
    with pytest.raises(W.InvalidItem):
        W.add_item(db, {})


@pytest.mark.parametrize("data", [b"", b"not an image", b"GIF89a" + b"0" * 20, b"\x89PNG\r\n\x1a\n" + b"0" * 50])
def test_invalid_images_rejected_and_nothing_stored(db, data_dir, data):
    with pytest.raises(W.InvalidImage):
        W.add_item(db, {"category": "shirt"}, data, data_dir=data_dir)
    assert W.list_items(db, None) == []
    assert not list(data_dir.rglob("*.jpg"))


def test_oversized_upload_rejected(db, data_dir):
    with pytest.raises(W.InvalidImage):
        W.validate_and_normalise_image(b"0" * (W.MAX_UPLOAD_BYTES + 1))


def test_unsupported_format_rejected(db):
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, "BMP")
    with pytest.raises(W.InvalidImage):
        W.validate_and_normalise_image(buf.getvalue())


def test_exif_gps_is_stripped(db, data_dir):
    img = Image.new("RGB", (50, 50), "red")
    exif = Image.Exif()
    exif[0x010F] = "SecretCamera"
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    iid = W.add_item(db, {"category": "shirt"}, buf.getvalue(), data_dir=data_dir)
    stored = Image.open(data_dir / W.get_item(db, iid)["image_path"])
    assert not stored.getexif()


def test_same_photo_cannot_be_added_twice_and_leaves_no_orphan_file(db, data_dir):
    W.add_item(db, {"category": "shirt"}, png_bytes(), data_dir=data_dir)
    with pytest.raises(W.DuplicateItem):
        W.add_item(db, {"category": "shirt"}, png_bytes(), data_dir=data_dir)
    assert len(list((data_dir / "wardrobe").glob("*.jpg"))) == 1


def test_update_marks_ai_labels_as_corrected(db):
    iid = W.add_item(db, {"category": "shirt", "colour": "blue"}, label_source="ai")
    W.update_item(db, iid, {"colour": "light blue", "category": "t-shirt"})
    it = W.get_item(db, iid)
    assert (it["colour"], it["category"], it["label_source"]) == ("light blue", "t-shirt", "ai_corrected")


def test_delete_item_removes_file_and_row(db, data_dir):
    iid = W.add_item(db, {"category": "shirt"}, png_bytes(), data_dir=data_dir)
    path = data_dir / W.get_item(db, iid)["image_path"]
    assert W.delete_item(db, iid, data_dir) and not path.exists() and W.get_item(db, iid) is None
    assert W.delete_item(db, iid, data_dir) is False


def test_delete_all_wardrobe_keeps_inspiration(db, data_dir):
    W.add_item(db, {"category": "shirt"}, png_bytes(), data_dir=data_dir)
    W.add_item(db, {"category": "shirt"}, png_bytes((1, 2, 3)), ownership="inspiration", source="sample", source_ref="1", data_dir=data_dir)
    assert W.delete_all_wardrobe(db, data_dir) == 1
    assert len(W.list_items(db, "wardrobe")) == 0 and len(W.list_items(db, "inspiration")) == 1


def test_path_traversal_is_refused(data_dir):
    assert W.resolve_image_path("../../etc/passwd", data_dir) is None
    assert W.read_image_bytes("../secret.txt", data_dir) is None


def test_label_suggestions_use_injected_vision_and_never_raise(db):
    ok = W.suggest_labels(png_bytes(), vision=lambda b: {"category": "shirt", "colour": "blue", "pattern": "striped"})
    assert ok["source"] == "ai" and ok["fields"]["category"] == "shirt"

    def boom(_):
        raise RuntimeError("429 quota exceeded key=AIzaSyFAKEFAKEFAKEFAKEFAKEFAKE12")
    bad = W.suggest_labels(png_bytes((200, 30, 30)), vision=boom)
    assert bad["fields"]["colour"] == "red" and bad["source"] == "heuristic"
    assert any("unavailable" in n for n in bad["notes"]) and "AIza" not in " ".join(bad["notes"])


def test_embeddings_and_similarity_search(db, data_dir):
    a = W.add_item(db, {"category": "shirt", "colour": "blue"}, png_bytes((0, 0, 255)), data_dir=data_dir)
    b = W.add_item(db, {"category": "shirt", "colour": "red"}, png_bytes((255, 0, 0)), data_dir=data_dir)
    fake = lambda imgs: [[1.0, 0.0] if i == 0 else [0.0, 1.0] for i, _ in enumerate(imgs)]
    assert W.index_item_embeddings(db, fake, data_dir=data_dir) == 2
    assert W.index_item_embeddings(db, fake, data_dir=data_dir) == 0  # already indexed
    hits = W.similar_items(db, [0.9, 0.1], k=2)
    assert [h[0]["id"] for h in hits] == [a, b] and hits[0][1] > hits[1][1]
