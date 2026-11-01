// The resident inspector: the operator's god's-eye view (/api/agents/{id}). This is the
// ONLY place a wish's private text (title / statement / requirements) is shown.
import {store, nav, getJSON} from './store.js';
import {agentName, locName, locByEn, occName, traitName, actName, moodZh, resolve, chapterTitle,
  REQ_KIND_ZH, WISH_STATUS_ZH, SCALE_ZH, CHAPTER_TYPE_ZH, OUTCOME_ZH, CAST} from './names.js';
import {Avatar, Icon, Num} from './ui.js';

const MEM_KIND = {rumor: '傳言', secret: '秘密', reflection: '反思'};
const POS_KEY = 'aitown.cardPos';
const W = 388, GAP = 12;
const loadPos = () => { try { return JSON.parse(sessionStorage.getItem(POS_KEY) || 'null'); } catch (e) { return null; } };
const savePos = p => { try { sessionStorage.setItem(POS_KEY, JSON.stringify(p)); } catch (e) { /* private mode */ } };
const TABS = [['chapter', '章節'], ['wish', '願望'], ['mind', '記憶'], ['trace', '決策'], ['rel', '關係']];

export default {
  components: {Avatar, Icon, Num},
  setup: () => ({TABS}),
  data: () => ({a: null, secrets: [], loading: false, error: '', tab: 'chapter', pos: null, dragging: false}),
  computed: {
    id: () => store.inspectId,
    cardStyle() { return {'--rc': 'var(--r-' + this.id + ')', ...(this.pos ? {left: this.pos.x + 'px', top: this.pos.y + 'px'} : {})}; },
  },
  watch: {
    id: { handler(v) { this.a = null; if (v) { this.load(true); this.$nextTick(() => this.place()); } }, immediate: true },
    'store.inspectAt'() { if (this.id) this.$nextTick(() => this.place()); },
  },
  created() { this.timer = setInterval(() => { if (this.id && !this.loading) this.load(false); }, 5000); },
  beforeUnmount() { clearInterval(this.timer); },
  methods: {
    agentName, occName, traitName, actName, moodZh, resolve, chapterTitle, locByEn,
    async load(fresh) {
      const id = this.id; this.loading = true;
      if (fresh) this.error = '';
      try {
        const [d, sec] = await Promise.all([getJSON('/api/agents/' + id), getJSON('/api/secrets')]);
        if (id === this.id) {
          this.a = d;
          // A secret's words live here: the owner's own, and those confided to them.
          this.secrets = (sec.secrets || []).filter(x => x.owner === id || x.confided_to.some(c => c.id === id));
        }
      }
      catch (e) { if (id === this.id && fresh) this.error = '讀取失敗:' + e.message; }
      this.loading = false;
    },
    close() { store.inspectId = null; },
    // A floating card: a position the viewer dragged it to (this session) wins; otherwise it
    // opens beside the resident who was clicked. It never covers the live column.
    bounds() {
      const st = this.$el.parentElement.getBoundingClientRect(), col = document.querySelector('.village .live-col');
      let right = st.width - GAP;
      if (store.view === 'village' && col && col.offsetParent) right = Math.min(right, col.getBoundingClientRect().left - st.left - GAP);
      return {st, right, bottom: st.height - GAP};
    },
    clamp(p) {
      const {right, bottom} = this.bounds(), h = this.$el.offsetHeight || 400;
      return {x: Math.round(Math.max(GAP, Math.min(right - W, p.x))), y: Math.round(Math.max(GAP, Math.min(bottom - Math.min(h, bottom - GAP), p.y)))};
    },
    place() {
      if (!this.$el || !this.$el.getBoundingClientRect || matchMedia('(max-width: 900px)').matches) { this.pos = null; return; }
      const saved = loadPos(), at = store.inspectAt, {st} = this.bounds();
      if (saved) this.pos = this.clamp(saved);
      else if (at) { const x = at.x - st.left, y = at.y - st.top; this.pos = this.clamp({x: x + 28, y: y - 40}); if (this.pos.x < x && x - W - 28 > GAP) this.pos = this.clamp({x: x - W - 28, y: y - 40}); }
      else this.pos = this.clamp({x: GAP + 8, y: GAP + 44});
    },
    dragStart(e) {
      if (e.target.closest('button') || !this.pos) return;
      const sx = e.clientX, sy = e.clientY, p0 = {...this.pos}; this.dragging = true;
      const move = ev => { this.pos = this.clamp({x: p0.x + ev.clientX - sx, y: p0.y + ev.clientY - sy}); };
      const up = () => { this.dragging = false; removeEventListener('pointermove', move); removeEventListener('pointerup', up); savePos(this.pos); };
      addEventListener('pointermove', move); addEventListener('pointerup', up);
    },
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
    memText(m) { return resolve(m.text); }, memKind: k => MEM_KIND[k] || '',
    subj(b) { return b.subject === 'self' ? '自己' : CAST.includes(b.subject) ? agentName(b.subject) : locName(b.subject) || b.subject_name; },
    pair(other) { nav.pair(this.id, other); },
  },
  template: `
  <aside class="inspector" v-if="id" role="dialog" aria-label="角色卡" :class="{floating: !!pos, dragging}" :style="cardStyle">
    <header class="insp-head" @pointerdown="dragStart" title="拖曳移動角色卡">
      <span class="lab">角色卡 · 上帝視角</span>
      <button class="x" @click="close" aria-label="關閉"><Icon name="x" :size="15"/></button>
    </header>
    <div class="insp-body">
      <p v-if="error" class="muted pad">{{ error }}</p>
      <div v-else-if="!a" class="skel pad"><i></i><i></i><i></i></div>
      <template v-else>
        <div class="who">
          <Avatar :id="a.id" :size="46" framed/>
          <div>
            <h2>{{ agentName(a.id, a.name) }}</h2>
            <p class="muted small">{{ a.age }} 歲 · {{ occName(a.occupation) }} · 心情{{ moodZh(a.state.mood) }}</p>
            <p class="traits"><span v-for="t in a.traits" :key="t">{{ traitName(t) }}</span></p>
          </div>
        </div>
        <dl class="stats">
          <div><dt>此刻</dt><dd>{{ actName(a.state.action) }} @ {{ locByEn(a.state.location) }}</dd></div>
          <div><dt>精力</dt><dd><span class="bar"><i :style="{width: a.state.energy + '%'}"></i></span><Num class="mono" :v="a.state.energy"/></dd></div>
          <div><dt>金錢</dt><dd class="mono">$<Num :v="a.state.money"/></dd></div>
          <div v-for="g in a.goals" :key="g.goal"><dt>目標</dt><dd>{{ resolve(g.goal) }}</dd></div>
        </dl>

        <nav class="index-tabs" role="tablist">
          <button v-for="t in TABS" :key="t[0]" role="tab" :aria-selected="tab === t[0]" :class="{active: tab === t[0]}" @click="tab = t[0]">{{ t[1] }}</button>
        </nav>
        <div class="tab-page">

        <template v-if="tab === 'chapter'">
          <div class="chap" v-if="a.chapter">
            <div class="row"><b>{{ chapterTitle(a.chapter.title) }}</b>
              <span class="muted small">{{ chType(a.chapter.type) }} · 自第 {{ a.chapter.started_on }} 天<template v-if="a.chapter.type === 'interlude'"> · 至第 {{ a.chapter.until_day }} 天</template></span></div>
            <p class="small">{{ resolve(a.chapter.narrative) }}</p>
            <p v-if="a.chapter.goal" class="small muted">目標:{{ resolve(a.chapter.goal) }}</p>
          </div>
          <p v-else class="muted small">平常的日子。</p>
          <h3 v-if="a.chapter_history.length">過往章節</h3>
          <div class="chap past" v-for="(h, i) in a.chapter_history" :key="i">
            <div class="row"><b>{{ chapterTitle(h.title) }}</b><span class="muted small">{{ outcome(h.outcome) }} · 第 {{ h.started_on }}–{{ h.ended_on }} 天</span></div>
            <p class="small">{{ resolve(h.biography_line) }}</p>
          </div>
        </template>

        <template v-if="tab === 'wish'">
          <p class="private"><Icon name="lock" :size="12"/>僅限操作者 · 居民私下的心願只出現在這裡;鎮誌與地圖只寫「有了新的願望」。</p>
          <p v-if="!a.wishes.length" class="muted small">目前沒有願望。</p>
          <div class="wish" v-for="w in a.wishes.slice().reverse()" :key="w.id" :class="w.status">
            <div class="row"><b>{{ resolve(w.title) }}</b><span class="badge" :class="w.status">{{ statusZh(w.status) }}</span></div>
            <p class="small">{{ resolve(w.statement) }}</p>
            <p class="muted small">{{ scaleZh(w.scale) }} · 第 {{ w.created_on }} 天許下
              <template v-if="w.ended_on"> · 第 {{ w.ended_on }} 天結束</template>
              <template v-else-if="w.expires_on"> · 期限第 {{ w.expires_on }} 天</template>
              <template v-if="w.frustration_count"> · 受挫 {{ w.frustration_count }} 次</template></p>
            <div class="req total"><span class="small">整體</span><span class="bar gold"><i :style="{width: Math.round(w.progress * 100) + '%'}"></i></span><span class="n mono small"><Num :v="w.progress * 100"/>%</span></div>
            <div class="req" v-for="(r, i) in w.requirements" :key="i" :class="{done: r.completed}">
              <span class="small">{{ r.completed ? '✓' : '○' }} {{ reqLabel(r) }}</span>
              <span class="bar"><i :style="{width: reqPct(r) + '%'}"></i></span>
              <span class="n mono small">{{ num(r.progress) }}/{{ num(r.threshold) }}</span>
            </div>
          </div>
        </template>

        <template v-if="tab === 'mind'">
          <h3>近期記憶</h3>
          <div class="mem" v-for="(m, i) in a.memories" :key="i">
            <time>{{ m.clock.replace(/^Day (\\d+) · /, 'D$1 ') }}</time><span><em v-if="memKind(m.kind)" class="tag">{{ memKind(m.kind) }}</em>{{ memText(m) }}</span><span class="imp">重要度 {{ m.importance }}</span>
          </div>
          <h3>秘密 <span class="private inline"><Icon name="lock" :size="11"/>僅限操作者</span></h3>
          <p v-if="!secrets.length" class="muted small">沒有秘密。</p>
          <div class="blf" v-for="x in secrets" :key="x.id">
            <div class="row"><b>{{ x.owner === id ? '自己的秘密' : agentName(x.owner) + ' 吐露的' }}</b>
              <span class="muted small">{{ x.resolved ? '已釋懷' : x.leaked ? '已外洩' : '' }}</span></div>
            <p class="small">{{ resolve(x.text) }}</p>
            <p v-if="x.owner === id && x.confided_to.length" class="small muted">
              吐露給:{{ x.confided_to.map(c => agentName(c.id)).join('、') }}</p>
          </div>
          <h3>長期印象</h3>
          <p v-if="!a.beliefs.length" class="muted small">尚無長期印象。</p>
          <div class="blf" v-for="(b, i) in a.beliefs" :key="'b' + i">
            <div class="row"><b>{{ b.sentiment < 0 ? '⊖ ' : b.sentiment > 0 ? '⊕ ' : '' }}{{ subj(b) }}<template v-if="b.source_count > 1"> · {{ b.source_count }} 次</template></b>
              <span class="muted small mono">{{ Math.round(b.confidence * 100) }}%</span></div>
            <span class="bar"><i :style="{width: Math.round(b.confidence * 100) + '%'}"></i></span>
            <p class="small muted">{{ resolve(b.text) }}</p>
          </div>
        </template>

        <template v-if="tab === 'trace'">
          <p v-if="!a.last_decisions.length" class="muted small">尚無決策。</p>
          <div class="trace" v-for="(t, i) in a.last_decisions" :key="i">
            <div class="muted small mono">{{ t.clock }} · L{{ t.level }} · {{ t.model }}</div>
            <div class="small">{{ t.observation }}</div>
            <div class="small"><b>→ {{ actName(t.action.split(' -> ')[0]) }}{{ t.action.includes(' -> ') ? ' → ' + t.action.split(' -> ')[1] : '' }}</b> — {{ t.reason }}</div>
            <div v-if="t.memories && t.memories.length" class="muted small">喚起:{{ t.memories.map(resolve).join(';') }}</div>
          </div>
        </template>

        <template v-if="tab === 'rel'">
          <p class="muted small">此人對別人的感受;點一列看兩人的關係曲線。</p>
          <button class="relrow" v-for="r in a.relationships.slice().sort((x, y) => y.friendship - x.friendship)" :key="r.id" @click="pair(r.id)">
            <Avatar :id="r.id" :size="18" framed/><b>{{ agentName(r.id, r.name) }}</b>
            <span class="small">友誼 <Num class="mono" :v="r.friendship"/></span><span class="small">信任 <Num class="mono" :v="r.trust"/></span><span class="small">衝突 <Num class="mono" :v="r.conflict"/></span>
            <span v-if="r.romance > 0" class="small">戀慕 <Num class="mono" :v="r.romance"/></span>
          </button>
          <p v-if="!a.relationships.length" class="muted small">尚無人際關係。</p>
        </template>
        </div>
      </template>
    </div>
  </aside>`,
};
