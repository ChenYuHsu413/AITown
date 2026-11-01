// The 3-D validation slice, "clay miniature" pass (Townscaper direction): an island
// with one café and a corner of the plaza on gently moving water. Everything is
// generated from slice.json -- rounded masses, slab roofs with eaves, framed windows,
// a recessed door with a canopy, a scalloped awning -- with vertex colours only (no
// textures, no model files).
// Pipeline: ACES tone mapping + sRGB output, a 4096 sun shadow map softened by PCF
// blur, GTAO ambient occlusion (EffectComposer, same three version), a gradient sky
// with fog. The sun's angle and colour follow the sim clock.
import * as THREE from 'three';
import {RoundedBoxGeometry} from 'three/addons/geometries/RoundedBoxGeometry.js';
import {EffectComposer} from 'three/addons/postprocessing/EffectComposer.js';
import {RenderPass} from 'three/addons/postprocessing/RenderPass.js';
import {GTAOPass} from 'three/addons/postprocessing/GTAOPass.js';
import {OutputPass} from 'three/addons/postprocessing/OutputPass.js';
import {phaseOf} from '../names.js';

const C = h => new THREE.Color(h);
const lerpC = (a, b, k) => a.clone().lerp(b, Math.max(0, Math.min(1, k)));
const rnd = (a, b = 0) => { const s = Math.sin(a * 127.1 + b * 311.7) * 43758.5453; return s - Math.floor(s); };

// Vertex colours: bottom a touch darker and warmer, faces turned east/west a shade apart,
// plus a little per-vertex grain -- never a dead flat colour.
function tint(g, color, {grad = 0.16, warm = 0.12, faceVar = 0.035, grain = 0.02, bands = null} = {}) {
  g.computeBoundingBox();
  const pos = g.attributes.position, nor = g.attributes.normal, bb = g.boundingBox, h = bb.max.y - bb.min.y || 1;
  const base = C(color), warmC = C('#c8794a'), col = new Float32Array(pos.count * 3), c = new THREE.Color();
  for (let i = 0; i < pos.count; i++) {
    const t = (pos.getY(i) - bb.min.y) / h;
    c.copy(base).lerp(warmC, warm * (1 - t));
    let k = 1 - grad * (1 - t);
    if (nor) { const nx = nor.getX(i), nz = nor.getZ(i); k += nx > 0.5 ? -faceVar : nz > 0.5 ? faceVar * 0.6 : 0; }
    if (bands) k *= bands[Math.floor(t * bands.length * 0.999)];
    k += (rnd(pos.getX(i) * 3.1, pos.getZ(i) * 2.7 + pos.getY(i)) - 0.5) * grain;
    col[i * 3] = Math.min(1, c.r * k); col[i * 3 + 1] = Math.min(1, c.g * k); col[i * 3 + 2] = Math.min(1, c.b * k);
  }
  g.setAttribute('color', new THREE.BufferAttribute(col, 3));
  return g;
}
const rbox = (w, h, d, r, seg = 4) => new RoundedBoxGeometry(w, h, d, seg, Math.min(r, w / 2 - 0.005, h / 2 - 0.005, d / 2 - 0.005));
const clay = (o = {}) => new THREE.MeshStandardMaterial({roughness: 0.9, metalness: 0, vertexColors: true, ...o});
function mesh(g, m, {cast = true, receive = true} = {}) { const x = new THREE.Mesh(g, m); x.castShadow = cast; x.receiveShadow = receive; return x; }
const CLAY = clay();

