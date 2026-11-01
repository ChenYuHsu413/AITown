// Chibi resident figures, procedural: a big round head on a small jointed body
// (hips -> thigh -> knee -> shin -> shoe, shoulder -> upper arm -> elbow -> forearm -> hand),
// a simple face, hair as real geometry. Proportions, colours and motion come from
// residents.json; the same clay vertex-colour language as the buildings.
import * as THREE from 'three';
import {C, tint, rbox, mesh, CLAY} from './clay.js';

const soft = {grad: 0.14, warm: 0.04, faceVar: 0, grain: 0.015};
const flat = {grad: 0.06, warm: 0, faceVar: 0, grain: 0.01};
const dark = (hex, k) => '#' + C(hex).multiplyScalar(k).getHexString();
const capsule = (r, len) => new THREE.CapsuleGeometry(r, Math.max(0.001, len - 2 * r), 6, 14);
const sphere = (r, ws = 24, hs = 18) => new THREE.SphereGeometry(r, ws, hs);

// hair styles: geometry in head-local space (head centre at 0, radius R); `put` adds a part
const HAIR = {
  cap(put, R, c, {tilt = -0.32, cover = 0.56, grow = 1.07} = {}) {
    const m = put(new THREE.SphereGeometry(R * grow, 32, 16, 0, Math.PI * 2, 0, Math.PI * cover), c);
    m.rotation.x = tilt; return m;
  },
  buzz(put, R, c) { HAIR.cap(put, R, c, {grow: 1.03, cover: 0.5}); },
  short(put, R, c) {
    HAIR.cap(put, R, c);
    for (const sx of [-1, 1]) { const s = put(sphere(R * 0.36), c); s.position.set(sx * R * 0.78, R * 0.12, -R * 0.12); }
  },
  messy(put, R, c) {   // a cap pushed back, a lumpy fringe over the forehead, sideburns, three tufts
    HAIR.cap(put, R, c, {tilt: -0.55, cover: 0.55});
    for (const sx of [-1, 1]) { const s = put(sphere(R * 0.3), c); s.position.set(sx * R * 0.86, R * 0.0, -R * 0.12); s.scale.set(0.55, 1.2, 1); }
    [[-0.56, 0.42, 0.66, 0.27], [-0.2, 0.5, 0.78, 0.3], [0.18, 0.48, 0.78, 0.28], [0.54, 0.4, 0.66, 0.26]].forEach(([x, y, z, r], i) => {
      const s = put(sphere(R * r), c); s.position.set(x * R, y * R, z * R); s.scale.set(1.1, 0.85, 0.7); s.rotation.z = (i - 1.5) * 0.25;
    });
    [[0, 1.05, 0, 0.1, 0.22], [0.3, 0.98, -0.2, -0.5, 0.18], [-0.32, 0.95, 0.1, 0.55, 0.17]].forEach(([x, y, z, rz, r]) => {
      const t = put(new THREE.ConeGeometry(R * r, R * 0.5, 12), c); t.position.set(x * R, y * R, z * R); t.rotation.set(-0.2, 0, rz);
    });
  },
  long(put, R, c) {
    HAIR.cap(put, R, c, {cover: 0.6});
    const back = put(rbox(R * 1.9, R * 1.9, R * 0.5, R * 0.24), c); back.position.set(0, -R * 0.45, -R * 0.62);
    for (const sx of [-1, 1]) { const s = put(rbox(R * 0.36, R * 1.5, R * 0.5, R * 0.17), c); s.position.set(sx * R * 0.9, -R * 0.4, -R * 0.08); }
  },
  bob(put, R, c) {
    HAIR.cap(put, R, c, {cover: 0.62, grow: 1.1});
    for (const sx of [-1, 1]) { const s = put(sphere(R * 0.5), c); s.position.set(sx * R * 0.72, -R * 0.22, -R * 0.15); s.scale.set(0.8, 1.15, 1.1); }
  },
  ponytail(put, R, c) {
    HAIR.cap(put, R, c);
    const tie = put(sphere(R * 0.2), c); tie.position.set(0, R * 0.25, -R * 1.02);
    const tail = put(capsule(R * 0.22, R * 1.2), c); tail.position.set(0, -R * 0.25, -R * 1.18); tail.rotation.x = 0.35;
  },
  mop(put, R, c) {   // a full bowl cut with a straight fringe
    HAIR.cap(put, R, c, {tilt: -0.1, cover: 0.6, grow: 1.09});
    const f = put(new THREE.SphereGeometry(R * 1.08, 32, 8, -Math.PI * 0.42, Math.PI * 0.84, Math.PI * 0.3, Math.PI * 0.16), c); f.rotation.y = 0;
  },
  side(put, R, c) {   // bald on top: hair only round the back and over the ears
    HAIR.cap(put, R, c, {tilt: -1.25, cover: 0.42, grow: 1.05});
    for (const sx of [-1, 1]) { const s = put(sphere(R * 0.3), c); s.position.set(sx * R * 0.88, -R * 0.02, -R * 0.3); s.scale.set(0.5, 1.0, 1.1); }
  },
  bun(put, R, c) {
    HAIR.cap(put, R, c);
    const b = put(sphere(R * 0.38), c); b.position.set(0, R * 0.78, -R * 0.62);
  },
  hat(put, R, c, H) {   // a soft hat in H.hat colour over short hair
    HAIR.short(put, R, c);
    const hc = H.hat || '#6b5a4a';
    const brim = put(new THREE.CylinderGeometry(R * 1.35, R * 1.38, R * 0.1, 32), hc); brim.position.set(0, R * 0.5, 0); brim.rotation.x = -0.12;
    const crown = put(new THREE.SphereGeometry(R * 0.9, 28, 14, 0, Math.PI * 2, 0, Math.PI * 0.5), hc); crown.position.set(0, R * 0.48, -R * 0.04); crown.rotation.x = -0.12;
  },
};

