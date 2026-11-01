// The clay piece kit: furniture and outdoor props as small rounded parts, all written into
// a Batch (merged per material). Every builder works in its own local space; `at` places
// it (x, y, z, rotation about y in degrees, uniform scale).
import * as THREE from 'three';
import {C, tint, rbox, rnd, CLAY} from './clay.js';

const T = {grad: 0.12, warm: 0.05, faceVar: 0.03, grain: 0.02};
const F = {grad: 0.05, warm: 0, faceVar: 0.02, grain: 0.015};
const cyl = (rt, rb, h, s = 16) => new THREE.CylinderGeometry(rt, rb, h, s);
const sph = (r, w = 16, h = 12) => new THREE.SphereGeometry(r, w, h);
const cap = (r, len) => new THREE.CapsuleGeometry(r, Math.max(0.001, len - 2 * r), 4, 10);
const DEG = Math.PI / 180;

// A writer bound to a batch and a base transform. put(geometry, color | material, [x,y,z], [rx,ry,rz], scale)
export function writer(batch, mats, base = new THREE.Matrix4()) {
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), e = new THREE.Euler(), v = new THREE.Vector3(), s = new THREE.Vector3();
  const put = (g, color, p = [0, 0, 0], r = [0, 0, 0], sc = 1, opts = T) => {
    let mat = CLAY;
    if (typeof color === 'string') tint(g, color, opts); else mat = color;
    e.set(...r); q.setFromEuler(e); m.compose(v.set(...p), q, typeof sc === 'number' ? s.set(sc, sc, sc) : s.set(...sc));
    batch.add(g, mat, {m: base.clone().multiply(m)});
  };
  put.at = (x, y, z, rotDeg = 0, scale = 1) => {
    const b = base.clone().multiply(new THREE.Matrix4().compose(new THREE.Vector3(x, y, z), new THREE.Quaternion().setFromEuler(new THREE.Euler(0, rotDeg * DEG, 0)), new THREE.Vector3(scale, scale, scale)));
    return writer(batch, mats, b);
  };
  put.mats = mats;
  return put;
}