export class World {
  constructor(el, spec, {onFrame} = {}) {
    this.el = el; this.S = spec; this.onFrame = onFrame;
    this.residents = new Map(); this.t = 0; this.alive = true; this.slow = 0; this.quality = 'full';
    const r = this.renderer = new THREE.WebGLRenderer({antialias: false, powerPreference: 'high-performance'});
    r.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    r.outputColorSpace = THREE.SRGBColorSpace; r.toneMapping = THREE.ACESFilmicToneMapping; r.toneMappingExposure = 0.92;
    r.shadowMap.enabled = true; r.shadowMap.type = THREE.PCFShadowMap;   // PCFSoftShadowMap is folded into PCF (+ radius) in r186
    el.appendChild(r.domElement); r.domElement.classList.add('world-canvas');
    this.scene = new THREE.Scene();
    this.skyCanvas = document.createElement('canvas'); this.skyCanvas.width = 2; this.skyCanvas.height = 256;
    this.sky = new THREE.CanvasTexture(this.skyCanvas); this.sky.colorSpace = THREE.SRGBColorSpace; this.scene.background = this.sky;
    this.scene.fog = new THREE.Fog(0xcfe3e0, 70, 190);
    this.camera = new THREE.PerspectiveCamera(spec.camera.fov, 1, 0.5, 500);
    this.cam = {target: new THREE.Vector3(...spec.camera.target), dist: spec.camera.distance,
      az: THREE.MathUtils.degToRad(spec.camera.azimuth), el: THREE.MathUtils.degToRad(spec.camera.elevation)};
    this.buildLights(); this.buildIsland(); this.buildWater(); this.buildPlaza();
    for (const b of spec.buildings) this.buildBuilding(b);
    this.buildProps();
    this.buildComposer();
    this.setupCamera(); this.resize();
    this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(el);
    this.timer = new THREE.Timer();
    this.fpsWin = []; this.fpsT = setInterval(() => this.watchFps(), 1000);
    const loop = () => { if (!this.alive) return; this.raf = requestAnimationFrame(loop); this.frame(); };
    loop();
  }

  /* ---------------------------------------------------------------- pipeline */
  buildComposer() {
    const rt = new THREE.WebGLRenderTarget(4, 4, {samples: 4, type: THREE.HalfFloatType});
    const c = this.composer = new EffectComposer(this.renderer, rt);
    c.addPass(new RenderPass(this.scene, this.camera));
    this.ao = new GTAOPass(this.scene, this.camera, 4, 4);   // contact shadows in corners, under eaves, at the ground
    this.ao.output = GTAOPass.OUTPUT.Default; this.ao.blendIntensity = 1.0;
    this.ao.updateGtaoMaterial({radius: 2.2, distanceExponent: 1.4, thickness: 2.0, scale: 1.6, samples: 16});
    this.ao.updatePdMaterial({lumaPhi: 10, depthPhi: 2, normalPhi: 3, radius: 6, rings: 2, samples: 16});
    c.addPass(this.ao);
    c.addPass(new OutputPass());
  }
  // Quality fallback: if the frame rate sags (< 45 fps for 3 s) drop the AO pass and the
  // pixel ratio; keep the shadows and everything else.
  watchFps() {
    const f = this.frames || 0; this.frames = 0; this.fps = f;
    if (this.quality !== 'full') return;
    this.slow = f < 45 ? this.slow + 1 : 0;
    if (this.slow >= 3) { this.quality = 'reduced'; this.ao.enabled = false; this.renderer.setPixelRatio(1); this.resize(); }
  }

