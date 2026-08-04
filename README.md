# AI Town

A generative multi-agent social simulation with a pixel-art town UI. Five LLM-driven
villagers follow daily routines, talk, gossip, fall out, and make up — and you can watch
it unfold, rewind it, or nudge it as a god.

![AI Town — the pixel-art village, event feed, and agent inspector](docs/screenshots/town.png)

> _Screenshot placeholder — drop `town.png` into `docs/screenshots/` (maintainer)._

- **Emergent social drama, not scripted.** Plant a rumor and watch it spread mouth-to-mouth,
  distort in the retelling, erode trust, drive customers away from a shop, and end in an
  on-map confrontation that finally settles it.
- **Free by default.** A deterministic mock provider runs the whole town with no API key, and
  ~90% of agent decisions are resolved by rules before any model is touched. Real models are
  opt-in with a triple free-provider fallback (Groq → Gemini → OpenRouter → mock floor).
- **It remembers, and it replays.** Optional Postgres + pgvector persistence snapshots the whole
  world so a restarted server resumes mid-day; a replay mode scrubs back through recorded history
  on the same map.
- **Agents that form impressions.** Semantic memory distils repeated experience into lasting
  beliefs (with confidence and decay) that bend future dialogue and give relationships inertia.

---

## Features

**Simulation core**
- Event-driven scheduler — each agent has a `next_decision_at`; the clock jumps straight to the
  next one, so sleeping agents cost zero compute.
- 4-level decision funnel (rules → cheap → normal → smart LLM); ~90% of decisions never reach a model.
- Daily routines, needs and energy, arrival interrupts that spark spontaneous conversations.

**Social systems**
- Full rumor lifecycle: seed → spread (two-way, per conversation) → distortion → relationship
  damage → seek-out & confrontation → resolution (a resolved rumor stops propagating).
- Relationships with friendship / trust / conflict, updated from conversation signals in Python.
- Semantic memory: per-agent beliefs about people and places — confidence, sentiment, source
  count, daily decay, and trust inertia (a bad impression is slow to repair, quick to sour).
