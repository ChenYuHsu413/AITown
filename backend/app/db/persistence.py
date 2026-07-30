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

from sqlalchemy import select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from ..agents.core import MemoryItem
from ..llm.embeddings import EmbeddingProvider, MockEmbedding
from ..llm.usage import LLMCall
from ..simulation.engine import Event
from .models import Base, EventRow, LLMCallRow, MemoryRow, SimulationRun


@dataclass
class _MemWrite:
    agent_id: str
    item: MemoryItem


class Persistence:
    def __init__(self, db_url: str, embedder: EmbeddingProvider | None = None):
        self.engine: AsyncEngine = create_async_engine(db_url, pool_size=5)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.embedder = embedder or MockEmbedding()
        self.run_id: str = ""
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None

    # ---- lifecycle -------------------------------------------------

    async def start(self, note: str = "") -> str:
        async with self.engine.begin() as conn:
            await conn.execute(sql_text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
        async with self.session() as s:
            run = SimulationRun(note=note)
            s.add(run)
            await s.commit()
            self.run_id = run.id
        self._task = asyncio.create_task(self._flush_loop())
        return self.run_id

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
            await s.commit()

    # ---- read path -------------------------------------------------

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

    async def events_between(self, minute_from: int, minute_to: int, limit: int = 500) -> list[dict]:
        async with self.session() as s:
            rows = await s.execute(
                select(EventRow)
                .where(
                    EventRow.run_id == self.run_id,
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
