// The village map on PixiJS v8 (WebGL), drawn from frontend/map/town.json and the
// programmatic pixel art in pixelart.js. Same public API as the old SVG map, so the
// live village and the day replay drive it identically:
//   buildMap(locations) · updateAgents([{id, location, action}]) · updateLandmarks(list)
//   applyDaylight(minute) · renderEffects(effects) · bubble(actor, loc, text, target)
//   setFocus(ids) · setShopRev(econ) · setBroken(locId, bool) · clearBubbles()
//   setActive(bool) · destroy()
// Camera: drag to pan, wheel / pinch to zoom; clicking a resident calls onAgentClick.
import {Application, Container, Sprite, Texture, Graphics, Text, TextureSource} from 'pixi.js';
import {agentName, locName, KINDS, phaseOf} from './names.js';
import {charCanvas, lookOf, buildingCanvas, decorCanvas, installationCanvas, groundCanvas, glowCanvas, iconCanvas, critterCanvas, splinePts, hash} from './pixelart.js';

TextureSource.defaultOptions.scaleMode = 'nearest';   // crisp pixel art
const tok = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const hexNum = h => parseInt(h.replace('#', ''), 16);
const mixHex = (a, b, k) => { const A = [16, 8, 0].map(s => (a >> s) & 255), B = [16, 8, 0].map(s => (b >> s) & 255);
  return A.reduce((acc, v, i) => (acc << 8) | Math.round(v + (B[i] - v) * k), 0); };
let MAP = null;
const loadMap = () => MAP || (MAP = fetch('/static/map/town.json').then(r => r.json()));
const FONT = "'Huninn','Noto Sans TC',sans-serif";
const DIRS = ['down', 'up', 'left', 'right'];
const CAST_K = {tree: 1, pine: 1, blossom: 1, bush: 0.7, shrub: 0.6, lamp: 0.55, washline: 0.6, well: 0.8, terrace: 0.8, board: 0.7, stall: 0.8,
  fountain: 0.5, bench: 0.4, crate: 0.5, barrel: 0.5, sacks: 0.5, postbox: 0.6, mailbox: 0.6, bike: 0.5, sandwich: 0.5, woodpile: 0.4,
  stump: 0.4, log: 0.3, rock: 0.3, grass: 0, bloom: 0, flowers: 0.2, pot: 0.4, basket: 0.3};

export class VillageMap {
  constructor(el, {onAgentClick, showRevenue = true} = {}) {
    this.el = el; this.onAgentClick = onAgentClick; this.showRevenue = showRevenue;
    this.locs = {}; this.locList = []; this.res = {}; this.bubbles = []; this.talk = {}; this.focus = null;
    this.state = {agents: null, landmarks: null, effects: null, minute: null};
    this.lowFx = false; this.slow = 0; this.alive = true;
    this.reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;   // walking stays; decoration stops
    this.ready = this.init().catch(e => console.error('map init failed', e));
  }

  /* ---------------------------------------------------------------- setup */
  async init() {
    const [M] = await Promise.all([loadMap(), Promise.race([document.fonts.ready, new Promise(r => setTimeout(r, 2500))])]);
    if (!this.alive) return;
    this.M = M; this.T = M.tile; this.W = M.cols * M.tile; this.H = M.rows * M.tile;
    const app = this.app = new Application();
    await app.init({resizeTo: this.el, backgroundAlpha: 0, antialias: false, roundPixels: true,
      resolution: Math.min(2, window.devicePixelRatio || 1), autoDensity: true, preference: 'webgl'});
    if (!this.alive) { app.destroy(true); return; }
    app.canvas.classList.add('pixmap'); app.canvas.dataset.quality = 'full';
    this.el.appendChild(app.canvas);
    const L = n => { const c = new Container(); c.label = n; return c; };
    this.world = L('world'); app.stage.addChild(this.world);
    this.ground = L('ground'); this.shade = L('shadows'); this.objects = L('objects'); this.objects.sortableChildren = true;
    this.fx = L('fx'); this.lights = L('lights'); this.labels = L('labels'); this.overlay = L('overlay');
    this.night = new Graphics().rect(0, 0, this.W, this.H).fill(0xffffff); this.night.blendMode = 'multiply';
    this.world.addChild(this.ground, this.shade, this.objects, this.fx, this.night, this.lights, this.labels, this.overlay);
    this.casters = [];

    this.ground.addChild(new Sprite(Texture.from(groundCanvas(M))));
    this.tex = {
      glowWarm: Texture.from(glowCanvas(32, '255,196,110')), glowCool: Texture.from(glowCanvas(32, '150,220,255')),
      glowPink: Texture.from(glowCanvas(32, '255,150,200')),
      eat: Texture.from(iconCanvas('eat')), work: Texture.from(iconCanvas('work')), zzz: Texture.from(iconCanvas('zzz')), wrench: Texture.from(iconCanvas('wrench')),
    };
    this.tex.ao = app.renderer.generateTexture(new Graphics().ellipse(0, 0, 16, 5).fill(0x1a1420));
    this.tex.bird = [0, 1].map(f => Texture.from(critterCanvas('bird', f)));
    this.tex.fly = [0, 1].map(f => Texture.from(critterCanvas('butterfly', f)));
    this.tex.puff = Texture.from(critterCanvas('puff')); this.tex.petal = Texture.from(critterCanvas('petal'));
    this.walk = {};   // path id -> world-px polyline from the door front to the plaza edge
    for (const p of M.paths) this.walk[p.id] = splinePts(p.pts, 6).map(([x, y]) => ({x: x * this.T + this.T / 2, y: y * this.T + this.T / 2}));
    this.plazaC = {x: M.plaza.c[0] * this.T + this.T / 2, y: M.plaza.c[1] * this.T};
    this.puffs = []; this.petals = []; this.critters = []; this.sway = []; this.nextBird = performance.now() + 8000; this.nextFly = performance.now() + 5000;
    this.buildDecor(); this.buildWater();
    this.setupCamera();
    app.ticker.add(t => this.tick(t.deltaMS));
    this.fpsTimer = setInterval(() => this.watchFps(), 1000);
    // replay whatever arrived before we were ready
    if (this.pendingLocations) this.buildMap(this.pendingLocations);
    const s = this.state;
    if (s.landmarks) this.updateLandmarks(s.landmarks);
    if (s.agents) this.updateAgents(s.agents);
    if (s.effects) this.renderEffects(s.effects);
    if (s.minute != null) this.applyDaylight(s.minute);
    if (this.pendingRev) this.setShopRev(this.pendingRev);
  }

