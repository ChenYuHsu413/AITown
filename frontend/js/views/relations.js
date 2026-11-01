// Relationships window: a town-wide directed matrix heatmap (one axis at a time) and,
// for any two residents, both directions' three-axis curves over the days, with the
// pair's major/notable beats marked (click -> that day in the chronicle).
import {store, nav, getJSON} from '../store.js';
import {CAST, agentName, beatShort, fmtDay} from '../names.js';
import {Avatar, AXES, fmtDelta, Icon, tokenHex} from '../ui.js';

// Heatmap: single-hue sequential ramps from tokens.css (validated as ordinal ramps:
// monotone lightness, light end >= 2:1 on both papers). Cool for friendship/trust,
// warm for conflict; values interpolate between the five steps.
const hexRgb = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
let RAMPS = null;
const ramps = () => RAMPS || (RAMPS = {
  cool: [0, 1, 2, 3, 4].map(i => hexRgb(tokenHex(`--ramp-cool-${i}`))),
  warm: [0, 1, 2, 3, 4].map(i => hexRgb(tokenHex(`--ramp-warm-${i}`))),
});
function cellColor(axis, v) {
  if (v == null) return 'transparent';
  const r = ramps()[axis === 'conflict' ? 'warm' : 'cool'], t = Math.max(0, Math.min(1, v / 100)) * 4;
  const i = Math.min(3, Math.floor(t)), f = t - i;
  return `rgb(${[0, 1, 2].map(k => Math.round(r[i][k] + (r[i + 1][k] - r[i][k]) * f)).join(',')})`;
}
// Two residents' identity colors can sit close (10 hues can't all clear the
// validator's all-pairs floor), so a pair whose colors are near in OKLab gets the
// second line dashed -- identity never rests on color alone (direct labels too).
const lin = x => (x /= 255) <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
function oklab(h) { const [r, g, b] = hexRgb(h).map(lin);
  const l = Math.cbrt(.4122214708*r+.5363325363*g+.0514459929*b), m = Math.cbrt(.2119034982*r+.6806995451*g+.1073969566*b), s = Math.cbrt(.0883024619*r+.2817188376*g+.6299787005*b);
  return [.2104542553*l+.793617785*m-.0040720468*s, 1.9779984951*l-2.428592205*m+.4505937099*s, .0259040371*l+.7827717662*m-.808675766*s]; }
const closeColors = (a, b) => { const p = oklab(tokenHex(`--r-${a}`)), q = oklab(tokenHex(`--r-${b}`));
  return Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]) * 100 < 15; };

