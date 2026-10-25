"""Live-camera (AR) try-on tests: Python payload, placement maths (run in Node when available), and static checks of the browser code.

No camera, browser, network or model is used. unittest-style so they run under both ``pytest`` and ``python -m unittest``.
"""
import base64
import io
import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

import ar_tryon as ar
import taxonomy as tx
import tryon_engine as T

COMPONENT = Path(__file__).resolve().parent / "ar_tryon_component"
HTML = (COMPONENT / "index.html").read_text(encoding="utf-8")
JS = (COMPONENT / "ar_placement.js").read_text(encoding="utf-8")
NODE = shutil.which("node") or shutil.which("nodejs")


def png(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def shirt_photo(colour=(40, 80, 170), bg=(250, 250, 250)) -> Image.Image:
    im = Image.new("RGB", (400, 450), bg)
    ImageDraw.Draw(im).polygon([(110, 40), (290, 40), (390, 130), (340, 190), (300, 150), (300, 420), (100, 420), (100, 150), (60, 190), (10, 130)], fill=colour)
    return im


def garment(slot="top", cat="shirt", img=None):
    return T.make_garment(slot, cat, png(img or shirt_photo()))


class PayloadTests(unittest.TestCase):
    def test_encode_is_small_valid_png_data_url(self):
        url = ar.encode_garment(garment())
        self.assertTrue(url.startswith("data:image/png;base64,"))
        img = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
        self.assertEqual(img.mode, "RGBA")
        self.assertLessEqual(max(img.size), ar.PAYLOAD_MAX_SIDE)
        self.assertLessEqual(len(url), ar.MAX_PAYLOAD_BYTES + 64)

    def test_noisy_photo_is_shrunk_below_the_byte_limit(self):
        noise = Image.frombytes("RGBA", (360, 360), os.urandom(360 * 360 * 4))
        g = T.Garment("top", "shirt", noise)
        self.assertLessEqual(len(ar.encode_garment(g)), ar.MAX_PAYLOAD_BYTES + 64)

    def test_uncut_garment_is_left_out_with_a_reason(self):
        busy = Image.effect_noise((400, 450), 80).convert("RGB")
        g = garment("top", "shirt", busy)
        self.assertFalse(g.cutout_ok)
        p = ar.prepare([g])
        self.assertEqual(p.garments, [])
        self.assertTrue(any("could not be cut out" in n for n in p.notes))

    def test_dress_replaces_top_and_bottom(self):
        p = ar.prepare([garment("top", "shirt"), garment("bottom", "chinos", shirt_photo((190, 170, 130))), garment("onepiece", "dress", shirt_photo((160, 30, 60)))])
        self.assertEqual([g["slot"] for g in p.garments], ["onepiece"])
        self.assertTrue(any("replaces" in n for n in p.notes))

    def test_draw_order_is_back_to_front_and_aspect_is_sent(self):
        p = ar.prepare([garment("outer", "jacket"), garment("footwear", "sneakers", shirt_photo((20, 20, 20))), garment("top", "shirt"),
                        garment("bottom", "jeans", shirt_photo((30, 40, 90)))])
        self.assertEqual([g["slot"] for g in p.garments], ["footwear", "bottom", "top", "outer"])
        self.assertTrue(all(g["aspect"] > 0 for g in p.garments))

    def test_component_args_are_json_and_tracking_is_explicit(self):
        args = ar.component_args(ar.prepare([garment()]), False)
        self.assertIs(args["tracking"], False)
        self.assertIs(ar.component_args(ar.prepare([]), True)["tracking"], True)
        json.dumps(args)
        self.assertLess(len(json.dumps(args)), 400_000)

    def test_every_taxonomy_garment_has_an_ar_length_rule(self):
        self.assertEqual(ar.missing_length_rules(), [])

    def test_pose_urls_are_pinned_https_and_env_cannot_inject(self):
        urls = ar.pose_urls()
        self.assertTrue(all(u.startswith("https://") for u in urls.values()))
        self.assertRegex(urls["module"], r"tasks-vision@\d+\.\d+\.\d+/")
        old = os.environ.get("AR_POSE_LIB_VERSION")
        try:
            for bad in ("latest", "1.0.0/../../evil", "https://evil.example/x"):
                os.environ["AR_POSE_LIB_VERSION"] = bad
                self.assertIn(f"@{ar.POSE_LIBRARY_VERSION}/", ar.pose_urls()["module"])
            os.environ["AR_POSE_LIB_VERSION"] = "0.10.22"
            self.assertIn("@0.10.22/", ar.pose_urls()["module"])
        finally:
            os.environ.pop("AR_POSE_LIB_VERSION", None) if old is None else os.environ.__setitem__("AR_POSE_LIB_VERSION", old)


class BrowserCodeStaticChecks(unittest.TestCase):
    def test_no_video_or_data_can_leave_the_page_from_our_code(self):
        for name, text in (("index.html", HTML), ("ar_placement.js", JS)):
            for banned in ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "setComponentValue", "innerHTML", "eval(", "document.write"):
                self.assertNotIn(banned, text, f"{banned} found in {name}")

    def test_camera_is_video_only_and_released(self):
        self.assertIn("getUserMedia", HTML)
        self.assertRegex(HTML, r"audio:\s*false")
        self.assertIn(".stop()", HTML)                       # tracks are stopped
        self.assertIn("pagehide", HTML)
        self.assertIn("IntersectionObserver", HTML)
        self.assertIn("visibilitychange", HTML)

    def test_only_the_trusted_tracker_url_can_be_imported(self):
        imports = re.findall(r"import\(([^)]*)\)", HTML)
        self.assertEqual(imports, ["args.pose.module"])
        self.assertIn("trustedPose", HTML)
        self.assertIn(r"cdn\.jsdelivr\.net", HTML)            # (escaped hostnames inside the allow-list regexes)
        self.assertIn(r"storage\.googleapis\.com", HTML)

    def test_placement_script_is_loaded_before_use_and_has_a_fallback_status(self):
        self.assertLess(HTML.index('src="ar_placement.js"'), HTML.index("window.ARPlacement"))
        self.assertIn("The AR script could not be loaded", HTML)

    def test_js_uses_only_constants_python_provides(self):
        cfg = ar.placement_config()
        for name in set(re.findall(r"\bf\.([a-z_]+)", JS)):
            self.assertIn(name, cfg["factors"], name)
        for name in set(re.findall(r"\bL\.([a-z_]+)", JS)):
            self.assertIn(name, cfg["lengths"], name)

    def test_ui_hooks_exist(self):
        for el in ("start", "stop", "snap", "mirror", "guides", "size", "dx", "dy", "reset", "status", "cv", "video"):
            self.assertIn(f'id="{el}"', HTML, el)


