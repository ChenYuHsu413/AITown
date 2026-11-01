// Small shared components.
import {spriteId} from './map.js';
import {agentName} from './names.js';
import {nav} from './store.js';

// A resident's pixel sprite (head-and-shoulders crop of the map sprite).
export const Avatar = {
  props: {id: String, size: {type: Number, default: 22}},
  computed: { href() { return `#spr-${spriteId(this.id)}-f1`; } },
  template: `<svg class="avatar" :width="size" :height="size * 25 / 24" viewBox="-12 -5 24 25" aria-hidden="true"><use :href="href"/></svg>`,
};

// A clickable resident chip -> opens the inspector.
export const Person = {
  components: {Avatar},
  props: {id: String},
  computed: { name() { return agentName(this.id); } },
  methods: { open() { nav.inspect(this.id); } },
  template: `<button class="person" type="button" @click.stop="open" :title="'檢視 ' + name"><Avatar :id="id" :size="18"/><span>{{ name }}</span></button>`,
};

export const AXES = [
  {key: 'friendship', zh: '友誼'},
  {key: 'trust', zh: '信任'},
  {key: 'conflict', zh: '衝突'},
];
export const fmtDelta = v => (v > 0 ? '+' : v < 0 ? '−' : '±') + Math.abs(v).toFixed(1);
