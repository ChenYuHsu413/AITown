// The pixel village: ported from the old single-file UI (sprites, buildings, terrain,
// standing slots, walking/sleeping/talking states, name tags, landmark, weather,
// day/night, speech bubbles). Imperative SVG; one VillageMap per <svg>. Sprite
// frames live once in a global <defs> (initSprites) so every map can <use> them.
import {agentName, locName, KINDS} from './names.js';

const SVGNS = 'http://www.w3.org/2000/svg';
export const VB_W = 800, VB_H = 520;

/* ---- pixel-art villager sprites: 12x16 logical px @2px, frame-based ---- */
export const SPR_PAL = {
  jiji:     {o:'#3a2e33',h:'#4a3524',k:'#f1c7a2',e:'#3a2e33',c:'#E4674F',d:'#b23f2c'},
  ange:     {o:'#38292a',h:'#5a3a26',k:'#e9be97',e:'#38292a',c:'#D9803A',d:'#a85e22'},
  oula:     {o:'#26262e',h:'#2b2630',k:'#d8a878',e:'#26262e',c:'#2F9E8F',d:'#1f6e63'},
  lengyue:  {o:'#33262f',h:'#3a2a3a',k:'#f1c7a2',e:'#33262f',c:'#8E6FB3',d:'#64478c'},
  azong:    {o:'#332b33',h:'#6b4128',k:'#ecd6c0',e:'#332b33',c:'#D4A63C',d:'#a87f22'},
  xixi:     {o:'#2b2c33',h:'#6a4a30',k:'#f1c7a2',e:'#2b2c33',c:'#6E7BC4',d:'#48539a'},
  aisi:     {o:'#3a2b36',h:'#d9b24f',k:'#f1c7a2',e:'#3a2b36',c:'#D46BA0',d:'#a8407a'},
  xue:      {o:'#2b2630',h:'#2e2a33',k:'#e9be97',e:'#2b2630',c:'#4E86B0',d:'#305f82'},
  long:     {o:'#2e2b2c',h:'#8a8580',k:'#e6c9a8',e:'#2e2b2c',c:'#5B9E6B',d:'#3f7049'},
  kuaizheng:{o:'#312e30',h:'#c8c2ba',k:'#ecd6c0',e:'#312e30',c:'#A98B5E',d:'#7d6540'},
};
const FALLBACK_ID = 'jiji';
export const spriteId = id => SPR_PAL[id] ? id : FALLBACK_ID;
const F1 = [
  "....oooo....","..oohhhhoo..",".ohhhhhhhho.",".ohhkkkkhho.",
  ".ohkkkkkkho.",".ohkekkekho.",".ohkkkkkkho.","..okkkkko...",
  ".occcccccco.",".occcddccco.",".okccccccko.",".occcccccco.",
  ".occcddccco.",".occcccccco.","..oddooddo..","...oo..oo..."];
const F2 = F1.slice(); F2[15] = "..oo....oo..";
const FS = F1.slice(); FS[5] = ".oheekkeeho.";
function rectsFromGrid(rows, pal) {
  let s = '';
  rows.forEach((row, y) => { let x = 0;
    while (x < row.length) { const ch = row[x];
      if (ch === '.') { x++; continue; }
      let run = 1; while (x + run < row.length && row[x + run] === ch) run++;
      s += `<rect x="${x}" y="${y}" width="${run}" height="1" fill="${pal[ch] || pal.o}"/>`;
      x += run;
    }});
  return s;
}
export function initSprites(defs) {
  let html = '';
  for (const id in SPR_PAL) { const pal = SPR_PAL[id];
    const frame = (suf, grid, ty) => `<g id="spr-${id}-${suf}" shape-rendering="crispEdges"><g transform="translate(-12,${ty}) scale(2)">${rectsFromGrid(grid, pal)}</g></g>`;
    html += frame('f1', F1, -5) + frame('f2', F2, -3) + frame('fs', FS, -5);
  }
  defs.innerHTML = html;
}

