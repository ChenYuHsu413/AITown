"""AI Town server.

    uvicorn backend.app.server:app --reload      # from repo root
    open http://localhost:8000

The simulation runs as a background asyncio task paced to real time
(speed = sim-minutes per real second). Events stream to all connected
browsers over WebSocket; REST endpoints serve the agent inspector and
the AI-usage report. The engine itself is untouched -- the server is a
thin real-time shell around it.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from .agents.core import MemoryItem
from .agents.decision import DecisionEngine
from .llm.factory import build_router
from .llm.prompts import builders
from .simulation import snapshot as snapshot_mod
from .simulation.engine import DAY_MIN, Event, SimulationEngine, fmt_time

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "frontend" / "index.html"

TICK_REAL_SECONDS = 0.5
START_MINUTE = 6 * 60  # Day 1, 06:00
IDLE_GRACE_SECONDS = 10.0  # keep running this long after the last client leaves (survives a page refresh)
MAX_LIVE_SPEED = 5.0       # in live mode, cap speed so LLM calls don't burst past free-tier rate limits
SNAPSHOT_REAL_SECONDS = 60.0  # persist the world at most this often (only when the clock advanced)


class Sim:
    """Owns the engine + real-time pacing + fan-out to websockets."""

    def __init__(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT))
        from data.seed import build_agents, build_locations
        from backend.app.world.world import World

        self.router = build_router()
        self.live = any(p.name != "mock" for chain in self.router.tiers.values() for p in chain)
        self.world = World(build_locations(), build_agents())
        self.engine = SimulationEngine(self.world, DecisionEngine(self.router))
        self.speed: float = 5.0          # sim minutes per real second
        self.paused: bool = False
        self._frac: float = 0.0
        self._new_events: list[Event] = []
        self.engine.bus.subscribers.append(self._new_events.append)
        self.clients: set[WebSocket] = set()
        self._empty_since: float | None = None  # monotonic time the client set became empty
        self._idle: bool = False                # auto-suspended because no clients are connected
        self.persistence = None          # set by lifespan when DB configured
        self._snap_wall: float = 0.0     # monotonic time of the last periodic snapshot
        self._snap_minute: int = -1      # sim minute at the last periodic snapshot
        self.engine.bootstrap(START_MINUTE)

    async def attach_persistence(self) -> None:
        """Optional: activates when AI_TOWN_DB_URL is set. Without it the
        simulation runs fully in-memory, exactly as before.

        With a DB and AI_TOWN_RESUME != 0 (the default), the latest snapshot is
        rehydrated so the town continues where it left off instead of restarting
        at Day 1; on a resume the same run_id is reused so events/memories keep
        accumulating in one run."""
        db_url = os.environ.get("AI_TOWN_DB_URL", "")
        if not db_url:
            return
        from .db.persistence import Persistence

        resume_enabled = os.environ.get("AI_TOWN_RESUME", "1") != "0"
        p = Persistence(db_url)

        restored_minute: int | None = None

        def _restore(minute: int, payload: dict) -> None:
            nonlocal restored_minute
            m = snapshot_mod.restore(payload, self.engine, self.world, self.engine.decisions)
            self.engine.bootstrap(m)   # re-anchor the clock/scheduler onto restored state
            restored_minute = m

        resumed = await p.start(note="server", resume=resume_enabled, restore_cb=_restore)
        self.persistence = p
        self.engine.bus.subscribers.append(p.on_event)
        self.router.usage.on_record = p.on_llm_call
        self.engine.on_snapshot = self._take_snapshot   # snapshot at each daily settlement
        self._snap_wall = time.monotonic()
        self._snap_minute = self.engine.now
        for agent in self.world.agents.values():
            # Mirror FUTURE memories to DB. On a fresh run also backfill the seed
            # memories; on a resume they're already in the same run's `memories`
            # table (and restored in-process from the snapshot), so re-mirroring
            # them would double every row -- skip it.
            agent.memory.on_add = (lambda aid: lambda item: p.on_memory(aid, item))(agent.id)
            if not resumed:
                for item in agent.memory.items:
                    p.on_memory(agent.id, item)
            # Retrieval switches to pgvector cosine search.
            agent.memory.vector_search = p.vector_retriever(agent.id)
        if resumed:
            print(f"[resume] restored from {fmt_time(restored_minute or self.engine.now)} "
                  f"(run {p.run_id})")
        else:
            print("[resume] fresh start")
        print(f"[persistence] enabled, run_id={p.run_id}")

    def _take_snapshot(self) -> None:
        """Serialize the whole world and queue it for an async upsert. Cheap and
        non-blocking; a no-op without persistence."""
        if self.persistence is None:
            return
        payload = snapshot_mod.capture(self.engine, self.world, self.engine.decisions)
        self.persistence.on_snapshot(payload)
        self._snap_minute = self.engine.now
        self._snap_wall = time.monotonic()

    # ---- pacing loop ----------------------------------------------

    def _is_idle(self) -> bool:
        """No clients (past the grace period) -> freeze the clock. Logs each
        suspend/resume transition exactly once. Independent of ``paused``."""
        if self.clients:
            self._empty_since = None
            if self._idle:
                self._idle = False
                # A resuming client already received recent history in its snapshot;
                # drop the queue accumulated while idle so those events aren't
                # double-delivered on the first post-resume tick (e.g. God Mode
                # events fired while nobody was watching).
                self._new_events.clear()
                print("[idle] simulation resumed")
            return False
        if self._empty_since is None:
            self._empty_since = time.monotonic()
        idle = (time.monotonic() - self._empty_since) >= IDLE_GRACE_SECONDS
        if idle and not self._idle:
            self._idle = True
            print("[idle] simulation suspended (no clients)")
        return idle

    async def loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_REAL_SECONDS)
            if self.paused:
                continue
            if self._is_idle():
                continue  # no clients: don't advance the clock or accumulate _frac
            self._frac += self.speed * TICK_REAL_SECONDS
            step = int(self._frac)
            if step <= 0:
                continue
            self._frac -= step
            target = self.engine.now + step
            await self.engine.run_until(target)
            self.engine.now = max(self.engine.now, target)  # clock advances even in quiet periods
            self.engine.expire_world_effects()  # end rain/festival on time even with no pending decisions
            await self._broadcast_tick()
            # Periodic snapshot: every SNAPSHOT_REAL_SECONDS, but only if the
            # clock actually moved since the last one (a paused/idle town isn't
            # re-persisted needlessly). Caps crash loss to ~one interval.
            if (
                self.persistence is not None
                and self.engine.now != self._snap_minute
                and (time.monotonic() - self._snap_wall) >= SNAPSHOT_REAL_SECONDS
            ):
                self._take_snapshot()

    # ---- snapshots ------------------------------------------------

    def agent_states(self) -> list[dict]:
        out = []
        for a in self.world.agents.values():
            loc = self.world.locations[a.state.location]
            out.append(
                {
                    "id": a.id,
                    "name": a.name,
                    "location": loc.id,
                    "x": loc.x,
                    "y": loc.y,
                    "action": a.state.current_action,
                    "mood": a.state.mood,
                    "energy": a.state.energy,
                    "money": round(a.state.money, 2),
                }
            )
        return out

    def effects_json(self) -> list[dict]:
        out = []
        for e in self.world.active_effects:
            lid = e.get("location", "")
            out.append({
                "type": e["type"], "location": lid,
                "location_name": self.world.locations[lid].name if lid in self.world.locations else "",
                "until_minute": e["until_minute"],
            })
        return out

    def snapshot(self) -> dict:
        return {
            "type": "snapshot",
            "minute": self.engine.now,
            "clock": fmt_time(self.engine.now),
            "paused": self.paused,
            "idle": self._idle,
            "speed": self.speed,
            "locations": [
                {"id": l.id, "name": l.name, "kind": l.kind, "x": l.x, "y": l.y, "owner": l.owner}
                for l in self.world.locations.values()
            ],
            "agents": self.agent_states(),
            "effects": self.effects_json(),
            "events": [self._event_json(e) for e in self.engine.bus.events[-40:]],
        }

    @staticmethod
    def _event_json(e: Event) -> dict:
        return {
            "minute": e.minute,
            "clock": fmt_time(e.minute),
            "kind": e.kind,
            "verb": e.verb,
            "actor": e.actor,
            "actor_name": e.actor_name,
            "target": e.target,
            "target_name": e.target_name,
            "location": e.location,
            "location_name": e.location_name,
            "speech": e.text,        # generated free text only (dialogue line / insight)
            "text": e.text_en,       # DEPRECATED: prerendered English sentence (old-frontend compat)
        }

    async def _broadcast_tick(self) -> None:
        payload = {
            "type": "tick",
            "minute": self.engine.now,
            "clock": fmt_time(self.engine.now),
            "paused": self.paused,
            "idle": self._idle,
            "speed": self.speed,
            "agents": self.agent_states(),
            "effects": self.effects_json(),
            "events": [self._event_json(e) for e in self._new_events],
        }
        self._new_events.clear()
        dead = []
        for ws in self.clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


sim: Sim | None = None
_loop_task: asyncio.Task | None = None


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global sim, _loop_task
    sim = Sim()
    await sim.attach_persistence()
    _loop_task = asyncio.create_task(sim.loop())
    yield
    _loop_task.cancel()
    if sim.persistence is not None:
        sim._take_snapshot()          # final graceful-shutdown snapshot...
        await sim.persistence.stop()  # ...which stop() drains before disposing


app = FastAPI(title="AI Town", lifespan=lifespan)


@app.get("/api/history")
async def history(
    minute_from: int = 0, minute_to: int = 10**9, limit: int = 500, run_id: str = "",
) -> JSONResponse:
    """Replay foundation: structured events straight from PostgreSQL.
    ``run_id`` selects the run (defaults to the live one); the frontend pages
    over a long run via the minute window + limit. Requires persistence
    (AI_TOWN_DB_URL); 501 otherwise."""
    assert sim is not None
    if sim.persistence is None:
        return JSONResponse({"error": "persistence not enabled"}, status_code=501)
    rid = run_id or sim.persistence.run_id
    events = await sim.persistence.events_between(minute_from, minute_to, limit, run_id=rid)
    for e in events:
        e["clock"] = fmt_time(e["minute"])
    return JSONResponse({"run_id": rid, "events": events})


@app.get("/api/runs")
async def runs() -> JSONResponse:
    """Catalogue of every simulation run (newest first) for the replay picker,
    plus which one is currently live. Requires persistence; 501 otherwise."""
    assert sim is not None
    if sim.persistence is None:
        return JSONResponse({"error": "persistence not enabled"}, status_code=501)
    return JSONResponse({
        "runs": await sim.persistence.list_runs(),
        "current": sim.persistence.run_id,
    })


@app.get("/healthz")
async def healthz() -> JSONResponse:
    """Liveness probe for the hosting platform. Deliberately touches no
    simulation state and registers no client, so the platform's frequent
    health polling never wakes an idle-suspended town."""
    return JSONResponse({"ok": True})


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(INDEX)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    assert sim is not None
    sim.clients.add(ws)
    await ws.send_json(sim.snapshot())
    try:
        while True:
            await ws.receive_text()  # client pings; content ignored
    except WebSocketDisconnect:
        sim.clients.discard(ws)


@app.get("/api/state")
async def state() -> JSONResponse:
    assert sim is not None
    return JSONResponse(sim.snapshot())


@app.post("/api/control")
async def control(body: dict) -> JSONResponse:
    assert sim is not None
    cmd = body.get("cmd")
    if cmd == "pause":
        sim.paused = True
    elif cmd == "play":
        sim.paused = False
    elif cmd == "speed":
        want = float(body.get("speed", 5))
        sim.speed = min(want, MAX_LIVE_SPEED) if sim.live else want   # live: don't burst rate limits
        sim.paused = False
    return JSONResponse({"paused": sim.paused, "speed": sim.speed})


@app.post("/api/rumors")
async def seed_rumor(body: dict) -> JSONResponse:
    """God Mode: plant a rumor in an agent's head. It then spreads through
    conversations (and drifts in the retelling). Returns the new rumor id."""
    assert sim is not None
    agent_id = str(body.get("agent_id", ""))
    text = str(body.get("text", "")).strip()
    agent = sim.world.agents.get(agent_id)
    if agent is None or not text:
        return JSONResponse({"error": "agent_id (known) and non-empty text required"}, status_code=400)

    subject = str(body.get("subject", ""))
    if body.get("sentiment") is not None:
        sentiment = float(body["sentiment"])
    else:
        # No polarity given -> classify it once (cheap tier).
        res = await sim.router.generate(
            task="appraise",
            messages=builders.appraise_prompt(text),
            agent_id=agent_id, sim_minute=sim.engine.now,
            schema={"type": "object"}, max_tokens=30,
        )
        sentiment = float(res.parsed.get("sentiment", 0.0)) if isinstance(res.parsed, dict) else 0.0

    rumor = sim.engine.decisions.rumors.seed(agent_id, text, sim.engine.now, subject=subject, sentiment=sentiment)
    agent.memory.add(MemoryItem(
        minute=sim.engine.now, text=text, importance=4, kind="rumor", rumor_id=rumor.id,
    ))
    return JSONResponse({"id": rumor.id, "origin": agent_id, "text": text,
                         "subject": subject, "sentiment": sentiment})


@app.get("/api/rumors")
async def list_rumors() -> JSONResponse:
    """Full version chain per rumor -- the telephone-game observability core."""
    assert sim is not None
    agents = sim.world.agents

    def name_of(aid: str) -> str:
        return agents[aid].name if aid in agents else aid

    out = []
    for r in sim.engine.decisions.rumors.rumors.values():
        out.append({
            "id": r.id,
            "origin": r.origin,
            "origin_name": name_of(r.origin),
            "subject": r.subject,
            "subject_name": name_of(r.subject) if r.subject else "",
            "sentiment": r.sentiment,
            "created_minute": r.created_minute,
            "created_clock": fmt_time(r.created_minute),
            "resolved": r.resolved,
            "resolved_minute": r.resolved_minute,
            "resolved_clock": fmt_time(r.resolved_minute) if r.resolved_minute >= 0 else "",
            "outcome": r.outcome,
            "versions": [
                {
                    "agent_id": v.agent_id,
                    "agent_name": name_of(v.agent_id),
                    "text": v.text,
                    "heard_from": v.heard_from,
                    "heard_from_name": name_of(v.heard_from) if v.heard_from else "",
                    "minute": v.minute,
                    "clock": fmt_time(v.minute),
                }
                for v in r.versions
            ],
        })
    return JSONResponse({"rumors": out})


@app.get("/api/economy")
async def economy() -> JSONResponse:
    """Money in wallets + each shop's takings -- the economy at a glance."""
    assert sim is not None
    agents = sim.world.agents

    def name_of(aid: str) -> str:
        return agents[aid].name if aid in agents else aid

    locations = [
        {
            "id": l.id,
            "name": l.name,
            "kind": l.kind,
            "owner": l.owner,
            "owner_name": name_of(l.owner) if l.owner else "",
            "price": l.price,
            "revenue": round(l.revenue, 2),
            "revenue_today": round(l.revenue_today, 2),
        }
        for l in sim.world.locations.values()
        if l.owner or l.price > 0
    ]
    wallets = [
        {"id": a.id, "name": a.name, "money": round(a.state.money, 2),
         "daily_wage": a.profile.daily_wage}
        for a in agents.values()
    ]
    return JSONResponse({"locations": locations, "agents": wallets})


