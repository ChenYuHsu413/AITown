"""Split public objects' stitched descriptions into kind / item / content.

    python scripts/split_object_text.py --run <run_id>              # DRY RUN (default)
    python scripts/split_object_text.py --run <run_id> --execute

Before the split an object kept the model's whole phrase ("一張寫著「…」的小紙條", or
the bare words with a signature) and every sentence pasted it into a template. This
takes each one apart with the same parser the engine now uses (actions.parse_leaves)
and rewrites, for that run:
  * public_objects rows: kind / item / content, description = the rebuilt phrase;
  * made_object events: speech = the content alone, obj = {kind, item, content};
  * the snapshot's world objects and chronicle entries, the same way.
The dry run prints every before -> after. --execute first copies the snapshot into
snapshot_archive. Memories are history and are left alone.

STOP THE SERVER FIRST (scripts\\stop.bat).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, text as sql_text, update
from sqlalchemy.orm import load_only

from backend.app.agents import actions as actions_mod
from backend.app.db.models import EventRow, PublicObject, WorldSnapshot
from backend.app.llm.env import load_env

REASON = "split-object-text"


def _split(raw: str, what: str) -> dict:
    return actions_mod.parse_leaves(raw, what) or {"kind": "other", "item": raw, "content": ""}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()
    load_env()
    db_url = os.environ.get("AI_TOWN_DB_URL", "")
    if not db_url:
        print("AI_TOWN_DB_URL is not set.")
        return
    from backend.app.db.persistence import Persistence
    p = Persistence(db_url)
    p.run_id = args.run
    try:
        async with p.session() as s:
            snap = (await s.execute(select(WorldSnapshot).where(WorldSnapshot.run_id == args.run))).scalar_one_or_none()
            if snap is None:
                print(f"no snapshot for run {args.run}.")
                return
            payload = snap.payload
            objs = (await s.execute(select(PublicObject).options(load_only(
                PublicObject.id, PublicObject.description, PublicObject.creator, PublicObject.location))
                .where(PublicObject.run_id == args.run).order_by(PublicObject.born_minute)))
            objs = objs.scalars().all()
            evs = (await s.execute(select(EventRow).options(load_only(
                EventRow.id, EventRow.speech, EventRow.detail, EventRow.minute, EventRow.actor))
                .where(EventRow.run_id == args.run, EventRow.verb == "made_object").order_by(EventRow.id)))
            evs = evs.scalars().all()

        # the doing behind each object (its event's detail) helps read bare signed words
        what_of = {(e.actor, e.speech): e.detail or "" for e in evs}
        obj_plan = []
        for o in objs:
            f = _split(o.description, what_of.get((o.creator, o.description), ""))
            obj_plan.append((o.id, o.description, f, actions_mod.object_phrase(f)))
        ev_plan = [(e.id, e.speech, _split(e.speech, e.detail or "")) for e in evs]

        print(f"== public_objects ({len(obj_plan)})")
        for oid, before, f, phrase in obj_plan:
            print(f"  {oid}  - {before}\n              + [{f['kind']}] item={f['item']!r} content={f['content']!r}")
        print(f"== made_object events ({len(ev_plan)}): speech -> content, obj added")
        changed = sum(1 for _, before, f in ev_plan if before != f["content"])
        print(f"   {changed} speech values change")
        snap_objs = payload.get("objects") or []
        snap_chron = [c for c in (payload.get("chronicle") or []) if c.get("verb") == "made_object"]
        print(f"== snapshot: {len(snap_objs)} world objects, {len(snap_chron)} chronicle entries")
        if not args.execute:
            print("DRY RUN -- nothing changed. Re-run with --execute (server stopped).")
            return

        aid = await p.archive_snapshot(payload, REASON)
        print(f"[archive] snapshot saved to snapshot_archive id={aid}")
        for o in snap_objs:
            if "kind" not in o:
                o.update(_split(o.get("description", ""), o.get("note", "")))
            o["description"] = actions_mod.object_phrase(o)
        for c in snap_chron:
            if not c.get("obj"):
                f = _split(c.get("speech", ""), c.get("detail", ""))
                c["obj"], c["speech"] = f, f["content"]
        async with p.session() as s:
            for col, ddl in (("kind", "VARCHAR(16)"), ("item", "TEXT"), ("content", "TEXT")):
                await s.execute(sql_text(
                    f"ALTER TABLE public_objects ADD COLUMN IF NOT EXISTS {col} {ddl} NOT NULL DEFAULT ''"))
            await s.execute(sql_text("ALTER TABLE events ADD COLUMN IF NOT EXISTS obj JSONB"))
            for oid, _, f, phrase in obj_plan:
                await s.execute(update(PublicObject).where(PublicObject.id == oid).values(
                    kind=f["kind"], item=f["item"], content=f["content"], description=phrase))
            for eid, _, f in ev_plan:
                await s.execute(update(EventRow).where(EventRow.id == eid).values(speech=f["content"], obj=f))
            await s.execute(update(WorldSnapshot).where(WorldSnapshot.run_id == args.run).values(payload=payload))
            await s.commit()
        print(f"[done] {len(obj_plan)} objects, {len(ev_plan)} events, snapshot rewritten.")
    finally:
        await p.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
