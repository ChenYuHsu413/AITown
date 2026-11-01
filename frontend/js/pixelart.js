// Programmatic pixel art (no third-party assets): residents, buildings, decor and the
// ground, all painted onto small canvases at 1 px = 1 art pixel. The Pixi map turns
// these canvases into textures; the UI avatars use the same character frames, so a
// resident looks the same everywhere. Each resident's clothing main color is their
// identity token (tokens.css).

const OUT = '#2a2024';
export const canvas = (w, h) => { const c = document.createElement('canvas'); c.width = w; c.height = h; return c; };
const hexRgb = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
const rgbHex = a => '#' + a.map(v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('');
export const shade = (h, k) => rgbHex(hexRgb(h).map(v => k < 0 ? v * (1 + k) : v + (255 - v) * k));
// deterministic noise: same tile, same speckles, every load
export const hash = (x, y, s = 0) => { let h = (x * 374761393 + y * 668265263 + s * 2147483647) | 0; h = (h ^ (h >>> 13)) * 1274126177 | 0; return ((h ^ (h >>> 16)) >>> 0) / 4294967296; };

/* ------------------------------------------------------------------ residents */
// Distinct silhouettes: hair style + build + accessory; cloth = identity color.
const LOOKS = {
  jiji:      {hair:'#4a3524', style:'short',    build:'mid',   skin:'#f1c7a2', pants:'#4a4048', acc:'apron'},
  ange:      {hair:'#5a3a26', style:'chef',     build:'broad', skin:'#e9be97', pants:'#5a4a3e', acc:'apron'},
  oula:      {hair:'#2b2630', style:'cap',      build:'mid',   skin:'#d8a878', pants:'#3e4656', acc:'bag'},
  lengyue:   {hair:'#3a2a3a', style:'long',     build:'slim',  skin:'#f1c7a2', pants:'#4a4048', acc:'glasses'},
  azong:     {hair:'#6b4128', style:'bob',      build:'slim',  skin:'#ecd6c0', pants:'#3a3a44', acc:'scarf'},
  xixi:      {hair:'#6a4a30', style:'ponytail', build:'slim',  skin:'#f1c7a2', pants:'#3e4656', acc:'bag'},
  aisi:      {hair:'#d9b24f', style:'spiky',    build:'mid',   skin:'#f1c7a2', pants:'#3a3a44', acc:'goggles'},
  xue:       {hair:'#2e2a33', style:'long',     build:'slim',  skin:'#e9be97', pants:'#2e3440', acc:''},
  long:      {hair:'#8a8580', style:'receding', build:'broad', skin:'#e6c9a8', pants:'#4e4a3a', acc:'apron'},
  kuaizheng: {hair:'#e2ddd4', style:'receding', build:'slim',  skin:'#ecd6c0', pants:'#5a5048', acc:'glasses'},
};
export const CHAR_W = 16, CHAR_H = 24;
// dir: 'down' | 'up' | 'left' | 'right'; frame: 0 stand, 1 / 2 walk steps
export function charCanvas(cloth, look, dir, frame) {
  const W = CHAR_W, H = CHAR_H, g = new Array(W * H).fill(null);
  const R = (x, y, w, h, c) => { for (let j = y; j < y + h; j++) for (let i = x; i < x + w; i++) if (i >= 0 && i < W && j >= 0 && j < H) g[j * W + i] = c; };
  const side = dir === 'left' || dir === 'right';
  const L = look, cd = shade(cloth, -0.28), cl = shade(cloth, 0.18), hd = shade(L.hair, -0.25);
  const bw = L.build === 'slim' ? 6 : L.build === 'broad' ? 10 : 8, bx = 8 - bw / 2;
  const step = frame === 0 ? 0 : frame === 1 ? 1 : -1;
  // legs + shoes
  if (!side) {
    const lw = L.build === 'broad' ? 3 : 2, lx = 8 - lw - 1 + (L.build === 'slim' ? 1 : 0), rx = 9 - (L.build === 'slim' ? 1 : 0);
    R(lx, 18, lw, step === 1 ? 3 : 4, L.pants); R(rx, 18, lw, step === -1 ? 3 : 4, L.pants);
    R(lx, step === 1 ? 21 : 22, lw, 1, '#3a2c28'); R(rx, step === -1 ? 21 : 22, lw, 1, '#3a2c28');
  } else {
    if (step === 0) { R(6, 18, 4, 4, L.pants); R(6, 22, 5, 1, '#3a2c28'); }
    else { R(5 - step, 18, 2, 4, L.pants); R(9 + step, 18, 2, 4, shade(L.pants, -0.15)); R(4 - step, 22, 3, 1, '#3a2c28'); R(9 + step, 22, 3, 1, '#3a2c28'); }
  }
  // torso
  if (!side) {
    R(bx, 11, bw, 7, cloth); R(bx, 16, bw, 2, cd); R(bx + 1, 11, bw - 2, 1, cl);
    const swing = step;   // arms swing opposite the legs
    R(bx - 1, 12, 1, 4 + (swing === -1 ? 1 : 0), cd); R(bx + bw, 12, 1, 4 + (swing === 1 ? 1 : 0), cd);
    R(bx - 1, 16 + (swing === -1 ? 1 : 0), 1, 1, L.skin); R(bx + bw, 16 + (swing === 1 ? 1 : 0), 1, 1, L.skin);
    if (L.acc === 'apron' && dir === 'down') { R(bx + 1, 13, bw - 2, 5, '#f4efe6'); R(bx + 1, 13, bw - 2, 1, '#d8d0c2'); }
    if (L.acc === 'scarf') R(bx, 11, bw, 1, '#e8dcc8');
    if (L.acc === 'bag') { R(dir === 'down' ? bx + bw - 1 : bx, 12, 1, 5, '#6a4a2e'); }
  } else {
    const sw = bw - 2, sx = 8 - sw / 2;
    R(sx, 11, sw, 7, cloth); R(sx, 16, sw, 2, cd); R(sx + 1, 11, sw - 2, 1, cl);
    R(7 + step, 12, 2, 4, cd); R(7 + step, 16, 2, 1, L.skin);   // the near arm swings
    if (L.acc === 'apron') R(dir === 'left' ? sx : sx + sw - 1, 13, 1, 5, '#f4efe6');
    if (L.acc === 'bag') R(dir === 'left' ? sx + sw : sx - 1, 12, 1, 5, '#6a4a2e');
  }
  // head
  R(4, 2, 8, 9, L.skin);
  if (dir === 'down') {
    R(6, 6, 1, 2, '#2a2024'); R(9, 6, 1, 2, '#2a2024'); R(5, 8, 1, 1, '#e8a4a0'); R(10, 8, 1, 1, '#e8a4a0');
  } else if (side) {
    R(dir === 'left' ? 5 : 10, 6, 1, 2, '#2a2024'); R(dir === 'left' ? 3 : 12, 7, 1, 1, L.skin);
  }
  // hair
  const H0 = L.hair;
  const cap = () => { R(4, 2, 8, 3, H0); R(5, 1, 6, 1, H0); };
  if (dir === 'up') {
    R(4, 1, 8, 10, H0); R(5, 1, 6, 1, H0);
    if (L.style === 'receding') { R(4, 1, 8, 10, L.skin); R(4, 5, 8, 5, H0); R(5, 9, 6, 1, hd); }
    if (L.style === 'long') R(3, 4, 10, 10, H0);
    if (L.style === 'bob') R(3, 3, 10, 6, H0);
    if (L.style === 'ponytail') R(7, 10, 2, 5, H0);
  } else {
    if (L.style === 'receding') { R(4, 3, 1, 4, H0); R(11, 3, 1, 4, H0); R(5, 2, 6, 1, shade(H0, -0.08)); }
    else cap();
    if (L.style === 'short') { R(4, 5, 1, 2, H0); R(11, 5, 1, 2, H0); R(5, 5, 2, 1, H0); }
    if (L.style === 'long') { if (side) R(dir === 'left' ? 8 : 4, 2, 4, 12, H0); else { R(3, 3, 1, 11, H0); R(12, 3, 1, 11, H0); R(4, 5, 1, 8, H0); R(11, 5, 1, 8, H0); } }
    if (L.style === 'bob') { if (side) R(dir === 'left' ? 8 : 4, 2, 4, 8, H0); else { R(3, 3, 1, 6, H0); R(12, 3, 1, 6, H0); } R(5, 5, 6, 1, H0); }
    if (L.style === 'ponytail') { R(4, 5, 2, 1, H0); R(10, 5, 2, 1, H0); if (side) R(dir === 'left' ? 11 : 3, 4, 2, 7, H0); }
    if (L.style === 'spiky') { R(4, 1, 1, 1, H0); R(6, 0, 1, 2, H0); R(9, 0, 1, 2, H0); R(11, 1, 1, 1, H0); R(4, 5, 1, 1, H0); R(11, 5, 1, 1, H0); }
    if (L.style === 'chef') { R(4, 4, 8, 1, H0); R(5, 0, 6, 4, '#f6f2ea'); R(4, 3, 8, 1, '#e4ddd0'); }
    if (L.style === 'cap') { R(4, 1, 8, 3, cd); R(side ? (dir === 'left' ? 2 : 10) : 3, 4, side ? 4 : 10, 1, shade(cd, -0.2)); R(4, 4, 1, 2, H0); R(11, 4, 1, 2, H0); }
    if (side && (L.style === 'short' || L.style === 'cap' || L.style === 'spiky' || L.style === 'chef')) R(dir === 'left' ? 9 : 4, 3, 3, 4, H0);
    if (L.style === 'chef' && side) R(5, 0, 6, 4, '#f6f2ea');
  }
  if (dir === 'down' && L.acc === 'glasses') { R(5, 6, 3, 1, '#2a2024'); R(8, 6, 3, 1, '#2a2024'); R(6, 6, 1, 1, '#cfe3ea'); R(9, 6, 1, 1, '#cfe3ea'); }
  if (side && L.acc === 'glasses') R(dir === 'left' ? 4 : 9, 6, 3, 1, '#2a2024');
  if (L.acc === 'goggles' && dir !== 'up') R(4, 3, 8, 1, '#5fb0c8');
  // auto outline: every empty pixel touching a filled one
  const out = g.slice();
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) if (!g[y * W + x]) {
    if ((x > 0 && g[y * W + x - 1]) || (x < W - 1 && g[y * W + x + 1]) || (y > 0 && g[(y - 1) * W + x]) || (y < H - 1 && g[(y + 1) * W + x])) out[y * W + x] = OUT;
  }
  const c = canvas(W, H), ctx = c.getContext('2d');
  for (let i = 0; i < out.length; i++) if (out[i]) { ctx.fillStyle = out[i]; ctx.fillRect(i % W, (i / W) | 0, 1, 1); }
  return c;
}
export const lookOf = id => LOOKS[id] || LOOKS.jiji;