  /* ---------------------------------------------------------------- light + sky */
  buildLights() {
    this.hemi = new THREE.HemisphereLight(0xd8ecf2, 0xe8d4b0, 1.25); this.scene.add(this.hemi);
    const s = this.sun = new THREE.DirectionalLight(0xfff2df, 3.0);
    s.castShadow = true; s.shadow.mapSize.set(4096, 4096);
    Object.assign(s.shadow.camera, {left: -24, right: 24, top: 24, bottom: -24, near: 1, far: 160});
    s.shadow.bias = -0.0003; s.shadow.normalBias = 0.04; s.shadow.radius = 6;   // soft edges
    this.scene.add(s, s.target);
    this.glassMat = new THREE.MeshStandardMaterial({color: 0x2e2a3c, roughness: 0.25, metalness: 0.1, emissive: 0xffc06a, emissiveIntensity: 0});
    this.glowMat = new THREE.MeshStandardMaterial({color: 0xfff1d0, emissive: 0xffd28a, emissiveIntensity: 0, roughness: 0.4});
    this.lampLights = [];
  }
  paintSky(top, bottom) {
    const x = this.skyCanvas.getContext('2d'), g = x.createLinearGradient(0, 0, 0, 256);
    g.addColorStop(0, top); g.addColorStop(0.62, bottom); g.addColorStop(1, bottom);
    x.fillStyle = g; x.fillRect(0, 0, 2, 256); this.sky.needsUpdate = true;
  }
  // Morning gold -> noon white -> dusk orange -> night blue (the 2-D UI's day mapping).
  setTime(minute) {
    const {n, dusk, h} = phaseOf(minute);
    const t = Math.max(0, Math.min(1, (h - 6) / 12)), up = Math.sin(Math.PI * t), morning = h < 11 ? Math.max(0, 1 - (h - 6) / 4) : 0;
    let col = lerpC(C(0xfff4e6), C(0xffd08a), morning * 0.75);
    col = lerpC(col, C(0xff9a5c), dusk); col = lerpC(col, C(0x8aa4ff), n);
    const sunPos = n > 0.6 ? new THREE.Vector3(-18, 34, 16) : new THREE.Vector3(Math.cos(Math.PI * t) * 34, 8 + up * 30, 20);
    this.sun.position.copy(sunPos).add(this.cam.target); this.sun.target.position.copy(this.cam.target);
    this.sun.color.copy(col); this.sun.intensity = THREE.MathUtils.lerp(2.9, 0.5, n) * (1 - dusk * 0.12);
    this.hemi.color.copy(lerpC(lerpC(C(0xd8ecf2), C(0xffc8a0), dusk * 0.6), C(0x2a3c6c), n));
    this.hemi.groundColor.copy(lerpC(C(0xe8d4b0), C(0x161a28), n));
    this.hemi.intensity = THREE.MathUtils.lerp(1.25, 0.5, n);
    // sky: misty teal by day, a peach horizon at dusk, deep blue at night (darker top, lighter bottom)
    const top = lerpC(lerpC(C('#7fb0bf'), C('#6a78a8'), dusk), C('#0d1530'), n), bottom = lerpC(lerpC(C('#d6e8e4'), C('#f4b88e'), dusk), C('#28385a'), n);
    const key = top.getHexString() + bottom.getHexString();
    if (key !== this.skyKey) { this.skyKey = key; this.paintSky('#' + top.getHexString(), '#' + bottom.getHexString()); this.scene.fog.color.copy(bottom); }
    this.waterMat.color.copy(lerpC(lerpC(C('#64aeb6'), C('#7aa0b8'), dusk * 0.5), C('#25466a'), n));
    this.rimMat.color.copy(lerpC(lerpC(C('#bfe8e0'), C('#f2d2b8'), dusk * 0.6), C('#3a5a80'), n));
    const glow = Math.min(1, n * 1.15 + dusk * 0.45);
    this.glassMat.emissiveIntensity = glow * 2.4; this.glowMat.emissiveIntensity = glow * 2.2;
    for (const l of this.lampLights) l.intensity = glow * 16;
    this.renderer.toneMappingExposure = THREE.MathUtils.lerp(0.92, 1.15, n);
  }

