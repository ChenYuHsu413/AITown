"""Persistence layer -- OPTIONAL.

Activated only when AI_TOWN_DB_URL is set (e.g.
postgresql+asyncpg://aitown:aitown@localhost/aitown). Without it the
simulation runs fully in-memory exactly as before.

Design:
  - Writes are queued and flushed by a background task, so the simulation
    loop never blocks on the database.
  - Memory retrieval switches from keyword matching to pgvector cosine
    search when persistence is on. The (query, k) -> list[str] contract is
    unchanged; decision.py doesn't know which backend answered.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from sqlalchemy import func, select, text as sql_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from ..agents.core import MemoryItem
from ..llm.embeddings import EmbeddingProvider, MockEmbedding
from ..llm.usage import LLMCall
from ..simulation.engine import Event
from .models import (
    Base, EventRow, LLMCallRow, MemoryRow, SimulationRun, SnapshotArchive, WorldSnapshot,
)


@dataclass
class _MemWrite:
    agent_id: str
    item: MemoryItem


@dataclass
class _SnapWrite:
    payload: dict


class Persistence:
    def __init__(self, db_url: str, embedder: EmbeddingProvider | None = None):
        # Supabase's Session pooler (pgbouncer-family) can choke on asyncpg's
        # prepared-statement cache; disabling it is harmless on a direct
        # Postgres, so we only pay the tiny cost when actually talking to a pooler.
        connect_args = {"statement_cache_size": 0} if "pooler.supabase.com" in db_url else {}
        self.engine: AsyncEngine = create_async_engine(
            db_url, pool_size=5, connect_args=connect_args)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.embedder = embedder or MockEmbedding()
        self.run_id: str = ""
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None

    # ---- lifecycle -------------------------------------------------

    async def start(
        self,
        note: str = "",
        resume: bool = False,
        restore_cb: Callable[[int, dict], None] | None = None,
    ) -> bool:
        """Create the schema, then either resume the latest run or open a new one.

        When ``resume`` and a snapshot exists, ``restore_cb(minute, payload)`` is
        invoked to rehydrate state; on success this run continues under the SAME
        ``run_id`` (events/memories keep appending to it) and True is returned.
        Any failure -- no snapshot, or a callback that raises on a bad payload --
        falls through to a brand-new run, and False is returned. A corrupt
        snapshot must never block startup."""
        async with self.engine.begin() as conn:
            await conn.execute(sql_text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)

        resumed = False
        if resume and restore_cb is not None:
            snap = await self.latest_snapshot()
            if snap is not None:
                try:
                    restore_cb(snap["minute"], snap["payload"])
                    self.run_id = snap["run_id"]   # continue the same run
                    resumed = True
                except Exception as err:
                    print(f"[resume] snapshot unreadable, starting fresh: {err}")

        if not resumed:
            async with self.session() as s:
                run = SimulationRun(note=note)
                s.add(run)
                await s.commit()
                self.run_id = run.id

        self._task = asyncio.create_task(self._flush_loop())
        return resumed

    async def stop(self) -> None:
        if self._task:
            await self._queue.join()
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.engine.dispose()

    # ---- write path (called synchronously from the sim) ------------

    def on_event(self, e: Event) -> None:
        self._queue.put_nowait(e)

    def on_llm_call(self, c: LLMCall) -> None:
        self._queue.put_nowait(c)

    def on_memory(self, agent_id: str, item: MemoryItem) -> None:
        self._queue.put_nowait(_MemWrite(agent_id, item))

    def on_snapshot(self, payload: dict) -> None:
        """Queue a full-world snapshot for an async upsert. Non-blocking, like
        every other write -- the sim never waits on the DB."""
        self._queue.put_nowait(_SnapWrite(payload))

    async def _flush_loop(self) -> None:
        while True:
            batch = [await self._queue.get()]
            while not self._queue.empty() and len(batch) < 200:
                batch.append(self._queue.get_nowait())
            try:
                await self._write_batch(batch)
            except Exception as err:  # DB down? Log, drop batch, keep simulating.
                print(f"[persistence] write failed, dropping {len(batch)} rows: {err}")
            finally:
                for _ in batch:
                    self._queue.task_done()

    async def _write_batch(self, batch: list) -> None:
        async with self.session() as s:
            for item in batch:
                if isinstance(item, Event):
                    s.add(EventRow(
                        run_id=self.run_id, minute=item.minute, kind=item.kind,
                        verb=item.verb, actor=item.actor, target=item.target,
                        location=item.location, speech=item.text,
                    ))
                elif isinstance(item, LLMCall):
                    s.add(LLMCallRow(
                        run_id=self.run_id, sim_minute=item.sim_minute,
                        agent_id=item.agent_id, task_type=item.task_type,
                        provider=item.provider, model=item.model,
                        input_tokens=item.input_tokens, output_tokens=item.output_tokens,
                        latency_ms=item.latency_ms, estimated_cost=item.estimated_cost,
                        cache_hit=item.cache_hit,
                    ))
                elif isinstance(item, _MemWrite):
                    emb = await self.embedder.embed(item.item.text)
                    s.add(MemoryRow(
                        run_id=self.run_id, agent_id=item.agent_id,
                        minute=item.item.minute, kind=item.item.kind,
                        importance=item.item.importance, text=item.item.text,
                        embedding=emb,
                    ))
                elif isinstance(item, _SnapWrite):
                    # One row per run: upsert so only the newest snapshot survives.
                    stmt = pg_insert(WorldSnapshot).values(
                        run_id=self.run_id,
                        minute=int(item.payload.get("minute", 0)),
                        payload=item.payload,
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[WorldSnapshot.run_id],
                        set_={
                            "minute": stmt.excluded.minute,
                            "payload": stmt.excluded.payload,
                            "created_at": datetime.utcnow(),
                        },
                    )
                    await s.execute(stmt)
            await s.commit()

    # ---- read path -------------------------------------------------

    async def latest_snapshot(self) -> dict | None:
        """The most recent snapshot across all runs (each run keeps only its
        latest), or None if none exist. Its ``run_id`` is the run to resume."""
        async with self.session() as s:
            row = (
                await s.execute(
                    select(WorldSnapshot)
                    .order_by(WorldSnapshot.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return {"run_id": row.run_id, "minute": row.minute, "payload": row.payload}

    # ---- archive (pre-destructive-op backups) ----------------------

    async def archive_snapshot(self, payload: dict, reason: str) -> int:
        """Durably store a full world payload BEFORE a destructive admin op, so the
        pre-operation state is always recoverable. Committed synchronously (not
        queued) -- the caller must be able to rely on it before deleting anything.
        Returns the new archive row id."""
        async with self.session() as s:
            row = SnapshotArchive(
                run_id=self.run_id, minute=int(payload.get("minute", 0)),
                reason=reason, payload=payload,
            )
            s.add(row)
            await s.commit()
            return row.id

    async def list_archives(self, limit: int = 20) -> list[dict]:
        """Recent pre-operation backups (newest first), without the heavy payloads
        -- for discoverability + the recovery path in docs/admin.md."""
        async with self.session() as s:
            rows = (await s.execute(
                select(SnapshotArchive.id, SnapshotArchive.run_id, SnapshotArchive.minute,
                       SnapshotArchive.reason, SnapshotArchive.created_at)
                .order_by(SnapshotArchive.created_at.desc()).limit(limit)
            )).all()
            return [{"id": r.id, "run_id": r.run_id, "minute": r.minute,
                     "reason": r.reason, "created_at": r.created_at.isoformat()} for r in rows]

    def vector_retriever(self, agent_id: str):
        """Returns an async (query, k) -> list[str] bound to one agent,
        matching EpisodicMemory's retrieval contract."""

        async def retrieve(query: str, k: int = 5) -> list[str]:
            q_emb = await self.embedder.embed(query)
            async with self.session() as s:
                rows = await s.execute(
                    select(MemoryRow.text)
                    .where(MemoryRow.run_id == self.run_id, MemoryRow.agent_id == agent_id)
                    .order_by(MemoryRow.embedding.cosine_distance(q_emb))
                    .limit(k)
                )
                return [r[0] for r in rows]

        return retrieve

    async def events_between(
        self, minute_from: int, minute_to: int, limit: int = 500, run_id: str | None = None
    ) -> list[dict]:
        """Structured events for one run in a minute window. ``run_id`` defaults
        to the live run; replay passes an older run's id. Ordered by (minute, id)
        so windowed paging over a long run stays contiguous."""
        rid = run_id or self.run_id
        async with self.session() as s:
            rows = await s.execute(
                select(EventRow)
                .where(
                    EventRow.run_id == rid,
                    EventRow.minute >= minute_from,
                    EventRow.minute <= minute_to,
                )
                .order_by(EventRow.minute, EventRow.id)
                .limit(limit)
            )
            return [
                {
                    "minute": r.minute, "kind": r.kind, "verb": r.verb,
                    "actor": r.actor, "target": r.target,
                    "location": r.location, "speech": r.speech,
                }
                for (r,) in rows
            ]

    async def list_runs(self) -> list[dict]:
        """Every run with its event count and minute span, newest first --
        the catalogue the replay picker reads. Runs with no events report
        count 0 and null bounds (an outer join keeps them in the list)."""
        async with self.session() as s:
            rows = await s.execute(
                select(
                    SimulationRun.id, SimulationRun.started_at, SimulationRun.note,
                    func.count(EventRow.id), func.min(EventRow.minute), func.max(EventRow.minute),
                )
                .outerjoin(EventRow, EventRow.run_id == SimulationRun.id)
                .group_by(SimulationRun.id, SimulationRun.started_at, SimulationRun.note)
                .order_by(SimulationRun.started_at.desc())
            )
            return [
                {
                    "id": rid,
                    "started_at": started.isoformat() if started else None,
                    "note": note or "",
                    "events": count or 0,
                    "minute_min": mn, "minute_max": mx,
                }
                for rid, started, note, count, mn, mx in rows
            ]
