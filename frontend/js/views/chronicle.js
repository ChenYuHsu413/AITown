// 鎮誌 — the chronicle as a town notebook: a date stamp and one digest line per sim
// day, the day's major/notable beats as sticky-note cards that unfold into the
// dialogue around them, the relationship moves, and the people. Today (in progress)
// sits on top, fed by the live socket. Older days page in as you scroll.
import {nextTick} from 'vue';
import {store, nav, getJSON, fetchDay, setDetail} from '../store.js';
import {resolve, evText, beatShort, beatDetail, agentName, dowOfDay, hhmm, mergeAmbient} from '../names.js';
import {Person, AXES, fmtDelta, Icon, Num} from '../ui.js';
import {verbIcon} from '../icons.js';
import {buildThreads, ThreadCard, pairOf} from '../threads.js';

const PAGE = 30;
const CHAPTER = new Set(['chapter_closed', 'chapter_started']);
const sameBeat = (e, r) => e.minute === r.minute && e.verb === r.verb && e.actor === r.actor && (e.target || '') === (r.target || '');

const BeatDetail = {
  components: {Person, Icon, Num, ThreadCard},
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
    threads() {   // the conversation this beat belongs to (same pair, same initiation minute), else the nearest ones
      if (!this.data) return [];
      const all = buildThreads(this.data.events), m = this.beat.minute;
      if (this.pair) {
        const key = pairOf(this.beat), own = all.filter(t => pairOf({actor: t.a, target: t.b}) === key);
        const exact = own.filter(t => t.init === m);
        if (exact.length) return exact;
        return own.filter(t => Math.abs(t.init - m) <= 180).slice(0, 2);
      }
      const P = new Set(this.people);
      return all.filter(t => (P.has(t.a) || P.has(t.b)) && Math.abs(t.init - m) <= 120).slice(0, 2);
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
    agentName, hhmm, fmtDelta,
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
      <ThreadCard v-for="t in threads" :key="t.key" :t="t"/>
      <p v-if="!threads.length" class="muted small">這附近沒有相關的對話紀錄。</p>
      <div class="lab">關係變化 <small>{{ isToday ? '(今天尚未結算)' : '(當天結束時)' }}</small></div>
      <table v-if="deltas.length" class="deltas">
        <tr v-for="d in deltas" :key="d.a + d.b">
          <th>{{ agentName(d.a) }} → {{ agentName(d.b) }}</th>
          <td v-for="ax in AXES" :key="ax.key" :class="cls(d['d_' + ax.key]) + ' ax-' + ax.key">
            {{ ax.zh }} <Num :v="d[ax.key]"/> <small>{{ fmtDelta(d['d_' + ax.key]) }}</small>
          </td>
        </tr>
      </table>
      <p v-else class="muted small">{{ isToday ? '關係在一天結束時才結算。' : '這天他們之間的關係沒有明顯變動。' }}</p>
    </template>
    <p v-else class="muted small">讀取中…</p>
    <div class="bd-actions">
      <button class="btn" @click="replay"><Icon name="rewind" :size="14"/>回放這一刻</button>
      <button v-if="pair" class="btn" @click="curve"><Icon name="chart" :size="14"/>兩人的關係曲線</button>
    </div>
  </div>`,
};

const BeatNode = {
  components: {BeatDetail, Icon},
  props: {day: Number, beat: Object, open: Boolean},
  emits: ['toggle'],
  computed: {
    s() { return beatShort(this.beat); },
    icon() { return verbIcon(this.beat.verb); },
    chapter() { return CHAPTER.has(this.beat.verb); },
  },
  methods: {hhmm},
  template: `
  <li class="note" :class="[beat.tier, {open, chapter}]" :data-minute="beat.minute" :style="{'--rc': 'var(--r-' + beat.actor + ')'}">
    <button class="note-head" type="button" @click="$emit('toggle')" :aria-expanded="open">
      <Icon :name="icon" :size="15"/><time>{{ hhmm(beat.minute) }}</time>
      <span class="what">{{ s.what }}</span><span class="who">{{ s.who }}</span><Icon class="caret" name="right" :size="13"/>
    </button>
    <transition name="unfold"><BeatDetail v-if="open" :class="{pageturn: chapter}" :day="day" :beat="beat"/></transition>
  </li>`,
};

// 詳細 mode: every conversation of the day, one thread card each.
const DayThreads = {
  components: {ThreadCard},
  props: {day: Number},
  data: () => ({threads: null, open: false}),
  async created() { try { this.threads = buildThreads((await fetchDay(this.day)).events); } catch (e) { this.threads = []; } },
  // cards are built only while the list is open, so a page of 30 days stays light
  template: `<details class="day-threads" @toggle="open = $event.target.open"><summary class="lab">當天的對話({{ threads ? threads.length : '…' }})</summary>
    <template v-if="open"><ThreadCard v-for="t in threads || []" :key="t.key" :t="t" compact/></template></details>`,
};

// 詳細 mode: the day's everyday (ambient) lines, folded per kind within 3-hour windows.
const DayAmbient = {
  props: {day: Number},
  data: () => ({lines: null}),
  async created() {
    try {
      const d = await fetchDay(this.day), win = new Map();   // fold per 3-hour window of the day
      for (const e of d.events) if (e.tier === 'ambient' && e.verb !== 'say') {
        const w = Math.floor((e.minute % 1440) / 180); (win.get(w) || win.set(w, []).get(w)).push(e); }
      this.lines = [...win.keys()].sort((x, y) => x - y).flatMap(w => mergeAmbient(win.get(w)));
    } catch (e) { this.lines = []; }
  },
  template: `<details class="day-amb" open><summary class="lab">當天的日常</summary>
    <p v-if="!lines" class="muted small">讀取中…</p>
    <ol v-else><li v-for="(l, i) in lines" :key="i"><time>{{ l.time }}</time><span>{{ l.text }}</span></li></ol></details>`,
};

const Stamp = {
  props: {day: Number, live: Boolean},
  methods: {dowOfDay},
  template: `<div class="stamp" :class="{live}"><span class="t">{{ live ? '今天 · 第' : '第' }}</span><span class="d">{{ day }}</span><span class="w">天 · {{ dowOfDay(day) }}</span></div>`,
};

export default {
  components: {BeatNode, Stamp, Icon, DayAmbient, DayThreads},
  data: () => ({digests: [], loading: true, error: '', open: {}, newestFirst: true, limit: PAGE}),
  computed: {
    store: () => store,
    past() { return this.digests.filter(d => d.day < store.todayDay); },
    rows() {
      const p = this.newestFirst ? this.past.slice().reverse() : this.past;
      return p.slice(0, this.limit);
    },
    more() { return this.past.length > this.limit; },
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
  mounted() {
    this.io = new IntersectionObserver(es => { if (es.some(e => e.isIntersecting) && this.more) this.limit += PAGE; }, {rootMargin: '400px'});
    this.$nextTick(() => this.$refs.sentinel && this.io.observe(this.$refs.sentinel));
  },
  beforeUnmount() { this.io && this.io.disconnect(); },
  methods: {
    resolve,
    async load() {
      try { const d = await getJSON('/api/digests'); this.digests = d.digests || []; this.error = ''; }
      catch (e) { this.error = '鎮誌讀取失敗:' + e.message; }
      this.loading = false;
    },
    key: (day, b) => `${day}|${b.minute}|${b.verb}|${b.actor}|${b.target || ''}`,
    isOpen(day, b) { const k = this.key(day, b); return k in this.open ? this.open[k] : store.detail === 'full'; },
    toggle(day, b) { const k = this.key(day, b); this.open[k] = !this.isOpen(day, b); },
    setDetail,
    async focus({day, minute}) {
      if (!this.digests.length) await this.load();
      if (day < store.todayDay) {   // make sure the page holding that day is rendered
        const idx = (this.newestFirst ? this.past.slice().reverse() : this.past).findIndex(d => d.day === day);
        if (idx >= this.limit) this.limit = idx + PAGE;
      }
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
    flip() { this.newestFirst = !this.newestFirst; this.limit = PAGE; },
  },
  template: `
  <section class="chronicle view-scroll">
    <header class="view-head">
      <h1>鎮誌</h1>
      <p class="muted">小鎮每天留下一句話。翻開當天的便條,看那時說了什麼、誰和誰的關係變了。</p>
      <div class="seg small" role="group" aria-label="顯示層級">
        <button :class="{active: store.detail === 'lean'}" @click="setDetail('lean'); open = {}">精簡</button>
        <button :class="{active: store.detail === 'full'}" @click="setDetail('full'); open = {}">詳細</button>
      </div>
      <button class="btn small" @click="flip">{{ newestFirst ? '新的在上' : '舊的在上' }}</button>
    </header>
    <p v-if="error" class="muted">{{ error }}</p>
    <ol class="journal">
      <li class="entry today" :data-day="store.todayDay" v-if="store.todayDay">
        <Stamp :day="store.todayDay" live/>
        <div class="entry-body">
          <div class="entry-head">
            <p class="digest muted">今天還沒過完,一句話的總結會在午夜寫下。</p>
            <button class="btn small ghost" @click="replayDay(store.todayDay)"><Icon name="rewind" :size="13"/>回放今天</button>
          </div>
          <transition-group v-if="todayBeats.length" tag="ul" name="row" class="notes">
            <BeatNode v-for="b in todayBeats" :key="key(store.todayDay, b)" :day="store.todayDay" :beat="b"
              :open="isOpen(store.todayDay, b)" @toggle="toggle(store.todayDay, b)"/>
          </transition-group>
          <p v-else class="muted small">今天到目前為止還沒有值得記下的事。</p>
        </div>
      </li>
      <transition-group name="row">
        <li v-for="d in rows" :key="d.day" class="entry" :data-day="d.day">
          <Stamp :day="d.day"/>
          <div class="entry-body">
            <div class="entry-head">
              <p class="digest">{{ resolve(d.text) }}</p>
              <button class="btn small ghost" @click="replayDay(d.day)"><Icon name="rewind" :size="13"/>回放</button>
            </div>
            <ul class="notes" v-if="d.refs.length">
              <BeatNode v-for="b in d.refs" :key="key(d.day, b)" :day="d.day" :beat="b"
                :open="isOpen(d.day, b)" @toggle="toggle(d.day, b)"/>
            </ul>
            <DayThreads v-if="store.detail === 'full'" :day="d.day"/>
            <DayAmbient v-if="store.detail === 'full'" :day="d.day"/>
          </div>
        </li>
      </transition-group>
      <li v-if="!loading && !rows.length && !error" class="entry"><p class="muted">還沒有任何結束的一天。</p></li>
    </ol>
    <div ref="sentinel" class="sentinel">{{ more ? '往前翻…' : (rows.length ? '— 第一頁 —' : '') }}</div>
  </section>`,
};