export class Figure {
  constructor(cfg, spec, mainColor) {
    const P = spec.proportions, H = cfg.height || 1, Bw = cfg.build || 1;
    this.spec = spec; this.cfg = cfg; this.M = spec.motion; this.H = H;
    const top = cfg.top === 'main' ? mainColor : cfg.top, bottom = cfg.bottom === 'main' ? mainColor : cfg.bottom;
    const skin = cfg.skin || spec.skin, coat = (cfg.extras || []).find(x => x.kind === 'coat'), sleeve = coat ? coat.color : top;
    const part = (parent, g, color, o = soft, x = 0, y = 0, z = 0) => { const m = mesh(tint(g, color, o), CLAY); m.position.set(x, y, z); parent.add(m); return m; };
    const pivot = (parent, x, y, z) => { const g = new THREE.Group(); g.position.set(x, y, z); parent.add(g); return g; };

    const root = this.root = new THREE.Group();
    const body = this.body = pivot(root, 0, 0, 0);
    const hipY = P.hip * H, thigh = P.thigh * H, shin = P.shin * H;
    const hips = this.hips = pivot(body, 0, hipY, 0);
    part(hips, rbox(P.pelvis[0] * Bw, P.pelvis[1], P.pelvis[2] * Bw, 0.1), bottom, soft, 0, 0.03, 0);
    this.legs = [-1, 1].map(sx => {
      const hip = pivot(hips, sx * P.hipGap * Bw, -0.02, 0);
      part(hip, capsule(P.leg * Bw, thigh + P.leg), bottom, soft, 0, -thigh / 2, 0);
      const knee = pivot(hip, 0, -thigh, 0);
      part(knee, capsule(P.leg * Bw * 0.92, shin + P.leg), bottom, soft, 0, -shin / 2, 0);
      const shoe = part(knee, rbox(P.shoe[0] * Bw, P.shoe[1], P.shoe[2], 0.05, 3), cfg.shoes || '#4a3a34', flat, 0, -shin - P.shoe[1] / 2 + 0.02, 0.04);
      return {hip, knee, shoe};
    });
    const tH = P.torso[1] * H, spine = this.spine = pivot(hips, 0, 0.06, 0);
    part(spine, rbox(P.torso[0] * Bw, tH, P.torso[2] * Bw, 0.15), top, soft, 0, tH / 2, 0);
    this.arms = [-1, 1].map(sx => {
      const sh = pivot(spine, sx * P.shoulder * Bw, tH - 0.11, 0);
      part(sh, sphere(P.arm * 1.0, 16, 12), sleeve, soft);   // rounded shoulder
      part(sh, capsule(P.arm, P.upperArm + P.arm), sleeve, soft, 0, -P.upperArm / 2, 0);
      const el = pivot(sh, 0, -P.upperArm, 0);
      part(el, capsule(P.arm * 0.9, P.foreArm + P.arm), sleeve, soft, 0, -P.foreArm / 2, 0);
      part(el, sphere(P.hand, 16, 12), skin, flat, 0, -P.foreArm - P.hand * 0.6, 0);
      sh.rotation.z = sx * 0.08;
      return {sh, el, sx};
    });
    // head on a short neck
    const R = P.head, neck = this.neck = pivot(spine, 0, tH + P.neck, 0);
    const head = this.head = pivot(neck, 0, R * 0.92, 0);
    part(head, sphere(R, 40, 30), skin, {grad: 0.1, warm: 0.03, faceVar: 0, grain: 0.01});
    const onFace = (x, y) => Math.sqrt(Math.max(0, R * R - x * x - y * y));
    const eyeM = new THREE.MeshStandardMaterial({color: C(spec.eyes), roughness: 0.35});
    const hiM = new THREE.MeshBasicMaterial({color: 0xffffff});
    for (const sx of [-1, 1]) {
      const ex = sx * R * 0.34, ey = -R * 0.04;
      const eye = mesh(sphere(R * 0.12, 16, 12), eyeM, {cast: false}); eye.scale.set(0.85, 1.15, 0.5);
      eye.position.set(ex, ey, onFace(ex, ey) - R * 0.02); eye.lookAt(eye.position.clone().multiplyScalar(2)); head.add(eye);
      const hi = mesh(sphere(R * 0.035, 8, 6), hiM, {cast: false}); hi.position.set(ex + R * 0.04, ey + R * 0.05, onFace(ex, ey) + R * 0.035); head.add(hi);
      const bx = sx * R * 0.6, by = -R * 0.28;
      const bl = mesh(sphere(R * 0.13, 16, 10), new THREE.MeshStandardMaterial({color: C(spec.blush), roughness: 0.9, transparent: true, opacity: 0.7}), {cast: false});
      bl.scale.set(1, 0.6, 0.25); bl.position.set(bx, by, onFace(bx, by) - R * 0.02); bl.lookAt(bl.position.clone().multiplyScalar(2)); head.add(bl);
    }
    const mouth = mesh(new THREE.TorusGeometry(R * 0.07, R * 0.018, 6, 14, Math.PI), eyeM, {cast: false});
    mouth.rotation.z = Math.PI; mouth.position.set(0, -R * 0.26, onFace(0, -R * 0.26) - R * 0.01); head.add(mouth);
    // hair
    const hair = new THREE.Group(); head.add(hair);
    const put = (g, color) => { const m = mesh(tint(g, color, {grad: -0.12, warm: 0, faceVar: 0, grain: 0.03}), CLAY); hair.add(m); return m; };
    (HAIR[cfg.hair.style] || HAIR.short)(put, R, cfg.hair.color, cfg.hair);
    // extras
    for (const x of cfg.extras || []) {
      if (x.kind === 'apron') {
        const fz = P.torso[2] * Bw / 2;
        part(spine, rbox(P.torso[0] * Bw * 0.6, tH * 0.62, 0.04, 0.02, 2), x.color, flat, 0, tH * 0.42, fz - 0.005);
        part(spine, rbox(P.torso[0] * Bw - 0.01, 0.05, P.torso[2] * Bw + 0.012, 0.025, 2), dark(x.color, 0.92), flat, 0, 0.1, 0);   // waist tie
        part(hips, rbox(P.pelvis[0] * Bw * 0.95, P.pelvis[1] + 0.14, 0.04, 0.02, 2), x.color, flat, 0, -0.03, P.pelvis[2] * Bw / 2 + 0.01).rotation.x = -0.08;
        part(spine, rbox(0.14, 0.09, 0.02, 0.01, 2), dark(x.color, 0.86), flat, 0, tH * 0.36, fz + 0.022);   // pocket
      }
      const fz = P.torso[2] * Bw / 2, fw = P.torso[0] * Bw / 2, F = {grad: 0.06, warm: 0, faceVar: 0, grain: 0.015};
      if (x.kind === 'toque') {   // the baker's tall white hat
        part(head, new THREE.CylinderGeometry(R * 0.95, R * 0.98, R * 0.4, 28), x.color || '#fbf8f2', F, 0, R * 0.74, -R * 0.08).rotation.x = -0.12;
        const puff = part(head, sphere(R * 0.9, 28, 18), x.color || '#fbf8f2', F, 0, R * 1.12, -R * 0.14); puff.scale.set(1.1, 0.72, 1.06);
      }
      if (x.kind === 'satchel') {   // the postman's bag: a strap across the chest, the bag at the hip
        const st = part(spine, rbox(0.07, tH * 1.45, 0.03, 0.015, 1), dark(x.color, 0.8), F, 0, tH * 0.5, fz + 0.012); st.rotation.z = 0.72;
        const sb = part(spine, rbox(0.07, tH * 1.45, 0.03, 0.015, 1), dark(x.color, 0.8), F, 0, tH * 0.5, -fz - 0.012); sb.rotation.z = -0.72;
        part(hips, rbox(0.36, 0.3, 0.15, 0.06, 2), x.color, F, -(fw + 0.06), 0.02, 0.06);
        part(hips, rbox(0.37, 0.14, 0.16, 0.04, 2), dark(x.color, 0.85), F, -(fw + 0.06), 0.13, 0.065);
        part(hips, rbox(0.08, 0.06, 0.02, 0.01, 1), '#e8c86a', F, -(fw + 0.06), 0.08, 0.15);
      }
      if (x.kind === 'coat') {   // a white coat over the resident's colour (an inner panel shows), coat tails to mid-thigh
        part(spine, rbox(P.torso[0] * Bw + 0.04, tH + 0.02, P.torso[2] * Bw + 0.04, 0.16), x.color, F, 0, tH / 2, 0);
        part(spine, rbox(P.torso[0] * Bw * 0.26, tH * 0.8, 0.04, 0.02, 1), top, soft, 0, tH * 0.52, fz + 0.02);
        for (const sx of [-1, 1]) part(spine, rbox(0.08, tH * 0.5, 0.03, 0.015, 1), dark(x.color, 0.9), F, sx * P.torso[0] * Bw * 0.17, tH * 0.68, fz + 0.035).rotation.z = sx * 0.25;
        for (const sx of [-1, 1]) part(hips, rbox(P.pelvis[0] * Bw * 0.5 + 0.03, P.pelvis[1] + 0.3, P.pelvis[2] * Bw + 0.06, 0.06, 2), x.color, F, sx * (P.pelvis[0] * Bw * 0.26 + 0.01), -0.1, 0);
        if (x.stethoscope) { const t = part(spine, new THREE.TorusGeometry(0.16, 0.018, 6, 20, Math.PI), x.stethoscope, F, 0, tH * 0.95, fz * 0.4); t.rotation.set(Math.PI / 2 + 0.6, 0, Math.PI); }
      }
      if (x.kind === 'backpack') {   // rounded pack on the back, two straps in front
        part(spine, rbox(P.torso[0] * Bw * 0.82, tH * 0.92, 0.24, 0.1), x.color, F, 0, tH * 0.5, -fz - 0.12);
        part(spine, rbox(P.torso[0] * Bw * 0.6, tH * 0.32, 0.08, 0.05, 2), dark(x.color, 0.88), F, 0, tH * 0.3, -fz - 0.26);
        for (const sx of [-1, 1]) part(spine, rbox(0.06, tH * 0.95, 0.03, 0.015, 1), dark(x.color, 0.78), F, sx * P.torso[0] * Bw * 0.26, tH * 0.52, fz + 0.012);
      }
      if (x.kind === 'brush') {   // a long paintbrush stuck through the bun
        const b = new THREE.Group(); b.position.set(0, R * 0.78, -R * 0.62); b.rotation.set(0.5, 0, 0.9); head.add(b);
        part(b, new THREE.CylinderGeometry(0.028, 0.022, R * 1.6, 8), x.color || '#c8643c', F);
        part(b, new THREE.CylinderGeometry(0.032, 0.032, R * 0.16, 8), '#c8ccd2', F, 0, R * 0.82, 0);
        part(b, new THREE.ConeGeometry(0.04, R * 0.32, 8), x.tip || '#3a7ad0', F, 0, R * 1.05, 0);
      }
      if (x.kind === 'goggles') {   // pushed up on the forehead: a band and two round lenses
        const band = part(head, new THREE.TorusGeometry(R * 1.045, 0.035, 6, 36), '#3c3f4a', F, 0, R * 0.38, 0); band.rotation.x = Math.PI / 2 - 0.2;
        for (const sx of [-1, 1]) {
          const g = new THREE.Group(); g.position.set(sx * R * 0.3, R * 0.58, R * 0.84); g.rotation.set(-0.75, sx * 0.3, 0); head.add(g);
          part(g, new THREE.CylinderGeometry(R * 0.2, R * 0.22, R * 0.16, 18), '#5a5f6a', F).rotation.x = Math.PI / 2;
          const lens = mesh(new THREE.CylinderGeometry(R * 0.16, R * 0.16, R * 0.17, 18), new THREE.MeshStandardMaterial({color: C(x.color || '#f2b84a'), roughness: 0.15, metalness: 0.2}), {cast: false});
          lens.rotation.x = Math.PI / 2; g.add(lens);
        }
      }
      if (x.kind === 'bag') {   // a commuter tote on one shoulder
        part(spine, rbox(0.06, tH * 0.85, 0.03, 0.015, 1), dark(x.color, 0.8), F, fw - 0.02, tH * 0.55, fz * 0.3).rotation.z = 0.06;
        part(hips, rbox(0.42, 0.34, 0.13, 0.06, 2), x.color, F, fw + 0.1, 0.06, 0.02);
        part(hips, rbox(0.24, 0.02, 0.135, 0.01, 1), dark(x.color, 0.8), F, fw + 0.1, 0.16, 0.022);
      }
      if (x.kind === 'toolbelt') {   // a belt with pouches and a wrench
        part(hips, rbox(P.pelvis[0] * Bw + 0.05, 0.08, P.pelvis[2] * Bw + 0.05, 0.03, 2), x.color, F, 0, 0.12, 0);
        for (const sx of [-1, 1]) part(hips, rbox(0.15, 0.2, 0.1, 0.04, 2), dark(x.color, 0.9), F, sx * (P.pelvis[0] * Bw * 0.36), 0.0, P.pelvis[2] * Bw / 2 + 0.03);
        part(hips, rbox(0.04, 0.26, 0.02, 0.01, 1), '#a8aeb8', F, P.pelvis[0] * Bw / 2 + 0.03, -0.04, 0.05);
        part(hips, new THREE.TorusGeometry(0.045, 0.016, 6, 12), '#a8aeb8', F, P.pelvis[0] * Bw / 2 + 0.03, -0.19, 0.05).rotation.y = Math.PI / 2;
      }
      if (x.kind === 'cane') {
        const cane = new THREE.Group(); this.arms[1].el.add(cane);
        const cl = hipY + 0.08;
        part(cane, new THREE.CylinderGeometry(0.035, 0.03, cl, 8), x.color || '#6b4a32', flat, 0, -P.foreArm - cl / 2 + 0.02, 0.1);
        part(cane, new THREE.TorusGeometry(0.06, 0.03, 6, 12, Math.PI), x.color || '#6b4a32', flat, 0, -P.foreArm + 0.02, 0.04).rotation.y = Math.PI / 2;
        this.cane = cane;
      }
    }
    this.stoop = cfg.stoop || 0;
    this.state = 'idle'; this.t = Math.random() * 10; this.seed = Math.random() * 6;
  }

