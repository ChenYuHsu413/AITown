// That day's replay: re-stages one sim day on the map from its recorded events
// (positions from `arrive`, states from action verbs), with the day's event list
// scrolling in sync. The old whole-run slider survives as the "advanced" entry.
import {markRaw, nextTick} from 'vue';
import {store, nav, getJSON, fetchDay} from '../store.js';
import {VillageMap} from '../pixmap.js';
import {CAST, evText, dialogueSubst, resolve, fmtDay, hhmm, fmtMinute, DAY_MIN, dayOf} from '../names.js';
import {Icon} from '../ui.js';
import {verbIcon} from '../icons.js';

const SEED_LOC = {jiji:'home_a',oula:'home_a',lengyue:'home_a',xixi:'home_a',aisi:'home_a',
  ange:'home_b',azong:'home_b',xue:'home_b',long:'home_b',kuaizheng:'home_b'};
const ACTION_VERBS = {sleep:1, eat:1, work:1, rest:1, idle:1, broke:1, talk_start:1};
const QUIET = new Set(['arrive', 'eat', 'work', 'rest', 'idle', 'sleep', 'day_summary']);
const SPEEDS = [{v: 20, zh: '慢'}, {v: 60, zh: '中'}, {v: 180, zh: '快'}];   // sim-minutes per second

function applyEv(st, ev) {
  if (ev.verb === 'arrive') { if (ev.location) { st.loc[ev.actor] = ev.location; st.act[ev.actor] = 'idle'; } }
  else if (ACTION_VERBS[ev.verb]) st.act[ev.actor] = ev.verb === 'talk_start' ? 'talk' : ev.verb === 'broke' ? 'rest' : ev.verb;
  if (ev.verb === 'talk_start' && ev.target) st.act[ev.target] = 'talk';
  if (ev.verb === 'rain_start') st.rain = true; else if (ev.verb === 'rain_end') st.rain = false;
  else if (ev.verb === 'festival_start') st.fest = ev.location || 'park'; else if (ev.verb === 'festival_end') st.fest = '';
}

