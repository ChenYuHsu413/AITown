"""Selective rollback + re-closure of ONE resident's chapter (dry-run by default).

    python scripts/reclose_chapter.py --agent xixi --archive 5 --outcome abandoned \
        --end-day 31 --aftermath 34-64 --forbid asked,told,confessed \
        --reason "..."            # DRY RUN: lists every change, writes nothing
    ... --execute                 # archives, applies, re-closes, snapshots

Why a script: the archive tooling in docs/admin.md restores the WHOLE world. Here
only one resident's closure was wrong, so this rolls back just that resident from
the archived pre-operation payload -- chapter, chapter_history, memory weights, the
retracted biography memory, belief weights, landmark decoupling, chronicle entry --
leaves everyone else exactly as they are, and then runs the closure pipeline again
with the corrected outcome and material (see chapters.closure_material for
``ended_minute`` / ``aftermath_window`` / ``forbid_terms``).

DB side on --execute: the retracted chapter-ledger rows are flagged ``superseded``
(kept as the audit trail), the retracted biography row is deleted from ``memories``
(a wrong near-permanent fact must not stay retrievable), the old ``events`` row is
kept (append-only history; replay stays faithful) and the new chapter_closed is
appended. STOP THE SERVER FIRST.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.app.agents import chapters as chapters_mod
from backend.app.agents.core import Belief, MemoryItem
from backend.app.agents.decision import DecisionEngine
from backend.app.llm.env import load_env
from backend.app.llm.factory import build_router
from backend.app.llm.prompts import builders
from backend.app.simulation import snapshot as snapshot_mod
from backend.app.simulation.engine import DAY_MIN, SimulationEngine, fmt_time
from backend.app.world.world import World
from data.seed import SEED_PURSUITS, build_agents, build_locations, seed_secrets


def _plan_rollback(agent, world, engine, arch_agent: dict) -> dict:
    """Diff the resident's live state against the archived one. Returns the
    rollback plan (each item is printed by the dry run and applied by execute)."""
    plan: dict = {"changes": []}
    live_hist = agent.chapter_history
    arch_hist_n = len(arch_agent.get("chapter_history") or [])
    retracted = live_hist[arch_hist_n:]                       # records added after the archive
    plan["retracted_records"] = retracted
    retracted_ids = {r.chapter["id"] for r in retracted}
    superseded = list(retracted_ids)
    if agent.chapter is not None and agent.chapter.chapter_type == "interlude":
        superseded.append(agent.chapter.id)                     # the interlude the closure opened
    plan["superseded_chapter_ids"] = superseded
    plan["changes"].append(f"chapter_history: {len(live_hist)} -> {arch_hist_n} record(s); "
                           + "; ".join(f"drop [{r.chapter['id']}] {r.outcome} \"{r.biography_line}\"" for r in retracted))

    # chapter: back to the archived one, or (archive pre-dates chapters) the seeded pursuit
    arch_ch = arch_agent.get("chapter")
    if isinstance(arch_ch, dict):
        plan["chapter"] = chapters_mod.Chapter.from_dict(arch_ch)
    else:
        spec = SEED_PURSUITS.get(agent.id)
        if spec is None:
            raise SystemExit(f"{agent.id}: archive has no chapter and no seeded pursuit exists -- nothing to re-close")
        goal, title, narrative, landmark_id = spec
        plan["chapter"] = chapters_mod.make_pursuit(goal, title, narrative, 1, landmark_id=landmark_id)
    cur = agent.chapter.to_dict() if agent.chapter else None
    plan["changes"].append(f"chapter: {cur and cur['chapter_type']} \"{cur and cur['title']}\" -> "
                           f"pursuit \"{plan['chapter'].title}\" (started_on {plan['chapter'].started_on})")

    # memories: drop retracted biographies; restore archived weights (new post-archive memories kept)
    arch_items = {(m.get("minute"), m.get("text")): m for m in (arch_agent.get("memory") or {}).get("items", [])}
    drop, reweight, kept_new = [], [], 0
    for m in agent.memory.items:
        if m.kind == "biography" and m.source_chapter_id in retracted_ids:
            drop.append(m)
            continue
        a = arch_items.get((m.minute, m.text))
        if a is None:
            kept_new += 1
            continue
        w = float(a.get("weight", 1.0))
        if abs(w - m.weight) > 1e-9:
            reweight.append((m, w))
    plan["drop_memories"], plan["reweight"] = drop, reweight
    for m in drop:
        plan["changes"].append(f"memory: remove biography [{m.source_chapter_id}] \"{m.text}\"")
    plan["changes"].append(f"memory weights: {len(reweight)} memories restored to archived weight "
                           f"(e.g. 0.3 -> 1.0); {kept_new} post-archive memories untouched")

    # beliefs: restore archived weights
    arch_bel = {(b.get("subject"), b.get("text")): b for b in (arch_agent.get("semantic") or {}).get("beliefs", [])}
    bel = []
    for b in agent.semantic.beliefs:
        a = arch_bel.get((b.subject, b.text))
        if a is not None and abs(float(a.get("weight", 1.0)) - b.weight) > 1e-9:
            bel.append((b, float(a.get("weight", 1.0))))
    plan["rebelief"] = bel
    plan["changes"].append(f"belief weights: {len(bel)} restored")

    # landmark decoupling + routine (only if the retracted chapter built a landmark)
    lm_ids = {r.chapter.get("related_landmark_id") for r in retracted if r.chapter.get("related_landmark_id")}
    plan["landmarks"] = lm_ids
    if lm_ids:
        plan["changes"].append(f"landmark(s) {sorted(lm_ids)}: 'decoupled' flag + creator routine restored from archive")

    # chronicle: the retracted chapter_closed beat(s)
    lines = {r.biography_line for r in retracted}
    chron = [c for c in engine.chronicle if c.get("verb") == "chapter_closed" and c.get("actor") == agent.id
             and c.get("speech") in lines]
    plan["chronicle_drop"] = chron
    plan["changes"].append(f"chronicle: remove {len(chron)} chapter_closed entr{'y' if len(chron) == 1 else 'ies'} "
                           f"(the events table row is KEPT -- append-only history)")
    return plan


def _apply_rollback(agent, world, engine, plan: dict, arch_agent: dict) -> None:
    for m in plan["drop_memories"]:
        agent.memory.items.remove(m)
    for m, w in plan["reweight"]:
        m.weight = w
    agent.memory.invalidate_weights()
    for b, w in plan["rebelief"]:
        b.weight = w
    n = len(agent.chapter_history) - len(plan["retracted_records"])
    agent.chapter_history = agent.chapter_history[:n]
    chapters_mod.start_pursuit(agent, plan["chapter"])
    if plan["landmarks"]:
        for loc in world.locations.values():
            for lm in loc.landmarks:
                if lm.get("id") in plan["landmarks"]:
                    lm.pop("decoupled", None)
        rt = arch_agent.get("routine") or {}
        if isinstance(rt.get("weekday"), list) and rt["weekday"]:
            from backend.app.agents.routine import Routine, RoutineEntry
            wk = [RoutineEntry(int(e[0]), str(e[1]), str(e[2])) for e in rt["weekday"] if len(e) >= 3]
            we = [RoutineEntry(int(e[0]), str(e[1]), str(e[2])) for e in (rt.get("weekend") or []) if len(e) >= 3]
            agent.routine = Routine(wk, we or None)
    for c in plan["chronicle_drop"]:
        engine.chronicle.remove(c)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--archive", type=int, required=True, help="snapshot_archive id holding the pre-closure state")
    ap.add_argument("--outcome", required=True, choices=list(chapters_mod.OUTCOMES))
    ap.add_argument("--end-day", type=int, default=None, help="true last sim-day of the chapter (inclusive)")
    ap.add_argument("--aftermath", default="", help="A-B: also show related memories from sim-days A..B as aftermath")
    ap.add_argument("--forbid", default="", help="comma-separated words the biography line must not contain")
    ap.add_argument("--reason", default="")
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()

    load_env()
    db_url = os.environ.get("AI_TOWN_DB_URL", "")
    if not db_url:
        print("AI_TOWN_DB_URL is not set -- nothing to do.")
        return
    from backend.app.db.persistence import Persistence

    router = build_router()
    world = World(build_locations(), build_agents())
    engine = SimulationEngine(world, DecisionEngine(router))
    seed_secrets(engine.decisions.secrets)
    for a in world.agents.values():
        a.chapter = None
        a.chapter_history = []

    def _restore(minute: int, payload: dict) -> None:
        snapshot_mod.restore(payload, engine, world, engine.decisions)
        engine.bootstrap(minute)

    p = Persistence(db_url)
    if not await p.start(note="reclose-chapter", resume=True, restore_cb=_restore):
        print("no live snapshot found")
        await p.stop()
        return
    engine.decisions.rebuild_suppressed_themes(world)
    # The re-closure runs a gender gate; without the roster it would wave every line
    # through and report a clean run it never actually checked.
    builders.require_roster("reclose_chapter")
    agent = world.agents.get(args.agent)
    if agent is None:
        raise SystemExit(f"unknown agent {args.agent}")
    arch = await p.load_archive(args.archive)
    if arch is None:
        raise SystemExit(f"archive id {args.archive} not found")
    arch_agent = (arch["payload"].get("agents") or {}).get(agent.id)
    if not isinstance(arch_agent, dict):
        raise SystemExit(f"archive {args.archive} has no state for {agent.id}")

    mode = "EXECUTE" if args.execute else "DRY RUN"
    print(f"=== reclose_chapter [{mode}] agent={agent.id} archive={args.archive} "
          f"({arch['reason']}, {fmt_time(arch['minute'])}) live={fmt_time(engine.now)} ===\n")
    plan = _plan_rollback(agent, world, engine, arch_agent)
    print("ROLLBACK (only this resident):")
    for c in plan["changes"]:
        print(f"  - {c}")
    print(f"  - chapters table: mark superseded -> {plan['superseded_chapter_ids']}")
    print(f"  - memories table: delete biography rows with source_chapter_id in "
          f"{sorted({r.chapter['id'] for r in plan['retracted_records']})}")

    ended = None if args.end_day is None else args.end_day * DAY_MIN - 1
    aft = None
    if args.aftermath:
        a, b = args.aftermath.split("-", 1)
        aft = (int(a), int(b))
    forbid = tuple(x.strip() for x in args.forbid.split(",") if x.strip())

    # Preview the material the closure would see, on a scratch copy of the rollback.
    # (The plan's objects are the live ones; apply, preview, and -- in a dry run -- stop.)
    _apply_rollback(agent, world, engine, plan, arch_agent)
    mat = chapters_mod.closure_material(agent, world, agent.chapter, engine.now, ended, aft, forbid)
    print(f"\nRE-CLOSE as {args.outcome}: span Day {mat['window']['start_day']}-{mat['window']['end_day']} "
          f"({mat['window']['days']} days); aftermath {mat['aftermath_window']}; forbid {mat['forbid_terms']}")
    print(f"  would down-weight {mat['related_count']} memories to x{chapters_mod.DOWNWEIGHT_COEFF}")
    print("  memories from the chapter:")
    for m in mat["memories"]:
        print(f"    [{m['id']}] (imp {m['importance']}, {fmt_time(m['minute'])}) {m['text']}")
    if mat["aftermath"]:
        print("  aftermath (after letting go):")
        for m in mat["aftermath"]:
            print(f"    [{m['id']}] (imp {m['importance']}, {fmt_time(m['minute'])}) {m['text']}")
    for line in chapters_mod.relationship_summary_lines(mat, world):
        print(f"    - {line}")
    print(f"  template line (fallback): {chapters_mod.template_biography(agent, agent.chapter, args.outcome)}")

    if not args.execute:
        print("\nDRY RUN -- nothing changed (the rollback above was applied in memory only and discarded).")
        await p.stop()
        return

    # ---- execute: the archive was taken BEFORE the in-memory rollback above? No --
    # capture the true pre-operation world: undo nothing, just archive the LIVE
    # snapshot payload as it was loaded (re-read from DB to be exact).
    live = await p.latest_snapshot()
    archive_id = await p.archive_snapshot(live["payload"], reason=f"reclose-{agent.id}")
    print(f"\n[archive] pre-operation world saved to snapshot_archive id={archive_id}")
    n_sup = await p.supersede_chapters(plan["superseded_chapter_ids"])
    n_del = 0
    for r in plan["retracted_records"]:
        n_del += await p.delete_biography_rows(agent.id, r.chapter["id"])
    print(f"[db] chapters superseded: {n_sup}; biography rows deleted: {n_del}")

    engine.bus.subscribers.append(p.on_event)
    engine.on_chapter_record = p.on_chapter
    router.usage.on_record = p.on_llm_call
    agent.memory.on_add = lambda item: p.on_memory(agent.id, item)
    engine._emit_chapter_record(agent, chapter=agent.chapter)
    rec = await engine.close_chapter(agent, args.outcome, trigger="backfill", reason=args.reason,
                                     ended_minute=ended, aftermath_window=aft, forbid_terms=forbid)
    if rec is None:
        print("closure did not run")
    else:
        print(f"[{agent.id}] closed ({rec.biography_source}): \"{rec.biography_line}\" residue={rec.emotional_residue}; "
              f"refs={[x['id'] for x in rec.memory_refs]}; down-weighted {rec.downweighted_memories} memories; "
              f"interlude until day {agent.chapter.until_day}")
    p.on_snapshot(snapshot_mod.capture(engine, world, engine.decisions))
    await p.stop()
    print(f"\n[done] snapshot updated; archive id={archive_id}. Restart the server to continue from it.")
    print(router.usage.summary())


if __name__ == "__main__":
    asyncio.run(main())
