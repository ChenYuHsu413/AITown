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
import re
import time
from pathlib import Path

from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from .agents.core import MemoryItem
from .agents.decision import DecisionEngine, belief_text_ok
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

# Unattended mode: keep the town running with nobody watching, so a user can
# leave it recording history overnight and replay it later. When on, the idle
# auto-pause is skipped and -- once the last viewer leaves -- the clock cruises
# at a slower, quota-friendly speed until someone reconnects.
UNATTENDED = os.environ.get("AI_TOWN_UNATTENDED", "0") != "0"
try:
    UNATTENDED_SPEED = float(os.environ.get("AI_TOWN_UNATTENDED_SPEED", "2") or "2")
except ValueError:
    UNATTENDED_SPEED = 2.0
AWAY_SUMMARY_MIN_SECONDS = 30 * 60.0  # only summarize an absence longer than this real-time gap


class Sim:
    """Owns the engine + real-time pacing + fan-out to websockets."""

    def __init__(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT))
        from data.seed import build_agents, build_locations, seed_secrets
        from backend.app.world.world import World

        self.router = build_router()
        self.live = any(p.name != "mock" for chain in self.router.tiers.values() for p in chain)
        self.world = World(build_locations(), build_agents())
        self.engine = SimulationEngine(self.world, DecisionEngine(self.router))
        seed_secrets(self.engine.decisions.secrets)   # fresh start; a resume overwrites from the snapshot
        self.speed: float = 5.0          # sim minutes per real second
        self.paused: bool = False
        self._frac: float = 0.0
        self._new_events: list[Event] = []
        self.engine.bus.subscribers.append(self._new_events.append)
        self.clients: set[WebSocket] = set()
        self._empty_since: float | None = None  # monotonic time the client set became empty
        self._idle: bool = False                # auto-suspended because no clients are connected
        # Unattended mode: the town keeps running with no viewers; when empty it
        # auto-slows to a quota-friendly cruise speed, restored on reconnect.
        self.unattended: bool = UNATTENDED
        self._user_speed: float = self.speed    # the viewer's chosen live speed, restored on reconnect
        self.unattended_speed: float = min(UNATTENDED_SPEED, MAX_LIVE_SPEED) if self.live else UNATTENDED_SPEED
        self._cruising: bool = False            # currently auto-slowed because nobody's watching
        self._away_since: float | None = None   # monotonic when the last viewer left (None = someone's here)
        self._away_mark: dict | None = None     # {minute, event_idx} captured when the last viewer left
        self.persistence = None          # set by lifespan when DB configured
        self._snap_wall: float = 0.0     # monotonic time of the last periodic snapshot
        self._snap_minute: int = -1      # sim minute at the last periodic snapshot
        # LLM in-flight tracking: the router pings these around each real call so
        # the UI can show a quiet "waiting for AI" hint while a slow call freezes
        # the tick, plus a watchdog that flags a call stuck past 45s.
        self._llm_depth: int = 0
        self._llm_since: float = 0.0     # monotonic when the current call started (0 = idle)
        self._llm_provider: str = ""
        self._llm_task: str = ""
        self._llm_warned: bool = False
        self.router.on_call_start = self._on_llm_start
        self.router.on_call_end = self._on_llm_end
        # Display-layer translation (zh): cache English->zh by text, and a
        # deterministic substitution applied to every result and fallback, so
        # resident names AND place names are always the canonical zh even if a
        # translation slips. Places longest-first so "Ferry Crossing Market" wins
        # before any shorter substring.
        self._translate_cache: dict[str, str] = {}
        self._name_subs = [
            (re.compile(rf"\b{re.escape(a.id.capitalize())}\b", re.IGNORECASE), a.name)
            for a in self.world.agents.values()
        ] + [
            (re.compile(rf"\b{re.escape(l.name)}\b", re.IGNORECASE), l.name_zh)
            for l in sorted(self.world.locations.values(), key=lambda loc: -len(loc.name))
            if l.name_zh
        ]
        self.engine.bootstrap(START_MINUTE)

    def _apply_name_subs(self, text: str) -> str:
        for pat, zh in self._name_subs:
            text = pat.sub(zh, text)
        return text

    async def translate_text(self, text: str) -> str:
        """English knowledge text -> Traditional Chinese for display. Cached by
        text; on any failure falls back to the English original. Either way a
        deterministic pinyin->zh name pass guarantees correct resident names."""
        text = (text or "").strip()
        if not text:
            return text
        hit = self._translate_cache.get(text)
        if hit is not None:
            return hit
        # Already non-English (e.g. historical zh knowledge) -> nothing to translate;
        # just normalize any resident names and return (no LLM call).
        if not re.search(r"[A-Za-z]", text):
            out = self._apply_name_subs(text)
            self._translate_cache[text] = out
            return out
        # Pre-substitute pinyin -> zh names so the model can't phonetically drift
        # them (Aisi -> 阿思); it then translates the English around the fixed names.
        src = self._apply_name_subs(text)
        zh = ""
        try:
            res = await self.router.generate(
                task="translate", messages=builders.translate_prompt(src),
                agent_id="-", sim_minute=self.engine.now,
                schema={"type": "object"}, max_tokens=200,
            )
            if isinstance(res.parsed, dict):
                zh = str(res.parsed.get("text") or "").strip()
        except Exception:
            zh = ""
        out = self._apply_name_subs(zh or src)   # belt-and-suspenders + English fallback
        self._translate_cache[text] = out
        return out

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

    # ---- LLM in-flight state (the "waiting for AI" hint) -------------

    def _on_llm_start(self, provider_name: str, task: str) -> None:
        if self._llm_depth == 0:
            self._llm_since = time.monotonic()
            self._llm_provider = provider_name
            self._llm_task = task
            self._llm_warned = False
        self._llm_depth += 1

    def _on_llm_end(self) -> None:
        self._llm_depth = max(0, self._llm_depth - 1)
        if self._llm_depth == 0:
            self._llm_since = 0.0

    def _llm_busy_ms(self) -> int:
        """Milliseconds the current call has been in flight (0 when idle). The UI
        only shows its hint past a 2s threshold, so a mock's instant call -- which
        pings start/end within the same tick -- never trips it."""
        if self._llm_depth <= 0 or not self._llm_since:
            return 0
        return int((time.monotonic() - self._llm_since) * 1000)

    def _llm_watchdog(self) -> None:
        """Log once if a single call has been in flight past 45s. httpx's own
        timeout should catch it well before; this only makes a genuinely stuck
        call visible in the log -- it never kills anything."""
        if self._llm_depth > 0 and not self._llm_warned and self._llm_since:
            elapsed = time.monotonic() - self._llm_since
            if elapsed > 45:
                print(f"[watchdog] LLM call stuck: {self._llm_provider}/{self._llm_task} "
                      f"{elapsed:.0f}s", flush=True)
                self._llm_warned = True

    async def status_loop(self) -> None:
        """A lightweight heartbeat independent of the main pacing loop: while an
        LLM call blocks a tick (the sim clock is frozen inside run_until), the
        main loop can't broadcast, so this pushes the in-flight state instead.
        Sends only while busy, plus once to clear -- the normal tick carries
        everything else, so quiet periods stay silent."""
        last_busy = False
        while True:
            await asyncio.sleep(0.4)
            self._llm_watchdog()
            busy = self._llm_depth > 0
            if self.clients and (busy or last_busy):
                await self._broadcast_status()
            last_busy = busy

    async def _broadcast_status(self) -> None:
        await self._send_all({
            "type": "status",
            "minute": self.engine.now,
            "clock": fmt_time(self.engine.now),
            "paused": self.paused,
            "idle": self._idle,
            "speed": self.speed,
            "unattended": self.unattended,
            "cruising": self._cruising,
            "llm_busy": self._llm_depth > 0,
            "busy_ms": self._llm_busy_ms(),
        })

    async def _send_all(self, payload: dict) -> None:
        dead = []
        for ws in self.clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    # ---- pacing loop ----------------------------------------------

    def _is_idle(self) -> bool:
        """No clients (past the grace period) -> freeze the clock. Logs each
        suspend/resume transition exactly once. Independent of ``paused``.

        Unattended mode never suspends: the town keeps running with nobody
        watching (it cruises slower instead -- see ``_apply_unattended_speed``)."""
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
        if self.unattended:
            return False  # keep advancing with no viewers; cruise speed handles pacing
        idle = (time.monotonic() - self._empty_since) >= IDLE_GRACE_SECONDS
        if idle and not self._idle:
            self._idle = True
            print("[idle] simulation suspended (no clients)")
        return idle

    def _apply_unattended_speed(self) -> None:
        """Unattended: once the client set has been empty past the grace window,
        drop to the cruise speed to conserve quota; reconnecting restores the
        viewer's speed (handled synchronously in ``register_client`` so the first
        snapshot already reads correctly). Logs each transition once."""
        if not self.unattended:
            return
        empty_past_grace = (
            not self.clients
            and self._empty_since is not None
            and (time.monotonic() - self._empty_since) >= IDLE_GRACE_SECONDS
        )
        if empty_past_grace and not self._cruising:
            self._cruising = True
            self.speed = self.unattended_speed
            print(f"[unattended] no viewers, cruising at {self.speed:g}x", flush=True)

    # ---- client registration + away summary -----------------------

    def register_client(self, ws: WebSocket) -> dict | None:
        """Add a websocket and, if this is the first viewer back after a long
        unattended stretch, return a summary of what they missed (else None).
        Also ends cruise immediately so the first snapshot reads the live speed."""
        first_viewer = not self.clients
        self.clients.add(ws)
        summary: dict | None = None
        if first_viewer:
            if (
                self._away_since is not None
                and self._away_mark is not None
                and (time.monotonic() - self._away_since) >= AWAY_SUMMARY_MIN_SECONDS
            ):
                summary = self._build_away_summary(self._away_mark)
            if self._cruising:
                self._cruising = False
                self.speed = self._user_speed
                print(f"[unattended] viewer connected, back to {self.speed:g}x", flush=True)
        self._away_since = None
        self._away_mark = None
        return summary

    def unregister_client(self, ws: WebSocket) -> None:
        """Drop a websocket; when the last viewer leaves, mark the moment so a
        long absence can be summarized on reconnect."""
        self.clients.discard(ws)
        if not self.clients:
            self._away_since = time.monotonic()
            self._away_mark = {"minute": self.engine.now, "event_idx": len(self.engine.bus.events)}

    def _build_away_summary(self, mark: dict) -> dict:
        """Summarize what happened while nobody was watching: elapsed sim time,
        activity tallies, and up to 10 dated highlights. Highlights come straight
        from the chronicle (the single source of the town's notable beats) -- the
        card and the live Chronicle section render the same data two ways."""
        start = int(mark.get("minute", self.engine.now))
        now = self.engine.now
        idx = int(mark.get("event_idx", 0))
        window = self.engine.bus.events[idx:]

        dialogues = sum(1 for e in window if e.verb == "talk_start")
        beliefs = sum(1 for c in self.engine.chronicle if c["minute"] >= start and c["verb"] == "belief")
        new_rumors = sum(1 for r in self.engine.decisions.rumors.rumors.values()
                         if r.created_minute >= start)
        new_secrets = sum(1 for s in self.engine.decisions.secrets.secrets.values()
                          if s.created_minute >= start)

        major = [{**c, "clock": fmt_time(c["minute"])}
                 for c in self.engine.chronicle if c["minute"] >= start]
        if len(major) > 10:
            major = major[-10:]  # keep the most recent highlights

        return {
            "away_seconds": int(time.monotonic() - self._away_since) if self._away_since else 0,
            "from_minute": start, "to_minute": now,
            "from_clock": fmt_time(start), "to_clock": fmt_time(now),
            "sim_days": round((now - start) / DAY_MIN, 1),
            "dialogues": dialogues,
            "rumors": new_rumors,
            "secrets": new_secrets,
            "beliefs": beliefs,
            "events": major,
        }

    async def loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_REAL_SECONDS)
            if self.paused:
                continue
            if self._is_idle():
                continue  # no clients: don't advance the clock or accumulate _frac
            self._apply_unattended_speed()  # unattended: cruise slower while unwatched
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

    def landmarks_json(self) -> list[dict]:
        """Flat list of every world object with its home location -- the map
        renders the mural's fill from ``progress``/``state``."""
        out = []
        for lid, loc in self.world.locations.items():
            for lm in loc.landmarks:
                out.append({"location": lid, **lm})
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
            "day_of_week": (self.engine.now // (24 * 60)) % 7,   # 0=Mon .. 6=Sun
            "paused": self.paused,
            "idle": self._idle,
            "speed": self.speed,
            "unattended": self.unattended,
            "cruising": self._cruising,
            "locations": [
                {"id": l.id, "name": l.name, "kind": l.kind, "x": l.x, "y": l.y,
                 "owner": l.owner, "landmarks": l.landmarks, "closed_days": l.closed_days,
                 "broken": l.broken}
                for l in self.world.locations.values()
            ],
            "agents": self.agent_states(),
            "effects": self.effects_json(),
            "landmarks": self.landmarks_json(),
            "llm_busy": self._llm_depth > 0,
            "busy_ms": self._llm_busy_ms(),
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
            "unattended": self.unattended,
            "cruising": self._cruising,
            "agents": self.agent_states(),
            "effects": self.effects_json(),
            "landmarks": self.landmarks_json(),
            "llm_busy": self._llm_depth > 0,
            "busy_ms": self._llm_busy_ms(),
            "events": [self._event_json(e) for e in self._new_events],
        }
        self._new_events.clear()
        await self._send_all(payload)