/* ------------------------------------------------------------------ furniture (local, floor at y=0) */
export const FURNITURE = {
  counter(put, c = '#9a6a4a') {
    put(rbox(3.2, 1.0, 0.7, 0.1), c, [0, 0.5, 0]);
    put(rbox(3.36, 0.1, 0.84, 0.04, 2), '#e9dccb', [0, 1.04, 0], undefined, 1, F);
    put(rbox(0.5, 0.3, 0.4, 0.06, 2), '#3c3f4a', [0.9, 1.25, 0]);   // till
  },
  espresso(put) {
    put(rbox(0.55, 0.45, 0.42, 0.08), '#c8ccd2', [0, 1.32, 0], undefined, 1, F);
    put(cyl(0.06, 0.06, 0.12), '#3c3f4a', [0.12, 1.06 + 0.06, 0.12]);
  },
  shelf(put, c = '#8a5a3c') {
    put(rbox(3.0, 1.9, 0.36, 0.06), c, [0, 0.95, 0]);
    const cols = ['#f2ead9', '#e8645a', '#5f8fd0', '#f2c84a', '#8cc06a'];
    for (const y of [0.75, 1.3, 1.75]) for (let i = 0; i < 6; i++) put(cyl(0.08, 0.07, 0.18, 10), cols[(i + y * 10 | 0) % cols.length], [-1.2 + i * 0.48, y + 0.09, 0.08], undefined, 1, F);
  },
  breadshelf(put, c = '#9a6a4a') {
    put(rbox(3.0, 1.9, 0.4, 0.06), c, [0, 0.95, 0]);
    for (const y of [0.7, 1.25, 1.75]) for (let i = 0; i < 5; i++) put(cap(0.1, 0.42), '#d9a05a', [-1.1 + i * 0.55, y + 0.1, 0.06], [0, 0, Math.PI / 2], 1, F);
  },
  table(put, c = '#b98258') {
    put(cyl(0.55, 0.55, 0.08, 24), c, [0, 0.76, 0]); put(cyl(0.07, 0.1, 0.72, 10), '#4a4550', [0, 0.38, 0]);
    put(cyl(0.3, 0.34, 0.05, 16), '#4a4550', [0, 0.03, 0]);
    put(cyl(0.07, 0.06, 0.1, 10), '#fff4ea', [0.15, 0.85, 0.05], undefined, 1, F);
  },
  chair(put, c = '#b98258') {
    put(rbox(0.46, 0.09, 0.46, 0.04, 2), c, [0, 0.46, 0]);
    put(rbox(0.46, 0.5, 0.08, 0.04, 2), c, [0, 0.74, -0.2]);
    put(rbox(0.38, 0.42, 0.38, 0.06, 2), '#5a4a44', [0, 0.21, 0], undefined, [1, 1, 1]);
  },
  stool(put, c = '#b98258') { put(cyl(0.22, 0.22, 0.08, 16), c, [0, 0.48, 0]); put(cyl(0.05, 0.08, 0.44, 8), '#4a4550', [0, 0.22, 0]); },
  bed(put, c = '#7fa6d8') {
    put(rbox(1.04, 0.36, 2.06, 0.08), '#8a6a52', [0, 0.18, 0]);
    put(rbox(1.04, 0.7, 0.14, 0.06), '#8a6a52', [0, 0.35, -1.0]);
    put(rbox(0.92, 0.16, 1.9, 0.07), '#f6f0e4', [0, 0.44, 0], undefined, 1, F);
    put(rbox(0.6, 0.13, 0.34, 0.06), '#ffffff', [0, 0.56, -0.72], undefined, 1, F);
    put(rbox(0.96, 0.09, 1.0, 0.04, 2), c, [0, 0.54, 0.42], undefined, 1, F);
  },
  stove(put) {
    put(rbox(0.8, 0.9, 0.62, 0.08), '#4a4f5a', [0, 0.45, 0]);
    put(cyl(0.14, 0.14, 0.12, 12), '#c8ccd2', [0.12, 0.98, 0]);
    put(rbox(0.5, 0.3, 0.06, 0.03, 2), '#2a2e36', [0, 0.45, 0.31]);
  },
  oven(put, c = '#b5654a') {
    put(rbox(1.9, 1.0, 1.4, 0.14), '#e6d8c4', [0, 0.5, 0]);
    put(new THREE.SphereGeometry(0.85, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2), c, [0, 1.0, 0], undefined, [1, 0.95, 0.8]);
    put(cyl(0.16, 0.2, 0.9, 12), c, [0.5, 1.6, -0.3]);
    put(rbox(0.6, 0.38, 0.1, 0.12, 3), put.mats.glow, [0, 1.25, 0.66]);
  },
  knead(put, c = '#c8a07a') {
    put(rbox(2.2, 0.12, 1.0, 0.05, 2), c, [0, 0.86, 0]); put(rbox(1.9, 0.78, 0.8, 0.1), '#8a6a52', [0, 0.39, 0]);
    for (let i = 0; i < 3; i++) put(sph(0.17), '#f2e4cf', [-0.6 + i * 0.6, 0.98, 0.1], undefined, [1, 0.6, 1], F);
  },
  display(put, c = '#e9dccb') {
    put(rbox(2.0, 0.95, 0.8, 0.1), c, [0, 0.47, 0]);
    put(rbox(1.9, 0.36, 0.7, 0.06, 2), put.mats.glass, [0, 1.12, 0]);
    for (let i = 0; i < 5; i++) put(sph(0.12), ['#d9a05a', '#c87a3c', '#f2e4cf'][i % 3], [-0.7 + i * 0.35, 1.02, 0.05], undefined, [1.3, 0.7, 1], F);
  },
  sacks(put) { [[0, 0, 0.36], [0.5, 0.1, 0.32], [0.24, 0.42, 0.3]].forEach(([x, z, r], i) => put(sph(r, 16, 12), i === 2 ? '#e8dcc0' : '#d8c8a2', [x, r * 0.85, z], [0, i, 0], [1, 1.15, 0.9])); },
  desk(put, c = '#a87a52') {
    put(rbox(1.7, 0.08, 0.8, 0.03, 2), c, [0, 0.76, 0]);
    put(rbox(0.1, 0.72, 0.7, 0.03, 2), c, [-0.75, 0.36, 0]); put(rbox(0.1, 0.72, 0.7, 0.03, 2), c, [0.75, 0.36, 0]);
    put(rbox(0.6, 0.42, 0.06, 0.04, 2), '#2a2e36', [0, 1.05, -0.2]); put(rbox(0.5, 0.34, 0.02, 0.02, 2), put.mats.screen, [0, 1.06, -0.165]);
    put(rbox(0.4, 0.03, 0.3, 0.01, 1), '#f6f0e4', [0.45, 0.81, 0.1], undefined, 1, F);
  },
  cabinet(put, c = '#8a96a6') { put(rbox(1.4, 1.6, 0.6, 0.06), c, [0, 0.8, 0]); for (const y of [0.45, 0.95, 1.4]) put(rbox(1.2, 0.04, 0.04, 0.01, 1), '#5a6676', [0, y, 0.3]); },
  cooler(put) { put(rbox(0.5, 0.95, 0.5, 0.08), '#e8ecf0', [0, 0.47, 0], undefined, 1, F); put(cyl(0.2, 0.2, 0.5, 14), '#8ac8f0', [0, 1.2, 0], undefined, 1, F); },
  plant(put) { put(cyl(0.22, 0.17, 0.4, 14), '#c8643c', [0, 0.2, 0]); put(sph(0.36), '#6fb866', [0, 0.72, 0], undefined, 1, {grad: 0.2, warm: 0, faceVar: 0, grain: 0.06}); put(sph(0.24), '#8fcf7a', [0.16, 0.98, 0.05]); },
  rug(put, c = '#c86a5a') { put(rbox(2.2, 0.03, 1.5, 0.015, 1), c, [0, 0.02, 0], undefined, 1, F); },
  easel(put) {
    put(rbox(0.08, 1.6, 0.08, 0.02, 1), '#8a6a52', [-0.3, 0.8, 0], [0, 0, 0.12]); put(rbox(0.08, 1.6, 0.08, 0.02, 1), '#8a6a52', [0.3, 0.8, 0], [0, 0, -0.12]);
    put(rbox(0.08, 1.5, 0.08, 0.02, 1), '#8a6a52', [0, 0.75, -0.35], [0.3, 0, 0]);
    put(rbox(0.9, 0.7, 0.05, 0.02, 1), '#f6f0e4', [0, 1.2, 0.06], [-0.1, 0, 0], 1, F); put(rbox(0.5, 0.3, 0.02, 0.01, 1), '#7fb0d8', [0.05, 1.25, 0.1], [-0.1, 0, 0], 1, F);
  },
};

