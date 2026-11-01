// Shared live state + the auto-reconnecting socket + small fetch helpers.
import {reactive, markRaw} from 'vue';
import {setPlaces, dayOf} from './names.js';

function readMode() { try { return localStorage.getItem('aitown.detail') === 'full' ? 'full' : 'lean'; } catch (e) { return 'lean'; } }
export function setDetail(m) { store.detail = m; try { localStorage.setItem('aitown.detail', m); } catch (e) { /* private mode */ } }

export const store = reactive({
  connected: false, ready: false,
  minute: 0, paused: false, pausePending: false, speed: 1, userSpeed: 1, night: false, llmBusy: false,
  locations: [], agents: [], landmarks: [], effects: [],
  today: [],          // today's major/notable beats (seeded from /api/day, then live)
  todayDay: 0,
  digestsVersion: 0,  // bumped when a day closes -> the chronicle refetches
  usage: null,
  // navigation
  view: 'chronicle', inspectId: null, inspectAt: null,   // inspectAt: where the card opens (client px, next to the resident)
  chronFocus: null,   // {day, minute} -> chronicle scrolls to and expands
  replay: null,       // {day, minute, runId}
  relPair: null,      // [a, b] preselected in the relationships view
  unseenMajor: 0,
  heartbeat: 0,
  detail: readMode(),  // 'lean' (精簡) | 'full' (詳細): live column + chronicle, remembered per viewer       // bumped (at most every 2.5 s) when a major beat arrives -> one gold pulse
});

// Non-reactive event listeners (the live map + column): fn(events, msg, isSnapshot).
const tickFns = new Set();
let lastBeat = 0;
export const onTick = fn => { tickFns.add(fn); return () => tickFns.delete(fn); };

export async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

const beatKey = e => `${e.minute}|${e.verb}|${e.actor}|${e.target || ''}`;
function pushToday(evs) {
  const seen = new Set(store.today.map(beatKey));
  for (const e of evs) {
    if ((e.tier === 'major' || e.tier === 'notable') && dayOf(e.minute) === store.todayDay && !seen.has(beatKey(e))) {
      store.today.push(markRaw(e)); seen.add(beatKey(e));
    }
  }
}
async function loadToday(day) {
  store.todayDay = day; store.today = [];
  try { const d = await getJSON(`/api/day/${day}`); pushToday(d.beats || []); } catch (e) { /* no DB: live beats only */ }
}

function applyFrame(msg) {
  store.minute = msg.minute; store.paused = msg.paused; store.speed = msg.speed; store.night = !!msg.night;
  if (msg.user_speed != null || msg.speed != null) store.userSpeed = msg.user_speed ?? msg.speed;   // the chosen tier survives a night cruise
  if (msg.paused) store.pausePending = false;
  if ('llm_busy' in msg) store.llmBusy = !!(msg.llm_busy && (msg.busy_ms || 0) >= 2000);
  if (msg.type === 'status') return;
  if (msg.agents) store.agents = markRaw(msg.agents);
  if (msg.landmarks) store.landmarks = markRaw(msg.landmarks);
  if (msg.effects) store.effects = markRaw(msg.effects);
}

let ws = null, retry = 0, timer = null;
const BACKOFF = [1000, 2000, 5000];
function handle(e) {
  const msg = JSON.parse(e.data);
  if (msg.type === 'snapshot') {
    setPlaces(msg.locations, msg.landmarks);
    store.locations = markRaw(msg.locations);
    applyFrame(msg);
    const day = dayOf(msg.minute);
    if (day !== store.todayDay) loadToday(day).then(() => pushToday(msg.events || []));
    else pushToday(msg.events || []);
    store.ready = true;
    for (const fn of tickFns) { try { fn(msg.events || [], msg, true); } catch (err) { console.error(err); } }
    return;
  }
  const before = store.todayDay;
  applyFrame(msg);
  if (msg.type !== 'tick') return;
  const day = dayOf(msg.minute);
  if (day !== before) { store.digestsVersion++; loadToday(day); }   // a day closed: its digest is written
  const evs = msg.events || [];
  for (const ev of evs) {
    if (ev.verb === 'breakdown' || ev.verb === 'repaired') {
      const l = store.locations.find(x => x.id === ev.location); if (l) l.broken = ev.verb === 'breakdown';
    }
    if (ev.tier === 'major' && store.view !== 'village') store.unseenMajor++;
  }
  pushToday(evs);
  if (evs.some(e => e.tier === 'major') && performance.now() - lastBeat > 2500) { lastBeat = performance.now(); store.heartbeat++; }
  emit(evs.filter(e => e.verb !== 'say'), msg);
  for (const e of evs) if (e.verb === 'say') paceSay(e, msg);
}