// UI avatar: the head-and-shoulders of the standing front frame, as a data URL.
const AVATAR = new Map();
export function avatarURL(id) {
  if (AVATAR.has(id)) return AVATAR.get(id);
  const cloth = getComputedStyle(document.documentElement).getPropertyValue(`--r-${id}`).trim() || '#8a7a6a';
  const src = charCanvas(cloth, lookOf(id), 'down', 0), c = canvas(16, 16);
  c.getContext('2d').drawImage(src, 0, 0, 16, 16, 0, 0, 16, 16);
  const url = c.toDataURL(); AVATAR.set(id, url); return url;
}

/* ------------------------------------------------------------------ small icons */
export function iconCanvas(kind) {
  const c = canvas(8, 8), x = c.getContext('2d'), P = (a, b, w, h, col) => { x.fillStyle = col; x.fillRect(a, b, w, h); };
  if (kind === 'eat') { P(1, 4, 6, 2, '#f4efe6'); P(2, 6, 4, 1, '#d8d0c2'); P(2, 2, 1, 2, '#c97a3a'); P(4, 1, 1, 3, '#c97a3a'); }
  if (kind === 'work') { P(1, 3, 6, 4, '#7a5a3a'); P(3, 2, 2, 1, '#5a4028'); P(1, 4, 6, 1, '#9a7a52'); }
  if (kind === 'zzz') { P(1, 1, 3, 1, '#e8f0ff'); P(3, 2, 1, 1, '#e8f0ff'); P(2, 3, 1, 1, '#e8f0ff'); P(1, 4, 3, 1, '#e8f0ff'); P(5, 4, 2, 1, '#c8d4f0'); P(6, 5, 1, 1, '#c8d4f0'); P(5, 6, 2, 1, '#c8d4f0'); }
  if (kind === 'wrench') { P(2, 1, 3, 2, '#9aa0a8'); P(3, 3, 2, 4, '#7a8088'); P(3, 1, 1, 1, '#fff0'); }
  return c;
}

/* ------------------------------------------------------------------ decor */

/* ------------------------------------------------------------------ outline + trees */
// The first rule of pixel art: every object gets a 1px dark outline. Padded by 1px all
// round, so nothing is clipped at the canvas edge.
const OUTLINE = [43, 34, 30];
export function outlined(src) {
  const W = src.width + 2, H = src.height + 2, c = canvas(W, H), x = c.getContext('2d');
  x.drawImage(src, 1, 1);
  const img = x.getImageData(0, 0, W, H), d = img.data, a = i => d[i * 4 + 3];
  const out = [];
  for (let y = 0; y < H; y++) for (let k = 0; k < W; k++) {
    const i = y * W + k; if (a(i) >= 100) continue;
    if ((k > 0 && a(i - 1) >= 200) || (k < W - 1 && a(i + 1) >= 200) || (y > 0 && a(i - W) >= 200) || (y < H - 1 && a(i + W) >= 200)) out.push(i);
  }
  for (const i of out) { d[i * 4] = OUTLINE[0]; d[i * 4 + 1] = OUTLINE[1]; d[i * 4 + 2] = OUTLINE[2]; d[i * 4 + 3] = 255; }
  x.putImageData(img, 0, 0);
  return c;
}
// Leaf-cluster canopies: several irregular clumps, each lit from the top-left in four
// steps (highlight / light / base / shadow) with a deep shade under the canopy and
// 2px leaf texture -- no smooth blobs.
const RAMPS = {
  tree: ['#b4d870', '#86b84e', '#5e9438', '#3f6e2a', '#2b5020'],
  oak: ['#a8cc64', '#78a846', '#527e32', '#3a6026', '#284419'],
  blossom: ['#fff0f4', '#f8bcd0', '#e892b2', '#c4698e', '#99496c'],
  bush: ['#9cc860', '#6e9e40', '#4c7e30', '#365e24', '#25421a'],
};
function canopy(P, W, cx, cy, rad, n, ramp, seed, flowers) {
  const cl = [];
  for (let k = 0; k < n; k++) {
    const ang = k / n * Math.PI * 2 + hash(k, seed, 1) * 0.8, dist = rad * (k === 0 ? 0 : 0.45 + hash(k, seed, 2) * 0.15);
    cl.push({x: cx + Math.cos(ang) * dist, y: cy + Math.sin(ang) * dist * 0.85, r: rad * (k === 0 ? 0.62 : 0.5 + hash(k, seed, 3) * 0.14)});
  }
  cl.sort((p, q) => p.y - q.y);   // lower clumps overlap the upper ones
  for (const c of cl) for (let y = Math.floor(c.y - c.r - 2); y <= c.y + c.r + 2; y++) for (let x = Math.floor(c.x - c.r - 2); x <= c.x + c.r + 2; x++) {
    const dx = x - c.x, dy = y - c.y, edge = c.r + (hash(x, y, seed) - 0.5) * 2.4;
    if (dx * dx + dy * dy > edge * edge) continue;
    const lit = -(dx * 0.6 + dy * 0.8) / c.r + (hash(x >> 1, y >> 1, seed + 7) - 0.5) * 0.5;
    let t = lit > 0.55 ? 0 : lit > 0.15 ? 1 : lit > -0.35 ? 2 : 3;
    if (y > cy + rad * 0.45 && t < 3) t++;   // the underside of the whole canopy sits in shade
    if (dx * 0.6 + dy * 0.8 > c.r * 0.82) t = 4;   // a dark rim separates the clumps
    P(x, y, 1, 1, ramp[t]);
  }
  if (flowers) for (let i = 0; i < 18; i++) P(cx - rad + hash(i, seed, 9) * rad * 2 | 0, cy - rad + hash(i, seed, 10) * rad * 1.6 | 0, 1, 1, '#ffffff');
}
function trunk(P, x, top, bottom, w) {
  P(x, top, w, bottom - top, '#7a5432'); P(x, top, 1, bottom - top, '#a87848'); P(x + w - 1, top, 1, bottom - top, '#553820');
  P(x - 1, bottom - 2, w + 2, 2, '#6a4a2c'); P(x - 1, bottom - 1, 1, 1, '#553820');
}
export function treeCanvas(type) {
  const size = {tree: [36, 46], pine: [28, 48], blossom: [36, 44], bush: [24, 16], shrub: [20, 14]}[type] || [32, 40];
  const c = canvas(size[0], size[1]), x = c.getContext('2d'), P = (a, b, w, h, col) => { x.fillStyle = col; x.fillRect(a, b, w, h); };
  const W = size[0], H = size[1];
  if (type === 'pine') {
    trunk(P, 12, H - 10, H, 4);
    const tiers = 5, ramp = ['#7cb070', '#4e8a50', '#3a6e42', '#2a5434', '#1d3e28'];
    for (let t = 0; t < tiers; t++) {
      const top = 2 + t * 7, hgt = 12, half0 = 3 + t * 2.2;
      for (let j = 0; j < hgt; j++) {
        const half = Math.round(half0 * (j + 2) / hgt + 1 + (hash(t, j, 4) < .3 ? 1 : 0)), y = top + j;
        for (let i = -half; i <= half; i++) {
          let s = i < -half * 0.4 ? 1 : i < half * 0.3 ? 2 : 3;
          if (j === 0 && Math.abs(i) < 2) s = 0;
          if (j >= hgt - 2) s = Math.min(4, s + 1);
          if (hash(i + 20, y, 8) < .12) s = Math.max(0, s - 1);
          P(14 + i, y, 1, 1, ramp[s]);
        }
      }
    }
    return c;
  }
  if (type === 'bush' || type === 'shrub') { canopy(P, W, W / 2, H / 2 + 1, H / 2 - 1, 4, RAMPS.bush, type === 'bush' ? 3 : 4, false);
    if (type === 'bush') { P(6, 9, 1, 1, '#c8402e'); P(15, 10, 1, 1, '#c8402e'); P(11, 5, 1, 1, '#e05a46'); } return c; }
  trunk(P, W / 2 - 2, H - 16, H, 5);
  P(W / 2 + 2, H - 14, 4, 2, '#7a5432');   // a branch
  canopy(P, W, W / 2, 17, 15, 5, type === 'blossom' ? RAMPS.blossom : RAMPS[hash(W, H, 2) < .5 ? 'tree' : 'oak'], type === 'blossom' ? 11 : 5, type === 'blossom');
  return c;
}