@app.post("/api/world-event")
async def world_event(body: dict) -> JSONResponse:
    """God Mode: start (or extend) a world effect. Same-type effects never
    stack -- a repeat just extends the window.
      {"type": "rain", "duration_minutes": 180}
      {"type": "festival", "location": "park", "duration_minutes": 240}"""
    assert sim is not None
    etype = str(body.get("type", ""))
    if etype not in ("rain", "festival"):
        return JSONResponse({"error": "type must be 'rain' or 'festival'"}, status_code=400)
    try:
        duration = int(body.get("duration_minutes", 180))
    except (TypeError, ValueError):
        return JSONResponse({"error": "duration_minutes must be an integer"}, status_code=400)
    duration = max(1, min(duration, 24 * 60))
    location = str(body.get("location", ""))
    if etype == "festival":
        if location not in sim.world.locations:
            return JSONResponse({"error": "festival requires a known 'location'"}, status_code=400)
    else:
        location = ""  # rain is town-wide
    eff = sim.engine.trigger_world_effect(etype, location, duration)
    return JSONResponse({"ok": True, "effect": eff})


@app.get("/api/relationships")
async def relationships() -> JSONResponse:
    """Town-wide social graph. One undirected edge per pair that has a
    relationship record in EITHER direction; both directions' numbers ride
    along so the UI can show asymmetry (one-sided trust after a rumor). A
    missing direction falls back to the neutral baseline (a fresh Relationship
    is 30/30/0), which is exactly what `agent.rel()` would return."""
    assert sim is not None
    agents = sim.world.agents
    ids = list(agents)
    nodes = [
        {"id": a.id, "name": a.name, "occupation": a.profile.occupation}
        for a in agents.values()
    ]
    edges = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            aid, bid = ids[i], ids[j]
            rab = agents[aid].relationships.get(bid)
            rba = agents[bid].relationships.get(aid)
            if rab is None and rba is None:
                continue  # never interacted -> no edge
            f_ab, t_ab, c_ab = (rab.friendship, rab.trust, rab.conflict) if rab else (30.0, 30.0, 0.0)
            f_ba, t_ba, c_ba = (rba.friendship, rba.trust, rba.conflict) if rba else (30.0, 30.0, 0.0)
            edges.append({
                "a": aid, "b": bid,
                "friendship_ab": round(f_ab), "friendship_ba": round(f_ba),
                "trust_ab": round(t_ab), "trust_ba": round(t_ba),
                "conflict_max": round(max(c_ab, c_ba)),
            })
    return JSONResponse({"nodes": nodes, "edges": edges})


