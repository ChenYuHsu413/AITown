// The resident inspector: the operator's god's-eye view (/api/agents/{id}). This is the
// ONLY place a wish's private text (title / statement / requirements) is shown.
import {store, nav, getJSON} from './store.js';
import {agentName, locName, locByEn, occName, traitName, actName, moodEmoji, resolve, chapterTitle,
  REQ_KIND_ZH, WISH_STATUS_ZH, SCALE_ZH, CHAPTER_TYPE_ZH, OUTCOME_ZH, CAST} from './names.js';
import {Avatar} from './ui.js';

const MEM_KIND = {rumor: '🗣️ ', secret: '🤫 ', reflection: '💭 '};

export default {
  components: {Avatar},
  data: () => ({a: null, loading: false, error: '', tab: 'life'}),
  computed: { id: () => store.inspectId },
  watch: { id: { handler(v) { this.a = null; if (v) this.load(true); }, immediate: true } },
  created() { this.timer = setInterval(() => { if (this.id && !this.loading) this.load(false); }, 5000); },
  beforeUnmount() { clearInterval(this.timer); },
  methods: {
    agentName, occName, traitName, actName, moodEmoji, resolve, chapterTitle, locByEn,
    async load(fresh) {
      const id = this.id; this.loading = true;
      if (fresh) this.error = '';
      try { const d = await getJSON('/api/agents/' + id); if (id === this.id) this.a = d; }
      catch (e) { if (id === this.id && fresh) this.error = '讀取失敗:' + e.message; }
      this.loading = false;
    },
    close() { store.inspectId = null; },
    reqTarget(r) {
      if (!r.target) return '';
      if (CAST.includes(r.target)) return agentName(r.target);
      const l = locName(r.target); if (l !== r.target) return l;
      return actName(r.target);
    },
    reqLabel(r) {
      const t = this.reqTarget(r), k = REQ_KIND_ZH[r.kind] || r.kind;
      return t ? `${k}:${t}` : k;
    },
    reqPct(r) { return r.threshold > 0 ? Math.min(100, Math.round(r.progress / r.threshold * 100)) : (r.completed ? 100 : 0); },
    num: v => Number.isInteger(v) ? v : Math.round(v * 10) / 10,
    statusZh: s => WISH_STATUS_ZH[s] || s, scaleZh: s => SCALE_ZH[s] || s,
    chType: t => CHAPTER_TYPE_ZH[t] || t, outcome: o => OUTCOME_ZH[o] || o,
    memText(m) { return (MEM_KIND[m.kind] || '') + resolve(m.text); },
    subj(b) { return b.subject === 'self' ? '自己' : CAST.includes(b.subject) ? agentName(b.subject) : locName(b.subject) || b.subject_name; },
    pair(other) { nav.pair(this.id, other); },
  },
  template: `
  <aside class="inspector" v-if="id" role="dialog" aria-label="居民檢視器">
    <header class="insp-head">
      <span class="lab">居民檢視器 · 上帝視角</span>
      <button class="x" @click="close" aria-label="關閉">✕</button>
    </header>
    <div class="insp-body">
      <p v-if="error" class="muted pad">{{ error }}</p>
      <div v-else-if="!a" class="skel pad"><i></i><i></i><i></i></div>
      <template v-else>
        <div class="who">
          <Avatar :id="a.id" :size="40"/>
          <div>
            <h2>{{ moodEmoji(a.state.mood) }} {{ agentName(a.id, a.name) }}</h2>
            <p class="muted small">{{ a.age }} 歲 · {{ occName(a.occupation) }} · {{ a.traits.map(traitName).join('、') }}</p>
          </div>
        </div>
        <div class="kv"><b>狀態</b><span>{{ actName(a.state.action) }} @ {{ locByEn(a.state.location) }}</span></div>
        <div class="kv"><b>精力</b><span class="bar"><i :style="{width: a.state.energy + '%'}"></i></span><span class="n">{{ a.state.energy }}</span></div>
        <div class="kv"><b>金錢</b><span class="money">💰 {{ a.state.money }}</span></div>
        <div class="kv" v-for="g in a.goals" :key="g.goal"><b>目標</b><span>{{ resolve(g.goal) }}</span></div>

        <nav class="insp-tabs">
          <button v-for="t in [['life','章節·願望'],['mind','記憶·印象'],['trace','決策'],['rel','關係']]" :key="t[0]"
            :class="{active: tab === t[0]}" @click="tab = t[0]">{{ t[1] }}</button>
        </nav>

        <template v-if="tab === 'life'">
          <h3>人生章節</h3>
          <div class="chap" v-if="a.chapter">
            <div class="row"><b>{{ chapterTitle(a.chapter.title) }}</b>
              <span class="muted small">{{ chType(a.chapter.type) }} · 自第 {{ a.chapter.started_on }} 天<template v-if="a.chapter.type === 'interlude'"> · 至第 {{ a.chapter.until_day }} 天</template></span></div>
            <p class="small">{{ resolve(a.chapter.narrative) }}</p>
            <p v-if="a.chapter.goal" class="small muted">目標:{{ resolve(a.chapter.goal) }}</p>
          </div>
          <p v-else class="muted small">平常的日子。</p>
          <details v-if="a.chapter_history.length" class="hist">
            <summary>過往章節({{ a.chapter_history.length }})</summary>
            <div class="chap past" v-for="(h, i) in a.chapter_history" :key="i">
              <div class="row"><b>{{ chapterTitle(h.title) }}</b><span class="muted small">{{ outcome(h.outcome) }} · 第 {{ h.started_on }}–{{ h.ended_on }} 天</span></div>
              <p class="small">{{ resolve(h.biography_line) }}</p>
            </div>
          </details>

          <h3>願望 <span class="private">🔒 僅限操作者</span></h3>
          <p class="muted small">居民私下的心願。這些文字只出現在這裡,大事記與地圖上只會寫「有了新的願望」。</p>
          <p v-if="!a.wishes.length" class="muted small">目前沒有願望。</p>
          <div class="wish" v-for="w in a.wishes.slice().reverse()" :key="w.id" :class="w.status">
            <div class="row"><b>{{ resolve(w.title) }}</b><span class="badge" :class="w.status">{{ statusZh(w.status) }}</span></div>
            <p class="small">{{ resolve(w.statement) }}</p>
            <p class="muted small">{{ scaleZh(w.scale) }} · 第 {{ w.created_on }} 天許下
              <template v-if="w.ended_on"> · 第 {{ w.ended_on }} 天結束</template>
              <template v-else-if="w.expires_on"> · 期限第 {{ w.expires_on }} 天</template>
              <template v-if="w.frustration_count"> · 受挫 {{ w.frustration_count }} 次</template></p>
            <div class="kv"><b>整體</b><span class="bar amber"><i :style="{width: Math.round(w.progress * 100) + '%'}"></i></span><span class="n">{{ Math.round(w.progress * 100) }}%</span></div>
            <div class="req" v-for="(r, i) in w.requirements" :key="i" :class="{done: r.completed}">
              <span class="small">{{ r.completed ? '✓' : '○' }} {{ reqLabel(r) }}</span>
              <span class="bar"><i :style="{width: reqPct(r) + '%'}"></i></span>
              <span class="n small">{{ num(r.progress) }}/{{ num(r.threshold) }}</span>
            </div>
          </div>
        </template>

        <template v-if="tab === 'mind'">
          <h3>近期記憶</h3>
          <div class="mem" v-for="(m, i) in a.memories" :key="i">
            <time>{{ m.clock.replace(/^Day (\\d+) · /, 'D$1 ') }}</time><span>{{ memText(m) }}</span><span class="imp">重要度 {{ m.importance }}</span>
          </div>
          <h3>長期印象</h3>
          <p v-if="!a.beliefs.length" class="muted small">尚無長期印象。</p>
          <div class="blf" v-for="(b, i) in a.beliefs" :key="'b' + i">
            <div class="row"><b>{{ b.sentiment < 0 ? '⊖ ' : b.sentiment > 0 ? '⊕ ' : '' }}{{ subj(b) }}<template v-if="b.source_count > 1"> · {{ b.source_count }} 次</template></b>
              <span class="muted small">{{ Math.round(b.confidence * 100) }}%</span></div>
            <span class="bar amber"><i :style="{width: Math.round(b.confidence * 100) + '%'}"></i></span>
            <p class="small muted">{{ resolve(b.text) }}</p>
          </div>
        </template>

        <template v-if="tab === 'trace'">
          <h3>決策軌跡</h3>
          <p v-if="!a.last_decisions.length" class="muted small">尚無決策。</p>
          <div class="trace" v-for="(t, i) in a.last_decisions" :key="i">
            <div class="muted small mono">{{ t.clock }} · L{{ t.level }} · {{ t.model }}</div>
            <div class="small">{{ t.observation }}</div>
            <div class="small"><b>→ {{ actName(t.action.split(' -> ')[0]) }}{{ t.action.includes(' -> ') ? ' → ' + t.action.split(' -> ')[1] : '' }}</b> — {{ t.reason }}</div>
            <div v-if="t.memories && t.memories.length" class="muted small">喚起:{{ t.memories.map(resolve).join(';') }}</div>
          </div>
        </template>

        <template v-if="tab === 'rel'">
          <h3>人際關係 <span class="muted small">(此人的感受)</span></h3>
          <p v-if="!a.relationships.length" class="muted small">尚無人際關係。</p>
          <button class="relrow" v-for="r in a.relationships.slice().sort((x, y) => y.friendship - x.friendship)" :key="r.id" @click="pair(r.id)" title="看兩人的關係曲線">
            <Avatar :id="r.id" :size="18"/><b>{{ agentName(r.id, r.name) }}</b>
            <span class="small">友誼 {{ r.friendship }}</span><span class="small">信任 {{ r.trust }}</span><span class="small">衝突 {{ r.conflict }}</span>
            <span v-if="r.romance > 0" class="small">💗 {{ r.romance }}</span>
          </button>
        </template>
      </template>
    </div>
  </aside>`,
};