- Economy: daily wages, cafe meals and revenue, and rumor-driven customer loss (a bad rumor about
  a shopkeeper makes people shun the shop, and the day's takings drop).

**World**
- Day/night cycle (map tints, windows glow), five locations (two homes, cafe, office, park).
- Weather and festivals as God-Mode world effects that reshape where agents go.

**LLM infrastructure**
- 3-tier task routing (cheap / normal / smart) assembled dynamically from whatever keys exist.
- Triple free-provider fallback (Groq / Gemini / OpenRouter) with a mock floor that never fails.
- 429 cooldowns, a per-sim-day dialogue cap, and a budget guard that collapses to free/mock when hit.
- Quality gates on generated JSON, including a CJK gibberish detector, plus language-aware chains
  that route zh free text only through Chinese-capable models.

**Persistence**
- Supabase / Postgres + pgvector: events, memories (vector retrieval), the LLM cost ledger, and
  per-run world snapshots.
- World snapshot & resume — a restarted (or crashed) server continues where it left off.
- Replay mode — pick a past run, drag a timeline, and re-stage its history on the map.

**UI** (single HTML file, no build step)
- Pixel-art village with animated villagers, live WebSocket event feed, and multi-line speech bubbles.
- Force-directed relationship graph; agent inspector (memories, beliefs, decision traces).
- God-Mode panel (rain / festival / rumor seeding), bilingual zh-TW / English, live cost strip.

---

## Quick start

No dependencies, no API key — the built-in mock provider runs everything for free.

**Headless one-day simulation:**

```bash
python scripts/run_day.py              # deterministic mock, prints the day's events
python scripts/run_day.py --traces 8   # also print N decision traces
```

**Live town UI:**

```bash
pip install -r requirements.txt
uvicorn backend.app.server:app         # from the repo root, then open http://localhost:8000
```

Controls: pause, 1× / 5× / 20× speed, language toggle, and the ⚡ God-Mode panel. Click any agent
to open the inspector. The sim auto-suspends when no browser is connected, so an idle tab costs nothing.

### Optional configuration (`.env` in the repo root)

Everything below is optional; with none set, the app runs on the mock provider, in memory.

| Variable | Purpose | Default |
|---|---|---|
| `AI_TOWN_LIVE` | `1` = use real LLM providers instead of the mock | unset (mock) |
| `GROQ_API_KEY` | Groq free tier (fast cheap/smart tiers) | — |
| `GEMINI_API_KEY` | Gemini free tier (normal tier; zh dialogue) | — |
| `OPENAI_API_KEY` | OpenAI models (optional, paid) | — |
| `OPENROUTER_API_KEY` | OpenRouter free models (third free fallback) | — |
| `OPENROUTER_MODEL` | OpenRouter model for English + structured tasks | `openrouter/free` (auto-router) |
| `OPENROUTER_MODEL_ZH` | Chinese-capable free model on the zh chain tail; blank to disable | `google/gemma-4-26b-a4b-it:free` |
| `AI_TOWN_LANG` | Generation + UI default language (`en` / `zh-tw`) | `en` |
| `AI_TOWN_DIALOGUE_CAP` | Max conversations per sim-day (live only; `0` = unlimited) | `12` |
| `AI_TOWN_BUDGET_USD` | Spend cap; providers collapse to free/mock when reached (`≤0` disables) | `1.0` |
| `AI_TOWN_DB_URL` | Postgres + pgvector persistence (`postgresql+asyncpg://…`); unset = in-memory | unset |
| `AI_TOWN_RESUME` | `0` = ignore the last snapshot and start at Day 1 (needs a DB) | `1` |

For the headless script, `--live` forces live mode regardless of `AI_TOWN_LIVE`.

---

## Architecture

```
backend/app/
├── server.py            FastAPI + WebSocket real-time shell, REST API, God Mode, replay
├── simulation/
│   ├── engine.py        event-driven scheduler, clock, daily settlement
│   └── snapshot.py      world serialization for snapshot & resume
├── world/world.py       locations, occupancy, observation, Level-0 execution
├── social/rumors.py     rumor registry + version chains (the telephone game)
├── agents/
│   ├── core.py          state, episodic memory (top-k / vector), semantic memory (beliefs)
│   ├── agent.py         Agent = profile + state + memory + routine + relationships
│   ├── routine.py       daily routines (Level 0 — kills most LLM calls)
│   └── decision.py      4-level decision funnel, conversations, reflection, gibberish gate
├── llm/
│   ├── factory.py       provider chains from env (live/mock, language-aware)
│   ├── router.py        task→tier routing, decision cache, fallback, cooldown, budget
│   ├── prompts/         lean context builders (~600-token prompts)
│   └── providers/       mock, groq, gemini, openai, openrouter, embeddings
└── db/
    ├── models.py        runs, events, memories (pgvector), llm_calls, world_snapshots
    └── persistence.py   async write queue, vector retrieval, snapshots, replay reads
data/seed.py             5 agents, 5 locations, routines engineered to cause encounters
scripts/run_day.py       headless one-day runner
frontend/index.html      single-file town UI (map, feed, graph, inspector, God Mode)
docs/                    event-contract.md, deploy.md
```

**Cost-control philosophy.** The design assumes the LLM is the expensive part and treats it as a
last resort. An event-driven scheduler means idle agents cost nothing; a rules-first decision funnel
resolves routine behaviour without a model; conversations are one JSON call instead of one per turn;
a decision cache makes repeated situations free; cheap models handle the common tiers and the smart
tier is reserved for reflection. In a sample headless mock day that works out to **104 decisions but
only 26 LLM calls — ~90% resolved by rules alone**, and all relationship math stays in Python so the
model only ever emits signals, never final numbers.

---

## Docs

- [`docs/event-contract.md`](docs/event-contract.md) — the structured Event Contract every UI and
  the database render from.
- [`docs/deploy.md`](docs/deploy.md) — container deployment (Railway / Fly.io) and Supabase +
  pgvector setup for persistence, snapshot & resume.

## Roadmap

- One-command cloud deployment (Dockerfile + Compose exist; see `docs/deploy.md`).
- Multi-agent LLM batching to cut wall-clock at higher speeds.
- A larger cast and map, and a richer economy.

## Acknowledgments

Inspired by Stanford's [Generative Agents](https://arxiv.org/abs/2304.03442) and DeepMind's
[Concordia](https://github.com/google-deepmind/concordia).
