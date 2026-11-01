// The 3-D validation slice (Townscaper direction): an island with one café and a corner
// of the plaza, on gently moving water. Everything is generated from slice.json --
// stacked rounded boxes, gable roofs, recessed window slots, a porch awning -- with flat
// colours and vertex gradients only (no textures, no model files). One warm sun, a soft
// sky light and PCF soft shadows; the sun's angle and colour follow the sim clock.
import * as THREE from 'three';
import {RoundedBoxGeometry} from 'three/addons/geometries/RoundedBoxGeometry.js';
import {phaseOf} from '../names.js';

const C = h => new THREE.Color(h);
const lerpC = (a, b, k) => a.clone().lerp(b, Math.max(0, Math.min(1, k)));

// a rounded box whose colour brightens a little toward the top (the soft wall gradient)
function softBox(w, h, d, r, color, seg = 3) {
  const g = new RoundedBoxGeometry(w, h, d, seg, Math.min(r, w / 2 - 0.01, h / 2 - 0.01, d / 2 - 0.01));
  const pos = g.attributes.position, col = new Float32Array(pos.count * 3), base = C(color);
  for (let i = 0; i < pos.count; i++) {
    const k = 0.86 + 0.16 * ((pos.getY(i) + h / 2) / h);
    col[i * 3] = Math.min(1, base.r * k); col[i * 3 + 1] = Math.min(1, base.g * k); col[i * 3 + 2] = Math.min(1, base.b * k);
  }
  g.setAttribute('color', new THREE.BufferAttribute(col, 3));
  return g;
}
const mat = (o = {}) => new THREE.MeshStandardMaterial({roughness: 0.88, metalness: 0, ...o});
function mesh(g, m, {cast = true, receive = true} = {}) { const x = new THREE.Mesh(g, m); x.castShadow = cast; x.receiveShadow = receive; return x; }

export class World {
  constructor(el, spec, {onFrame} = {}) {
    this.el = el; this.S = spec; this.onFrame = onFrame;
    this.residents = new Map(); this.t = 0; this.alive = true;
    const r = this.renderer = new THREE.WebGLRenderer({antialias: true, alpha: true});
    r.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    r.outputColorSpace = THREE.SRGBColorSpace; r.toneMapping = THREE.ACESFilmicToneMapping; r.toneMappingExposure = 1.05;
    r.shadowMap.enabled = true; r.shadowMap.type = THREE.PCFShadowMap;   // soft via shadow.radius
    r.setClearColor(0x000000, 0);
    el.appendChild(r.domElement); r.domElement.classList.add('world-canvas');
    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.Fog(0x10151c, 60, 115);   // the water fades into the dark stage
    this.camera = new THREE.PerspectiveCamera(spec.camera.fov, 1, 0.5, 400);
    this.cam = {target: new THREE.Vector3(...spec.camera.target), dist: spec.camera.distance,
      az: THREE.MathUtils.degToRad(spec.camera.azimuth), el: THREE.MathUtils.degToRad(spec.camera.elevation)};
    this.buildLights(); this.buildIsland(); this.buildWater(); this.buildPlaza();
    for (const b of spec.buildings) this.buildBuilding(b);
    this.buildProps();
    this.setupCamera(); this.resize();
    this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(el);
    this.timer = new THREE.Timer();
    const loop = () => { if (!this.alive) return; this.raf = requestAnimationFrame(loop); this.frame(); };
    loop();
  }

