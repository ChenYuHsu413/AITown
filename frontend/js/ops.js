// Operator tools: the god / admin endpoints, behind a small button in the corner.
// Maintenance, not a showcase -- pick an endpoint, edit its JSON, send, read the reply.
// Destructive ones are prefilled as dry runs (the server's own default too).

import {Icon} from './icons.js';

const OPS = [
  {path: '/api/rumors', zh: '種謠言', body: {agent_id: 'oula', text: '聽說渡口市場下個月要收攤了。', subject: '', sentiment: -0.3}},
  {path: '/api/secrets', zh: '植入秘密', body: {owner: 'xue', text: '我其實很怕一個人過年。', sensitivity: 0.5}},
  {path: '/api/world-event', zh: '天氣 / 慶典', body: {type: 'rain', duration_minutes: 180}},
  {path: '/api/break-equipment', zh: '設備故障', body: {location: 'cafe'}},
  {path: '/god/seed_wish', zh: '植入願望(預設只驗證)', body: {agent_id: 'xixi', scale: 'minor', title: '…', statement: '我想…',
    motivation: '…', requirements: [{kind: 'location_visits', target: 'market', threshold: 2}], expires_on: 0,
    provenance: [], validate_only: true}},
  {path: '/god/close_chapter', zh: '結束追逐章節', body: {agent_id: 'aisi', outcome: 'completed', reason: '操作者手動收束'}},
  {path: '/api/admin/translations/clear', zh: '清翻譯快取(預設 dry-run)', body: {dry_run: true, lang: '', contains: '', gave_up_only: false}},
  {path: '/api/admin/prune-beliefs', zh: '清垃圾印象(預設 dry-run)', body: {dry_run: true}},
  {path: '/api/admin/resolve-stale-secrets', zh: '釋懷過期秘密(預設 dry-run)', body: {dry_run: true, secret_ids: []}},
  {path: '/api/admin/rewrite-stale-goals', zh: '改寫過期目標(預設 dry-run)', body: {dry_run: true}},
  {path: '/api/admin/resolve-completed-landmark-worries', zh: '作品完成後釋懷(預設 dry-run)', body: {dry_run: true}},
];
const READS = ['/api/admin/archives', '/api/admin/translations?limit=20', '/api/secrets', '/api/rumors', '/api/usage', '/api/runs'];

export default {
  components: {Icon},
  data: () => ({open: false, i: 0, body: JSON.stringify(OPS[0].body, null, 2), out: '', busy: false}),
  computed: { ops: () => OPS, reads: () => READS },
  watch: { i(v) { this.body = JSON.stringify(OPS[v].body, null, 2); this.out = ''; } },
  methods: {
    async send() {
      let payload;
      try { payload = JSON.parse(this.body); } catch (e) { this.out = 'JSON 格式錯誤:' + e.message; return; }
      this.busy = true;
      try {
        const r = await fetch(OPS[this.i].path, {method: 'POST', headers: {'Content-Type': 'application/json'},
                                                   body: JSON.stringify(payload)});
        this.out = `HTTP ${r.status}\n` + JSON.stringify(await r.json(), null, 2);
      } catch (e) { this.out = '失敗:' + e.message; }
      this.busy = false;
    },
    async read(url) {
      try { const r = await fetch(url); this.out = `GET ${url} → HTTP ${r.status}\n` + JSON.stringify(await r.json(), null, 2); }
      catch (e) { this.out = '失敗:' + e.message; }
    },
  },
  template: `
  <button class="ops-toggle" @click="open = !open" title="維運工具" aria-label="維運工具"><Icon name="wrench" :size="15"/></button>
  <section v-if="open" class="ops" aria-label="維運工具">
    <div class="ops-head"><b>維運工具</b><span class="muted small">操作者用;破壞性操作預設 dry-run</span>
      <button class="x" @click="open = false" aria-label="關閉"><Icon name="x" :size="14"/></button></div>
    <select v-model.number="i"><option v-for="(o, k) in ops" :key="o.path" :value="k">{{ o.zh }} — POST {{ o.path }}</option></select>
    <textarea v-model="body" rows="9" spellcheck="false"></textarea>
    <button class="btn" :disabled="busy" @click="send">送出</button>
    <div class="ops-reads"><span class="muted small">查詢:</span>
      <button v-for="u in reads" :key="u" class="chip" @click="read(u)">{{ u }}</button></div>
    <pre v-if="out" class="ops-out">{{ out }}</pre>
  </section>`,
};