  px(t) { return t * this.T + this.T / 2; }           // tile centre -> world px
  doorPx(id) {
    const d = this.M.locations[id];
    if (d) return {x: d.door[0] * this.T + this.T / 2, y: d.door[1] * this.T};
    const l = this.locs[id]; return {x: (l ? l.x : 400) * 1.2, y: (l ? l.y : 260) * 1.2};   // a place the map file doesn't know yet
  }

  buildDecor() {
    const T = this.T, cache = {};
    for (const [type, tx, ty] of this.M.decor) {
      const tex = cache[type] || (cache[type] = Texture.from(decorCanvas(type)));
      const s = new Sprite(tex); s.anchor.set(0.5, 1); s.x = Math.round(tx * T + T / 2); s.y = Math.round(ty * T + T); s.zIndex = s.y;
      this.objects.addChild(s);
      this.cast(s, CAST_K[type] ?? 0.6);
      if (type === 'tree' || type === 'pine' || type === 'blossom') (this.trees = this.trees || []).push(s);
      if (type === 'blossom') (this.blossoms = this.blossoms || []).push(s);
      if (type === 'flowers') (this.flowerBeds = this.flowerBeds || []).push(s);
    }
    const lampTex = cache.lamp || (cache.lamp = Texture.from(decorCanvas('lamp')));
    this.lampGlows = [];
    for (const [tx, ty] of this.M.lamps) {
      const x = Math.round(tx * T + T / 2), y = Math.round(ty * T + T);
      const s = new Sprite(lampTex); s.anchor.set(0.5, 1); s.x = x; s.y = y; s.zIndex = y; this.objects.addChild(s); this.cast(s, 0.5);
      const g = new Sprite(this.tex.glowWarm); g.anchor.set(0.5); g.x = x; g.y = y - 27; g.scale.set(1.5); g.blendMode = 'add'; g.alpha = 0;
      this.lights.addChild(g); this.lampGlows.push(g);
    }
  }

  buildWater() {   // drifting glints on the river (the cheap animated part of the ground)
    this.glints = [];
    const g = new Graphics().rect(0, 0, 3, 1).fill(0xe2eef2), tex = this.app.renderer.generateTexture(g);
    for (let i = 0; i < 26; i++) {
      const s = new Sprite(tex); s.x = 6 + Math.random() * (this.M.river.x - 18); s.y = Math.random() * this.H; s.alpha = Math.random();
      s.v = 4 + Math.random() * 6; this.fx.addChild(s); this.glints.push(s);
    }
  }

  /* ---------------------------------------------------------------- places */
  buildMap(locations) {
    if (!this.app) { this.pendingLocations = locations; return; }
    if (this.builtFor === locations) return; this.builtFor = locations;
    this.locs = {}; this.locList = locations;
    for (const b of this.buildings || []) { b.spr.destroy(); b.label.destroy(); b.windows.forEach(w => w.destroy()); b.sign.destroy(); b.rev && b.rev.destroy(); b.zzz.destroy(); b.broken.destroy(); }
    this.buildings = [];
    for (const l of locations) {
      this.locs[l.id] = l;
      const d = this.doorPx(l.id), meta = this.M.locations[l.id] || {};
      const b = {id: l.id, l, d, windows: [], spr: null};
      if (meta.landmark) { b.spr = new Sprite(Texture.EMPTY); }   // the park: its landmark is drawn by updateLandmarks
      else {
        const art = buildingCanvas(l); b.spr = new Sprite(Texture.from(art.c)); b.spr.anchor.set(0.5, 1);
        for (const w of art.windows) {   // night: warm window glow + a soft halo
          const g = new Graphics().rect(d.x - art.w / 2 + w.x, d.y - art.h + w.y, w.w, w.h).fill(0xffcf6e);
          const h = new Sprite(this.tex.glowWarm); h.anchor.set(0.5); h.x = d.x - art.w / 2 + w.x + w.w / 2; h.y = d.y - art.h + w.y + w.h / 2;
          h.scale.set(Math.max(w.w, w.h) / 22); h.blendMode = 'add';
          const c = new Container(); c.addChild(g, h); c.alpha = 0; this.lights.addChild(c); b.windows.push(c);
        }
        b.top = d.y - art.h;
        if (art.chimney) b.chimney = {x: d.x - art.w / 2 + art.chimney.x, y: d.y - art.h + art.chimney.y};
      }
      b.spr.x = d.x; b.spr.y = d.y; b.spr.zIndex = d.y - 1; this.objects.addChild(b.spr);
      if (!meta.landmark) { this.cast(b.spr, 1, b.spr.width * 0.95); b.w = b.spr.width; }
      b.label = new Text({text: locName(l.id) + '\n' + (KINDS[l.kind] || l.kind), resolution: 3,
        style: {fontFamily: FONT, fontSize: 11, fill: 0x2b2520, align: 'center', lineHeight: 12, stroke: {color: 0xfbf7ee, width: 3}}});
      b.label.anchor.set(0.5, 0); b.label.x = d.x; b.label.y = d.y + (meta.landmark ? 6 : 36); b.label.scale.set(1);
      this.labels.addChild(b.label);
      b.sign = new Container();   // closed-day sign by the door
      const sg = new Graphics().roundRect(-8, -8, 16, 14, 3).fill(0xa3402f).stroke({color: 0x6e2a22, width: 1});
      const st = new Text({text: '休', resolution: 3, style: {fontFamily: FONT, fontSize: 10, fill: 0xffffff}}); st.anchor.set(0.5); st.y = -1;
      b.sign.addChild(sg, st); b.sign.x = d.x + 18; b.sign.y = d.y - 16; b.sign.visible = false; this.labels.addChild(b.sign);
      b.zzz = new Container(); b.zzz.x = d.x - 22; b.zzz.y = (b.top || d.y - 40) + 18; b.zzz.visible = false; this.labels.addChild(b.zzz);
      b.zzzIcon = new Sprite(this.tex.zzz); b.zzzIcon.scale.set(1.5);
      b.zzzText = new Text({text: '', resolution: 3, style: {fontFamily: "'JetBrains Mono',monospace", fontSize: 8, fill: 0xe8f0ff, stroke: {color: 0x2b2520, width: 2}}});
      b.zzzText.x = 13; b.zzzText.y = 1; b.zzz.addChild(b.zzzIcon, b.zzzText);
      b.broken = new Sprite(this.tex.wrench); b.broken.anchor.set(0.5); b.broken.scale.set(1.6); b.broken.x = d.x + 26; b.broken.y = (b.top || d.y - 40) + 6; b.broken.visible = !!l.broken;
      this.labels.addChild(b.broken);
      if (l.owner && this.showRevenue) {
        b.rev = new Text({text: '', resolution: 3, style: {fontFamily: "'JetBrains Mono',monospace", fontSize: 9, fill: 0x9a6b12, stroke: {color: 0xfbf7ee, width: 3}}});
        b.rev.anchor.set(0.5, 1); b.rev.x = d.x; b.rev.y = (b.top || d.y - 60) - 2; this.labels.addChild(b.rev);
      }
      this.buildings.push(b);
    }
    if (this.state.landmarks) this.updateLandmarks(this.state.landmarks);
    if (!this.populated) { this.populated = true; this.populate(); }
    if (this.phase) this.updateShadows();
    if (!this.camSet) this.fit();
  }

