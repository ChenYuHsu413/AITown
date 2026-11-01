"""Correct specific event lines and memories in a run (DB rows + the live snapshot).

    python scripts/fix_text_records.py --run <run_id> --plan fixes.json             # DRY RUN (default)
    python scripts/fix_text_records.py --run <run_id> --plan fixes.json --execute

``fixes.json``: a list of {"table": "events"|"memories", "id": int, "old": text, "new": text}, or
{"table": "snapshot", "old": text, "new": text} for a string that lives only in the snapshot
(e.g. today's plan intent).
Each fix must match its row by id AND current text, or nothing is changed. The
snapshot is corrected by replacing each exact ``old`` string wherever it appears in
it (memory items, today's plan intent, ...). --execute first copies the run's
snapshot into snapshot_archive.

For mechanism-made errors only (a line no guard checked, a fabricated observation) --
never a resident's own organic misremembering, which is history.

STOP THE SERVER FIRST (pause, confirm the snapshot, stop).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, update

from backend.app.db.models import EventRow, MemoryRow, WorldSnapshot
from backend.app.llm.env import load_env

REASON = "fix-text-records"
COLS = {"events": (EventRow, "speech"), "memories": (MemoryRow, "text")}


def _replace_all(node, old: str, new: str) -> tuple[object, int]:
    if isinstance(node, str):
        return (node.replace(old, new), node.count(old)) if old in node else (node, 0)
    if isinstance(node, list):
        out, n = [], 0
        for v in node:
            v2, k = _replace_all(v, old, new); out.append(v2); n += k
        return out, n
    if isinstance(node, dict):
        out, n = {}, 0
        for k, v in node.items():
            v2, c = _replace_all(v, old, new); out[k] = v2; n += c
        return out, n
    return node, 0


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
    p = Persistence(db_url)
    p.run_id = args.run
    try:
        async with p.session() as s:
            snap = (await s.execute(select(WorldSnapshot).where(WorldSnapshot.run_id == args.run))).scalar_one_or_none()
            if snap is None:
                print(f"no snapshot for run {args.run}.")
                return
            payload, ok = snap.payload, True
            for f in fixes:
                _, in_snap = _replace_all(payload, f["old"], f["new"])
                if f["table"] == "snapshot":
                    match = in_snap > 0
                    ok &= match
                    print(f"[{'OK' if match else 'NO MATCH'}] snapshot only  (occurrences: {in_snap})")
                    print(f"    - {f['old']}\n    + {f['new']}")
                    continue
                model, col = COLS[f["table"]]
                row = (await s.execute(select(model).where(model.id == f["id"], model.run_id == args.run))).scalar_one_or_none()
                match = row is not None and getattr(row, col) == f["old"]
                ok &= match
                print(f"[{'OK' if match else 'NO MATCH'}] {f['table']} #{f['id']}  (snapshot occurrences: {in_snap})")
                print(f"    - {f['old']}\n    + {f['new']}")
            if not ok:
                print("a fix did not match its row -- nothing changed.")
                return
            if not args.execute:
                print("DRY RUN -- nothing changed. Re-run with --execute (server stopped).")
                return
        aid = await p.archive_snapshot(payload, REASON)
        print(f"[archive] snapshot saved to snapshot_archive id={aid}")
        async with p.session() as s:
            for f in fixes:
                if f["table"] != "snapshot":
                    model, col = COLS[f["table"]]
                    await s.execute(update(model).where(model.id == f["id"], model.run_id == args.run).values({col: f["new"]}))
                payload, _ = _replace_all(payload, f["old"], f["new"])
            await s.execute(update(WorldSnapshot).where(WorldSnapshot.run_id == args.run).values(payload=payload))
            await s.commit()
        print(f"[done] {len(fixes)} records corrected.")
    finally:
        await p.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
