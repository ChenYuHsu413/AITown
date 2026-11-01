// Parametric buildings from town.json: rounded masses, gable / flat / ledge roofs with
// thickness and eaves, framed windows (+ flower boxes, shop windows), a recessed door with a
// canopy, awnings with a scalloped edge, chimneys. Each building is a few merged meshes:
// `ext` (the closed house) and `cut` (the doll-house cutaway: floor, low walls, furniture).
import * as THREE from 'three';
import {C, tint, rbox, rnd, Batch} from './clay.js';
import {writer, FURNITURE, PROPS} from './kit.js';

const DEG = Math.PI / 180;
const W = {grad: 0.08, warm: 0.04, faceVar: 0.03, grain: 0.02};

export function buildBuilding(B, ctx) {
  const {groundY, mats} = ctx;
  const g = new THREE.Group(); g.position.set(B.pos[0], groundY, B.pos[1]); g.rotation.y = (B.rot || 0) * DEG;
  const ext = new THREE.Group(), cut = new THREE.Group(); g.add(ext, cut); cut.visible = false;
  const glass = mats.glass.clone(), bmats = {...mats, glass};   // per building: its windows light up on their own
  const eb = new Batch(), put = writer(eb, bmats);
  const out = {id: B.id, B, group: g, ext, cut, glass, open: 0, smoke: null, top: 0};

  const parts = (B.parts || []).map((p, i) => {
    const bands = B.brick ? Array.from({length: Math.round(p.size[1] / 0.25)}, (_, k) => k % 2 ? 0.94 : 1.0) : null;
    put(rbox(p.size[0], p.size[1], p.size[2], 0.34), p.color, [p.at[0], p.at[1] + p.size[1] / 2, p.at[2]], undefined, 1,
      {grad: i ? 0.08 : 0.16, warm: i ? 0.05 : 0.12, faceVar: 0.035, grain: B.brick ? 0.05 : 0.02, bands});
    out.top = Math.max(out.top, p.at[1] + p.size[1]);
    return {...p, y0: p.at[1]};
  });
  const face = (p, f, x, y, depth = 0) => f === 'east'
    ? [p.at[0] + p.size[0] / 2 + depth, p.y0 + y, p.at[2] + x] : [p.at[0] + x, p.y0 + y, p.at[2] + p.size[2] / 2 + depth];
  const rotF = f => f === 'east' ? [0, Math.PI / 2, 0] : [0, 0, 0];
  const add = (geo, color, f, p, x, y, d, o = W) => put(geo, color, face(p, f, x, y, d), rotF(f), 1, o);

  for (const r of B.roofs || []) {
    const p = parts[r.part], top = p.y0 + p.size[1];
    if (r.kind === 'ledge') { put(rbox(p.size[0] + 0.4, 0.32, p.size[2] + 0.4, 0.14), r.color, [p.at[0], top + 0.1, p.at[2]], undefined, 1, W); continue; }
    if (r.kind === 'band') { put(rbox(p.size[0] + 0.24, 0.22, p.size[2] + 0.24, 0.1), r.color, [p.at[0], top, p.at[2]], undefined, 1, W); continue; }
    if (r.kind === 'flat') {   // parapet ring + a darker roof deck
      put(rbox(p.size[0] + 0.36, 0.5, p.size[2] + 0.36, 0.16), r.color, [p.at[0], top + 0.2, p.at[2]], undefined, 1, W);
      put(rbox(p.size[0] - 0.5, 0.1, p.size[2] - 0.5, 0.05), '#6e6a6a', [p.at[0], top + 0.42, p.at[2]], undefined, 1, W);
      out.top = Math.max(out.top, top + 0.5); continue;
    }
    // gable: a wall-coloured gable prism under two thick slabs with eaves, a ridge cap
    const D = p.size[2] / 2, H = r.height, sh = new THREE.Shape();
    sh.moveTo(-D + 0.05, 0); sh.lineTo(D - 0.05, 0); sh.lineTo(0, H - 0.05); sh.lineTo(-D + 0.05, 0);
    const len = p.size[0] - 0.3, gable = new THREE.ExtrudeGeometry(sh, {depth: len, bevelEnabled: true, bevelThickness: 0.12, bevelSize: 0.12, bevelSegments: 3, curveSegments: 4});
    gable.translate(0, 0, -len / 2); gable.rotateY(Math.PI / 2);
    put(gable, p.color, [p.at[0], top - 0.05, p.at[2]], undefined, 1, {grad: 0.05, warm: 0, faceVar: 0.03, grain: 0.02});
    const ov = r.overhang + 0.15, half = D + ov, slope = Math.hypot(half, H) + 0.05, ang = Math.atan2(H, half), th = 0.3;
    for (const sgn of [-1, 1]) put(rbox(p.size[0] + ov * 2, th, slope, 0.12, 3), r.color, [p.at[0], top + H / 2 - (ov / half) * H / 2 + th * 0.35, p.at[2] + sgn * half / 2], [sgn * ang, 0, 0], 1, {grad: -0.1, warm: 0.02, faceVar: 0.02, grain: 0.02});
    put(new THREE.CapsuleGeometry(0.17, p.size[0] + ov * 2 - 0.3, 4, 10), r.color, [p.at[0], top + H + 0.14, p.at[2]], [0, 0, Math.PI / 2], 1, {grad: 0, warm: 0, faceVar: 0, grain: 0.02});
    out.top = Math.max(out.top, top + H + 0.3);
    if (r.chimney != null) {
      const cx = p.at[0] + r.chimney, cz = p.at[2] - half * 0.35, ch = H * 0.62 + 1.1;
      put(rbox(0.7, ch, 0.7, 0.14), B.brick ? p.color : '#c9a48a', [cx, top + ch / 2 + 0.2, cz], undefined, 1, W);
      put(rbox(0.92, 0.2, 0.92, 0.08), '#6a5a52', [cx, top + ch + 0.3, cz], undefined, 1, W);
      if (r.smoke) out.smoke = new THREE.Vector3(cx, top + ch + 0.5, cz);
    }
  }
  for (const w of B.windows || []) {   // protruding frame bars + sill, dark glass recessed inside
    const p = parts[w.part], f = w.face, fr = 0.16, dp = 0.2, white = '#fff7ee';
    add(rbox(w.w + fr * 2, fr, dp, 0.06, 3), white, f, p, w.x, w.y + w.h / 2 + fr / 2, dp / 2);
    add(rbox(fr, w.h, dp, 0.06, 3), white, f, p, w.x - w.w / 2 - fr / 2, w.y, dp / 2);
    add(rbox(fr, w.h, dp, 0.06, 3), white, f, p, w.x + w.w / 2 + fr / 2, w.y, dp / 2);
    add(rbox(w.w + 0.5, 0.16, 0.42, 0.06, 3), white, f, p, w.x, w.y - w.h / 2 - 0.08, 0.2);
    put(rbox(w.w, w.h, 0.06, 0.04, 2), glass, face(p, f, w.x, w.y, 0.015), rotF(f));
    if (w.w > 1.4) add(rbox(0.08, w.h, 0.1, 0.03, 2), white, f, p, w.x, w.y, 0.06);
    if (w.shop) add(rbox(w.w, 0.08, 0.1, 0.03, 2), white, f, p, w.x, w.y + w.h * 0.18, 0.06);
    if (w.box) {   // a flower box under the sill
      add(rbox(w.w + 0.3, 0.26, 0.36, 0.08), '#a8744c', f, p, w.x, w.y - w.h / 2 - 0.32, 0.32);
      for (let i = 0; i < 6; i++) add(new THREE.SphereGeometry(0.1, 8, 6), ['#f27a8a', '#ffffff', '#f2c84a'][i % 3], f, p, w.x - w.w / 2 + 0.05 + i * (w.w + 0.1) / 5, w.y - w.h / 2 - 0.12, 0.34);
    }
  }
  if (B.door) {   // recessed doorway: deep frame, the door set back, a canopy on brackets, a step
    const D = B.door, p = parts[D.part], dp = 0.28, fr = 0.22;
    add(rbox(D.w + fr * 2, fr, dp, 0.08), '#e9dccb', D.face, p, D.x, D.h + fr / 2, dp / 2);
    add(rbox(fr, D.h, dp, 0.08), '#e9dccb', D.face, p, D.x - D.w / 2 - fr / 2, D.h / 2, dp / 2);
    add(rbox(fr, D.h, dp, 0.08), '#e9dccb', D.face, p, D.x + D.w / 2 + fr / 2, D.h / 2, dp / 2);
    add(rbox(D.w, D.h, 0.08, 0.06, 3), D.color, D.face, p, D.x, D.h / 2, 0.02);
    put(new THREE.SphereGeometry(0.07, 10, 8), mats.glow, face(p, D.face, D.x + D.w * 0.3, D.h * 0.48, 0.12));
    add(rbox(D.w + 1.0, 0.16, 0.9, 0.07), '#7a5a4c', D.face, p, D.x, D.h + 0.55, 0.45);
    for (const sx of [-1, 1]) add(rbox(0.1, 0.4, 0.5, 0.04, 2), '#7a5a4c', D.face, p, D.x + sx * (D.w / 2 + 0.3), D.h + 0.3, 0.25);
    add(rbox(D.w + 0.9, 0.2, 0.8, 0.08), '#e6dccb', D.face, p, D.x, 0.1, 0.4);
    if (D.plate) add(rbox(0.5, 0.32, 0.06, 0.03, 2), D.plate, D.face, p, D.x + D.w / 2 + 0.62, 1.55, 0.05, {grad: 0, warm: 0, faceVar: 0, grain: 0.01});
    const dp_ = face(p, D.face, D.x, 0, 1.3);
    out.doorLocal = new THREE.Vector3(dp_[0], 0, dp_[2]);
    // the "closed today" sign hanging on the door (toggled, not merged)
    out.closedSign = closedSign(); out.closedSign.position.set(...face(p, D.face, D.x, D.h * 0.62, 0.14)); out.closedSign.visible = false; ext.add(out.closedSign);
  }
  if (B.awning) {   // striped slats tilted toward the street, a scalloped edge
    const A = B.awning, p = parts[A.part], n = Math.max(5, Math.round(A.w / 0.7)), sw = A.w / n;
    const base = face(p, 'front', A.x, A.y, 0.05), aw = writer(eb, bmats, new THREE.Matrix4().compose(new THREE.Vector3(...base), new THREE.Quaternion().setFromEuler(new THREE.Euler(0.34, 0, 0)), new THREE.Vector3(1, 1, 1)));
    const S = {grad: 0, warm: 0, faceVar: 0, grain: 0.015};
    for (let i = 0; i < n; i++) {
      aw(rbox(sw * 0.99, 0.1, A.depth, 0.04, 2), A.colors[i % 2], [-A.w / 2 + sw * (i + 0.5), 0, A.depth / 2], undefined, 1, S);
      aw(new THREE.CylinderGeometry(sw / 2, sw / 2, 0.08, 16, 1, false, 0, Math.PI), A.colors[i % 2], [-A.w / 2 + sw * (i + 0.5), 0, A.depth], [Math.PI / 2, Math.PI / 2, 0], 1, S);
    }
  }
  if (B.sign) {
    const G = B.sign, p = parts[G.part];
    add(rbox(G.w, G.h, 0.16, 0.08), G.color, 'front', p, G.x, G.y, 0.1);
    put(rbox(G.w - 0.3, 0.12, 0.05, 0.04, 2), mats.glow, face(p, 'front', G.x, G.y, 0.19));
  }
  if (B.kind === 'market') buildMarket(B, put, out);
  if (B.kind === 'park') buildPark(B, put, out, ctx);
  eb.build(ext);

  // outdoor props that belong to this building (local to it)
  const pb = new Batch(), pp = writer(pb, bmats);
  (B.props || []).forEach(([kind, x, z, rot = 0], i) => { const fn = PROPS[kind]; if (fn) fn(pp.at(x, 0, z, rot), {greens: ctx.greens, flowers: ctx.flowers, seed: i + (B.pos[0] | 0)}); });
  if (B.kind === 'park') for (const [x, z, rot] of B.benches || []) PROPS.bench(pp.at(x, moundY(B, x, z), z, rot));
  pb.build(g);

  if (B.interior && parts[0]) buildCutaway(B, parts[0], cut, bmats, out);
  out.doorLocal = out.doorLocal || new THREE.Vector3(...(B.doorAt ? [B.doorAt[0], 0, B.doorAt[1]] : [0, 0, 3]));
  return out;
}