/* ------------------------------------------------------------------ outdoor props (local, ground at y=0) */
export const PROPS = {
  tree(put, {scale = 1, greens, seed = 0}) {
    put(cyl(0.16 * scale, 0.26 * scale, 1.5 * scale, 10), '#8f6446', [0, 0.75 * scale, 0], undefined, 1, {grad: 0.2, warm: 0.05, faceVar: 0, grain: 0.03});
    const blobs = [[0, 2.2, 0, 1.15], [0.66, 1.85, 0.25, 0.85], [-0.55, 1.92, -0.22, 0.9], [0.1, 2.85, -0.12, 0.72]];
    blobs.forEach(([x, y, z, r], k) => put(new THREE.IcosahedronGeometry(r * scale, 3), greens[(k + seed) % greens.length], [x * scale, y * scale, z * scale], undefined, 1, {grad: 0.24, warm: 0, faceVar: 0, grain: 0.08}));
  },
  shrub(put, {greens, seed = 0}) {
    [[0, 0.42, 0, 0.55], [0.5, 0.34, 0.15, 0.42], [-0.42, 0.32, -0.1, 0.4]].forEach(([x, y, z, r], k) => put(new THREE.IcosahedronGeometry(r, 2), greens[(k + seed + 1) % greens.length], [x, y, z], undefined, [1, 0.85, 1], {grad: 0.25, warm: 0, faceVar: 0, grain: 0.08}));
  },
  flowerbed(put, {flowers, seed = 0}) {
    put(rbox(2.2, 0.32, 1.2, 0.14), '#d8c9ae', [0, 0.16, 0]); put(rbox(1.96, 0.1, 0.96, 0.08), '#7a5a40', [0, 0.32, 0], undefined, 1, F);
    for (let i = 0; i < 14; i++) {
      const x = (rnd(i, seed) - 0.5) * 1.7, z = (rnd(seed, i * 3) - 0.5) * 0.75;
      put(sph(0.1, 8, 6), '#6fb866', [x, 0.42, z]); put(sph(0.09, 8, 6), flowers[(i + seed) % flowers.length], [x, 0.55, z], undefined, 1, F);
    }
  },
  pot(put, {flowers, seed = 0}) {
    put(cyl(0.26, 0.2, 0.42, 14), '#c8643c', [0, 0.21, 0]); put(sph(0.3, 12, 10), '#6fb866', [0, 0.52, 0], undefined, 1, {grad: 0.2, warm: 0, faceVar: 0, grain: 0.06});
    for (let i = 0; i < 4; i++) put(sph(0.08, 8, 6), flowers[(i + seed) % flowers.length], [Math.cos(i * 1.7) * 0.2, 0.7, Math.sin(i * 1.7) * 0.2], undefined, 1, F);
  },
  umbrella(put) {
    put(cyl(0.55, 0.55, 0.08, 24), '#fff4ea', [0, 0.75, 0], undefined, 1, F); put(cyl(0.06, 0.06, 0.75, 8), '#3c3f4a', [0, 0.37, 0]);
    put(cyl(0.04, 0.04, 2.3, 8), '#e8e0d0', [0, 1.4, 0]); put(new THREE.ConeGeometry(1.3, 0.55, 20), '#e8645a', [0, 2.45, 0], undefined, 1, {grad: -0.15, warm: 0, faceVar: 0, grain: 0.02});
    FURNITURE.chair(put.at(-0.85, 0, 0, 90)); FURNITURE.chair(put.at(0.85, 0, 0, -90));
  },
  board(put) {   // the notice board
    put(rbox(0.14, 1.9, 0.14, 0.05, 2), '#8a6a52', [-0.75, 0.95, 0]); put(rbox(0.14, 1.9, 0.14, 0.05, 2), '#8a6a52', [0.75, 0.95, 0]);
    put(rbox(1.7, 1.0, 0.12, 0.06), '#b98a62', [0, 1.35, 0]); put(rbox(1.9, 0.14, 0.34, 0.06, 2), '#7a5a4c', [0, 1.95, 0]);
    [['#fff4ea', -0.45, 1.45], ['#f2c84a', 0.1, 1.25], ['#bfe6cf', 0.48, 1.5], ['#ffd8d0', -0.2, 1.08]].forEach(([c, x, y]) => put(rbox(0.36, 0.42, 0.02, 0.01, 1), c, [x, y, 0.07], [0, 0, (x * 7 % 1) * 0.2], 1, F));
  },
  postbox(put) { put(rbox(0.5, 1.1, 0.5, 0.2), '#d64a3c', [0, 0.55, 0]); put(new THREE.SphereGeometry(0.25, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2), '#d64a3c', [0, 1.1, 0], undefined, [1, 0.6, 1]); put(rbox(0.3, 0.05, 0.04, 0.02, 1), '#2a2430', [0, 0.85, 0.25]); },
  mailbox(put) { put(rbox(0.1, 1.0, 0.1, 0.03, 1), '#5a4a44', [0, 0.5, 0]); put(rbox(0.32, 0.3, 0.5, 0.12), '#5f8fd0', [0, 1.1, 0]); },
  sacks(put) { FURNITURE.sacks(put); },
  crate(put) { put(rbox(0.7, 0.55, 0.6, 0.06), '#b98a62', [0, 0.27, 0]); for (let i = 0; i < 4; i++) put(sph(0.12, 10, 8), ['#e8645a', '#f2a23c', '#8cc06a'][i % 3], [-0.2 + (i % 2) * 0.36, 0.58, -0.12 + (i >> 1) * 0.26], undefined, 1, F); },
  barrel(put) { put(cyl(0.34, 0.34, 0.85, 16), '#9a6a4a', [0, 0.42, 0], undefined, [1, 1, 1]); put(new THREE.TorusGeometry(0.35, 0.03, 6, 20), '#4a4550', [0, 0.2, 0], [Math.PI / 2, 0, 0]); put(new THREE.TorusGeometry(0.35, 0.03, 6, 20), '#4a4550', [0, 0.66, 0], [Math.PI / 2, 0, 0]); },
  basket(put) { put(cyl(0.34, 0.26, 0.3, 16), '#c8a06a', [0, 0.15, 0]); for (let i = 0; i < 5; i++) put(sph(0.1, 10, 8), ['#f2d04a', '#e8645a', '#8cc06a'][i % 3], [Math.cos(i * 1.3) * 0.16, 0.32, Math.sin(i * 1.3) * 0.16], undefined, 1, F); },
  bike(put) {
    for (const x of [-0.5, 0.5]) put(new THREE.TorusGeometry(0.32, 0.04, 8, 20), '#2a2e36', [x, 0.34, 0]);
    put(rbox(1.0, 0.06, 0.06, 0.03, 1), '#3f8fb0', [0, 0.5, 0], [0, 0, 0.1]); put(rbox(0.06, 0.5, 0.06, 0.03, 1), '#3f8fb0', [-0.15, 0.55, 0], [0, 0, -0.3]);
    put(rbox(0.26, 0.06, 0.12, 0.03, 1), '#3c3f4a', [-0.2, 0.84, 0]); put(rbox(0.06, 0.06, 0.46, 0.03, 1), '#3c3f4a', [0.42, 0.86, 0]);
  },
  washline(put, {seed = 0}) {   // two posts, a sagging line, clothes in pastel
    for (const x of [-2, 2]) put(rbox(0.1, 2.0, 0.1, 0.04, 1), '#8a6a52', [x, 1.0, 0]);
    const cols = ['#fff4ea', '#7fb0d8', '#f2a6a0', '#f2d04a', '#bfe6cf'];
    for (let i = 0; i < 9; i++) { const x = -1.8 + i * 0.45, y = 1.88 - Math.sin((i + 0.5) / 9 * Math.PI) * 0.22; put(cyl(0.012, 0.012, 0.46, 4), '#eeeeee', [x, y, 0], [0, 0, Math.PI / 2], 1, F); }
    [[-1.3, 0.5, 0.6], [-0.5, 0.42, 0.5], [0.4, 0.55, 0.62], [1.2, 0.4, 0.45]].forEach(([x, w, h], i) => put(rbox(w, h, 0.04, 0.02, 1), cols[(i + seed) % cols.length], [x, 1.7 - h / 2 - Math.sin((x + 2) / 4 * Math.PI) * 0.2, 0], undefined, 1, F));
  },
  woodpile(put) { for (let i = 0; i < 7; i++) put(cyl(0.14, 0.14, 1.0, 10), i % 2 ? '#a87a52' : '#8f6446', [-0.42 + (i % 4) * 0.28 + (i >= 4 ? 0.14 : 0), 0.14 + (i >= 4 ? 0.25 : 0), 0], [Math.PI / 2, 0, 0]); },
  lamp(put) {   // thick, short, rounded post with a warm head
    put(cyl(0.3, 0.36, 0.25, 16), '#3c3f4a', [0, 0.12, 0]);
    put(cap(0.13, 2.0), '#3c3f4a', [0, 1.15, 0]);
    put(cyl(0.34, 0.26, 0.14, 16), '#3c3f4a', [0, 2.62, 0]);
    put(sph(0.28, 18, 14), put.mats.glow, [0, 2.42, 0]);
    put(new THREE.SphereGeometry(0.3, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2), '#3c3f4a', [0, 2.66, 0], undefined, [1, 0.55, 1]);
  },
  bench(put) {
    [[0, 0.46, 0, 1.8, 0.14, 0.55, '#b98258'], [0, 0.85, -0.25, 1.8, 0.46, 0.12, '#b98258'], [-0.7, 0.23, 0, 0.14, 0.46, 0.5, '#4a4550'], [0.7, 0.23, 0, 0.14, 0.46, 0.5, '#4a4550']]
      .forEach(([x, y, z, w, h, d, c]) => put(rbox(w, h, d, 0.05, 3), c, [x, y, z]));
  },
};
