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
import hashlib
import contextlib
import os
import re
import time
from pathlib import Path

from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from .agents import chapters as chapters_mod
from .agents.core import MemoryItem
from .agents.decision import DecisionEngine, belief_text_ok
from .llm.factory import build_router
from .llm.prompts import builders
from .llm.router import _is_garbage_text
from .simulation import snapshot as snapshot_mod
from .simulation.engine import DAY_MIN, Event, SimulationEngine, fmt_time

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "frontend" / "index.html"

TICK_REAL_SECONDS = 0.5
START_MINUTE = 6 * 60  # Day 1, 06:00
IDLE_GRACE_SECONDS = 10.0  # keep running this long after the last client leaves (survives a page refresh)
MAX_LIVE_SPEED = 5.0       # in live mode, cap speed so LLM calls don't burst past free-tier rate limits
SNAPSHOT_REAL_SECONDS = 60.0  # persist the world at most this often (only when the clock advanced)
# Per-client WebSocket send timeout. A frozen browser tab keeps its socket open but
# stops reading it, so the OS send buffer fills and `ws.send_json` blocks on
# backpressure -- which, in the broadcast, would stall the whole pacing loop. Past
# this we drop the client (it auto-reconnects) rather than let one dead viewer freeze
# the town. Generous: a healthy client sends instantly.
WS_SEND_TIMEOUT_S = 5.0
# Pacing-loop observability: a heartbeat line every N real-seconds, and a watchdog
# that flags the loop if a single iteration phase stalls past the threshold -- so a
# future freeze is self-locating (which phase, which minute) instead of a guess.
PACE_HEARTBEAT_S = 30.0
PACE_STALL_WARN_S = 15.0

# Daytime pacing: the fixed live speed while the town is awake. There are no manual
# speed tiers -- days run at DAY_SPEED (real time by default), nights auto fast-forward
# (see NIGHT_SPEED). DAY_SPEED sits below MAX_LIVE_SPEED so free mode never clamps it.
try:
    DAY_SPEED = float(os.environ.get("AI_TOWN_DAY_SPEED", "1") or "1")
except ValueError:
    DAY_SPEED = 1.0

# Unattended mode: keep the town running with nobody watching, so a user can
# leave it recording history overnight and replay it later. When on, the idle
# auto-pause is skipped and -- once the last viewer leaves -- the clock cruises
# at a slower, quota-friendly speed until someone reconnects.
UNATTENDED = os.environ.get("AI_TOWN_UNATTENDED", "0") != "0"
try:
    UNATTENDED_SPEED = float(os.environ.get("AI_TOWN_UNATTENDED_SPEED", "2") or "2")
except ValueError:
    UNATTENDED_SPEED = 2.0

# Night skip: when the whole town is asleep with no LLM work in flight, fast-forward
# through the dead hours so a 1x viewer doesn't wait out eight sim-hours. An all-asleep
# town makes zero LLM calls, so this speed is honest even in free mode (it's exempt from
# the MAX_LIVE_SPEED clamp -- see _apply_night_skip). Restores the viewer's own gear the
# moment anyone stirs, or NIGHT_WAKE_LEAD_MIN sim-min before the earliest routine wake so
# morning opens at their chosen speed rather than mid-fast-forward.
try:
    NIGHT_SPEED = float(os.environ.get("AI_TOWN_NIGHT_SPEED", "20") or "20")
except ValueError:
    NIGHT_SPEED = 20.0
NIGHT_WAKE_LEAD_MIN = 15  # restore this many sim-min before the earliest scheduled wake

AWAY_SUMMARY_MIN_SECONDS = 30 * 60.0  # only summarize an absence longer than this real-time gap

# Display-layer translation retry (the "no English left on screen" policy). When a
# translation fails the whole chain (deepseek -> gemini -> ...), the English original
# is shown but NOT cached, and the text is queued for a background retry so it turns
# to zh on a later panel-open without the user lifting a finger.
try:
    TRANSLATE_RETRY_WAIT_S = float(os.environ.get("AI_TOWN_TRANSLATE_RETRY_WAIT", "30") or "30")
except ValueError:
    TRANSLATE_RETRY_WAIT_S = 30.0
try:
    TRANSLATE_RETRY_MAX = int(os.environ.get("AI_TOWN_TRANSLATE_RETRY_MAX", "3"))
except ValueError:
    TRANSLATE_RETRY_MAX = 3

# Display translation is a low-priority chore that must never crowd the live
# performance: dialogue/reflection each own their own engine semaphore, and translate
# gets this SEPARATE pool of 1 so at most one translation chain runs at a time. It
# therefore yields the shared providers/event-loop to conversation work instead of
# racing it (both the foreground /api/translate path and the background backfill
# worker acquire it).
TRANSLATE_MAX_CONCURRENT = 1

