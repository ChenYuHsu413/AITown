// zh-TW display layer: name tables, placeholder resolver, event phrasing, clock.
// The tables are the developer's established wording, carried over from the old UI.

export const CAST = ['jiji', 'ange', 'oula', 'lengyue', 'azong', 'xixi', 'aisi', 'xue', 'long', 'kuaizheng'];
export const NAME_ZH = {jiji:'ㄐㄐ',ange:'安哥',oula:'歐拉',lengyue:'冷月',azong:'阿總',
  xixi:'希希',aisi:'艾斯',xue:'雪',long:'瓏',kuaizheng:'蒯政'};
const NAME_EN = {jiji:'Jiji',ange:'Ange',oula:'Oula',lengyue:'Lengyue',azong:'Azong',
  xixi:'Xixi',aisi:'Aisi',xue:'Xue',long:'Long',kuaizheng:'Kuaizheng'};
// Fallback place names (the snapshot's name_zh wins once it arrives).
const LOC_ZH = {home_a:'聽雨居',home_b:'望坡樓',cafe:'潮汐咖啡館',office:'川流事務所',park:'螢火公園',
  bakery:'爐心烘焙坊',market:'渡口市場'};
export const KINDS = {home:'住家',cafe:'咖啡館',office:'辦公室',park:'公園',bakery:'烘焙坊',market:'市場'};
const ACTIONS = {work:'工作',eat:'用餐',move:'移動',sleep:'睡眠',rest:'休息',idle:'閒置',talk:'交談'};
const OCCS = {'Cafe Owner':'咖啡館老闆','Office Worker':'上班族','Doctor':'醫生','Retired Teacher':'退休教師','Art Student':'藝術系學生',
  'Baker':'烘焙師','Postman':'郵差','Market Vendor':'市場攤販','Student':'學生','Retired Nurse':'退休護士',
  'Engineer':'工程師','Freelance Designer':'自由設計師','Retired':'退休',
  'job seeker':'求職中','cafe staff':'咖啡館員工','bakery staff':'烘焙坊員工','freelancer':'自由接案者',
  'Repair Technician':'修理師傅'};
const TRAITS = {curious:'好奇',friendly:'友善',gossipy:'八卦',quiet:'安靜',loyal:'忠誠',stressed:'焦慮',
  calm:'冷靜',observant:'敏銳',helpful:'樂於助人',wise:'睿智',talkative:'健談',nostalgic:'念舊',
  creative:'有創意',dreamy:'愛幻想','night-owl':'夜貓子',
  hardworking:'勤奮',proud:'自豪',competitive:'好勝',cheerful:'開朗',restless:'閒不住',
  frugal:'節儉',shy:'害羞',artistic:'有藝術氣質',kind:'善良',private:'內斂',
  technical:'技術控',focused:'專注',easygoing:'隨和',sociable:'善交際'};
const CHAPTER_ZH = {'Ordinary days':'平常的日子','Between chapters':'章節之間',
  'Building the light installation':'打造燈光裝置','Working up the courage to ask Aisi':'鼓起勇氣開口問艾斯',
  'Deciding whether to quit':'決定要不要辭職'};
const PRIVATE_CHAPTER = 'a private chapter';
const SPEECH_ZH = {'admitted it':'承認了','denied it':'否認了'};
const TRANSITION_ZH = {quit_job:'辭去了工作,開始尋找新方向',
  take_job_cafe:'到潮汐咖啡館上工了',take_job_bakery:'到爐心烘焙坊上工了',
  freelance_from_home:'開始了在家接案的生活'};
const MILESTONE_ZH = {acquaintance:'開始互相熟識',friend:'成為了朋友',close:'成了摯友',distant:'漸漸疏遠了'};
const MOOD_ZH = {upset:'不悅',worried:'擔心',happy:'愉快',anxious:'焦慮',neutral:'平靜'};
export const REQ_KIND_ZH = {location_visits:'造訪地點',action_count:'做某件事',talk_count:'與人交談',
  meetups_kept:'赴約見面',event_witnessed:'親眼見到',friendship:'友誼達到',trust:'信任達到',money_gain:'存到錢'};
