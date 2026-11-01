"""One-shot ledger fix: write the started row of every open chapter the ledger lacks.

    python scripts/backfill_chapter_ledger.py --run <run_id>             # DRY RUN (default)
    python scripts/backfill_chapter_ledger.py --run <run_id> --execute

Until 2026-11 a chapter seeded at a fresh start (the seeded pursuits) reached the
chapters ledger only when it closed; the server now writes it at once. This fills
the gap for a run that started before that: every resident's CURRENT chapter in the
run's snapshot that has no ledger row gets its started row (the same shape the
engine writes). Rows that already exist -- including every closed chapter -- are not
touched. An archived run is refused (an era, once closed, is read-only).

Safe while the server runs: it only inserts rows the server has not written, and the
server upserts the same row by chapter_id when that chapter later closes.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.app.db.models import ChapterRow, WorldSnapshot
from backend.app.llm.env import load_env


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--execute", action="store_true", help="apply (default is a dry run)")
    args = parser.parse_args()

    load_env()
    db_url = os.environ.get("AI_TOWN_DB_URL", "")
    if not db_url:
        print("AI_TOWN_DB_URL is not set -- nothing to backfill.")
        return
    from backend.app.db.persistence import Persistence

    p = Persistence(db_url)          # never .start(): that would open a new run
    try:
        if await p.is_archived(args.run):
            print(f"run {args.run} is archived -- read-only, not backfilled.")
            return
        async with p.session() as s:
            snap = (await s.execute(select(WorldSnapshot).where(WorldSnapshot.run_id == args.run))
                    ).scalar_one_or_none()
            if snap is None:
                print(f"no snapshot for run {args.run}.")
                return
            have = set((await s.execute(select(ChapterRow.chapter_id)
                                        .where(ChapterRow.run_id == args.run))).scalars())
        rows = []
        for agent_id, a in sorted((snap.payload.get("agents") or {}).items()):
            ch = a.get("chapter")
            if not isinstance(ch, dict) or not ch.get("id") or ch["id"] in have:
                continue
            rows.append({
                "run_id": args.run, "chapter_id": ch["id"], "agent_id": agent_id,
                "chapter_type": ch.get("chapter_type", ""), "title": ch.get("title", ""),
                "narrative": ch.get("narrative", ""), "goal": ch.get("goal", ""),
                "related_goal_id": ch.get("related_goal_id") or "",
                "related_landmark_id": ch.get("related_landmark_id", ""),
                "started_on": int(ch.get("started_on", 0)), "ended_on": 0, "outcome": "",
                "biography_line": "", "emotional_residue": ch.get("emotional_residue", ""),
                "trigger": "", "memory_refs": [], "updated_at": datetime.utcnow(),
            })
        mode = "EXECUTE" if args.execute else "DRY RUN"
        print(f"=== backfill_chapter_ledger [{mode}] run={args.run} snapshot minute={snap.minute} ===")
        print(f"  ledger rows already present: {len(have)} (left untouched)")
        for r in rows:
            print(f"  + {r['agent_id']:<10} {r['chapter_type']:<9} day {r['started_on']:<3} "
                  f"{r['chapter_id']}  {r['title']}")
        print(f"{len(rows)} started row(s) to write.")
        if not rows or not args.execute:
            if rows:
                print("DRY RUN -- nothing changed. Re-run with --execute to apply.")
            return
        async with p.session() as s:
            for r in rows:
                await s.execute(pg_insert(ChapterRow).values(**r)
                                .on_conflict_do_nothing(index_elements=[ChapterRow.chapter_id]))
            await s.commit()
        print(f"[done] {len(rows)} row(s) written.")
    finally:
        await p.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