  /* ---------------------------------------------------------------- shadows */
  // Every building, tree, prop and resident: a soft contact shadow at its feet, plus a
  // cast silhouette whose length and direction follow the sun (short at noon, long and
  // to the east late in the day; gone at night).
  cast(src, k, w) {
    const ao = new Sprite(this.tex.ao); ao.anchor.set(0.5); ao.x = src.x; ao.y = src.y - 1;
    ao.scale.set((w || src.width) / 32 * 1.1, Math.max(0.6, (w || src.width) / 60)); ao.alpha = 0.22; this.shade.addChild(ao);
    let sh = null;
    if (k > 0) { sh = new Sprite(src.texture); sh.anchor.copyFrom(src.anchor); sh.x = src.x; sh.y = src.y; sh.tint = 0x1a1420; sh.alpha = 0; this.shade.addChild(sh); }
    const c = {src, ao, sh, k, sx: src.scale.x, sy: src.scale.y}; this.casters.push(c); return c;
  }
  updateShadows() {
    const {n, h} = this.phase, day = 1 - n;
    const t = Math.max(0, Math.min(1, (h - 6) / 12.5)), dir = (t - 0.5) * 2, L = 0.25 + 0.85 * Math.abs(dir) ** 1.3;
    const alpha = h < 5.5 || h > 19.5 ? 0 : 0.3 * day;
    this.sun = {dir, L, alpha};
    for (const c of this.casters) this.shapeShadow(c);
  }
  shapeShadow(c) {
    const {dir, L, alpha} = this.sun || {dir: 0, L: 0.3, alpha: 0.2};
    c.ao.alpha = 0.16 + 0.14 * (1 - (this.phase ? this.phase.n : 0));
    if (!c.sh) return;
    c.sh.alpha = alpha; c.sh.visible = alpha > 0.01;
    c.sh.scale.set(c.src.scale.x, c.src.scale.y * (0.35 + 0.6 * L) * c.k);
    c.sh.skew.x = -dir * (0.25 + 0.95 * L);
    c.sh.x = c.src.x + dir * 2; c.sh.y = c.src.y;
  }

  /* ---------------------------------------------------------------- ground cover + woodland */
  // Fill the open grass so the town never reads as empty: bushes, tall grass, flower
  // clumps, rocks, stumps, logs -- and frame the town with woodland at the map edges.
  // Everything is placed deterministically (same town every load) and keeps clear of
  // buildings, paths, plazas, props and the space where residents stand.
  populate() {
    const M = this.M, T = this.T, C = 8, gw = Math.ceil(this.W / C), gh = Math.ceil(this.H / C), occ = new Uint8Array(gw * gh);
    const mark = (x, y, r) => { for (let gy = Math.floor((y - r) / C); gy <= (y + r) / C; gy++) for (let gx = Math.floor((x - r) / C); gx <= (x + r) / C; gx++)
      if (gx >= 0 && gy >= 0 && gx < gw && gy < gh) occ[gy * gw + gx] = 1; };
    const rect = (x0, y0, x1, y1) => { for (let y = y0; y <= y1; y += C / 2) for (let x = x0; x <= x1; x += C / 2) mark(x, y, 1); };
    const free = (x, y, r) => { for (let gy = Math.floor((y - r) / C); gy <= (y + r) / C; gy++) for (let gx = Math.floor((x - r) / C); gx <= (x + r) / C; gx++)
      if (gx < 0 || gy < 0 || gx >= gw || gy >= gh || occ[gy * gw + gx]) return false; return true; };
    for (const b of this.buildings) {
      const d = b.d;
      if (b.w) rect(d.x - b.w / 2 - 8, b.top - 6, d.x + b.w / 2 + 8, d.y + 8); else rect(d.x - 40, d.y - 90, d.x + 40, d.y + 8);
      rect(d.x - 66, d.y, d.x + 66, d.y + 72);   // where residents stand
    }
    for (const [ax, ay, aw, ah] of M.aprons) rect(ax * T - 6, ay * T - 6, (ax + aw) * T + 6, (ay + ah) * T + 10);
    const pc = this.plazaC; mark(pc.x, pc.y, M.plaza.r * T + 26);
    for (const p of [...M.paths, ...(M.trails || [])]) for (const [x, y] of splinePts(p.pts, 12)) mark(x * T + T / 2, y * T + T / 2, p.w / 2 + (M.trails.includes(p) ? 4 : 9));
    for (const [, tx, ty] of M.decor) mark(tx * T + T / 2, ty * T + T - 6, 14);
    for (const [tx, ty] of M.lamps) mark(tx * T + T / 2, ty * T + T - 4, 10);
    for (const [lx0, ly, lx1] of M.ledges || []) rect(lx0 * T, ly * T - 6, lx1 * T, ly * T + 14);
    rect(0, 0, M.river.x + 22, this.H);
    if (M.pond) { const [cx, cy, rx, ry] = M.pond.map(v => v * T); rect(cx - rx - 10, cy - ry - 10, cx + rx + 10, cy + ry + 10); }
    const tex = {}, place = (type, x, y, opts = {}) => {
      const t = tex[type] || (tex[type] = Texture.from(decorCanvas(type)));
      const s = new Sprite(t); s.anchor.set(0.5, 1); s.x = Math.round(x); s.y = Math.round(y); s.zIndex = s.y;
      if (opts.scale) s.scale.set(opts.scale); if (opts.tint) s.tint = opts.tint;
      this.objects.addChild(s); this.cast(s, CAST_K[type] ?? 0.4);
      if (type === 'tree' || type === 'pine') this.trees.push(s);
      return s;
    };
    // woodland along the chosen edges, two to three trees deep, varied in size and shade
    const F = M.forest || {edges: []}, sp = F.spacing || 22, TINTS = [0xffffff, 0xe8f0e0, 0xd8e6cc, 0xf0f4e8];
    let i = 0;
    const tryTree = (x, y) => { i++;
      if (!free(x, y - 6, 9)) return;
      place(hash(i, 3) < .55 ? 'pine' : 'tree', x, y, {scale: 0.9 + hash(i, 4) * 0.35, tint: TINTS[hash(i, 5) * 4 | 0]}); mark(x, y - 6, 12); };
    for (const e of F.edges) {
      if (e === 'n') for (let y = 14; y <= F.depth; y += sp * 0.7) for (let x = M.river.x + 30; x < this.W; x += sp) tryTree(x + (hash(x, y, 1) - .5) * 12, y + (hash(x, y, 2) - .5) * 8);
      if (e === 's') for (let y = this.H - F.depth + 18; y <= this.H + 14; y += sp * 0.7) for (let x = M.river.x + 30; x < this.W; x += sp) tryTree(x + (hash(x, y, 1) - .5) * 12, y + (hash(x, y, 2) - .5) * 8);
      if (e === 'e') for (let x = this.W - F.depth + 10; x <= this.W + 8; x += sp * 0.75) for (let y = 30; y < this.H; y += sp * 0.8) tryTree(x + (hash(x, y, 1) - .5) * 10, y + (hash(x, y, 2) - .5) * 10);
    }
    // ground cover in whatever open grass is left
    const S = M.scatter || {cell: 28, fill: 0.8, kinds: [['grass', 1]]}, total = S.kinds.reduce((a, k) => a + k[1], 0);
    const R = {bush: 12, shrub: 10, log: 12, stump: 8, rock: 7, grass: 6, bloom: 6};
    for (let y = 8; y < this.H; y += S.cell) for (let x = 8; x < this.W; x += S.cell) {
      const px = x + hash(x, y, 7) * (S.cell - 6), py = y + hash(x, y, 8) * (S.cell - 6);
      if (hash(x, y, 9) > S.fill) continue;
      let r = hash(x, y, 10) * total, kind = S.kinds[0][0];
      for (const [k, wgt] of S.kinds) { if ((r -= wgt) < 0) { kind = k; break; } }
      const rad = R[kind] || 8;
      if (!free(px, py - 4, rad)) continue;
      place(kind, px, py, {tint: kind === 'bush' || kind === 'shrub' ? TINTS[hash(x, y, 11) * 4 | 0] : 0});
      mark(px, py - 4, rad + 2);
    }
  }