function closedSign() {
  const c = document.createElement('canvas'); c.width = 128; c.height = 64; const x = c.getContext('2d');
  x.fillStyle = '#5a3a32'; x.beginPath(); x.roundRect(2, 2, 124, 60, 12); x.fill();
  x.fillStyle = '#fff4e0'; x.font = "600 30px 'Noto Sans TC', sans-serif"; x.textAlign = 'center'; x.textBaseline = 'middle'; x.fillText('今日公休', 64, 34);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
  return new THREE.Mesh(new THREE.PlaneGeometry(0.9, 0.45), new THREE.MeshStandardMaterial({map: t, roughness: 0.8}));
}

// The doll-house: floor, low walls (lower at the front, a gap at the door), the furniture.
function buildCutaway(B, p, cut, mats, out) {
  const I = B.interior, w = p.size[0], d = p.size[2], cb = new Batch(), put = writer(cb, mats);
  const wall = I.wall || p.color, t = 0.24, hb = 1.15, hf = 0.55;
  put(rbox(w - 0.1, 0.14, d - 0.1, 0.06), I.floor || '#d8b892', [p.at[0], 0.07, p.at[2]], undefined, 1, {grad: 0, warm: 0, faceVar: 0, grain: 0.04});
  put(rbox(w, hb, t, 0.1), wall, [p.at[0], hb / 2, p.at[2] - d / 2 + t / 2], undefined, 1, W);
  for (const sx of [-1, 1]) put(rbox(t, hb, d, 0.1), wall, [p.at[0] + sx * (w / 2 - t / 2), hb / 2, p.at[2]], undefined, 1, W);
  const dx = B.door ? B.door.x : 0, dw = (B.door ? B.door.w : 1.2) + 0.2, L = dx - dw / 2 - (-w / 2), R = w / 2 - (dx + dw / 2);
  if (L > 0.3) put(rbox(L, hf, t, 0.1), wall, [p.at[0] - w / 2 + L / 2, hf / 2, p.at[2] + d / 2 - t / 2], undefined, 1, W);
  if (R > 0.3) put(rbox(R, hf, t, 0.1), wall, [p.at[0] + w / 2 - R / 2, hf / 2, p.at[2] + d / 2 - t / 2], undefined, 1, W);
  for (const [kind, x, z, rot = 0, color] of I.furniture || []) { const fn = FURNITURE[kind]; if (fn) fn(put.at(p.at[0] + x, 0.14, p.at[2] + z, rot), color); }
  cb.build(cut);
  out.anchors = {};
  for (const k of ['beds', 'seats', 'work', 'stand']) out.anchors[k] = (I[k] || []).map(([x, z, f = 0]) => ({x: p.at[0] + x, z: p.at[2] + z, face: f}));
  out.floorY = 0.14;
}

