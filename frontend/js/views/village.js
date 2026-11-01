// Live village: the ported pixel map fed by the socket, with speech bubbles and an
// event card popping over the map for each major beat.
import {markRaw} from 'vue';
import {store, nav, onTick, getJSON} from '../store.js';
import {VillageMap} from '../map.js';
import {evText, beatDetail, dialogueSubst, CARD_KIND, hhmm, agentName} from '../names.js';

let cardSeq = 0;
export default {
  props: {active: Boolean},
  data: () => ({cards: []}),
  computed: { store: () => store },
  watch: {
    'store.locations'(l) { this.map.buildMap(l); this.paint(); },
    'store.agents'() { this.map.updateAgents(store.agents); },
    'store.landmarks'(l) { this.map.updateLandmarks(l); },
    'store.effects'(e) { this.map.renderEffects(e); },
    'store.minute'(m) { this.map.applyDaylight(m); },
    active(v) { if (v) this.pollEcon(); },
  },
  mounted() {
    this.map = markRaw(new VillageMap(this.$refs.svg, {onAgentClick: id => nav.inspect(id)}));
    if (store.locations.length) { this.map.buildMap(store.locations); this.paint(); }
    this.off = onTick(evs => this.onEvents(evs));
    this.econTimer = setInterval(() => { if (this.active) this.pollEcon(); }, 5000);
    this.pollEcon();
  },
  beforeUnmount() { this.off && this.off(); clearInterval(this.econTimer); this.map.destroy(); },
  methods: {
    paint() {
      this.map.updateAgents(store.agents); this.map.updateLandmarks(store.landmarks);
      this.map.renderEffects(store.effects); this.map.applyDaylight(store.minute);
    },
    async pollEcon() { try { this.map.setShopRev(await getJSON('/api/economy')); } catch (e) {} },
    onEvents(evs) {
      for (const ev of evs) {
        if (ev.verb === 'say') this.map.bubble(ev.actor, ev.location, dialogueSubst(ev.speech));
        if (ev.verb === 'breakdown' || ev.verb === 'repaired') this.map.setBroken(ev.location, ev.verb === 'breakdown');
        const k = CARD_KIND[ev.verb];
        if (k && ev.tier === 'major') this.pushCard(ev, k);
      }
    },
    pushCard(ev, k) {
      const card = {id: ++cardSeq, ...k, text: evText(ev), detail: beatDetail(ev), time: hhmm(ev.minute),
        minute: ev.minute, actor: ev.actor, target: ev.target};
      this.cards.unshift(card);
      if (this.cards.length > 4) this.cards.pop();
      setTimeout(() => this.dismiss(card.id), 15000);
    },
    dismiss(id) { this.cards = this.cards.filter(c => c.id !== id); },
    toChronicle(c) { nav.chronicle(store.todayDay, c.minute); },
    agentName, inspect: id => nav.inspect(id),
  },
  template: `
  <section class="village">
    <div class="mapwrap">
      <svg ref="svg" class="map" role="img" aria-label="小鎮地圖"></svg>
      <div class="cards" aria-live="polite">
        <transition-group name="card">
          <article v-for="c in cards" :key="c.id" class="ev-card" :class="c.cls">
            <header><span class="icon">{{ c.icon }}</span><b>{{ c.label }}</b><time>{{ c.time }}</time>
              <button class="x" @click="dismiss(c.id)" aria-label="關閉">✕</button></header>
            <p>{{ c.text }}</p>
            <p v-if="c.detail" class="sub">{{ c.detail }}</p>
            <footer>
              <button class="link" @click="inspect(c.actor)">檢視 {{ agentName(c.actor) }}</button>
              <button class="link" @click="toChronicle(c)">在大事記中查看</button>
            </footer>
          </article>
        </transition-group>
      </div>
      <p class="map-hint">點擊居民開啟檢視器</p>
    </div>
  </section>`,
};