  /* ---------------------------------------------------------------- base, water, plaza */
  buildIsland() {   // a thick rounded base: grass, then soil and stone strata, sitting in the water
    const I = this.S.island, y0 = 0;
    const stone = mesh(tint(rbox(I.w - 0.7, 1.8, I.d - 0.7, 0.8), '#c9b89e', {grad: 0.1, warm: 0.05, bands: [0.86, 0.94, 0.88, 0.97, 0.9, 1]}), CLAY);
    stone.position.y = y0 + 0.9; this.scene.add(stone);
    const soil = mesh(tint(rbox(I.w - 0.25, 1.15, I.d - 0.25, 0.75), '#a87a58', {grad: 0.12, warm: 0.04, bands: [0.88, 0.97, 0.9, 1]}), CLAY);
    soil.position.y = y0 + 1.8 + 0.5; this.scene.add(soil);
    const grass = mesh(tint(rbox(I.w, 0.55, I.d, 0.6), I.grass, {grad: 0.08, warm: 0, grain: 0.05}), CLAY, {cast: false});
    grass.position.y = y0 + 2.85 + 0.2; this.scene.add(grass);
    this.groundY = y0 + 3.3;
  }
  buildWater() {   // flat teal with slow swells, displaced in the vertex shader; flat shading gives the facets
    const W = this.S.water, g = new THREE.PlaneGeometry(420, 420, 160, 160); g.rotateX(-Math.PI / 2);
    const m = this.waterMat = new THREE.MeshStandardMaterial({color: C('#64aeb6'), roughness: 0.32, metalness: 0.05, flatShading: true});
    this.waterTime = {value: 0};
    m.onBeforeCompile = sh => {
      sh.uniforms.uTime = this.waterTime;
      sh.vertexShader = 'uniform float uTime;\n' + sh.vertexShader.replace('#include <begin_vertex>',
        '#include <begin_vertex>\n transformed.y += sin(position.x * 0.32 + uTime * 0.8) * 0.09 + cos(position.z * 0.27 + uTime * 1.05) * 0.08 + sin((position.x + position.z) * 0.6 + uTime * 1.6) * 0.025;');
    };
    this.water = mesh(g, m, {cast: false}); this.water.position.y = 0.55; this.scene.add(this.water);
    const I = this.S.island, rim = new THREE.Mesh(rbox(I.w + 2.4, 0.2, I.d + 2.4, 0.1, 4), this.rimMat = new THREE.MeshStandardMaterial({color: 0xbfe8e0, roughness: 0.4, transparent: true, opacity: 0.55, depthWrite: false}));
    rim.position.y = 0.56; rim.receiveShadow = true; this.scene.add(rim);
  }
  buildPlaza() {   // small slabs with narrow grout gaps and per-tile colour, over a darker grout bed
    const P = this.S.plaza, I = this.S.island, s = 0.72, gap = 0.06, y = this.groundY;
    const hx = I.w / 2 - 0.45, hz = I.d / 2 - 0.45, out = new THREE.Shape();   // grout bed = plaza disc clipped to the island
    for (let i = 0; i <= 96; i++) { const a = i / 96 * Math.PI * 2;
      const px = Math.max(-hx, Math.min(hx, P.center[0] + Math.cos(a) * (P.radius + 0.3))), pz = Math.max(-hz, Math.min(hz, P.center[1] + Math.sin(a) * (P.radius + 0.3)));
      i ? out.lineTo(px, -pz) : out.moveTo(px, -pz); }
    const bg = new THREE.ExtrudeGeometry(out, {depth: 0.1, bevelEnabled: true, bevelThickness: 0.04, bevelSize: 0.06, bevelSegments: 2}); bg.rotateX(-Math.PI / 2);
    const bed = mesh(tint(bg, '#9c8f7a', {grad: 0, warm: 0, faceVar: 0}), CLAY, {cast: false}); bed.position.y = y - 0.02; this.scene.add(bed);
    const spots = [];
    for (let row = 0, z = -I.d / 2 + 0.8; z < I.d / 2 - 0.5; z += s, row++) for (let x = -I.w / 2 + 0.8 + (row % 2) * s / 2; x < I.w / 2 - 0.5; x += s) {
      const dx = x - P.center[0], dz = z - P.center[1];
      if (dx * dx + dz * dz < P.radius * P.radius) spots.push([x, z]);
    }
    const g = rbox(s - gap, 0.16, s - gap, 0.05, 2);
    const inst = new THREE.InstancedMesh(g, new THREE.MeshStandardMaterial({roughness: 0.92}), spots.length);
    const m = new THREE.Matrix4(), col = new THREE.Color(), q = new THREE.Quaternion(), e = new THREE.Euler(), v = new THREE.Vector3(), sc = new THREE.Vector3(1, 1, 1);
    spots.forEach(([x, z], i) => {
      const r1 = rnd(x, z), r2 = rnd(z, x);
      e.set(0, (r1 - 0.5) * 0.06, 0); q.setFromEuler(e); v.set(x, y + 0.1 + r2 * 0.03, z);
      m.compose(v, q, sc); inst.setMatrixAt(i, m);
      col.set(P.colors[Math.floor(r1 * P.colors.length)]); col.offsetHSL((r2 - 0.5) * 0.02, 0, (rnd(x * 2, z * 3) - 0.5) * 0.07);
      inst.setColorAt(i, col);
    });
    inst.receiveShadow = true; inst.castShadow = false; this.scene.add(inst);
  }