/* ---- pixel-art buildings & terrain (same 2px grid) ---- */
const P = (x, y, w, h, f, op) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${f}"${op != null ? ` fill-opacity="${op}"` : ''}/>`;
const ROOF = {home_a:'#C77A57', home_b:'#7E97C6'};
const ROOF_D = {home_a:'#9c5838', home_b:'#556aa0'};
function homeSprite(l) {
  const R = ROOF[l.id] || '#C0714C', Rd = ROOF_D[l.id] || '#9c5838', w = 22, h = 20; let s = '';
  s += P(15,0,2,6,'#8a6a52');
  for (let r = 0; r < 8; r++) { const hw = r + 1; s += P(11-hw,r,2*hw+1,1,R); }
  s += P(2,8,18,1,Rd);
  s += P(3,9,16,11,'#ECDCC2') + P(17,9,2,11,'#d8c39f');
  s += P(9,14,4,6,'#9C6B3F') + P(9,14,1,6,'#744e2c') + P(12,14,1,6,'#744e2c') + P(11,17,1,1,'#5e3d22');
  s += P(5,10,5,5,'#CFE3EA') + P(7,10,1,5,'#8aa0a8') + P(5,12,5,1,'#8aa0a8');
  s += P(3,20,16,1,'#000',.14);
  return {rects:s, light:P(5,10,5,5,'#FFCF6E'), w, h};
}
function cafeSprite() {
  const w = 26, h = 18; let s = '';
  s += P(2,6,22,12,'#EFE4D2') + P(22,6,2,12,'#dccbad');
  for (let c = 1; c < 25; c += 2) { s += P(c,3,2,3,(c>>1)%2?'#F0E2CE':'#D98C5F'); }
  s += P(1,6,24,1,'#c9b18a');
  s += P(4,9,9,7,'#BFD8DF') + P(4,9,9,1,'#8fb0b8') + P(8,9,1,7,'#8fb0b8');
  s += P(15,10,5,8,'#9C6B3F') + P(15,10,1,8,'#744e2c');
  s += P(17,6,5,3,'#f4efe6') + P(21,6,1,2,'#cbb79a') + P(18,5,3,1,'#b58a63') + P(17,9,5,1,'#cbb79a');
  s += P(2,18,22,1,'#000',.14);
  return {rects:s, light:P(4,9,9,7,'#FFCF6E'), w, h};
}
function officeSprite() {
  const w = 22, h = 26; let s = '', lite = '';
  s += P(2,4,18,22,'#DEE3EA') + P(18,4,2,22,'#c4ccd6');
  s += P(1,3,20,2,'#c8ced8');
  s += P(10,0,1,4,'#7a828a') + P(9,0,3,1,'#7a828a');
  for (let r = 0; r < 3; r++) for (let c = 0; c < 2; c++) { const x = 5 + c*8, y = 7 + r*6;
    s += P(x,y,5,4,'#9DB6C4') + P(x+2,y,1,4,'#6d8898') + P(x,y+2,5,1,'#6d8898');
    lite += P(x,y,5,4,'#FFCF6E'); }
  s += P(8,20,6,6,'#8b98a6');
  s += P(2,26,18,1,'#000',.14);
  return {rects:s, light:lite, w, h};
}
function pxTree(x, base, big) {
  const R = big ? 6 : 5, trunkH = big ? 7 : 5, cy = base - trunkH - 2;
  return P(x-1,base-trunkH,2,trunkH,'#8a6239') + P(x-R+1,cy,2*R-1,3,'#5f8f52')
    + P(x-R+2,cy-2,2*R-3,2,'#7BB06A') + P(x-R+2,cy+3,2*R-3,2,'#4b7742') + P(x-1,cy-3,3,1,'#7BB06A');
}
function parkSprite() {
  const w = 34, h = 20; let s = '';
  s += pxTree(6,14,true) + pxTree(12,11,false) + pxTree(2,18,false);
  s += P(20,13,14,2,'#5f93aa') + P(18,15,18,3,'#7FB0C4') + P(20,18,14,1,'#5f93aa')
    + P(23,16,3,1,'#B7DCE8') + P(29,15,2,1,'#B7DCE8');
  s += P(14,16,7,1,'#8a6239') + P(14,14,7,1,'#8a6239') + P(14,15,1,3,'#6a4a2a') + P(20,15,1,3,'#6a4a2a');
  s += P(2,20,30,1,'#000',.12);
  return {rects:s, light:'', w, h};
}
function bakerySprite() {
  const w = 24, h = 20; let s = '';
  for (let r = 0; r < 5; r++) { s += P(1+r,r,w-2-r,2,r<2?'#CE9552':'#B87A3C'); }
  s += P(2,8,20,12,'#F0E2CC') + P(20,8,2,12,'#dccbad');
  s += P(4,11,9,6,'#EAD9B8') + P(4,11,9,1,'#c9b088') + P(4,16,9,1,'#c9b088') + P(8,11,1,6,'#c9b088');
  s += P(5,13,3,2,'#C8873C') + P(9,13,3,2,'#B5732E');
  s += P(15,12,5,8,'#9C6B3F') + P(15,12,1,8,'#744e2c');
  s += P(16,6,4,4,'#D9A65A') + P(17,6,2,1,'#e8be7e') + P(16,7,1,2,'#e8be7e') + P(17,9,2,1,'#b5843c');
  s += P(2,20,20,1,'#000',.14);
  return {rects:s, light:P(4,11,9,6,'#FFCF6E'), w, h};
}
function marketSprite() {
  const w = 30, h = 18; let s = '';
  for (let c = 0; c < 28; c += 2) { s += P(1+c,2,2,3,(c>>1)%2?'#E38C6B':'#F0E2CE'); }
  s += P(0,5,30,1,'#b06a4c');
  s += P(2,5,2,12,'#8a6239') + P(26,5,2,12,'#8a6239') + P(14,5,2,12,'#7a5535');
  s += P(4,12,9,5,'#b98a5a') + P(4,12,9,1,'#cfa877');
  s += P(17,12,9,5,'#b98a5a') + P(17,12,9,1,'#cfa877');
  s += P(5,10,2,2,'#C0503A') + P(8,10,2,2,'#E0A63C') + P(11,10,1,2,'#5B9E6B');
  s += P(18,10,2,2,'#8E6FB3') + P(21,10,2,2,'#C0503A') + P(24,10,1,2,'#E0A63C');
  s += P(2,17,26,1,'#000',.14);
  return {rects:s, light:'', w, h};
}
function buildingSVG(l) {
  return l.kind === 'home' ? homeSprite(l) : l.kind === 'cafe' ? cafeSprite(l) : l.kind === 'office' ? officeSprite(l)
    : l.kind === 'bakery' ? bakerySprite(l) : l.kind === 'market' ? marketSprite(l) : parkSprite(l);
}
function terrainSVG() {
  let s = '', hi = '';
  const water = ['#4E86B0', '#6AA0C4', '#8FBBD6'];
  for (let y = 40, i = 0; y < 504; y += 8, i++) { const jag = (i % 2) ? 0 : 3, x = 14 + jag, ww = 40 - jag;
    s += P(x,y,ww,8,water[1]) + P(x,y,4,8,water[2]) + P(x+ww-6,y,6,8,water[0]); }
  [[22,90],[30,180],[24,270],[34,360],[26,450]].forEach(([x, y]) => { hi += P(x,y,2,2,'#DCEEF6') + P(x+6,y+18,2,2,'#DCEEF6'); });
  const G = '#5f8a4f', G2 = '#6E9A5C';
  [[250,120],[330,470],[470,300],[540,180],[300,320],[210,300],[700,255],[600,470]].forEach(([x, y]) => {
    s += P(x,y,1,3,G) + P(x+2,y-1,1,4,G2) + P(x+4,y,1,3,G) + P(x+1,y+1,1,2,G2); });
  return `<g shape-rendering="crispEdges">${s}</g><g class="river-hi" shape-rendering="crispEdges">${hi}</g>`;
}

