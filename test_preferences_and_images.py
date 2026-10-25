import json
import tempfile
import unittest
from pathlib import Path

from outfit_images import keyword_scores, list_outfit_images, outfit_query, rank_images
from preferences import clean, delete_preferences, load_preferences, save_preferences


class PreferenceConsentTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "prefs.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_nothing_written_without_consent(self):
        self.assertFalse(save_preferences({"style": "Minimal"}, consent=False, path=self.path))
        self.assertFalse(self.path.exists())

    def test_saved_with_consent_and_secrets_or_unknown_keys_dropped(self):
        prefs = {"style": "Minimal", "budget": "Premium", "GEMINI_API_KEY": "x", "location": "Indore", "avoid": "wool"}
        self.assertTrue(save_preferences(prefs, consent=True, path=self.path))
        self.assertEqual(load_preferences(self.path), {"style": "Minimal", "budget": "Premium", "avoid": "wool"})
        raw = self.path.read_text()
        self.assertNotIn("x", json.loads(raw)["preferences"].values())
        self.assertNotIn("Indore", raw)

    def test_file_without_consent_flag_is_ignored(self):
        self.path.write_text(json.dumps({"preferences": {"style": "Sporty"}}))
        self.assertEqual(load_preferences(self.path), {})

    def test_corrupt_file_ignored_and_delete_works(self):
        self.path.write_text("{nope")
        self.assertEqual(load_preferences(self.path), {})
        self.assertTrue(delete_preferences(self.path))
        self.assertFalse(delete_preferences(self.path))

    def test_clean_truncates(self):
        self.assertEqual(len(clean({"colours": "a" * 500})["colours"]), 200)


class OutfitImageTests(unittest.TestCase):
    ITEMS = [("linen_shirt_chinos.png", b"1"), ("wool_coat_winter.png", b"2"), ("rain_jacket_boots.png", b"3")]

    def test_query_text(self):
        q = outfit_query("Cotton T-shirt", "Chinos", "Sneakers", "Casual", "College")
        self.assertEqual(q, "a person wearing cotton t-shirt, chinos, sneakers, casual style, for college")

    def test_fashionclip_scores_drive_ranking(self):
        ranked, note = rank_images("q", self.ITEMS, scorer=lambda q, imgs: [0.1, 0.9, 0.5])
        self.assertEqual([r.label for r in ranked][:2], ["wool_coat_winter.png", "rain_jacket_boots.png"])
        self.assertEqual({r.method for r in ranked}, {"fashionclip"})
        self.assertEqual(note, "")

    def test_falls_back_to_keywords_and_says_so(self):
        def broken(q, imgs):
            raise RuntimeError("FashionCLIP packages are not installed.")
        ranked, note = rank_images("water resistant rain jacket and boots", self.ITEMS, scorer=broken)
        self.assertEqual(ranked[0].label, "rain_jacket_boots.png")
        self.assertEqual(ranked[0].method, "keyword")
        self.assertIn("FashionCLIP unavailable", note)

    def test_empty_items(self):
        ranked, note = rank_images("q", [])
        self.assertEqual(ranked, [])
        self.assertIn("assets/outfits", note)

    def test_folder_listing_and_catalog_tags(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.png").write_bytes(b"1")
            (Path(d) / "notes.txt").write_text("x")
            self.assertEqual([n for n, _ in list_outfit_images(d)], ["a.png"])
            self.assertEqual(list_outfit_images(Path(d) / "missing"), [])
        self.assertEqual(keyword_scores("sneakers", ["x.png"], {"x.png": "white sneakers"}), [1.0])


if __name__ == "__main__":
    unittest.main()