  /* ---------------------------------------------------------------- buildings */
  buildBuilding(B) {
    const grp = new THREE.Group(); grp.position.set(...B.pos);
    const parts = B.parts.map(p => {
      const m = mesh(tint(rbox(p.size[0], p.size[1], p.size[2], 0.34), p.color), CLAY);
      m.position.set(p.at[0], this.groundY + p.at[1] + p.size[1] / 2, p.at[2]); grp.add(m);
      return {...p, y0: this.groundY + p.at[1]};
    });
    const face = (p, f, x, y, depth = 0) => f === 'east'
      ? new THREE.Vector3(p.at[0] + p.size[0] / 2 + depth, p.y0 + y, p.at[2] + x)
      : new THREE.Vector3(p.at[0] + x, p.y0 + y, p.at[2] + p.size[2] / 2 + depth);
    const add = (g, color, pos, f, opts) => { const m = mesh(tint(g, color, {grad: 0.08, warm: 0.04}), CLAY, opts); m.position.copy(pos); if (f === 'east') m.rotation.y = Math.PI / 2; grp.add(m); return m; };
    for (const r of B.roofs || []) {
      const p = parts[r.part], top = p.y0 + p.size[1];
      if (r.kind === 'ledge') { add(rbox(p.size[0] + 0.4, 0.32, p.size[2] + 0.4, 0.14), r.color, new THREE.Vector3(p.at[0], top + 0.1, p.at[2])); continue; }
      // gable: a wall-coloured gable prism under two thick roof slabs with eaves, a ridge cap
      const D = p.size[2] / 2, H = r.height, sh = new THREE.Shape();
      sh.moveTo(-D + 0.05, 0); sh.lineTo(D - 0.05, 0); sh.lineTo(0, H - 0.05); sh.lineTo(-D + 0.05, 0);
      const len = p.size[0] - 0.3, gable = new THREE.ExtrudeGeometry(sh, {depth: len, bevelEnabled: true, bevelThickness: 0.12, bevelSize: 0.12, bevelSegments: 3, curveSegments: 4});
      gable.translate(0, 0, -len / 2); gable.rotateY(Math.PI / 2);
      const gm = mesh(tint(gable, p.color, {grad: 0.05, warm: 0}), CLAY); gm.position.set(p.at[0], top - 0.05, p.at[2]); grp.add(gm);
      const ov = r.overhang + 0.15, half = D + ov, slope = Math.hypot(half, H) + 0.05, ang = Math.atan2(H, half), th = 0.3;
      for (const sgn of [-1, 1]) {
        const slab = mesh(tint(rbox(p.size[0] + ov * 2, th, slope, 0.12, 3), r.color, {grad: -0.1, warm: 0.02, faceVar: 0.02}), CLAY);
        slab.rotation.x = sgn * ang;
        slab.position.set(p.at[0], top + H / 2 - (ov / half) * H / 2 + th * 0.35, p.at[2] + sgn * half / 2);
        grp.add(slab);
      }
      const ridge = mesh(tint(new THREE.CapsuleGeometry(0.17, p.size[0] + ov * 2 - 0.3, 4, 10), r.color, {grad: 0, warm: 0, faceVar: 0}), CLAY);
      ridge.rotation.z = Math.PI / 2; ridge.position.set(p.at[0], top + H + 0.14, p.at[2]); grp.add(ridge);
      if (r.chimney) {
        const cx = p.at[0] + r.chimney, cz = p.at[2] - half * 0.35, ch = H * 0.62 + 1.1;
        add(rbox(0.7, ch, 0.7, 0.14), p.color, new THREE.Vector3(cx, top + ch / 2 + 0.2, cz));
        add(rbox(0.92, 0.2, 0.92, 0.08), '#6a5a52', new THREE.Vector3(cx, top + ch + 0.3, cz));
      }
    }
    for (const w of B.windows || []) {   // protruding frame bars + sill, the dark glass recessed inside
      const p = parts[w.part], f = w.face, fr = 0.16, dp = 0.2, white = '#fff7ee';
      add(rbox(w.w + fr * 2, fr, dp, 0.06, 3), white, face(p, f, w.x, w.y + w.h / 2 + fr / 2, dp / 2), f, {cast: true});
      add(rbox(fr, w.h, dp, 0.06, 3), white, face(p, f, w.x - w.w / 2 - fr / 2, w.y, dp / 2), f, {cast: true});
      add(rbox(fr, w.h, dp, 0.06, 3), white, face(p, f, w.x + w.w / 2 + fr / 2, w.y, dp / 2), f, {cast: true});
      add(rbox(w.w + 0.5, 0.16, 0.42, 0.06, 3), white, face(p, f, w.x, w.y - w.h / 2 - 0.08, 0.2), f);
      const glass = mesh(rbox(w.w, w.h, 0.06, 0.04, 2), this.glassMat, {cast: false});
      glass.position.copy(face(p, f, w.x, w.y, 0.015)); if (f === 'east') glass.rotation.y = Math.PI / 2; grp.add(glass);
      if (w.w > 1.4) add(rbox(0.08, w.h, 0.1, 0.03, 2), white, face(p, f, w.x, w.y, 0.06), f, {cast: false});   // mullion
    }
    if (B.door) {   // a recessed doorway: deep frame, the door set back, a small canopy on brackets
      const D = B.door, p = parts[D.part], dp = 0.28, fr = 0.22;
      add(rbox(D.w + fr * 2, fr, dp, 0.08), '#e9dccb', face(p, D.face, D.x, D.h + fr / 2, dp / 2), D.face);
      add(rbox(fr, D.h, dp, 0.08), '#e9dccb', face(p, D.face, D.x - D.w / 2 - fr / 2, D.h / 2, dp / 2), D.face);
      add(rbox(fr, D.h, dp, 0.08), '#e9dccb', face(p, D.face, D.x + D.w / 2 + fr / 2, D.h / 2, dp / 2), D.face);
      add(rbox(D.w, D.h, 0.08, 0.06, 3), D.color, face(p, D.face, D.x, D.h / 2, 0.02), D.face, {cast: false});
      const knob = mesh(new THREE.SphereGeometry(0.07, 10, 8), this.glowMat); knob.position.copy(face(p, D.face, D.x + D.w * 0.3, D.h * 0.48, 0.12)); grp.add(knob);
      add(rbox(D.w + 1.0, 0.16, 0.9, 0.07), '#7a5a4c', face(p, D.face, D.x, D.h + 0.55, 0.45), D.face);
      for (const sx of [-1, 1]) add(rbox(0.1, 0.4, 0.5, 0.04, 2), '#7a5a4c', face(p, D.face, D.x + sx * (D.w / 2 + 0.3), D.h + 0.3, 0.25), D.face, {cast: false});
      add(rbox(D.w + 0.9, 0.2, 0.8, 0.08), '#e6dccb', face(p, D.face, D.x, 0.1, 0.4), D.face);
    }
    if (B.awning) {   // striped slats, tilted toward the street, finished with a scalloped (wavy-cut) edge
      const A = B.awning, p = parts[A.part], n = 9, sw = A.w / n, aw = new THREE.Group();
      for (let i = 0; i < n; i++) {
        const s = mesh(tint(rbox(sw * 0.99, 0.1, A.depth, 0.04, 2), A.colors[i % 2], {grad: 0, warm: 0, faceVar: 0}), CLAY);
        s.position.set(-A.w / 2 + sw * (i + 0.5), 0, A.depth / 2); aw.add(s);
        const sc = mesh(tint(new THREE.CylinderGeometry(sw / 2, sw / 2, 0.08, 16, 1, false, 0, Math.PI), A.colors[i % 2], {grad: 0, warm: 0, faceVar: 0}), CLAY);
        sc.rotation.set(Math.PI / 2, Math.PI / 2, 0); sc.position.set(-A.w / 2 + sw * (i + 0.5), 0, A.depth); aw.add(sc);
      }
      aw.rotation.x = 0.34; aw.position.copy(face(p, 'front', A.x, A.y, 0.05)); grp.add(aw);
    }
    if (B.sign) {
      const G = B.sign, p = parts[G.part];
      add(rbox(G.w, G.h, 0.16, 0.08), G.color, face(p, 'front', G.x, G.y, 0.1), 'front');
      const strip = mesh(rbox(G.w - 0.3, 0.12, 0.05, 0.04, 2), this.glowMat); strip.position.copy(face(p, 'front', G.x, G.y, 0.19)); grp.add(strip);
    }
    this.scene.add(grp);
  }