  setShopRev(econ) {
    if (!this.buildings) { this.pendingRev = econ; return; }
    for (const l of (econ && econ.locations) || []) { const b = this.buildings.find(x => x.id === l.id); if (b && b.rev) b.rev.text = `今日 $${Math.round(l.revenue_today)}`; }
  }
  setBroken(id, v) { if (this.locs[id]) this.locs[id].broken = v; const b = (this.buildings || []).find(x => x.id === id); if (b) b.broken.visible = v; }

  updateLandmarks(list) {
    this.state.landmarks = list;
    if (!this.buildings) return;
    for (const lm of list || []) {
      const b = this.buildings.find(x => x.id === lm.location); if (!b) continue;
      const done = lm.state === 'completed', lit = done ? 16 : Math.round(16 * Math.max(0, Math.min(1, lm.progress)));
      const key = lit + (done ? 'd' : '');
      if (b.lmKey !== key) {
        b.lmKey = key; b.spr.texture = Texture.from(installationCanvas(lit, done)); b.spr.anchor.set(0.5, 1); b.spr.scale.set(1.5);
        if (!b.lmCast) b.lmCast = this.cast(b.spr, 1, 60); else b.lmCast.sh.texture = b.spr.texture;
        if (this.sun) this.shapeShadow(b.lmCast);
        if (!b.lmGlow) {
          b.lmGlow = new Container(); this.lights.addChild(b.lmGlow);
          [this.tex.glowWarm, this.tex.glowCool, this.tex.glowPink].forEach((t, i) => { const s = new Sprite(t); s.anchor.set(0.5); s.blendMode = 'add';
            s.x = b.d.x + (i - 1) * 16; s.y = b.d.y - 50 + (i % 2) * 12; s.scale.set(1.1); s.alpha = 0.5; b.lmGlow.addChild(s); });
        }
        b.lmDone = done; b.lmLit = lit;
      }
    }
  }

  /* ---------------------------------------------------------------- residents */
  resident(id) {
    let r = this.res[id];
    if (r) return r;
    const cloth = tok(`--r-${id}`) || '#8a7a6a', look = lookOf(id);
    const frames = {}; for (const d of DIRS) frames[d] = [0, 1, 2].map(f => Texture.from(charCanvas(cloth, look, d, f)));
    const c = new Container(); c.eventMode = 'static'; c.cursor = 'pointer';
    c.on('pointertap', () => { if (!this.dragMoved && this.onAgentClick) this.onAgentClick(id); });
    const shadow = new Graphics().ellipse(0, 0, 7, 2.5).fill({color: 0x000000, alpha: 0.18});
    const ring = new Graphics().ellipse(0, 0, 11, 4).stroke({color: 0x9a6b12, width: 2}); ring.visible = false;
    const spr = new Sprite(frames.down[0]); spr.anchor.set(0.5, 1);
    const icon = new Sprite(Texture.EMPTY); icon.anchor.set(0.5, 1); icon.x = 9; icon.y = -24; icon.visible = false;
    const tag = new Container(); tag.y = -27;
    const tt = new Text({text: agentName(id), resolution: 3, style: {fontFamily: FONT, fontSize: 8, fill: 0x2b2520}}); tt.anchor.set(0.5, 1);
    const tb = new Graphics().roundRect(-tt.width / 2 - 3, -tt.height - 1, tt.width + 6, tt.height + 2, 3).fill({color: 0xfffdf7, alpha: 0.92}).stroke({color: hexNum(cloth), width: 1.2});
    tag.addChild(tb, tt);
    c.addChild(shadow, ring, spr, icon, tag);
    const cst = this.cast(spr, 0.9, 16); cst.ao.visible = false;   // the figure keeps its own contact shadow
    c.hitArea = {contains: (x, y) => x > -9 && x < 9 && y > -24 && y < 2};
    this.objects.addChild(c);
    r = this.res[id] = {id, c, spr, icon, tag, tb, tt, ring, frames, cst, x: 0, y: 0, path: [], dir: 'down', frame: 0, ft: 0, phase: Math.random() * 1000, placed: false, loc: null, slot: -1};
    return r;
  }

