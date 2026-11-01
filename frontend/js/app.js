// Root: header (clock, pause/play, usage), view switcher, the inspector drawer.
import {createApp} from 'vue';
import {store, nav, connect, togglePause} from './store.js';
import {fmtMinute} from './names.js';
import {initSprites} from './map.js';
import Chronicle from './views/chronicle.js';
import Village from './views/village.js';
import Replay from './views/replay.js';
import Relations from './views/relations.js';
import Inspector from './inspector.js';

initSprites(document.getElementById('sprite-defs'));

const VIEWS = [
  {id: 'chronicle', zh: '大事記', icon: '📜'},
  {id: 'village', zh: '小鎮', icon: '🏘️'},
  {id: 'replay', zh: '回放', icon: '⏪'},
  {id: 'relations', zh: '關係', icon: '🕸️'},
];

const fromHash = () => { const h = location.hash.slice(1); if (VIEWS.some(v => v.id === h)) store.view = h; };
fromHash();
addEventListener('hashchange', fromHash);

createApp({
  components: {Chronicle, Village, Replay, Relations, Inspector},
  setup: () => ({VIEWS}),
  computed: {
    store: () => store,
    clock() { return store.ready ? fmtMinute(store.minute) : '— —:—'; },
    sun() { return ((store.minute % 1440) / 1440 * 100) + '%'; },
    usageLine() {
      const u = store.usage; if (!u) return 'LLM —';
      return `LLM ${u.calls} 次 · $${u.estimated_cost.toFixed(4)}`;
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
  watch: { 'store.view'(v) { if (location.hash.slice(1) !== v) history.replaceState(null, '', '#' + v); } },
  methods: {
    go: v => nav.go(v), togglePause,
  },
  template: `
  <header class="top">
    <div class="wordmark">高柏小鎮<small>田野觀測站 · 10 位居民</small></div>
    <div class="clockwrap">
      <span class="clock">{{ clock }}</span>
      <span v-if="!store.connected" class="conn-dot" title="連線中斷,重連中…" role="status"></span>
      <span v-if="store.llmBusy" class="llm-dot" title="等待 AI 回應中…" role="status"></span>
      <span v-if="store.night" class="moon" title="夜間快轉中,清晨自動恢復" role="status">🌙</span>
    </div>
    <div class="daystrip" aria-hidden="true"><div class="sun" :style="{left: sun}"></div></div>
    <button class="btn pause" :class="{active: store.paused, pending: store.pausePending}" @click="togglePause"
      :title="store.paused ? '繼續模擬' : (store.pausePending ? '暫停中…' : '暫停模擬')">{{ store.paused ? '▶ 繼續' : '▮▮ 暫停' }}</button>
    <span class="usage" :class="{warn: overBudget}" :title="usageTitle">{{ usageLine }}</span>
    <nav class="tabs" role="tablist">
      <button v-for="v in VIEWS" :key="v.id" role="tab" :aria-selected="store.view === v.id"
        :class="{active: store.view === v.id}" @click="go(v.id)">
        <span class="ti">{{ v.icon }}</span>{{ v.zh }}<span v-if="v.id === 'village' && store.unseenMajor" class="badge-dot" :title="store.unseenMajor + ' 件大事'"></span>
      </button>
    </nav>
  </header>
  <main class="stage" :class="{'with-insp': !!store.inspectId}">
    <Chronicle v-if="store.view === 'chronicle'"/>
    <Village v-show="store.view === 'village'" :active="store.view === 'village'"/>
    <Replay v-if="store.view === 'replay'"/>
    <Relations v-if="store.view === 'relations'"/>
    <Inspector/>
  </main>`,
}).mount('#app');

connect();