@app.get("/api/agents/{agent_id}")
async def agent_detail(agent_id: str) -> JSONResponse:
    assert sim is not None
    a = sim.world.agents.get(agent_id)
    if a is None:
        return JSONResponse({"error": "unknown agent"}, status_code=404)
    traces = [t for t in sim.engine.decisions.traces if t.agent_id == agent_id][-3:]

    def subject_name(sid: str) -> str:
        if sid == "self":
            return a.name
        if sid in sim.world.agents:
            return sim.world.agents[sid].name
        if sid in sim.world.locations:
            return sim.world.locations[sid].name
        return sid

    return JSONResponse(
        {
            "id": a.id,
            "name": a.name,
            "age": a.profile.age,
            "occupation": a.profile.occupation,
            "traits": a.profile.traits,
            "goals": a.profile.goals,
            "beliefs": [
                {
                    "subject": b.subject, "subject_name": subject_name(b.subject),
                    "text": b.text, "confidence": round(b.confidence, 2),
                    "sentiment": round(b.sentiment, 2), "source_count": b.source_count,
                }
                for b in sorted(a.semantic.beliefs, key=lambda x: x.confidence, reverse=True)
            ],
            "state": {
                "location": sim.world.locations[a.state.location].name,
                "action": a.state.current_action,
                "mood": a.state.mood,
                "energy": a.state.energy,
                "money": round(a.state.money, 2),
            },
            "memories": [
                {"clock": fmt_time(m.minute), "text": m.text, "importance": m.importance, "kind": m.kind}
                for m in a.memory.items[-10:]
            ][::-1],
            "relationships": [
                {
                    "id": k,
                    "name": sim.world.agents[k].name,
                    "friendship": round(r.friendship),
                    "trust": round(r.trust),
                    "conflict": round(r.conflict),
                }
                for k, r in a.relationships.items()
            ],
            "last_decisions": [
                {
                    "clock": fmt_time(t.minute),
                    "observation": t.observation,
                    "memories": t.retrieved_memories,
                    "action": t.decision.action
                    + (f" -> {sim.world.agents[t.decision.talk_partner].name}" if t.decision.talk_partner else ""),
                    "level": t.decision.level,
                    "model": t.model,
                    "reason": t.decision.reason,
                }
                for t in traces
            ][::-1],
        }
    )


