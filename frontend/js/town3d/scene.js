// The whole town in 3-D (the 小鎮 view): an organic island on the water with the seven
// places, paths, plaza, vegetation and residents -- all generated from town.json.
// Pipeline (kept from the validation slice): ACES + sRGB, PCF soft shadows (radius), GTAO
// through EffectComposer, vertex-colour clay, gradient sky + fog, a degrade step when the
// frame rate sags. This class draws; townmap.js decides who stands where.
import * as THREE from 'three';
import {EffectComposer} from 'three/addons/postprocessing/EffectComposer.js';
import {RenderPass} from 'three/addons/postprocessing/RenderPass.js';
import {GTAOPass} from 'three/addons/postprocessing/GTAOPass.js';
import {OutputPass} from 'three/addons/postprocessing/OutputPass.js';
import {phaseOf} from '../names.js';
import {C, lerpC, rnd, tint, rbox, mesh, CLAY, Batch, stops} from './clay.js';
import {writer, PROPS} from './kit.js';
import {buildBuilding, moundY} from './buildings.js';
import {Figure} from './figure.js';

const DEG = Math.PI / 180;
const radial = (inner, outer) => {   // a soft round texture (light pools, blobs, puffs)
  const c = document.createElement('canvas'); c.width = c.height = 64; const x = c.getContext('2d'), g = x.createRadialGradient(32, 32, 0, 32, 32, 32);
  g.addColorStop(0, inner); g.addColorStop(1, outer); x.fillStyle = g; x.fillRect(0, 0, 64, 64);
  return new THREE.CanvasTexture(c);
};

export class TownScene {
  constructor(el, spec, figSpec, {onPick, onFrame} = {}) {
    this.el = el; this.S = spec; this.FS = figSpec; this.onPick = onPick; this.onFrame = onFrame;
    this.R = spec.render; this.active = true; this.alive = true; this.quality = 'full'; this.slow = 0; this.t = 0;
    this.figures = new Map(); this.puffs = []; this.pulses = [];
    const r = this.renderer = new THREE.WebGLRenderer({antialias: false, powerPreference: 'high-performance'});
    r.setPixelRatio(Math.min(this.R.maxPixelRatio, window.devicePixelRatio || 1));
    r.outputColorSpace = THREE.SRGBColorSpace; r.toneMapping = THREE.ACESFilmicToneMapping; r.toneMappingExposure = this.R.exposure;
    r.shadowMap.enabled = true; r.shadowMap.type = THREE.PCFShadowMap;   // PCFSoftShadowMap is folded into PCF (+ radius) in r186
    el.appendChild(r.domElement); r.domElement.classList.add('world-canvas');
    this.scene = new THREE.Scene();
    this.skyCanvas = document.createElement('canvas'); this.skyCanvas.width = 2; this.skyCanvas.height = 256;
    this.sky = new THREE.CanvasTexture(this.skyCanvas); this.sky.colorSpace = THREE.SRGBColorSpace; this.scene.background = this.sky;
    this.envCanvas = document.createElement('canvas'); this.envCanvas.width = 64; this.envCanvas.height = 32;
    this.env = new THREE.CanvasTexture(this.envCanvas); this.env.mapping = THREE.EquirectangularReflectionMapping; this.env.colorSpace = THREE.SRGBColorSpace;
    this.scene.fog = new THREE.Fog(0xcfe3e0, ...this.R.fog);
    const cam = spec.camera;
    this.camera = new THREE.PerspectiveCamera(cam.fov, 1, 0.5, 600);
    this.cam = {target: new THREE.Vector3(...cam.target), dist: cam.distance, az: cam.azimuth * DEG, el: cam.elevation * DEG};
    this.goal = null;
    this.mats = {
      glass: new THREE.MeshStandardMaterial({color: 0x2e2a3c, roughness: 0.25, metalness: 0.1, emissive: 0xffc06a, emissiveIntensity: 0}),
      glow: new THREE.MeshStandardMaterial({color: 0xfff1d0, emissive: 0xffd28a, emissiveIntensity: 0, roughness: 0.4}),
      screen: new THREE.MeshStandardMaterial({color: 0x24384a, emissive: 0x7fc8ff, emissiveIntensity: 0.4, roughness: 0.3}),
    };
    this.greens = spec.props.treeGreens; this.flowers = spec.props.flowerColors;
    this.buildLights(); this.buildIsland(); this.buildWater(); this.buildPlaza();
    this.buildings = {};
    for (const B of spec.buildings) {
      const b = buildBuilding(B, {groundY: this.groundY, mats: this.mats, greens: this.greens, flowers: this.flowers});
      this.scene.add(b.group); b.group.updateMatrixWorld(true);
      b.door = b.doorLocal.clone().applyMatrix4(b.group.matrixWorld).setY(this.groundY);
      this.buildings[B.id] = b;
    }
    this.buildPaths(); this.buildProps(); this.buildInstallation(); this.buildFx();
    this.buildComposer();
    this.setupCamera(); this.resize();
    this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(el);
    this.timer = new THREE.Timer();
    this.fpsT = setInterval(() => this.watchFps(), 1000);
    const loop = () => { if (!this.alive) return; this.raf = requestAnimationFrame(loop); if (this.active) this.frame(); };
    this.raf = requestAnimationFrame(loop);
  }