/* ---- landmark: the light installation (4x4 lamp array lighting bottom-up) ---- */
const INST_HUES = ['#ffd867', '#5fd0e0', '#ff8fc0', '#8fe0a0'];
function installationPixels(p, done) {
  const cols = [1,4,7,10], rows = [13,9,5,1], N = cols.length * rows.length;
  const lit = done ? N : Math.round(N * Math.max(0, Math.min(1, p)));
  let s = P(0,14,12,2,'#3d4450');
  cols.forEach(x => { s += P(x,1,2,13,'#4a5160'); });
  let i = 0;
  for (let r = 0; r < rows.length; r++) for (let c = 0; c < cols.length; c++) {
    const on = i < lit, col = on ? INST_HUES[(r + c) % INST_HUES.length] : '#2f3540';
    s += P(cols[c], rows[r], 2, 2, col);
    if (on) s += P(cols[c], rows[r], 1, 1, '#ffffff');
    i++;
  }
  return s;
}

/* ---- standing slots: villagers stand beside their building ---- */
const SLOT = 34;
const slotOffset = (i, n) => (i - (Math.min(n, 6) - 1) / 2) * SLOT;
const FOOT = {home:22, cafe:26, office:22, park:34, bakery:24, market:30};
const clampX = x => Math.max(26, Math.min(774, x));
const clampY = y => Math.max(44, Math.min(486, y));
export function standXY(l, idx, n) {
  const half = FOOT[l.kind] || 26;
  const halfspread = (Math.min(n, 6) - 1) / 2 * SLOT;
  const off = Math.max(half + 18, 40 + halfspread);
  const side = l.x > 400 ? -1 : 1;
  return [clampX(l.x + side * off + slotOffset(idx, n)), clampY(l.y + 8)];
}
function nameW(id) { let w = 0; for (const c of agentName(id)) w += /[㄀-鿿豈-﫿]/.test(c) ? 13 : 6.2; return Math.ceil(w) + 14; }