@app.get("/api/usage")
async def usage() -> JSONResponse:
    assert sim is not None
    u = sim.router.usage
    by_task: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    in_tok = out_tok = 0
    cost = 0.0
    for c in u.calls:
        row = by_task.setdefault(c.task_type, {"calls": 0, "cost": 0.0})
        row["calls"] += 1
        row["cost"] += c.estimated_cost
        mrow = by_model.setdefault(f"{c.provider}/{c.model}", {"calls": 0, "cost": 0.0})
        mrow["calls"] += 1
        mrow["cost"] += c.estimated_cost
        in_tok += c.input_tokens
        out_tok += c.output_tokens
        cost += c.estimated_cost
    total_decisions = len(sim.engine.decisions.traces)
    rules_only = sum(1 for t in sim.engine.decisions.traces if t.decision.level == 0)
    return JSONResponse(
        {
            "calls": u.total_calls,
            "cache_hits": u.cache_hits,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "estimated_cost": round(cost, 6),
            "budget_usd": sim.router.budget_usd,
            "by_task": [{"task": k, **{**v, "cost": round(v["cost"], 6)}} for k, v in
                        sorted(by_task.items(), key=lambda kv: -kv[1]["calls"])],
            "by_model": [{"model": k, **{**v, "cost": round(v["cost"], 6)}} for k, v in
                         sorted(by_model.items(), key=lambda kv: -kv[1]["calls"])],
            "decisions": total_decisions,
            "rules_only": rules_only,
        }
    )
