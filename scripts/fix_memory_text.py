"""Correct specific memory texts in a run -- the "fix self-contradiction" tool.

    python scripts/fix_memory_text.py --run <run_id> --plan fixes.json             # DRY RUN (default)
    python scripts/fix_memory_text.py --run <run_id> --plan fixes.json --execute

``fixes.json`` is a list of {"agent": id, "minute": int, "old": exact text, "new": text,
"why": "..."}. Each fix must match exactly one memory row (run, agent, minute, text)
AND exactly one item in that agent's snapshot memory, or nothing is changed at all.
--execute first copies the run's snapshot into snapshot_archive, then rewrites the
rows and the snapshot together.

Only for a world-level contradiction: a memory that states something the world's own
record says did not happen. A resident misremembering someone else is legitimate
history and is never "fixed".

STOP THE SERVER FIRST (pause, confirm the snapshot, stop): a running server would
overwrite the snapshot with its in-memory copy.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, update

from backend.app.db.models import MemoryRow, WorldSnapshot
from backend.app.llm.env import load_env

REASON = "fix-memory-text"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()
    fixes = json.load(open(args.plan, encoding="utf-8"))

    load_env()
    db_url = os.environ.get("AI_TOWN_DB_URL", "")
    if not db_url:
        print("AI_TOWN_DB_URL is not set.")
        return
    from backend.app.db.persistence import Persistence
    p = Persistence(db_url)          # never .start(): that would open a new run
    p.run_id = args.run
    try:
        async with p.session() as s:
            snap = (await s.execute(select(WorldSnapshot).where(WorldSnapshot.run_id == args.run))
                    ).scalar_one_or_none()
            if snap is None:
                print(f"no snapshot for run {args.run}.")
                return
            payload = snap.payload
            plan_ok = True
            for f in fixes:
                rows = (await s.execute(select(MemoryRow.id).where(
                    MemoryRow.run_id == args.run, MemoryRow.agent_id == f["agent"],
                    MemoryRow.minute == f["minute"], MemoryRow.text == f["old"]))).scalars().all()
                items = [i for i in (payload["agents"][f["agent"]]["memory"]["items"])
                         if i.get("minute") == f["minute"] and i.get("text") == f["old"]]
                state = "OK" if len(rows) == 1 and len(items) <= 1 else "NO MATCH"
                plan_ok &= state == "OK"
                print(f"[{state}] {f['agent']} @{f['minute']}  rows={len(rows)} snapshot_items={len(items)}  ({f.get('why', '')})")
                print(f"    - {f['old']}\n    + {f['new']}")
            if not plan_ok:
                print("a fix did not match exactly one row -- nothing changed.")
                return
            if not args.execute:
                print("DRY RUN -- nothing changed. Re-run with --execute (server stopped).")
                return
        aid = await p.archive_snapshot(payload, REASON)
        print(f"[archive] snapshot saved to snapshot_archive id={aid}")
        async with p.session() as s:
            for f in fixes:
                await s.execute(update(MemoryRow).where(
                    MemoryRow.run_id == args.run, MemoryRow.agent_id == f["agent"],
                    MemoryRow.minute == f["minute"], MemoryRow.text == f["old"]).values(text=f["new"]))
                for i in payload["agents"][f["agent"]]["memory"]["items"]:
                    if i.get("minute") == f["minute"] and i.get("text") == f["old"]:
                        i["text"] = f["new"]
            await s.execute(update(WorldSnapshot).where(WorldSnapshot.run_id == args.run).values(payload=payload))
            await s.commit()
        print(f"[done] {len(fixes)} memories corrected.")
    finally:
        await p.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