/* ---- speech bubbles: CJK-aware wrap, one per speaker, collision-stacked ---- */
const BUB_GAP = 4, BUB_MAX_W = 220, BUB_MAX_PER_LOC = 3;
const BUB_PAD_X = 8, BUB_PAD_Y = 6, BUB_FONT = 11, BUB_LINE_H = 15, BUB_MAX_LINES = 3;
const BUB_LINE_W = BUB_MAX_W - BUB_PAD_X * 2;
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
const bubCharW = ch => ch.codePointAt(0) > 0x2E7F ? 11 : 6.2;
const bubWidth = s => { let w = 0; for (const ch of s) w += bubCharW(ch); return w; };
const RE_LATIN = /[A-Za-z0-9''\-]/;
function bubTokens(s) {
  const toks = []; let cur = '';
  for (const ch of s) { if (RE_LATIN.test(ch)) cur += ch; else { if (cur) { toks.push(cur); cur = ''; } toks.push(ch); } }
  if (cur) toks.push(cur);
  return toks;
}
function bubLines(s) {
  const lines = []; let line = '', w = 0;
  for (const tok of bubTokens(s)) {
    if (tok === ' ' && line === '') continue;
    let tw = bubWidth(tok);
    if (w + tw <= BUB_LINE_W) { line += tok; w += tw; continue; }
    if (line !== '') { lines.push(line); line = ''; w = 0; if (tok === ' ') continue; tw = bubWidth(tok); }
    if (tw <= BUB_LINE_W) { line = tok; w = tw; }
    else for (const ch of tok) { const cw = bubCharW(ch); if (w + cw > BUB_LINE_W) { lines.push(line); line = ''; w = 0; } line += ch; w += cw; }
  }
  if (line !== '') lines.push(line);
  if (lines.length <= BUB_MAX_LINES) return lines.length ? lines : [''];
  const kept = lines.slice(0, BUB_MAX_LINES);
  let last = kept[BUB_MAX_LINES - 1];
  while (last && bubWidth(last + '…') > BUB_LINE_W) last = Array.from(last).slice(0, -1).join('');
  kept[BUB_MAX_LINES - 1] = last + '…';
  return kept;
}
const bubHit = (a, b) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;

const DOW_OF = m => ((Math.floor(m / 1440) % 7) + 7) % 7;

