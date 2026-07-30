"""Agent core data: Profile (static), State (dynamic), Memory (episodic).

Phase 1 memory is a plain in-process list with keyword relevance --
deliberately no pgvector yet. The retrieval interface is already shaped
like the future vector version (query -> top-k), so swapping in
embeddings later won't touch callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Profile:
    id: str
    name: str
    age: int
    occupation: str
    personality: dict[str, float]           # big-five-ish, 0..1
    traits: list[str] = field(default_factory=list)
    goals: list[dict] = field(default_factory=list)  # {"goal": str, "priority": float}

    @property
    def extraversion(self) -> float:
        return self.personality.get("extraversion", 0.5)


@dataclass
class AgentState:
    location: str
    energy: int = 80                        # 0..100
    mood: str = "neutral"
    current_action: str = "idle"
    busy_until: int = 0                     # sim minute; can't be interrupted before
    last_talk_minute: dict[str, int] = field(default_factory=dict)  # per-partner cooldown


@dataclass
class MemoryItem:
    minute: int
    text: str
    importance: int = 1                     # 1..10
    kind: str = "observation"               # observation | conversation | reflection


class EpisodicMemory:
    """Append-only list + naive keyword top-k retrieval."""

    def __init__(self) -> None:
        self.items: list[MemoryItem] = []
        self.importance_since_reflection: int = 0

    def add(self, item: MemoryItem) -> None:
        self.items.append(item)
        self.importance_since_reflection += item.importance

    def retrieve(self, query: str, k: int = 5) -> list[str]:
        """Score = keyword overlap + importance + recency. Same contract as
        the future vector search: (query, k) -> list[str]."""
        q_words = {w for w in query.lower().split() if len(w) > 2}
        scored: list[tuple[float, MemoryItem]] = []
        latest = self.items[-1].minute if self.items else 0
        for m in self.items:
            overlap = len(q_words & set(m.text.lower().split()))
            recency = 1.0 - min((latest - m.minute) / (24 * 60), 1.0)
            score = overlap * 2.0 + m.importance * 0.3 + recency
            if score > 0:
                scored.append((score, m))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [m.text for _, m in scored[:k]]

    def today(self, day_start_minute: int) -> list[str]:
        return [m.text for m in self.items if m.minute >= day_start_minute]