export const WISH_STATUS_ZH = {active:'進行中',completed:'已實現',failed:'沒能實現',abandoned:'已放下'};
export const SCALE_ZH = {major:'大願',minor:'小願'};
export const CHAPTER_TYPE_ZH = {pursuit:'追逐中',interlude:'空窗期',ordinary:'日常'};
export const OUTCOME_ZH = {completed:'做到了',failed:'沒做到',abandoned:'– 放下了'};

// Live place/landmark names, filled from the snapshot (setPlaces).
let PLACES = {};      // id -> {name, name_zh}
let LANDMARKS = {};   // id -> name_zh
let SUBST = null;
export function setPlaces(locations, landmarks) {
  PLACES = {};
  for (const l of locations || []) PLACES[l.id] = {name: l.name, name_zh: l.name_zh || LOC_ZH[l.id] || l.name};
  for (const lm of landmarks || []) if (lm.name_zh) LANDMARKS[lm.id] = lm.name_zh;
  SUBST = null;
}

export const agentName = (id, fb) => NAME_ZH[id] || fb || id || '';
export const locName = id => (PLACES[id] && PLACES[id].name_zh) || LOC_ZH[id] || id || '';
export const locByEn = en => { for (const id in PLACES) if (PLACES[id].name === en) return PLACES[id].name_zh; return en; };
export const landmarkName = id => LANDMARKS[id] || (id === 'installation' ? '互動光影裝置' : id);
export const occName = o => OCCS[o] || o;
export const traitName = x => TRAITS[x] || x;
export const actName = a => ACTIONS[a] || a;
export const moodZh = m => MOOD_ZH[m] || m || '';
export const chapterTitle = s => s === PRIVATE_CHAPTER ? '一段不公開的篇章' : (CHAPTER_ZH[s] || s);

// {agent:id} / {loc:id} / {landmark:id} -> zh names. Unknown ids fall through.
export function resolve(text) {
  return String(text ?? '')
    .replace(/\{agent:([a-z_]+)\}/g, (_, id) => agentName(id))
    .replace(/\{loc:([a-z_]+)\}/g, (_, id) => locName(id))
    .replace(/\{landmark:([a-z_]+)\}/g, (_, id) => landmarkName(id));
}

// Dialogue backstop: a model occasionally copies pinyin/English sign names into a line.
export function dialogueSubst(text) {
  if (!text) return '';
  if (!SUBST) {
    const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const pairs = [];
    for (const id in NAME_ZH) pairs.push([NAME_EN[id], NAME_ZH[id]]);
    for (const id in PLACES) if (PLACES[id].name && PLACES[id].name_zh) pairs.push([PLACES[id].name, PLACES[id].name_zh]);
    pairs.sort((a, b) => b[0].length - a[0].length);
    SUBST = pairs.map(([en, zh]) => [new RegExp('\\b' + esc(en) + '\\b', 'gi'), zh]);
  }
  let s = resolve(text);
  for (const [re, zh] of SUBST) s = s.replace(re, zh);
  return s;
}

const dayRevZh = sp => { const m = String(sp || '').match(/\$\s*(\d+)/); return m ? `營收 $${m[1]}` : String(sp || ''); };
const WISH_CLOSED = {completed:'完成了一件放在心上的事', failed:'的一個心願沒能實現', abandoned:'放下了一個心願'};