// One small chart: an axis over days, both directions, beat days marked.
const Curve = {
  props: {axis: Object, series: Object, days: Array, beats: Array, a: String, b: String, width: Number, dashB: Boolean},
  emits: ['beat'],
  data: () => ({hover: null}),
  computed: {
    H: () => 150, padL: () => 30, padR: () => 54, padT: () => 16, padB: () => 22,
    ab() { return this.series.ab; }, ba() { return this.series.ba; },
    yr() {
      const vals = [...this.ab, ...this.ba].filter(v => v != null);
      let lo = Math.min(...vals), hi = Math.max(...vals);
      if (!vals.length) { lo = 0; hi = 100; }
      const mid = (lo + hi) / 2, span = Math.max(10, hi - lo + 4);
      lo = Math.max(0, mid - span / 2); hi = Math.min(100, lo + span); lo = Math.max(0, hi - span);
      return [lo, hi];
    },
    ticks() { const [lo, hi] = this.yr, s = (hi - lo) / 3; return [0, 1, 2, 3].map(i => Math.round(lo + s * i)); },
    xTicks() { const n = this.days.length, step = Math.max(1, Math.ceil(n / 8)); return this.days.filter((d, i) => i % step === 0); },
    pathAB() { return this.path(this.ab); }, pathBA() { return this.path(this.ba); },
    beatDays() { return [...new Set(this.beats.map(b => b.day))]; },
  },
  methods: {
    x(day) { const d0 = this.days[0], d1 = this.days[this.days.length - 1]; return this.padL + (d1 === d0 ? 0 : (day - d0) / (d1 - d0)) * (this.width - this.padL - this.padR); },
    y(v) { const [lo, hi] = this.yr; return this.padT + (1 - (v - lo) / (hi - lo)) * (this.H - this.padT - this.padB); },
    path(arr) { return arr.map((v, i) => `${i ? 'L' : 'M'}${this.x(this.days[i]).toFixed(1)},${this.y(v).toFixed(1)}`).join(''); },
    move(e) {
      const r = e.currentTarget.getBoundingClientRect(), px = (e.clientX - r.left) * this.width / r.width;
      let best = 0, bd = 1e9; this.days.forEach((d, i) => { const dd = Math.abs(this.x(d) - px); if (dd < bd) { bd = dd; best = i; } });
      this.hover = best;
    },
    tipLeft() { const x = this.x(this.days[this.hover]); return Math.min(x + 8, this.width - 150); },
    last(arr) { return arr[arr.length - 1]; },
    agentName, fmtDelta,
    d(arr, i) { return i > 0 ? arr[i] - arr[i - 1] : 0; },
  },
  template: `
  <div class="curve">
    <div class="curve-title">{{ axis.zh }}</div>
    <svg :viewBox="'0 0 ' + width + ' ' + H" :width="width" :height="H" @mousemove="move" @mouseleave="hover = null">
      <g class="grid">
        <g v-for="t in ticks" :key="t"><line :x1="padL" :x2="width - padR" :y1="y(t)" :y2="y(t)"/><text :x="padL - 4" :y="y(t) + 3" text-anchor="end">{{ t }}</text></g>
        <text v-for="d in xTicks" :key="'x' + d" :x="x(d)" :y="H - 6" text-anchor="middle">D{{ d }}</text>
      </g>
      <line v-for="d in beatDays" :key="'b' + d" class="beatline" :x1="x(d)" :x2="x(d)" :y1="padT" :y2="H - padB"/>
      <g v-for="d in beatDays" :key="'m' + d" class="bmark" @click="$emit('beat', d)">
        <title>第 {{ d }} 天的大事:點擊翻到鎮誌</title>
        <rect :x="x(d) - 8" :y="0" width="16" height="16" fill="transparent"/>
        <circle :cx="x(d)" :cy="padT - 4" r="4.5"/>
      </g>
      <path :d="pathBA" fill="none" :style="{stroke: 'var(--r-' + b + ')'}" stroke-width="2" stroke-linejoin="round" :stroke-dasharray="dashB ? '5 3' : null"/>
      <path :d="pathAB" fill="none" :style="{stroke: 'var(--r-' + a + ')'}" stroke-width="2" stroke-linejoin="round"/>
      <text class="dlabel" :x="width - padR + 4" :y="y(last(ab)) + 3">{{ agentName(a) }}→ {{ Math.round(last(ab)) }}</text>
      <text class="dlabel" :x="width - padR + 4" :y="y(last(ba)) + (Math.abs(y(last(ab)) - y(last(ba))) < 11 ? 14 : 3)">{{ agentName(b) }}→ {{ Math.round(last(ba)) }}</text>
      <g v-if="hover != null">
        <line class="cross" :x1="x(days[hover])" :x2="x(days[hover])" :y1="padT" :y2="H - padB"/>
        <circle :cx="x(days[hover])" :cy="y(ab[hover])" r="4" :style="{fill: 'var(--r-' + a + ')', stroke: 'var(--card)'}" stroke-width="2"/>
        <circle :cx="x(days[hover])" :cy="y(ba[hover])" r="4" :style="{fill: 'var(--r-' + b + ')', stroke: 'var(--card)'}" stroke-width="2"/>
      </g>
    </svg>
    <div v-if="hover != null" class="tip" :style="{left: tipLeft() + 'px'}">
      <b>第 {{ days[hover] }} 天</b><br>
      <i class="sw" :style="{background: 'var(--r-' + a + ')'}"></i>{{ agentName(a) }}→{{ agentName(b) }} {{ ab[hover].toFixed(1) }} <small>{{ fmtDelta(d(ab, hover)) }}</small><br>
      <i class="sw" :class="{dash: dashB}" :style="{background: 'var(--r-' + b + ')'}"></i>{{ agentName(b) }}→{{ agentName(a) }} {{ ba[hover].toFixed(1) }} <small>{{ fmtDelta(d(ba, hover)) }}</small>
    </div>
  </div>`,
};