export function decorCanvas(type) {
  if (type === 'tree' || type === 'pine' || type === 'blossom' || type === 'bush' || type === 'shrub') return outlined(treeCanvas(type));
  const T = {tree:[32, 42], pine:[24, 44], blossom:[32, 40], shrub:[18, 12], flowers:[30, 12], lamp:[8, 30], bench:[24, 14],
    board:[22, 26], crate:[12, 10], barrel:[10, 12], basket:[12, 8], fountain:[40, 30], sign:[16, 16],
    washline:[40, 20], woodpile:[22, 12], mailbox:[8, 16], pot:[8, 10], well:[24, 26], bike:[20, 13], terrace:[28, 29],
    sandwich:[12, 16], sacks:[18, 12], postbox:[10, 18], stall:[28, 28],
    bush:[22, 15], rock:[12, 8], grass:[12, 9], bloom:[10, 8], stump:[12, 9], log:[22, 9]}[type] || [8, 8];
  const c = canvas(T[0], T[1]), x = c.getContext('2d'), P = (a, b, w, h, col) => { x.fillStyle = col; x.fillRect(a, b, w, h); };
  const blob = (cx, cy, r, col) => { for (let j = -r; j <= r; j++) { const w = Math.round(Math.sqrt(r * r - j * j)); P(cx - w, cy + j, 2 * w + 1, 1, col); } };
  if (type === 'tree') {
    P(14, 28, 4, 12, '#7a5432'); P(14, 28, 1, 12, '#5e3f24'); P(10, 40, 12, 2, 'rgba(0,0,0,.12)');
    blob(16, 17, 13, '#3f6e3a'); blob(14, 14, 11, '#4f8746'); blob(12, 11, 7, '#67a057'); blob(19, 19, 6, '#457a3f');
    for (let i = 0; i < 14; i++) P(5 + Math.floor(hash(i, 1) * 22), 5 + Math.floor(hash(i, 2) * 22), 1, 1, '#7fb76b');
  }
  if (type === 'pine') {
    P(11, 34, 3, 8, '#6a4a2e'); P(7, 42, 10, 2, 'rgba(0,0,0,.12)');
    for (let r = 0; r < 4; r++) { const top = 2 + r * 8, w = 4 + r * 3; for (let j = 0; j < 10; j++) P(12 - Math.round(w * j / 10), top + j, Math.round(2 * w * j / 10) + 1, 1, r % 2 ? '#2f5b3e' : '#386b47'); }
    P(12, 2, 1, 30, '#2a4f36');
  }
  if (type === 'blossom') {
    P(14, 26, 4, 12, '#7a5432'); P(10, 38, 12, 2, 'rgba(0,0,0,.12)');
    blob(16, 15, 12, '#d98aa6'); blob(13, 12, 9, '#eaa9c0'); blob(19, 17, 6, '#c9768f');
    for (let i = 0; i < 16; i++) P(5 + Math.floor(hash(i, 3) * 22), 4 + Math.floor(hash(i, 4) * 20), 1, 1, '#f8dbe5');
  }
  if (type === 'shrub') { blob(9, 7, 5, '#4a7d40'); blob(7, 6, 3, '#5f964f'); P(3, 11, 12, 1, 'rgba(0,0,0,.1)'); }
  if (type === 'flowers') {
    P(0, 6, 30, 6, '#7a5a3a'); P(1, 7, 28, 4, '#5e4630');
    for (let i = 0; i < 12; i++) { const fx = 2 + i * 2.3 | 0, col = ['#f2d45c', '#e8708a', '#f4efe6', '#b07ad0'][i % 4]; P(fx, 3 + (i % 3), 1, 3, '#4f8746'); P(fx - 1 + (i % 2), 2 + (i % 3), 2, 2, col); }
  }
  if (type === 'lamp') { P(3, 6, 2, 22, '#3a3a40'); P(2, 28, 4, 2, '#2a2a30'); P(1, 2, 6, 5, '#3a3a40'); P(2, 3, 4, 3, '#ffe9a8'); P(3, 0, 2, 2, '#3a3a40'); }
  if (type === 'bench') { P(1, 4, 22, 3, '#9a6a3e'); P(1, 4, 22, 1, '#b8844e'); P(1, 0, 22, 2, '#8a5c34'); P(2, 7, 2, 6, '#5e3f24'); P(20, 7, 2, 6, '#5e3f24'); }
  if (type === 'board') {
    P(2, 4, 2, 22, '#6a4a2e'); P(18, 4, 2, 22, '#6a4a2e'); P(0, 2, 22, 14, '#8a6440'); P(1, 3, 20, 12, '#c9a66e');
    P(3, 5, 6, 5, '#f4efe6'); P(10, 4, 7, 6, '#efe0b8'); P(12, 10, 6, 4, '#f4efe6'); P(5, 11, 4, 3, '#e8c8c0'); P(4, 6, 4, 1, '#9a8a7a'); P(11, 6, 5, 1, '#9a8a7a'); P(16, 11, 2, 2, '#e8708a'); P(8, 4, 2, 2, '#5fb0c8'); P(2, 12, 2, 2, '#f2d45c'); P(13, 3, 1, 1, '#c8402e'); P(6, 10, 1, 1, '#c8402e');
  }
  if (type === 'crate') { P(0, 0, 12, 10, '#a57a48'); P(1, 1, 10, 8, '#c49558'); P(1, 4, 10, 1, '#8a6238'); P(5, 1, 1, 8, '#8a6238'); }
  if (type === 'barrel') { P(1, 0, 8, 12, '#8a5c34'); P(0, 2, 10, 8, '#9a6a3e'); P(0, 3, 10, 1, '#5a5a60'); P(0, 8, 10, 1, '#5a5a60'); }
  if (type === 'basket') { P(0, 3, 12, 5, '#b8844e'); P(1, 4, 10, 1, '#9a6a3e'); P(2, 1, 3, 3, '#e04848'); P(5, 0, 3, 3, '#f2a83a'); P(8, 1, 3, 3, '#6aa848'); }
  if (type === 'washline') {   // two posts, a sagging line, laundry
    P(1, 4, 2, 16, '#6a4a2e'); P(37, 4, 2, 16, '#6a4a2e');
    for (let i = 0; i < 34; i++) P(3 + i, 5 + Math.round(2 * Math.sin(Math.PI * i / 34)), 1, 1, '#e8e0d2');
    const cl = [['#d46a5a', 6, 6], ['#f4efe6', 9, 8], ['#6e8fc4', 7, 7], ['#e8c860', 6, 5]]; let x0 = 6;
    for (const [col, w, h] of cl) { const yy = 6 + Math.round(2 * Math.sin(Math.PI * (x0 - 3) / 34)); P(x0, yy, w, h, col); P(x0, yy + h - 1, w, 1, shade(col, -0.2)); x0 += w + 2; }
  }
  if (type === 'woodpile') { for (let r = 0; r < 3; r++) for (let k = 0; k < 4 - r; k++) { const x0 = 1 + k * 5 + r * 2.5 | 0, y0 = 8 - r * 3; P(x0, y0, 5, 3, '#8a5c34'); P(x0 + 1, y0 + 1, 2, 1, '#c8935a'); } P(0, 11, 22, 1, 'rgba(0,0,0,.15)'); }
  if (type === 'mailbox') { P(3, 6, 2, 10, '#6a4a2e'); P(0, 1, 8, 6, '#b8463a'); P(1, 2, 6, 1, '#d8665a'); P(6, 0, 1, 3, '#e8c860'); }
  if (type === 'pot') { P(1, 5, 6, 5, '#b8683e'); P(0, 4, 8, 2, '#c87a4a'); P(2, 1, 1, 3, '#4f8746'); P(4, 0, 1, 4, '#4f8746'); P(5, 2, 1, 2, '#4f8746'); P(1, 0, 2, 2, '#e8708a'); P(4, 0, 2, 1, '#f2d45c'); }
  if (type === 'well') {
    P(2, 14, 20, 12, '#9d9484'); P(2, 14, 20, 2, '#b9b09e'); for (let i = 0; i < 20; i += 5) P(2 + i, 18 + (i % 2) * 3, 4, 1, '#8a8274'); P(5, 14, 14, 2, '#3f6a8a');
    P(3, 2, 2, 13, '#6a4a2e'); P(19, 2, 2, 13, '#6a4a2e'); P(1, 0, 22, 3, '#8a5c34'); P(11, 3, 2, 6, '#c8c0b4'); P(9, 8, 6, 4, '#8a6a4a');
  }
  if (type === 'bike') {
    const wheel = cx => { P(cx - 3, 6, 7, 1, '#3a3a40'); P(cx - 3, 12, 7, 1, '#3a3a40'); P(cx - 4, 7, 1, 5, '#3a3a40'); P(cx + 4, 7, 1, 5, '#3a3a40'); };
    wheel(4); wheel(16); P(4, 8, 12, 1, '#2f7d8a'); P(9, 4, 1, 5, '#2f7d8a'); P(7, 3, 4, 1, '#3a3a40'); P(15, 3, 2, 1, '#3a3a40'); P(15, 3, 1, 6, '#2f7d8a');
  }
  if (type === 'terrace') {   // café table, two chairs, a parasol
    P(13, 0, 2, 22, '#7a5a40'); for (let r = 0; r < 6; r++) P(14 - (4 + r * 2), 2 + r, 8 + r * 4, 1, r % 2 ? '#c8553d' : '#f4efe6');
    P(8, 20, 12, 2, '#9a6a3e'); P(13, 22, 2, 6, '#6a4a2e'); P(9, 28, 10, 1, '#6a4a2e');
    P(1, 22, 5, 2, '#8a5c34'); P(1, 18, 1, 4, '#8a5c34'); P(1, 24, 1, 5, '#6a4a2e'); P(5, 24, 1, 5, '#6a4a2e');
    P(22, 22, 5, 2, '#8a5c34'); P(26, 18, 1, 4, '#8a5c34'); P(22, 24, 1, 5, '#6a4a2e'); P(26, 24, 1, 5, '#6a4a2e');
    P(10, 18, 2, 2, '#f4efe6'); P(16, 18, 2, 2, '#f4efe6');
  }
  if (type === 'sandwich') { P(1, 0, 10, 14, '#3a3a40'); P(2, 1, 8, 12, '#2f3a34'); P(3, 3, 6, 1, '#f4efe6'); P(3, 6, 4, 1, '#e8c860'); P(3, 9, 5, 1, '#f4efe6'); P(0, 14, 2, 2, '#3a3a40'); P(10, 14, 2, 2, '#3a3a40'); }
  if (type === 'sacks') { for (let k = 0; k < 3; k++) { const x0 = k * 6, y0 = k === 1 ? 0 : 3; P(x0, y0 + 2, 6, 9 - (k === 1 ? 0 : 3), '#e8dcc0'); P(x0 + 1, y0, 4, 3, '#d8c8a4'); P(x0 + 1, y0 + 5, 4, 1, '#c8a870'); } }
  if (type === 'postbox') { P(3, 8, 4, 10, '#4a4a50'); P(0, 0, 10, 10, '#c8402e'); P(0, 0, 10, 2, '#a8301e'); P(2, 4, 6, 1, '#2a2024'); P(3, 7, 4, 1, '#e8c860'); }
  if (type === 'stall') {
    P(2, 8, 2, 20, '#6a4a2e'); P(24, 8, 2, 20, '#6a4a2e'); for (let i = 0; i < 28; i += 4) P(i, 2, 4, 7, (i / 4) % 2 ? '#f4efe6' : '#7a9a3a');
    P(2, 18, 24, 9, '#a57a48'); P(2, 18, 24, 2, '#c49558'); for (let i = 0; i < 7; i++) P(4 + i * 3, 15, 3, 3, ['#e04848', '#f2a83a', '#6aa848', '#8e6fb3'][i % 4]);
  }
  if (type === 'bush') { blob(11, 9, 6, '#3f6e3a'); blob(7, 8, 4, '#4f8746'); blob(15, 7, 5, '#4a7d40'); blob(9, 6, 3, '#67a057'); P(13, 4, 2, 1, '#7fb76b'); P(5, 7, 1, 1, '#7fb76b');
    P(6, 9, 1, 1, '#c8402e'); P(14, 10, 1, 1, '#c8402e'); P(10, 5, 1, 1, '#c8402e'); }
  if (type === 'rock') { P(2, 2, 8, 5, '#9d9686'); P(1, 4, 10, 3, '#8a8374'); P(3, 1, 5, 2, '#c9c2b2'); P(4, 2, 2, 1, '#e0dacd'); P(2, 7, 9, 1, '#6f6a5e'); }
  if (type === 'grass') { for (let i = 0; i < 6; i++) { const x0 = 1 + i * 2; P(x0, 3 + (i % 3), 1, 6 - (i % 3), i % 2 ? '#5f8f52' : '#6f9a4e'); P(x0, 2 + (i % 3), 1, 1, '#8fbe62'); } }
  if (type === 'bloom') { P(1, 4, 8, 4, '#5f8f52'); P(2, 2, 2, 2, '#f2d45c'); P(5, 1, 2, 2, '#f4efe6'); P(7, 3, 2, 2, '#e8708a'); P(3, 3, 1, 1, '#c8873c'); }
  if (type === 'stump') { P(1, 3, 10, 6, '#7a5432'); P(1, 2, 10, 2, '#c8a070'); P(3, 2, 6, 1, '#a8804e'); P(5, 3, 2, 1, '#8a6238'); P(0, 8, 12, 1, '#5e3f24'); }
  if (type === 'log') { P(1, 2, 18, 6, '#7a5432'); P(1, 2, 18, 2, '#9a6a3e'); P(18, 2, 4, 6, '#c8a070'); P(19, 3, 2, 3, '#a8804e'); P(6, 4, 1, 3, '#5e3f24'); P(12, 5, 1, 2, '#5e3f24'); P(3, 0, 2, 2, '#5f8f52'); }
  if (type === 'fountain') {
    blob(20, 20, 16, '#9d9484'); blob(20, 20, 14, '#b9b09e'); blob(20, 20, 11, '#5b93b8'); blob(18, 18, 4, '#8fc0dc');
    P(18, 6, 4, 14, '#a99f8c'); P(17, 4, 6, 3, '#c9c0ae'); P(19, 1, 2, 4, '#bfe0f0');
  }
  return outlined(c);
}