  buildProps() {
    const P = this.S.props, y = this.groundY;
    for (const [x, z, s] of P.trees) {   // canopy = four merged spheres, each its own green, with vertex grain
      const g = new THREE.Group(); g.position.set(x, y, z); g.scale.setScalar(s);
      const trunk = mesh(tint(new THREE.CylinderGeometry(0.15, 0.24, 1.4, 12), '#8f6446', {grad: 0.2}), CLAY); trunk.position.y = 0.7; g.add(trunk);
      [[0, 2.15, 0, 1.1, '#8fcf7a'], [0.62, 1.8, 0.25, 0.82, '#7cc46c'], [-0.5, 1.88, -0.22, 0.86, '#9ad884'], [0.1, 2.75, -0.15, 0.7, '#a5de8c']].forEach(([cx, cy, cz, r, c], k) => {
        const b = mesh(tint(new THREE.IcosahedronGeometry(r, 3), c, {grad: 0.22, warm: 0, faceVar: 0, grain: 0.08}), CLAY); b.position.set(cx, cy, cz); g.add(b);
      });
      this.scene.add(g);
    }
    for (const [x, z] of P.lamps) {
      add3(this.scene, mesh(tint(new THREE.CylinderGeometry(0.07, 0.11, 2.6, 10), '#3c3f4a', {grad: 0.1}), CLAY), x, y + 1.3, z);
      add3(this.scene, mesh(tint(new THREE.CylinderGeometry(0.2, 0.26, 0.1, 12), '#3c3f4a'), CLAY), x, y + 2.95, z);
      const bulb = mesh(new THREE.SphereGeometry(0.22, 16, 12), this.glowMat, {cast: false}); bulb.position.set(x, y + 2.75, z); this.scene.add(bulb);
      const l = new THREE.PointLight(0xffc27a, 0, 9, 1.6); l.position.set(x, y + 2.7, z); this.scene.add(l); this.lampLights.push(l);
    }
    for (const [x, z, rot] of P.benches) {
      const g = new THREE.Group(); g.position.set(x, y, z); g.rotation.y = rot;
      [[0, 0.5, 0, 1.8, 0.14, 0.55, '#b98258'], [0, 0.82, -0.24, 1.8, 0.45, 0.12, '#b98258'], [-0.7, 0.25, 0, 0.12, 0.5, 0.45, '#4a4550'], [0.7, 0.25, 0, 0.12, 0.5, 0.45, '#4a4550']]
        .forEach(([px, py, pz, w, h, d, c]) => add3(g, mesh(tint(rbox(w, h, d, 0.05, 3), c), CLAY), px, py, pz));
      this.scene.add(g);
    }
    for (const [x, z] of P.tables) {
      const g = new THREE.Group(); g.position.set(x, y, z);
      add3(g, mesh(tint(new THREE.CylinderGeometry(0.55, 0.55, 0.08, 24), '#fff4ea', {grad: 0}), CLAY), 0, 0.75, 0);
      add3(g, mesh(tint(new THREE.CylinderGeometry(0.06, 0.06, 0.75, 8), '#3c3f4a'), CLAY), 0, 0.37, 0);
      add3(g, mesh(tint(new THREE.CylinderGeometry(0.04, 0.04, 2.1, 8), '#e8e0d0'), CLAY), 0, 1.4, 0);
      add3(g, mesh(tint(new THREE.ConeGeometry(1.25, 0.55, 20), '#e8645a', {grad: -0.15, warm: 0}), CLAY), 0, 2.35, 0);
      this.scene.add(g);
    }
    if (P.fountain) {
      const [x, z] = P.fountain, g = new THREE.Group(); g.position.set(x, y, z);
      add3(g, mesh(tint(new THREE.CylinderGeometry(1.6, 1.75, 0.55, 32), '#e6dccb'), CLAY), 0, 0.27, 0);
      add3(g, mesh(new THREE.CylinderGeometry(1.35, 1.35, 0.05, 32), new THREE.MeshStandardMaterial({color: 0x7fd0e2, roughness: 0.2}), {cast: false}), 0, 0.52, 0);
      add3(g, mesh(tint(new THREE.CylinderGeometry(0.22, 0.3, 1.3, 16), '#e6dccb'), CLAY), 0, 1.0, 0);
      const bowl = mesh(tint(new THREE.SphereGeometry(0.55, 24, 10, 0, Math.PI * 2, 0, Math.PI / 2), '#e6dccb'), CLAY); bowl.rotation.x = Math.PI; bowl.position.y = 1.75; g.add(bowl);
      this.scene.add(g);
    }
    // a soft contact-shadow blob texture for the residents
    const c = document.createElement('canvas'); c.width = c.height = 64; const x = c.getContext('2d'), gr = x.createRadialGradient(32, 32, 2, 32, 32, 31);
    gr.addColorStop(0, 'rgba(30,24,40,.55)'); gr.addColorStop(1, 'rgba(30,24,40,0)'); x.fillStyle = gr; x.fillRect(0, 0, 64, 64);
    this.blobTex = new THREE.CanvasTexture(c);
  }

