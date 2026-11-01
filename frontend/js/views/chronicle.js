// The chronicle: one row per sim day (its digest sentence), each day's major/notable
// beats as expandable nodes. Today (in progress) sits on top, fed by the live socket.
import {nextTick} from 'vue';
import {store, nav, getJSON, fetchDay} from '../store.js';
import {resolve, evText, beatShort, beatDetail, dialogueSubst, agentName, fmtDay, hhmm} from '../names.js';
import {Person, AXES, fmtDelta} from '../ui.js';

const sameBeat = (e, r) => e.minute === r.minute && e.verb === r.verb && e.actor === r.actor && (e.target || '') === (r.target || '');

// What a node expands into: the beat's sentence, the dialogue around it between the
// people involved, and the relationship moves the day closed with for those people.
const BeatDetail = {
  components: {Person},
  props: {day: Number, beat: Object},
  data: () => ({data: null, error: ''}),
  async created() {
    try { this.data = await fetchDay(this.day); } catch (e) { this.error = '這一天的細節讀取失敗(需要資料庫)。'; }
  },
  computed: {
    ev() { return (this.data && this.data.beats.find(e => sameBeat(e, this.beat))) || this.beat; },
    people() { return [this.beat.actor, this.beat.target].filter(Boolean); },
    pair() { return this.people.length === 2; },
    text() { return evText(this.ev); },
    detail() { return beatDetail(this.ev); },
    lines() {
      if (!this.data) return [];
      const m = this.beat.minute, P = new Set(this.people);
      const pick = this.pair
        ? this.data.dialogue.filter(x => P.has(x.actor) && P.has(x.target) && x.minute >= m - 120 && x.minute <= m + 180)
        : this.data.dialogue.filter(x => (P.has(x.actor) || P.has(x.target)) && Math.abs(x.minute - m) <= 90);
      // keep the lines nearest the beat, in order
      return pick.sort((a, b) => Math.abs(a.minute - m) - Math.abs(b.minute - m)).slice(0, 14).sort((a, b) => a.minute - b.minute);
    },
    deltas() {
      if (!this.data) return [];
      const P = new Set(this.people);
      const rows = this.pair
        ? this.data.deltas.filter(d => P.has(d.a) && P.has(d.b))
        : this.data.deltas.filter(d => P.has(d.a) || P.has(d.b));
      const mag = d => Math.abs(d.d_friendship) + Math.abs(d.d_trust) + Math.abs(d.d_conflict);
      return rows.sort((x, y) => mag(y) - mag(x)).slice(0, 8);
    },
    isToday() { return this.day === store.todayDay; },
  },
  methods: {
    agentName, hhmm, fmtDelta, say: x => dialogueSubst(x.speech),
    replay() { nav.replay(this.day, this.beat.minute); },
    curve() { nav.pair(this.people[0], this.people[1]); },
    cls: v => v > 0 ? 'up' : v < 0 ? 'down' : '',
  },
  setup: () => ({AXES}),
  template: `
  <div class="beat-detail">
    <p class="bd-text">{{ text }}</p>
    <p v-if="detail" class="bd-sub">{{ detail }}</p>
    <div class="bd-people"><span class="lab">相關居民</span><Person v-for="p in people" :key="p" :id="p"/></div>
    <p v-if="error" class="muted">{{ error }}</p>
    <template v-else-if="data">
      <div class="lab">對話</div>
      <div v-if="lines.length" class="convo">
        <div v-for="x in lines" :key="x.minute + x.actor" class="line" :class="{near: Math.abs(x.minute - beat.minute) <= 2}">
          <time>{{ hhmm(x.minute) }}</time><b>{{ agentName(x.actor) }}</b><span>{{ say(x) }}</span>
        </div>
      </div>
      <p v-else class="muted">這附近沒有相關的對話紀錄。</p>
      <div class="lab">關係變化 <small>{{ isToday ? '(今天尚未結算)' : '(當天結束時)' }}</small></div>
      <table v-if="deltas.length" class="deltas">
        <tr v-for="d in deltas" :key="d.a + d.b">
          <th>{{ agentName(d.a) }} → {{ agentName(d.b) }}</th>
          <td v-for="ax in AXES" :key="ax.key" :class="cls(d['d_' + ax.key]) + ' ax-' + ax.key">
            {{ ax.zh }} {{ Math.round(d[ax.key]) }} <small>{{ fmtDelta(d['d_' + ax.key]) }}</small>
          </td>
        </tr>
      </table>
      <p v-else class="muted">{{ isToday ? '關係在一天結束時才結算。' : '這天他們之間的關係沒有明顯變動。' }}</p>
    </template>
    <p v-else class="muted">讀取中…</p>
    <div class="bd-actions">
      <button class="btn" @click="replay">⏪ 回放這一刻</button>
      <button v-if="pair" class="btn" @click="curve">📈 兩人的關係曲線</button>
    </div>
  </div>`,
};

