// Shared clay-miniature helpers: vertex-colour tinting, rounded boxes, the matte clay material.
import * as THREE from 'three';
import {RoundedBoxGeometry} from 'three/addons/geometries/RoundedBoxGeometry.js';

export const C = h => new THREE.Color(h);
export const lerpC = (a, b, k) => a.clone().lerp(b, Math.max(0, Math.min(1, k)));
export const rnd = (a, b = 0) => { const s = Math.sin(a * 127.1 + b * 311.7) * 43758.5453; return s - Math.floor(s); };

// Vertex colours: bottom a touch darker and warmer, faces turned east/west a shade apart,
// plus a little per-vertex grain -- never a dead flat colour.
export function tint(g, color, {grad = 0.16, warm = 0.12, faceVar = 0.035, grain = 0.02, bands = null} = {}) {
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
export const rbox = (w, h, d, r, seg = 4) => new RoundedBoxGeometry(w, h, d, seg, Math.min(r, w / 2 - 0.005, h / 2 - 0.005, d / 2 - 0.005));
export const clay = (o = {}) => new THREE.MeshStandardMaterial({roughness: 0.9, metalness: 0, vertexColors: true, ...o});
export function mesh(g, m, {cast = true, receive = true} = {}) { const x = new THREE.Mesh(g, m); x.castShadow = cast; x.receiveShadow = receive; return x; }
export const CLAY = clay();

// A batch collects many small clay pieces (geometry + transform) and merges them into one
// mesh per material, so a whole building or every tree in town is a single draw call.
import {mergeGeometries} from 'three/addons/utils/BufferGeometryUtils.js';
const _m = new THREE.Matrix4(), _q = new THREE.Quaternion(), _e = new THREE.Euler(), _v = new THREE.Vector3(), _s = new THREE.Vector3();
export class Batch {
  constructor() { this.lists = new Map(); }
  // add(geometry, material, {p:[x,y,z], r:[rx,ry,rz], s:[sx,sy,sz] | number}) or with a ready Matrix4 (o.m)
  add(g, mat, o = {}) {
    let m = o.m;
    if (!m) {
      const s = o.s == null ? 1 : o.s;
      _e.set(...(o.r || [0, 0, 0])); _q.setFromEuler(_e);
      m = _m.compose(_v.set(...(o.p || [0, 0, 0])), _q, typeof s === 'number' ? _s.set(s, s, s) : _s.set(...s));
    }
    let geo = g.index ? g.toNonIndexed() : g.clone();
    for (const k of Object.keys(geo.attributes)) if (!['position', 'normal', 'color'].includes(k)) geo.deleteAttribute(k);
    if (!geo.attributes.color) { const n = geo.attributes.position.count, c = new Float32Array(n * 3).fill(1); geo.setAttribute('color', new THREE.BufferAttribute(c, 3)); }
    geo.applyMatrix4(m);
    if (!this.lists.has(mat)) this.lists.set(mat, []);
    this.lists.get(mat).push(geo);
    return this;
  }
  build(parent, {cast = true, receive = true} = {}) {
    const out = [];
    for (const [mat, list] of this.lists) {
      if (!list.length) continue;
      const g = mergeGeometries(list); list.forEach(x => x.dispose());
      const m = mesh(g, mat, {cast, receive}); parent.add(m); out.push(m);
    }
    this.lists.clear();
    return out;
  }
}
// colour helpers for day / dusk / night stops in the JSON ({day, dusk, night})
export function stops(o, dusk, n) {
  const d = C(o.day), k = o.dusk ? lerpC(d, C(o.dusk), dusk) : d;
  return o.night ? lerpC(k, C(o.night), n) : k;
}