  // state: 'walk' | 'idle' | 'talk' | 'sit' | 'sleep'
  pose(state, dt) {
    const M = this.M, t = (this.t += dt), s = this.seed;
    const L = this.legs, A = this.arms;
    let bodyY = 0, hipsRot = 0, knee = [0, 0], hip = [0, 0], arm = [0.05, 0.05], elbow = [-0.2, -0.2], spineRot = this.stoop, headX = 0, headY = 0, roll = 0, twist = 0, lie = 0;
    if (state === 'walk') {
      const ph = t * M.walkFreq;
      hip = [Math.sin(ph) * M.legSwing, -Math.sin(ph) * M.legSwing];
      knee = [Math.max(0, Math.sin(ph + 1.4)) * M.knee, Math.max(0, -Math.sin(ph + 1.4)) * M.knee];
      arm = [-Math.sin(ph) * M.armSwing, Math.sin(ph) * M.armSwing];
      elbow = [-M.elbow, -M.elbow];
      bodyY = Math.abs(Math.cos(ph)) * M.bob - M.bob * 0.5;
      twist = Math.sin(ph) * M.twist; roll = Math.sin(ph) * 0.03; spineRot += 0.06;
    } else if (state === 'sit') {
      hip = [-1.5, -1.5]; knee = [1.45, 1.45]; arm = [-0.35, -0.35]; elbow = [-0.7, -0.7];
      bodyY = M.seat - this.spec.proportions.hip * this.H;
      headX = Math.sin(t * 0.7 + s) * 0.05;
    } else if (state === 'sleep') {
      lie = 1; arm = [0.1, 0.1]; elbow = [-0.1, -0.1]; headY = 0.35;
    } else {   // idle / talk
      const b = Math.sin(t * 2 + s);
      roll = Math.sin(t * 0.9 + s) * M.sway; headY = Math.sin(t * 0.45 + s) * 0.25; headX = b * 0.02;
      arm = [0.04 + b * 0.02, 0.04 - b * 0.02];
      if (state === 'talk') {
        const k = (t % M.nodEvery) / M.nodEvery;   // a nod near the end of each beat, a small hand gesture
        headX = k > 0.78 ? Math.sin((k - 0.78) / 0.22 * Math.PI * 2) * M.nod : headX; headY *= 0.25;
        arm[1] = -0.5 - Math.max(0, Math.sin(t * 1.7 + s)) * 0.5; elbow[1] = -0.9;
      }
    }
    L.forEach((l, i) => { l.hip.rotation.x = hip[i]; l.knee.rotation.x = knee[i]; l.shoe.rotation.x = -Math.min(0, hip[i] + knee[i]) * 0.3; });
    A.forEach((a, i) => { a.sh.rotation.x = arm[i]; a.el.rotation.x = elbow[i]; });
    const br = 1 + Math.sin(t * 2.1 + s) * M.breath;
    this.spine.rotation.set(spineRot, twist, 0); this.spine.scale.set(1, br, 1);
    this.head.rotation.set(headX - this.stoop * 0.6, headY, 0);
    this.body.position.y = bodyY; this.body.rotation.z = roll;
    this.body.rotation.x = lie ? -Math.PI / 2 : 0;
    if (lie) this.body.position.y = 0.32;
  }
}