// One event -> one zh sentence. Wish beats are content-free by construction: they
// never read ev.speech beyond the closed outcome word.
export function evText(ev) {
  const a = agentName(ev.actor, ev.actor_name), tg = agentName(ev.target, ev.target_name);
  const l = locName(ev.location), sp = ev.speech || '';
  switch (ev.verb) {
    case 'sleep': return `${a} 在 ${l} 就寢`;
    case 'eat': return `${a} 在 ${l} 用餐`;
    case 'work': return `${a} 在 ${l} 工作`;
    case 'rest': return `${a} 在 ${l} 休息`;
    case 'idle': return `${a} 在 ${l} 閒晃`;
    case 'arrive': return `${a} 前往 ${l}`;
    case 'talk_start': return ev.tier === 'notable' ? `${a} 和 ${tg} 第一次聊天` : `${a} 在 ${l} 開始與 ${tg} 交談`;
    case 'say': return `${a}:${dialogueSubst(sp)}`;
    case 'insight': return `${a}:${resolve(sp)}`;
    case 'belief': return `${a} 對 ${tg} 有了長期印象:${resolve(sp)}`;
    case 'share_rumor': return `${a} 向 ${tg} 傳了個八卦:${resolve(sp)}`;
    case 'seek_out': return `${a} 去找 ${tg} 了`;
    case 'landmark_done': return `${a} 完成了 ${l} 的${sp === 'the interactive light installation' ? landmarkName('installation') : sp}`;
    case 'confronted': return `${a} 當面質問了 ${tg}——對方${SPEECH_ZH[sp] || sp}`;
    case 'confide': return `${a} 向 ${tg} 吐露了心事`;
    case 'leak': return `${a} 把 ${tg} 的秘密說了出去`;
    case 'secret_born': return `${a} 開始藏著心事`;
    case 'week_close': return '完成本週結算';
    case 'day_summary': return `${a} 的一天結束了 — ${dayRevZh(sp)}`;
    case 'broke': return `${a} 在 ${l} 付不起餐費`;
    case 'rain_start': return '下雨了';
    case 'rain_end': return '雨停了';
    case 'festival_start': return `${l} 的慶典開始了`;
    case 'festival_end': return `${l} 的慶典結束了`;
    case 'transition': return `${a} ${TRANSITION_ZH[sp] || '做了人生中的重大改變'}`;
    case 'milestone': return `${a} 與 ${tg} ${MILESTONE_ZH[sp] || '的關係更近了一步'}`;
    case 'romance_dating': return `${a} 向 ${tg} 告白,兩人在一起了`;
    case 'romance_rejected': return `${a} 向 ${tg} 告白,卻被婉拒了`;
    case 'romance_partners': return `${a} 和 ${tg} 決定長長久久`;
    case 'breakdown': return `${l} 的設備故障了`;
    case 'repaired': return `${a} 修好了 ${l} 的設備`;
    case 'meetup_arranged': return `${a} 和 ${tg} 約好在 ${l} 見面`;
    case 'meetup_declined': return `${a} 邀 ${tg} 見面,但對方婉拒了`;
    case 'met_up': return `${a} 和 ${tg} 如約在 ${l} 碰了面`;
    case 'chapter_closed': return sp ? `${a} 翻過了人生的一頁——${resolve(sp)}` : `${a} 的人生翻過了一頁`;
    case 'chapter_started': return (!sp || sp === PRIVATE_CHAPTER) ? `${a} 開始了新的篇章` : `${a} 展開了新章節:${resolve(chapterTitle(sp))}`;
    case 'wish_born': case 'wish_seeded': return `${a} 心裡悄悄有了一個新的願望`;
    case 'wish_frustrated': return `${a} 默默努力的事又卡住了`;
    case 'wish_closed': return `${a}${WISH_CLOSED[sp] || '的一個心願落幕了'}`;
    default: return ev.text || `${a} ${ev.verb}`;
  }
}

// Short label for a beat chip in the chronicle (refs carry no speech/detail).
export function beatShort(ref) {
  const a = agentName(ref.actor), b = agentName(ref.target);
  const M = {
    wish_born: '有了新願望', wish_seeded: '有了新願望', wish_closed: '一個心願落幕',
    wish_frustrated: '心願卡住了', chapter_closed: '人生翻頁', chapter_started: '新的篇章',
    confronted: '當面對質', romance_dating: '在一起了', romance_rejected: '告白被婉拒',
    romance_partners: '長長久久', milestone: '關係變化', confide: '吐露心事', leak: '秘密外洩',
    talk_start: '第一次聊天', transition: '人生轉變', landmark_done: '完成作品',
  };
  return {who: b && ref.target ? `${a} · ${b}` : a, what: M[ref.verb] || ref.verb};
}

// The few major verbs that pop an event card over the live map.
export const CARD_KIND = {
  wish_born: {icon:'sprout', label:'願望誕生', cls:'wish'}, wish_seeded: {icon:'sprout', label:'願望誕生', cls:'wish'},
  wish_closed: {icon:'flag', label:'願望落幕', cls:'wish'},
  confronted: {icon:'scale', label:'當面對質', cls:'confront'},
  chapter_closed: {icon:'bookopen', label:'人生翻頁', cls:'chapter'}, chapter_started: {icon:'bookmark', label:'新的篇章', cls:'chapter'},
  romance_dating: {icon:'heart', label:'告白成功', cls:'romance'}, romance_rejected: {icon:'heartoff', label:'告白落空', cls:'romance'},
};

