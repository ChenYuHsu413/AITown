// Conversation threads: one conversation = one card. The engine stamps a conversation's
// talk_start (and its confide / confronted beats) at the initiation minute `init`, and
// each `say` line at `init + i` (engine._dialogue_task settlement). So a thread's key is
// (unordered pair, init); a say joins the latest thread of its pair whose init <= its
// minute. Exact, given the engine's stamping; no time-window heuristics.
import {reactive} from 'vue';
import {agentName, locName, hhmm, dialogueSubst, evText} from './names.js';
import {Avatar} from './ui.js';

export const pairOf = ev => [ev.actor, ev.target].sort().join('|');
const ATTACH = new Set(['confide', 'confronted']);

export class ThreadIndex {
  constructor() { this.byPair = new Map(); }   // pair -> threads (ascending init)
  // Returns {thread, created} for a conversation event, or null for anything else.
  add(ev) {
    if (!ev.actor || !ev.target) return null;
    const pair = pairOf(ev), list = this.byPair.get(pair) || [];
    if (ev.verb === 'talk_start') {
      let t = list.find(x => x.init === ev.minute);
      if (t) { t.first = t.first || ev.tier === 'notable'; t.loc = t.loc || ev.location; return {thread: t, created: false}; }
      t = this.make(ev, pair); t.first = ev.tier === 'notable'; list.push(t); list.sort((x, y) => x.init - y.init); this.byPair.set(pair, list);
      return {thread: t, created: true};
    }
    if (ev.verb === 'say' || ATTACH.has(ev.verb)) {
      let t = null;
      for (const x of list) if (x.init <= ev.minute) t = x;   // latest conversation of this pair begun at or before
      let created = false;
      if (!t) { t = this.make(ev, pair); list.push(t); list.sort((x, y) => x.init - y.init); this.byPair.set(pair, list); created = true; }
      if (ev.verb === 'say') t.lines.push({actor: ev.actor, text: dialogueSubst(ev.speech), minute: ev.minute});
      else t.beats.push({verb: ev.verb, text: evText(ev)});
      return {thread: t, created};
    }
    return null;
  }
  make(ev, pair) {
    // the initiator speaks on the left; a thread first seen through a say starts at that minute
    return reactive({key: pair + '@' + ev.minute, init: ev.minute, a: ev.actor, b: ev.target, loc: ev.location, first: false, lines: [], beats: []});
  }
  prune(keepFrom) { for (const [p, l] of this.byPair) { const k = l.filter(t => t.init >= keepFrom); if (k.length) this.byPair.set(p, k); else this.byPair.delete(p); } }
}

// All threads in a list of events (a day from /api/day), in time order.
export function buildThreads(events) {
  const ix = new ThreadIndex(), out = [];
  for (const ev of events) { const r = ix.add(ev); if (r && r.created) out.push(r.thread); }
  return out;
}

export const ThreadCard = {
  components: {Avatar},
  props: {t: Object, compact: Boolean},
  methods: {
    agentName, hhmm,
    side(l) { return l.actor === this.t.a ? 'l' : 'r'; },
    showName(i) { return i === 0 || this.t.lines[i - 1].actor !== this.t.lines[i].actor; },
  },
  computed: { place() { return locName(this.t.loc); } },
  template: `
  <article class="thread" :class="{compact}">
    <header class="th-head">
      <span class="th-pair"><Avatar :id="t.a" :size="18" framed/><b>{{ agentName(t.a) }}</b><span class="x">×</span><Avatar :id="t.b" :size="18" framed/><b>{{ agentName(t.b) }}</b></span>
      <span class="th-meta">{{ hhmm(t.init) }} · {{ place }}</span>
      <span v-if="t.first" class="th-tag">第一次聊天</span>
    </header>
    <div class="th-body">
      <div v-for="(l, i) in t.lines" :key="i" class="tl" :class="side(l)" :style="{'--rc': 'var(--r-' + l.actor + ')'}">
        <span v-if="showName(i)" class="tl-who">{{ agentName(l.actor) }}</span>
        <p>{{ l.text }}</p>
      </div>
      <p v-if="!t.lines.length" class="th-wait muted small">交談中…</p>
      <p v-for="(b, i) in t.beats" :key="'b' + i" class="th-beat" :class="b.verb">{{ b.text }}</p>
    </div>
  </article>`,
};
