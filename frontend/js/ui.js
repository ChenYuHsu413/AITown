// Small shared components.
import {avatarURL} from './pixelart.js';
import {agentName} from './names.js';
import {nav} from './store.js';
import {Icon} from './icons.js';

export const REDUCE_MOTION = matchMedia('(prefers-reduced-motion: reduce)').matches;
// The resolved hex of a token (for math, e.g. comparing two residents' colors).
export const tokenHex = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// A resident's pixel portrait: the same generated character the map draws (head and
// shoulders of the standing front frame), optionally framed in their identity color.
export const Avatar = {
  props: {id: String, size: {type: Number, default: 22}, framed: Boolean},
  computed: { src() { return avatarURL(this.id); } },
  template: `<span class="avatar" :class="{framed}" :style="framed ? {'--rc': 'var(--r-' + id + ')'} : null">
    <img :src="src" :width="size" :height="size" alt="" aria-hidden="true"></span>`,
};

// A clickable resident chip -> opens the inspector.
export const Person = {
  components: {Avatar},
  props: {id: String},
  computed: { name() { return agentName(this.id); } },
  methods: { open() { nav.inspect(this.id); } },
  template: `<button class="person" type="button" @click.stop="open" :title="'檢視 ' + name" :style="{'--rc': 'var(--r-' + id + ')'}">
    <Avatar :id="id" :size="18"/><span>{{ name }}</span></button>`,
};

// A number that tweens to its new value (instant under reduced motion).
export const Num = {
  props: {v: Number, d: {type: Number, default: 0}},
  data() { return {shown: this.v ?? 0}; },
  watch: {
    v(to) {
      if (to == null) return;
      cancelAnimationFrame(this.raf);
      if (REDUCE_MOTION) { this.shown = to; return; }
      const from = this.shown, t0 = performance.now(), dur = 420;
      const step = now => { const k = Math.min(1, (now - t0) / dur), e = 1 - (1 - k) ** 3;
        this.shown = from + (to - from) * e; if (k < 1) this.raf = requestAnimationFrame(step); };
      this.raf = requestAnimationFrame(step);
    },
  },
  beforeUnmount() { cancelAnimationFrame(this.raf); },
  template: `<span class="num">{{ (shown ?? 0).toFixed(d) }}</span>`,
};

export {Icon};
export const AXES = [
  {key: 'friendship', zh: '友誼'},
  {key: 'trust', zh: '信任'},
  {key: 'conflict', zh: '衝突'},
];
export const fmtDelta = v => (v > 0 ? '+' : v < 0 ? '−' : '±') + Math.abs(v).toFixed(1);