// A beat's detail line, where it is public prose (never for wish beats). A secret's
// own words never lie flat on the main views: a confide shows only that it happened,
// a leak only the gossip now going round; the secret itself is in the inspector.
export function beatDetail(ev) {
  if (ev.verb === 'leak') return ev.speech ? `傳言:「${resolve(ev.speech)}」` : '';
  if (!ev.detail || String(ev.verb).startsWith('wish_') || ev.verb === 'chapter_closed'
      || ev.verb === 'confide') return '';
  return /[㐀-鿿]/.test(ev.detail) ? resolve(ev.detail) : '';
}

export const DAY_MIN = 1440;
const DOW = ['週一','週二','週三','週四','週五','週六','週日'];
export const dayOf = m => Math.floor(m / DAY_MIN) + 1;
export const dowOfDay = d => DOW[(((d - 1) % 7) + 7) % 7];          // Day 1 = Monday
export const hhmm = m => { const x = ((Math.floor(m) % DAY_MIN) + DAY_MIN) % DAY_MIN;
  return `${String(Math.floor(x / 60)).padStart(2, '0')}:${String(x % 60).padStart(2, '0')}`; };
export const fmtMinute = m => `第 ${dayOf(m)} 天 · ${dowOfDay(dayOf(m))} ${hhmm(m)}`;
export const fmtDay = d => `第 ${d} 天 · ${dowOfDay(d)}`;

// ---- ambient merging (詳細 mode): a run of consecutive everyday lines folds into one
// line per kind, e.g. 「21:00–22:30 六人陸續就寢」. Input: events in time order.
const AMB_PHRASE = {sleep:'就寢', eat:'用餐', rest:'休息', work:'上工', arrive:'走動', idle:'閒晃',
  talk_start:'開始交談', insight:'有所反思', day_summary:'結算一天', seek_out:'去找人', broke:'付不起餐費'};
const ZH_NUM = ['零','一','兩','三','四','五','六','七','八','九','十'];
export const zhCount = n => n <= 10 ? ZH_NUM[n] : String(n);
export function mergeAmbient(evs) {
  const groups = new Map();   // verb -> {verb, first, last, actors:Set, evs:[]}
  for (const e of evs) {
    let g = groups.get(e.verb);
    if (!g) groups.set(e.verb, g = {verb: e.verb, first: e.minute, last: e.minute, actors: new Set(), evs: []});
    g.last = e.minute; g.evs.push(e); if (e.actor) g.actors.add(e.actor);
  }
  return [...groups.values()].map(g => {
    if (g.evs.length === 1) return {verb: g.verb, minute: g.first, time: hhmm(g.first), text: evText(g.evs[0]), n: 1};
    const span = g.first === g.last ? hhmm(g.first) : `${hhmm(g.first)}–${hhmm(g.last)}`;
    const who = g.actors.size > 1 ? `${zhCount(g.actors.size)}人陸續` : agentName([...g.actors][0] || '');
    return {verb: g.verb, minute: g.first, time: span, text: `${who}${AMB_PHRASE[g.verb] || g.verb}` + (g.evs.length > g.actors.size ? `(${g.evs.length} 次)` : ''), n: g.evs.length};
  });
}

// ---- day/night: one continuous phase from the sim minute. The UI tokens (--n, --dusk)
// and the map's tint / lights both read this, so they always agree.
const smooth = (a, b, x) => { const t = Math.max(0, Math.min(1, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
export function phaseOf(minute) {
  const h = (((minute % 1440) + 1440) % 1440) / 60;
  const n = h >= 12 ? smooth(17.5, 20.5, h) : 1 - smooth(4.5, 7, h);                   // night amount
  const dusk = Math.max(smooth(16.5, 18.75, h) * (1 - smooth(18.75, 21, h)),           // evening glow
                        0.6 * smooth(4.5, 5.75, h) * (1 - smooth(5.75, 7.5, h)));      // a softer dawn
  const label = h >= 5 && h < 8 ? '清晨' : h >= 8 && h < 17 ? '白天' : h >= 17 && h < 20 ? '黃昏' : '夜晚';
  const icon = label === '白天' ? 'sun' : label === '夜晚' ? 'moon' : 'sunset';
  return {n, dusk, h, label, icon};
}