/* ------------------------------------------------------------------ buildings */
// Exteriors at ~1.6x the old size: individually drawn roof tiles, framed windows with
// sills, wall materials (plaster + timber, clapboard, brick, concrete), signboards.
// Each returns {c, windows:[{x,y,w,h}], w, h, chimney}; door = bottom centre.
export const BUILDING_SIZE = {home: [128, 128], cafe: [160, 128], bakery: [160, 128], office: [144, 160], market: [176, 104]};
function painter(ctx) {
  const P = (x, y, w, h, c) => { ctx.fillStyle = c; ctx.fillRect(x, y, w, h); };
  const tiles = (x0, y0, w, rows, col, rowFn) => {   // individually drawn roof tiles, staggered rows
    for (let r = 0; r < rows; r++) {
      const [rx, rw] = rowFn ? rowFn(r) : [x0, w], y = y0 + r * 5;
      for (let i = -((r % 2) * 4); i < rw; i += 8) {
        const tx = rx + Math.max(0, i), tw = Math.min(8, rw - Math.max(0, i), i < 0 ? 8 + i : 8); if (tw <= 0) continue;
        const k = hash(r, i + rx, 5) * 0.12 - 0.06, base = shade(col, k);
        P(tx, y, tw, 5, base); P(tx, y, tw, 1, shade(base, 0.22)); P(tx, y + 4, tw, 1, shade(base, -0.35)); P(tx + tw - 1, y + 1, 1, 3, shade(base, -0.2));
      }
    }
  };
  const plaster = (x, y, w, h, col) => { P(x, y, w, h, col); for (let i = 0; i < w * h / 26; i++) P(x + (hash(i, x, 7) * w | 0), y + (hash(i, y, 8) * h | 0), 1, 1, shade(col, hash(i, 9, 9) < .5 ? -0.06 : 0.05)); };
  const clap = (x, y, w, h, col) => { for (let j = 0; j < h; j += 5) { P(x, y + j, w, 5, shade(col, (j / 5) % 2 ? 0 : 0.03)); P(x, y + j + 4, w, 1, shade(col, -0.22)); } };
  const brick = (x, y, w, h, col) => { P(x, y, w, h, shade(col, -0.35));
    for (let j = 0; j < h; j += 5) for (let i = ((j / 5) % 2) * -5; i < w; i += 10) { const bx = Math.max(x, x + i), bw = Math.min(9, x + i + 9 - bx, x + w - bx); if (bw > 0) P(bx, y + j, bw, 4, shade(col, hash(i, j, 3) * 0.14 - 0.07)); } };
  const win = (x, y, w, h, frame, o = {}) => {   // frame, glass with a glint, mullions, sill (+ shutters / flower box)
    if (o.shutters) { P(x - 7, y - 1, 5, h + 2, o.shutters); P(x + w + 2, y - 1, 5, h + 2, o.shutters); for (let j = 1; j < h; j += 3) { P(x - 6, y + j, 3, 1, shade(o.shutters, -0.25)); P(x + w + 3, y + j, 3, 1, shade(o.shutters, -0.25)); } }
    P(x - 2, y - 2, w + 4, h + 4, frame); P(x, y, w, h, '#9cc0d0'); P(x, y, w, 2, '#c8e0ea');
    for (let i = 0; i < Math.min(w, h); i += 1) if (i % 5 < 2) P(x + 2 + i, y + h - 3 - i, 1, 1, '#e4f2f6');
    P(x + (w >> 1), y, 1, h, frame); if (h > 12) P(x, y + (h >> 1), w, 1, frame);
    P(x - 3, y + h + 2, w + 6, 2, shade(frame, 0.15)); P(x - 3, y + h + 4, w + 6, 1, shade(frame, -0.35));
    if (o.box) { P(x - 1, y + h + 5, w + 2, 4, '#7a5a3a'); for (let i = 0; i < w; i += 3) { P(x + i, y + h + 3, 2, 2, ['#e8708a', '#f2d45c', '#f4efe6', '#b07ad0'][(i / 3) % 4]); P(x + i + 1, y + h + 4, 1, 1, '#4f8746'); } }
  };
  const door = (x, y, w, h, col, o = {}) => {
    P(x - 2, y - 2, w + 4, h + 2, shade(col, -0.35)); P(x, y, w, h, col);
    P(x + 2, y + 3, w - 4, (h >> 1) - 4, shade(col, -0.12)); P(x + 2, y + (h >> 1) + 2, w - 4, (h >> 1) - 5, shade(col, -0.12));
    if (o.glass) { P(x + 3, y + 4, w - 6, (h >> 1) - 4, '#9cc0d0'); P(x + 3, y + 4, w - 6, 1, '#c8e0ea'); }
    P(x + w - 5, y + (h >> 1), 2, 2, '#e8c860'); P(x - 4, y + h, w + 8, 2, '#b9b09e'); P(x - 4, y + h + 2, w + 8, 1, '#8f8676');
  };
  return {P, tiles, plaster, clap, brick, win, door};
}
export function buildingCanvas(loc) {
  const a = buildingRaw(loc), sh = o => o && {...o, x: o.x + 1, y: o.y + 1};
  return {c: outlined(a.c), windows: a.windows.map(sh), w: a.w + 2, h: a.h + 2, chimney: sh(a.chimney)};
}
function buildingRaw(loc) {
  const kind = loc.kind, [W, H] = BUILDING_SIZE[kind] || [128, 128];
  const c = canvas(W, H), ctx = c.getContext('2d'), {P, tiles, plaster, clap, brick, win, door} = painter(ctx), wins = [];
  const ground = () => { P(6, H - 3, W - 12, 3, 'rgba(0,0,0,.16)'); };
  if (kind === 'home') {
    const a = loc.id !== 'home_b', roof = a ? '#b8603e' : '#5f78a8';
    if (a) { plaster(10, 56, 108, 68, '#efe2c8'); for (const bx of [10, 40, 86, 116]) P(bx, 56, 3, 68, '#6e4a30'); P(10, 56, 108, 3, '#6e4a30'); P(10, 88, 108, 2, '#6e4a30'); for (let i = 0; i < 26; i += 2) { P(13 + i, 59 + i, 2, 2, '#6e4a30'); P(113 - i, 59 + i, 2, 2, '#6e4a30'); } }
    else { clap(10, 56, 108, 68, '#cdd6dc'); P(10, 56, 3, 68, '#e8eef2'); P(115, 56, 3, 68, '#e8eef2'); }
    P(112, 56, 6, 68, 'rgba(0,0,0,.12)');
    P(88, 6, 14, 30, '#8a6a52'); for (let j = 8; j < 34; j += 4) P(88, j, 14, 1, '#6f5240'); P(86, 4, 18, 4, '#6f5240');   // chimney
    tiles(0, 4, W, 11, roof, r => { const hw = 14 + r * 5; return [64 - hw, hw * 2]; });
    P(2, 58, 124, 3, shade(roof, -0.45));
    win(20, 72, 24, 20, '#6a4a34', {box: true, shutters: a ? '#4f7a4a' : '#8a5a5a'}); win(84, 72, 24, 20, '#6a4a34', {box: true, shutters: a ? '#4f7a4a' : '#8a5a5a'});
    wins.push({x: 20, y: 72, w: 24, h: 20}, {x: 84, y: 72, w: 24, h: 20});
    win(58, 30, 12, 12, '#6a4a34'); wins.push({x: 58, y: 30, w: 12, h: 12});   // attic window
    door(55, 90, 18, 32, a ? '#8a5a34' : '#5a6a8a');
    P(80, 92, 4, 6, '#3a3a40'); P(81, 93, 2, 3, '#ffe9a8');   // wall lamp
    ground();
    return {c, windows: wins, w: W, h: H, chimney: {x: 95, y: 4}};
  }
  if (kind === 'cafe') {
    plaster(8, 22, 144, 36, '#f2e6d2'); P(4, 14, 152, 9, '#8a6a52'); P(4, 14, 152, 2, '#a88a6e'); P(4, 22, 152, 2, '#6a4e3a');
    P(30, 28, 100, 22, '#5a3a28'); P(32, 30, 96, 18, '#6a4a34');   // signboard
    P(40, 33, 12, 10, '#f4efe6'); P(52, 35, 3, 5, '#f4efe6'); P(42, 31, 2, 2, '#c8c0b4'); P(46, 30, 2, 3, '#c8c0b4');
    for (let i = 0; i < 5; i++) P(64 + i * 11, 34, 8, 9, '#e8d8b8');   // lettering blocks
    brick(8, 70, 144, 54, '#b4573e');
    for (let i = 0; i < 152; i += 10) { P(4 + i, 56, 10, 12, (i / 10) % 2 ? '#f4efe6' : '#c8553d'); P(4 + i, 67, 10, 1, 'rgba(0,0,0,.18)'); P(5 + i, 68, 8, 2, (i / 10) % 2 ? '#e8e0d2' : '#a8402e'); }
    win(16, 78, 64, 34, '#4a3424'); wins.push({x: 16, y: 78, w: 64, h: 34});
    P(18, 100, 60, 4, '#8a5c34'); for (let i = 0; i < 5; i++) P(22 + i * 11, 95, 5, 5, i % 2 ? '#f4efe6' : '#c8553d');
    door(100, 80, 22, 42, '#5a3a28', {glass: true}); wins.push({x: 103, y: 84, w: 16, h: 17});
    P(132, 76, 14, 18, '#5a3a28'); P(133, 77, 12, 16, '#f4efe6'); P(136, 81, 6, 6, '#8a5c34'); P(142, 82, 2, 3, '#8a5c34');   // menu board
    P(90, 74, 4, 6, '#3a3a40'); P(91, 75, 2, 3, '#ffe9a8'); P(128, 74, 4, 6, '#3a3a40'); P(129, 75, 2, 3, '#ffe9a8');
    ground();
    return {c, windows: wins, w: W, h: H, chimney: null};
  }
  if (kind === 'bakery') {
    brick(8, 52, 144, 72, '#c8875a');
    P(120, 0, 14, 26, '#8a6a52'); for (let j = 2; j < 26; j += 4) P(120, j, 14, 1, '#6f5240'); P(118, 0, 18, 3, '#6f5240');
    tiles(2, 6, 156, 9, '#a8662e', r => [2 + r * 2, 156 - r * 4]);
    P(0, 50, 160, 3, '#6a3e20');
    P(66, 12, 26, 22, '#a8662e'); P(70, 16, 18, 16, '#7a4a28'); win(73, 18, 12, 11, '#6a4a34'); wins.push({x: 73, y: 18, w: 12, h: 11});   // dormer
    for (let i = 0; i < 64; i += 8) P(14 + i, 62, 8, 7, (i / 8) % 2 ? '#f4efe6' : '#d9a548');   // small awning
    win(16, 72, 60, 32, '#6a4228'); wins.push({x: 16, y: 72, w: 60, h: 32});
    P(18, 96, 56, 4, '#9a6a3e'); for (let i = 0; i < 6; i++) { P(20 + i * 9, 90, 7, 6, i % 2 ? '#c8873c' : '#b5732e'); P(21 + i * 9, 90, 5, 1, '#e0a860'); }
    door(96, 78, 22, 44, '#7a4a28', {glass: true});
    P(130, 56, 22, 22, '#6a4228'); P(132, 58, 18, 18, '#e8c890'); P(135, 64, 12, 7, '#c8873c'); P(136, 65, 10, 2, '#e8b070'); P(141, 52, 1, 4, '#3a3a40');   // bread sign
    P(126, 96, 18, 16, '#e8dcc0'); P(128, 94, 14, 4, '#d8c8a4');   // flour sack by the door
    ground();
    return {c, windows: wins, w: W, h: H, chimney: {x: 127, y: 0}};
  }
  if (kind === 'office') {
    brick(8, 20, 128, 134, '#a9563e');
    for (let i = 8; i < 136; i += 8) P(i, 8, 4, 5, '#7a4a36');   // parapet
    P(4, 12, 136, 9, '#7a4a36'); P(4, 12, 136, 2, '#a86a4e'); P(4, 20, 136, 2, '#5a3424');   // cornice
    P(112, 0, 12, 13, '#7a4a36'); P(112, 0, 12, 2, '#a86a4e'); P(114, 4, 8, 1, '#5a3424');
    for (const by of [62, 104]) { P(8, by, 128, 5, '#d8cdb8'); P(8, by, 128, 1, '#efe6d4'); P(8, by + 4, 128, 1, '#9e9584'); }   // stone bands
    P(130, 20, 6, 134, 'rgba(0,0,0,.14)');
    for (const fy of [30, 72]) for (const fx of [18, 50, 82, 114]) {   // upper floors: arched windows with keystones
      win(fx, fy, 14, 22, '#5a3a2a'); P(fx - 2, fy - 4, 18, 2, '#d8cdb8'); P(fx + 6, fy - 6, 2, 3, '#efe6d4'); wins.push({x: fx, y: fy, w: 14, h: 22});
    }
    win(16, 118, 22, 22, '#5a3a2a'); win(106, 118, 22, 22, '#5a3a2a'); wins.push({x: 16, y: 118, w: 22, h: 22}, {x: 106, y: 118, w: 22, h: 22});
    P(52, 108, 40, 9, '#c9a66e'); P(53, 109, 38, 1, '#e8c890'); for (let i = 0; i < 5; i++) P(57 + i * 7, 111, 4, 4, '#5a3a2a');   // name plaque
    P(54, 118, 36, 4, '#3a3a40'); P(54, 118, 36, 1, '#6a6a72');   // canopy
    door(58, 122, 28, 30, '#4a3424', {glass: true}); P(72, 122, 1, 30, '#2a1a14'); wins.push({x: 61, y: 126, w: 22, h: 12});
    P(40, 152, 64, 2, '#b9b09e'); P(36, 154, 72, 2, '#8f8676');
    ground();
    return {c, windows: wins, w: W, h: H, chimney: {x: 118, y: 0}};
  }
  // market: three stalls, scalloped canopies, crates of produce
  for (let s = 0; s < 3; s++) {
    const sx = 4 + s * 57, col = ['#c8553d', '#2f7d8a', '#c8962a'][s];
    P(sx + 3, 22, 3, 74, '#6a4a2e'); P(sx + 48, 22, 3, 74, '#6a4a2e');
    for (let i = 0; i < 54; i += 9) { P(sx + i, 8, 9, 14, (i / 9) % 2 ? '#f4efe6' : col); P(sx + i + 1, 22, 7, 2, (i / 9) % 2 ? '#e8e0d2' : shade(col, -0.25)); P(sx + i + 3, 24, 3, 1, (i / 9) % 2 ? '#e8e0d2' : shade(col, -0.25)); }
    P(sx, 6, 54, 2, shade(col, -0.4));
    P(sx + 3, 64, 48, 24, '#a57a48'); P(sx + 3, 64, 48, 4, '#c49558'); for (let i = 0; i < 48; i += 12) P(sx + 3 + i, 68, 1, 20, '#8a6238');
    const prod = [['#e04848', '#f2a83a', '#6aa848'], ['#8e6fb3', '#e04848', '#f2d45c'], ['#6aa848', '#c8873c', '#f2a83a']][s];
    for (let i = 0; i < 14; i++) { P(sx + 5 + i * 3.2 | 0, 58 + (i % 2) * 2, 3, 4, prod[i % 3]); P(sx + 5 + i * 3.2 | 0, 58 + (i % 2) * 2, 1, 1, '#ffffff'); }
    P(sx + 10, 30, 32, 10, '#f4efe6'); P(sx + 12, 33, 20, 2, '#6a4a2e'); P(sx + 12, 36, 14, 1, '#6a4a2e');   // price board
  }
  P(0, H - 4, W, 3, 'rgba(0,0,0,.14)');
  return {c, windows: wins, w: W, h: H, chimney: null};
}

