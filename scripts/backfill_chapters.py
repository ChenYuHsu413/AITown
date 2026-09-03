"""One-shot backfill: initialize life chapters on a pre-chapter world (phase 1).

    python scripts/backfill_chapters.py             # DRY RUN (default): report only
    python scripts/backfill_chapters.py --execute   # archive, then apply + snapshot

What it does (see backend/app/agents/chapters.py):
  1. Loads the latest world snapshot from AI_TOWN_DB_URL (.env) onto a seeded world,
     with every chapter cleared -- so "chapter is None" means "never initialized".
  2. For each of the 10 residents proposes a chapter: the seeded pursuits
     (data.seed.SEED_PURSUITS) become *pursuit* chapters; everyone else *ordinary*.
  3. For a pursuit whose matter has ALREADY ended (landmark completed, the matching
     secret laid to rest, a life transition applied, the goal rewritten), runs the
     closure pipeline retroactively -- one smart-tier closure reflection per closure
     (real providers if .env is live; template line on failure) -> biography memory,
     down-weighted memories/beliefs, interlude, chapter_closed event, chapter ledger.
  4. Dry run prints the proposal + basis + closure material and changes NOTHING.
     --execute first writes the exact pre-operation world to snapshot_archive, then
     applies and upserts the new world snapshot (and events / memories / chapter rows).

STOP THE SERVER FIRST when executing: its periodic snapshot would overwrite the
backfilled one with its own (chapter-less) in-memory world.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.app.agents import chapters as chapters_mod
from backend.app.agents import transitions as transitions_mod
from backend.app.agents.decision import DecisionEngine
from backend.app.llm.env import load_env
from backend.app.llm.factory import build_router
from backend.app.simulation import snapshot as snapshot_mod
from backend.app.simulation.engine import DAY_MIN, SimulationEngine, fmt_time
from backend.app.world.world import World
from data.seed import SEED_PURSUITS, build_agents, build_locations, seed_secrets

ARCHIVE_REASON = "backfill-chapters"


def _judge(agent, world, decisions, day: int) -> dict:
    """Propose this resident's chapter + (for pursuits) whether it already ended.
    Returns {"type", "chapter", "close": {outcome, trigger, reason} | None, "basis": [..]}."""
    basis: list[str] = []
    spec = SEED_PURSUITS.get(agent.id)
    if spec is None:
        basis.append("no seeded pursuit -> ordinary days (standing aim stays on profile.goals)")
        return {"type": "ordinary", "chapter": chapters_mod.make_ordinary(agent, 1), "close": None, "basis": basis}

    goal, title, narrative, landmark_id = spec
    ch = chapters_mod.make_pursuit(goal, title, narrative, 1, landmark_id=landmark_id)
    close = None

    # (a) landmark completed -- the true end is the creator's own "I finished" memory
    if landmark_id:
        for loc in world.locations.values():
            for lm in loc.landmarks:
                if lm.get("id") == landmark_id and lm.get("created_by") == agent.id:
                    basis.append(f"landmark '{landmark_id}' state={lm.get('state')} progress={lm.get('progress')}")
                    if lm.get("state") == "completed":
                        fin = next((m.minute for m in agent.memory.items
                                    if m.text.startswith(f"I finished {{landmark:{landmark_id}}}")), None)
                        if fin is not None:
                            basis.append(f"finished on {fmt_time(fin)} (the true end of the span given to the model)")
                        close = {"outcome": "completed", "trigger": "landmark",
                                 "reason": lm.get("name", ""), "ended_minute": fin}
    # (b) a resolved secret with the chapter's theme -- ended when it was laid to rest
    for s in decisions.secrets.secrets_of(agent.id):
        if chapters_mod.secret_matches_chapter(ch, s):
            basis.append(f"matching secret [{s.id}] resolved={s.resolved}: \"{s.text}\"")
            if s.resolved and close is None:
                fin = s.resolved_minute if s.resolved_minute >= 0 else None
                if fin is not None:
                    basis.append(f"laid to rest on {fmt_time(fin)} (the true end of the span given to the model)")
                close = {"outcome": "completed", "trigger": "secret_resolved", "reason": s.resolution,
                         "ended_minute": fin}
    # (c) a life transition applied that makes the goal moot
    if agent.state.last_transition_day >= 0:
        basis.append(f"life transition applied on day {agent.state.last_transition_day + 1} "
                     f"(occupation now '{agent.profile.occupation}')")
        for t in transitions_mod.REGISTRY.values():
            if chapters_mod.goal_matches_chapter(ch, *t.clears_goal) and close is None:
                close = {"outcome": "completed", "trigger": "transition", "reason": t.label}
                break
    # (d) the original goal still on the profile, or already rewritten
    goal_texts = [str(g.get("goal", "")) for g in agent.profile.goals]
    if goal in goal_texts:
        basis.append("original goal still on profile.goals (moves onto the chapter)")
    elif goal_texts:
        basis.append(f"goal was rewritten -> {goal_texts!r}")
        if close is None and any(g.lower().startswith("keep ") for g in goal_texts):
            close = {"outcome": "completed", "trigger": "goal_rewritten", "reason": goal_texts[0]}
    else:
        basis.append("no goals on profile")
    if close is None:
        basis.append("no end signal found -> pursuit stays OPEN")
    return {"type": "pursuit", "chapter": ch, "close": close, "basis": basis}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="apply (default is a dry run)")
    parser.add_argument("--agents", default="", help="comma-separated subset of agent ids (default: all)")
    args = parser.parse_args()

    load_env()
    db_url = os.environ.get("AI_TOWN_DB_URL", "")
    if not db_url:
        print("AI_TOWN_DB_URL is not set -- nothing to backfill (this script only touches a DB world).")
        return
    from backend.app.db.persistence import Persistence

    router = build_router()              # live per .env: the closure reflection uses real providers on --execute
    world = World(build_locations(), build_agents())
    engine = SimulationEngine(world, DecisionEngine(router))
    seed_secrets(engine.decisions.secrets)
    for a in world.agents.values():      # seeded chapters must not masquerade as restored ones
        a.chapter = None
        a.chapter_history = []

    restored: dict = {}

    def _restore(minute: int, payload: dict) -> None:
        m = snapshot_mod.restore(payload, engine, world, engine.decisions)
        engine.bootstrap(m)
        restored["minute"] = m
        restored["schema"] = payload.get("schema_version")

    p = Persistence(db_url)
    resumed = await p.start(note="backfill-chapters", resume=True, restore_cb=_restore)
    if not resumed:
        print("No snapshot found to backfill (fresh DB) -- nothing to do.")
        await p.stop()
        return
    engine.decisions.rebuild_suppressed_themes(world)
    now = engine.now
    day = now // DAY_MIN + 1
    only = {x.strip() for x in args.agents.split(",") if x.strip()}
    mode = "EXECUTE" if args.execute else "DRY RUN"
    print(f"=== backfill_chapters [{mode}] run={p.run_id} snapshot schema v{restored.get('schema')} "
          f"at {fmt_time(now)} (day {day}) ===\n")

    already = [a.id for a in world.agents.values() if a.chapter is not None]
    if already:
        print(f"already initialized (skipped): {', '.join(already)}\n")

    plan = []
    for a in world.agents.values():
        if a.chapter is not None or (only and a.id not in only):
            continue
        j = _judge(a, world, engine.decisions, day)
        plan.append((a, j))
        ch = j["chapter"]
        print(f"--- {a.id} ({a.name}) -> {j['type'].upper()}: \"{ch.title}\"")
        for b in j["basis"]:
            print(f"    basis: {b}")
        if j["close"]:
            c = j["close"]
            print(f"    CLOSE as {c['outcome']} (trigger={c['trigger']}: {c['reason']})")
            # closure material preview (rules only, no LLM in a dry run)
            mat = chapters_mod.closure_material(a, world, ch, now, c.get("ended_minute"))
            n_bel = sum(1 for b in a.semantic.beliefs if chapters_mod.is_related_belief(b, ch))
            print(f"    would down-weight {mat['related_count']} memories + {n_bel} beliefs to x{chapters_mod.DOWNWEIGHT_COEFF}")
            print(f"    reflection material ({len(mat['memories'])} top memories, span Day "
                  f"{mat['window']['start_day']}-{mat['window']['end_day']} = {mat['window']['days']} days):")
            for m in mat["memories"]:
                print(f"      [{m['id']}] (imp {m['importance']}, {fmt_time(m['minute'])}) {m['text']}")
            for line in chapters_mod.relationship_summary_lines(mat, world):
                print(f"      - {line}")
            print(f"    template line (fallback): {chapters_mod.template_biography(a, ch, c['outcome'])}")
            print(f"    interlude: {chapters_mod.interlude_days(chapters_mod.RESIDUE_DEFAULT[c['outcome']], a)} days "
                  f"from day {day} (residue-dependent, 3-7)")
        print()

    if not plan:
        print("nothing to do.")
        await p.stop()
        return

    if not args.execute:
        print("DRY RUN -- nothing changed. Re-run with --execute to apply (stop the server first).")
        await p.stop()
        return

    # ---- execute -------------------------------------------------------
    payload = snapshot_mod.capture(engine, world, engine.decisions)
    archive_id = await p.archive_snapshot(payload, reason=ARCHIVE_REASON)
    print(f"[archive] pre-operation world saved to snapshot_archive id={archive_id}\n")

    engine.bus.subscribers.append(p.on_event)            # chapter_closed events -> events table
    engine.on_chapter_record = p.on_chapter              # ledger rows
    router.usage.on_record = p.on_llm_call               # the closure reflection's cost
    for a in world.agents.values():                      # only NEW memories (the biography) are mirrored
        a.memory.on_add = (lambda aid: lambda item: p.on_memory(aid, item))(a.id)

    for a, j in plan:
        ch = j["chapter"]
        if j["type"] == "pursuit":
            chapters_mod.start_pursuit(a, ch)
        else:
            a.chapter = ch
        engine._emit_chapter_record(a, chapter=ch)
        if j["close"]:
            c = j["close"]
            rec = await engine.close_chapter(a, c["outcome"], trigger="backfill", reason=c["reason"],
                                             ended_minute=c.get("ended_minute"))
            if rec is None:
                print(f"[{a.id}] closure did not run")
                continue
            print(f"[{a.id}] closed ({rec.biography_source}): \"{rec.biography_line}\" "
                  f"residue={rec.emotional_residue}; down-weighted {rec.downweighted_memories} memories, "
                  f"{rec.downweighted_beliefs} beliefs; interlude until day {a.chapter.until_day}")
        else:
            print(f"[{a.id}] initialized as {ch.chapter_type}: \"{ch.title}\"")

    p.on_snapshot(snapshot_mod.capture(engine, world, engine.decisions))
    await p.stop()                                       # drains the queue before disposing
    print(f"\n[done] snapshot updated (schema v{snapshot_mod.SCHEMA_VERSION}); archive id={archive_id}. "
          f"Restart the server with AI_TOWN_RESUME=1 to continue from it.")
    print(router.usage.summary())


if __name__ == "__main__":
    asyncio.run(main())