export class VillageMap {
  constructor(svg, {onAgentClick} = {}) {
    this.svg = svg; this.onAgentClick = onAgentClick;
    this.locs = {}; this.locList = []; this.agents = {}; this.lastPos = {}; this.bubbles = [];
    this.focus = null;
    svg.setAttribute('viewBox', `0 0 ${VB_W} ${VB_H}`);
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    svg.innerHTML = `<rect width="800" height="520" fill="var(--paper)"/>
      <g class="m-terrain">${terrainSVG()}</g><g class="m-roads"></g><g class="m-locs"></g>
      <g class="m-landmarks"></g><g class="m-closed"></g><g class="m-broken"></g><g class="m-agents"></g>
      <g class="m-festival" pointer-events="none"></g>
      <g class="m-rain" opacity="0" pointer-events="none"><rect width="800" height="520" fill="#4a6a90" opacity="0.12"/><g class="m-drops raindrops"></g></g>
      <rect class="m-night" width="800" height="520" fill="var(--night)" opacity="0" pointer-events="none"/>
      <g class="m-lights" opacity="0" pointer-events="none"></g>
      <g class="m-bubbles"></g>`;
    this.g = n => svg.querySelector('.m-' + n);
  }

  buildMap(locations) {
    this.locs = {}; this.locList = locations;
    const g = this.g('locs'), roads = this.g('roads'), lights = this.g('lights');
    g.innerHTML = ''; lights.innerHTML = '';
    locations.forEach(l => { this.locs[l.id] = l; });
    const hub = this.locs.cafe || locations[0]; let rs = '';
    locations.forEach(l => { if (l.id === hub.id) return;
      const x0 = l.x, y0 = l.y + 14, x1 = hub.x, y1 = hub.y + 14, cx = (x0 + x1) / 2 + 18, cy = (y0 + y1) / 2 + 6;
      for (let tt = 0.06; tt < 0.95; tt += 0.07) { const it = 1 - tt;
        const px = it*it*x0 + 2*it*tt*cx + tt*tt*x1, py = it*it*y0 + 2*it*tt*cy + tt*tt*y1;
        rs += P(Math.round(px)-2, Math.round(py)-2, 4, 4, (Math.round(tt*20)) % 2 ? '#a98d5f' : '#c3a878'); }
    });
    roads.innerHTML = `<g shape-rendering="crispEdges">${rs}</g>`;
    locations.forEach(l => {
      const b = buildingSVG(l);
      const tf = `translate(${l.x - b.w},${l.y + 14 - b.h * 2}) scale(2)`;
      const el = document.createElementNS(SVGNS, 'g');
      el.innerHTML = `<g shape-rendering="crispEdges" transform="${tf}">${b.rects}</g>`
        + `<text class="loc-label" x="${l.x}" y="${l.y + 27}">${esc(locName(l.id))}</text>`
        + `<text class="loc-kind" x="${l.x}" y="${l.y + 36}">${KINDS[l.kind] || l.kind}</text>`
        + (l.owner ? `<g class="shop-rev-g" transform="translate(${l.x},${l.y - 30})"><rect x="-33" y="-8" width="66" height="13" rx="3"/><text data-rev="${l.id}" class="shop-rev"></text></g>` : '');
      g.appendChild(el);
      if (b.light) { const lg = document.createElementNS(SVGNS, 'g'); lg.innerHTML = `<g shape-rendering="crispEdges" transform="${tf}">${b.light}</g>`; lights.appendChild(lg); }
    });
    this.updateBrokenSigns();
  }

  setShopRev(econ) {
    for (const l of (econ && econ.locations) || []) {
      const el = this.svg.querySelector(`[data-rev="${l.id}"]`);
      if (el) el.textContent = `今日 $${Math.round(l.revenue_today)}`;
    }
  }

  updateLandmarks(list) {
    this.g('landmarks').innerHTML = (list || []).map(lm => {
      const loc = this.locs[lm.location]; if (!loc) return '';
      const done = lm.state === 'completed', H = 16, sc = 2, bx = loc.x - 58, by = loc.y + 14 - H * sc;
      return `<g class="installation${done ? ' done' : ''}" shape-rendering="crispEdges" transform="translate(${bx},${by}) scale(${sc})">`
        + installationPixels(lm.progress, done) + `</g>`;
    }).join('');
  }

