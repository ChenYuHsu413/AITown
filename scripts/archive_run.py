"""Close an era: mark a run archived (read-only from then on).

    python scripts/archive_run.py --run <run_id> --note "..."             # DRY RUN (default)
    python scripts/archive_run.py --run <run_id> --note "..." --execute

An archived run is never resumed: a boot whose latest snapshot belongs to it opens a
new run instead (see Persistence.start). Replay and the run catalogue still read it.
Nothing of the run's history is changed -- only its simulation_runs row (archived,
note). --execute first copies the run's snapshot into snapshot_archive.

STOP THE SERVER FIRST: a server still running the era would keep writing to it.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import func, select, text as sql_text, update

from backend.app.db.models import EventRow, SimulationRun, WorldSnapshot
from backend.app.llm.env import load_env
from backend.app.simulation.engine import fmt_time

ARCHIVE_REASON = "archive-run"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--note", required=True, help="what this era was, for the run catalogue")
    parser.add_argument("--execute", action="store_true", help="apply (default is a dry run)")
    args = parser.parse_args()

    load_env()
    db_url = os.environ.get("AI_TOWN_DB_URL", "")
    if not db_url:
        print("AI_TOWN_DB_URL is not set -- nothing to archive.")
        return
    from backend.app.db.persistence import Persistence

    p = Persistence(db_url)          # never .start(): that would open a new run
    p.run_id = args.run
    try:
        async with p.engine.begin() as conn:   # same additive migration the server runs
            await conn.execute(sql_text(
                "ALTER TABLE simulation_runs ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT FALSE"))
        async with p.session() as s:
            run = (await s.execute(select(SimulationRun).where(SimulationRun.id == args.run))
                   ).scalar_one_or_none()
            if run is None:
                print(f"no run {args.run}.")
                return
            snap = (await s.execute(select(WorldSnapshot).where(WorldSnapshot.run_id == args.run))
                    ).scalar_one_or_none()
            n, mn, mx = (await s.execute(select(func.count(EventRow.id), func.min(EventRow.minute),
                                                func.max(EventRow.minute))
                                         .where(EventRow.run_id == args.run))).one()
        mode = "EXECUTE" if args.execute else "DRY RUN"
        print(f"=== archive_run [{mode}] run={args.run} ===")
        print(f"  started_at={run.started_at:%Y-%m-%d}  archived={run.archived}  note={run.note!r}")
        print(f"  events={n}  span={fmt_time(mn) if mn is not None else '-'} -> "
              f"{fmt_time(mx) if mx is not None else '-'}")
        print(f"  snapshot={'minute ' + str(snap.minute) + ' (' + fmt_time(snap.minute) + ')' if snap else 'none'}")
        print(f"  -> archived=True, note={args.note!r}")
        if not args.execute:
            print("DRY RUN -- nothing changed. Re-run with --execute to apply (stop the server first).")
            return
        if snap is not None:
            archive_id = await p.archive_snapshot(snap.payload, reason=ARCHIVE_REASON)
            print(f"[archive] snapshot saved to snapshot_archive id={archive_id}")
        async with p.session() as s:
            await s.execute(update(SimulationRun).where(SimulationRun.id == args.run)
                            .values(archived=True, note=args.note))
            await s.commit()
        print("[done] run archived.")
    finally:
        await p.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