  /* ---------------------------------------------------------------- residents */
  // sphere head on a rounded body in the resident's colour, a soft blob underneath, a little bounce
  resident(id, color) {
    let r = this.residents.get(id);
    if (r) return r;
    const g = new THREE.Group();
    const body = mesh(tint(new THREE.CapsuleGeometry(0.36, 0.48, 8, 20), color, {grad: 0.18, warm: 0.05, faceVar: 0}), CLAY); body.position.y = 0.72; g.add(body);
    const head = mesh(tint(new THREE.SphereGeometry(0.34, 24, 18), '#f6d5b8', {grad: 0.1, warm: 0, faceVar: 0}), CLAY); head.position.y = 1.56; g.add(head);
    const hair = mesh(tint(new THREE.SphereGeometry(0.355, 24, 12, 0, Math.PI * 2, 0, Math.PI * 0.5), '#' + C(color).multiplyScalar(0.45).getHexString(), {grad: 0, warm: 0, faceVar: 0}), CLAY);
    hair.position.y = 1.6; hair.rotation.x = -0.25; g.add(hair);
    for (const sx of [-0.12, 0.12]) { const eye = mesh(new THREE.SphereGeometry(0.04, 8, 6), new THREE.MeshStandardMaterial({color: 0x2a2430}), {cast: false}); eye.position.set(sx, 1.58, 0.31); g.add(eye); }
    const blob = new THREE.Mesh(new THREE.PlaneGeometry(1.5, 1.5), new THREE.MeshBasicMaterial({map: this.blobTex, transparent: true, depthWrite: false}));
    blob.rotation.x = -Math.PI / 2; blob.position.y = this.groundY + 0.2; blob.renderOrder = 2; this.scene.add(blob);
    g.position.set(0, this.groundY + 0.13, 0); this.scene.add(g);
    r = {id, g, blob, x: 0, z: 0, tx: 0, tz: 0, phase: Math.random() * 6, moving: false};
    this.residents.set(id, r); return r;
  }
  place(id, color, [x, z], snap) {
    const r = this.resident(id, color);
    r.tx = x; r.tz = z; if (snap || r.fresh === undefined) { r.x = x; r.z = z; r.fresh = false; }
  }
  drop(id) { const r = this.residents.get(id); if (r) { this.scene.remove(r.g, r.blob); this.residents.delete(id); } }
  headScreen(id) {
    const r = this.residents.get(id); if (!r) return null;
    const v = new THREE.Vector3(r.g.position.x, r.g.position.y + 2.15, r.g.position.z).project(this.camera);
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
    cv.addEventListener('dblclick', () => { const C_ = this.S.camera; this.cam.target.set(...C_.target); this.cam.dist = C_.distance; });
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
    if (this.composer) { this.composer.setPixelRatio(this.renderer.getPixelRatio()); this.composer.setSize(w, h); }
  }