/* ------------------------------------------------------------------ cutaways */
// The doll-house view of a building with people inside: the roof turns to a faint ghost,
// the room shows -- back wall, floor, cut side walls, a low front wall with a doorway --
// and the furniture from map JSON (interiors.<id>.furniture).
const ROOM = {home: {x0: 8, x1: 120, wall: 40, floor: 62, bottom: 122}, cafe: {x0: 6, x1: 154, wall: 38, floor: 62, bottom: 122},
  bakery: {x0: 6, x1: 154, wall: 38, floor: 60, bottom: 122}, office: {x0: 8, x1: 136, wall: 88, floor: 112, bottom: 152}};
const WALLPAPER = {home_a: ['#c8d8b8', '#b8cca6'], home_b: ['#ead8c0', '#e0c8a8'], cafe: ['#8a6a4a', '#7a5a3e'], bakery: ['#f0ece2', '#dcd6c8'], office: ['#d8c8a8', '#ccb898']};
const FLOOR = {home_a: 'planks', home_b: 'planks', cafe: 'check', bakery: 'terracotta', office: 'carpet'};
export function interiorCanvas(loc, ext, spec) {
  const [W, H] = BUILDING_SIZE[loc.kind], R = ROOM[loc.kind];
  const c = canvas(W, H), ctx = c.getContext('2d'), {P, win} = painter(ctx);
  ctx.globalAlpha = 0.2; ctx.drawImage(ext, -1, -1); ctx.globalAlpha = 1;   // the roof, lifted to a ghost
  const [w1, w2] = WALLPAPER[loc.id] || WALLPAPER.home_a;
  P(R.x0, R.wall, R.x1 - R.x0, R.floor - R.wall, w1);
  for (let x = R.x0; x < R.x1; x += 8) P(x, R.wall, 4, R.floor - R.wall, w2);
  P(R.x0, R.wall, R.x1 - R.x0, 2, '#f6efe0'); P(R.x0, R.wall + 2, R.x1 - R.x0, 1, '#6a5a4a');   // the cut top of the back wall
  P(R.x0, R.floor - 3, R.x1 - R.x0, 3, shade(w1, -0.35));   // skirting
  const fl = FLOOR[loc.id] || 'planks';
  for (let y = R.floor; y < R.bottom; y++) for (let x = R.x0; x < R.x1; x++) {
    let col;
    if (fl === 'planks') col = ((y - R.floor) % 6 === 5) ? '#8a6238' : ((x + ((y - R.floor) / 6 | 0) * 13) % 28 === 0 ? '#8a6238' : (hash(x >> 3, (y - R.floor) / 6 | 0, 4) < .5 ? '#c49558' : '#b8894e'));
    else if (fl === 'check') col = (((x - R.x0) >> 3) + ((y - R.floor) >> 3)) % 2 ? '#e8e0d2' : '#4a3a34';
    else if (fl === 'terracotta') col = ((x - R.x0) % 10 === 0 || (y - R.floor) % 10 === 0) ? '#b07050' : (hash(x / 10 | 0, y / 10 | 0, 6) < .5 ? '#d08a60' : '#c47e56');
    else col = hash(x >> 1, y >> 1, 7) < .5 ? '#7a8496' : '#727c8e';
    P(x, y, 1, 1, col);
  }
  P(R.x0 - 4, R.wall, 4, R.bottom - R.wall + 4, '#a39a88'); P(R.x1, R.wall, 4, R.bottom - R.wall + 4, '#8f8676');   // cut side walls
  P(R.x0 - 4, R.wall, 4, 2, '#f6efe0'); P(R.x1, R.wall, 4, 2, '#f6efe0');
  const gap0 = (W >> 1) - 11, gap1 = (W >> 1) + 11;   // the low front wall, a doorway in the middle
  P(R.x0 - 4, R.bottom, gap0 - R.x0 + 4, 5, '#a39a88'); P(gap1, R.bottom, R.x1 + 4 - gap1, 5, '#a39a88');
  P(R.x0 - 4, R.bottom, gap0 - R.x0 + 4, 1, '#f6efe0'); P(gap1, R.bottom, R.x1 + 4 - gap1, 1, '#f6efe0');
  P(gap0, R.bottom + 2, gap1 - gap0, 3, '#b9b09e');
  for (const [k, x, y] of spec.furniture || []) furniture(P, k, x, y, win);
  return outlined(c);
}
function furniture(P, k, x, y, win) {
  const wood = '#8a5c34', woodL = '#b07a48', woodD = '#5e3f24';
  if (k === 'bed') { P(x, y, 18, 4, woodD); P(x + 1, y + 1, 16, 2, woodL); P(x, y + 4, 18, 22, wood); P(x + 1, y + 4, 16, 21, '#f4efe6'); P(x + 3, y + 5, 12, 5, '#ffffff'); P(x + 1, y + 11, 16, 14, '#d8cdb8'); P(x + 1, y + 11, 16, 1, '#ece4d4'); P(x, y + 25, 18, 1, woodD); }
  if (k === 'table') { P(x, y, 40, 12, woodL); P(x, y, 40, 2, '#c8935a'); P(x, y + 12, 40, 2, woodD); P(x + 2, y + 14, 3, 6, woodD); P(x + 35, y + 14, 3, 6, woodD);
    P(x + 8, y + 3, 6, 4, '#f4efe6'); P(x + 26, y + 3, 6, 4, '#f4efe6'); P(x + 17, y + 2, 6, 6, '#c8553d'); P(x + 18, y + 3, 4, 2, '#e8a050'); }
  if (k === 'chair') { P(x, y, 8, 3, woodD); P(x, y + 3, 8, 6, wood); P(x + 1, y + 4, 6, 4, woodL); P(x, y + 9, 2, 3, woodD); P(x + 6, y + 9, 2, 3, woodD); }
  if (k === 'stool') { P(x, y, 8, 3, woodL); P(x + 1, y + 3, 2, 5, woodD); P(x + 5, y + 3, 2, 5, woodD); }
  if (k === 'rug') { P(x, y, 52, 28, '#a8463a'); P(x + 2, y + 2, 48, 24, '#c8604a'); P(x + 6, y + 6, 40, 16, '#e0b060'); P(x + 8, y + 8, 36, 12, '#c8604a'); }
  if (k === 'stove') { P(x, y, 16, 20, '#4a4a50'); P(x, y, 16, 3, '#6a6a72'); P(x + 2, y + 6, 12, 10, '#2a2a30'); P(x + 4, y + 8, 8, 6, '#e07a30'); P(x + 5, y + 10, 6, 3, '#ffd060'); P(x + 6, y - 6, 4, 6, '#3a3a40'); }
  if (k === 'shelf' || k === 'bookcase') { const h = k === 'bookcase' ? 26 : 22; P(x, y, k === 'bookcase' ? 18 : 14, h, woodD); for (let j = 3; j < h - 2; j += 6) { P(x + 1, y + j + 4, (k === 'bookcase' ? 16 : 12), 1, wood);
      for (let i = 0; i < (k === 'bookcase' ? 7 : 5); i++) P(x + 1 + i * 2, y + j, 2, 4, ['#c8553d', '#2f7d8a', '#e8c860', '#6e8fc4', '#5f8f52'][(i + j) % 5]); } }
  if (k === 'plant') { P(x + 2, y + 8, 6, 6, '#b8683e'); P(x + 1, y + 7, 8, 2, '#c87a4a'); P(x + 3, y + 1, 2, 7, '#4f8746'); P(x, y + 2, 3, 3, '#5f964f'); P(x + 5, y, 4, 4, '#4f8746'); P(x + 6, y + 4, 3, 3, '#67a057'); }
  if (k === 'clock') { P(x, y, 8, 8, woodD); P(x + 1, y + 1, 6, 6, '#f4efe6'); P(x + 4, y + 2, 1, 3, '#2a2024'); P(x + 4, y + 4, 2, 1, '#2a2024'); }
  if (k === 'frame') { P(x, y, 12, 10, '#c8a060'); P(x + 1, y + 1, 10, 8, '#7fa6b5'); P(x + 2, y + 5, 8, 3, '#5f8f52'); P(x + 6, y + 2, 2, 2, '#f2d45c'); }
  if (k === 'toolbox') { P(x, y + 4, 12, 8, '#c8402e'); P(x, y + 4, 12, 2, '#e05a46'); P(x + 4, y + 1, 4, 3, '#3a3a40'); }
  if (k === 'teaset') { P(x, y + 6, 12, 6, woodL); P(x + 2, y + 2, 5, 4, '#f4efe6'); P(x + 7, y + 3, 2, 2, '#f4efe6'); P(x + 1, y + 1, 1, 2, '#c8c0b4'); }
  if (k === 'easel') { P(x + 6, y, 2, 22, woodD); P(x + 1, y + 12, 2, 10, woodD); P(x + 11, y + 12, 2, 10, woodD); P(x, y + 2, 14, 11, '#f4efe6'); P(x + 2, y + 4, 10, 7, '#7fa6b5'); P(x + 4, y + 7, 6, 3, '#e8708a'); }
  if (k === 'lamp') { P(x + 2, y + 4, 2, 10, '#3a3a40'); P(x, y, 6, 5, '#f2d45c'); P(x + 1, y + 13, 4, 1, '#3a3a40'); }
  if (k === 'cupshelf') { P(x, y + 10, 40, 3, woodD); for (let i = 0; i < 8; i++) { P(x + 2 + i * 5, y + 5, 3, 5, ['#f4efe6', '#c8553d', '#2f7d8a'][i % 3]); P(x + 2 + i * 5, y + 5, 3, 1, '#ffffff'); } P(x, y + 2, 40, 1, woodD); }
  if (k === 'menu') { P(x, y, 26, 16, '#2f3a34'); P(x + 1, y + 1, 24, 14, '#3a4a40'); for (let j = 3; j < 14; j += 3) P(x + 3, y + j, 10 + (j % 2) * 6, 1, '#f4efe6'); }
  if (k === 'counter') { P(x, y, 76, 16, '#7a4a2e'); P(x, y, 76, 3, '#c8935a'); for (let i = 4; i < 76; i += 12) P(x + i, y + 5, 8, 9, '#6a3e24'); }
  if (k === 'espresso') { P(x, y, 12, 10, '#a8b0b8'); P(x + 1, y + 1, 10, 3, '#c8d0d8'); P(x + 3, y + 5, 2, 3, '#3a3a40'); P(x + 7, y + 5, 2, 3, '#3a3a40'); }
  if (k === 'register') { P(x, y + 2, 10, 6, '#3a3a40'); P(x + 1, y, 8, 3, '#6a6a72'); P(x + 2, y + 3, 6, 2, '#5fd0a0'); }
  if (k === 'display') { P(x, y, 54, 14, '#c8b898'); P(x + 1, y + 1, 52, 7, '#bfdce6'); for (let i = 0; i < 8; i++) P(x + 3 + i * 6, y + 3, 4, 4, ['#c8873c', '#e8708a', '#f4efe6', '#b5732e'][i % 4]); P(x, y + 12, 54, 2, '#8a7a5a'); }
  if (k === 'rtable') { P(x + 2, y, 12, 8, '#f4efe6'); P(x, y + 2, 16, 4, '#f4efe6'); P(x + 2, y + 6, 12, 2, '#d8cdb8'); P(x + 7, y + 8, 2, 4, '#3a3a40'); P(x + 5, y + 2, 3, 3, '#c8553d'); }
  if (k === 'oven') { P(x, y, 36, 34, '#a8462e'); for (let j = 0; j < 34; j += 5) for (let i = (j / 5 % 2) * 4; i < 36; i += 8) P(x + i, y + j, 7, 4, '#c8603e');
    P(x + 6, y + 14, 24, 16, '#2a1a14'); P(x + 8, y + 18, 20, 10, '#e07a30'); P(x + 10, y + 22, 16, 5, '#ffd060'); P(x + 12, y + 2, 12, 6, '#5a4a40'); }
  if (k === 'breadshelf') { P(x, y, 38, 30, woodD); for (let j = 4; j < 28; j += 8) { P(x + 1, y + j + 5, 36, 2, wood); for (let i = 0; i < 6; i++) { P(x + 2 + i * 6, y + j + 1, 5, 4, i % 2 ? '#c8873c' : '#e0a860'); P(x + 3 + i * 6, y + j + 1, 3, 1, '#f0c890'); } } }
  if (k === 'kneadtable') { P(x, y, 40, 14, woodL); P(x, y, 40, 2, '#d8a870'); P(x + 8, y + 3, 14, 7, '#f4ead4'); P(x + 26, y + 4, 8, 5, '#e8dcc0'); P(x + 2, y + 14, 3, 6, woodD); P(x + 35, y + 14, 3, 6, woodD); }
  if (k === 'flour') { P(x, y + 2, 14, 12, '#e8dcc0'); P(x + 2, y, 10, 4, '#d8c8a4'); P(x + 3, y + 6, 8, 2, '#c8a870'); }
  if (k === 'desk') { P(x, y, 28, 12, woodL); P(x, y, 28, 2, '#c8935a'); P(x, y + 12, 28, 2, woodD); P(x + 9, y - 10, 12, 9, '#3a3a40'); P(x + 10, y - 9, 10, 6, '#7fc8e0'); P(x + 14, y - 1, 2, 2, '#3a3a40'); P(x + 2, y + 3, 6, 4, '#f4efe6'); P(x + 22, y + 3, 4, 5, '#c8553d'); }
  if (k === 'cabinet') { P(x, y, 14, 24, '#8a929c'); for (let j = 2; j < 24; j += 7) { P(x + 1, y + j, 12, 6, '#a8b0b8'); P(x + 6, y + j + 2, 3, 1, '#3a3a40'); } }
  if (k === 'cooler') { P(x + 1, y + 10, 8, 12, '#d8dde2'); P(x + 2, y, 6, 10, '#9cc8e8'); P(x + 2, y, 6, 2, '#c8e0f0'); P(x + 3, y + 14, 2, 2, '#3a6aa8'); }
  if (k === 'printer') { P(x, y + 4, 16, 8, '#c8ccd2'); P(x + 2, y, 12, 5, '#f4efe6'); P(x + 2, y + 7, 12, 1, '#3a3a40'); }
  if (k === 'window' && win) win(x, y, 18, 12, '#6a4a34');
}