  anchorsFor(loc) { return (this.M.locations[loc] || {}).landmark ? this.M.anchors.park : this.M.anchors.building; }

  route(from, to, tx, ty) {   // door -> its path -> around the plaza -> the other path -> the spot
    const pts = [], pa = (this.M.locations[from] || {}).path, pb = (this.M.locations[to] || {}).path;
    const A = this.walk[pa], B = this.walk[pb];
    if (from && to && from !== to && A && B) {
      pts.push(...A);
      const c = this.plazaC, R = this.M.plaza.r * this.T - 10, ea = A[A.length - 1], eb = B[B.length - 1];
      let a0 = Math.atan2(ea.y - c.y, ea.x - c.x), a1 = Math.atan2(eb.y - c.y, eb.x - c.x), da = a1 - a0;
      if (da > Math.PI) da -= 2 * Math.PI; if (da < -Math.PI) da += 2 * Math.PI;
      const n = Math.ceil(Math.abs(da) / 0.35);
      for (let i = 1; i < n; i++) { const t = a0 + da * i / n; pts.push({x: c.x + Math.cos(t) * R, y: c.y + Math.sin(t) * R}); }
      pts.push(...B.slice().reverse());
    }
    pts.push({x: tx, y: ty});
    return pts;
  }

  updateAgents(list) {
    this.state.agents = list;
    if (!this.buildings) return;
    const byLoc = {};
    for (const a of list) (byLoc[a.location] = byLoc[a.location] || []).push(a);
    const sleepers = {};
    for (const [loc, people] of Object.entries(byLoc)) {
      const anchors = this.anchorsFor(loc), taken = new Set();
      const isHome = (this.locs[loc] || {}).kind === 'home';
      // keep each resident on the anchor they already hold here; newcomers take the next free one
      const order = people.slice().sort((p, q) => { const rp = this.res[p.id], rq = this.res[q.id];
        return ((rq && rq.loc === loc && rq.slot >= 0) ? 1 : 0) - ((rp && rp.loc === loc && rp.slot >= 0) ? 1 : 0); });
      for (const a of order) {
        const r = this.resident(a.id);
        r.act = a.action; r.asleep = a.action === 'sleep' && isHome;
        if (r.asleep) { sleepers[loc] = (sleepers[loc] || 0) + 1; }
        let slot = r.loc === loc && r.slot >= 0 && !taken.has(r.slot) ? r.slot : -1;
        if (slot < 0) { for (let i = 0; i < anchors.length; i++) if (!taken.has(i)) { slot = i; break; } }
        if (slot < 0) slot = taken.size % anchors.length;
        taken.add(slot);
        const d = this.doorPx(loc), [ox, oy] = anchors[slot];
        this.moveTo(r, loc, slot, d.x + ox, d.y + oy, ox < -30 ? 'right' : ox > 30 ? 'left' : 'down');
      }
    }
    // two residents in conversation (spoke within the last 12 s) stand together, facing each other
    const now = performance.now();
    for (const [a, v] of Object.entries(this.talk)) {
      if (now - v.t > 12000) { delete this.talk[a]; continue; }
      const ra = this.res[a], rb = this.res[v.to];
      if (!ra || !rb || ra.loc !== rb.loc || ra.asleep || rb.asleep) continue;
      const left = ra.tx <= rb.tx ? ra : rb, right = left === ra ? rb : ra;
      this.moveTo(right, right.loc, right.slot, left.tx + 18, left.ty, 'left', true);
      left.face = 'right';
    }
    for (const b of this.buildings) {   // asleep inside: a quiet zzz by the house instead of figures outside
      const n = sleepers[b.id] || 0; b.zzz.visible = n > 0; b.zzzText.text = n > 1 ? '×' + n : '';
    }
    for (const r of Object.values(this.res)) {
      r.c.visible = !r.asleep;
      const icon = r.act === 'eat' ? this.tex.eat : r.act === 'work' ? this.tex.work : null;
      r.icon.visible = !!icon; if (icon) r.icon.texture = icon;
      r.ring.visible = !!this.focus && this.focus.has(r.id);
    }
    this.updateWindows();
  }

  moveTo(r, loc, slot, x, y, face, keepPath) {
    x = Math.round(x); y = Math.round(y);
    if (!r.placed) { r.x = x; r.y = y; r.placed = true; r.path = []; }
    else if (r.tx !== x || r.ty !== y) {
      const fromLoc = r.loc;
      r.path = keepPath ? [{x, y}] : this.route(fromLoc, loc, x, y);
      let len = 0, px = r.x, py = r.y; for (const p of r.path) { len += Math.hypot(p.x - px, p.y - py); px = p.x; py = p.y; }
      r.speed = Math.max(60, len / 2.2);   // a trip never takes more than ~2 s, even at 20x
    }
    r.tx = x; r.ty = y; r.loc = loc; r.slot = slot; r.face = face;
  }