sim: Sim | None = None
_loop_task: asyncio.Task | None = None
_status_task: asyncio.Task | None = None


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global sim, _loop_task, _status_task
    sim = Sim()
    await sim.attach_persistence()
    if sim.unattended:
        print(f"[unattended] enabled -- town keeps running with no viewers; "
              f"cruises at {sim.unattended_speed:g}x while unwatched", flush=True)
    _loop_task = asyncio.create_task(sim.loop())
    _status_task = asyncio.create_task(sim.status_loop())   # in-flight heartbeat
    yield
    _loop_task.cancel()
    _status_task.cancel()
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
    away_summary = sim.register_client(ws)
    snap = sim.snapshot()
    if away_summary is not None:
        snap["away_summary"] = away_summary   # "while you were away" card on reconnect
    await ws.send_json(snap)
    try:
        while True:
            await ws.receive_text()  # client pings; content ignored
    except WebSocketDisconnect:
        sim.unregister_client(ws)


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
        want = min(want, MAX_LIVE_SPEED) if sim.live else want   # live: don't burst rate limits
        sim._user_speed = want                                   # remember it across unattended cruise
        if not sim._cruising:                                    # while cruising, keep the slow speed
            sim.speed = want
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
            "from_secret_id": r.from_secret_id,   # non-empty when this rumor was leaked from a secret
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


