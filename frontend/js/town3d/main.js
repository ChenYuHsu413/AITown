// /town3d-preview: the 3-D validation slice in the "glass observatory" UI. Reuses the
// store (WebSocket, paced dialogue), the zh display layer and the live column component
// read-only; nothing here changes the main page.
import {createApp, markRaw} from 'vue';
import {store, connect, onTick} from '../store.js';
import {agentName, fmtMinute, phaseOf, dialogueSubst} from '../names.js';
import LiveFeed from '../views/livefeed.js';
import {World} from './world.js';

const SPEC = await (await fetch('/static/js/town3d/slice.json')).json();
const CAST = ['jiji', 'ange', 'oula', 'lengyue', 'azong', 'xixi', 'aisi', 'xue', 'long', 'kuaizheng'];
const rc = id => getComputedStyle(document.documentElement).getPropertyValue(`--r-${id}`).trim() || '#c8b8a8';
const SCRIPT_PATH = ['plaza', 'fountain', 'bench', 'plaza2', 'terrace2', 'plaza'];   // the scripted stroll

createApp({
  components: {LiveFeed},
  data: () => ({labels: [], bubbles: [], mode: '', sweep: 0, fps: 0}),
  computed: {
    store: () => store,
    clock() { return store.ready ? fmtMinute(store.minute) : '— —:—'; },
    phase() { return phaseOf(store.minute).label; },
  },
  watch: {
    'store.minute'(m) { this.world.setTime(m); },
    'store.agents'() { this.cast(); },
    'store.heartbeat'(v) { if (v) this.sweep = v; },
  },
  mounted() {
    this.world = markRaw(new World(this.$refs.stage, SPEC, {onFrame: () => this.track()}));
    this.world.setTime(store.minute || 12 * 60);
    this.off = onTick(evs => { for (const e of evs) if (e.verb === 'say' && this.shown && this.shown.includes(e.actor)) this.say(e); });
    this.scriptT = setInterval(() => this.stroll(), 5200);
    this.fpsT = setInterval(() => { this.fps = this.frames || 0; this.frames = 0; }, 1000);
    this.cast();
  },
  methods: {
    // Who is in the slice: whoever the live town has at the café right now (the owner
    // first). If fewer than two are there, scripted residents stroll the plaza instead.
    cast() {
      if (!this.world) return;
      const here = (store.agents || []).filter(a => a.location === 'cafe').sort((a, b) => (b.id === 'jiji') - (a.id === 'jiji') || CAST.indexOf(a.id) - CAST.indexOf(b.id)).slice(0, 2);
      const live = here.map(a => a.id), fill = CAST.filter(id => !live.includes(id)).slice(0, 2 - live.length);
      this.mode = live.length === 2 ? '居民:即時(此刻在咖啡館的人)' : live.length ? `居民:${live.length} 位即時 + ${fill.length} 位腳本` : '居民:腳本(咖啡館此刻沒有人)';
      const want = [...live, ...fill];
      for (const id of this.shown || []) if (!want.includes(id)) this.world.drop(id);
      here.forEach((a, i) => {
        const spot = a.action === 'work' ? 'door' : a.action === 'eat' ? (i ? 'terrace2' : 'terrace') : i ? 'plaza2' : 'plaza';
        this.world.place(a.id, rc(a.id), SPEC.spots[spot]);
      });
      this.scripted = fill;
      fill.forEach((id, i) => { if (!(this.shown || []).includes(id)) this.world.place(id, rc(id), SPEC.spots[SCRIPT_PATH[i * 2]], true); });
      this.shown = want;
      this.labels = want.map(id => ({id, name: agentName(id), x: -99, y: -99, live: live.includes(id)}));
    },
    stroll() {
      (this.scripted || []).forEach((id, i) => {
        this.step = (this.step || 0) + 1;
        this.world.place(id, rc(id), SPEC.spots[SCRIPT_PATH[(this.step + i * 2) % SCRIPT_PATH.length]]);
      });
    },
    say(e) {
      const b = {key: e.minute + e.actor + Math.random(), id: e.actor, text: dialogueSubst(e.speech), x: -99, y: -99};
      this.bubbles = this.bubbles.filter(x => x.id !== e.actor).concat(b);
      setTimeout(() => { this.bubbles = this.bubbles.filter(x => x !== b); }, 4600);
    },
    track() {   // labels and bubbles follow the residents' heads every frame
      this.frames = (this.frames || 0) + 1;
      const els = this.$refs.overlay ? this.$refs.overlay.children : [];
      for (const el of els) {
        const p = this.world.headScreen(el.dataset.id); if (!p) continue;
        const dy = el.classList.contains('bubble3') ? -22 : 0;
        el.style.transform = `translate(${p.x}px, ${p.y + dy}px) translate(-50%, -100%)`; el.style.opacity = p.visible ? 1 : 0;
      }
    },
  },
  template: `
  <div class="stage" ref="stage"></div>
  <div class="overlay" ref="overlay">
    <div v-for="l in labels" :key="'l' + l.id" class="pill" :data-id="l.id" :style="{'--rc': 'var(--r-' + l.id + ')'}"><i></i>{{ l.name }}</div>
    <div v-for="b in bubbles" :key="b.key" class="bubble3" :data-id="b.id" :style="{'--rc': 'var(--r-' + b.id + ')'}">{{ b.text }}</div>
  </div>
  <header class="glass bar">
    <div class="brand">高柏小鎮<small>3D 預覽 · Townscaper 方向</small></div>
    <div class="time"><span class="num">{{ clock }}</span><span class="tag">{{ phase }}</span></div>
    <div class="mode">{{ mode }}</div>
  </header>
  <aside class="glass column"><LiveFeed/><div v-if="sweep" :key="sweep" class="sweep" aria-hidden="true"></div></aside>
  <footer class="glass hint">拖曳平移 · 滾輪縮放 · <span class="num">{{ fps }}</span> fps</footer>`,
}).mount('#app');

connect();