  /* ---------------------------------------------------------------- frame */
  frame() {
    this.timer.update(); const dt = Math.min(0.05, this.timer.getDelta()); this.t += dt; this.frames = (this.frames || 0) + 1;
    this.waterTime.value = this.t;
    for (const r of this.residents.values()) {
      const dx = r.tx - r.x, dz = r.tz - r.z, d = Math.hypot(dx, dz), step = 1.7 * dt;
      r.moving = d > 0.05;
      if (r.moving) { const k = Math.min(1, step / d); r.x += dx * k; r.z += dz * k; r.g.rotation.y = Math.atan2(dx, dz); }
      const bounce = r.moving ? Math.abs(Math.sin(this.t * 9 + r.phase)) * 0.14 : Math.sin(this.t * 2 + r.phase) * 0.015;
      r.g.position.set(r.x, this.groundY + 0.13 + bounce, r.z);
      r.g.scale.set(1, r.moving ? 1 - bounce * 0.4 : 1, 1);
      r.blob.position.set(r.x, this.groundY + 0.2, r.z); r.blob.scale.setScalar(1 - bounce * 1.2);
    }
    this.updateCamera();
    this.composer.render();
    if (this.onFrame) this.onFrame();
  }
  destroy() { this.alive = false; cancelAnimationFrame(this.raf); clearInterval(this.fpsT); this.ro.disconnect(); this.renderer.dispose(); }
}
function add3(parent, m, x, y, z) { m.position.set(x, y, z); parent.add(m); return m; }