  /* ---------------------------------------------------------------- bubbles */
  bubble(actor, loc, text, target) {
    if (!this.app || !text || !text.trim()) return;
    if (target) {   // remember who is talking with whom (one entry per pair) so they face each other
      for (const [k, v] of Object.entries(this.talk)) if (k === actor || v.to === actor || k === target || v.to === target) delete this.talk[k];
      this.talk[actor] = {to: target, t: performance.now()};
      if (this.state.agents) this.updateAgents(this.state.agents);   // turn to face each other now
    }
    const r = this.res[actor]; if (!r || r.asleep) return;
    this.bubbles.filter(b => b.actor === actor).forEach(b => this.removeBubble(b));
    const c = new Container();
    const t = new Text({text, resolution: 2, style: {fontFamily: "'Noto Sans TC',sans-serif", fontSize: 10, fill: 0x2b2520, wordWrap: true, breakWords: true, wordWrapWidth: 132, lineHeight: 13}});
    t.x = 6; t.y = 6;
    const w = Math.ceil(t.width) + 12, h = Math.ceil(t.height) + 10, ink = 0x2b2520, paper = 0xfbf7ee, rc = hexNum(tok(`--r-${actor}`) || '#9a6b12');
    const g = new Graphics(), cx = Math.round(w / 2);
    // a pixel frame: square edges with notched corners, the speaker's color as the top band, a stepped tail
    g.rect(1, 1, w - 2, h - 2).fill(paper);
    g.rect(2, 0, w - 4, 1).fill(ink).rect(2, h - 1, w - 4, 1).fill(ink).rect(0, 2, 1, h - 4).fill(ink).rect(w - 1, 2, 1, h - 4).fill(ink)
     .rect(1, 1, 1, 1).fill(ink).rect(w - 2, 1, 1, 1).fill(ink).rect(1, h - 2, 1, 1).fill(ink).rect(w - 2, h - 2, 1, 1).fill(ink);
    g.rect(2, 1, w - 4, 2).fill(rc);
    g.rect(cx - 2, h - 1, 5, 1).fill(paper).rect(cx - 3, h - 1, 1, 1).fill(ink).rect(cx + 3, h - 1, 1, 1).fill(ink)
     .rect(cx - 1, h, 3, 1).fill(paper).rect(cx - 2, h, 1, 1).fill(ink).rect(cx + 2, h, 1, 1).fill(ink).rect(cx - 1, h + 1, 3, 1).fill(ink).rect(cx, h + 2, 1, 1).fill(ink);
    c.addChild(g, t); c.pivot.set(cx, h + 3);
    this.overlay.addChild(c);
    const b = {c, actor, loc, w, h: h + 3, until: performance.now() + Math.min(9000, 3200 + text.length * 120)};
    this.bubbles.push(b);
    const here = this.bubbles.filter(x => x.loc === loc);
    while (here.length > 3) this.removeBubble(here.shift());
  }
  removeBubble(b) { if (b.gone) return; b.gone = true; b.c.destroy({children: true}); const i = this.bubbles.indexOf(b); if (i >= 0) this.bubbles.splice(i, 1); }
  clearBubbles() { this.bubbles.slice().forEach(b => this.removeBubble(b)); }

  setFocus(ids) {
    this.focus = ids && ids.length ? new Set(ids) : null;
    for (const r of Object.values(this.res)) r.ring.visible = !!this.focus && this.focus.has(r.id);
  }

  /* ---------------------------------------------------------------- day / night */
  applyDaylight(minute) {
    this.state.minute = minute;
    if (!this.app) return;
    const {n, dusk, h} = phaseOf(minute);
    this.phase = {n, dusk, h};
    let col = mixHex(0xffffff, 0xf0b47a, dusk * 0.45);
    col = mixHex(col, 0x4a5a92, n * 0.78);
    this.night.tint = col;
    const lamp = Math.min(1, n * 1.1 + dusk * 0.5);
    for (const g of this.lampGlows) g.alpha = lamp;
    const dow = ((Math.floor(minute / 1440) % 7) + 7) % 7;
    for (const b of this.buildings || []) b.closed = !!(b.l.owner && (b.l.closed_days || []).includes(dow));
    for (const b of this.buildings || []) b.sign.visible = b.closed;
    this.updateWindows();
    if (!this.lastSunH || Math.abs(h - this.lastSunH) > 0.08) { this.lastSunH = h; this.updateShadows(); }
  }
  updateWindows() {
    if (!this.phase || !this.buildings) return;
    const {n, dusk, h} = this.phase, glow = Math.min(1, n + dusk * 0.6);
    const awake = {}; for (const r of Object.values(this.res)) if (r.c.visible && r.loc) awake[r.loc] = (awake[r.loc] || 0) + 1;
    for (const b of this.buildings) {
      const shop = !!b.l.owner || b.l.kind === 'market';
      const lit = b.lit = !b.closed && (awake[b.id] > 0 || (shop && h >= 6 && h < 22));
      b.windows.forEach(w => { w.alpha = lit ? glow : 0; });
      if (b.lmGlow) b.lmGlow.alpha = (b.lmLit || 0) / 16 * Math.min(0.8, n + dusk * 0.4);
    }
  }

  /* ---------------------------------------------------------------- weather */
  renderEffects(effects) {
    this.state.effects = effects;
    if (!this.app) return;
    const rain = (effects || []).some(e => e.type === 'rain'), fest = (effects || []).find(e => e.type === 'festival');
    if (rain && !this.drops) {
      const g = new Graphics().moveTo(0, 0).lineTo(-2, 7).stroke({color: 0xbcd3e6, width: 1, alpha: 0.6}), tex = this.app.renderer.generateTexture(g);
      this.drops = []; for (let i = 0; i < 110; i++) { const s = new Sprite(tex); s.x = Math.random() * this.W; s.y = Math.random() * this.H; this.fx.addChild(s); this.drops.push(s); }
    }
    if (this.drops) this.drops.forEach((s, i) => { s.visible = rain && (!this.lowFx || i % 2 === 0); });
    this.raining = rain;
    if (this.festG) { this.festG.destroy(); this.festG = null; }
    if (fest) {
      const d = this.doorPx(fest.location), g = this.festG = new Graphics(), y = d.y - 70, x0 = d.x - 48, cols = [0xd9544d, 0xe0a63c, 0x5f7d5b, 0x5e86b0];
      g.moveTo(x0, y).lineTo(x0 + 96, y).stroke({color: 0x8a6a52, width: 1});
      for (let i = 0; i < 8; i++) g.poly([x0 + i * 12, y, x0 + i * 12 + 8, y, x0 + i * 12 + 4, y + 9]).fill(cols[i % 4]);
      this.labels.addChild(g);
    }
  }