# Per-provider timeout for the cheap out-of-tick LLM calls (translate on its worker,
# appraise on the /api/rumors handler). They don't park the pacing loop, but the same
# rule applies -- a single hung provider should steal at most this long before the
# chain falls through -- so no call site can hang indefinitely on a wedged provider.
try:
    SYNC_CALL_TIMEOUT_S = float(os.environ.get("AI_TOWN_SYNC_CALL_TIMEOUT", "10") or "10")
except ValueError:
    SYNC_CALL_TIMEOUT_S = 10.0


class Sim:
    """Owns the engine + real-time pacing + fan-out to websockets."""

    def __init__(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT))
        from data.seed import build_agents, build_locations, seed_secrets
        from backend.app.world.world import World

        self.router = build_router()
        self.live = any(p.name != "mock" for chain in self.router.tiers.values() for p in chain)
        # Paid mode lifts the live speed clamp (paid providers aren't on a free-tier
        # rate limit), so 20x is honest instead of silently capped to 5x.
        self.paid = os.environ.get("AI_TOWN_PAID") == "1"
        self.world = World(build_locations(), build_agents())
        self.engine = SimulationEngine(self.world, DecisionEngine(self.router))
        seed_secrets(self.engine.decisions.secrets)   # fresh start; a resume overwrites from the snapshot
        self.speed: float = DAY_SPEED    # sim minutes per real second (fixed daytime pace)
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
        self._user_speed: float = self.speed    # daytime live speed, restored after an unattended cruise
        self.unattended_speed: float = min(UNATTENDED_SPEED, MAX_LIVE_SPEED) if self.live else UNATTENDED_SPEED
        self._cruising: bool = False            # currently auto-slowed because nobody's watching
        # Night skip: fast-forward through the sleeping hours (see NIGHT_SPEED).
        self.night_speed: float = NIGHT_SPEED
        self._night_cruising: bool = False      # currently fast-forwarding through the night
        self._away_since: float | None = None   # monotonic when the last viewer left (None = someone's here)
        self._away_mark: dict | None = None     # {minute, event_idx} captured when the last viewer left
        self.persistence = None          # set by lifespan when DB configured
        self._snap_wall: float = 0.0     # monotonic time of the last periodic snapshot
        self._snap_minute: int = -1      # sim minute at the last periodic snapshot
        # Pacing-loop heartbeat/watchdog state (see PACE_* constants).
        self._pace_phase: str = "init"   # which loop phase we're in (for the stall watchdog)
        self._pace_beat: float = 0.0     # monotonic at the start of the current iteration
        self._pace_hb: float = 0.0       # monotonic of the last heartbeat line
        self._pace_warned: bool = False  # stall already logged for this stall episode
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
        # Background translation retry/backfill queue: a text that failed to translate
        # (English shown, uncached) is queued here and re-tried after a delay, up to
        # TRANSLATE_RETRY_MAX times, so English self-heals to zh without user action.
        self._tr_queue: asyncio.Queue[str] = asyncio.Queue()
        self._tr_attempts: dict[str, int] = {}   # text -> failed attempts so far
        self._tr_inflight: set[str] = set()       # texts queued or awaiting requeue (dedupe)
        # Texts that exhausted TRANSLATE_RETRY_MAX background attempts: the daily
        # backfill scan skips these so a permanently-failing phrase can't be re-queued
        # every sim-day forever (perpetual night LLM). A user opening the panel still
        # retries them on demand (translate_text -> one fresh attempt).
        self._tr_gaveup: set[str] = set()
        self._tr_worker: asyncio.Task | None = None
        self._tr_backfill_day: int = -1           # last sim-day the display layer was scanned
        # translate's own concurrency pool (see TRANSLATE_MAX_CONCURRENT): keeps
        # display translation from competing with dialogue/reflection for capacity.
        self._translate_sem = asyncio.Semaphore(TRANSLATE_MAX_CONCURRENT)
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

    async def _translate_once(self, src: str) -> tuple[str, str]:
        """One translation attempt over the whole chain. Returns (zh_text, model), or
        ("", "") if it failed the gate / errored (so the caller can fall back + queue a
        retry). Held to the translate-only semaphore so it yields to dialogue/reflection."""
        try:
            async with self._translate_sem:   # low-priority: at most one translate chain at a time
                res = await self.router.generate(
                    task="translate", messages=builders.translate_prompt(src),
                    agent_id="-", sim_minute=self.engine.now,
                    schema={"type": "object"}, max_tokens=200,
                    per_call_timeout=SYNC_CALL_TIMEOUT_S,   # a wedged provider must not hold the translate slot
                    # The router's universal gate already rejects a junk "text" (a bare
                    # number, an "ok" dodge) and falls through; this local validate is a
                    # thin echo of it for the same-provider retry.
                    validate=lambda r: isinstance(r.parsed, dict)
                    and not _is_garbage_text(r.parsed.get("text")),
                )
            if isinstance(res.parsed, dict):
                cand = str(res.parsed.get("text") or "").strip()
                if not _is_garbage_text(cand):   # never let a dodge ("ok") reach the display
                    return cand, f"{res.provider}/{res.model}"
        except Exception:
            pass
        return "", ""

    def _persist_translation(self, source: str, translated: str, model: str, gave_up: bool) -> None:
        """Land a translation (or a gave-up marker) in the DB so it survives a restart.
        No-op without persistence; failures never touch what's on screen (queued async)."""
        if self.persistence is None:
            return
        lang = builders.lang_code()
        h = hashlib.sha256(f"{lang}|{source}".encode()).hexdigest()
        try:
            self.persistence.on_translation(
                text_hash=h, source_text=source, translated_text=translated,
                lang=lang, model=model, gave_up=gave_up)
        except Exception:
            pass

    async def translate_text(self, text: str) -> str:
        """English knowledge text -> Traditional Chinese for display. Cached by text;
        on failure falls back to the English original AND queues a background retry so
        the English self-heals to zh on a later panel-open (never pinned). Either way a
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
        zh, model = await self._translate_once(src)
        if zh:
            out = self._apply_name_subs(zh)
            self._translate_cache[text] = out       # only cache a REAL translation
            self._persist_translation(text, out, model, gave_up=False)   # translate once, keep forever
            return out
        self._enqueue_translation(text)             # English shown now, retried in the background
        return self._apply_name_subs(src)           # English original is the fallback, never junk

    # ---- background translation retry / backfill ----------------------
    def _enqueue_translation(self, text: str) -> None:
        """Queue an English text for a background retry (deduped). Lazily starts the
        worker on the running loop."""
        text = (text or "").strip()
        if not text or text in self._translate_cache or text in self._tr_inflight:
            return
        if not re.search(r"[A-Za-z]", text):   # nothing to translate
            return
        self._tr_inflight.add(text)
        self._tr_queue.put_nowait(text)
        if self._tr_worker is None or self._tr_worker.done():
            self._tr_worker = asyncio.ensure_future(self._translation_worker())

    async def _translation_worker(self) -> None:
        """Drains the retry queue: each text gets another whole-chain attempt; on
        success it lands in the cache (so the next panel-open shows zh), on failure it
        is requeued after TRANSLATE_RETRY_WAIT_S, up to TRANSLATE_RETRY_MAX attempts.
        After that it's marked given-up (skipped by the daily backfill so it can't
        cycle forever) -- still uncached, so a fresh panel-open retries it on demand."""
        while True:
            text = await self._tr_queue.get()
            try:
                if text in self._translate_cache:
                    self._tr_inflight.discard(text)   # already filled in elsewhere
                    continue
                src = self._apply_name_subs(text)
                zh, model = await self._translate_once(src)
                if zh:
                    out = self._apply_name_subs(zh)
                    self._translate_cache[text] = out
                    self._tr_attempts.pop(text, None)
                    self._tr_inflight.discard(text)
                    self._tr_gaveup.discard(text)     # a retry (e.g. panel-open) finally succeeded
                    self._persist_translation(text, out, model, gave_up=False)
                else:
                    n = self._tr_attempts.get(text, 0) + 1
                    self._tr_attempts[text] = n
                    if n < TRANSLATE_RETRY_MAX:
                        asyncio.ensure_future(self._requeue_after(text, TRANSLATE_RETRY_WAIT_S))
                    else:  # gave up -> mark it so the daily backfill won't re-queue it forever
                        self._tr_attempts.pop(text, None)
                        self._tr_inflight.discard(text)
                        self._tr_gaveup.add(text)
                        self._persist_translation(text, "", "", gave_up=True)   # don't retry it next boot
            except Exception:
                self._tr_inflight.discard(text)
            finally:
                self._tr_queue.task_done()

    async def _requeue_after(self, text: str, delay: float) -> None:
        await asyncio.sleep(delay)
        if text not in self._translate_cache:
            self._tr_queue.put_nowait(text)      # stays in _tr_inflight across the wait
        else:
            self._tr_inflight.discard(text)

    def backfill_translations(self) -> int:
        """Scan the current display layer -- chronicle beats, held secrets, agent
        beliefs, live rumors -- and queue every English string not yet translated, so
        the user never has to open a panel to trigger the first translation (and any
        English left from a prior run turns to zh in the background). Returns how many
        new texts were queued. No-op in en mode."""
        if not builders.lang_is_zh():
            return 0
        before = len(self._tr_inflight)
        texts: list[str] = []
        for c in self.engine.chronicle:                      # the town's living history
            # chronicle entries carry the free text as "speech" (=ev.text) plus "detail";
            # a belief/insight beat's impression lives in "speech", NOT "text".
            for key in ("speech", "detail"):
                v = c.get(key)
                if isinstance(v, str):
                    texts.append(v)
        for s in self.engine.decisions.secrets.secrets.values():
            texts.append(s.text)                             # currently-held secrets
            texts.append(s.resolution)                       # ...and the "laid to rest" note
        for a in self.world.agents.values():
            for b in a.semantic.beliefs:                     # lasting impressions
                texts.append(getattr(b, "text", ""))
        for r in self.engine.decisions.rumors.rumors.values():
            for v in r.versions:
                texts.append(v.text)                         # every phrasing in circulation
        for t in texts:
            t = (t or "").strip()
            if (t and re.search(r"[A-Za-z]", t) and t not in self._translate_cache
                    and t not in self._tr_gaveup):   # don't perpetually re-queue a dead phrase
                self._enqueue_translation(t)
        return len(self._tr_inflight) - before

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
        # Rehydrate the translation cache: a restart should NOT re-translate the whole
        # standing corpus (the boot-time queue storm + repeat spend). Load every cached
        # translation + gave-up marker for this language into memory, so the startup
        # backfill queues only genuinely-new English (see backfill_translations).
        if builders.lang_is_zh():
            try:
                rows = await p.load_translation_cache(builders.lang_code())
                for r in rows:
                    if r["gave_up"]:
                        self._tr_gaveup.add(r["source"])
                    elif r["translated"]:
                        self._translate_cache[r["source"]] = r["translated"]
                print(f"[translate] loaded {len(self._translate_cache)} cached translations + "
                      f"{len(self._tr_gaveup)} gave-up from DB", flush=True)
            except Exception as err:
                print(f"[translate] cache load failed (continuing memory-only): {err}", flush=True)
        self.engine.bus.subscribers.append(p.on_event)
        self.router.usage.on_record = p.on_llm_call
        self.engine.on_snapshot = self._take_snapshot   # snapshot at each daily settlement
        self.engine.on_chapter_record = p.on_chapter    # chapter ledger (started / closed rows)
        self.engine.on_wish_record = p.on_wish          # wish ledger (snapshot remains source of truth)
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
            # Retrieval switches to pgvector cosine search (memory passed so the
            # resolved-worry down-weight applies here too).
            agent.memory.vector_search = p.vector_retriever(agent.id, agent.memory)
        # Rebuild the resolved-worry theme suppressions from the restored secrets.
        self.engine.decisions.rebuild_suppressed_themes(self.world)
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

    def _pace_heartbeat(self) -> None:
        """One heartbeat line every PACE_HEARTBEAT_S while running, so the log always
        carries the loop's live position. If these lines STOP, the loop stalled -- and
        the stall watchdog (in status_loop) names the phase it stalled in."""
        now = time.monotonic()
        if now - self._pace_hb < PACE_HEARTBEAT_S:
            return
        self._pace_hb = now
        e = self.engine
        print(f"[pace] min={e.now} {fmt_time(e.now)} speed={self.speed:g}x "
              f"cruise={'night' if self._night_cruising else 'unatt' if self._cruising else 'no'} "
              f"clients={len(self.clients)} dlg={len(e._in_dialogue)} refl={len(e._reflecting)} "
              f"tr_q={self._tr_queue.qsize()} llm_depth={self._llm_depth}", flush=True)

    def _pace_watchdog(self) -> None:
        """Flag (once) if the pacing loop's current iteration has run past
        PACE_STALL_WARN_S -- names the phase and minute so a freeze locates itself."""
        if self._pace_beat <= 0 or self._pace_warned or self.paused or self._idle:
            return
        elapsed = time.monotonic() - self._pace_beat
        if elapsed > PACE_STALL_WARN_S:
            print(f"[watchdog] pacing loop stalled in phase '{self._pace_phase}' at "
                  f"minute {self.engine.now} for {elapsed:.0f}s (clients={len(self.clients)}, "
                  f"llm_depth={self._llm_depth})", flush=True)
            self._pace_warned = True

    async def status_loop(self) -> None:
        """A lightweight heartbeat independent of the main pacing loop: while an
        LLM call blocks a tick (the sim clock is frozen inside run_until), the
        main loop can't broadcast, so this pushes the in-flight state instead.
        Sends only while busy, plus once to clear -- the normal tick carries
        everything else, so quiet periods stay silent. Also runs the pacing-loop
        stall watchdog (independent of the main loop, so it fires even mid-stall)."""
        last_busy = False
        while True:
            await asyncio.sleep(0.4)
            self._llm_watchdog()
            self._pace_watchdog()
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
            "night": self._night_cruising,
            "user_speed": self._user_speed,
            "llm_busy": self._llm_depth > 0,
            "busy_ms": self._llm_busy_ms(),
        })

    async def _send_all(self, payload: dict) -> None:
        # Send to every client CONCURRENTLY and bound each send with WS_SEND_TIMEOUT_S.
        # A frozen tab (socket open, never drained) backpressures ws.send_json, which --
        # done sequentially and unbounded, as before -- stalled the whole pacing loop and
        # starved every other viewer (the observed non-LLM night freeze). Now a stuck or
        # errored client is dropped (it auto-reconnects) and healthy clients are never
        # held up by it.
        if not self.clients:
            return

        async def _one(ws):
            try:
                await asyncio.wait_for(ws.send_json(payload), timeout=WS_SEND_TIMEOUT_S)
                return None
            except Exception:
                return ws   # timed out on backpressure, or the socket errored -> drop
        drop = await asyncio.gather(*[_one(ws) for ws in list(self.clients)])
        for ws in drop:
            if ws is not None:
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

    # ---- night skip -----------------------------------------------

    def _town_all_asleep(self) -> bool:
        agents = self.world.agents.values()
        return bool(agents) and all(a.state.current_action == "sleep" for a in agents)

    def _dialogue_or_reflection_inflight(self) -> bool:
        """Any dialogue or reflection still generating. Night skip waits for these --
        a late-night conversation must play out at normal speed -- but deliberately
        NOT for display-layer translation. Translation is a background chore that has
        nothing to do with the sleeping town; counting it (it bumps ``_llm_depth`` like
        any call) used to pin the town awake all night, since the backfill worker is
        almost always mid-call in live mode (see _apply_night_skip). ``e._tasks`` holds
        only dialogue/reflection background tasks (``_spawn``); the translate worker
        runs on its own task, so it isn't in there."""
        e = self.engine
        return bool(e._tasks or e._in_dialogue or e._reflecting)

    def _night_meetup_pending(self) -> bool:
        """True while an arranged meetup (see decision.maybe_arrange_meetup) is still
        unkept and falls before the next scheduled wake -- so night skip won't fast-forward
        past an appointment. Meetups are arranged into daytime windows, so this is normally
        clear at night; it just guarantees the town never cruises over one."""
        now = self.engine.now
        next_wake = self.engine.scheduler.peek_minute()
        horizon = next_wake if next_wake is not None else now + NIGHT_WAKE_LEAD_MIN
        for a in self.world.agents.values():
            m = a.state.pending_meetup
            if m and now <= m.get("minute", 0) < horizon:
                return True
        return False

    def _apply_night_skip(self) -> None:
        """Fast-forward the sleeping hours. Engages when the whole town is asleep, no
        dialogue/reflection is in flight, and no night appointment is pending -- then
        cruises at ``night_speed`` (display translation does NOT hold it off; see
        _dialogue_or_reflection_inflight). Restores the pre-skip gear the instant someone stirs, or
        NIGHT_WAKE_LEAD_MIN sim-min before the earliest scheduled wake so morning opens
        at the viewer's own speed. Coexists with the unattended cruise (takes the
        higher) and is exempt from the free-mode 5x clamp: an all-asleep town makes zero
        LLM calls, so 20x burns no quota -- which is exactly why it's safe to run fast."""
        next_wake = self.engine.scheduler.peek_minute()
        near_wake = next_wake is not None and (next_wake - self.engine.now) <= NIGHT_WAKE_LEAD_MIN
        want = (
            self._town_all_asleep()
            and not self._dialogue_or_reflection_inflight()
            and not self._night_meetup_pending()
            and not near_wake
        )
        if want:
            # Baseline = the speed that would be active without night skip (the
            # unattended cruise while unwatched, else the daytime speed). Take the
            # higher so unattended-2x and night-20x coexist as 20x, not 2x.
            baseline = self.unattended_speed if self._cruising else self._user_speed
            target = max(self.night_speed, baseline)
            if not self._night_cruising:
                self._night_cruising = True
                print(f"[night] town asleep, cruising through the night at {target:g}x", flush=True)
            self.speed = target
        elif self._night_cruising:
            self._night_cruising = False
            self.speed = self.unattended_speed if self._cruising else self._user_speed
            print(f"[night] morning -- restored {self.speed:g}x", flush=True)

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
            self._pace_phase = "sleep"
            await asyncio.sleep(TICK_REAL_SECONDS)
            self._pace_beat = time.monotonic()   # iteration start: the stall watchdog measures from here
            self._pace_warned = False
            self._pace_heartbeat()
            if self.paused:
                continue
            if self._is_idle():
                continue  # no clients: don't advance the clock or accumulate _frac
            self._apply_unattended_speed()  # unattended: cruise slower while unwatched
            self._apply_night_skip()        # night: fast-forward the sleeping hours (overrides cruise upward)
            self._frac += self.speed * TICK_REAL_SECONDS
            step = int(self._frac)
            if step <= 0:
                continue
            self._frac -= step
            target = self.engine.now + step
            self._pace_phase = "run_until"
            await self.engine.run_until(target)
            self.engine.now = max(self.engine.now, target)  # clock advances even in quiet periods
            self.engine.expire_world_effects()  # end rain/festival on time even with no pending decisions
            # Each sim-day, proactively queue the day's new English knowledge for zh
            # translation so a panel-open never shows English first (cheap: deduped).
            # Skipped while night-cruising: translation is daytime work, and letting the
            # backfill fire mid-cruise would spend LLM calls through the sleeping hours
            # (and _tr_backfill_day is left unchanged so it runs once morning restores).
            day = self.engine.now // DAY_MIN
            if day != self._tr_backfill_day and not self._night_cruising:
                self._tr_backfill_day = day
                self._pace_phase = "backfill"
                self.backfill_translations()
            self._pace_phase = "broadcast"
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
            "night": self._night_cruising,
            "user_speed": self._user_speed,
            "live": self.live, "paid": self.paid,   # frontend gates the 20x button on these
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
            "night": self._night_cruising,
            "user_speed": self._user_speed,
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
    # Warm the display-layer translation cache at startup: a resumed run carries a
    # full English chronicle/secrets/beliefs backlog the user shouldn't see in English.
    queued = sim.backfill_translations()
    if queued:
        print(f"[translate] startup backfill queued {queued} English texts for zh", flush=True)
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


