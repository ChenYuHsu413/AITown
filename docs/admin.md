# Admin operations & recovery

Destructive admin endpoints are **safe by default** and **reversible**. Two rules
guarantee no repeat of a data-loss incident:

1. **Dry-run by default.** A destructive endpoint, called with no body (or
   `{"dry_run": true}`), only *reports* what it would remove — including the full
   text of each item — and changes nothing. You must explicitly pass
   `{"dry_run": false}` to apply.
2. **Pre-operation archive.** The moment before a real delete, the entire world
   state is written to the append-only `snapshot_archive` table (unlike
   `world_snapshots`, which is a single upserted row per run). The exact
   pre-operation state is therefore always recoverable.

Both require persistence (`AI_TOWN_DB_URL`). Without a DB there is nothing to
delete-from or archive, and `run_day.py` never touches any of this.

## Endpoints

### `POST /api/admin/prune-beliefs`

Removes low-quality beliefs/secrets (pre-quality-gate `"ok"`-style filler).

```bash
# 1) DRY RUN (default): see exactly what would go, change nothing
curl -X POST localhost:8000/api/admin/prune-beliefs
# -> {"dry_run": true, "counts": {...}, "would_remove_beliefs": [...], "would_remove_secrets": [...]}

# 2) APPLY: archives the pre-op state first, then deletes + snapshots
curl -X POST localhost:8000/api/admin/prune-beliefs \
     -H 'Content-Type: application/json' -d '{"dry_run": false}'
# -> {"dry_run": false, "archive_id": 7, "removed_beliefs": 0, "removed_secrets": 3}
```

Always review the dry-run output first. The apply response returns the
`archive_id` of the backup taken just before the delete.

### `POST /api/admin/resolve-stale-secrets`

Retires secrets whose worry has plainly already been acted on but that predate the
resolution lifecycle (e.g. Xixi's *"too shy to ask Aisi"* after he has confided in
and repeatedly sought out Aisi). Per unresolved secret it finds who the worry is
*about* (the seeded `about`, else a resident named in the text) and flags it when
the owner has already confided it to that person **or** has spoken with them ≥ 3
times. Applying resolves each (and leaves the owner a "laid to rest" memory).

```bash
# 1) DRY RUN (default): list the candidates with the reason each was flagged
curl -X POST localhost:8000/api/admin/resolve-stale-secrets
# -> {"dry_run": true, "count": 3, "would_resolve": [{"owner_name": "希希", "about_name": "艾斯",
#     "reason": "spoke with 艾斯 14x", "text": "..."}, ...]}

# 2) APPLY: archives the pre-op state first, then resolves + snapshots
curl -X POST localhost:8000/api/admin/resolve-stale-secrets \
     -H 'Content-Type: application/json' -d '{"dry_run": false}'
# -> {"dry_run": false, "archive_id": 8, "resolved": 3, "requested": "all-candidates"}

# APPLY ONLY SOME: pass secret_ids (a subset of the dry-run candidates)
curl -X POST localhost:8000/api/admin/resolve-stale-secrets \
     -H 'Content-Type: application/json' -d '{"dry_run": false, "secret_ids": ["06ddae6b"]}'
```

Non-destructive (it only marks secrets resolved — nothing is deleted), but it still
archives first and is fully reversible via the recovery flow below.

### `POST /god/close_chapter`

Closes a resident's current **pursuit** chapter right now (see
`backend/app/agents/chapters.py`): one smart-tier closure reflection (or the template
line if the model fails), the atomic state change (biography memory, chapter-related
memories/beliefs down-weighted to 0.3×, interlude begins, landmark decoupled), a
`chapter_closed` beat, then a snapshot. For testing and for matters that ended before
the pipeline existed. Not dry-run gated (it is a narrative action, nothing is deleted).

```bash
curl -X POST localhost:8000/god/close_chapter -H 'Content-Type: application/json' \
     -d '{"agent_id": "aisi", "outcome": "completed", "reason": "the installation lit up"}'
# -> {"ok": true, "closed": {...history record...}, "now": {...the interlude chapter...}}
# 409 when the resident has no pursuit chapter, or a closure is already in flight.
```

