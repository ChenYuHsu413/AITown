"""Database models (SQLAlchemy 2.0, async).

Eight tables:

    simulation_runs   one row per server boot / script run
    events            structured events (Event Contract v1, verbatim)
    memories          episodic memories + pgvector embedding (+ source_chapter_id, v11)
    llm_calls         the cost ledger (was in-memory UsageTracker only)
    world_snapshots   latest serialized world state per run (resume foundation)
    snapshot_archive  pre-operation backups saved before destructive admin ops
    translation_cache display-layer English->zh translations (translate once, keep)
    chapters          life-chapter ledger: one row per chapter per agent (started/closed)

The events table mirrors the Event Contract exactly -- that was the point
of the structured-events refactor.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBED_DIM = 64  # mock embedding dim; bump when switching to a real model


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return uuid.uuid4().hex


class SimulationRun(Base):
    __tablename__ = "simulation_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    note: Mapped[str] = mapped_column(String(200), default="")


class EventRow(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("simulation_runs.id"), index=True)
    minute: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    verb: Mapped[str] = mapped_column(String(24))
    actor: Mapped[str] = mapped_column(String(32), default="")
    target: Mapped[str] = mapped_column(String(32), default="")
    location: Mapped[str] = mapped_column(String(32), default="")
    speech: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (Index("ix_events_run_minute", "run_id", "minute"),)


class MemoryRow(Base):
    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("simulation_runs.id"), index=True)
    agent_id: Mapped[str] = mapped_column(String(32), index=True)
    minute: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    importance: Mapped[int] = mapped_column(Integer, default=1)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBED_DIM))
    # v11: a ``biography`` memory traces back to the chapter it closed ("" otherwise).
    # Added to an existing DB by the ALTER in Persistence.start (create_all won't).
    source_chapter_id: Mapped[str] = mapped_column(String(32), default="")


class LLMCallRow(Base):
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("simulation_runs.id"), index=True)
    sim_minute: Mapped[int] = mapped_column(Integer)
    agent_id: Mapped[str] = mapped_column(String(32))
    task_type: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(24))
    model: Mapped[str] = mapped_column(String(48))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)


class WorldSnapshot(Base):
    """One row per run holding the whole serialized world (clock, agents,
    rumors, economy, dialogue budget) as a single JSON payload. Upserted on
    ``run_id`` so only the latest snapshot survives -- the resume foundation.
    Memories still live in ``memories`` for retrieval/history; the snapshot
    carries its own full copy so rehydration is a single self-contained read."""

    __tablename__ = "world_snapshots"

    run_id: Mapped[str] = mapped_column(ForeignKey("simulation_runs.id"), primary_key=True)
    minute: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TranslationCacheRow(Base):
    """Display-layer translation cache, persisted so a restart doesn't re-translate the
    whole standing corpus (the boot-time queue storm + repeat spend). Keyed by a hash
    of (lang, source_text). A ``gave_up`` row records a text that exhausted its retries
    so the startup backfill won't re-queue it (a user opening the panel still retries it
    on demand). No FK -- the cache is content-addressed and outlives any single run."""

    __tablename__ = "translation_cache"

    text_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_text: Mapped[str] = mapped_column(Text)
    translated_text: Mapped[str] = mapped_column(Text, default="")
    lang: Mapped[str] = mapped_column(String(8), default="", index=True)  # "zh-tw"/"en"/"zh-hant" all fit
    model: Mapped[str] = mapped_column(String(64), default="")
    gave_up: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ChapterRow(Base):
    """Life-chapter ledger (see agents/chapters.py): one row per chapter, upserted
    on ``chapter_id`` -- written when a chapter starts and again when it closes
    (outcome + biography line filled in). The world snapshot stays the resume
    source of truth; this table is the queryable history (the future town paper)."""

    __tablename__ = "chapters"

    chapter_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    agent_id: Mapped[str] = mapped_column(String(32), index=True)
    chapter_type: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200), default="")
    narrative: Mapped[str] = mapped_column(Text, default="")
    goal: Mapped[str] = mapped_column(Text, default="")
    related_goal_id: Mapped[str] = mapped_column(String(16), default="")
    related_landmark_id: Mapped[str] = mapped_column(String(32), default="")
    started_on: Mapped[int] = mapped_column(Integer, default=0)      # sim day
    ended_on: Mapped[int] = mapped_column(Integer, default=0)        # sim day (0 = still open)
    outcome: Mapped[str] = mapped_column(String(16), default="")     # completed | failed | abandoned
    biography_line: Mapped[str] = mapped_column(Text, default="")
    emotional_residue: Mapped[str] = mapped_column(String(16), default="")
    trigger: Mapped[str] = mapped_column(String(24), default="")
    memory_refs: Mapped[list] = mapped_column(JSONB, default=list)   # [{"id","text"}]
    # A retracted closure (re-closed with a different outcome) is never deleted: the
    # row stays as the audit trail, flagged so readers skip it. Added to an existing
    # DB by the ALTER in Persistence.start.
    superseded: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SnapshotArchive(Base):
    """Append-only safety net: a full world payload saved *before* any destructive
    admin operation (unlike world_snapshots, which is upsert/single-row). Every row
    is the exact pre-operation state, so a mistaken prune can always be recovered
    (see docs/admin.md). No FK -- the archive outlives everything."""

    __tablename__ = "snapshot_archive"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    minute: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(200), default="")
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