  /* ---------------------------------------------------------------- light */
  buildLights() {
    this.hemi = new THREE.HemisphereLight(0xcfe4ff, 0xe9d8b4, 1.3); this.scene.add(this.hemi);
    const s = this.sun = new THREE.DirectionalLight(0xfff2df, 3.0);
    s.castShadow = true; s.shadow.mapSize.set(2048, 2048);
    Object.assign(s.shadow.camera, {left: -24, right: 24, top: 24, bottom: -24, near: 1, far: 140});
    s.shadow.bias = -0.0004; s.shadow.normalBias = 0.035; s.shadow.radius = 4; s.shadow.blurSamples = 12;
    this.scene.add(s, s.target);
    this.windowMat = mat({color: 0x3a3346, roughness: 0.35, emissive: 0xffc879, emissiveIntensity: 0});
    this.glowMat = mat({color: 0xfff1d0, emissive: 0xffd896, emissiveIntensity: 0, roughness: 0.4});
    this.lampLights = [];
  }
  // Morning gold -> noon white -> dusk orange -> night blue, with the 2-D UI's mapping.
  setTime(minute) {
    const {n, dusk, h} = phaseOf(minute);
    this.night = n;
    const t = Math.max(0, Math.min(1, (h - 6) / 12)), up = Math.sin(Math.PI * t);
    const morning = h < 11 ? Math.max(0, 1 - (h - 6) / 4) : 0;
    let col = lerpC(C(0xfff4e4), C(0xffcf86), morning * 0.8);
    col = lerpC(col, C(0xff9a58), dusk);
    col = lerpC(col, C(0x8aa4ff), n);
    const sunPos = n > 0.6 ? new THREE.Vector3(-18, 30, 14) : new THREE.Vector3(Math.cos(Math.PI * t) * 34, 6 + up * 30, 18);
    this.sun.position.copy(sunPos).add(this.cam.target); this.sun.target.position.copy(this.cam.target);
    this.sun.color.copy(col); this.sun.intensity = THREE.MathUtils.lerp(3.1, 0.55, n) * (1 - dusk * 0.15);
    this.hemi.color.copy(lerpC(lerpC(C(0xcfe4ff), C(0xffc9a0), dusk * 0.6), C(0x283a6a), n));
    this.hemi.groundColor.copy(lerpC(C(0xe9d8b4), C(0x161a28), n));
    this.hemi.intensity = THREE.MathUtils.lerp(1.35, 0.55, n);
    const glow = Math.min(1, n * 1.15 + dusk * 0.45);
    this.windowMat.emissiveIntensity = glow * 1.8; this.glowMat.emissiveIntensity = glow * 2.2;
    for (const l of this.lampLights) l.intensity = glow * 14;
    this.renderer.toneMappingExposure = THREE.MathUtils.lerp(1.05, 1.25, n);
  }

  /* ---------------------------------------------------------------- ground + water */
  buildIsland() {
    const I = this.S.island;
    const base = mesh(softBox(I.w, I.h, I.d, I.radius, I.stone, 4), mat({vertexColors: true}));
    base.position.y = I.h / 2; this.scene.add(base);
    const grass = mesh(softBox(I.w - 0.9, 0.3, I.d - 0.9, 0.15, I.grass, 3), mat({vertexColors: true, roughness: 0.95}), {cast: false});
    grass.position.y = I.h + 0.02; this.scene.add(grass);
    this.groundY = I.h + 0.17;
  }
  buildWater() {
    const W = this.S.water, g = new THREE.PlaneGeometry(W.size, W.size, 90, 90); g.rotateX(-Math.PI / 2);
    this.water = mesh(g, mat({color: C(W.color), roughness: 0.35, flatShading: true, transparent: true, opacity: 0.96}), {cast: false});
    this.water.position.y = 0.35; this.scene.add(this.water);
    this.waterBase = Float32Array.from(g.attributes.position.array);
  }
  buildPlaza() {
    const P = this.S.plaza, I = this.S.island, s = P.slab;
    const spots = [];
    for (let x = -I.w / 2 + 1; x < I.w / 2 - 0.5; x += s) for (let z = -I.d / 2 + 1; z < I.d / 2 - 0.5; z += s) {
      const dx = x - P.center[0], dz = z - P.center[1];
      if (dx * dx + dz * dz < P.radius * P.radius) spots.push([x, z]);
    }
    const g = new RoundedBoxGeometry(s * 0.92, 0.14, s * 0.92, 2, 0.05);
    const inst = new THREE.InstancedMesh(g, mat({roughness: 0.9}), spots.length);
    const m = new THREE.Matrix4(), col = new THREE.Color();
    spots.forEach(([x, z], i) => {
      const jit = (Math.sin(x * 12.9 + z * 78.2) * 43758.5453) % 1;
      m.makeTranslation(x, this.groundY + 0.02 + Math.abs(jit) * 0.03, z); inst.setMatrixAt(i, m);
      inst.setColorAt(i, col.set(P.colors[Math.floor(Math.abs(jit) * P.colors.length) % P.colors.length]));
    });
    inst.receiveShadow = true; this.scene.add(inst);
  }