  /* ---------------------------------------------------------------- frame */
  tick(dt) {
    const now = performance.now();
    for (const r of Object.values(this.res)) {
      if (!r.c.visible) { if (r.path.length) { const p = r.path[r.path.length - 1]; r.x = p.x; r.y = p.y; r.path = []; } }
      let moving = false;
      if (r.path.length) {
        const p = r.path[0], dx = p.x - r.x, dy = p.y - r.y, dist = Math.hypot(dx, dy), stepLen = r.speed * dt / 1000;
        if (dist <= stepLen) { r.x = p.x; r.y = p.y; r.path.shift(); } else { r.x += dx / dist * stepLen; r.y += dy / dist * stepLen; }
        r.dir = Math.abs(dx) > Math.abs(dy) ? (dx < 0 ? 'left' : 'right') : (dy < 0 ? 'up' : 'down');
        moving = true;
      } else r.dir = r.face || 'down';
      if (moving) { r.ft += dt; if (r.ft > 130) { r.ft = 0; r.frame = r.frame === 1 ? 2 : 1; } }
      else r.frame = 0;
      r.spr.texture = r.frames[r.dir][r.frame];
      r.spr.y = moving || this.lowFx || this.reduce ? 0 : ((now + r.phase) % 1400 < 700 ? 0 : -1);   // idle breathing
      r.c.x = Math.round(r.x); r.c.y = Math.round(r.y); r.c.zIndex = r.c.y;
      if (r.cst.sh) { r.cst.sh.texture = r.spr.texture; r.cst.sh.x = r.c.x + (this.sun ? this.sun.dir * 2 : 0); r.cst.sh.y = r.c.y; r.cst.sh.visible = r.c.visible && this.sun && this.sun.alpha > 0.01; }
      r.tag.visible = this.zoom >= 0.75 || r.ring.visible;
    }
    for (const b of this.bubbles.slice()) {
      const r = this.res[b.actor];
      if (now > b.until || !r || !r.c.visible) { this.removeBubble(b); continue; }
      b.c.x = r.c.x; b.c.y = r.c.y - 27;   // just above the head
    }
    // stack bubbles that would overlap (newest on top)
    const bs = this.bubbles;
    for (let i = 0; i < bs.length; i++) for (let j = 0; j < i; j++) {
      const a = bs[i], b = bs[j];
      if (Math.abs(a.c.x - b.c.x) < (a.w + b.w) / 2 && Math.abs(a.c.y - b.c.y) < (a.h + b.h) / 2) b.c.y = a.c.y - a.h - 2;
    }
    if (!this.lowFx && !this.reduce) for (const s of this.glints) { s.y += s.v * dt / 1000; s.alpha = 0.35 + 0.35 * Math.sin((now / 500) + s.x); if (s.y > this.H) { s.y = -2; s.x = 6 + Math.random() * (this.M.river.x - 18); } }
    if (this.raining && this.drops) for (const s of this.drops) { if (!s.visible) continue; s.y += 0.42 * dt; s.x -= 0.1 * dt; if (s.y > this.H) { s.y = -8; s.x = Math.random() * this.W; } if (s.x < 0) s.x += this.W; }
    if (!this.lowFx && !this.reduce) this.ambientMotion(now, dt);
    for (const b of this.buildings || []) if (b.lmDone && b.lmGlow && !this.lowFx && !this.reduce) b.lmGlow.children.forEach((s, i) => { s.scale.set(1.05 + 0.1 * Math.sin(now / 380 + i * 2)); });
  }

  // Signs of life, all slow and small: a tree's canopy now and then, chimney smoke from
  // buildings that are open, cherry petals along the lane, a bird crossing, a butterfly.
  ambientMotion(now, dt) {
    if (this.trees && (!this.nextSway || now > this.nextSway)) {
      this.nextSway = now + 2500 + Math.random() * 3500;
      const s = this.trees[Math.random() * this.trees.length | 0]; this.sway.push({s, t0: now});
    }
    this.sway = this.sway.filter(w => { const k = (now - w.t0) / 1800; if (k >= 1) { w.s.skew.x = 0; return false; }
      w.s.skew.x = 0.045 * Math.sin(k * Math.PI * 3) * (1 - k); return true; });
    for (const b of this.buildings || []) if (b.chimney && b.lit && this.puffs.length < 24 && (!b.nextPuff || now > b.nextPuff)) {
      b.nextPuff = now + 1300 + Math.random() * 700;
      const s = new Sprite(this.tex.puff); s.anchor.set(0.5); s.x = b.chimney.x; s.y = b.chimney.y; s.alpha = 0.7; s.t0 = now; this.fx.addChild(s); this.puffs.push(s);
    }
    this.puffs = this.puffs.filter(s => { const k = (now - s.t0) / 3400; if (k >= 1) { s.destroy(); return false; }
      s.y -= 7 * dt / 1000; s.x += 3 * dt / 1000; s.scale.set(1 + k * 1.6); s.alpha = 0.7 * (1 - k); return true; });
    if (this.blossoms && this.petals.length < 26 && (!this.nextPetal || now > this.nextPetal)) {
      this.nextPetal = now + 380;
      const t = this.blossoms[Math.random() * this.blossoms.length | 0], s = new Sprite(this.tex.petal);
      s.x = t.x - 12 + Math.random() * 24; s.y = t.y - 30 + Math.random() * 12; s.t0 = now; s.ph = Math.random() * 6; this.fx.addChild(s); this.petals.push(s);
    }
    this.petals = this.petals.filter(s => { const k = (now - s.t0) / 4200; if (k >= 1) { s.destroy(); return false; }
      s.y += 11 * dt / 1000; s.x += (9 + 6 * Math.sin(now / 400 + s.ph)) * dt / 1000; s.alpha = k < .8 ? 1 : (1 - k) * 5; return true; });
    if (now > this.nextBird) {   // a bird crosses the town every 15-40 s
      this.nextBird = now + 15000 + Math.random() * 25000;
      const ltr = Math.random() < .5, s = new Sprite(this.tex.bird[0]); s.anchor.set(0.5);
      s.x = ltr ? -10 : this.W + 10; s.y = 40 + Math.random() * (this.H * .6); s.vx = (ltr ? 1 : -1) * (55 + Math.random() * 25); s.y0 = s.y; s.kind = 'bird'; s.t0 = now;
      this.labels.addChild(s); this.critters.push(s);
    }
    if (this.flowerBeds && now > this.nextFly) {   // now and then a butterfly over a flower bed
      this.nextFly = now + 12000 + Math.random() * 18000;
      const f = this.flowerBeds[Math.random() * this.flowerBeds.length | 0], s = new Sprite(this.tex.fly[0]); s.anchor.set(0.5);
      s.x = f.x; s.y = f.y - 10; s.kind = 'fly'; s.t0 = now; s.hx = f.x; s.hy = f.y - 10; this.objects.addChild(s); s.zIndex = 99999; this.critters.push(s);
    }
    this.critters = this.critters.filter(s => {
      const age = now - s.t0;
      if (s.kind === 'bird') { s.x += s.vx * dt / 1000; s.y = s.y0 + 6 * Math.sin(age / 500); s.texture = this.tex.bird[(age / 160 | 0) % 2]; s.scale.x = s.vx < 0 ? -1 : 1;
        if (s.x < -20 || s.x > this.W + 20) { s.destroy(); return false; } return true; }
      if (age > 9000) { s.destroy(); return false; }
      s.x = s.hx + 14 * Math.sin(age / 900) + 4 * Math.sin(age / 210); s.y = s.hy - 6 - 8 * Math.abs(Math.sin(age / 700)); s.texture = this.tex.fly[(age / 120 | 0) % 2];
      return true;
    });
  }