export default {
  components: {Avatar, Curve, Icon},
  data: () => ({graph: null, axis: 'friendship', a: 'jiji', b: 'lengyue', hist: null, histErr: '', width: 600}),
  setup: () => ({AXES, CAST}),
  computed: {
    store: () => store,
    edgeMap() {
      const m = {};
      for (const e of (this.graph && this.graph.edges) || []) { m[e.a + '|' + e.b] = [e, true]; m[e.b + '|' + e.a] = [e, false]; }
      return m;
    },
    days() {
      const last = Math.max(1, store.todayDay - 1);
      const first = this.hist && this.hist.history.length ? Math.max(0, Math.min(...this.hist.history.map(h => h.day)) - 1) : 0;
      const out = []; for (let d = first; d <= last; d++) out.push(d); return out;
    },
    series() {   // carry each direction's value forward over days nothing moved
      const res = {};
      for (const ax of AXES) res[ax.key] = {ab: this.carry(this.a, this.b, ax.key), ba: this.carry(this.b, this.a, ax.key)};
      return res;
    },
    dashB() { return this.a !== this.b && closeColors(this.a, this.b); },
    rampCss() { return `linear-gradient(90deg,${cellColor(this.axis, 0)},${cellColor(this.axis, 50)},${cellColor(this.axis, 100)})`; },
    beatList() {
      return ((this.hist && this.hist.beats) || []).map(b => ({...b, s: beatShort(b)}));
    },
  },
  watch: {
    a() { this.loadHist(); }, b() { this.loadHist(); },
    'store.relPair'(p) { if (p) this.setPair(p); },
    'store.digestsVersion'() { this.loadHist(); },
  },
  created() {
    if (store.relPair) { this.a = store.relPair[0]; this.b = store.relPair[1]; }
    this.loadGraph(); this.loadHist();
    this.timer = setInterval(this.loadGraph, 10000);
  },
  mounted() {
    this.ro = new ResizeObserver(() => { const el = this.$refs.curves; if (el) this.width = Math.max(260, el.clientWidth); });
    this.ro.observe(this.$refs.curves);
  },
  beforeUnmount() { clearInterval(this.timer); this.ro && this.ro.disconnect(); },
  methods: {
    agentName, fmtDay,
    setPair(p) { this.a = p[0]; this.b = p[1]; },
    async loadGraph() { try { this.graph = await getJSON('/api/relationships'); } catch (e) {} },
    async loadHist() {
      if (!this.a || !this.b || this.a === this.b) { this.hist = null; return; }
      const a = this.a, b = this.b;
      try { const d = await getJSON(`/api/relationship_history?a=${a}&b=${b}`); if (a === this.a && b === this.b) { this.hist = d; this.histErr = ''; } }
      catch (e) { this.hist = null; this.histErr = '關係歷史讀取失敗(需要資料庫)。'; }
    },
    val(r, c) {
      if (r === c) return null;
      const hit = this.edgeMap[r + '|' + c]; if (!hit) return null;
      const [e, fwd] = hit;
      if (this.axis === 'conflict') return e.conflict_max;
      return fwd ? e[this.axis + '_ab'] : e[this.axis + '_ba'];
    },
    cellStyle(r, c) { const v = this.val(r, c); return {background: cellColor(this.axis, v), color: v != null && v > 45 ? '#FFFDF7' : 'var(--ink-day)'}; },
    cellTitle(r, c) {
      const v = this.val(r, c), ax = AXES.find(x => x.key === this.axis).zh;
      if (r === c) return ''; if (v == null) return `${agentName(r)} 與 ${agentName(c)} 還沒有交集`;
      return this.axis === 'conflict' ? `${agentName(r)} ↔ ${agentName(c)} 衝突(兩方向取高)${v}` : `${agentName(r)} 對 ${agentName(c)} 的${ax}:${v}`;
    },
    pick(r, c) { if (r !== c) { this.a = r; this.b = c; this.$nextTick(() => this.$refs.pairsec.scrollIntoView({behavior: 'smooth', block: 'start'})); } },
    carry(from, to, key) {
      const rows = ((this.hist && this.hist.history) || []).filter(h => h.a === from && h.b === to).sort((x, y) => x.day - y.day);
      const byDay = {}; rows.forEach(h => { byDay[h.day] = h[key]; });
      let v = rows.length ? rows[0][key] - rows[0]['d_' + key] : (key === 'conflict' ? 0 : 30);
      return this.days.map(d => { if (byDay[d] != null) v = byDay[d]; return v; });
    },
    swap() { const t = this.a; this.a = this.b; this.b = t; },
    jumpDay(day) { const bt = this.beatList.find(x => x.day === day); if (bt) this.jump(bt); },
    jump(bt) { nav.chronicle(bt.day, bt.minute); },
  },
  template: `
  <section class="relations view-scroll">
    <header class="view-head">
      <h1>關係</h1>
      <p class="muted">每一格是「列」對「欄」的感受(有方向);衝突取兩方向較高者。點一格,下方看兩人的歷史曲線。</p>
    </header>
    <div class="axis-switch" role="tablist">
      <div class="seg"><button v-for="ax in AXES" :key="ax.key" :class="{active: axis === ax.key}" @click="axis = ax.key">{{ ax.zh }}</button></div>
    </div>
    <div class="matrix-wrap">
      <table class="matrix" v-if="graph">
        <thead><tr><th class="corner">從 ↓ 對 →</th><th v-for="c in CAST" :key="c" :title="agentName(c)"><Avatar :id="c" :size="16" framed/><span>{{ agentName(c) }}</span></th></tr></thead>
        <tbody>
          <tr v-for="r in CAST" :key="r">
            <th><Avatar :id="r" :size="16" framed/><span>{{ agentName(r) }}</span></th>
            <td v-for="c in CAST" :key="c" :class="{diag: r === c, sel: r === a && c === b}" :style="cellStyle(r, c)" :title="cellTitle(r, c)" @click="pick(r, c)">
              {{ r === c ? '' : (val(r, c) ?? '·') }}
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">讀取中…</p>
      <div class="legend-ramp"><span>0</span><i :style="{background: rampCss}"></i><span>100</span><span class="muted small">· 表示還沒有交集</span></div>
    </div>

    <h2 ref="pairsec" class="sec-title">兩人的關係歷史</h2>
    <div class="pair-pick">
      <select v-model="a"><option v-for="c in CAST" :key="c" :value="c">{{ agentName(c) }}</option></select>
      <button class="btn icon-only" @click="swap" title="交換" aria-label="交換"><Icon name="shuffle" :size="15"/></button>
      <select v-model="b"><option v-for="c in CAST" :key="c" :value="c">{{ agentName(c) }}</option></select>
    </div>
    <div class="legend">
      <span><i class="sw" :style="{background: 'var(--r-' + a + ')'}"></i>{{ agentName(a) }} → {{ agentName(b) }}</span>
      <span><i class="sw" :class="{dash: dashB}" :style="{background: 'var(--r-' + b + ')'}"></i>{{ agentName(b) }} → {{ agentName(a) }}</span>
      <span class="muted small">金色點 = 兩人的大事,點擊翻到鎮誌</span>
    </div>
    <div ref="curves" class="curves">
      <p v-if="a === b" class="muted">請選兩位不同的居民。</p>
      <p v-else-if="histErr" class="muted">{{ histErr }}</p>
      <template v-else-if="hist">
        <p v-if="!hist.history.length" class="muted">這兩人之間的關係還沒有變動過的紀錄。</p>
        <Curve v-for="ax in AXES" :key="ax.key" :axis="ax" :series="series[ax.key]" :days="days" :beats="hist.beats" :a="a" :b="b" :width="width" :dash-b="dashB" @beat="jumpDay"/>
      </template>
    </div>
    <div v-if="hist && beatList.length" class="pair-beats">
      <div class="lab">兩人的大事(點擊翻到鎮誌)</div>
      <button v-for="(bt, i) in beatList" :key="i" class="beat-chip" :class="bt.tier" @click="jump(bt)">
        <b>D{{ bt.day }}</b> {{ bt.s.what }} <span class="muted">{{ bt.s.who }}</span>
      </button>
    </div>
  </section>`,
};
