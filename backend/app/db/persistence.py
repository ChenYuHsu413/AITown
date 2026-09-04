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
    Base, ChapterRow, EventRow, LLMCallRow, MemoryRow, SimulationRun, SnapshotArchive,
    TranslationCacheRow, WishRow, WorldSnapshot,
)


@dataclass
class _MemWrite:
    agent_id: str
    item: MemoryItem


@dataclass
class _SnapWrite:
    payload: dict


@dataclass
class _TransWrite:
    text_hash: str
    source_text: str
    translated_text: str
    lang: str
    model: str
    gave_up: bool


@dataclass
class _ChapterWrite:
    row: dict          # ChapterRow columns (chapter_id, agent_id, ... ) -- upserted


@dataclass
class _WishWrite:
    row: dict          # WishRow columns (wish_id, owner, ...) -- upserted


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
            # v11 additive migration: create_all never alters an existing table, so
            # the memories column a biography memory writes must be added by hand
            # (idempotent; a no-op on a fresh DB where create_all already made it).
            await conn.execute(sql_text(
                "ALTER TABLE memories ADD COLUMN IF NOT EXISTS source_chapter_id VARCHAR(32) NOT NULL DEFAULT ''"))
            await conn.execute(sql_text(
                "ALTER TABLE chapters ADD COLUMN IF NOT EXISTS superseded BOOLEAN NOT NULL DEFAULT FALSE"))
            await self._migrate_wishes(conn)

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

    # Columns an earlier, since-reverted wish implementation left behind on a
    # deployed `wishes` table. create_all never alters an existing table, so on such
    # a database the current columns must be added by hand and the stale NOT NULLs
    # relaxed -- otherwise the first ledger write fails on a column nobody writes.
    # Idempotent, and a no-op on a database that never saw that version.
    _WISH_LEGACY_NOT_NULL = (
        "owner_id", "created_day", "ended_day", "source_memory_refs", "failure_conditions",
        "progress", "last_progress_day", "secret_id", "related_chapter_id",
    )
    _WISH_ADD_COLUMNS = (
        ("owner", "VARCHAR(32) NOT NULL DEFAULT ''"),
        ("created_on", "INTEGER NOT NULL DEFAULT 0"),
        ("ended_on", "INTEGER NOT NULL DEFAULT 0"),
        ("expires_on", "INTEGER NOT NULL DEFAULT 0"),
        ("chapter_id", "VARCHAR(32) NOT NULL DEFAULT ''"),
        ("frustration_count", "INTEGER NOT NULL DEFAULT 0"),
        ("provenance", "JSONB"),
    )

    async def _migrate_wishes(self, conn) -> None:
        for name, ddl in self._WISH_ADD_COLUMNS:
            await conn.execute(sql_text(f"ALTER TABLE wishes ADD COLUMN IF NOT EXISTS {name} {ddl}"))
        legacy = ", ".join(f"'{c}'" for c in self._WISH_LEGACY_NOT_NULL)
        await conn.execute(sql_text(f"""
            DO $$
            DECLARE col text;
            BEGIN
              FOR col IN
                SELECT column_name FROM information_schema.columns
                 WHERE table_name = 'wishes' AND is_nullable = 'NO'
                   AND column_name IN ({legacy})
              LOOP
                EXECUTE format('ALTER TABLE wishes ALTER COLUMN %I DROP NOT NULL', col);
              END LOOP;
            END $$;"""))

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

    def on_translation(self, *, text_hash: str, source_text: str, translated_text: str,
                       lang: str, model: str, gave_up: bool) -> None:
        """Queue a translation-cache upsert (a real zh translation, or a gave-up
        marker). Non-blocking; a DB failure never affects what's shown on screen."""
        self._queue.put_nowait(_TransWrite(
            text_hash=text_hash, source_text=source_text, translated_text=translated_text,
            lang=lang, model=model, gave_up=gave_up))

    def on_chapter(self, row: dict) -> None:
        """Queue a chapter-ledger upsert (chapter started / closed). Non-blocking."""
        self._queue.put_nowait(_ChapterWrite(dict(row)))

    def on_wish(self, row: dict) -> None:
        """Queue a wish-ledger upsert (seeded / ended). Non-blocking."""
        self._queue.put_nowait(_WishWrite(dict(row)))

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
                        embedding=emb, source_chapter_id=item.item.source_chapter_id,
                    ))
                elif isinstance(item, _ChapterWrite):
                    row = {k: v for k, v in item.row.items() if k in ChapterRow.__table__.columns}
                    row.setdefault("run_id", self.run_id)
                    row["updated_at"] = datetime.utcnow()
                    stmt = pg_insert(ChapterRow).values(**row)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[ChapterRow.chapter_id],
                        set_={k: v for k, v in row.items() if k != "chapter_id"},
                    )
                    await s.execute(stmt)
                elif isinstance(item, _WishWrite):
                    row = {k: v for k, v in item.row.items() if k in WishRow.__table__.columns}
                    row.setdefault("run_id", self.run_id)
                    row["updated_at"] = datetime.utcnow()
                    stmt = pg_insert(WishRow).values(**row)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[WishRow.wish_id],
                        set_={k: v for k, v in row.items() if k != "wish_id"},
                    )
                    await s.execute(stmt)
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
                elif isinstance(item, _TransWrite):
                    # Content-addressed upsert: the newest result (a real translation,
                    # or a gave_up marker) wins for a given (lang, source_text) hash.
                    stmt = pg_insert(TranslationCacheRow).values(
                        text_hash=item.text_hash, source_text=item.source_text,
                        translated_text=item.translated_text, lang=item.lang,
                        model=item.model, gave_up=item.gave_up,
                    ).on_conflict_do_update(
                        index_elements=[TranslationCacheRow.text_hash],
                        set_={"translated_text": item.translated_text, "model": item.model,
                              "gave_up": item.gave_up, "created_at": datetime.utcnow()},
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

    # ---- translation cache ----------------------------------------

    async def load_translation_cache(self, lang: str) -> list[dict]:
        """Whole translation cache for a language, loaded into memory at startup (the
        corpus is small). Each row: source_text, translated_text, gave_up."""
        async with self.session() as s:
            rows = (await s.execute(
                select(TranslationCacheRow.source_text, TranslationCacheRow.translated_text,
                       TranslationCacheRow.gave_up)
                .where(TranslationCacheRow.lang == lang)
            )).all()
            return [{"source": r.source_text, "translated": r.translated_text,
                     "gave_up": r.gave_up} for r in rows]

    async def list_translations(self, *, lang: str | None = None, contains: str | None = None,
                                limit: int = 50) -> list[dict]:
        """Admin discoverability: recent cache entries, optionally filtered by language
        or a substring of the source/translated text (newest first)."""
        async with self.session() as s:
            q = select(TranslationCacheRow).order_by(TranslationCacheRow.created_at.desc())
            if lang:
                q = q.where(TranslationCacheRow.lang == lang)
            if contains:
                like = f"%{contains}%"
                q = q.where(TranslationCacheRow.source_text.ilike(like)
                            | TranslationCacheRow.translated_text.ilike(like))
            rows = (await s.execute(q.limit(limit))).scalars().all()
            return [{"text_hash": r.text_hash, "source": r.source_text,
                     "translated": r.translated_text, "lang": r.lang, "model": r.model,
                     "gave_up": r.gave_up, "created_at": r.created_at.isoformat()} for r in rows]

    async def count_translations(self, *, lang: str | None = None,
                                 contains: str | None = None, gave_up_only: bool = False) -> int:
        async with self.session() as s:
            q = select(func.count()).select_from(TranslationCacheRow)
            if lang:
                q = q.where(TranslationCacheRow.lang == lang)
            if contains:
                like = f"%{contains}%"
                q = q.where(TranslationCacheRow.source_text.ilike(like)
                            | TranslationCacheRow.translated_text.ilike(like))
            if gave_up_only:
                q = q.where(TranslationCacheRow.gave_up.is_(True))
            return int((await s.execute(q)).scalar_one())

    async def clear_translations(self, *, lang: str | None = None, contains: str | None = None,
                                 gave_up_only: bool = False) -> int:
        """Delete matching cache rows; returns how many. The admin endpoint gates this
        behind a dry-run by default (it counts first)."""
        from sqlalchemy import delete
        async with self.session() as s:
            q = delete(TranslationCacheRow)
            if lang:
                q = q.where(TranslationCacheRow.lang == lang)
            if contains:
                like = f"%{contains}%"
                q = q.where(TranslationCacheRow.source_text.ilike(like)
                            | TranslationCacheRow.translated_text.ilike(like))
            if gave_up_only:
                q = q.where(TranslationCacheRow.gave_up.is_(True))
            res = await s.execute(q)
            await s.commit()
            return int(res.rowcount or 0)

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

    async def load_archive(self, archive_id: int) -> dict | None:
        """One archived pre-operation payload by id (the selective-restore source)."""
        async with self.session() as s:
            row = (await s.execute(
                select(SnapshotArchive).where(SnapshotArchive.id == archive_id))).scalar_one_or_none()
            return None if row is None else {"id": row.id, "run_id": row.run_id, "minute": row.minute,
                                             "reason": row.reason, "payload": row.payload}

    async def supersede_chapters(self, chapter_ids: list[str]) -> int:
        """Flag retracted chapter-ledger rows (kept as the audit trail). Synchronous."""
        from sqlalchemy import update
        if not chapter_ids:
            return 0
        async with self.session() as s:
            res = await s.execute(update(ChapterRow).where(ChapterRow.chapter_id.in_(chapter_ids))
                                  .values(superseded=True, updated_at=datetime.utcnow()))
            await s.commit()
            return int(res.rowcount or 0)

    async def delete_biography_rows(self, agent_id: str, source_chapter_id: str) -> int:
        """Remove a retracted biography from the memories table (a wrong near-permanent
        fact must not stay retrievable). The archive keeps the pre-state. Synchronous."""
        from sqlalchemy import delete
        async with self.session() as s:
            res = await s.execute(delete(MemoryRow).where(
                MemoryRow.run_id == self.run_id, MemoryRow.agent_id == agent_id,
                MemoryRow.kind == "biography", MemoryRow.source_chapter_id == source_chapter_id))
            await s.commit()
            return int(res.rowcount or 0)

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

    def vector_retriever(self, agent_id: str, memory=None):
        """Returns an async (query, k) -> list[str] bound to one agent, matching
        EpisodicMemory's retrieval contract. When the agent has suppressed themes
        (resolved worries) or chapter down-weights, it over-fetches and re-ranks so a
        pre-resolution reflection/rumor of that theme sinks (its effective similarity
        is halved) and a closed chapter's memory sinks by its weight. ``biography``
        rows are never returned here -- EpisodicMemory surfaces those itself, only on
        a topic/place match (see EpisodicMemory.biography_hits)."""

        async def retrieve(query: str, k: int = 5) -> list[str]:
            q_emb = await self.embedder.embed(query)
            rerank = bool(memory is not None and (memory.suppressed or memory.has_downweights))
            base = (MemoryRow.run_id == self.run_id, MemoryRow.agent_id == agent_id,
                    MemoryRow.kind != "biography")
            async with self.session() as s:
                if not rerank:
                    rows = await s.execute(
                        select(MemoryRow.text)
                        .where(*base)
                        .order_by(MemoryRow.embedding.cosine_distance(q_emb))
                        .limit(k)
                    )
                    return [r[0] for r in rows]
                dist = MemoryRow.embedding.cosine_distance(q_emb)
                rows = (await s.execute(
                    select(MemoryRow.text, MemoryRow.kind, MemoryRow.minute, dist.label("d"))
                    .where(*base)
                    .order_by(dist).limit(k * 4)
                )).all()

            # halved weight == doubled distance for a penalized memory; a chapter
            # weight w scales the distance by 1/w (w=0.3 -> ~3.3x farther).
            def eff(r):
                d = r[3] * (2.0 if memory.penalty(r[0], r[1], r[2]) < 1.0 else 1.0)
                w = memory.weight_of(r[0])
                return d / w if w > 0 else float("inf")
            ranked = sorted(rows, key=eff)
            return [r[0] for r in ranked[:k]]

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