  /* ---------------------------------------------------------------- buildings */
  buildBuilding(B) {
    const grp = new THREE.Group(); grp.position.set(...B.pos);
    const parts = B.parts.map(p => {
      const m = mesh(softBox(p.size[0], p.size[1], p.size[2], 0.28, p.color), mat({vertexColors: true}));
      m.position.set(p.at[0], this.groundY + p.at[1] + p.size[1] / 2, p.at[2]); grp.add(m);
      return {...p, y0: this.groundY + p.at[1]};
    });
    const face = (p, f, x, y, depth = 0) => {   // a point on a part's face (front = +z, east = +x)
      if (f === 'east') return new THREE.Vector3(p.at[0] + p.size[0] / 2 + depth, p.y0 + y, p.at[2] + x);
      return new THREE.Vector3(p.at[0] + x, p.y0 + y, p.at[2] + p.size[2] / 2 + depth);
    };
    const orient = (o, f) => { if (f === 'east') o.rotation.y = Math.PI / 2; };
    for (const r of B.roofs || []) {
      const p = parts[r.part];
      if (r.kind === 'ledge') {
        const m = mesh(softBox(p.size[0] + 0.35, 0.3, p.size[2] + 0.35, 0.12, r.color), mat({vertexColors: true}));
        m.position.set(p.at[0], p.y0 + p.size[1] + 0.12, p.at[2]); grp.add(m); continue;
      }
      const o = r.overhang, d = p.size[2] / 2 + o, sh = new THREE.Shape();
      sh.moveTo(-d, 0); sh.lineTo(d, 0); sh.lineTo(0, r.height); sh.lineTo(-d, 0);
      const len = p.size[0] + o * 2 - 0.2;
      const g = new THREE.ExtrudeGeometry(sh, {depth: len, bevelEnabled: true, bevelThickness: 0.1, bevelSize: 0.1, bevelSegments: 3, curveSegments: 4});
      g.translate(0, 0, -len / 2); g.rotateY(Math.PI / 2);
      const m = mesh(g, mat({color: C(r.color), roughness: 0.75})); m.position.set(p.at[0], p.y0 + p.size[1] - 0.02, p.at[2]); grp.add(m);
    }
    for (const w of B.windows || []) {   // a lighter frame with a dark rounded slot set into it
      const p = parts[w.part], pos = face(p, w.face, w.x, w.y);
      const frame = mesh(softBox(w.w + 0.32, w.h + 0.32, 0.16, 0.12, '#fff6ec'), mat({vertexColors: true}), {cast: false});
      frame.position.copy(pos); orient(frame, w.face); grp.add(frame);
      const slot = mesh(new RoundedBoxGeometry(w.w, w.h, 0.12, 2, 0.12), this.windowMat, {cast: false});
      const p2 = face(p, w.face, w.x, w.y, 0.04); slot.position.copy(p2); orient(slot, w.face); grp.add(slot);
      const sill = mesh(softBox(w.w + 0.5, 0.12, 0.34, 0.05, '#fff6ec'), mat({vertexColors: true}));
      sill.position.copy(face(p, w.face, w.x, w.y - w.h / 2 - 0.2, 0.12)); orient(sill, w.face); grp.add(sill);
    }
    if (B.door) {
      const D = B.door, p = parts[D.part];
      const door = mesh(new RoundedBoxGeometry(D.w, D.h, 0.2, 2, 0.14), mat({color: C(D.color), roughness: 0.6}), {cast: false});
      door.position.copy(face(p, D.face, D.x, D.h / 2, 0.02)); grp.add(door);
      const knob = mesh(new THREE.SphereGeometry(0.07, 10, 8), this.glowMat); knob.position.copy(face(p, D.face, D.x + D.w * 0.32, D.h * 0.48, 0.14)); grp.add(knob);
      const step = mesh(softBox(D.w + 0.8, 0.18, 0.7, 0.06, '#e8dcc4'), mat({vertexColors: true})); step.position.copy(face(p, D.face, D.x, 0.09, 0.32)); grp.add(step);
    }
    if (B.awning) {   // striped porch awning: alternating slats, tilted down toward the street
      const A = B.awning, p = parts[A.part], n = 9, sw = A.w / n;
      const aw = new THREE.Group();
      for (let i = 0; i < n; i++) {
        const s = mesh(softBox(sw * 0.98, 0.09, A.depth, 0.03, A.colors[i % 2]), mat({vertexColors: true, roughness: 0.7}));
        s.position.set(-A.w / 2 + sw * (i + 0.5), 0, A.depth / 2); aw.add(s);
      }
      const valance = mesh(softBox(A.w, 0.22, 0.08, 0.03, A.colors[0]), mat({vertexColors: true})); valance.position.set(0, -0.12, A.depth); aw.add(valance);
      aw.rotation.x = 0.32; aw.position.copy(face(p, 'front', A.x, A.y, 0.04)); grp.add(aw);
    }
    if (B.sign) {
      const G = B.sign, p = parts[G.part];
      const board = mesh(softBox(G.w, G.h, 0.14, 0.08, G.color), mat({vertexColors: true})); board.position.copy(face(p, 'front', G.x, G.y, 0.08)); grp.add(board);
      const strip = mesh(new RoundedBoxGeometry(G.w - 0.3, 0.12, 0.05, 2, 0.04), this.glowMat); strip.position.copy(face(p, 'front', G.x, G.y, 0.17)); grp.add(strip);
    }
    this.scene.add(grp);
  }

