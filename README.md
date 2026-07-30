# AI Town — Generative Multi-Agent Social Simulation

Phase 1 core: **5 agents live one full simulated day, headless, at near-zero LLM cost.**

Internals are 100% English (cheaper tokens, more reliable structured output from small
models). Chinese/localization happens only at the future UI display layer.

## Run it

**Headless one-day simulation** (no dependencies, no API key):

```bash
python scripts/run_day.py              # MockProvider, free
python scripts/run_day.py --traces 8   # print more decision traces
```

**Live Town UI** (map, event feed, agent inspector, cost strip):

```bash
pip install fastapi uvicorn
uvicorn backend.app.server:app          # from the repo root
# open http://localhost:8000
```

The server keeps the simulation running as long as it's up (foreground in
your terminal is fine — Ctrl-C stops the town). Controls: pause, 1× / 5× /
20× (sim-minutes per real second; at 20× a full day passes in ~72 s). Click
any agent on the map to open the inspector. The map tints with the town's
day/night cycle, and the bottom strip always shows live LLM cost.

**Real models**: set `OPENAI_API_KEY` / `GEMINI_API_KEY`, `pip install httpx`,
then `AI_TOWN_LIVE=1 uvicorn backend.app.server:app` (or `--live` for the script).

## Architecture (what's implemented)

```
scripts/run_day.py                 headless runner
backend/app/server.py              FastAPI + WebSocket real-time shell
frontend/index.html                Town UI (single file, no build step)
backend/app/
├── simulation/engine.py           SimulationEngine + EventBus + Scheduler + clock
├── world/world.py                 locations, occupancy, observation, Level-0 execution
├── agents/
│   ├── core.py                    Profile / State / EpisodicMemory (top-k retrieval)
│   ├── routine.py                 Routine Engine (Level 0 — kills most LLM calls)
│   ├── agent.py                   Agent = profile+state+memory+routine+relationships
│   └── decision.py                4-level decision funnel + DecisionTrace
└── llm/
    ├── router.py                  task→tier routing, decision cache, fallback chains
    ├── usage.py                   llm_calls ledger (future DB table)
    ├── prompts/builders.py        lean Context Builder (~600-token prompts)
    └── providers/                 base / mock / openai / gemini
data/seed.py                       5 agents, 5 locations, routines that cause encounters
```

## Cost-control design (measured on a sample day)

| Mechanism | Effect |
|---|---|
| Event-driven scheduler (`next_decision_at` heap) | sleeping agents cost zero |
| Routine Engine (Level 0) | ~70–90% of decisions never touch an LLM |
| One-call conversations (JSON turns) | 4 calls → 1 call per chat |
| Decision cache (state fingerprint) | repeat situations are free |
| LLM Router tiers (cheap/normal/smart) | nano/flash for 95%, mini for reflection only |
| Relationship math in Python | LLM emits signals; numbers never drift |

Sample run: **105 decisions → 28 LLM calls** (17 cheap `should_talk`, 11 `dialogue`),
94 decisions were pure rules.

## Swapping in real models

`scripts/run_day.py::build_router` defines the tiers:

```python
cheap : gpt-5-nano  → gemini-2.5-flash-lite → mock   (fallback chain)
normal: gemini-2.5-flash-lite → gpt-5-nano → mock
smart : gpt-5-mini  → gemini-2.5-flash-lite → mock
```

Set `OPENAI_API_KEY` / `GEMINI_API_KEY` and pass `--live`. A provider 429/timeout
automatically falls through the chain. (`pip install httpx` for live mode.)

## Phase 2 (next)

FastAPI + WebSocket event stream → Next.js Town UI (map, event feed, Agent
Inspector, AI-usage dashboard) → PostgreSQL + pgvector memory → reflection
tuning → replay. Memory retrieval already has the `(query, k) -> memories`
contract, so pgvector slots in without touching callers.
