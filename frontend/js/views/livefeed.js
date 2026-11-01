// The village's always-on live column. A conversation is one thread card (both people,
// time, place; alternating bubbles in each speaker's color; a confide / confrontation
// from the same conversation attaches inside). Event cards are secondary; a closed day
// lands as a notebook end-of-day stamp. 精簡 hides everyday (ambient) lines entirely;
// 詳細 shows them faint, each run of consecutive ambient lines folded per kind.
// Events come from the store's single paced source (onTick) -- the same stream, order
// and moment the map's bubbles use.
// Bounded: the stream keeps ALL_MAX items, the DOM shows at most RENDER_MAX rows; while
// the reader has scrolled up, new items wait in a buffer (PENDING_MAX, oldest dropped).
import {nextTick, markRaw} from 'vue';
import {store, onTick, getJSON, setDetail} from '../store.js';
import {evText, beatDetail, hhmm, CARD_KIND, mergeAmbient, resolve, dowOfDay} from '../names.js';
import {Icon} from '../ui.js';
import {verbIcon} from '../icons.js';
import {ThreadIndex, ThreadCard} from '../threads.js';

const ALL_MAX = 400, RENDER_MAX = 120, PENDING_MAX = 300;
let seq = 0;

export default {
  components: {Icon, ThreadCard},
  emits: ['focus'],
  data: () => ({all: [], following: true, pendingCount: 0, selected: 0}),
  computed: {
    store: () => store,
    rows() {
      const out = [];
      if (store.detail === 'lean') { for (const it of this.all) if (it.kind !== 'ambient') out.push(it); }
      else {
        let run = [];
        const flush = () => {
          if (!run.length) return;
          for (const l of mergeAmbient(run.map(r => r.ev))) out.push({id: 'a' + run[0].id + l.verb, kind: 'ambient', time: l.time, text: l.text, people: []});
          run = [];
        };
        for (const it of this.all) { if (it.kind === 'ambient') run.push(it); else { flush(); out.push(it); } }
        flush();
      }
      return out.length > RENDER_MAX ? out.slice(out.length - RENDER_MAX) : out;
    },
  },
  watch: {
    'store.detail'() { this.toBottom(); },
    'store.digestsVersion'() { setTimeout(this.stampDay, 4500, store.todayDay - 1); },   // the digest lands a moment after midnight
  },
  mounted() {
    this.pending = []; this.threads = new ThreadIndex();
    this.off = onTick((evs, msg, isSnap) => {
      if (isSnap && this.all.length) return;   // a reconnect snapshot: we already have the stream
      this.add(evs.map(this.toItem).filter(Boolean));
    });
  },
  beforeUnmount() { this.off && this.off(); },
  methods: {
    setDetail, dowOfDay,
    toItem(ev) {
      const r = this.threads.add(ev);
      if (r) return r.created ? markRaw({id: ++seq, kind: 'thread', t: r.thread, people: [r.thread.a, r.thread.b]}) : (this.toBottom(), null);
      const base = {id: ++seq, minute: ev.minute, time: hhmm(ev.minute), people: [ev.actor, ev.target].filter(Boolean), actor: ev.actor, verb: ev.verb};
      const tier = ev.tier === 'major' ? 'major' : ev.tier === 'notable' ? 'notable' : 'ambient';
      if (tier === 'ambient') return markRaw({...base, kind: 'ambient', ev});
      const k = CARD_KIND[ev.verb];
      return markRaw({...base, kind: tier, text: evText(ev), detail: beatDetail(ev), label: k ? k.label : '', icon: verbIcon(ev.verb)});
    },
    trim(list) {
      if (list.length <= ALL_MAX) return list;
      const kept = list.slice(list.length - ALL_MAX);
      const oldest = kept.find(i => i.kind === 'thread');
      if (oldest) this.threads.prune(oldest.t.init);   // forget threads no longer on screen
      return kept;
    },
    add(list) {
      if (!list.length) return;
      if (!this.following) {
        this.pending.push(...list);
        if (this.pending.length > PENDING_MAX) this.pending.splice(0, this.pending.length - PENDING_MAX);
        this.pendingCount += list.filter(i => store.detail === 'full' || i.kind !== 'ambient').length;
        return;
      }
      this.all = this.trim(this.all.concat(list));
      this.toBottom();
    },
    async stampDay(day) {
      if (day < 1) return;
      try {
        const d = await getJSON(`/api/digests?day_from=${day}&day_to=${day}`);
        const g = (d.digests || [])[0]; if (!g) return;
        this.add([markRaw({id: ++seq, kind: 'stamp', day, text: resolve(g.text), people: []})]);
      } catch (e) { /* no DB: no stamp */ }
    },
    toBottom() { if (this.following) nextTick(() => { const el = this.$refs.list; if (el) el.scrollTop = el.scrollHeight; }); },
    onScroll() {
      const el = this.$refs.list; if (!el) return;
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 28;
      if (atBottom && !this.following) this.resume();
      else if (!atBottom && this.following) this.following = false;
    },
    resume() {
      this.following = true;
      const list = this.pending; this.pending = []; this.pendingCount = 0;
      this.all = this.trim(this.all.concat(list));
      this.toBottom();
    },
    pick(it) {
      if (!it.people.length) return;
      this.selected = this.selected === it.id ? 0 : it.id;
      this.$emit('focus', this.selected ? it.people : []);
    },
  },
  template: `
  <section class="live" aria-label="即時動態">
    <header class="live-head"><b>即時動態</b>
      <div class="seg small" role="group" aria-label="顯示層級">
        <button :class="{active: store.detail === 'lean'}" @click="setDetail('lean')" title="對話與大小事件,不含日常瑣事">精簡</button>
        <button :class="{active: store.detail === 'full'}" @click="setDetail('full')" title="連日常瑣事也顯示(合併成一行)">詳細</button>
      </div>
    </header>
    <ol ref="list" class="live-list" @scroll.passive="onScroll">
      <li v-for="it in rows" :key="it.id" :class="['li', it.kind, {sel: selected === it.id}]" @click="pick(it)">
        <ThreadCard v-if="it.kind === 'thread'" :t="it.t" compact/>
        <template v-else-if="it.kind === 'ambient'">
          <time>{{ it.time }}</time><span class="amb">{{ it.text }}</span>
        </template>
        <div v-else-if="it.kind === 'stamp'" class="day-stamp">
          <span class="ds-mark">第 {{ it.day }} 天<br><small>{{ dowOfDay(it.day) }} · 收筆</small></span>
          <p>{{ it.text }}</p>
        </div>
        <div v-else class="ev-note" :style="{'--rc': 'var(--r-' + it.actor + ')'}">
          <div class="note-head"><Icon :name="it.icon" :size="14"/><b v-if="it.label">{{ it.label }}</b><span v-else class="muted">{{ it.kind === 'notable' ? '小事' : '' }}</span><time>{{ it.time }}</time></div>
          <p>{{ it.text }}</p>
          <p v-if="it.detail" class="muted small">{{ it.detail }}</p>
        </div>
      </li>
      <li v-if="!rows.length" class="li empty muted small">{{ store.detail === 'lean' ? '還沒有人說話,也還沒有值得記下的事…' : '等待小鎮的下一個動靜…' }}</li>
    </ol>
    <button v-if="!following" class="pill" @click="resume"><Icon name="down" :size="13"/>{{ pendingCount ? pendingCount + ' 則新訊息' : '回到最新' }}</button>
  </section>`,
};
