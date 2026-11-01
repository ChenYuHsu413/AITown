// Root: header (clock, pause/play, usage), view switcher, the inspector drawer, the
// day/night driver (sim minute -> --n / --dusk on :root) and the major-event heartbeat.
import {createApp} from 'vue';
import {store, nav, connect, togglePause, setSpeed, SPEED_TIERS} from './store.js';
import {fmtMinute, phaseOf} from './names.js';
import {Icon} from './icons.js';
import Chronicle from './views/chronicle.js';
import Village from './views/village.js';
import Replay from './views/replay.js';
import Relations from './views/relations.js';
import Inspector from './inspector.js';
import Ops from './ops.js';

const VIEWS = [
  {id: 'chronicle', zh: '鎮誌', icon: 'book'},
  {id: 'village', zh: '小鎮', icon: 'map'},
  {id: 'replay', zh: '回放', icon: 'rewind'},
  {id: 'relations', zh: '關係', icon: 'web'},
];

const fromHash = () => { const h = location.hash.slice(1); if (VIEWS.some(v => v.id === h)) store.view = h; };
fromHash();
addEventListener('hashchange', fromHash);

// ---- day/night: one continuous phase from the sim clock (shared with the map) ----
let lastN = -1, lastD = -1;
function applyPhase(minute) {
  const {n, dusk} = phaseOf(minute), root = document.documentElement.style;
  if (Math.abs(n - lastN) > 0.004) { root.setProperty('--n', n.toFixed(3)); lastN = n; }
  if (Math.abs(dusk - lastD) > 0.004) { root.setProperty('--dusk', dusk.toFixed(3)); lastD = dusk; }
}

createApp({
  components: {Chronicle, Village, Replay, Relations, Inspector, Ops, Icon},
  setup: () => ({VIEWS, SPEED_TIERS}),
  computed: {
    store: () => store,
    clock() { return store.ready ? fmtMinute(store.minute) : '— —:—'; },
    phase() { return phaseOf(store.minute); },
    sun() { return ((store.minute % 1440) / 1440 * 100) + '%'; },
    usageLine() {
      const u = store.usage; if (!u) return 'LLM —';
      return `LLM ${u.calls} · $${u.estimated_cost.toFixed(4)}`;
    },
    usageTitle() {
      const u = store.usage; if (!u) return '';
      const f = u.dialogue_floor || {};
      return [`LLM 呼叫 ${u.calls}(快取命中 ${u.cache_hits})`,
        `輸入/輸出 Token ${u.input_tokens.toLocaleString()} / ${u.output_tokens.toLocaleString()}`,
        `預估成本 $${u.estimated_cost.toFixed(6)} / 預算 $${u.budget_usd}`,
        `規則決策佔比 ${u.decisions ? Math.round(u.rules_only / u.decisions * 100) + '%' : '—'}`,
        `對話落到保底 ${f.total ? Math.round(f.rate * 1000) / 10 + '%' : '—'}(近一日 ${f.recent_total ? Math.round(f.recent_rate * 1000) / 10 + '%' : '—'})`].join('\n');
    },
    overBudget() { const u = store.usage; return u && u.budget_usd && u.estimated_cost >= u.budget_usd * 0.8; },
  },
  watch: {
    'store.view'(v) { if (location.hash.slice(1) !== v) history.replaceState(null, '', '#' + v); },
    'store.minute': {handler: applyPhase, immediate: true},
  },
  methods: { go: v => nav.go(v), togglePause, setSpeed },
  template: `
  <header class="top sheet">
    <div class="wordmark">高柏小鎮<small>鎮誌 · 觀測站</small></div>
    <div class="clockwrap" :title="phase.label">
      <Icon :name="phase.icon" :size="17" class="phase-icon"/>
      <span class="clock">{{ clock }}</span>
      <span class="phase">{{ phase.label }}</span>
      <span v-if="!store.connected" class="conn-dot" title="連線中斷,重連中…" role="status"></span>
      <span v-if="store.llmBusy" class="llm-dot" title="等待 AI 回應中…" role="status"></span>
      <span v-if="store.night" class="cruise" title="夜間快轉中,清晨恢復你選的速度" role="status"><Icon name="moon" :size="13"/>夜間快轉</span>
    </div>
    <div class="daystrip" aria-hidden="true"><div class="sun" :style="{left: sun}"></div></div>
    <button class="btn pause" :class="{active: store.paused, pending: store.pausePending}" @click="togglePause"
      :title="store.paused ? '繼續模擬' : (store.pausePending ? '暫停中…' : '暫停模擬')">
      <Icon :name="store.paused ? 'play' : 'pause'" :size="14"/>{{ store.paused ? '繼續' : '暫停' }}</button>
    <div class="seg speed" role="group" aria-label="模擬速度">
      <button v-for="n in SPEED_TIERS" :key="n" :class="{active: store.userSpeed === n}" :aria-pressed="store.userSpeed === n"
        @click="setSpeed(n)" :title="'模擬速度 ' + n + '×'">{{ n }}×</button>
    </div>
    <span class="usage" :class="{warn: overBudget}" :title="usageTitle">{{ usageLine }}</span>
    <nav class="tabs" role="tablist">
      <button v-for="v in VIEWS" :key="v.id" role="tab" :aria-selected="store.view === v.id"
        :class="{active: store.view === v.id}" @click="go(v.id)">
        <Icon :name="v.icon" :size="16"/>{{ v.zh }}<span v-if="v.id === 'village' && store.unseenMajor" class="badge-dot" :title="store.unseenMajor + ' 件大事'"></span>
      </button>
    </nav>
  </header>
  <main class="stage sheet" :class="{'with-insp': !!store.inspectId}">
    <Chronicle v-if="store.view === 'chronicle'"/>
    <Village v-show="store.view === 'village'" :active="store.view === 'village'"/>
    <Replay v-if="store.view === 'replay'"/>
    <Relations v-if="store.view === 'relations'"/>
    <Inspector/>
    <Ops/>
  </main>
  <div v-if="store.heartbeat" :key="store.heartbeat" class="heartbeat" aria-hidden="true"></div>`,
}).mount('#app');

connect();
