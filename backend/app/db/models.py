"""Database models (SQLAlchemy 2.0, async).

Four tables for phase 2:

    simulation_runs   one row per server boot / script run
    events            structured events (Event Contract v1, verbatim)
    memories          episodic memories + pgvector embedding
    llm_calls         the cost ledger (was in-memory UsageTracker only)

The events table mirrors the Event Contract exactly -- that was the point
of the structured-events refactor.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
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
