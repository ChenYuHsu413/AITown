// The 3-D town as the village / replay map. Same interface as the old pixel VillageMap
// (buildMap, updateAgents, updateLandmarks, renderEffects, applyDaylight, setShopRev,
// setBroken, bubble, clearBubbles, setFocus, setActive, destroy) plus mark() for major
// beats. Decides who stands where (interior anchors by pose, outside anchors, talk
// pairs), walks people along the paths, and keeps the DOM overlay (name tags, bubbles,
// place labels, gold markers, conversation lines) pinned to the 3-D scene.
import * as THREE from 'three';
import {agentName, phaseOf} from '../names.js';
import {TownScene} from './scene.js';

const CAST = ['jiji', 'ange', 'oula', 'lengyue', 'azong', 'xixi', 'aisi', 'xue', 'long', 'kuaizheng'];
const ORDER = id => { const i = CAST.indexOf(id); return i < 0 ? 99 : i; };
const tok = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const DEG = Math.PI / 180;
let SPECS = null;
const loadSpecs = () => SPECS || (SPECS = Promise.all(['town', 'residents'].map(f => fetch(`/static/js/town3d/${f}.json`).then(r => r.json()))));
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

export class TownMap {
  constructor(el, {onAgentClick, showRevenue = true} = {}) {
    this.el = el; this.onAgentClick = onAgentClick; this.showRevenue = showRevenue;
    this.locs = {}; this.res = {}; this.talk = {}; this.bubbles = []; this.marks = []; this.focus = null; this.bedOf = {};
    this.state = {agents: null, landmarks: null, effects: null, minute: null, rev: null, locations: null};
    this.active = true; this.alive = true;
    try { this.cutMode = localStorage.getItem('aitown.cutaway') === 'click' ? 'click' : 'auto'; } catch (e) { this.cutMode = 'auto'; }
    this.manualOpen = new Set();
    el.__town = this;   // the harness / devtools handle
    this.ready = this.init().catch(e => console.error('town init failed', e));
  }
  async init() {
    const [S, FS] = await loadSpecs(); if (!this.alive) return;
    this.S = S; this.FS = FS;
    this.sc = new TownScene(this.el, S, FS, {onPick: id => this.openCard(id), onFrame: dt => this.step(dt)});
    this.sc.onFollowEnd = () => { for (const r of Object.values(this.res)) r.tag.classList.remove('following'); };
    this.sc.afterFrame = () => this.track();
    this.sc.setActive(this.active);
    this.ov = document.createElement('div'); this.ov.className = 't3-overlay'; this.el.appendChild(this.ov);
    this.svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); this.svg.classList.add('t3-lines'); this.ov.appendChild(this.svg);
    this.places = {};
    for (const [id, b] of Object.entries(this.sc.buildings)) {
      const d = document.createElement('div'); d.className = 't3-place'; d.innerHTML = `<b>${esc(b.B.name || id)}</b><span class="rev"></span><span class="st"></span>`;
      this.ov.appendChild(d); this.places[id] = {el: d, b, pos: new THREE.Vector3(b.group.position.x, this.sc.groundY + b.top + 1.4, b.group.position.z)};
      d.addEventListener('click', () => { if (this.cutMode === 'click') this.toggleOpen(id); });
    }
    this.addCutToggle();
    const s = this.state;
    if (s.locations) this.buildMap(s.locations);
    if (s.minute != null) this.applyDaylight(s.minute);
    if (s.landmarks) this.updateLandmarks(s.landmarks);
    if (s.effects) this.renderEffects(s.effects);
    if (s.agents) this.updateAgents(s.agents);
    if (s.rev) this.setShopRev(s.rev);
  }

  /* ---------------------------------------------------------------- places */
  buildMap(locations) {
    this.state.locations = locations; if (!this.sc) return;
    this.locs = {}; for (const l of locations) this.locs[l.id] = l;
    for (const l of locations) if (l.broken) this.setBroken(l.id, true);
  }
  setShopRev(econ) {
    this.state.rev = econ; if (!this.sc || !this.showRevenue) return;
    for (const l of (econ && econ.locations) || []) { const p = this.places[l.id]; if (p) p.el.querySelector('.rev').textContent = `今日 $${Math.round(l.revenue_today)}`; }
  }
  setBroken(id, v) {
    if (this.locs[id]) this.locs[id].broken = v;
    const p = this.places && this.places[id]; if (p) { p.broken = v; this.placeStatus(id); }
  }
  placeStatus(id) {
    const p = this.places[id], b = p.b, bits = [];
    if (p.broken) bits.push('維修中'); if (b.closed) bits.push('今日公休');
    const n = p.hiddenSleepers || 0; if (n) bits.push('z' + (n > 1 ? ' ×' + n : ''));
    p.el.querySelector('.st').textContent = bits.join(' · ');
    p.el.classList.toggle('warn', !!p.broken); p.el.classList.toggle('closed', !!b.closed);
  }
  updateLandmarks(list) {
    this.state.landmarks = list; if (!this.sc) return;
    const lm = (list || []).find(x => x.id === 'installation') || (list || []).find(x => x.location === 'park');
    if (lm) this.sc.setInstallation(lm.progress || 0, lm.state === 'completed');
  }
  renderEffects(effects) {
    this.state.effects = effects; if (!this.sc) return;
    this.sc.setRain((effects || []).some(e => e.type === 'rain'));
    const fest = (effects || []).find(e => e.type === 'festival'); this.sc.setFestival(fest ? fest.location : null);
  }
  applyDaylight(minute) {
    this.state.minute = minute; if (!this.sc) return;
    this.sc.setTime(minute); this.phase = phaseOf(minute);
    const dow = ((Math.floor(minute / 1440) % 7) + 7) % 7;
    for (const [id, p] of Object.entries(this.places)) {
      const day = Math.floor(minute / 1440) + 1;   // closed_on: the owner chose not to open today
      const l = this.locs[id]; const closed = !!(l && l.owner && ((l.closed_days || []).includes(dow) || l.closed_on === day));
      if (closed !== p.b.closed) { p.b.closed = closed; if (p.b.closedSign) p.b.closedSign.visible = closed; this.placeStatus(id); }
    }
    this.updateWindows();
  }
  updateWindows() {
    if (!this.phase || !this.sc) return;
    const {n, dusk, h} = this.phase, glow = Math.min(1, n * 1.15 + dusk * 0.45);
    const awake = {}; for (const r of Object.values(this.res)) if (r.f.root.visible && r.loc) awake[r.loc] = (awake[r.loc] || 0) + 1;
    const open = [];
    for (const b of Object.values(this.sc.buildings)) {
      const l = this.locs[b.id] || {}, shop = !!l.owner || l.kind === 'market';
      b.lit = !b.closed && (awake[b.id] > 0 || (shop && h >= 6 && h < 22));
      b.glass.emissiveIntensity = b.lit ? glow * 2.4 : 0;
      b.smokeOn = !!b.smoke && !b.closed && h >= 5 && h < 20;
      if (b.wantOpen) open.push(b);
    }
    // the pooled warm lights go to the open rooms (nearest the view first) when it's dark
    const pool = this.sc.roomLights, t = this.sc.cam.target;
    open.sort((a, c) => a.group.position.distanceTo(t) - c.group.position.distanceTo(t));
    pool.forEach((L, i) => {
      const b = open[i];
      if (b && glow > 0.05) { L.position.copy(b.group.localToWorld(new THREE.Vector3(0, 2.4, 0))); L.intensity = glow * this.S.render.interiorLightIntensity; }
      else L.intensity = 0;
    });
  }

  /* ---------------------------------------------------------------- residents */
  resident(id) {
    let r = this.res[id]; if (r) return r;
    const f = this.sc.figure(id, tok(`--r-${id}`) || '#8a7a6a');
    const tag = document.createElement('button'); tag.type = 'button'; tag.className = 't3-tag'; tag.style.setProperty('--rc', `var(--r-${id})`);
    tag.innerHTML = `<i></i>${esc(agentName(id))}<em></em>`; tag.addEventListener('click', () => { this.sc.follow(id); for (const x of Object.values(this.res)) x.tag.classList.toggle('following', x.id === id); this.openCard(id); });
    this.ov.appendChild(tag);
    r = this.res[id] = {id, f, tag, path: [], placed: false, loc: null, slot: -1, tx: 0, tz: 0, face: 0, pose: 'idle'};
    return r;
  }
  worldAt(b, a) { const v = b.group.localToWorld(new THREE.Vector3(a.x, 0, a.z)); return {x: v.x, z: v.z, face: (a.face + (b.B.rot || 0)) * DEG}; }
  outsideSpots(loc) {
    const b = this.sc.buildings[loc];
    if (b && b.B.anchors) return b.B.anchors.map(([x, z]) => { const v = b.group.localToWorld(new THREE.Vector3(x, 0, z)); return [v.x, v.z]; });
    const d = b ? b.door : this.sc.plazaC;
    return this.S.outside.map(([dx, dz]) => [d.x + dx, d.z + dz]);
  }
  updateAgents(list) {
    this.state.agents = list; if (!this.sc) return;
    const byLoc = {};
    for (const a of list) (byLoc[a.location] = byLoc[a.location] || []).push(a);
    const seen = new Set();
    for (const [loc, people] of Object.entries(byLoc)) {
      const b = this.sc.buildings[loc], spec = b && b.B.interior, A = b && b.anchors, taken = new Set(), spots = this.outsideSpots(loc);
      const used = {beds: new Set(), seats: new Set(), work: new Set(), stand: new Set()};
      const inSlot = (kind, id) => {
        if (!A) return null; const L = A[kind] || [];
        if (kind === 'beds') {
          const mine = (this.bedOf[loc] = this.bedOf[loc] || {}); let i = mine[id];
          if (i == null) { const pref = (spec.bedOf || []).indexOf(id); i = pref >= 0 && !Object.values(mine).includes(pref) ? pref : L.findIndex((_, k) => !Object.values(mine).includes(k)); if (i >= 0) mine[id] = i; }
          if (i == null || i < 0 || used.beds.has(i)) return null; used.beds.add(i); return L[i];
        }
        let order = L.map((_, k) => k);
        if (kind === 'work' && (this.locs[loc] || {}).owner && id !== this.locs[loc].owner) order = order.slice(1).concat(0);
        for (const k of order) if (!used[kind].has(k)) { used[kind].add(k); return L[k]; }
        return null;
      };
      const owner = (this.locs[loc] || {}).owner;
      const order = people.slice().sort((p, q) => (q.id === owner) - (p.id === owner) || ORDER(p.id) - ORDER(q.id));
      for (const a of order) {
        const r = this.resident(a.id); seen.add(a.id); r.act = a.action; r.asleep = false;
        const want = a.action === 'sleep' ? 'beds' : (a.action === 'eat' || a.action === 'rest') ? 'seats' : a.action === 'work' ? 'work' : 'stand';
        let spot = inSlot(want, a.id), kind = want; if (!spot && want !== 'stand') { spot = inSlot('stand', a.id); kind = 'stand'; }
        if (spot && b) {
          r.inside = loc;
          r.pose = kind === 'beds' ? 'sleep' : kind === 'seats' || (kind === 'work' && spec.sitAtWork) ? 'sit' : 'idle';
          const w = this.worldAt(b, spot);
          this.moveTo(r, loc, -1, w.x, w.z, w.face, false, b.floorY);
          continue;
        }
        r.inside = null; r.pose = 'idle';
        if (a.action === 'sleep' && (this.locs[loc] || {}).kind === 'home') r.asleep = true;   // no bed free: asleep out of sight
        let slot = r.loc === loc && r.slot >= 0 && !taken.has(r.slot) ? r.slot : -1;
        if (slot < 0) for (let i = 0; i < spots.length; i++) if (!taken.has(i)) { slot = i; break; }
        if (slot < 0) slot = taken.size % spots.length;
        taken.add(slot);
        const [x, z] = spots[slot], d = b ? b.door : this.sc.plazaC;
        this.moveTo(r, loc, slot, x, z, Math.atan2(d.x - x, d.z - z) + Math.PI, false, 0);
      }
    }
    for (const id of Object.keys(this.res)) if (!seen.has(id)) { const r = this.res[id]; r.f.root.visible = false; r.tag.hidden = true; }
    // two residents in conversation (spoke within the last 12 s) stand together, facing each other
    const now = performance.now(), gap = this.S.residents.talkGap;
    for (const [a, v] of Object.entries(this.talk)) {
      if (now - v.t > 12000) { delete this.talk[a]; continue; }
      const ra = this.res[a], rb = this.res[v.to];
      if (!ra || !rb || ra.loc !== rb.loc || ra.asleep || rb.asleep || ra.inside || rb.inside) continue;
      const ang = Math.atan2(ra.tx - this.sc.plazaC.x, ra.tz - this.sc.plazaC.z) + Math.PI / 2;
      const bx = ra.tx + Math.sin(ang) * gap, bz = ra.tz + Math.cos(ang) * gap;
      this.moveTo(rb, rb.loc, rb.slot, bx, bz, Math.atan2(ra.tx - bx, ra.tz - bz), true, 0);
      ra.face = Math.atan2(bx - ra.tx, bz - ra.tz);
    }
    // cutaways: open while someone is inside (auto) or by click
    for (const b of Object.values(this.sc.buildings)) {
      if (!b.anchors) continue;
      b.occupied = Object.values(this.res).some(r => r.inside === b.id);
      b.wantOpen = this.cutMode === 'auto' ? b.occupied : this.manualOpen.has(b.id);
    }
    for (const [id, p] of Object.entries(this.places)) {
      const hidden = Object.values(this.res).filter(r => r.loc === id && (r.asleep || (r.inside === id && r.pose === 'sleep' && !p.b.wantOpen))).length;
      if (hidden !== p.hiddenSleepers) { p.hiddenSleepers = hidden; this.placeStatus(id); }
    }
    for (const r of Object.values(this.res)) {
      if (!seen.has(r.id)) continue;
      const b = r.inside && this.sc.buildings[r.inside];
      r.visible = !r.asleep && (!b || b.wantOpen);
      r.f.root.visible = r.visible; r.tag.hidden = !r.visible;
      r.tag.classList.toggle('focus', !!this.focus && this.focus.has(r.id));
      r.tag.querySelector('em').textContent = r.pose === 'sleep' ? 'z z' : '';
    }
    this.updateWindows();
  }
  // the floating character card opens next to the resident (client coordinates of their head)
  openCard(id) {
    const r = this.res[id], box = this.el.getBoundingClientRect();
    const at = r && r.screen ? {x: box.left + r.screen.x, y: box.top + (r.tagY ?? r.screen.y)} : null;
    if (this.onAgentClick) this.onAgentClick(id, at);
  }
  isOpen(b) { return !!b.wantOpen; }
  toggleOpen(id) { if (this.manualOpen.has(id)) this.manualOpen.delete(id); else this.manualOpen.add(id); if (this.state.agents) this.updateAgents(this.state.agents); }
  addCutToggle() {
    const btn = document.createElement('button'); btn.type = 'button'; btn.className = 't3-cut';
    const label = () => { btn.textContent = this.cutMode === 'auto' ? '室內:自動打開' : '室內:點地名打開'; btn.title = this.cutMode === 'auto' ? '有人在裡面就掀開屋頂;點一下改成手動' : '點建築上方的地名打開或關上;點一下改回自動'; };
    btn.onclick = () => { this.cutMode = this.cutMode === 'auto' ? 'click' : 'auto'; this.manualOpen.clear(); try { localStorage.setItem('aitown.cutaway', this.cutMode); } catch (e) {} label(); if (this.state.agents) this.updateAgents(this.state.agents); };
    label(); this.el.appendChild(btn); this.cutBtn = btn;
  }

  // a route: door -> its path -> round the plaza -> the other path -> the spot
  route(from, to, x, z) {
    const pts = [], W = this.sc.walk, A = W[from], B = W[to];
    if (from && to && from !== to && A && B) {
      pts.push(...A);
      const c = this.sc.plazaC, R = this.S.plaza.radius - 0.4, ea = A[A.length - 1], eb = B[B.length - 1];
      let a0 = Math.atan2(ea.z - c.z, ea.x - c.x), a1 = Math.atan2(eb.z - c.z, eb.x - c.x), da = a1 - a0;
      if (da > Math.PI) da -= 2 * Math.PI; if (da < -Math.PI) da += 2 * Math.PI;
      const n = Math.ceil(Math.abs(da) / 0.3);
      for (let i = 1; i < n; i++) { const t = a0 + da * i / n; pts.push(new THREE.Vector3(c.x + Math.cos(t) * R, 0, c.z + Math.sin(t) * R)); }
      pts.push(...B.slice().reverse());
    } else if (from && to && from !== to && (A || B)) pts.push(...(A || B.slice().reverse()));
    pts.push(new THREE.Vector3(x, 0, z));
    return pts.map(p => ({x: p.x, z: p.z}));
  }
  moveTo(r, loc, slot, x, z, face, keepPath, lift) {
    const f = r.f;
    if (!r.placed) { f.x = x; f.z = z; r.placed = true; r.path = []; f.heading = face; }
    else if (Math.abs(r.tx - x) > 0.01 || Math.abs(r.tz - z) > 0.01) {
      r.path = keepPath ? [{x, z}] : this.route(r.loc, loc, x, z);
      let len = 0, px = f.x, pz = f.z; for (const p of r.path) { len += Math.hypot(p.x - px, p.z - pz); px = p.x; pz = p.z; }
      r.speed = Math.max(this.S.residents.speed, len / this.S.residents.maxTrip);
    }
    r.tx = x; r.tz = z; r.loc = loc; r.slot = slot; r.face = face; r.lift = lift || 0;
  }
  step(dt) {   // every frame: walk along the path, then settle into the pose
    const G = this.sc.groundY, now = performance.now();
    for (const r of Object.values(this.res)) {
      const f = r.f;
      if (r.path.length) {
        let left = r.speed * dt;
        while (left > 0 && r.path.length) {
          const p = r.path[0], dx = p.x - f.x, dz = p.z - f.z, d = Math.hypot(dx, dz);
          if (d > 0.001) f.heading = turn(f.heading, Math.atan2(dx, dz), 0.35);
          if (d <= left) { f.x = p.x; f.z = p.z; left -= d; r.path.shift(); } else { f.x += dx / d * left; f.z += dz / d * left; left = 0; }
        }
        f.state = 'walk'; f.y = G + groundLift(this.sc, f.x, f.z);
        continue;
      }
      const talking = (this.talk[r.id] && now - this.talk[r.id].t < 12000) || Object.values(this.talk).some(v => v.to === r.id && now - v.t < 12000);
      f.state = r.pose === 'sleep' ? 'sleep' : r.pose === 'sit' ? 'sit' : talking ? 'talk' : 'idle';
      f.heading = turn(f.heading, r.face, 0.12);
      f.y = G + (r.inside ? r.lift : groundLift(this.sc, f.x, f.z)) + (f.state === 'sleep' ? this.S.residents.sleepLift : 0);
    }
  }

  /* ---------------------------------------------------------------- overlay: tags, bubbles, markers, lines */
  bubble(actor, loc, text, target) {
    if (!this.sc || !text || !text.trim()) return;
    if (target) {
      for (const [k, v] of Object.entries(this.talk)) if (k === actor || v.to === actor || k === target || v.to === target) delete this.talk[k];
      this.talk[actor] = {to: target, t: performance.now()};
      if (this.state.agents) this.updateAgents(this.state.agents);
    }
    const r = this.res[actor]; if (!r || r.asleep) return;
    // Lines are paced out at reading speed, so at high sim speeds a line can arrive
    // long after the talk -- by then the speaker may be somewhere else. Only speak
    // over them while they are still where the conversation happened; the line
    // itself is always in the live column's thread card.
    if (loc && r.loc && r.loc !== loc) return;
    this.bubbles.filter(b => b.actor === actor).forEach(b => this.removeBubble(b));
    // The speaker is named in the bubble, and a tail points at their own head: with
    // several people at one place the name tags stack upward, and a bubble that
    // followed the stacked tag could sit over someone else's head.
    const el = document.createElement('div'); el.className = 't3-bubble'; el.style.setProperty('--rc', `var(--r-${actor})`);
    const who = document.createElement('b'); who.className = 'who'; who.textContent = agentName(actor);
    el.append(who, document.createTextNode(text));
    const tail = document.createElement('i'); tail.className = 't3-tail'; tail.style.setProperty('--rc', `var(--r-${actor})`);
    this.ov.append(el, tail);
    const b = {el, tail, actor, loc, until: performance.now() + Math.min(9000, 3200 + text.length * 120)};
    this.bubbles.push(b);
    const here = this.bubbles.filter(x => x.loc === loc); while (here.length > 3) this.removeBubble(here.shift());
  }
  removeBubble(b) { if (b.gone) return; b.gone = true; b.el.remove(); if (b.tail) b.tail.remove(); const i = this.bubbles.indexOf(b); if (i >= 0) this.bubbles.splice(i, 1); }
  clearBubbles() { this.bubbles.slice().forEach(b => this.removeBubble(b)); }
  setFocus(ids) {
    this.focus = ids && ids.length ? new Set(ids) : null;
    for (const r of Object.values(this.res)) r.tag.classList.toggle('focus', !!this.focus && this.focus.has(r.id));
    if (this.focus && this.sc) {   // glide the camera to them
      const pts = [...this.focus].map(id => this.res[id]).filter(r => r && r.visible).map(r => new THREE.Vector3(r.tx, 0, r.tz));
      this.sc.focusOn(pts);
    }
  }
  // a major beat: a gold-ink marker over each person involved + one soft light pulse
  mark(ids, label) {
    if (!this.sc) return;
    ids = ids.filter(id => this.res[id] && this.res[id].visible); if (!ids.length) return;
    for (const id of ids) {
      const el = document.createElement('div'); el.className = 't3-mark'; el.innerHTML = `<span>✦</span>${label ? `<small>${esc(label)}</small>` : ''}`;
      this.ov.appendChild(el); const m = {el, id, until: performance.now() + 12000}; this.marks.push(m);
    }
    this.sc.pulse(ids);
  }
  track() {
    const sc = this.sc, v = new THREE.Vector3(), now = performance.now(), W = this.el.clientWidth;
    // name tags: over each head; tags that would overlap are stacked upward (several at one place spread out)
    const tags = [];
    for (const r of Object.values(this.res)) {
      if (!r.visible) continue;
      const p = sc.project(sc.headWorld(r.f, v)); r.screen = p;
      tags.push({r, x: p.x, y: p.y, w: r.tag.offsetWidth || 50, h: r.tag.offsetHeight || 20, vis: p.visible});
    }
    tags.sort((a, b) => b.y - a.y);
    const done = [];
    for (const t of tags) {
      for (let k = 0; k < 6; k++) { const hit = done.find(q => Math.abs(q.x - t.x) < (q.w + t.w) / 2 + 2 && Math.abs(q.y - t.y) < t.h); if (!hit) break; t.y = hit.y - t.h - 2; }
      done.push(t); t.r.tagY = t.y;
      t.r.tag.style.transform = `translate(${t.x}px,${t.y}px) translate(-50%,-100%)`; t.r.tag.style.opacity = t.vis ? 1 : 0;
    }
    for (const [id, pl] of Object.entries(this.places)) {
      const p = sc.project(pl.pos); pl.el.style.transform = `translate(${p.x}px,${p.y}px) translate(-50%,-100%)`; pl.el.style.opacity = p.visible ? 1 : 0;
    }
    // bubbles: above their speaker; overlapping ones are nudged upward
    const placed = [];
    for (const b of this.bubbles.slice()) {
      if (now > b.until) { this.removeBubble(b); continue; }
      const r = this.res[b.actor]; if (!r || !r.visible || !r.screen) { b.el.style.opacity = 0; if (b.tail) b.tail.style.opacity = 0; continue; }
      // anchored to the speaker's own head (not their possibly stacked name tag)
      const tagH = r.tag.offsetHeight || 20;
      const w = b.el.offsetWidth, h = b.el.offsetHeight; let x = r.screen.x, y = r.screen.y - tagH - 22 - h / 2;
      for (let k = 0; k < 4; k++) { const hit = placed.find(q => Math.abs(q.x - x) < (q.w + w) / 2 && Math.abs(q.y - y) < (q.h + h) / 2); if (!hit) break; y = hit.y - (hit.h + h) / 2 - 6; }
      x = Math.max(w / 2 + 6, Math.min(W - w / 2 - 6, x)); placed.push({x, y, w, h});
      b.el.style.transform = `translate(${x}px,${y}px) translate(-50%,-50%)`; b.el.style.opacity = r.screen.visible ? 1 : 0;
      if (b.tail) {   // from the bubble's bottom edge down to the speaker's head
        const x0 = x, y0 = y + h / 2, x1 = r.screen.x, y1 = r.screen.y - 4;
        const len = Math.max(0, Math.hypot(x1 - x0, y1 - y0)), ang = Math.atan2(y1 - y0, x1 - x0);
        b.tail.style.width = `${len}px`;
        b.tail.style.transform = `translate(${x0}px,${y0}px) rotate(${ang}rad)`;
        b.tail.style.opacity = r.screen.visible ? 1 : 0;
      }
    }
    for (const m of this.marks.slice()) {
      const r = this.res[m.id];
      if (now > m.until || !r || !r.visible) { m.el.remove(); this.marks.splice(this.marks.indexOf(m), 1); continue; }
      m.el.style.transform = `translate(${r.screen.x}px,${(r.tagY ?? r.screen.y) - 22}px) translate(-50%,-100%)`;
    }
    // a thin light line between two people in conversation
    let lines = '';
    for (const [a, t] of Object.entries(this.talk)) {
      if (now - t.t > 12000) continue;
      const ra = this.res[a], rb = this.res[t.to]; if (!ra || !rb || !ra.visible || !rb.visible || !ra.screen || !rb.screen || ra.path.length || rb.path.length) continue;
      const x1 = ra.screen.x, y1 = ra.screen.y + 6, x2 = rb.screen.x, y2 = rb.screen.y + 6, lift = 18 + Math.abs(x2 - x1) * 0.25;
      lines += `<path d="M${x1.toFixed(1)} ${y1.toFixed(1)} Q${((x1 + x2) / 2).toFixed(1)} ${(Math.min(y1, y2) - lift).toFixed(1)} ${x2.toFixed(1)} ${y2.toFixed(1)}" style="--rc:var(--r-${a})"/>`;
    }
    if (lines !== this.lastLines) { this.svg.innerHTML = lines; this.lastLines = lines; }
  }

  setActive(v) { this.active = v; if (this.sc) this.sc.setActive(v); if (this.ov) this.ov.hidden = !v; }
  setLowFx(v) { if (v && this.sc) this.sc.degrade(); }
  destroy() { this.alive = false; if (this.sc) this.sc.destroy(); if (this.ov) this.ov.remove(); if (this.cutBtn) this.cutBtn.remove(); }
}
function turn(a, b, k) { let d = b - a; while (d > Math.PI) d -= 2 * Math.PI; while (d < -Math.PI) d += 2 * Math.PI; return a + d * k; }
function groundLift(sc, x, z) {
  const p = sc.S.buildings.find(b => b.kind === 'park'); if (!p || !p.mound) return 0;
  const M = p.mound, r = Math.hypot(x - p.pos[0] - M.at[0], z - p.pos[1] - M.at[1]) / M.r; return r >= 1 ? 0 : M.h * (1 - r * r) * (1 - r * r);
}