export default {
  components: {Icon},
  data: () => ({
    day: 0, runId: '', events: [], digest: '', loading: false, error: '',
    cur: 0, playing: false, speed: 60, quiet: true, follow: true,
    runs: [], current: '', advRun: '', advMinute: 0,
  }),
  setup: () => ({SPEEDS}),
  computed: {
    store: () => store,
    dayStart() { return (this.day - 1) * DAY_MIN; },
    dayEnd() { return this.day === store.todayDay && !this.runId ? store.minute : this.dayStart + DAY_MIN - 1; },
    shown() { return this.events.filter(e => !this.quiet || !QUIET.has(e.verb) || e.tier !== 'ambient'); },
    curIdx() { let i = -1; const s = this.shown; for (let k = 0; k < s.length; k++) { if (s[k].minute <= this.cur) i = k; else break; } return i; },
    marks() { return this.events.filter(e => e.tier === 'major' || e.tier === 'notable')
      .map(e => ({e, pct: (e.minute - this.dayStart) / DAY_MIN * 100})); },
    advRunObj() { return this.runs.find(r => r.id === this.advRun); },
    maxDay() { return this.runId ? dayOf(this.advRunObj ? this.advRunObj.minute_max : 0) : store.todayDay; },
    clock() { return hhmm(this.cur); },
    pct() { return Math.max(0, Math.min(100, (this.cur - this.dayStart) / DAY_MIN * 100)); },
  },
  watch: {
    'store.replay'(r) { if (r) this.open(r.day, r.minute, r.runId); },
    'store.locations'(l) { this.map.buildMap(l); this.render(); },
  },
  async mounted() {
    this.map = markRaw(new VillageMap(this.$refs.stage, {onAgentClick: id => nav.inspect(id), showRevenue: false}));
    this.map.setActive(true);
    if (store.locations.length) this.map.buildMap(store.locations);
    const r = store.replay;
    if (r) this.open(r.day, r.minute, r.runId);
    else this.open(Math.max(1, store.todayDay - 1), null, '');
    try { const d = await getJSON('/api/runs'); this.runs = d.runs.filter(x => x.events > 0 && x.minute_min != null); this.current = d.current;
      this.advRun = d.current; } catch (e) { /* no DB */ }
  },
  beforeUnmount() { this.playing = false; cancelAnimationFrame(this.raf); this.map.destroy(); },
  methods: {
    fmtDay, hhmm, resolve, evText, fmtMinute, verbIcon,
    async open(day, minute, runId) {
      this.playing = false; this.loading = true; this.error = '';
      this.day = day; this.runId = runId || '';
      const page = this.$el;   // turning to that page: a light page-turn on every open
      if (page && page.classList) { page.classList.remove('turn'); void page.offsetWidth; page.classList.add('turn'); }
      const gen = this.gen = (this.gen || 0) + 1;
      try {
        const [d, prev] = await Promise.all([fetchDay(day, this.runId), day > 1 ? fetchDay(day - 1, this.runId) : null]);
        if (gen !== this.gen) return;
        this.events = markRaw(d.events || []);
        this.digest = d.digest ? d.digest.text : (day === store.todayDay ? '今天還在進行中。' : '');
        // the day opens where the previous one left everyone
        const base = {loc: {...SEED_LOC}, act: {}, rain: false, fest: ''};
        for (const ev of (prev && prev.events) || []) applyEv(base, ev);
        this.base = base;
        const beats = (d.beats || []).filter(b => b.minute === minute);
        this.map.setFocus(beats.flatMap(b => [b.actor, b.target]).filter(Boolean));
        this.seek(minute != null ? Math.max(this.dayStart, minute - 20) : this.firstMorning());
        this.advMinute = minute != null ? minute : this.dayStart + 360;
      } catch (e) { this.error = '這一天的紀錄讀取失敗(回放需要資料庫)。'; this.events = []; }
      this.loading = false;
    },
    firstMorning() {   // skip the sleeping hours: start just before the first non-sleep event after 05:00
      const e = this.events.find(x => x.minute >= this.dayStart + 300 && x.verb !== 'sleep');
      return e ? Math.max(this.dayStart, e.minute - 10) : this.dayStart;
    },
    state(T) {
      const st = {loc: {...this.base.loc}, act: {...this.base.act}, rain: this.base.rain, fest: this.base.fest};
      let i = 0;
      while (i < this.events.length && this.events[i].minute <= T) applyEv(st, this.events[i++]);
      this.idx = i; this.st = st;
    },
    render() {
      if (!this.st) return;
      this.map.updateAgents(CAST.map(id => ({id, location: this.st.loc[id] || SEED_LOC[id] || 'home_a', action: this.st.act[id] || 'idle'})));
      const eff = []; if (this.st.rain) eff.push({type: 'rain'}); if (this.st.fest) eff.push({type: 'festival', location: this.st.fest});
      this.map.renderEffects(eff); this.map.updateLandmarks(store.landmarks); this.map.applyDaylight(this.cur);
    },
    seek(T) {
      T = Math.max(this.dayStart, Math.min(Math.floor(T), this.dayEnd));
      this.cur = T; this.state(T); this.map.clearBubbles(); this.render(); this.scrollList();
    },
    toggle() {
      if (this.playing) { this.playing = false; return; }
      if (this.cur >= this.dayEnd) this.seek(this.firstMorning());
      this.playing = true; this.last = performance.now(); this.acc = this.cur;
      this.raf = requestAnimationFrame(this.loop);
    },
    loop(now) {
      if (!this.playing) return;
      const dt = Math.min(0.25, (now - this.last) / 1000); this.last = now;
      let next = this.acc + dt * this.speed; const end = next >= this.dayEnd; if (end) next = this.dayEnd;
      const m = Math.floor(next), fired = [];
      while (this.idx < this.events.length && this.events[this.idx].minute <= m) {
        const ev = this.events[this.idx++]; applyEv(this.st, ev); fired.push(ev);
      }
      this.acc = next;
      if (m !== this.cur) this.cur = m;
      if (fired.length) { this.render(); for (const ev of fired) if (ev.verb === 'say') this.map.bubble(ev.actor, ev.location, dialogueSubst(ev.speech), ev.target); this.scrollList(); }
      else this.map.applyDaylight(m);
      if (end) { this.playing = false; return; }
      this.raf = requestAnimationFrame(this.loop);
    },
    scrollList() {
      if (!this.follow) return;
      nextTick(() => { const box = this.$refs.list; const el = box && box.querySelector('.cur');
        if (el) box.scrollTop = el.offsetTop - box.clientHeight / 2; });
    },
    text(e) { return evText(e); },
    go(d) { if (d >= 1 && d <= this.maxDay) this.open(d, null, this.runId); },
    advGo() {
      const r = this.advRunObj; if (!r) return;
      const m = Math.max(r.minute_min, Math.min(+this.advMinute, r.minute_max));
      this.open(dayOf(m), m + 20, r.id === this.current ? '' : r.id);
    },
    runLabel(r) { return `${(r.started_at || '').slice(0, 16).replace('T', ' ')} · ${r.events} 筆事件` + (r.id === this.current ? '(目前)' : r.archived ? '(封存)' : ''); },
    toChronicle() { if (!this.runId) nav.chronicle(this.day, null); },
  },
  template: `
  <section class="replay turn">
    <div class="replay-main">
      <div class="replay-bar">
        <button class="btn icon-only" :disabled="day <= 1" @click="go(day - 1)" aria-label="前一天" title="前一天"><Icon name="left" :size="16"/></button>
        <h2>{{ fmtDay(day) }}</h2>
        <button class="btn icon-only" :disabled="day >= maxDay" @click="go(day + 1)" aria-label="後一天" title="後一天"><Icon name="right" :size="16"/></button>
        <span class="replay-tag"><Icon name="rewind" :size="12"/>回放</span>
      </div>
      <p class="digest" v-if="digest">{{ resolve(digest) }} <button v-if="!runId" class="link" @click="toChronicle">翻到鎮誌</button></p>
      <div class="mapwrap replaying">
        <div ref="stage" class="map" role="img" aria-label="回放地圖"></div>
        <div v-if="loading" class="map-overlay">讀取中…</div>
        <div v-else-if="error" class="map-overlay">{{ error }}</div>
      </div>
      <div class="transport">
        <button class="play" @click="toggle" :disabled="loading || !!error" :aria-label="playing ? '暫停' : '播放'">
          <Icon :name="playing ? 'pause' : 'play'" :size="18"/></button>
        <span class="t-clock">{{ clock }}</span>
        <div class="scrub" :style="{'--p': pct + '%'}">
          <input type="range" :min="dayStart" :max="dayStart + 1439" :value="Math.floor(cur)" step="1"
            @input="playing = false; seek(+$event.target.value)" aria-label="時間">
          <div class="scrub-marks">
            <button v-for="(m, i) in marks" :key="i" class="mk" :class="m.e.tier" :style="{left: m.pct + '%'}"
              :title="hhmm(m.e.minute) + ' ' + text(m.e)" @click="playing = false; seek(m.e.minute - 5)"></button>
          </div>
          <div class="scrub-hours" aria-hidden="true"><span>00</span><span>06</span><span>12</span><span>18</span><span>24</span></div>
        </div>
        <div class="seg" role="group" aria-label="播放速度">
          <button v-for="s in SPEEDS" :key="s.v" :class="{active: speed === s.v}" @click="speed = s.v">{{ s.zh }}</button>
        </div>
      </div>
      <details class="advanced">
        <summary>進階:整段場次回放</summary>
        <div v-if="runs.length" class="adv-body">
          <label>場次 <select v-model="advRun">
            <option v-for="r in runs" :key="r.id" :value="r.id">{{ runLabel(r) }}</option>
          </select></label>
          <template v-if="advRunObj">
            <input type="range" :min="advRunObj.minute_min" :max="advRunObj.minute_max" step="10" v-model="advMinute" @change="advGo" aria-label="整段時間">
            <span class="muted small">{{ fmtMinute(+advMinute) }} — 放開滑桿即跳到那一刻</span>
          </template>
        </div>
        <p v-else class="muted small">沒有可用的場次(需要資料庫)。</p>
      </details>
    </div>
    <aside class="replay-list">
      <div class="list-head">
        <b>當天事件</b>
        <label class="small"><input type="checkbox" v-model="quiet"> 精簡</label>
        <label class="small"><input type="checkbox" v-model="follow"> 跟隨</label>
      </div>
      <ol ref="list" class="evlist">
        <li v-for="(e, i) in shown" :key="i" :class="[e.tier, e.kind, {cur: i === curIdx, past: i < curIdx}]" @click="playing = false; seek(e.minute)"
          :style="{'--rc': 'var(--r-' + e.actor + ', var(--line))'}">
          <time>{{ hhmm(e.minute) }}</time><Icon :name="verbIcon(e.verb)" :size="13"/><span>{{ text(e) }}</span>
        </li>
        <li v-if="!shown.length && !loading" class="muted">這一天沒有紀錄。</li>
      </ol>
    </aside>
  </section>`,
};