// Lying in bed: pillow, head, a blanket in the resident's own colour (18x26, = the bed).
export function sleeperCanvas(cloth, look) {
  const c = canvas(18, 26), x = c.getContext('2d'), P = (a, b, w, h, col) => { x.fillStyle = col; x.fillRect(a, b, w, h); };
  P(3, 5, 12, 5, '#ffffff'); P(5, 4, 8, 7, look.skin); P(5, 4, 8, 3, look.hair); P(4, 5, 1, 4, look.hair); P(13, 5, 1, 4, look.hair);
  P(7, 8, 2, 1, '#2a2024'); P(10, 8, 2, 1, '#2a2024');   // closed eyes
  P(1, 10, 16, 15, cloth); P(1, 10, 16, 2, shade(cloth, 0.25)); P(1, 17, 16, 1, shade(cloth, -0.2)); P(3, 13, 12, 1, shade(cloth, 0.12)); P(1, 24, 16, 1, shade(cloth, -0.35));
  return c;
}
// Sitting: the standing front frame with the legs folded out of sight.
export function sitterCanvas(cloth, look) {
  const src = charCanvas(cloth, look, 'down', 0), c = canvas(16, 24), x = c.getContext('2d');
  x.drawImage(src, 0, 0, 16, 18, 0, 3, 16, 18);
  x.fillStyle = look.pants; x.fillRect(4, 21, 8, 2); x.fillStyle = '#2a2024'; x.fillRect(4, 23, 8, 1);
  return c;
}

