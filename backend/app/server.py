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
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from .agents.decision import DecisionEngine
from .llm.factory import build_router
from .simulation.engine import DAY_MIN, Event, SimulationEngine, fmt_time

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "frontend" / "index.html"

TICK_REAL_SECONDS = 0.5
START_MINUTE = 6 * 60  # Day 1, 06:00


class Sim:
    """Owns the engine + real-time pacing + fan-out to websockets."""

    def __init__(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT))
        from data.seed import build_agents, build_locations
        from backend.app.world.world import World

        self.router = build_router()
        self.world = World(build_locations(), build_agents())
        self.engine = SimulationEngine(self.world, DecisionEngine(self.router))
        self.speed: float = 5.0          # sim minutes per real second
        self.paused: bool = False
        self._frac: float = 0.0
        self._new_events: list[Event] = []
        self.engine.bus.subscribers.append(self._new_events.append)
        self.clients: set[WebSocket] = set()
        self.engine.bootstrap(START_MINUTE)

    # ---- pacing loop ----------------------------------------------

    async def loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_REAL_SECONDS)
            if self.paused:
                continue
            self._frac += self.speed * TICK_REAL_SECONDS
            step = int(self._frac)
            if step <= 0:
                continue
            self._frac -= step
            target = self.engine.now + step
            await self.engine.run_until(target)
            self.engine.now = max(self.engine.now, target)  # clock advances even in quiet periods
            await self._broadcast_tick()

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
                }
            )
        return out

    def snapshot(self) -> dict:
        return {
            "type": "snapshot",
            "minute": self.engine.now,
            "clock": fmt_time(self.engine.now),
            "paused": self.paused,
            "speed": self.speed,
            "locations": [
                {"id": l.id, "name": l.name, "kind": l.kind, "x": l.x, "y": l.y}
                for l in self.world.locations.values()
            ],
            "agents": self.agent_states(),
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
            "speed": self.speed,
            "agents": self.agent_states(),
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
    _loop_task = asyncio.create_task(sim.loop())
    yield
    _loop_task.cancel()


app = FastAPI(title="AI Town", lifespan=lifespan)


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
        sim.speed = float(body.get("speed", 5))
        sim.paused = False
    return JSONResponse({"paused": sim.paused, "speed": sim.speed})


@app.get("/api/agents/{agent_id}")
async def agent_detail(agent_id: str) -> JSONResponse:
    assert sim is not None
    a = sim.world.agents.get(agent_id)
    if a is None:
        return JSONResponse({"error": "unknown agent"}, status_code=404)
    traces = [t for t in sim.engine.decisions.traces if t.agent_id == agent_id][-3:]
    return JSONResponse(
        {
            "id": a.id,
            "name": a.name,
            "age": a.profile.age,
            "occupation": a.profile.occupation,
            "traits": a.profile.traits,
            "goals": a.profile.goals,
            "state": {
                "location": sim.world.locations[a.state.location].name,
                "action": a.state.current_action,
                "mood": a.state.mood,
                "energy": a.state.energy,
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
    in_tok = out_tok = 0
    cost = 0.0
    for c in u.calls:
        row = by_task.setdefault(c.task_type, {"calls": 0, "cost": 0.0})
        row["calls"] += 1
        row["cost"] += c.estimated_cost
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
            "by_task": [{"task": k, **{**v, "cost": round(v["cost"], 6)}} for k, v in
                        sorted(by_task.items(), key=lambda kv: -kv[1]["calls"])],
            "decisions": total_decisions,
            "rules_only": rules_only,
        }
    )