  /* ---------------------------------------------------------------- pipeline */
  buildComposer() {
    const rt = new THREE.WebGLRenderTarget(4, 4, {samples: 4, type: THREE.HalfFloatType});
    const c = this.composer = new EffectComposer(this.renderer, rt);
    c.addPass(new RenderPass(this.scene, this.camera));
    this.ao = new GTAOPass(this.scene, this.camera, 4, 4);
    this.ao.output = GTAOPass.OUTPUT.Default; this.ao.blendIntensity = 1.0;
    this.ao.updateGtaoMaterial(this.R.ao);
    this.ao.updatePdMaterial({lumaPhi: 10, depthPhi: 2, normalPhi: 3, radius: 6, rings: 2, samples: 16});
    // AO only for solid geometry: sprites, decals and other transparent bits stay out of its depth/normal pass
    this.ao._overrideVisibility = function () {
      const cache = this._visibilityCache;
      this.scene.traverse(o => { if (o.visible && (o.isPoints || o.isLine || o.isSprite || (o.material && o.material.transparent))) { o.visible = false; cache.push(o); } });
    };
    c.addPass(this.ao); c.addPass(new OutputPass());
  }
  // the degrade step: below R.degrade.fps for R.degrade.seconds -> no AO, pixel ratio 1
  watchFps() {
    const f = this.frames || 0; this.frames = 0; this.fps = f;
    if (this.quality !== 'full' || !this.active || document.hidden) { this.slow = 0; return; }
    this.slow = f < this.R.degrade.fps ? this.slow + 1 : 0;
    if (this.slow >= this.R.degrade.seconds) this.degrade();
  }
  degrade() { this.quality = 'reduced'; this.ao.enabled = false; this.renderer.setPixelRatio(1); this.resize(); }

  /* ---------------------------------------------------------------- light, sky, time */
  buildLights() {
    const Sn = this.S.sun;
    this.hemi = new THREE.HemisphereLight(C(Sn.hemiSky.day), C(Sn.hemiGround.day), Sn.hemi); this.scene.add(this.hemi);
    const s = this.sun = new THREE.DirectionalLight(C(Sn.day), Sn.intensity), E = this.R.shadowExtent;
    s.castShadow = true; s.shadow.mapSize.set(this.R.shadowMap, this.R.shadowMap);
    Object.assign(s.shadow.camera, {left: -E, right: E, top: E, bottom: -E, near: 1, far: 240});
    s.shadow.bias = this.R.shadowBias; s.shadow.normalBias = this.R.shadowNormalBias; s.shadow.radius = this.R.shadowRadius;
    this.scene.add(s, s.target);
    // a fixed pool of warm point lights for lit interiors at night (fixed count: no shader recompiles)
    this.roomLights = Array.from({length: this.R.interiorLights}, () => { const l = new THREE.PointLight(0xffc887, 0, 9, 1.5); this.scene.add(l); return l; });
    this.pulseLight = new THREE.PointLight(0xffd27a, 0, 10, 1.4); this.scene.add(this.pulseLight);
  }
  paint(cv, top, bottom, horizon = 0.62) {
    const x = cv.getContext('2d'), g = x.createLinearGradient(0, 0, 0, cv.height);
    g.addColorStop(0, top); g.addColorStop(horizon, bottom); g.addColorStop(1, bottom);
    x.fillStyle = g; x.fillRect(0, 0, cv.width, cv.height);
  }
  setTime(minute) {
    const {n, dusk, h} = phaseOf(minute), Sn = this.S.sun;
    this.phase = {n, dusk, h};
    const t = Math.max(0, Math.min(1, (h - 6) / 12)), up = Math.sin(Math.PI * t), morning = h < 11 ? Math.max(0, 1 - (h - 6) / 4) : 0;
    let col = lerpC(C(Sn.day), C(Sn.morning), morning * 0.75); col = lerpC(col, C(Sn.dusk), dusk); col = lerpC(col, C(Sn.night), n);
    const D = Sn.distance, sunPos = n > 0.6 ? new THREE.Vector3(-0.5 * D, 0.8 * D, 0.45 * D) : new THREE.Vector3(Math.cos(Math.PI * t) * D, 12 + up * D * 0.85, D * 0.55);
    this.sunOffset = sunPos;
    this.sun.color.copy(col); this.sun.intensity = THREE.MathUtils.lerp(Sn.intensity, Sn.intensityNight, n) * (1 - dusk * 0.12);
    this.hemi.color.copy(stops({day: Sn.hemiSky.day, dusk: Sn.hemiSky.dusk, night: Sn.hemiSky.night}, dusk * 0.6, n));
    this.hemi.groundColor.copy(stops(Sn.hemiGround, 0, n)); this.hemi.intensity = THREE.MathUtils.lerp(Sn.hemi, Sn.hemiNight, n);
    // sky + fog + reflection map: quantized so 20x speed doesn't repaint every frame
    const q = this.S.sky.steps, qd = Math.round(dusk * q) / q, qn = Math.round(n * q) / q;
    const top = stops(this.S.sky.top, qd, qn), bottom = stops(this.S.sky.bottom, qd, qn), key = top.getHexString() + bottom.getHexString();
    if (key !== this.skyKey) {
      this.skyKey = key; const T = '#' + top.getHexString(), Bm = '#' + bottom.getHexString();
      this.paint(this.skyCanvas, T, Bm); this.sky.needsUpdate = true; this.scene.fog.color.copy(bottom);
      this.paint(this.envCanvas, T, Bm, 0.5); this.env.needsUpdate = true;
    }
    const Wt = this.S.water;
    this.waterMat.color.copy(stops(Wt.color, dusk * 0.7, n)); this.rimMat.color.copy(stops(Wt.rim, dusk * 0.6, n));
    const glow = this.glow = Math.min(1, n * 1.15 + dusk * 0.45);
    this.mats.glow.emissiveIntensity = glow * 2.2;
    this.lampPools.material.opacity = glow * 0.55;
    this.renderer.toneMappingExposure = THREE.MathUtils.lerp(this.R.exposure, this.R.exposureNight, n);
    this.updateInstallation();
  }