const BeatNode = {
  components: {BeatDetail},
  props: {day: Number, beat: Object, open: Boolean},
  emits: ['toggle'],
  computed: { s() { return beatShort(this.beat); } },
  methods: {hhmm},
  template: `
  <li class="beat" :class="[beat.tier, {open}]" :data-minute="beat.minute">
    <button class="beat-head" type="button" @click="$emit('toggle')" :aria-expanded="open">
      <span class="dot"></span><time>{{ hhmm(beat.minute) }}</time>
      <span class="what">{{ s.what }}</span><span class="who">{{ s.who }}</span><span class="caret">▸</span>
    </button>
    <BeatDetail v-if="open" :day="day" :beat="beat"/>
  </li>`,
};

export default {
  components: {BeatNode},
  data: () => ({digests: [], loading: true, error: '', open: {}, newestFirst: true}),
  computed: {
    store: () => store,
    rows() {
      const past = this.digests.filter(d => d.day < store.todayDay);
      return this.newestFirst ? past.slice().reverse() : past;
    },
    todayBeats() { return store.today.slice().sort((a, b) => a.minute - b.minute); },
  },
  watch: {
    'store.digestsVersion'() { this.load(); setTimeout(this.load, 4000); },   // the digest lands a moment after midnight
    'store.chronFocus'(f) { if (f) this.focus(f); },
  },
  async created() {
    await this.load();
    if (store.chronFocus) this.focus(store.chronFocus);
  },
  methods: {
    resolve, fmtDay,
    async load() {
      try { const d = await getJSON('/api/digests'); this.digests = d.digests || []; this.error = ''; }
      catch (e) { this.error = '大事記讀取失敗:' + e.message; }
      this.loading = false;
    },
    key: (day, b) => `${day}|${b.minute}|${b.verb}|${b.actor}|${b.target || ''}`,
    toggle(day, b) { const k = this.key(day, b); this.open[k] = !this.open[k]; },
    async focus({day, minute}) {
      if (!this.digests.length) await this.load();
      const row = day === store.todayDay ? {refs: this.todayBeats} : this.digests.find(d => d.day === day);
      let target = null;
      if (row && minute != null) {
        const b = row.refs.find(r => r.minute === minute) || null;
        if (b) { this.open[this.key(day, b)] = true; target = b; }
      }
      await nextTick();
      const el = this.$el.querySelector(target ? `[data-day="${day}"] [data-minute="${target.minute}"]` : `[data-day="${day}"]`);
      if (el) { el.scrollIntoView({block: 'center', behavior: 'smooth'}); el.classList.add('flash'); setTimeout(() => el.classList.remove('flash'), 1600); }
    },
    replayDay(day) { nav.replay(day, null); },
  },
  template: `
  <section class="chronicle view-scroll">
    <header class="view-head">
      <h1>大事記</h1>
      <p class="muted">小鎮每天留下一句話。點開當天的事件,看看那時說了什麼、誰和誰的關係變了。</p>
      <button class="btn small" @click="newestFirst = !newestFirst">{{ newestFirst ? '↓ 新的在上' : '↑ 舊的在上' }}</button>
    </header>
    <p v-if="error" class="muted">{{ error }}</p>
    <ol class="timeline" :class="{asc: !newestFirst}">
      <li class="day today" :data-day="store.todayDay" v-if="store.todayDay">
        <div class="day-head">
          <h2>{{ fmtDay(store.todayDay) }} <span class="live-tag">進行中</span></h2>
          <button class="btn small" @click="replayDay(store.todayDay)">⏪ 回放今天</button>
        </div>
        <p class="digest muted">今天還沒過完,一句話的總結會在午夜寫下。</p>
        <ul class="beats" v-if="todayBeats.length">
          <BeatNode v-for="b in todayBeats" :key="key(store.todayDay, b)" :day="store.todayDay" :beat="b"
            :open="!!open[key(store.todayDay, b)]" @toggle="toggle(store.todayDay, b)"/>
        </ul>
        <p v-else class="muted small">今天到目前為止還沒有值得記下的事。</p>
      </li>
      <li v-for="d in rows" :key="d.day" class="day" :data-day="d.day">
        <div class="day-head">
          <h2>{{ fmtDay(d.day) }}</h2>
          <button class="btn small" @click="replayDay(d.day)">⏪ 回放這一天</button>
        </div>
        <p class="digest">{{ resolve(d.text) }}</p>
        <ul class="beats" v-if="d.refs.length">
          <BeatNode v-for="b in d.refs" :key="key(d.day, b)" :day="d.day" :beat="b"
            :open="!!open[key(d.day, b)]" @toggle="toggle(d.day, b)"/>
        </ul>
      </li>
      <li v-if="!loading && !rows.length && !error" class="day"><p class="muted">還沒有任何結束的一天。</p></li>
    </ol>
  </section>`,
};
