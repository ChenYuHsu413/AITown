"""One-shot ledger fix: align wishes.born with the world snapshot.

    python scripts/fix_wish_born.py --run <run_id>             # DRY RUN (default): report only
    python scripts/fix_wish_born.py --run <run_id> --execute   # archive, then update the ledger

The `born` column was added to the wishes ledger after phase 2b had already grown
wishes, with a DEFAULT of 'seeded' -- so every ledger row written before that column
existed reads 'seeded', grown or not. The snapshot always carried each wish's true
`born`, so it is the source of truth here. This corrects our bookkeeping; it changes
nothing about any resident's history (the snapshot is not touched).

--execute first copies the run's current snapshot into snapshot_archive, then
updates only the rows whose ledger value disagrees with the snapshot.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, update

from backend.app.db.models import WishRow, WorldSnapshot
from backend.app.llm.env import load_env

ARCHIVE_REASON = "fix-wish-born"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, help="run_id whose ledger to check")
    parser.add_argument("--execute", action="store_true", help="apply (default is a dry run)")
    args = parser.parse_args()

    load_env()
    db_url = os.environ.get("AI_TOWN_DB_URL", "")
    if not db_url:
        print("AI_TOWN_DB_URL is not set -- nothing to fix.")
        return
    from backend.app.db.persistence import Persistence

    p = Persistence(db_url)          # never .start(): that would open a new run
    p.run_id = args.run
    try:
        async with p.session() as s:
            snap = (await s.execute(select(WorldSnapshot).where(WorldSnapshot.run_id == args.run))
                    ).scalar_one_or_none()
            if snap is None:
                print(f"no snapshot for run {args.run} -- nothing to compare against.")
                return
            truth = {}
            for agent_id, a in (snap.payload.get("agents") or {}).items():
                for w in a.get("wishes") or []:
                    truth[w["id"]] = (agent_id, str(w.get("born", "seeded")), w.get("title", ""))
            rows = (await s.execute(select(WishRow.wish_id, WishRow.owner, WishRow.born)
                                    .where(WishRow.run_id == args.run))).all()

        mode = "EXECUTE" if args.execute else "DRY RUN"
        print(f"=== fix_wish_born [{mode}] run={args.run} snapshot minute={snap.minute} ===")
        fixes = []
        for wish_id, owner, born in rows:
            if wish_id not in truth:
                print(f"  {wish_id} ({owner}): not in the snapshot -- left alone")
                continue
            snap_owner, snap_born, title = truth[wish_id]
            mark = "FIX " if born != snap_born else "ok  "
            print(f"  {mark}{wish_id} {snap_owner:<10} ledger={born:<7} snapshot={snap_born:<7} {title}")
            if born != snap_born:
                fixes.append((wish_id, snap_born))
        missing = set(truth) - {r[0] for r in rows}
        for wish_id in sorted(missing):
            print(f"  {wish_id}: in the snapshot but not in the ledger -- not created here")
        print(f"\n{len(fixes)} row(s) to fix.")
        if not fixes or not args.execute:
            if fixes:
                print("DRY RUN -- nothing changed. Re-run with --execute to apply.")
            return

        archive_id = await p.archive_snapshot(snap.payload, reason=ARCHIVE_REASON)
        print(f"[archive] snapshot saved to snapshot_archive id={archive_id}")
        async with p.session() as s:
            for wish_id, snap_born in fixes:
                await s.execute(update(WishRow).where(WishRow.wish_id == wish_id, WishRow.run_id == args.run)
                                .values(born=snap_born))
            await s.commit()
        print(f"[done] {len(fixes)} ledger row(s) updated; updated_at left as it was.")
    finally:
        await p.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