  /* ---------------------------------------------------------------- island, water */
  outline() {   // polar outline grown around the blobs, smoothed, with the west bank cut straight-ish
    const I = this.S.island, N = I.points, rs = [];
    for (let i = 0; i < N; i++) {
      const a = i / N * Math.PI * 2, dx = Math.cos(a), dz = Math.sin(a); let best = 4;
      for (const [cx, cz, R] of I.blobs) {
        const b = dx * cx + dz * cz, disc = b * b - (cx * cx + cz * cz) + (R + I.margin) ** 2;
        if (disc >= 0) best = Math.max(best, b + Math.sqrt(disc));
      }
      rs.push(best);
    }
    const sm = rs.map((_, i) => { let s = 0; for (let k = -I.smooth; k <= I.smooth; k++) s += rs[(i + k + N) % N]; return s / (2 * I.smooth + 1); });
    const pts = sm.map((r, i) => {
      const a = i / N * Math.PI * 2; r += Math.sin(a * 5 + 1.3) * I.noise * 0.6 + Math.sin(a * 11 + 0.4) * I.noise * 0.4;
      let x = Math.cos(a) * r, z = Math.sin(a) * r;
      if (x < I.riverX) { const k = I.riverX / x; x = I.riverX + Math.sin(z * 0.3) * I.bankNoise; z *= Math.min(1, 0.35 + 0.65 * k); }
      return new THREE.Vector2(x, z);
    });
    return pts;
  }
  insideIsland(x, z, pad = 0) {   // point-in-polygon on the outline (inset by pad)
    const P = this.islandPts; let inside = false;
    for (let i = 0, j = P.length - 1; i < P.length; j = i++) {
      const a = P[i], b = P[j];
      if ((a.y > z) !== (b.y > z) && x < (b.x - a.x) * (z - a.y) / (b.y - a.y) + a.x) inside = !inside;
    }
    if (!inside || !pad) return inside;
    for (let k = 0; k < 8; k++) { const a = k / 8 * Math.PI * 2; if (!this.insideIsland(x + Math.cos(a) * pad, z + Math.sin(a) * pad)) return false; }
    return true;
  }
  buildIsland() {
    const I = this.S.island, pts = this.islandPts = this.outline(), L = I.layers;
    const layer = (inset, h, bevel, color, opts, y0) => {
      const sh = new THREE.Shape(pts.map(p => { const r = p.length(); return new THREE.Vector2(p.x * (r - inset) / r, -p.y * (r - inset) / r); }));
      const g = new THREE.ExtrudeGeometry(sh, {depth: Math.max(0.05, h - 2 * bevel), bevelEnabled: true, bevelThickness: bevel, bevelSize: bevel, bevelSegments: 4, curveSegments: 2});
      g.rotateX(-Math.PI / 2); g.translate(0, y0 + bevel, 0); g.computeVertexNormals();
      const m = mesh(tint(g, color, opts), CLAY); this.scene.add(m); return m;
    };
    layer(0.9, L.stone, 0.55, I.stone, {grad: 0.1, warm: 0.05, faceVar: 0.02, grain: 0.04, bands: [0.86, 0.94, 0.88, 0.97, 0.9, 1]}, 0);
    layer(0.45, L.soil, 0.4, I.soil, {grad: 0.12, warm: 0.04, faceVar: 0.02, grain: 0.04, bands: [0.88, 0.97, 0.9, 1]}, L.stone);
    const grass = layer(0, L.grass, 0.24, I.grass, {grad: 0.08, warm: 0, faceVar: 0, grain: 0.06}, L.stone + L.soil);
    grass.castShadow = false;
    this.groundY = L.stone + L.soil + L.grass;
    // the far bank across the river (west) and a little pier
    const F = this.S.farBank, fb = new Batch(), fp = writer(fb, this.mats), len = F.z[1] - F.z[0];
    fp(rbox(F.w, 2.6, len, 1.0), I.stone, [F.x, 1.3, (F.z[0] + F.z[1]) / 2], undefined, 1, {grad: 0.1, warm: 0.05, faceVar: 0.02, grain: 0.04});
    fp(rbox(F.w - 0.6, 0.6, len - 0.6, 0.3), I.grass, [F.x, 2.75, (F.z[0] + F.z[1]) / 2], undefined, 1, {grad: 0.08, warm: 0, faceVar: 0, grain: 0.06});
    for (let i = 0; i < F.trees; i++) PROPS.tree(fp.at(F.x + (rnd(i, 3) - 0.5) * (F.w - 4), 3.0, F.z[0] + 6 + i * (len - 12) / F.trees + (rnd(i, 7) - 0.5) * 3, 0, 0.9 + rnd(i, 9) * 0.5), {greens: this.greens, seed: i});
    const Pr = this.S.pier, [px, pz] = Pr.at;
    for (let i = 0; i < Pr.len / 0.5; i++) fp(rbox(0.46, 0.14, Pr.w, 0.05, 2), Pr.color, [px - i * 0.5, this.S.water.y + 0.75, pz], undefined, 1, {grad: 0, warm: 0, faceVar: 0, grain: 0.06});
    for (let i = 0; i <= Pr.len / 2; i++) for (const s of [-1, 1]) fp(new THREE.CylinderGeometry(0.12, 0.12, 1.6, 10), '#7a5a40', [px - i * 2, this.S.water.y + 0.2, pz + s * (Pr.w / 2 - 0.1)]);
    fb.build(this.scene);
  }
  buildWater() {
    const W = this.S.water, g = new THREE.PlaneGeometry(W.size, W.size, W.segments, W.segments); g.rotateX(-Math.PI / 2);
    const m = this.waterMat = new THREE.MeshStandardMaterial({color: C(W.color.day), roughness: W.roughness, metalness: 0.05, flatShading: true, envMap: this.env, envMapIntensity: W.reflect});
    this.waterTime = {value: 0};
    const waves = W.waves.map(([f, s, a], i) => `${['sin', 'cos', 'sin'][i % 3]}(${i === 2 ? '(position.x + position.z)' : i ? 'position.z' : 'position.x'} * ${f.toFixed(3)} + uTime * ${s.toFixed(3)}) * ${a.toFixed(3)}`).join(' + ');
    m.onBeforeCompile = sh => {
      sh.uniforms.uTime = this.waterTime;
      sh.vertexShader = 'uniform float uTime;\n' + sh.vertexShader.replace('#include <begin_vertex>', `#include <begin_vertex>\n transformed.y += ${waves};`);
    };
    this.water = mesh(g, m, {cast: false}); this.water.position.y = W.y; this.scene.add(this.water);
    // the shallow rim around the island: a pale band following the outline
    const sh = new THREE.Shape(this.islandPts.map(p => { const r = p.length(); return new THREE.Vector2(p.x * (r + 2.2) / r, -p.y * (r + 2.2) / r); }));
    const rg = new THREE.ShapeGeometry(sh, 2); rg.rotateX(-Math.PI / 2);
    this.rimMat = new THREE.MeshStandardMaterial({color: C(W.rim.day), roughness: 0.4, transparent: true, opacity: 0.5, depthWrite: false});
    const rim = new THREE.Mesh(rg, this.rimMat); rim.position.y = W.y + 0.02; rim.receiveShadow = true; this.scene.add(rim);
  }

