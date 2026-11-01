// Live village: watch and listen at once — the 3-D town plus an always-on live column
// (a drawer on narrow screens). Major beats float a glass event card and put a gold-ink
// marker + one light pulse over the people involved.
import {markRaw} from 'vue';
import {store, nav, onTick, getJSON} from '../store.js';
import {TownMap as VillageMap} from '../town3d/townmap.js';
import {evText, beatDetail, dialogueSubst, CARD_KIND, hhmm, agentName} from '../names.js';
import {Icon} from '../ui.js';
import LiveFeed from './livefeed.js';

let cardSeq = 0;
export default {
  components: {Icon, LiveFeed},
  props: {active: Boolean},
  data: () => ({cards: [], drawer: false, unread: 0}),
  computed: { store: () => store },
  watch: {
    'store.locations'(l) { this.map.buildMap(l); this.paint(); },
    'store.agents'() { this.map.updateAgents(store.agents); },
    'store.landmarks'(l) { this.map.updateLandmarks(l); },
    'store.effects'(e) { this.map.renderEffects(e); },
    'store.minute'(m) { this.map.applyDaylight(m); },
    active(v) { this.map.setActive(v); if (v) this.pollEcon(); },
    drawer(v) { if (v) this.unread = 0; },
  },
  mounted() {
    this.map = markRaw(new VillageMap(this.$refs.stage, {onAgentClick: (id, at) => nav.inspect(id, at)}));
    this.map.setActive(this.active);
    if (store.locations.length) { this.map.buildMap(store.locations); this.paint(); }
    this.off = onTick((evs, msg, isSnap) => { if (!isSnap) this.onEvents(evs); });
    this.econTimer = setInterval(() => { if (this.active) this.pollEcon(); }, 5000);
    this.pollEcon();
  },
  beforeUnmount() { this.off && this.off(); clearInterval(this.econTimer); clearTimeout(this.focusT); this.map.destroy(); },
  methods: {
    paint() {
      this.map.updateAgents(store.agents); this.map.updateLandmarks(store.landmarks);
      this.map.renderEffects(store.effects); this.map.applyDaylight(store.minute);
    },
    async pollEcon() { try { this.map.setShopRev(await getJSON('/api/economy')); } catch (e) {} },
    onEvents(evs) {
      if (!this.drawer) this.unread += evs.filter(e => e.verb === 'say' || e.tier !== 'ambient').length;
      for (const ev of evs) {
        if (ev.verb === 'say') this.map.bubble(ev.actor, ev.location, dialogueSubst(ev.speech), ev.target);
        if (ev.verb === 'breakdown' || ev.verb === 'repaired') this.map.setBroken(ev.location, ev.verb === 'breakdown');
        const k = CARD_KIND[ev.verb];
        if (k && ev.tier === 'major') this.pushCard(ev, k);
      }
    },
    pushCard(ev, k) {
      const card = {id: ++cardSeq, ...k, text: evText(ev), detail: beatDetail(ev), time: hhmm(ev.minute),
        minute: ev.minute, actor: ev.actor, target: ev.target};
      this.cards.unshift(card);
      this.map.mark([ev.actor, ev.target].filter(Boolean), k.label);
      if (this.cards.length > 3) this.cards.pop();
      setTimeout(() => this.dismiss(card.id), 15000);
    },
    dismiss(id) { this.cards = this.cards.filter(c => c.id !== id); },
    focus(people) {   // a column entry was picked: highlight its people on the map for a while
      this.map.setFocus(people); clearTimeout(this.focusT);
      if (people.length) this.focusT = setTimeout(() => this.map.setFocus([]), 12000);
    },
    toChronicle(c) { nav.chronicle(store.todayDay, c.minute); },
    agentName, inspect: id => nav.inspect(id),
  },
  template: `
  <section class="village" :class="{'drawer-open': drawer}">
    <div class="mapwrap">
      <div ref="stage" class="map" role="img" aria-label="小鎮地圖(拖曳平移、滾輪縮放、雙擊重設)"></div>
      <div class="cards" aria-live="polite">
        <transition-group name="card">
          <article v-for="c in cards" :key="c.id" class="ev-card gold" :class="c.cls">
            <header><Icon :name="c.icon" :size="17"/><b>{{ c.label }}</b><time>{{ c.time }}</time>
              <button class="x" @click="dismiss(c.id)" aria-label="關閉"><Icon name="x" :size="14"/></button></header>
            <p>{{ c.text }}</p>
            <p v-if="c.detail" class="sub">{{ c.detail }}</p>
            <footer>
              <button class="link" @click="inspect(c.actor)">檢視 {{ agentName(c.actor) }}</button>
              <button class="link" @click="toChronicle(c)">翻到鎮誌</button>
            </footer>
          </article>
        </transition-group>
      </div>
      <p class="map-hint">點居民開角色卡 · 拖曳平移 · 滾輪/雙指縮放 · 雙擊回到全景</p>
    </div>
    <LiveFeed class="live-col" @focus="focus"/>
    <button class="drawer-toggle" @click="drawer = !drawer" :aria-expanded="drawer">
      <Icon :name="drawer ? 'x' : 'say'" :size="16"/>{{ drawer ? '收起' : '即時動態' }}<span v-if="!drawer && unread" class="count">{{ unread > 99 ? '99+' : unread }}</span>
    </button>
  </section>`,
};