app = FastAPI(title="Gaobo Town (高柏小鎮)", lifespan=lifespan)


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
        # No manual speed tiers in the UI anymore; this endpoint stays for unattended
        # and internal callers. It sets the daytime speed without disturbing an active
        # cruise (unattended or night), which the pacing loop recomputes each tick.
        want = float(body.get("speed", DAY_SPEED))
        want = min(want, MAX_LIVE_SPEED) if (sim.live and not sim.paid) else want   # free live: don't burst rate limits
        sim._user_speed = want                                   # remember it across unattended cruise
        if not sim._cruising and not sim._night_cruising:
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
            per_call_timeout=SYNC_CALL_TIMEOUT_S,   # bound this request-path call too
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


@app.post("/god/close_chapter")
async def god_close_chapter(body: dict = Body(default={})) -> JSONResponse:
    """God Mode: close an agent's current *pursuit* chapter now (testing, and the
    retroactive fix for a matter that ended before the pipeline existed).
      {"agent_id": "aisi", "outcome": "completed"|"failed"|"abandoned", "reason": "..."}
    Runs the whole pipeline synchronously -- one smart-tier closure reflection (or the
    template line if it fails), the atomic state change, the chapter_closed beat -- and
    snapshots. 409 when the agent has no pursuit to close or one is already closing."""
    assert sim is not None
    b = body if isinstance(body, dict) else {}
    agent = sim.world.agents.get(str(b.get("agent_id", "")))
    if agent is None:
        return JSONResponse({"error": "agent_id must be a known resident"}, status_code=400)
    outcome = str(b.get("outcome", "completed")).strip().lower()
    if outcome not in chapters_mod.OUTCOMES:
        return JSONResponse({"error": f"outcome must be one of {list(chapters_mod.OUTCOMES)}"}, status_code=400)
    if agent.chapter is None or agent.chapter.chapter_type != "pursuit":
        return JSONResponse({"error": "agent has no pursuit chapter to close",
                             "chapter": agent.chapter.to_dict() if agent.chapter else None}, status_code=409)
    record = await sim.engine.close_chapter(agent, outcome, trigger="manual",
                                            reason=str(b.get("reason", "")).strip())
    if record is None:
        return JSONResponse({"error": "a closure is already in flight for this agent"}, status_code=409)
    if sim.persistence is not None:
        sim._take_snapshot()
    return JSONResponse({"ok": True, "agent_id": agent.id, "closed": record.to_dict(),
                         "now": agent.chapter.to_dict() if agent.chapter else None})


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

    ch = a.chapter
    return JSONResponse(
        {
            "id": a.id,
            "name": a.name,
            "age": a.profile.age,
            "occupation": a.profile.occupation,
            "traits": a.profile.traits,
            "goals": a.profile.goals,
            "wishes": [w.to_dict() for w in a.wishes],
            # Life chapter (see agents/chapters.py); None = uninitialized (reads as ordinary).
            "chapter": ({
                "id": ch.id, "type": ch.chapter_type, "title": ch.title, "narrative": ch.narrative,
                "started_on": ch.started_on, "goal": ch.goal, "until_day": ch.until_day,
                "emotional_residue": ch.emotional_residue,
            } if ch is not None else None),
            "chapter_history": [
                {"title": r.chapter.get("title", ""), "type": r.chapter.get("chapter_type", ""),
                 "started_on": r.chapter.get("started_on", 0), "ended_on": r.ended_on,
                 "outcome": r.outcome, "biography_line": r.biography_line,
                 "emotional_residue": r.emotional_residue, "trigger": r.trigger}
                for r in a.chapter_history
            ][::-1],
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
                {"clock": fmt_time(m.minute), "text": m.text, "importance": m.importance, "kind": m.kind,
                 "weight": m.weight}
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


@app.get("/api/admin/translations")
async def list_translations(lang: str = "", contains: str = "", limit: int = 50) -> JSONResponse:
    """Inspect the persistent translation cache (newest first). Filter by ``lang`` or a
    ``contains`` substring of the source/translated text. Needs persistence."""
    assert sim is not None
    if sim.persistence is None:
        return JSONResponse({"error": "persistence not enabled"}, status_code=501)
    rows = await sim.persistence.list_translations(
        lang=lang or None, contains=contains or None, limit=max(1, min(limit, 500)))
    return JSONResponse({"count": len(rows), "translations": rows})


@app.post("/api/admin/translations/clear")
async def clear_translations(body: dict = Body(default={})) -> JSONResponse:
    """Remove cached translations -- the clean-up path for a future 'ok'-style
    contamination (clear the DB instead of waiting for a restart).

    SAFE BY DEFAULT: a dry run that only REPORTS how many rows match. Send
    {"dry_run": false} to actually delete. Filters: ``lang``, ``contains`` (substring
    of source/translated), ``gave_up_only`` (drop only the given-up markers so they
    retry fresh). Also evicts matching entries from the in-memory cache so the running
    server reflects the clear immediately."""
    assert sim is not None
    if sim.persistence is None:
        return JSONResponse({"error": "persistence not enabled"}, status_code=501)
    b = body if isinstance(body, dict) else {}
    dry_run = bool(b.get("dry_run", True))
    lang = (b.get("lang") or "") or None
    contains = (b.get("contains") or "") or None
    gave_up_only = bool(b.get("gave_up_only", False))
    n = await sim.persistence.count_translations(
        lang=lang, contains=contains, gave_up_only=gave_up_only)
    if dry_run:
        return JSONResponse({
            "dry_run": True, "would_remove": n,
            "filters": {"lang": lang, "contains": contains, "gave_up_only": gave_up_only},
            "hint": 'nothing changed -- re-send with {"dry_run": false} to apply',
        })
    removed = await sim.persistence.clear_translations(
        lang=lang, contains=contains, gave_up_only=gave_up_only)
    # Evict from the live in-memory cache too, so the running server stops serving them.
    def _match(src: str) -> bool:
        return contains is None or contains.lower() in src.lower()
    if gave_up_only:
        for t in [t for t in sim._tr_gaveup if _match(t)]:
            sim._tr_gaveup.discard(t)
    else:
        for t in [t for t in sim._translate_cache if _match(t)]:
            del sim._translate_cache[t]
        for t in [t for t in sim._tr_gaveup if _match(t)]:
            sim._tr_gaveup.discard(t)
    return JSONResponse({"dry_run": False, "removed": removed})


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


@app.post("/api/admin/rewrite-stale-goals")
async def rewrite_stale_goals(body: dict = Body(default={})) -> JSONResponse:
    """Move forward any goal that was about a now-resolved secret but never got
    rewritten (e.g. Xixi's 'work up the courage to ask Aisi...' after his worry was
    laid to rest). SAFE BY DEFAULT (dry run reports only). {"dry_run": false}
    archives first, then rewrites + leaves each owner a memory of the shift."""
    assert sim is not None
    dry_run = bool(body.get("dry_run", True)) if isinstance(body, dict) else True
    world = sim.world
    dec = sim.engine.decisions
    now = sim.engine.now

    candidates, seen = [], set()
    for s in dec.secrets.secrets.values():
        if not s.resolved:
            continue
        owner = world.agents.get(s.owner)
        if owner is None:
            continue
        for g in owner.profile.goals:
            old = str(g.get("goal", ""))
            key = (s.owner, old)
            if key in seen or not dec.goal_matches_secret(old, s):
                continue
            new = dec.forward_goal(old, s)
            if new and new != old:
                seen.add(key)
                candidates.append({"owner": s.owner, "owner_name": owner.name,
                                   "secret": s.text, "old_goal": old, "new_goal": new})

    if dry_run:
        return JSONResponse({
            "dry_run": True, "count": len(candidates), "would_rewrite": candidates,
            "hint": 'nothing changed -- re-send with {"dry_run": false} to apply',
        })

    archive_id = None
    if sim.persistence is not None:
        payload = snapshot_mod.capture(sim.engine, sim.world, sim.engine.decisions)
        archive_id = await sim.persistence.archive_snapshot(payload, reason="rewrite-stale-goals")
    applied = 0
    for c in candidates:
        owner = world.agents.get(c["owner"])
        if owner is None:
            continue
        for g in owner.profile.goals:
            if str(g.get("goal", "")) == c["old_goal"]:
                g["goal"] = c["new_goal"]
                owner.memory.add(MemoryItem(
                    minute=now, importance=4, kind="reflection",
                    text=f"That old worry is behind me now -- my focus is on the next chapter: {c['new_goal']}."))
                applied += 1
                break
    if sim.persistence is not None:
        sim._take_snapshot()
    return JSONResponse({"dry_run": False, "archive_id": archive_id, "rewritten": applied})


@app.post("/api/admin/resolve-completed-landmark-worries")
async def resolve_completed_landmark_worries(body: dict = Body(default={})) -> JSONResponse:
    """Lay to rest a creator's 'will I ever finish it?' worry once the landmark is
    actually done -- for pieces completed before the world-fact hook existed (e.g.
    Aisi's finished installation vs her still-open 'too ambitious to finish' secret).
    Dry-run by default; {"dry_run": false} archives then resolves."""
    assert sim is not None
    dry_run = bool(body.get("dry_run", True)) if isinstance(body, dict) else True
    world = sim.world
    dec = sim.engine.decisions
    now = sim.engine.now
    words = sim.engine._LANDMARK_WORDS

    candidates = []
    for loc in world.locations.values():
        for lm in loc.landmarks:
            if lm.get("state") != "completed":
                continue
            creator = world.agents.get(lm.get("created_by", ""))
            if creator is None:
                continue
            for s in dec.secrets.active_secrets_of(creator.id):
                sw = {w.strip(".,;:!?'\"").lower() for w in s.text.split()}
                if any(k in sw for k in words):
                    candidates.append({"owner": creator.id, "owner_name": creator.name,
                                       "landmark": lm.get("name"), "secret_id": s.id, "text": s.text})

    if dry_run:
        return JSONResponse({
            "dry_run": True, "count": len(candidates), "would_resolve": candidates,
            "hint": 'nothing changed -- re-send with {"dry_run": false} to apply',
        })

    archive_id = None
    if sim.persistence is not None:
        payload = snapshot_mod.capture(sim.engine, sim.world, sim.engine.decisions)
        archive_id = await sim.persistence.archive_snapshot(payload, reason="resolve-landmark-worries")
    resolved = 0
    for c in candidates:
        s = dec.secrets.secrets.get(c["secret_id"])
        owner = world.agents.get(c["owner"])
        if s is not None and owner is not None and dec._resolve_secret(
                owner, s, now, f"{c['owner'].capitalize()} finished it -- the worry never came true."):
            resolved += 1
    if sim.persistence is not None:
        sim._take_snapshot()
    return JSONResponse({"dry_run": False, "archive_id": archive_id, "resolved": resolved})


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
    # Dialogue mock-floor rate: how often a conversation fell all the way to the
    # canned mock floor (a live-model health signal). Cumulative this process, plus a
    # recent window (last sim-day) so a fresh outage shows up without the long tail
    # washing it out. Floors are now always recorded (the timeout path used to bypass
    # usage), so this is honest.
    dlg = [c for c in u.calls if c.task_type == "dialogue" and not c.cache_hit]
    dlg_floor = sum(1 for c in dlg if c.provider == "mock")
    recent_cut = (max((c.sim_minute for c in dlg), default=0)) - 1440
    dlg_recent = [c for c in dlg if c.sim_minute >= recent_cut]
    dlg_recent_floor = sum(1 for c in dlg_recent if c.provider == "mock")
    # Patient-retry stats (task 4): proof the task-1 retry is doing its job. Of the
    # conversations that failed the first whole-chain pass and had to brew+retry,
    # ``recovered`` is how many a retry then rescued to a real model (vs ``exhausted``,
    # which spent every round and still fell to mock). retry_success_rate is the
    # fraction rescued -- a high value means the retry is what's keeping floor low.
    rs = sim.engine.dialogue_retry_stats
    dialogue_floor = {
        "total": len(dlg), "floor": dlg_floor,
        "rate": round(dlg_floor / len(dlg), 3) if dlg else 0.0,
        "recent_total": len(dlg_recent), "recent_floor": dlg_recent_floor,
        "recent_rate": round(dlg_recent_floor / len(dlg_recent), 3) if dlg_recent else 0.0,
        "retried": rs["retried"], "recovered": rs["recovered"], "exhausted": rs["exhausted"],
        "retry_rounds": rs["rounds_extra"],
        "retry_success_rate": round(rs["recovered"] / rs["retried"], 3) if rs["retried"] else 0.0,
    }
    return JSONResponse(
        {
            "calls": u.total_calls,
            "cache_hits": u.cache_hits,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "estimated_cost": round(cost, 6),
            "budget_usd": sim.router.budget_usd,
            "dialogue_floor": dialogue_floor,
            "chapters": dict(sim.engine.chapter_stats),   # closed / llm-written / template-written
            "by_task": [{"task": k, **{**v, "cost": round(v["cost"], 6)}} for k, v in
                        sorted(by_task.items(), key=lambda kv: -kv[1]["calls"])],
            "by_model": [{"model": k, **{**v, "cost": round(v["cost"], 6)}} for k, v in
                         sorted(by_model.items(), key=lambda kv: -kv[1]["calls"])],
            "decisions": total_decisions,
            "rules_only": rules_only,
        }
    )