@app.get("/api/secrets")
async def secrets() -> JSONResponse:
    """God's-eye view of every secret: who owns it, who's been trusted with it,
    and whether it's been leaked (and by whom). The world itself never sees this."""
    assert sim is not None
    agents = sim.world.agents

    def name_of(aid: str) -> str:
        return agents[aid].name if aid in agents else aid

    out = []
    for s in sim.engine.decisions.secrets.secrets.values():
        out.append({
            "id": s.id,
            "owner": s.owner, "owner_name": name_of(s.owner),
            "text": s.text, "sensitivity": round(s.sensitivity, 2),
            "created_minute": s.created_minute,
            "created_clock": fmt_time(s.created_minute),
            "confided_to": [
                {"id": aid, "name": name_of(aid), "clock": fmt_time(m)}
                for aid, m in s.confided_to.items()
            ],
            "leaked": s.leaked,
            "leaked_by": s.leaked_by, "leaked_by_name": name_of(s.leaked_by) if s.leaked_by else "",
            "about": s.about, "about_name": name_of(s.about) if s.about else "",
            "resolved": s.resolved,
            "resolution": s.resolution,
            "resolved_clock": fmt_time(s.resolved_minute) if s.resolved else "",
        })
    return JSONResponse({"secrets": out})


@app.post("/api/secrets")
async def plant_secret(body: dict) -> JSONResponse:
    """God Mode: plant a secret in an agent's head. It surfaces only if they come
    to trust someone enough to confide it (see decision._maybe_confide)."""
    assert sim is not None
    owner = str(body.get("owner", ""))
    text = str(body.get("text", "")).strip()
    if owner not in sim.world.agents or not text:
        return JSONResponse({"error": "owner (a known agent) and non-empty text required"}, status_code=400)
    try:
        sensitivity = float(body.get("sensitivity", 0.5))
    except (TypeError, ValueError):
        sensitivity = 0.5
    s = sim.engine.decisions.secrets.add(owner, text, sensitivity, sim.engine.now)
    return JSONResponse({"id": s.id, "owner": owner, "text": text, "sensitivity": round(s.sensitivity, 2)})


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