### `POST /god/seed_wish`

Hands a resident a structured private intention (see
`backend/app/agents/wishes.py`). Phase 2a seeds wishes by hand; generating one
from a resident's own history is 2b.

**SAFE BY DEFAULT:** `validate_only` defaults to `true` and only returns the
feasibility report. Send `{"validate_only": false}` to actually seed (that path
also snapshots).

```bash
curl -X POST localhost:8000/god/seed_wish -H 'Content-Type: application/json' -d '{
  "agent_id": "azong", "scale": "major",
  "title": "...", "statement": "...", "motivation": "...",
  "narrative": "I am quietly making something of my own right now.",
  "requirements": [{"kind": "location_visits", "target": "park", "threshold": 5}],
  "expires_on": 135 }'
# -> {"ok": true, "requirements": [{... "feasible": true, "actionable": true}], ...}
# 422 with a `problems` list when the proposal does not hold up.
```

`narrative` is required for a `major` (it becomes the pursuit chapter's
self-description, and is the *only* way the owner perceives the wish in their own
prompts). Per resident: at most 1 active major + 2 active minor.

**`feasible` vs `actionable`.** A requirement is *feasible* when it can progress
at all, and *actionable* when this particular resident can go and do something
about it. They differ: a `money_gain` requirement held by someone living on a
pension is feasible (the wallet does grow) but not actionable (there is no work
entry and no shop, so there is nothing to pursue). A non-actionable requirement
still accrues progress from real events; the drive simply never chases it, so it
never records a blocked day, never breeds frustration, and cannot be the
requirement that justifies a major.

**Privacy, and one accepted exception.** `title` / `statement` / `motivation` are
private: they never enter another resident's prompt, another resident's memory, a
public event, or the chronicle. Public beats are content-free (`wish_seeded`
carries only the scale; a wish-linked chapter's beats say "a private chapter").

The one place the private wording does leave the process is the **display-layer
translation cache**: a major wish's chapter title and narrative are English
knowledge text, so a zh run sends them to the translation provider like any
biography line or belief. This has been reviewed and accepted — it is the same
path phase 1 already uses for chapter and biography text, the operator's own
inspector is the only reader, and no other resident's context is involved. If a
future deployment needs the wording to stay in-process, seed the chapter
narrative in the display language and skip the translate round-trip.

### `scripts/backfill_chapters.py` (one-shot, dry-run by default)

Initializes every resident's chapter on a pre-chapter snapshot and retroactively
closes pursuits that had already ended (Aisi's finished installation, ...). Stop the
server first (its periodic snapshot would overwrite the backfilled one).

```bash
python scripts/backfill_chapters.py             # DRY RUN: proposed chapters, basis, closure material
python scripts/backfill_chapters.py --execute   # archives to snapshot_archive, then applies + snapshots
```

### `GET /api/admin/archives`

Lists recent pre-operation backups (newest first), without the heavy payloads:

```bash
curl localhost:8000/api/admin/archives
# -> {"archives": [{"id": 7, "run_id": "...", "minute": 17280, "reason": "prune-beliefs", "created_at": "..."}]}
```

## Recovery — restoring a pre-operation state

If an apply removed something it shouldn't have, restore from its archive. The
archived `payload` has the same shape as `world_snapshots.payload`, so the
recovery is: copy the archived payload back into `world_snapshots` for that run,
then restart the server (which resumes from the latest snapshot).

```sql
-- find the backup taken before the operation
SELECT id, run_id, minute, reason, created_at FROM snapshot_archive ORDER BY created_at DESC;

-- restore it into the live snapshot for that run (use the id + run_id from above)
UPDATE world_snapshots ws
   SET payload = sa.payload, minute = sa.minute, created_at = now()
  FROM snapshot_archive sa
 WHERE sa.id = <ARCHIVE_ID> AND ws.run_id = sa.run_id;
```

Then restart with resume enabled (the default, `AI_TOWN_RESUME=1`). The town
rehydrates from the restored snapshot with the pruned items back in place.

> Tip: take a manual dry run before any apply, and keep the `archive_id` from the
> apply response — it is the one-line path back to the exact prior state.