  // list: [{id, location, action}] -- live snapshot rows or a replay reconstruction.
  updateAgents(list) {
    const g = this.g('agents'), byLoc = {};
    list.forEach(a => { (byLoc[a.location] = byLoc[a.location] || []).push(a); });
    list.forEach(a => {
      const peers = byLoc[a.location], idx = peers.findIndex(p => p.id === a.id);
      const loc = this.locs[a.location] || {kind:'home', x:a.x || 400, y:a.y || 260};
      const [x, y] = standXY(loc, idx, peers.length);
      let el = this.agents[a.id];
      if (!el) {
        el = document.createElementNS(SVGNS, 'g');
        el.setAttribute('class', 'agent agent-g');
        el.style.transition = 'transform 1s ease';
        el.style.setProperty('--d', (Object.keys(this.agents).length * 0.2) + 's');
        const sid = spriteId(a.id), nw = nameW(a.id), nm = esc(agentName(a.id, a.name));
        el.innerHTML = `<ellipse class="shadow" cx="0" cy="27" rx="8" ry="2.5"/>
          <rect class="hit" x="-13" y="-8" width="26" height="40"/>
          <circle class="ring" r="11"/>
          <g class="body"><use class="fr fr1" href="#spr-${sid}-f1"/><use class="fr fr2" href="#spr-${sid}-f2"/><use class="fr fs" href="#spr-${sid}-fs"/></g>
          <text class="zzz" x="9" y="-9">z</text>
          <g class="nametag"><rect x="${-nw / 2}" y="-24" width="${nw}" height="11" rx="3"/><text y="-15.5">${nm}</text></g>`;
        el.addEventListener('click', () => this.onAgentClick && this.onAgentClick(a.id));
        g.appendChild(el); this.agents[a.id] = el;
      }
      const prev = this.lastPos[a.id];
      if (prev && a.action !== 'sleep' && (Math.abs(prev.x - x) > 0.5 || Math.abs(prev.y - y) > 0.5)) {
        el.classList.add('walking');
        clearTimeout(el._wt); el._wt = setTimeout(() => el.classList.remove('walking'), 1000);
      }
      this.lastPos[a.id] = {x, y};
      el.setAttribute('transform', `translate(${x},${y})`);
      el.classList.toggle('talking', a.action === 'talk');
      el.classList.toggle('sleeping', a.action === 'sleep');
      el.classList.toggle('focus', !!this.focus && this.focus.has(a.id));
      if (a.action === 'sleep') el.classList.remove('walking');
    });
  }

  setFocus(ids) {   // highlight a set of residents (replay: the people in the current beat)
    this.focus = ids && ids.length ? new Set(ids) : null;
    for (const id in this.agents) this.agents[id].classList.toggle('focus', !!this.focus && this.focus.has(id));
  }

  applyDaylight(minute) {
    const mod = ((minute % 1440) + 1440) % 1440;
    const dist = Math.min(Math.abs(mod - 780), 1440 - Math.abs(mod - 780));
    const nf = Math.min(.45, Math.max(0, (dist - 260) / 700));
    this.g('night').setAttribute('opacity', nf);
    this.g('lights').setAttribute('opacity', (nf / 0.45).toFixed(3));
    this.updateClosedSigns(minute);
  }

  updateClosedSigns(minute) {
    const dow = DOW_OF(minute); let s = '';
    this.locList.forEach(l => {
      if (l.owner && (l.closed_days || []).includes(dow))
        s += `<g transform="translate(${l.x},${l.y - 8})"><rect class="closed-badge" x="-9" y="-9" width="18" height="14" rx="3"/>`
          + `<text class="closed-text" x="0" y="1.5">休</text></g>`;
    });
    this.g('closed').innerHTML = s;
  }

  setBroken(locId, broken) { if (this.locs[locId]) { this.locs[locId].broken = broken; this.updateBrokenSigns(); } }
  updateBrokenSigns() {
    let s = '';
    this.locList.forEach(l => { if (this.locs[l.id] && this.locs[l.id].broken) s += `<text class="broken-badge" x="${l.x + 16}" y="${l.y - 14}">🛠️</text>`; });
    this.g('broken').innerHTML = s;
  }

