// Shared live state + the auto-reconnecting socket + small fetch helpers.
import {reactive, markRaw} from 'vue';
import {setPlaces, dayOf} from './names.js';

export const store = reactive({
  connected: false, ready: false,
  minute: 0, paused: false, pausePending: false, speed: 1, night: false, llmBusy: false,
  locations: [], agents: [], landmarks: [], effects: [],
  today: [],          // today's major/notable beats (seeded from /api/day, then live)
  todayDay: 0,
  digestsVersion: 0,  // bumped when a day closes -> the chronicle refetches
  usage: null,
  // navigation
  view: 'chronicle', inspectId: null,
  chronFocus: null,   // {day, minute} -> chronicle scrolls to and expands
  replay: null,       // {day, minute, runId}
  relPair: null,      // [a, b] preselected in the relationships view
  unseenMajor: 0,
});

// Non-reactive tick listeners (the live map: bubbles, event cards).
const tickFns = new Set();
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
  for (const fn of tickFns) { try { fn(evs, msg); } catch (err) { console.error(err); } }
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

async function pollUsage() { try { store.usage = markRaw(await getJSON('/api/usage')); } catch (e) {} }
setInterval(pollUsage, 5000); pollUsage();

// Navigation helpers used across views.
export const nav = {
  go(view) { store.view = view; if (view === 'village') store.unseenMajor = 0; },
  inspect(id) { store.inspectId = id; },
  chronicle(day, minute) { store.chronFocus = {day, minute: minute ?? null, t: Date.now()}; nav.go('chronicle'); },
  replay(day, minute, runId) { store.replay = {day, minute: minute ?? null, runId: runId || '', t: Date.now()}; nav.go('replay'); },
  pair(a, b) { store.relPair = [a, b]; nav.go('relations'); },
};

// Cached /api/day/{n} (closed days never change; today is always refetched).
const dayCache = new Map();
export async function fetchDay(day, runId = '') {
  const key = `${runId}|${day}`;
  if (day < store.todayDay && dayCache.has(key)) return dayCache.get(key);
  const d = await getJSON(`/api/day/${day}` + (runId ? `?run_id=${encodeURIComponent(runId)}` : ''));
  if (day < store.todayDay) dayCache.set(key, d);
  return d;
}