/* ---------------------------------------------------------------- the market: open sheds */
function buildMarket(B, put, out) {
  for (const s of B.stalls) {
    const [x, z] = s.at, w = s.w, d = s.d, S = {grad: 0, warm: 0, faceVar: 0, grain: 0.015};
    for (const [px, pz] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) put(rbox(0.14, 2.5, 0.14, 0.05, 2), '#8a6a52', [x + px * (w / 2 - 0.1), 1.25, z + pz * (d / 2 - 0.1)]);
    put(rbox(w - 0.2, 0.85, d * 0.45, 0.08), '#b98a62', [x, 0.42, z + d * 0.18]);   // the counter
    put(rbox(w - 0.1, 0.08, d * 0.5, 0.03, 2), '#e9dccb', [x, 0.88, z + d * 0.18], undefined, 1, S);
    s.goods.forEach((c, i) => { for (let k = 0; k < 3; k++) put(new THREE.SphereGeometry(0.14, 10, 8), c, [x - w / 2 + 0.5 + i * (w - 1) / 2 + (k - 1) * 0.2, 1.02, z + d * 0.18 + (k % 2) * 0.16], undefined, 1, S); });
    // a striped canopy: two pitched halves of slats + scalloped front edge
    const n = Math.max(4, Math.round(w / 0.6)), sw = w / n;
    for (const sg of [-1, 1]) for (let i = 0; i < n; i++)
      put(rbox(sw * 0.99, 0.08, d / 2 + 0.35, 0.03, 2), s.colors[i % 2], [x - w / 2 + sw * (i + 0.5), 2.72 - 0.0, z + sg * (d / 4 + 0.1)], [sg * 0.32, 0, 0], 1, S);
    for (let i = 0; i < n; i++) put(new THREE.CylinderGeometry(sw / 2, sw / 2, 0.07, 14, 1, false, 0, Math.PI), s.colors[i % 2], [x - w / 2 + sw * (i + 0.5), 2.5, z + d / 2 + 0.38], [Math.PI / 2 + 0.32, Math.PI / 2, 0], 1, S);
    put(new THREE.CapsuleGeometry(0.08, w, 3, 8), '#8a6a52', [x, 2.9, z], [0, 0, Math.PI / 2]);
  }
  out.top = 3.2;
}