  // Degradation: if the frame rate sags (< 45 fps for 3 s), drop the decorative motion
  // (water glints, idle bob, half the rain, installation shimmer, sway, smoke, petals,
  // birds, butterflies) and cap at 30 fps;
  // try full quality again after 20 s, and stay low for good if it sags a second time.
  watchFps() {
    if (!this.app || !this.active) return;
    const fps = this.app.ticker.FPS;
    if (!this.lowFx) {
      this.slow = fps < 45 ? this.slow + 1 : 0;
      if (this.slow >= 3) { this.setLowFx(true); this.lowSince = performance.now(); this.drops2 = (this.drops2 || 0) + 1; }
    } else if (this.drops2 < 2 && performance.now() - this.lowSince > 20000) { this.setLowFx(false); this.slow = 0; }
  }
  setLowFx(v) {
    this.lowFx = v; this.app.ticker.maxFPS = v ? 30 : 0;
    this.app.canvas.dataset.quality = v ? 'low' : 'full';   // visible in devtools
    this.glints.forEach(s => { s.visible = !v; });
    if (v) { [...this.puffs, ...this.petals, ...this.critters].forEach(s => s.destroy()); this.puffs = []; this.petals = []; this.critters = []; }
    if (this.drops) this.renderEffects(this.state.effects);
  }
  get quality() { return this.lowFx ? 'low' : 'full'; }

  setActive(v) {
    this.active = v;
    if (!this.app) return;
    if (v) this.app.ticker.start(); else this.app.ticker.stop();
  }

  /* ---------------------------------------------------------------- camera */
  setupCamera() {
    const cv = this.app.canvas; cv.style.touchAction = 'none';
    const ptrs = new Map(); let last = null, pinch = null;
    this.zoom = 1;
    cv.addEventListener('pointerdown', e => {
      ptrs.set(e.pointerId, {x: e.clientX, y: e.clientY});
      if (ptrs.size === 1) { this.dragMoved = false; last = {x: e.clientX, y: e.clientY}; this.downAt = {x: e.clientX, y: e.clientY}; }
      if (ptrs.size === 2) { const [a, b] = [...ptrs.values()]; pinch = {d: Math.hypot(a.x - b.x, a.y - b.y), z: this.zoom}; }
    });
    cv.addEventListener('pointermove', e => {
      if (!ptrs.has(e.pointerId)) return;
      ptrs.set(e.pointerId, {x: e.clientX, y: e.clientY});
      if (ptrs.size === 2 && pinch) {
        const [a, b] = [...ptrs.values()], r = cv.getBoundingClientRect();
        this.zoomAt(pinch.z * Math.hypot(a.x - b.x, a.y - b.y) / pinch.d, (a.x + b.x) / 2 - r.left, (a.y + b.y) / 2 - r.top); this.dragMoved = true; return;
      }
      if (!last) return;
      if (!this.dragMoved && Math.hypot(e.clientX - this.downAt.x, e.clientY - this.downAt.y) > 5) { this.dragMoved = true; cv.style.cursor = 'grabbing'; }
      if (this.dragMoved) { this.world.x += e.clientX - last.x; this.world.y += e.clientY - last.y; this.camSet = true; this.clampCam(); }
      last = {x: e.clientX, y: e.clientY};
    });
    const up = e => { ptrs.delete(e.pointerId); if (ptrs.size < 2) pinch = null; if (!ptrs.size) { last = null; cv.style.cursor = ''; } };
    cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up); cv.addEventListener('pointerleave', up);
    cv.addEventListener('wheel', e => { e.preventDefault(); const r = cv.getBoundingClientRect();
      this.zoomAt(this.zoom * Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top); }, {passive: false});
    cv.addEventListener('dblclick', () => { this.camSet = false; this.fit(); });
    // Pixi's resizeTo only hears window resizes; the map's box also changes when a view is shown
    this.ro = new ResizeObserver(() => { if (!this.el.clientWidth) return; this.app.resize(); if (!this.camSet) this.fit(); else this.clampCam(); });
    this.ro.observe(this.el);
  }
  fit() {
    if (!this.app) return;
    const w = this.el.clientWidth, h = this.el.clientHeight; if (!w || !h) return;
    let z = Math.min(w / this.W, h / this.H);
    if (w < 700) z = Math.min(h / this.H, (w / this.W) * 2.4);   // phones: fill the height, pan sideways
    this.zoom = z; this.world.scale.set(z);
    const hub = this.M.plaza.c;
    this.world.x = Math.round(w / 2 - (w < 700 ? this.px(hub[0]) : this.W / 2) * z);
    this.world.y = Math.round((h - this.H * z) / 2);
    this.clampCam();
  }
  zoomAt(z, sx, sy) {
    z = Math.max(0.35, Math.min(4, z));
    const wx = (sx - this.world.x) / this.zoom, wy = (sy - this.world.y) / this.zoom;
    this.zoom = z; this.world.scale.set(z);
    this.world.x = sx - wx * z; this.world.y = sy - wy * z; this.camSet = true; this.clampCam();
  }
  clampCam() {
    const w = this.el.clientWidth, h = this.el.clientHeight, ww = this.W * this.zoom, wh = this.H * this.zoom, m = 80;
    this.world.x = ww <= w ? Math.min(Math.max(this.world.x, 0), w - ww) : Math.min(m, Math.max(this.world.x, w - ww - m));
    this.world.y = wh <= h ? Math.min(Math.max(this.world.y, 0), h - wh) : Math.min(m, Math.max(this.world.y, h - wh - m));
  }

  destroy() {
    this.alive = false; clearInterval(this.fpsTimer); this.ro && this.ro.disconnect();
    if (this.app) { try { this.app.destroy({removeView: true}, {children: true, texture: true, textureSource: true}); } catch (e) { /* already gone */ } }
  }
}