  /* ---------------------------------------------------------------- plaza, paths */
  buildPlaza() {
    const P = this.S.plaza, [cx, cz] = P.center, s = P.tile, y = this.groundY;
    const bed = new THREE.CylinderGeometry(P.radius + 0.35, P.radius + 0.45, 0.16, 72);
    const bm = mesh(tint(bed, P.grout, {grad: 0, warm: 0, faceVar: 0, grain: 0.03}), CLAY, {cast: false}); bm.position.set(cx, y + 0.0, cz); this.scene.add(bm);
    const spots = [];
    for (let row = 0, z = cz - P.radius; z <= cz + P.radius; z += s, row++) for (let x = cx - P.radius + (row % 2) * s / 2; x <= cx + P.radius; x += s) {
      const dx = x - cx, dz = z - cz; if (dx * dx + dz * dz < (P.radius - 0.2) ** 2 && Math.hypot(dx, dz) > 2.6) spots.push([x, z]);
    }
    const g = rbox(s - P.gap, 0.16, s - P.gap, 0.05, 2);
    const inst = new THREE.InstancedMesh(g, new THREE.MeshStandardMaterial({roughness: 0.92}), spots.length);
    const m = new THREE.Matrix4(), col = new THREE.Color(), q = new THREE.Quaternion(), e = new THREE.Euler(), v = new THREE.Vector3(), sc = new THREE.Vector3(1, 1, 1);
    spots.forEach(([x, z], i) => {
      const r1 = rnd(x, z), r2 = rnd(z, x);
      e.set(0, (r1 - 0.5) * 0.06, 0); q.setFromEuler(e); v.set(x, y + 0.1 + r2 * 0.03, z); m.compose(v, q, sc); inst.setMatrixAt(i, m);
      col.set(P.colors[Math.floor(r1 * P.colors.length)]); col.offsetHSL((r2 - 0.5) * 0.02, 0, (rnd(x * 2, z * 3) - 0.5) * 0.07); inst.setColorAt(i, col);
    });
    inst.receiveShadow = true; this.scene.add(inst);
    // the fountain at the centre
    const fb = new Batch(), put = writer(fb, this.mats).at(cx, y, cz);
    put(new THREE.CylinderGeometry(2.4, 2.6, 0.6, 40), '#e6dccb', [0, 0.3, 0]);
    put(new THREE.CylinderGeometry(0.32, 0.42, 1.6, 18), '#e6dccb', [0, 1.1, 0]);
    put(new THREE.SphereGeometry(0.8, 28, 10, 0, Math.PI * 2, 0, Math.PI / 2), '#e6dccb', [0, 1.95, 0], [Math.PI, 0, 0]);
    put(new THREE.SphereGeometry(0.3, 16, 12), '#e6dccb', [0, 2.15, 0]);
    fb.build(this.scene);
    this.fountainWater = new THREE.Mesh(new THREE.CylinderGeometry(2.1, 2.1, 0.05, 40), new THREE.MeshStandardMaterial({color: 0x7fd0e2, roughness: 0.15, metalness: 0.1, envMap: this.env, envMapIntensity: 0.6}));
    this.fountainWater.position.set(cx, y + 0.56, cz); this.fountainWater.receiveShadow = true; this.scene.add(this.fountainWater);
    this.plazaC = new THREE.Vector3(cx, y, cz);
  }
  // paths: from each door through a bend to the plaza edge; light paving bands of varying width
  buildPaths() {
    const P = this.S.paths, R = this.S.plaza.radius - 0.4, y = this.groundY + 0.035;
    this.walk = {};
    const ribbons = [];
    for (const b of Object.values(this.buildings)) {
      const d = b.door, toC = new THREE.Vector3(this.plazaC.x - d.x, 0, this.plazaC.z - d.z).normalize();
      const edge = this.plazaC.clone().addScaledVector(toC, -R), bend = b.B.bend || [0, 0];
      const mid = d.clone().lerp(edge, 0.45).add(new THREE.Vector3(bend[0], 0, bend[1]));
      const pts = [d.clone(), d.clone().lerp(mid, 0.5).add(new THREE.Vector3(0, 0, 0.6)), mid, edge];
      const curve = new THREE.CatmullRomCurve3(pts.map(p => p.clone().setY(y)));
      this.walk[b.id] = curve.getSpacedPoints(Math.max(6, Math.round(curve.getLength() / 1.2)));
      ribbons.push({curve, w: P.width * (b.B.kind === 'park' ? 0.9 : 1), seed: b.B.pos[0]});
    }
    for (const x of P.extra) ribbons.push({curve: new THREE.CatmullRomCurve3(x.pts.map(([px, pz]) => new THREE.Vector3(px, y, pz))), w: x.w, seed: x.pts[0][0]});
    const pos = [], col = [], c = new THREE.Color();
    for (const {curve, w, seed} of ribbons) {
      const n = Math.max(8, Math.round(curve.getLength() / 0.5)), pts = curve.getSpacedPoints(n);
      for (let i = 0; i < n; i++) {
        const a = pts[i], b = pts[i + 1], dir = b.clone().sub(a).normalize(), side = new THREE.Vector3(-dir.z, 0, dir.x);
        const wa = w * (1 + P.vary * Math.sin(i * 0.37 + seed)) / 2, wb = w * (1 + P.vary * Math.sin((i + 1) * 0.37 + seed)) / 2;
        const A1 = a.clone().addScaledVector(side, wa), A2 = a.clone().addScaledVector(side, -wa), B1 = b.clone().addScaledVector(side, wb), B2 = b.clone().addScaledVector(side, -wb);
        for (const p of [A1, A2, B1, A2, B2, B1]) {
          pos.push(p.x, moundYAt(this, p.x, p.z) + p.y, p.z);
          const k = (rnd(Math.round(p.x * 2), Math.round(p.z * 2)) - 0.5) * 0.06; c.set(P.color).offsetHSL(0, 0, k); col.push(c.r, c.g, c.b);
        }
      }
    }
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3)); g.setAttribute('color', new THREE.Float32BufferAttribute(col, 3)); g.computeVertexNormals();
    const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial({vertexColors: true, roughness: 0.95, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2}));
    m.receiveShadow = true; this.scene.add(m);
  }

  /* ---------------------------------------------------------------- vegetation & props */
  buildProps() {
    const P = this.S.props, y = this.groundY, tb = new Batch(), tp = writer(tb, this.mats);
    P.trees.forEach(([x, z, s], i) => PROPS.tree(tp.at(x, y + moundYAt(this, x, z), z, i * 40, 1), {scale: s, greens: this.greens, seed: i}));
    P.shrubs.forEach(([x, z], i) => PROPS.shrub(tp.at(x, y, z, i * 70, 0.9 + rnd(i, 2) * 0.4), {greens: this.greens, seed: i}));
    P.flowerbeds.forEach(([x, z], i) => PROPS.flowerbed(tp.at(x, y, z, (i * 37) % 180), {flowers: this.flowers, seed: i}));
    P.benches.forEach(([x, z, r]) => PROPS.bench(tp.at(x, y, z, r)));
    PROPS.board(tp.at(P.board[0], y, P.board[1], P.board[2])); PROPS.postbox(tp.at(P.postbox[0], y, P.postbox[1], P.postbox[2]));
    // lamps: thick short posts, warm heads; at night a soft pool of light on the ground
    const lamps = P.lamps.map(([x, z]) => [x, z]);
    const park = this.S.buildings.find(b => b.kind === 'park');
    if (park) for (const [k, x, z] of park.props || []) if (k === 'lamp') lamps.push([park.pos[0] + x, park.pos[1] + z]);
    lamps.forEach(([x, z]) => PROPS.lamp(tp.at(x, y + moundYAt(this, x, z), z)));
    tb.build(this.scene);
    const pool = new THREE.InstancedMesh(new THREE.PlaneGeometry(4.6, 4.6).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({map: radial('rgba(255,196,120,0.9)', 'rgba(255,196,120,0)'), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, opacity: 0}), lamps.length);
    const m = new THREE.Matrix4();
    lamps.forEach(([x, z], i) => { m.makeTranslation(x, y + moundYAt(this, x, z) + 0.06, z); pool.setMatrixAt(i, m); });
    pool.renderOrder = 3; this.scene.add(pool); this.lampPools = pool;
  }

  /* ---------------------------------------------------------------- the light installation */
  buildInstallation() {
    const park = Object.values(this.buildings).find(b => b.installation); if (!park) return;
    const L = park.installation, n = L.bulbs.length;
    const inst = this.bulbs = new THREE.InstancedMesh(new THREE.SphereGeometry(0.11, 10, 8), new THREE.MeshBasicMaterial({toneMapped: false}), n);
    const m = new THREE.Matrix4();
    L.bulbs.forEach((p, i) => { const w = p.clone().applyMatrix4(park.group.matrixWorld); m.makeTranslation(w.x, w.y, w.z); inst.setMatrixAt(i, m); });
    this.scene.add(inst); this.bulbColors = L.colors.map(c => C(c)); this.lit = 0.3; this.instDone = false;
    this.instCenter = L.center.clone().applyMatrix4(park.group.matrixWorld);
    this.instLight = this.roomLights.pop();   // one pooled light lives at the installation
    this.instLight.position.copy(this.instCenter); this.instLight.color.set(0xb8c8ff); this.instLight.distance = 14;
    this.updateInstallation();
  }
  setInstallation(progress, done) { this.lit = done ? 1 : progress; this.instDone = done; this.updateInstallation(); }
  updateInstallation() {
    if (!this.bulbs) return;
    const n = this.bulbs.count, on = Math.round(n * Math.max(0, Math.min(1, this.lit))), night = this.glow || 0, c = new THREE.Color();
    for (let i = 0; i < n; i++) {
      const base = this.bulbColors[i % this.bulbColors.length];
      if (i < on) c.copy(base).multiplyScalar(0.55 + night * 2.2); else c.copy(base).multiplyScalar(0.18).lerp(new THREE.Color(0x3a3a44), 0.6);
      this.bulbs.setColorAt(i, c);
    }
    this.bulbs.instanceColor.needsUpdate = true;
    this.instLight.intensity = (on / n) * night * 14;
  }

  /* ---------------------------------------------------------------- fx: smoke, rain, pulses, blobs */
  buildFx() {
    this.puffTex = radial('rgba(255,255,255,0.85)', 'rgba(255,255,255,0)');
    this.blobTex = radial('rgba(30,24,40,.55)', 'rgba(30,24,40,0)');
    this.ringTex = (() => { const c = document.createElement('canvas'); c.width = c.height = 128; const x = c.getContext('2d');
      const g = x.createRadialGradient(64, 64, 30, 64, 64, 62); g.addColorStop(0, 'rgba(255,214,120,0)'); g.addColorStop(0.7, 'rgba(255,214,120,0.9)'); g.addColorStop(1, 'rgba(255,214,120,0)');
      x.fillStyle = g; x.fillRect(0, 0, 128, 128); return new THREE.CanvasTexture(c); })();
    for (const b of Object.values(this.buildings)) if (b.smoke) {
      b.smokeW = b.smoke.clone().applyMatrix4(b.group.matrixWorld);
      for (let i = 0; i < 9; i++) { const s = new THREE.Sprite(new THREE.SpriteMaterial({map: this.puffTex, transparent: true, depthWrite: false, opacity: 0})); s.userData = {b, t: i / 9}; this.scene.add(s); this.puffs.push(s); }
    }
    // rain: line segments falling in a box around the view target
    const N = 1400, pos = new Float32Array(N * 6);
    for (let i = 0; i < N; i++) { const x = (Math.random() - 0.5) * 90, y = Math.random() * 40, z = (Math.random() - 0.5) * 90; pos.set([x, y, z, x - 0.08, y - 0.9, z], i * 6); }
    const rg = new THREE.BufferGeometry(); rg.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    this.rain = new THREE.LineSegments(rg, new THREE.LineBasicMaterial({color: 0xcfe0ee, transparent: true, opacity: 0.45})); this.rain.visible = false; this.rain.frustumCulled = false; this.scene.add(this.rain);
    // festival bunting group (rebuilt on demand)
    this.bunting = new THREE.Group(); this.scene.add(this.bunting);
  }
  setRain(on) { this.rain.visible = !!on; }
  setFestival(locId) {
    this.bunting.clear(); if (!locId) return;
    const b = this.buildings[locId]; if (!b) return;
    const d = b.door, cols = ['#d9544d', '#e0a63c', '#5f7d5b', '#5e86b0'], bt = new Batch(), put = writer(bt, this.mats);
    for (let k = 0; k < 2; k++) for (let i = 0; i < 12; i++) {
      const t = i / 11, x = d.x - 4 + t * 8, z = d.z + 1.5 + k * 2.2, y = d.y + 3.6 - Math.sin(t * Math.PI) * 0.6;
      put(new THREE.ConeGeometry(0.22, 0.45, 3), cols[(i + k) % 4], [x, y, z], [Math.PI, 0, 0], 1, {grad: 0, warm: 0, faceVar: 0, grain: 0});
    }
    bt.build(this.bunting, {cast: false});
  }
  pulse(ids) {   // one soft light pulse at the feet of the people involved (a major beat)
    const ps = ids.map(id => this.figures.get(id)).filter(Boolean); if (!ps.length) return;
    const c = ps.reduce((a, f) => a.add(f.root.position), new THREE.Vector3()).divideScalar(ps.length);
    for (const f of ps) {
      const ring = new THREE.Mesh(new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2), new THREE.MeshBasicMaterial({map: this.ringTex, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending}));
      ring.position.copy(f.root.position).setY(f.root.position.y + 0.08); ring.userData.t = 0; this.scene.add(ring); this.pulses.push(ring);
    }
    this.pulseLight.position.copy(c).setY(c.y + 2.5); this.pulseT = 0;
  }

  /* ---------------------------------------------------------------- residents */
  figure(id, color) {
    let f = this.figures.get(id); if (f) return f;
    const cfg = this.FS.residents[id] || this.FS.placeholder;
    f = new Figure(cfg, this.FS, color); f.id = id;
    f.root.traverse(o => { o.userData.rid = id; });
    const blob = new THREE.Mesh(new THREE.PlaneGeometry(1.4, 1.4).rotateX(-Math.PI / 2), new THREE.MeshBasicMaterial({map: this.blobTex, transparent: true, depthWrite: false}));
    blob.renderOrder = 2; f.blob = blob; this.scene.add(f.root, blob);
    f.x = 0; f.z = 0; f.y = this.groundY; f.path = []; f.heading = 0; f.state = 'idle';
    this.figures.set(id, f); return f;
  }
  dropFigure(id) { const f = this.figures.get(id); if (f) { this.scene.remove(f.root, f.blob); this.figures.delete(id); } }
  headWorld(f, v = new THREE.Vector3()) {
    const h = f.state === 'sleep' ? 1.2 : f.state === 'sit' ? 2.0 : 2.45;
    return v.set(f.root.position.x, f.root.position.y + h * (f.H || 1), f.root.position.z);
  }
  project(v) {
    const p = v.clone().project(this.camera), W = this.el.clientWidth, H = this.el.clientHeight;
    return {x: (p.x + 1) / 2 * W, y: (1 - p.y) / 2 * H, visible: p.z < 1 && p.x > -1.2 && p.x < 1.2 && p.y > -1.2 && p.y < 1.2};
  }

  /* ---------------------------------------------------------------- camera */
  // left-drag pans, right-drag orbits (pitch clamped, full 360 deg), wheel / pinch zooms, two fingers rotate;
  // orbit and zoom ease out (damping). Any manual move ends "follow".
  setupCamera() {
    const cv = this.renderer.domElement; cv.style.touchAction = 'none';
    const ptrs = new Map(); let pinch = 0, twist = null, moved = 0, rot = false;
    this.vel = {az: 0, el: 0};
    cv.addEventListener('contextmenu', e => e.preventDefault());
    cv.addEventListener('pointerdown', e => {
      ptrs.set(e.pointerId, {x: e.clientX, y: e.clientY}); moved = 0; rot = e.button === 2 || e.shiftKey;
      try { cv.setPointerCapture(e.pointerId); } catch (_) {} cv.style.cursor = rot ? 'move' : 'grabbing'; this.goal = null;
    });
    cv.addEventListener('pointermove', e => {
      const last = ptrs.get(e.pointerId); if (!last) return;
      const dx = e.clientX - last.x, dy = e.clientY - last.y; ptrs.set(e.pointerId, {x: e.clientX, y: e.clientY}); moved += Math.abs(dx) + Math.abs(dy);
      if (moved > 4) this.stopFollow();
      if (ptrs.size === 2) {   // two fingers: pinch zoom + twist rotate
        const [a, b] = [...ptrs.values()], d = Math.hypot(a.x - b.x, a.y - b.y), ang = Math.atan2(b.y - a.y, b.x - a.x);
        if (pinch) this.zoom(pinch / d); pinch = d;
        if (twist != null) { let da = ang - twist; if (da > Math.PI) da -= 2 * Math.PI; if (da < -Math.PI) da += 2 * Math.PI; this.vel.az += da * 0.5; }
        twist = ang; return;
      }
      if (rot) { this.vel.az -= dx * 0.0022; this.vel.el += dy * 0.0016; return; }
      const k = this.cam.dist * 0.0016, az = this.cam.az;
      const right = new THREE.Vector3(Math.cos(az), 0, -Math.sin(az)), fwd = new THREE.Vector3(Math.sin(az), 0, Math.cos(az));
      this.cam.target.addScaledVector(right, -dx * k).addScaledVector(fwd, -dy * k * 1.5); this.clampTarget();
    });
    const up = e => {
      if (ptrs.size === 1 && moved < 6 && e.type === 'pointerup' && !rot) this.pick(e);
      ptrs.delete(e.pointerId); if (ptrs.size < 2) { pinch = 0; twist = null; } cv.style.cursor = '';
    };
    cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up);
    cv.addEventListener('dblclick', () => { this.stopFollow(); this.resetView(); });
    cv.addEventListener('wheel', e => { e.preventDefault(); this.goal = null; this.zoom(Math.exp(e.deltaY * 0.0012)); }, {passive: false});
    // keyboard pan: WASD / arrows along the camera's heading (W = toward the top of the screen), Shift = faster
    this.keys = new Set();
    const KEYS = {KeyW: 'f', ArrowUp: 'f', KeyS: 'b', ArrowDown: 'b', KeyA: 'l', ArrowLeft: 'l', KeyD: 'r', ArrowRight: 'r'};
    const typing = t => t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
    this.onKey = e => {
      const k = KEYS[e.code];
      if (e.type === 'keyup') { if (k) this.keys.delete(k); this.keyFast = e.shiftKey; return; }
      if (!k || !this.active || e.ctrlKey || e.metaKey || e.altKey || typing(e.target) || !this.el.offsetParent) return;
      e.preventDefault(); this.keys.add(k); this.keyFast = e.shiftKey;
    };
    this.onBlur = () => this.keys.clear();
    addEventListener('keydown', this.onKey); addEventListener('keyup', this.onKey); addEventListener('blur', this.onBlur);
  }
  keyPan(dt) {
    if (!this.keys || !this.keys.size) return;
    const K = this.keys, f = (K.has('b') ? 1 : 0) - (K.has('f') ? 1 : 0), r = (K.has('r') ? 1 : 0) - (K.has('l') ? 1 : 0);
    if (!f && !r) return;
    this.stopFollow(); this.goal = null;
    const az = this.cam.az, v = this.cam.dist * 0.35 * (this.keyFast ? 2.5 : 1) * dt, n = Math.hypot(f, r);
    this.cam.target.x += (Math.cos(az) * r + Math.sin(az) * f) / n * v;
    this.cam.target.z += (-Math.sin(az) * r + Math.cos(az) * f) / n * v;
    this.clampTarget();
  }
  follow(id) { this.following = id; this.goal = null; if (this.cam.dist > this.S.camera.focus * 1.4) this.goal = {target: this.cam.target.clone(), dist: this.S.camera.focus}; }
  stopFollow() { if (this.following) { this.following = null; if (this.onFollowEnd) this.onFollowEnd(); } }
  zoom(k) { const C_ = this.S.camera; this.cam.dist = Math.max(C_.min, Math.min(C_.max, this.cam.dist * k)); }
  clampTarget() { const t = this.cam.target; t.x = Math.max(-45, Math.min(45, t.x)); t.z = Math.max(-32, Math.min(34, t.z)); }
  resetView() { const C_ = this.S.camera; this.goal = {target: new THREE.Vector3(...C_.target), dist: C_.distance, az: C_.azimuth * DEG, el: C_.elevation * DEG}; }
  focusOn(points, dist) { if (!points.length) return; const c = points.reduce((a, p) => a.add(p), new THREE.Vector3()).divideScalar(points.length); c.y = this.groundY + 1; this.goal = {target: c, dist: dist || this.S.camera.focus}; }
  pick(e) {
    const r = this.renderer.domElement.getBoundingClientRect(), ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2((e.clientX - r.left) / r.width * 2 - 1, -(e.clientY - r.top) / r.height * 2 + 1), this.camera);
    const hits = ray.intersectObjects([...this.figures.values()].filter(f => f.root.visible).map(f => f.root), true);
    if (hits.length && this.onPick) this.onPick(hits[0].object.userData.rid);
  }
  updateCamera() {
    const C_ = this.S.camera, c = this.cam, k = C_.ease;
    if (this.following) {   // follow mode: the target glides after the resident
      const f = this.figures.get(this.following);
      if (f && f.root.visible) c.target.lerp(new THREE.Vector3(f.x, this.groundY + 1, f.z), k * 1.5); else this.stopFollow();
    }
    if (this.goal) {
      if (!this.following) c.target.lerp(this.goal.target, k);
      c.dist += (this.goal.dist - c.dist) * k;
      if (this.goal.az != null) { let d = this.goal.az - c.az; while (d > Math.PI) d -= 2 * Math.PI; while (d < -Math.PI) d += 2 * Math.PI; c.az += d * k; c.el += (this.goal.el - c.el) * k; }
      if ((this.following || c.target.distanceTo(this.goal.target) < 0.05) && Math.abs(c.dist - this.goal.dist) < 0.05) this.goal = null;
    }
    // orbit with damping; pitch clamped, yaw free
    const v = this.vel; c.az += v.az; c.el = Math.max(C_.pitchMin * DEG, Math.min(C_.pitchMax * DEG, c.el + v.el));
    v.az *= C_.damping; v.el *= C_.damping; if (Math.abs(v.az) < 1e-5) v.az = 0; if (Math.abs(v.el) < 1e-5) v.el = 0;
    const h = Math.cos(c.el) * c.dist;
    this.camera.position.set(c.target.x + Math.sin(c.az) * h, c.target.y + Math.sin(c.el) * c.dist, c.target.z + Math.cos(c.az) * h);
    this.camera.lookAt(c.target);
    if (this.sunOffset) { this.sun.position.copy(this.sunOffset).add(c.target); this.sun.target.position.copy(c.target); }
  }
  resize() {
    const w = this.el.clientWidth, h = this.el.clientHeight; if (!w || !h) return;
    this.renderer.setSize(w, h); this.camera.aspect = w / h;
    const sh = this.S.camera.shift || 0, sx = w > 900 && this.el.closest('.village') ? (this.S.camera.shiftX || 0) : 0;   // leave room for the live column
    this.camera.setViewOffset(w, h, sx * w, -sh * h, w, h);
    this.camera.updateProjectionMatrix();
    if (this.composer) { this.composer.setPixelRatio(this.renderer.getPixelRatio()); this.composer.setSize(w, h); }
  }
  setActive(v) { this.active = v; if (v) { this.timer.reset?.(); this.resize(); } }

  /* ---------------------------------------------------------------- frame */
  frame() {
    this.timer.update(); const dt = Math.min(0.05, this.timer.getDelta()); this.t += dt; this.frames = (this.frames || 0) + 1;
    this.waterTime.value = this.t;
    if (this.onFrame) this.onFrame(dt);   // townmap moves the figures
    for (const f of this.figures.values()) {
      f.root.position.set(f.x, f.y, f.z); f.root.rotation.y = f.heading;
      f.pose(f.state, dt);
      f.blob.visible = f.root.visible && f.state !== 'sleep';
      f.blob.position.set(f.x, f.y + 0.07, f.z);
    }
    // chimney smoke while the bakery is open
    for (const s of this.puffs) {
      const u = s.userData; u.t = (u.t + dt * 0.18) % 1; const on = u.b.smokeOn ? 1 : 0;
      s.position.copy(u.b.smokeW).add(new THREE.Vector3(u.t * 1.6 + Math.sin(u.t * 6 + this.t) * 0.2, u.t * 4.2, -u.t * 0.6));
      s.scale.setScalar(0.5 + u.t * 1.8); s.material.opacity = on * Math.sin(u.t * Math.PI) * 0.55;
    }
    if (this.rain.visible) {
      this.rain.position.set(this.cam.target.x, this.groundY - 2, this.cam.target.z);
      this.rain.geometry.translate(0, 0, 0);
      const a = this.rain.geometry.attributes.position.array, fall = dt * 26;
      for (let i = 0; i < a.length; i += 6) { a[i + 1] -= fall; a[i + 4] -= fall; if (a[i + 1] < 0) { a[i + 1] += 40; a[i + 4] += 40; } }
      this.rain.geometry.attributes.position.needsUpdate = true;
    }
    for (let i = this.pulses.length - 1; i >= 0; i--) {
      const r = this.pulses[i]; r.userData.t += dt / 1.8; const t = r.userData.t;
      r.scale.setScalar(1 + t * 5); r.material.opacity = Math.max(0, 1 - t);
      if (t >= 1) { this.scene.remove(r); r.geometry.dispose(); r.material.dispose(); this.pulses.splice(i, 1); }
    }
    if (this.pulseT != null) { this.pulseT += dt / 1.8; this.pulseLight.intensity = Math.max(0, Math.sin(Math.min(1, this.pulseT) * Math.PI)) * 18; if (this.pulseT >= 1) { this.pulseT = null; this.pulseLight.intensity = 0; } }
    for (const b of Object.values(this.buildings)) this.animateCut(b, dt);
    this.keyPan(dt); this.updateCamera();
    this.composer.render();
    if (this.afterFrame) this.afterFrame();
  }
  // cutaway: the closed house lifts away (and the room rises) while someone is inside
  animateCut(b, dt) {
    if (!b.anchors) return;
    const want = b.wantOpen ? 1 : 0; if (b.open === want && !b.moving) return;
    b.open += Math.sign(want - b.open) * Math.min(Math.abs(want - b.open), dt / 0.7); b.moving = b.open !== want;
    const e = b.open * b.open * (3 - 2 * b.open);
    b.ext.position.y = e * 14; b.ext.visible = e < 0.98; b.ext.scale.setScalar(1 - e * 0.08);
    b.cut.visible = e > 0.02; b.cut.scale.set(1, Math.max(0.01, e), 1);
  }
  destroy() {
    this.alive = false; cancelAnimationFrame(this.raf); clearInterval(this.fpsT); this.ro.disconnect();
    removeEventListener('keydown', this.onKey); removeEventListener('keyup', this.onKey); removeEventListener('blur', this.onBlur);
    this.renderer.dispose(); this.renderer.domElement.remove();
  }
}
function moundYAt(scene, x, z) {   // ground lift on the park's slope (world coords)
  const p = scene.S.buildings.find(b => b.kind === 'park'); if (!p || !p.mound) return 0;
  return moundY(p, x - p.pos[0], z - p.pos[1]);
}
export {moundYAt};