/* ---------------------------------------------------------------- the park: a grassy slope + the light installation */
export function moundY(B, x, z) {
  const M = B.mound; if (!M) return 0;
  const r = Math.hypot(x - M.at[0], z - M.at[1]) / M.r; return r >= 1 ? 0 : M.h * (1 - r * r) * (1 - r * r);
}
function buildPark(B, put, out, ctx) {
  const M = B.mound;
  if (M) {   // a soft dome of grass: a sphere cap squashed flat
    const g = new THREE.SphereGeometry(M.r, 48, 16, 0, Math.PI * 2, 0, Math.PI / 2); g.scale(1, M.h / M.r, 1);
    put(g, M.color, [M.at[0], -0.02, M.at[1]], undefined, 1, {grad: 0.12, warm: 0, faceVar: 0, grain: 0.05});
  }
  const L = B.installation, y0 = moundY(B, L.at[0], L.at[1]);
  // posts and a central sculpture (stacked rounded rings), built into the merged park mesh
  const posts = L.posts.map(([x, z]) => new THREE.Vector3(L.at[0] + x, moundY(B, L.at[0] + x, L.at[1] + z), L.at[1] + z));
  for (const p of posts) {
    put(new THREE.CylinderGeometry(0.12, 0.16, L.postH, 10), '#4a4550', [p.x, p.y + L.postH / 2, p.z]);
    put(new THREE.SphereGeometry(0.2, 12, 10), '#4a4550', [p.x, p.y + L.postH, p.z]);
  }
  for (let i = 0; i < 3; i++) put(new THREE.TorusGeometry(1.0 - i * 0.24, 0.12, 10, 32), L.core, [L.at[0], y0 + 0.5 + i * 0.75, L.at[1]], [Math.PI / 2 + (i - 1) * 0.35, 0, i * 0.6], 1, {grad: 0, warm: 0, faceVar: 0, grain: 0.02});
  put(new THREE.CylinderGeometry(1.2, 1.4, 0.3, 24), '#d8c9ae', [L.at[0], y0 + 0.15, L.at[1]]);
  // the light strings: bulbs along sagging curves between posts (one instanced mesh; lit with progress)
  const bulbs = [];
  for (const [a, b] of L.strings) {
    const A = posts[a].clone().setY(posts[a].y + L.postH - 0.1), Bp = posts[b].clone().setY(posts[b].y + L.postH - 0.1);
    for (let k = 0; k < L.bulbsPer; k++) { const t = (k + 0.5) / L.bulbsPer, p = A.clone().lerp(Bp, t); p.y -= Math.sin(t * Math.PI) * L.sag; bulbs.push(p); }
  }
  // the core sculpture's own bulbs
  for (let i = 0; i < 12; i++) { const a = i / 12 * Math.PI * 2; bulbs.push(new THREE.Vector3(L.at[0] + Math.cos(a) * 1.05, y0 + 1.25 + Math.sin(a * 2) * 0.35, L.at[1] + Math.sin(a) * 1.05)); }
  out.installation = {bulbs, colors: L.colors, center: new THREE.Vector3(L.at[0], y0 + 2.2, L.at[1])};
  // the cables themselves (thin, dark): merged
  for (const [a, b] of L.strings) {
    const A = posts[a].clone().setY(posts[a].y + L.postH - 0.1), Bp = posts[b].clone().setY(posts[b].y + L.postH - 0.1);
    const pts = []; for (let k = 0; k <= 12; k++) { const t = k / 12, p = A.clone().lerp(Bp, t); p.y -= Math.sin(t * Math.PI) * L.sag; pts.push(p); }
    put(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 16, 0.025, 4), '#2a2e36');
  }
  out.top = L.postH + 1;
}