@app.post("/api/break-equipment")
async def break_equipment(body: dict) -> JSONResponse:
    """God Mode (test aid): force an equipment fault at a place so Long gets
    dispatched. {"location": "cafe"}. Homes have nothing to break."""
    assert sim is not None
    lid = str(body.get("location", ""))
    loc = sim.world.locations.get(lid)
    if loc is None or loc.kind == "home":
        return JSONResponse({"error": "location must be a known non-home place"}, status_code=400)
    if not loc.broken:
        loc.broken = True
        sim.engine._publish("system", "breakdown", location_id=lid)
    return JSONResponse({"ok": True, "location": lid, "broken": True})


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
            r_ab = rab.romance if rab else 0.0
            r_ba = rba.romance if rba else 0.0
            stage = (rab.romance_stage if rab and rab.romance_stage != "none"
                     else rba.romance_stage if rba else "none")
            edges.append({
                "a": aid, "b": bid,
                "friendship_ab": round(f_ab), "friendship_ba": round(f_ba),
                "trust_ab": round(t_ab), "trust_ba": round(t_ba),
                "conflict_max": round(max(c_ab, c_ba)),
                "romance_ab": round(r_ab), "romance_ba": round(r_ba),
                "romance_stage": stage,
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
                    "romance": round(r.romance),
                    "romance_stage": r.romance_stage,
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


@app.post("/api/admin/prune-beliefs")
async def prune_beliefs(body: dict = Body(default={})) -> JSONResponse:
    """Drop low-quality beliefs/secrets (the old 'ok' filler) that predate the
    quality gate.

    SAFE BY DEFAULT: a dry run -- it only REPORTS what it would remove (with full
    text) and changes nothing. Send {"dry_run": false} to actually delete; that
    path first writes a pre-operation backup to snapshot_archive (recoverable --
    see docs/admin.md), then removes and snapshots."""
    assert sim is not None
    dry_run = bool(body.get("dry_run", True)) if isinstance(body, dict) else True
    world = sim.world

    doomed_beliefs = [
        {"agent": a.id, "agent_name": a.name, "text": b.text}
        for a in world.agents.values() for b in a.semantic.beliefs
        if not belief_text_ok(b.text, world)
    ]
    doomed_secrets = [
        {"id": sid, "owner": s.owner, "text": s.text}
        for sid, s in sim.engine.decisions.secrets.secrets.items()
        if not belief_text_ok(s.text, world)
    ]

    if dry_run:
        return JSONResponse({
            "dry_run": True,
            "counts": {"beliefs": len(doomed_beliefs), "secrets": len(doomed_secrets)},
            "would_remove_beliefs": doomed_beliefs,
            "would_remove_secrets": doomed_secrets,
            "hint": 'nothing changed -- re-send with {"dry_run": false} to apply',
        })

    # Real delete: back up the exact pre-operation state first, so a mistake is
    # always recoverable (world_snapshots is upsert/single-row; the archive is not).
    archive_id = None
    if sim.persistence is not None:
        payload = snapshot_mod.capture(sim.engine, sim.world, sim.engine.decisions)
        archive_id = await sim.persistence.archive_snapshot(payload, reason="prune-beliefs")
    doomed_ids = {d["id"] for d in doomed_secrets}
    for a in world.agents.values():
        a.semantic.beliefs = [b for b in a.semantic.beliefs if belief_text_ok(b.text, world)]
    for sid in doomed_ids:
        del sim.engine.decisions.secrets.secrets[sid]
    if sim.persistence is not None:
        sim._take_snapshot()
    return JSONResponse({
        "dry_run": False, "archive_id": archive_id,
        "removed_beliefs": len(doomed_beliefs), "removed_secrets": len(doomed_secrets),
    })


@app.post("/api/admin/resolve-stale-secrets")
async def resolve_stale_secrets(body: dict = Body(default={})) -> JSONResponse:
    """Retire secrets whose underlying worry has plainly already been acted on but
    that predate the resolution lifecycle (e.g. Xixi's 'too shy to ask Aisi' after
    he has confided in and repeatedly sought out Aisi).

    SAFE BY DEFAULT: a dry run -- it only REPORTS the candidates and changes
    nothing. Heuristic per unresolved secret: find who it is *about* (the seeded
    `about`, else a resident named in the text) and flag it when the owner has
    already confided it to that person OR has spoken with them >= 3 times. Send
    {"dry_run": false} to apply (writes a pre-op archive, resolves + leaves each
    owner a 'laid to rest' memory, then snapshots)."""
    assert sim is not None
    dry_run = bool(body.get("dry_run", True)) if isinstance(body, dict) else True
    wanted = {str(x) for x in (body.get("secret_ids") or [])} if isinstance(body, dict) else set()
    world = sim.world
    reg = sim.engine.decisions.secrets
    now = sim.engine.now

    def name_of(aid: str) -> str:
        return world.agents[aid].name if aid in world.agents else aid

    def infer_about(s) -> str:
        if s.about:
            return s.about
        for a in world.agents.values():            # fall back to a resident named in the text
            if a.id != s.owner and re.search(rf"\b{re.escape(a.id.capitalize())}\b", s.text):
                return a.id
        return ""

    candidates = []
    for s in reg.secrets.values():
        if s.resolved:
            continue
        about = infer_about(s)
        if not about:
            continue
        owner = world.agents.get(s.owner)
        talk = sum(1 for m in owner.memory.items
                   if m.kind == "conversation" and f"{{agent:{about}}}" in m.text) if owner else 0
        confided = about in s.confided_to
        if confided or talk >= 3:
            candidates.append({
                "id": s.id, "owner": s.owner, "owner_name": name_of(s.owner),
                "about": about, "about_name": name_of(about), "text": s.text,
                "reason": (f"already confided to {name_of(about)}" if confided
                           else f"spoke with {name_of(about)} {talk}x"),
            })

    if dry_run:
        return JSONResponse({
            "dry_run": True,
            "count": len(candidates),
            "would_resolve": candidates,
            "hint": 'nothing changed -- re-send with {"dry_run": false} to apply',
        })

    archive_id = None
    if sim.persistence is not None:
        payload = snapshot_mod.capture(sim.engine, sim.world, sim.engine.decisions)
        archive_id = await sim.persistence.archive_snapshot(payload, reason="resolve-stale-secrets")
    to_apply = [c for c in candidates if not wanted or c["id"] in wanted]
    resolved = 0
    for c in to_apply:
        s = reg.secrets.get(c["id"])
        owner = world.agents.get(c["owner"])
        if s is None or owner is None:
            continue
        resolution = f"{c['owner'].capitalize()} has long since acted on this and moved past it."
        if sim.engine.decisions._resolve_secret(owner, s, now, resolution):
            resolved += 1
    if sim.persistence is not None:
        sim._take_snapshot()
    return JSONResponse({
        "dry_run": False, "archive_id": archive_id,
        "resolved": resolved, "requested": sorted(wanted) or "all-candidates",
    })


@app.get("/api/admin/archives")
async def admin_archives() -> JSONResponse:
    """List recent pre-operation backups (newest first) so a mistaken admin op can
    be traced and recovered (see docs/admin.md). Requires persistence."""
    assert sim is not None
    if sim.persistence is None:
        return JSONResponse({"error": "persistence not enabled"}, status_code=501)
    return JSONResponse({"archives": await sim.persistence.list_archives()})


@app.get("/api/chronicle")
async def chronicle(limit: int = 200) -> JSONResponse:
    """The town's living history -- the notable beats (confide/confront/leak/
    landmark/belief/secret/broke/world-events/weekly-books), newest last. The feed
    tab's Chronicle section seeds from this, then appends live from the event stream."""
    assert sim is not None
    limit = max(1, min(limit, 200))
    items = [{**c, "clock": fmt_time(c["minute"])} for c in sim.engine.chronicle[-limit:]]
    return JSONResponse({"chronicle": items})


@app.post("/api/translate")
async def translate(body: dict) -> JSONResponse:
    """Display-layer translation of English knowledge text -> zh (secrets, beliefs,
    reflection memories). Returns {english: zh} for each input; cached server-side,
    so the same text is translated once. The zh frontend calls this; en never does."""
    assert sim is not None
    texts = body.get("texts") or []
    out: dict[str, str] = {}
    for t in texts:
        if isinstance(t, str) and t.strip() and t not in out:
            out[t] = await sim.translate_text(t)
    return JSONResponse({"translations": out})


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
