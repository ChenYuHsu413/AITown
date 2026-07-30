# Event Contract v1 — Structured Simulation Events

Audience: the frontend (and, in phase 2, the `events` DB table).
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
  "actor": "alice",            // agent id ("" if none)
  "actor_name": "Alice",       // denormalized display name (English)
  "target": "bob",             // agent id, only for talk_start / say
  "target_name": "Bob",
  "location": "cafe",          // location id ("" if none)
  "location_name": "Moonlight Cafe",
  "speech": "",                // generated free text ONLY (dialogue line / insight)
  "text": "Alice started talking with Bob at Moonlight Cafe"  // DEPRECATED
}
```

Notes:

- `actor` / `target` / `location` are stable IDs — join against the
  `locations` and `agents` arrays from the snapshot for localized names.
  (`*_name` fields are English conveniences; for zh display, map IDs to
  your own zh name table exactly as you already do for the map.)
- `speech` is model-generated content and stays in whatever language the
  model produced (English under mock). Never template-translate it.

## Verb table (complete for v1)

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
| system     | (reserved)  | —                              | —                                            | —                                   |

Unknown verbs may appear in future versions (rumors, conflicts, world
events). **Frontend must fall back gracefully**: if a verb has no
template, render the deprecated `text` field as-is.

## Migration checklist for the frontend

1. Replace regex parsing of `ev.text` with template rendering keyed on
   `ev.verb`, using the tables above (keep your existing zh location/name
   maps; key them by `ev.location` / `ev.actor` IDs).
2. Speech bubbles: trigger on `kind === "dialogue"`, position by
   `ev.actor` (an agent ID — no more name-string matching), content from
   `ev.speech`.
3. Reflection feed styling: trigger on `kind === "reflection"`.
4. Keep the unknown-verb fallback to `ev.text`.
5. After switching, `ev.text` is unused by your code (it will be removed
   in Event Contract v2, when events move into PostgreSQL).

## Stability promise

- v1 fields will not be renamed or removed without a version bump.
- New verbs and new optional fields MAY be added at any time — hence the
  fallback rule.
