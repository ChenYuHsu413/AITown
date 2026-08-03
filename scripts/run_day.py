"""Run one full simulated day with 5 agents -- no UI, no API key needed.

    python scripts/run_day.py                # mock LLM (free, deterministic-ish)
    python scripts/run_day.py --live         # real providers if keys are set
    python scripts/run_day.py --traces 8     # also print N decision traces

Provider config lives in build_router(); swap models there or via env.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.app.agents.decision import DecisionEngine
from backend.app.llm.factory import build_router  # shared: .env, Groq+Gemini, lang, budget
from backend.app.simulation.engine import DAY_MIN, Event, SimulationEngine, fmt_time
from backend.app.world.world import World
from data.seed import build_agents, build_locations


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="use real LLM providers if keys are set")
    parser.add_argument("--traces", type=int, default=5, help="how many decision traces to print")
    args = parser.parse_args()

    # --live forces live; without it, .env / AI_TOWN_LIVE decides.
    router = build_router(live=True if args.live else None)
    world = World(build_locations(), build_agents())
    engine = SimulationEngine(world, DecisionEngine(router))

    # Live event feed to stdout.
    engine.bus.subscribers.append(lambda e: print(e.render()))

    start = 6 * 60          # Day 1, 06:00
    end = DAY_MIN           # Day 1, 24:00
    print(f"=== AI TOWN · simulating {fmt_time(start)} → {fmt_time(end - 1)} ===\n")
    engine.bootstrap(start)
    await engine.run_until(end)

    print("\n=== END-OF-DAY AGENT STATUS ===")
    for a in world.agents.values():
        rels = ", ".join(
            f"{world.agents[k].name}: F{int(r.friendship)}/T{int(r.trust)}"
            for k, r in a.relationships.items()
        ) or "none"
        print(
            f"  {a.name:<6} loc={world.locations[a.state.location].name:<18} "
            f"energy={a.state.energy:>3}  memories={len(a.memory.items):>2}  rel[{rels}]"
        )

    print(f"\n=== DECISION TRACES (last {args.traces}) ===")
    interesting = [t for t in engine.decisions.traces if t.decision.level > 0] or engine.decisions.traces
    for t in interesting[-args.traces:]:
        print(f"\n[{fmt_time(t.minute)}]")
        print(t.render())

    print("\n=== " + engine.decisions.router.usage.summary().replace("\n", "\n=== ") if False else "")
    print("=" * 50)
    print(engine.decisions.router.usage.summary())

    total_decisions = len(engine.decisions.traces)
    llm_calls = engine.decisions.router.usage.total_calls
    cache_hits = engine.decisions.router.usage.cache_hits
    print("=" * 50)
    print(
        f"Decisions made: {total_decisions} | LLM calls: {llm_calls} "
        f"(incl. {cache_hits} cache hits) | "
        f"Rules-only decisions: {total_decisions - sum(1 for t in engine.decisions.traces if t.decision.level > 0)}"
    )


if __name__ == "__main__":
    asyncio.run(main())
