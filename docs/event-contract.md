# Event Contract v1 — Structured Simulation Events

Audience: the frontend and the `events` DB table (with `AI_TOWN_DB_URL` set,
every event is also persisted there).
Backend guarantees this shape for every event delivered over WebSocket
(`type: "snapshot"` → `events[]`, and `type: "tick"` → `events[]`).

## Goal

Stop parsing English sentences with regex. Render every event from
structured fields using per-language templates. The old prerendered
sentence is still delivered in `text` (DEPRECATED) so nothing breaks
during migration — switch to structured rendering, then ignore `text`.

## Event JSON shape

```json
{
  "minute": 752,
  "clock": "Day 1 12:32",
  "kind": "action",            // action | dialogue | reflection | system
  "verb": "talk_start",        // see verb table below
  "actor": "jiji",             // agent id ("" if none)
  "actor_name": "ㄐㄐ",         // denormalized: the resident's name from data/seed.py
  "target": "ange",            // agent id (talk partner, confidant, ...; "" if none)
  "target_name": "安哥",
  "location": "cafe",          // location id ("" if none)
  "location_name": "Tide Cafe",  // the location's English name (zh: name_zh in the snapshot)
  "speech": "",                // generated free text ONLY (dialogue line / insight)
  "text": "ㄐㄐ started talking with 安哥 at Tide Cafe",  // DEPRECATED
  "tier": "notable",           // major | notable | ambient (narrative layer, see below)
  "detail": ""                 // optional richer context for the chronicle
}
```

### Tiers (narrative layer, `backend/app/simulation/narrative.py`)

Every event is stamped by rule from its verb when it is published, and stored in
`events.tier` (NULL on rows written before the layer existed — never backfilled):

| tier | verbs |
|---|---|
| `major` | `wish_born`, `wish_seeded`, `wish_closed`, `chapter_closed`, `chapter_started`, `confronted`, `romance_dating`, `romance_rejected` |
| `notable` | `wish_frustrated`, `milestone`, `romance_partners`, `confide`, `leak`, `transition`, `landmark_done`, and a pair's **first** `talk_start` of the run (seeded friends are not strangers) |
| `ambient` | everything else |

`wish_frustrated` is content-free (actor only): a wish's setback, surfaced at the
daily settlement at the minute its private memory was written. The day's
major/notable beats become one sentence in `daily_digests`
(`/api/digests`, `/api/day/{day}`); relationship moves land in
`relationship_deltas` (`/api/relationship_history`).

Notes:

- `actor` / `target` / `location` are stable IDs — join against the
  `locations` and `agents` arrays from the snapshot for localized names.
  (`*_name` fields are conveniences; the zh display layer maps IDs through
  its own table in `frontend/js/names.js`.)
- `speech` is generated content in the run's language (`AI_TOWN_LANG`; the
  mock provider writes Chinese too in a zh run). Never template-translate it.

## Verb table (core verbs)

| kind       | verb        | fields used                    | EN template                                  | zh-TW template（建議）              |
|------------|-------------|--------------------------------|----------------------------------------------|-------------------------------------|
| action     | sleep       | actor, location                | {actor} went to sleep at {loc}               | {actor} 在 {loc} 就寢               |
| action     | eat         | actor, location                | {actor} is eating at {loc}                   | {actor} 在 {loc} 用餐               |
| action     | work        | actor, location                | {actor} is working at {loc}                  | {actor} 在 {loc} 工作               |
| action     | rest        | actor, location                | {actor} is resting at {loc}                  | {actor} 在 {loc} 休息               |
| action     | idle        | actor, location                | {actor} is idling at {loc}                   | {actor} 在 {loc} 閒晃               |
| action     | arrive      | actor, location (destination)  | {actor} → {loc}                              | {actor} 前往 {loc}                  |
| action     | talk_start  | actor, target, location        | {actor} started talking with {target} at {loc} | {actor} 在 {loc} 開始與 {target} 交談 |
| dialogue   | say         | actor, target, speech          | 💬 {actor}: {speech}                          | 💬 {actor}：{speech}                |
| reflection | insight     | actor, speech                  | 💭 {actor}: {speech}                          | 💭 {actor}：{speech}                |
| system     | chapter_closed  | actor, location, speech (biography line), detail (`outcome · title[ · reason]`) | 📖 {actor} closed a chapter — {speech} | 📖 {actor} 翻過了人生的一頁——{speech} |
| system     | chapter_started | actor, location, speech (chapter title), detail (narrative) | 📗 {actor} began a new chapter: {speech} | 📗 {actor} 展開了新章節：{speech} |
| system     | (reserved)  | —                              | —                                            | —                                   |

Life-chapter events (see `backend/app/agents/chapters.py`): `chapter_closed` fires
once per closed *pursuit* chapter with the first-person biography line in `speech`
(written in the run's language) and `outcome ∈ completed | failed | abandoned` at the
head of `detail`. `chapter_started` fires when a new non-interlude chapter opens:
the interlude lapsing into ordinary days, or a grown / seeded major wish opening a
pursuit (then `speech` is the neutral "a private chapter", never the wish's wording).
Both are chronicle beats and land in the `events` table; the queryable ledger is the
`chapters` table.

### Other verbs

The engine emits more verbs than the core table above; most of their English fallback
templates live in `_EN_TEMPLATES` in `backend/app/simulation/engine.py` and their zh
phrasing in `frontend/js/names.js`:

- movement and world: `move`, `seek_out`, `broke`, `rain_start`, `rain_end`,
  `festival_start`, `festival_end`, `breakdown`, `repaired`, `landmark_done`,
  `week_close`, `day_summary`
- social: `share_rumor`, `confide`, `leak`, `secret_born`, `confronted`, `belief`,
  `milestone`, `transition`
- meetups: `meetup_arranged`, `meetup_declined`, `met_up`
- romance: `romance_dating`, `romance_rejected`, `romance_partners`
- wishes (content-free; the intention itself is private): `wish_seeded`,
  `wish_born`, `wish_frustrated`, `wish_closed`

New verbs may still be added. **Frontend must fall back gracefully**: if a
verb has no template, render the deprecated `text` field as-is.

## Rendering rules for a frontend

The current UI (`frontend/js/names.js`) already follows these:

1. Render from `ev.verb` with per-language templates keyed by the
   `ev.location` / `ev.actor` IDs; never regex-parse `ev.text`.
2. Speech bubbles: trigger on `kind === "dialogue"`, position by
   `ev.actor` (an agent ID), content from `ev.speech`.
3. Reflection styling: trigger on `kind === "reflection"`.
4. Keep the unknown-verb fallback to `ev.text` (the only remaining use of
   `text`; it is still delivered for that reason).

## Stability promise

- v1 fields will not be renamed or removed without a version bump.
- New verbs and new optional fields MAY be added at any time — hence the
  fallback rule.