  renderEffects(effects) {
    effects = effects || [];
    const rain = effects.some(e => e.type === 'rain'), fest = effects.find(e => e.type === 'festival');
    const drops = this.g('drops');
    if (rain && !drops.childElementCount) {
      let s = '';
      for (let i = 0; i < 70; i++) { const x = Math.round(Math.random() * 820), y = Math.round(Math.random() * 540);
        s += `<line x1="${x}" y1="${y}" x2="${x - 3}" y2="${y + 8}" stroke="#bcd3e6" stroke-width="1" stroke-opacity=".5"/>`; }
      drops.innerHTML = s;
    }
    this.g('rain').setAttribute('opacity', rain ? '1' : '0');
    const fg = this.g('festival'), l = fest && this.locs[fest.location];
    if (!l) { fg.innerHTML = ''; return; }
    const cols = ['#D9544D', '#E0A63C', '#5F7D5B', '#5E86B0'], y = l.y - 38, x0 = l.x - 42, n = 8, gap = 12;
    let s = `<line x1="${x0}" y1="${y}" x2="${x0 + n * gap}" y2="${y}" stroke="#8a6a52" stroke-width="1"/>`;
    for (let i = 0; i < n; i++) { const x = x0 + i * gap; s += `<path d="M${x} ${y} L${x + 8} ${y} L${x + 4} ${y + 9} Z" fill="${cols[i % cols.length]}"/>`; }
    fg.innerHTML = s;
  }

  // A speech bubble over the speaker (text already zh-resolved by the caller).
  bubble(actor, loc, raw) {
    const el = this.agents[actor]; if (!el || !raw || !raw.trim()) return;
    const tf = (el.getAttribute('transform') || '').match(/(-?[\d.]+),(-?[\d.]+)/); if (!tf) return;
    const ax = +tf[1], ay = +tf[2];
    const lines = bubLines(raw);
    const w = Math.min(BUB_MAX_W, Math.ceil(Math.max(...lines.map(bubWidth))) + BUB_PAD_X * 2);
    const h = BUB_PAD_Y * 2 + lines.length * BUB_LINE_H;
    this.bubbles.filter(b => b.speaker === actor).forEach(b => this.removeBubble(b));
    let bx = ax - w / 2, by = ay - 34 - h, guard = 0;
    while (guard++ < 6 && this.bubbles.some(b => bubHit({x:bx, y:by, w, h}, b.box))) by -= (h + BUB_GAP);
    bx = Math.max(2, Math.min(bx, VB_W - w - 2));
    if (by < 2) {
      by = ay + 22; let g2 = 0;
      while (g2++ < 6 && this.bubbles.some(b => bubHit({x:bx, y:by, w, h}, b.box))) by += (h + BUB_GAP);
      by = Math.min(by, VB_H - h - 2);
    }
    const tspans = lines.map((ln, i) => `<tspan x="${bx + BUB_PAD_X}"${i ? ` dy="${BUB_LINE_H}"` : ''}>${esc(ln)}</tspan>`).join('');
    const g = document.createElementNS(SVGNS, 'g'); g.setAttribute('class', 'bubble');
    g.innerHTML = `<rect x="${bx}" y="${by}" width="${w}" height="${h}" rx="7"/><text x="${bx + BUB_PAD_X}" y="${by + BUB_PAD_Y + BUB_FONT}">${tspans}</text>`;
    this.g('bubbles').appendChild(g);
    const rec = {el:g, speaker:actor, loc, box:{x:bx, y:by, w, h}, timer:null, gone:false};
    rec.timer = setTimeout(() => this.removeBubble(rec), Math.min(9000, 3000 + Math.ceil(raw.length / 10) * 500));
    this.bubbles.push(rec);
    const here = this.bubbles.filter(b => b.loc === loc);
    while (here.length > BUB_MAX_PER_LOC) this.removeBubble(here.shift());
  }
  removeBubble(rec) {
    if (rec.gone) return; rec.gone = true; clearTimeout(rec.timer); rec.el.remove();
    const i = this.bubbles.indexOf(rec); if (i >= 0) this.bubbles.splice(i, 1);
  }
  clearBubbles() { this.bubbles.slice().forEach(b => this.removeBubble(b)); }
  destroy() { this.clearBubbles(); for (const id in this.agents) clearTimeout(this.agents[id]._wt); }
}