// The park's light installation: a lamp-grid sculpture on a stone plinth. lit = lamps on.
const INST_HUES = ['#ffd867', '#5fd0e0', '#ff8fc0', '#8fe0a0'];
export function installationCanvas(lit, done) {
  const c = canvas(44, 58), x = c.getContext('2d'), P = (a, b, w, h, col) => { x.fillStyle = col; x.fillRect(a, b, w, h); };
  P(2, 48, 40, 8, '#8f877a'); P(4, 46, 36, 3, '#b2a996'); P(2, 55, 40, 2, '#6f685c');   // plinth
  const cols = [7, 16, 25, 34], rows = [37, 27, 17, 7];
  cols.forEach(cx => P(cx + 1, 4, 2, 43, '#4a5160'));
  P(5, 3, 34, 2, '#3d4450');
  let i = 0;
  for (let r = 0; r < 4; r++) for (let k = 0; k < 4; k++) {
    const on = i < lit, col = on ? INST_HUES[(r + k) % 4] : '#2f3540';
    P(cols[k], rows[r], 4, 4, col); if (on) P(cols[k], rows[r], 2, 2, '#ffffff'); i++;
  }
  if (!done) { P(0, 20, 2, 28, '#9a7a52'); P(42, 20, 2, 28, '#9a7a52'); P(0, 30, 44, 1, '#9a7a52'); }   // scaffold while unfinished
  return outlined(c);
}

// Catmull-Rom through control points (tile coords) -> dense polyline [[x, y], ...].
export function splinePts(pts, perSeg = 10) {
  if (pts.length < 2) return pts.slice();
  const P = [pts[0], ...pts, pts[pts.length - 1]], out = [];
  for (let i = 1; i < P.length - 2; i++) {
    const [p0, p1, p2, p3] = [P[i - 1], P[i], P[i + 1], P[i + 2]];
    for (let k = 0; k < perSeg; k++) {
      const t = k / perSeg, t2 = t * t, t3 = t2 * t;
      out.push([0, 1].map(j => 0.5 * ((2 * p1[j]) + (-p0[j] + p2[j]) * t + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t2 + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t3)));
    }
  }
  out.push(pts[pts.length - 1].slice());
  return out;
}
// smooth value noise (0..1) on a grid of `cell` px
function vnoise(x, y, cell, seed) {
  const gx = Math.floor(x / cell), gy = Math.floor(y / cell), fx = x / cell - gx, fy = y / cell - gy;
  const s = t => t * t * (3 - 2 * t), a = hash(gx, gy, seed), b = hash(gx + 1, gy, seed), c = hash(gx, gy + 1, seed), d = hash(gx + 1, gy + 1, seed);
  return a + (b - a) * s(fx) + (c - a) * s(fy) + (a - b - c + d) * s(fx) * s(fy);
}