// ONE event source for everything that shows the live town (map bubbles + live column):
// the same events, in the same order, at the same moment. A conversation's lines are
// generated in the background and arrive in one burst; they are released here one at a
// time at a speaking pace (per pair, so two conversations still overlap naturally).
function emit(evs, msg) {
  if (!evs.length) return;
  for (const fn of tickFns) { try { fn(evs, msg, false); } catch (err) { console.error(err); } }
}
const pacing = new Map();   // pair -> {queue, busy}
function paceSay(ev, msg) {
  const pair = [ev.actor, ev.target].sort().join('|');
  const p = pacing.get(pair) || {queue: [], busy: false}; pacing.set(pair, p);
  p.queue.push([ev, msg]);
  if (!p.busy) release(pair);
}
function release(pair) {
  const p = pacing.get(pair);
  if (!p || !p.queue.length) { if (p) p.busy = false; pacing.delete(pair); return; }
  p.busy = true;
  const [ev, msg] = p.queue.shift();
  emit([ev], msg);
  const len = (ev.speech || '').length;
  const wait = document.hidden || p.queue.length > 10 ? 250 : Math.max(1100, Math.min(3200, 700 + len * 55));
  setTimeout(() => release(pair), wait);
}
export function connect() {
  clearTimeout(timer);
  const sock = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
  ws = sock;
  sock.onopen = () => { if (ws === sock) { retry = 0; store.connected = true; } };
  sock.onmessage = e => { if (ws === sock) handle(e); };
  sock.onclose = () => { if (ws !== sock) return; store.connected = false;
    const wait = retry < BACKOFF.length ? BACKOFF[retry] : 10000; retry++;
    timer = setTimeout(connect, wait); };
  sock.onerror = () => { try { sock.close(); } catch (_) {} };
}
setInterval(() => { if (ws && ws.readyState === 1) ws.send('ping'); }, 15000);

export function togglePause() {
  const cmd = store.paused ? 'play' : 'pause';
  if (cmd === 'pause') store.pausePending = true;
  fetch('/api/control', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({cmd})})
    .then(r => r.json()).then(d => { store.paused = d.paused; if (d.paused) store.pausePending = false; }).catch(() => {});
}

// Live speed tiers (sim speed multipliers). One list: add 60 here and it just appears.
export const SPEED_TIERS = [1, 5, 20];
export function setSpeed(n) {
  store.userSpeed = n;
  fetch('/api/control', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({cmd:'speed', speed:n})})
    .then(r => r.json()).then(d => { store.paused = d.paused; }).catch(() => {});
}

async function pollUsage() { try { store.usage = markRaw(await getJSON('/api/usage')); } catch (e) {} }
setInterval(pollUsage, 5000); pollUsage();

// Navigation helpers used across views.
export const nav = {
  go(view) { store.view = view; if (view === 'village') store.unseenMajor = 0; },
  inspect(id, at = null) { store.inspectAt = at; store.inspectId = id; },
  chronicle(day, minute) { store.chronFocus = {day, minute: minute ?? null, t: Date.now()}; nav.go('chronicle'); },
  replay(day, minute, runId) { store.replay = {day, minute: minute ?? null, runId: runId || '', t: Date.now()}; nav.go('replay'); },
  pair(a, b) { store.relPair = [a, b]; nav.go('relations'); },
};

// Cached /api/day/{n} (closed days never change; today is always refetched). The
// promise itself is cached, so a page full of nodes asking for one day fetches it once.
const dayCache = new Map();
export function fetchDay(day, runId = '') {
  const key = `${runId}|${day}`;
  if (day < store.todayDay && dayCache.has(key)) return dayCache.get(key);
  const p = getJSON(`/api/day/${day}` + (runId ? `?run_id=${encodeURIComponent(runId)}` : ''));
  if (day < store.todayDay) {
    dayCache.set(key, p); p.catch(() => dayCache.delete(key));
    if (dayCache.size > 60) dayCache.delete(dayCache.keys().next().value);   // bounded: oldest-fetched day goes
  }
  return p;
}