  buildProps() {
    const P = this.S.props, y = this.groundY;
    for (const [x, z, s] of P.trees) {
      const g = new THREE.Group(); g.position.set(x, y, z); g.scale.setScalar(s);
      const trunk = mesh(new THREE.CylinderGeometry(0.16, 0.24, 1.3, 10), mat({color: 0x9a6a4a})); trunk.position.y = 0.65; g.add(trunk);
      [[0, 2.1, 0, 1.15, 0x8fd07a], [0.55, 1.75, 0.25, 0.8, 0x7cc46c], [-0.45, 1.85, -0.2, 0.85, 0x9ad884]].forEach(([cx, cy, cz, r, c]) => {
        const b = mesh(new THREE.IcosahedronGeometry(r, 2), mat({color: c, roughness: 0.9})); b.position.set(cx, cy, cz); g.add(b); });
      this.scene.add(g);
    }
    for (const [x, z] of P.lamps) {
      const post = mesh(new THREE.CylinderGeometry(0.07, 0.1, 2.6, 8), mat({color: 0x3c3f4a, roughness: 0.5})); post.position.set(x, y + 1.3, z); this.scene.add(post);
      const bulb = mesh(new THREE.SphereGeometry(0.24, 16, 12), this.glowMat, {cast: false}); bulb.position.set(x, y + 2.75, z); this.scene.add(bulb);
      const l = new THREE.PointLight(0xffc27a, 0, 9, 1.6); l.position.set(x, y + 2.7, z); this.scene.add(l); this.lampLights.push(l);
    }
    for (const [x, z, rot] of P.benches) {
      const g = new THREE.Group(); g.position.set(x, y, z); g.rotation.y = rot;
      const seat = mesh(softBox(1.8, 0.14, 0.55, 0.05, '#b98258'), mat({vertexColors: true})); seat.position.y = 0.5; g.add(seat);
      const back = mesh(softBox(1.8, 0.45, 0.12, 0.05, '#b98258'), mat({vertexColors: true})); back.position.set(0, 0.8, -0.24); g.add(back);
      for (const sx of [-0.7, 0.7]) { const leg = mesh(softBox(0.12, 0.5, 0.45, 0.03, '#4a4550'), mat({vertexColors: true})); leg.position.set(sx, 0.25, 0); g.add(leg); }
      this.scene.add(g);
    }
    for (const [x, z] of P.tables) {
      const g = new THREE.Group(); g.position.set(x, y, z);
      const top = mesh(new THREE.CylinderGeometry(0.55, 0.55, 0.08, 20), mat({color: 0xfff4ea})); top.position.y = 0.75; g.add(top);
      const leg = mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.75, 8), mat({color: 0x3c3f4a})); leg.position.y = 0.37; g.add(leg);
      const pole = mesh(new THREE.CylinderGeometry(0.04, 0.04, 2.1, 8), mat({color: 0xe8e0d0})); pole.position.y = 1.4; g.add(pole);
      const shade = mesh(new THREE.ConeGeometry(1.25, 0.55, 16), mat({color: 0xe8645a, roughness: 0.7})); shade.position.y = 2.35; g.add(shade);
      this.scene.add(g);
    }
    if (P.fountain) {
      const [x, z] = P.fountain, g = new THREE.Group(); g.position.set(x, y, z);
      const basin = mesh(new THREE.CylinderGeometry(1.6, 1.75, 0.55, 28), mat({color: 0xe6dccb})); basin.position.y = 0.27; g.add(basin);
      const pool = mesh(new THREE.CylinderGeometry(1.35, 1.35, 0.05, 28), mat({color: 0x7fd0e2, roughness: 0.25}), {cast: false}); pool.position.y = 0.52; g.add(pool);
      const col = mesh(new THREE.CylinderGeometry(0.22, 0.3, 1.3, 14), mat({color: 0xe6dccb})); col.position.y = 1.0; g.add(col);
      const bowl = mesh(new THREE.SphereGeometry(0.55, 20, 10, 0, Math.PI * 2, 0, Math.PI / 2), mat({color: 0xe6dccb})); bowl.rotation.x = Math.PI; bowl.position.y = 1.75; g.add(bowl);
      this.scene.add(g);
    }
  }

  /* ---------------------------------------------------------------- residents */
  // round head on a capsule body in the resident's colour; a little bounce while walking
  resident(id, color) {
    let r = this.residents.get(id);
    if (r) return r;
    const g = new THREE.Group();
    const body = mesh(new THREE.CapsuleGeometry(0.34, 0.5, 6, 16), mat({color: C(color), roughness: 0.6})); body.position.y = 0.7; g.add(body);
    const head = mesh(new THREE.SphereGeometry(0.33, 20, 16), mat({color: 0xf6d5b8, roughness: 0.6})); head.position.y = 1.52; g.add(head);
    const hair = mesh(new THREE.SphereGeometry(0.345, 20, 12, 0, Math.PI * 2, 0, Math.PI * 0.5), mat({color: C(color).multiplyScalar(0.45), roughness: 0.7})); hair.position.y = 1.56; hair.rotation.x = -0.25; g.add(hair);
    for (const sx of [-0.12, 0.12]) { const eye = mesh(new THREE.SphereGeometry(0.04, 8, 6), mat({color: 0x2a2430}), {cast: false}); eye.position.set(sx, 1.55, 0.3); g.add(eye); }
    g.position.set(0, this.groundY, 0); this.scene.add(g);
    r = {id, g, x: 0, z: 0, tx: 0, tz: 0, phase: Math.random() * 6, moving: false};
    this.residents.set(id, r); return r;
  }
  place(id, color, [x, z], snap) {
    const r = this.resident(id, color);
    r.tx = x; r.tz = z; if (snap || r.fresh === undefined) { r.x = x; r.z = z; r.fresh = false; }
  }
  drop(id) { const r = this.residents.get(id); if (r) { this.scene.remove(r.g); this.residents.delete(id); } }
  headScreen(id) {   // where a resident's head is on screen (for labels and bubbles)
    const r = this.residents.get(id); if (!r) return null;
    const v = new THREE.Vector3(r.g.position.x, r.g.position.y + 2.1, r.g.position.z).project(this.camera);
    const W = this.el.clientWidth, H = this.el.clientHeight;
    return {x: (v.x + 1) / 2 * W, y: (1 - v.y) / 2 * H, visible: v.z < 1};
  }

  /* ---------------------------------------------------------------- camera */
  setupCamera() {
    const cv = this.renderer.domElement; cv.style.touchAction = 'none';
    let last = null;
    cv.addEventListener('pointerdown', e => { last = {x: e.clientX, y: e.clientY}; try { cv.setPointerCapture(e.pointerId); } catch (_) {} cv.style.cursor = 'grabbing'; });
    cv.addEventListener('pointermove', e => {
      if (!last) return;
      const dx = e.clientX - last.x, dy = e.clientY - last.y; last = {x: e.clientX, y: e.clientY};
      const k = this.cam.dist * 0.0017, az = this.cam.az;
      const right = new THREE.Vector3(Math.cos(az), 0, -Math.sin(az)), fwd = new THREE.Vector3(Math.sin(az), 0, Math.cos(az));
      this.cam.target.addScaledVector(right, -dx * k).addScaledVector(fwd, -dy * k * 1.4);
      const I = this.S.island; this.cam.target.x = Math.max(-I.w / 2, Math.min(I.w / 2, this.cam.target.x)); this.cam.target.z = Math.max(-I.d / 2, Math.min(I.d / 2, this.cam.target.z));
    });
    const up = () => { last = null; cv.style.cursor = ''; };
    cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up);
    cv.addEventListener('dblclick', () => { const C_ = this.S.camera; this.cam.target.set(...C_.target); this.cam.dist = C_.distance; });   // back to the default view
    cv.addEventListener('wheel', e => { e.preventDefault(); const C_ = this.S.camera;
      this.cam.dist = Math.max(C_.min, Math.min(C_.max, this.cam.dist * Math.exp(e.deltaY * 0.0012))); }, {passive: false});
  }
  updateCamera() {
    const c = this.cam, h = Math.cos(c.el) * c.dist;
    this.camera.position.set(c.target.x + Math.sin(c.az) * h, c.target.y + Math.sin(c.el) * c.dist, c.target.z + Math.cos(c.az) * h);
    this.camera.lookAt(c.target);
  }
  resize() {
    const w = this.el.clientWidth, h = this.el.clientHeight; if (!w || !h) return;
    this.renderer.setSize(w, h); this.camera.aspect = w / h; this.camera.updateProjectionMatrix();
  }

  /* ---------------------------------------------------------------- frame */
  frame() {
    this.timer.update(); const dt = Math.min(0.05, this.timer.getDelta()); this.t += dt;
    // gentle waves: a couple of slow sine swells over the flat-shaded water
    const pos = this.water.geometry.attributes.position, b = this.waterBase, t = this.t;
    for (let i = 0; i < pos.count; i++) {
      const x = b[i * 3], z = b[i * 3 + 2];
      pos.array[i * 3 + 1] = Math.sin(x * 0.42 + t * 0.9) * 0.07 + Math.cos(z * 0.36 + t * 1.15) * 0.06;
    }
    pos.needsUpdate = true; this.water.geometry.computeVertexNormals();
    for (const r of this.residents.values()) {
      const dx = r.tx - r.x, dz = r.tz - r.z, d = Math.hypot(dx, dz), step = 1.7 * dt;
      r.moving = d > 0.05;
      if (r.moving) { const k = Math.min(1, step / d); r.x += dx * k; r.z += dz * k; r.g.rotation.y = Math.atan2(dx, dz); }
      const bounce = r.moving ? Math.abs(Math.sin(this.t * 9 + r.phase)) * 0.14 : Math.sin(this.t * 2 + r.phase) * 0.015;
      r.g.position.set(r.x, this.groundY + bounce, r.z);
      r.g.scale.set(1, r.moving ? 1 - bounce * 0.4 : 1, 1);
    }
    this.updateCamera();
    this.renderer.render(this.scene, this.camera);
    if (this.onFrame) this.onFrame();
  }
  destroy() { this.alive = false; cancelAnimationFrame(this.raf); this.ro.disconnect(); this.renderer.dispose(); }
}