/* ------------------------------------------------------------------ ground */
// The whole static ground baked once: grass with large-scale variation and patches
// (dark / light grass, flowers, gravel, fallen leaves), curved dirt paths of varying
// width, the round stone plaza, stone aprons, the river bank and the pond.
export function groundCanvas(M) {
  const T = M.tile, W = M.cols * T, H = M.rows * T, c = canvas(W, H), ctx = c.getContext('2d');
  const img = ctx.createImageData(W, H), d = img.data;
  const set = (x, y, col) => { if (x < 0 || y < 0 || x >= W || y >= H) return; const i = (y * W + x) * 4; d[i] = col[0]; d[i + 1] = col[1]; d[i + 2] = col[2]; d[i + 3] = 255; };
  const C = s => hexRgb(s), mix = (a, b, k) => [0, 1, 2].map(i => a[i] + (b[i] - a[i]) * k);
  const DARK = C('#6a9042'), MID = C('#8cb054'), LIGHT = C('#adcc6a'), TUFT = C('#4f7432');
  const patches = (M.patches || []).map(p => ({...p, x: p.x * T, y: p.y * T, r: p.r * T}));
  const near = (x, y, kind) => { let w = 0; for (const p of patches) if (p.kind === kind) { const dd = Math.hypot(x - p.x, y - p.y) / p.r; if (dd < 1) w = Math.max(w, 1 - dd * dd); } return w; };
  // grass: big soft variation + patches + per-pixel speckle
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    let t = 0.25 + 0.5 * vnoise(x, y, 72, 3) + 0.2 * (vnoise(x, y, 23, 4) - 0.5);
    const dk = near(x, y, 'dark'), lt = near(x, y, 'light');
    t = t * (1 - dk * 0.8) + (1 - dk) * 0 + lt * 0.35; t = Math.max(0, Math.min(1, t));
    let col = t < 0.5 ? mix(DARK, MID, t * 2) : mix(MID, LIGHT, (t - 0.5) * 2);
    const h = hash(x, y, 1);
    if (h < 0.06) col = mix(col, DARK, 0.5); else if (h > 0.95) col = mix(col, LIGHT, 0.6);
    const gv = near(x, y, 'gravel');
    if (gv > 0 && hash(x >> 1, y >> 1, 2) < gv * 0.7) col = [C('#a8a08e'), C('#bdb5a2'), C('#8f877a')][hash(x, y, 3) * 3 | 0];
    set(x, y, col);
  }
  // tufts, wild flowers (denser in flower patches), fallen leaves (in leaf patches)
  const FL = [C('#f4efe6'), C('#f2d45c'), C('#e89ab0'), C('#b9a4e0')], LEAF = [C('#c9843a'), C('#a8642e'), C('#d9a548'), C('#8a6a3a')];
  for (let ty = 0; ty < M.rows; ty++) for (let tx = 0; tx < M.cols; tx++) {
    const cx = tx * T + 8, cy = ty * T + 8;
    for (let k = 0; k < 2; k++) if (hash(tx, ty, 5 + k * 50) < .45) {   // grass tufts, two sizes
      const px = tx * T + 2 + (hash(tx, ty, 6 + k * 50) * 11 | 0), py = ty * T + 4 + (hash(tx, ty, 7 + k * 50) * 9 | 0), big = hash(tx, ty, 9 + k * 50) < .4;
      set(px, py, TUFT); set(px + 1, py - 1, TUFT); set(px + 2, py, TUFT); set(px + 1, py, TUFT);
      if (big) { set(px - 1, py - 1, TUFT); set(px + 3, py - 1, TUFT); set(px + 1, py - 2, C('#86a862')); set(px, py + 1, DARK); set(px + 2, py + 1, DARK); }
    }
    if (hash(tx, ty, 60) < .14) {   // a pebble with a lit top and a shadow
      const px = tx * T + 2 + (hash(tx, ty, 61) * 12 | 0), py = ty * T + 2 + (hash(tx, ty, 62) * 12 | 0), w = 2 + (hash(tx, ty, 63) * 2 | 0);
      for (let i = 0; i < w; i++) { set(px + i, py, C('#c9c2b2')); set(px + i, py + 1, C('#9d9686')); set(px + i + 1, py + 2, DARK); }
    }
    if (hash(tx, ty, 64) < .1) { const px = tx * T + 3 + (hash(tx, ty, 65) * 10 | 0), py = ty * T + 3 + (hash(tx, ty, 66) * 10 | 0);   // clover
      set(px, py, C('#6f9a4e')); set(px + 1, py + 1, C('#6f9a4e')); set(px - 1, py + 1, C('#6f9a4e')); set(px, py + 1, C('#8fbe62')); }
    if (hash(tx, ty, 67) < .015) { const px = tx * T + 6, py = ty * T + 8;   // a mushroom
      set(px, py, C('#c8402e')); set(px + 1, py, C('#c8402e')); set(px - 1, py + 1, C('#c8402e')); set(px + 2, py + 1, C('#c8402e')); set(px, py + 1, C('#f4efe6')); set(px, py + 2, C('#efe4d0')); set(px + 1, py + 2, C('#efe4d0')); }
    const fr = 0.04 + near(cx, cy, 'flowers') * 0.6;
    for (let k = 0; k < 3; k++) if (hash(tx, ty, 8 + k) < fr) {
      const px = tx * T + 2 + (hash(tx, ty, 11 + k) * 12 | 0), py = ty * T + 2 + (hash(tx, ty, 14 + k) * 12 | 0), f = FL[hash(tx, ty, 17 + k) * 4 | 0];
      set(px, py, f); set(px + 1, py, f); set(px, py + 1, f); set(px + 1, py + 1, C('#f8e070')); set(px, py + 2, TUFT);
    }
    const lv = near(cx, cy, 'leaves');
    for (let k = 0; k < 6; k++) if (hash(tx, ty, 20 + k) < lv * 0.8) {
      const px = tx * T + (hash(tx, ty, 26 + k) * 15 | 0), py = ty * T + (hash(tx, ty, 32 + k) * 15 | 0), f = LEAF[hash(tx, ty, 38 + k) * 4 | 0];
      set(px, py, f); if (hash(tx, ty, 44 + k) < .5) set(px + 1, py, f);
    }
  }
  // curved dirt paths: stamp discs along each spline; width breathes a little along the way
  const mask = new Uint8Array(W * H);
  const disc = (cx, cy, r) => { for (let y = Math.floor(cy - r); y <= cy + r; y++) for (let x = Math.floor(cx - r); x <= cx + r; x++)
    if (x >= 0 && y >= 0 && x < W && y < H && (x - cx) ** 2 + (y - cy) ** 2 <= r * r) mask[y * W + x] = 1; };
  for (const p of [...M.paths, ...(M.trails || [])]) {
    const pts = splinePts(p.pts, 16).map(([x, y]) => [x * T + T / 2, y * T + T / 2]);
    let along = 0;
    for (let i = 0; i < pts.length - 1; i++) {
      const [ax, ay] = pts[i], [bx, by] = pts[i + 1], len = Math.hypot(bx - ax, by - ay), n = Math.max(1, Math.ceil(len / 2));
      for (let k = 0; k <= n; k++) { const x = ax + (bx - ax) * k / n, y = ay + (by - ay) * k / n; along += len / n;
        disc(x, y, p.w / 2 + 2.2 * (vnoise(along, 0, 40, 9) - 0.5)); }
    }
  }
  const DIRT = [C('#cdb07c'), C('#c8a874'), C('#d3b886')], DD = C('#b4925e'), EDGE = C('#a8885a');
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) if (mask[y * W + x]) {
    const nb = (dx, dy) => mask[(y + dy) * W + x + dx];
    const edge = !nb(-1, 0) || !nb(1, 0) || !nb(0, -1) || !nb(0, 1);
    if (edge && hash(x >> 1, y >> 1, 9) < .45) continue;
    set(x, y, edge ? EDGE : (hash(x, y, 11) < .08 ? DD : DIRT[hash(x >> 2, y >> 2, 12) * 3 | 0]));
  }
  // the round plaza: stones in concentric rings, a darker kerb
  const ST = [C('#cfc6b4'), C('#c6bda9'), C('#d8d0bf'), C('#c0b6a2')], MOR = C('#a99f8c');
  if (M.plaza) {
    const pcx = M.plaza.c[0] * T + T / 2, pcy = M.plaza.c[1] * T, R = M.plaza.r * T;
    for (let y = Math.floor(pcy - R - 3); y <= pcy + R + 3; y++) for (let x = Math.floor(pcx - R - 3); x <= pcx + R + 3; x++) {
      const dist = Math.hypot(x - pcx, y - pcy); if (dist > R + 2) continue;
      if (dist > R) { set(x, y, C('#8f8676')); continue; }
      if (dist > R - 2) { set(x, y, C('#e4dccb')); continue; }   // lit rim
      const ring = Math.floor(dist / 8), ang = Math.atan2(y - pcy, x - pcx) + ring * 0.37, circ = Math.max(1, Math.round(2 * Math.PI * (ring * 8 + 4) / 9));
      const seg = Math.floor((ang + Math.PI) / (2 * Math.PI) * circ), fr = ((ang + Math.PI) / (2 * Math.PI) * circ) % 1;
      const mortar = dist % 8 < 1 || fr < 0.09;
      set(x, y, mortar ? MOR : ST[hash(ring, seg, 21) * 4 | 0]);
    }
  }
  if (M.plaza) {   // the platform's face (visible on the south half), steps at the paths
    const pcx = M.plaza.c[0] * T + T / 2, pcy = M.plaza.c[1] * T, R = M.plaza.r * T + 2, FH = 7;
    for (let x = Math.floor(pcx - R); x <= pcx + R; x++) {
      const rimY = Math.round(pcy + Math.sqrt(Math.max(0, R * R - (x - pcx) ** 2)));
      const stairs = mask[(rimY + 4) * W + x] || mask[(rimY + 1) * W + x];
      for (let k = 0; k < FH; k++) {
        const y = rimY + k;
        if (stairs) set(x, y, k % 3 === 0 ? C('#e0d8c6') : k % 3 === 1 ? C('#bdb4a1') : C('#9e9584'));
        else set(x, y, k === 0 ? C('#8f8676') : ((x + (k > 3 ? 4 : 0)) % 8 === 0 || k === 3) ? C('#7d7466') : C('#a39a88'));
      }
      for (let k = 0; k < 3; k++) { const y = rimY + FH + k; if (!mask[y * W + x]) { const i = (y * W + x) * 4; d[i] *= 0.82 + k * 0.05; d[i + 1] *= 0.82 + k * 0.05; d[i + 2] *= 0.85 + k * 0.05; } }
    }
  }
  // stone aprons in front of doors
  for (const [ax, ay, aw, ah] of M.aprons || []) {
    const X0 = Math.round(ax * T), Y0 = Math.round(ay * T), X1 = Math.round((ax + aw) * T), Y1 = Math.round((ay + ah) * T);
    for (let y = Y0; y < Y1; y++) for (let x = X0; x < X1; x++) {
      const row = ((y - Y0) / 8) | 0, off = row % 2 ? 4 : 0, bx = ((x - X0 + off) / 8) | 0;
      set(x, y, (y - Y0) % 8 === 0 || (x - X0 + off) % 8 === 0 ? MOR : ST[hash(bx, row, 13 + X0) * 3 | 0]);
    }
    for (let x = X0; x < X1; x++) { set(x, Y1, C('#e0d8c6')); set(x, Y1 + 1, C('#a39a88')); set(x, Y1 + 2, C('#d4ccba')); set(x, Y1 + 3, C('#8f8676')); set(x, Y1 + 4, DARK); }
  }
  // retaining ledges: grass above is higher -- lit lip, a stone face, steps where a path crosses
  for (const [lx0, ly, lx1] of M.ledges || []) {
    const X0 = Math.round(lx0 * T), X1 = Math.round(lx1 * T), Y = Math.round(ly * T);
    for (let x = X0; x < X1; x++) {
      const wob = Math.round(1.5 * Math.sin(x / 13)), y0 = Y + wob, onPath = mask[(y0 + 3) * W + x];
      if (onPath) { for (let k = 0; k < 7; k++) set(x, y0 + k, k % 3 === 0 ? C('#d9c9a0') : k % 3 === 1 ? C('#bba57a') : C('#9d8a62')); continue; }
      set(x, y0 - 1, C('#b9d08a'));
      for (let k = 0; k < 7; k++) set(x, y0 + k, k === 0 ? C('#c9c2b2') : ((x + (k > 3 ? 5 : 0)) % 10 === 0 || k === 3) ? C('#6f6a5e') : (k < 3 ? C('#a39c8c') : C('#8d8677')));
      for (let k = 0; k < 3; k++) { const y = y0 + 7 + k, i = (y * W + x) * 4; if (y < H) { d[i] *= 0.8 + k * 0.06; d[i + 1] *= 0.8 + k * 0.06; d[i + 2] *= 0.84 + k * 0.05; } }
    }
  }
  // river along the west edge: wavy bank, sand, foam, deep channel
  const WA = C('#5a8fb4'), WD = C('#477aa1'), WL = C('#78a8c8'), SAND = C('#d9c79a'), SAND2 = C('#c9b585'), FOAM = C('#e2eef2');
  for (let y = 0; y < H; y++) {
    const bank = Math.round(M.river.x + 5 * Math.sin(y / 37) + 3 * Math.sin(y / 11 + 1));
    for (let x = 0; x <= bank + 5; x++) {
      if (x < bank - 1) set(x, y, x < bank * .45 ? WD : (hash(x >> 3, y >> 1, 14) < .06 ? WL : WA));
      else if (x < bank) set(x, y, FOAM);
      else set(x, y, hash(x, y, 15) < .25 ? SAND2 : SAND);
    }
    for (let k = 0; k < 5; k++) set(bank + 6 + k, y, k === 4 ? C('#b9d08a') : (hash(bank + k, y >> 1, 16) < .3 ? C('#8a6a44') : C('#a07c50')));
  }
  if (M.pond) {
    const [cx, cy, rx, ry] = M.pond.map(v => v * T);
    for (let y = Math.floor(cy - ry - 3); y <= cy + ry + 3; y++) for (let x = Math.floor(cx - rx - 3); x <= cx + rx + 3; x++) {
      const e = ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2, e2 = ((x - cx) / (rx + 3)) ** 2 + ((y - cy) / (ry + 3)) ** 2;
      if (e < .78) set(x, y, e < .35 ? WD : WA); else if (e < 1) set(x, y, FOAM); else if (e2 < 1) set(x, y, SAND);
    }
  }
  ctx.putImageData(img, 0, 0);
  return c;
}

// Small living things: a bird (2 wing frames), a butterfly (2 frames), a smoke puff, a petal.
export function critterCanvas(kind, f) {
  const c = canvas(8, 6), x = c.getContext('2d'), P = (a, b, w, h, col) => { x.fillStyle = col; x.fillRect(a, b, w, h); };
  if (kind === 'bird') { if (f) { P(0, 1, 2, 1, '#3a3036'); P(2, 2, 1, 1, '#3a3036'); P(3, 3, 2, 1, '#3a3036'); P(5, 2, 1, 1, '#3a3036'); P(6, 1, 2, 1, '#3a3036'); }
                         else { P(0, 3, 2, 1, '#3a3036'); P(2, 2, 1, 1, '#3a3036'); P(3, 3, 2, 1, '#3a3036'); P(5, 2, 1, 1, '#3a3036'); P(6, 3, 2, 1, '#3a3036'); } }
  if (kind === 'butterfly') { const col = f ? '#f2d45c' : '#e8708a'; if (f) { P(1, 1, 2, 3, col); P(5, 1, 2, 3, col); } else { P(2, 1, 1, 3, col); P(5, 1, 1, 3, col); } P(3, 1, 2, 4, '#3a3036'); }
  if (kind === 'puff') { P(2, 1, 4, 4, '#e8e4dc'); P(1, 2, 6, 2, '#e8e4dc'); P(3, 1, 2, 1, '#f6f4ee'); }
  if (kind === 'petal') { P(0, 0, 2, 1, '#f4b8cc'); P(1, 1, 1, 1, '#e89ab0'); }
  return c;
}

// Soft round light (for halos), drawn once and reused.
export function glowCanvas(r, rgb) {
  const c = canvas(r * 2, r * 2), x = c.getContext('2d'), g = x.createRadialGradient(r, r, 0, r, r, r);
  g.addColorStop(0, `rgba(${rgb},0.85)`); g.addColorStop(.35, `rgba(${rgb},0.35)`); g.addColorStop(1, `rgba(${rgb},0)`);
  x.fillStyle = g; x.fillRect(0, 0, r * 2, r * 2); return c;
}