NODE_RUNNER = r"""
const P = require(process.argv[1]);
const cfg = JSON.parse(process.argv[2]);
const out = {};
const lm = (over) => { const a = []; for (let i = 0; i < 33; i++) a.push({x: .5, y: .5, visibility: .99}); Object.entries(over).forEach(([i, v]) => Object.assign(a[+i], v)); return a; };
const stand = (dx = 0, extra = {}) => lm(Object.assign({0:{x:.5+dx,y:.12},11:{x:.4+dx,y:.30},12:{x:.6+dx,y:.30},23:{x:.44+dx,y:.55},24:{x:.56+dx,y:.55},
  25:{x:.45+dx,y:.72},26:{x:.55+dx,y:.72},27:{x:.45+dx,y:.92},28:{x:.55+dx,y:.92}}, extra));
const W = 640, H = 480;
const b = P.bodyFromLandmarks(stand(), W, H, false);
out.body = {ok: b.ok, S: b.S, torso: b.torso, cx: b.shoulder.x, angle: b.angle};
const top = P.placeGarment(b, {slot:"top", category:"t-shirt"}, cfg, 1, W, H, {});
out.top = {cx: top.x + top.w / 2, y: top.y, bottom: top.y + top.h, clip: top.clipTop, angle: top.angle};
const shoes = P.placeGarment(b, {slot:"footwear", category:"sneakers"}, cfg, 2, W, H, {});
out.shoes = {bottom: shoes.y + shoes.h, cx: shoes.x + shoes.w / 2, w: shoes.w};
const hidden = P.bodyFromLandmarks(stand(0, {27:{visibility:.1},28:{visibility:.1}}), W, H, false);
out.noAnkles = {skip: P.placeGarment(hidden, {slot:"footwear", category:"boots"}, cfg, 1, W, H, {}), jeans: P.placeGarment(hidden, {slot:"bottom", category:"jeans"}, cfg, .5, W, H, {})};
out.noHips = P.bodyFromLandmarks(stand(0, {23:{visibility:.1}}), W, H, false);
out.noBody = P.bodyFromLandmarks(null, W, H, false);
const off = P.bodyFromLandmarks(stand(-.2), W, H, false), offM = P.bodyFromLandmarks(stand(-.2), W, H, true);
out.mirror = {plain: off.shoulder.x, mirrored: offM.shoulder.x};
const tilt = {11:{x:.4,y:.30},12:{x:.6,y:.32}};
out.tilt = {plain: P.bodyFromLandmarks(stand(0, tilt), W, H, false).angle, mirrored: P.bodyFromLandmarks(stand(0, tilt), W, H, true).angle};
const man = P.manualBody(W, H);
const base = P.placeGarment(man, {slot:"top", category:"shirt"}, cfg, 1, W, H, {});
const moved = P.placeGarment(man, {slot:"top", category:"shirt"}, cfg, 1, W, H, {dx: 5, dy: -4, scale: 1.2});
out.adjust = {dcx: (moved.x + moved.w / 2) - (base.x + base.w / 2), dy: moved.y - base.y, hRatio: moved.h / base.h};
out.dress = P.placeGarment(b, {slot:"onepiece", category:"dress"}, cfg, .5, W, H, {});
out.jump = P.placeGarment(b, {slot:"onepiece", category:"jumpsuit"}, cfg, .5, W, H, {});
out.unknown = P.placeGarment(b, {slot:"hat", category:"cap"}, cfg, 1, W, H, {});
out.short = P.placeGarment(b, {slot:"bottom", category:"shorts"}, cfg, .6, W, H, {});
out.skirt = P.placeGarment(b, {slot:"bottom", category:"skirt"}, cfg, .6, W, H, {});
const b2 = P.bodyFromLandmarks(stand(0.1), W, H, false);
const sm = P.smoothBody(b, b2, 0.5);
out.smooth = {cx: sm.shoulder.x, expect: (b.shoulder.x + b2.shoulder.x) / 2};
out.manual = {tracked: man.tracked, S: man.S};
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "Node.js not installed (placement maths is only checked when it is)")
class PlacementMathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        res = subprocess.run([NODE, "-e", NODE_RUNNER, str(COMPONENT / "ar_placement.js"), json.dumps(ar.placement_config())],
                             capture_output=True, text=True, timeout=30)
        assert res.returncode == 0, res.stderr
        cls.o = json.loads(res.stdout)

    def test_body_measured_from_landmarks(self):
        b = self.o["body"]
        self.assertTrue(b["ok"])
        self.assertAlmostEqual(b["S"], 128, places=3)          # 0.2 of 640
        self.assertAlmostEqual(b["torso"], 120, places=3)      # 0.25 of 480
        self.assertAlmostEqual(b["cx"], 320, places=3)
        self.assertAlmostEqual(b["angle"], 0, places=6)

    def test_top_is_centred_on_shoulders_ends_at_hip_and_never_covers_the_face(self):
        t = self.o["top"]
        self.assertAlmostEqual(t["cx"], 320, delta=0.01)
        self.assertAlmostEqual(t["y"], 144 - 0.10 * 128, delta=0.01)
        self.assertAlmostEqual(t["bottom"], 264, delta=0.01)    # t-shirt hem sits on the hip line
        self.assertAlmostEqual(t["clip"], 144 - 0.18 * 128, delta=0.01)
        self.assertGreater(t["clip"], 0.12 * 480 + 20)          # collar line is below the nose

    def test_shoes_sit_at_the_ankles(self):
        s = self.o["shoes"]
        self.assertAlmostEqual(s["bottom"], 0.92 * 480 + 0.22 * 128, delta=0.01)
        self.assertAlmostEqual(s["cx"], 320, delta=0.01)

    def test_hidden_feet_skip_shoes_but_trousers_still_draw(self):
        n = self.o["noAnkles"]
        self.assertTrue(n["skip"]["skip"])
        self.assertIn("Feet", n["skip"]["reason"])
        self.assertNotIn("skip", n["jeans"])

    def test_lost_body_gives_a_helpful_reason(self):
        self.assertFalse(self.o["noHips"]["ok"])
        self.assertIn("Hips", self.o["noHips"]["reason"])
        self.assertFalse(self.o["noBody"]["ok"])
        self.assertIn("No body", self.o["noBody"]["reason"])

    def test_mirror_flips_x_and_keeps_tilt_direction_correct(self):
        self.assertAlmostEqual(self.o["mirror"]["plain"], 0.3 * 640, delta=0.01)
        self.assertAlmostEqual(self.o["mirror"]["mirrored"], 0.7 * 640, delta=0.01)
        self.assertGreater(self.o["tilt"]["plain"], 0)
        self.assertLess(self.o["tilt"]["mirrored"], 0)
        self.assertLess(abs(self.o["tilt"]["mirrored"]), 0.2)      # small angle, not ~180 degrees

    def test_sliders_move_and_scale(self):
        a = self.o["adjust"]
        self.assertAlmostEqual(a["dcx"], 0.05 * 640, delta=0.01)    # centre moved 5 % of the width to the right
        self.assertAlmostEqual(a["dy"], -0.04 * 480, delta=0.01)
        self.assertAlmostEqual(a["hRatio"], 1.2, places=6)

    def test_garment_lengths_by_category(self):
        d, j, sh, sk = self.o["dress"], self.o["jump"], self.o["short"], self.o["skirt"]
        knee = 0.72 * 480
        self.assertGreater(d["y"] + d["h"], knee)               # dress reaches past the knee
        self.assertAlmostEqual(j["y"] + j["h"], 0.92 * 480, delta=0.5)   # jumpsuit reaches the ankles
        self.assertLess(sh["y"] + sh["h"], knee)                # shorts end above the knee
        self.assertGreater(sk["y"] + sk["h"], sh["y"] + sh["h"])

    def test_unknown_slot_is_skipped(self):
        self.assertTrue(self.o["unknown"]["skip"])

    def test_smoothing_blends_frames(self):
        self.assertAlmostEqual(self.o["smooth"]["cx"], self.o["smooth"]["expect"], places=6)

    def test_manual_mode_needs_no_tracking(self):
        self.assertFalse(self.o["manual"]["tracked"])
        self.assertAlmostEqual(self.o["manual"]["S"], 0.28 * 480)


if __name__ == "__main__":
    unittest.main()
