/* Pure placement maths for the AR try-on (no DOM, no network). Loaded by index.html and unit-tested with Node.
 *
 * Coordinates are CANVAS PIXELS. Pose landmarks arrive normalised (0..1) in MediaPipe order; only these are used:
 * 0 nose, 11/12 shoulders, 23/24 hips, 25/26 knees, 27/28 ankles.
 * Garment lengths come from Python (ar_tryon.placement_config) so the table lives in one place.
 */
(function (root) {
  "use strict";
  var LM = { nose: 0, lSh: 11, rSh: 12, lHip: 23, rHip: 24, lKnee: 25, rKnee: 26, lAnk: 27, rAnk: 28 };
  var MIN_VIS = 0.5;

  function mid(a, b) { return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }; }
  /** Tilt of the line through two points, always measured left-to-right on screen (so mirroring doesn't flip it by 180 degrees). */
  function lineAngle(p, q) { var a = p.x <= q.x ? p : q, b = p.x <= q.x ? q : p; return Math.atan2(b.y - a.y, b.x - a.x); }
  function vis(p) { return p && typeof p.visibility === "number" ? p.visibility : 1; }
  function ok(p) { return !!p && vis(p) >= MIN_VIS && isFinite(p.x) && isFinite(p.y); }

  /** Body measurements in canvas pixels from tracked landmarks. Returns {ok:false, reason} if shoulders/hips are not visible. */
  function bodyFromLandmarks(lm, W, H, mirror) {
    if (!lm || lm.length < 29) { return { ok: false, reason: "No body detected. Step back so your head and shoulders are in view." }; }
    var px = function (p) { return { x: (mirror ? 1 - p.x : p.x) * W, y: p.y * H, visibility: vis(p) }; };
    var pts = {};
    Object.keys(LM).forEach(function (k) { pts[k] = px(lm[LM[k]]); });
    if (!ok(pts.lSh) || !ok(pts.rSh)) { return { ok: false, reason: "Shoulders not visible. Move back or raise the camera." }; }
    if (!ok(pts.lHip) || !ok(pts.rHip)) { return { ok: false, reason: "Hips not visible. Step back so your torso fits in the frame." }; }
    var sh = mid(pts.lSh, pts.rSh), hip = mid(pts.lHip, pts.rHip);
    var S = Math.hypot(pts.rSh.x - pts.lSh.x, pts.rSh.y - pts.lSh.y);
    var torso = hip.y - sh.y;
    if (S < 8 || torso < 8) { return { ok: false, reason: "Body too small or sideways. Face the camera and move a little closer." }; }
    var ankles = ok(pts.lAnk) && ok(pts.rAnk), knees = ok(pts.lKnee) && ok(pts.rKnee);
    return {
      ok: true, tracked: true, S: S, torso: torso, shoulder: sh, hip: hip,
      angle: lineAngle(pts.lSh, pts.rSh),
      hipAngle: lineAngle(pts.lHip, pts.rHip),
      kneeY: knees ? mid(pts.lKnee, pts.rKnee).y : null,
      ankle: ankles ? mid(pts.lAnk, pts.rAnk) : null
    };
  }

  /** Fixed "stand here" body used when tracking is off or unavailable. */
  function manualBody(W, H) {
    var cx = W / 2;
    return {
      ok: true, tracked: false, S: 0.28 * H, torso: 0.30 * H, shoulder: { x: cx, y: 0.24 * H }, hip: { x: cx, y: 0.54 * H },
      angle: 0, hipAngle: 0, kneeY: 0.72 * H, ankle: { x: cx, y: 0.90 * H }
    };
  }

  function smoothBody(prev, next, a) {
    if (!prev || !prev.tracked || !next.tracked) { return next; }
    var m = function (p, q) { return p + (q - p) * a; };
    var out = Object.assign({}, next);
    ["S", "torso", "angle", "hipAngle"].forEach(function (k) { out[k] = m(prev[k], next[k]); });
    out.shoulder = { x: m(prev.shoulder.x, next.shoulder.x), y: m(prev.shoulder.y, next.shoulder.y) };
    out.hip = { x: m(prev.hip.x, next.hip.x), y: m(prev.hip.y, next.hip.y) };
    out.kneeY = next.kneeY === null || prev.kneeY === null ? next.kneeY : m(prev.kneeY, next.kneeY);
    out.ankle = !next.ankle || !prev.ankle ? next.ankle : { x: m(prev.ankle.x, next.ankle.x), y: m(prev.ankle.y, next.ankle.y) };
    return out;
  }

  /**
   * Where to draw one garment. g = {slot, category}; aspect = image width/height; adj = {dx, dy (% of canvas), scale (1 = 100%)}.
   * Returns {x, y, w, h, angle, pivot:{x,y}, clipTop} (x,y = top-left of the box the image is fitted into) or {skip:true, reason}.
   */
  function placeGarment(body, g, cfg, aspect, W, H, adj) {
    adj = adj || {}; var scale = Math.max(0.3, adj.scale || 1);
    var S = body.S, box, anchorBottom = false, angle = 0, L = (cfg.lengths || {}), f = cfg.factors || {};
    var neckY = body.shoulder.y - (f.neck_above_shoulder || 0.18) * S;
    if (g.slot === "top" || g.slot === "outer" || g.slot === "onepiece") {
      var width = S * (g.slot === "outer" ? f.outer_width : f.top_width);
      var top = body.shoulder.y - (f.top_above_shoulder || 0.10) * S;
      var bottom;
      if (g.slot === "onepiece" && g.category === "jumpsuit" && body.ankle) { bottom = body.ankle.y; }
      else if (g.category === "dress" && body.kneeY !== null) { bottom = body.kneeY + (L.dress_below_knee || 0.15) * body.torso; }
      else { bottom = body.hip.y + (L[g.category] !== undefined ? L[g.category] : (L.default || 0.15)) * body.torso; }
      bottom = Math.min(bottom, H * 0.99);
      box = { x: body.shoulder.x - width / 2, y: top, w: width, h: bottom - top };
      angle = body.angle;
    } else if (g.slot === "bottom") {
      var waist = body.hip.y - (f.waist_above_hip || 0.10) * body.torso;
      var leg = body.ankle ? body.ankle.y : body.hip.y + (f.leg_to_torso || 1.6) * body.torso;
      var knee = body.kneeY !== null ? body.kneeY : body.hip.y + 0.62 * (leg - body.hip.y);
      var end = g.category === "shorts" ? body.hip.y + 0.55 * (knee - body.hip.y) : g.category === "skirt" ? knee + 0.05 * body.torso : leg;
      if (waist >= H * 0.97) { return { skip: true, reason: "Hips are outside the picture." }; }
      var bw = S * f.bottom_width;
      box = { x: body.hip.x - bw / 2, y: waist, w: bw, h: Math.min(end, H * 0.995) - waist };
      angle = body.hipAngle;
    } else if (g.slot === "footwear") {
      if (!body.ankle) { return { skip: true, reason: "Feet are not in view. Step back so your legs and feet fit." }; }
      var fw = S * f.shoe_width, fh = fw * (f.shoe_aspect || 0.45);
      var by = body.ankle.y + (f.shoe_below_ankle || 0.22) * S;
      if (by - fh * 0.5 > H) { return { skip: true, reason: "Feet are outside the picture." }; }
      box = { x: body.ankle.x - fw / 2, y: by - fh, w: fw, h: fh };
      anchorBottom = true;
    } else {
      return { skip: true, reason: "Unknown garment type." };
    }
    if (!(box.w > 4 && box.h > 4)) { return { skip: true, reason: "Garment would be too small to draw." }; }
    // fit the (cut-out) image inside the box without distortion, then apply the user's sliders
    var h = Math.min(box.h, box.w / aspect) * scale, w = h * aspect;
    var cx = box.x + box.w / 2;
    var x = cx - w / 2 + (adj.dx || 0) / 100 * W;
    var y = (anchorBottom ? box.y + box.h - h : box.y) + (adj.dy || 0) / 100 * H;
    var pivot = { x: cx + (adj.dx || 0) / 100 * W, y: anchorBottom ? y + h : y };
    // never draw above the collar line, so the face stays visible (tops, outer, one-pieces)
    var clipTop = g.slot === "footwear" ? null : neckY;
    if (g.slot === "bottom") { clipTop = null; }
    return { x: x, y: y, w: w, h: h, angle: g.slot === "footwear" ? 0 : angle, pivot: pivot, clipTop: clipTop };
  }

  var api = { LM: LM, MIN_VIS: MIN_VIS, bodyFromLandmarks: bodyFromLandmarks, manualBody: manualBody, smoothBody: smoothBody, placeGarment: placeGarment };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; } else { root.ARPlacement = api; }
})(typeof window !== "undefined" ? window : this);
