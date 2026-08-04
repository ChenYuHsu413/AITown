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
    daily_wage: float = 0.0                 # flat daily income paid at each day boundary

    @property
    def extraversion(self) -> float:
        return self.personality.get("extraversion", 0.5)


@dataclass
class AgentState:
    location: str
    energy: int = 80                        # 0..100
    money: float = 100.0                    # wallet; spent on meals, topped up by wages/revenue
    mood: str = "neutral"
    current_action: str = "idle"
    busy_until: int = 0                     # sim minute; can't be interrupted before
    last_talk_minute: dict[str, int] = field(default_factory=dict)  # per-partner cooldown
    pending_concern: dict | None = None     # {"rumor_id", "told_by"} when a rumor about self must be reacted to
    seek_target: str = ""                   # agent_id being chased down to confront over a rumor
    seek_text: str = ""                     # the confrontation opener to use on arrival
    seek_rumor_id: str = ""                 # the rumor being confronted (moves with seek_target/seek_text)
    seek_tries: int = 0                     # chase attempts so far (give up after 2)
    avoid_location: str = ""                # a shop to shun after hearing a bad rumor about its owner
    meals_bought: int = 0                   # lifetime paid meals (throttles the "had a meal" memory)
    last_meal_slot: int = -1                # (day, eat-slot) already paid for -- stops double-charging
    seen_landmark_progress: dict[str, float] = field(default_factory=dict)  # landmark_id -> progress last noticed


@dataclass
class MemoryItem:
    minute: int
    text: str
    importance: int = 1                     # 1..10
    kind: str = "observation"               # observation | conversation | reflection | rumor | secret
    rumor_id: str = ""                      # set when this memory records a rumor
    secret_id: str = ""                     # set when this memory records a confided secret


@dataclass
class Belief:
    """A long-term impression distilled from repeated experience -- the semantic
    layer above episodic memory. One belief per subject (an agent_id, "self", or
    a location id). ``sentiment`` (-1..1) drives trust inertia; ``confidence``
    grows with reinforcement and decays when left unreinforced."""

    subject: str                            # agent_id | "self" | location_id
    text: str                               # one-line long-term impression (English)
    confidence: float = 0.3                 # 0..1
    sentiment: float = 0.0                  # -1 negative .. +1 positive
    formed_minute: int = 0
    last_reinforced_minute: int = 0
    source_count: int = 1                   # experiences backing this belief


class SemanticMemory:
    """Every agent's small set of lasting impressions (max 8). Formation and
    reinforcement happen in the reflection flow; decay runs at each day boundary."""

    def __init__(self) -> None:
        self.beliefs: list[Belief] = []

    def about(self, subject: str) -> "Belief | None":
        for b in self.beliefs:
            if b.subject == subject:
                return b
        return None

    def decay(self, amount: float = 0.02, floor: float = 0.15) -> None:
        """Erode every belief a little (call once per sim-day); drop the faded."""
        for b in self.beliefs:
            b.confidence -= amount
        self.beliefs = [b for b in self.beliefs if b.confidence >= floor]

    def _prune(self, cap: int = 8) -> None:
        """Keep at most ``cap`` beliefs -- evict the least-confident."""
        if len(self.beliefs) > cap:
            self.beliefs.sort(key=lambda b: b.confidence, reverse=True)
            del self.beliefs[cap:]


class EpisodicMemory:
    """Append-only list + naive keyword top-k retrieval.

    Two optional hooks wired by the persistence layer:
      - on_add: called for every new memory (mirror to DB)
      - vector_search: async (query, k) -> list[str]; when set,
        retrieve_async delegates to pgvector instead of keywords.
    """

    def __init__(self) -> None:
        self.items: list[MemoryItem] = []
        self.importance_since_reflection: int = 0
        self.on_add = None            # Callable[[MemoryItem], None] | None
        self.vector_search = None     # async (query, k) -> list[str] | None

    def add(self, item: MemoryItem) -> None:
        self.items.append(item)
        self.importance_since_reflection += item.importance
        if self.on_add is not None:
            self.on_add(item)

    async def retrieve_async(self, query: str, k: int = 5) -> list[str]:
        if self.vector_search is not None:
            try:
                return await self.vector_search(query, k)
            except Exception:
                pass  # DB hiccup -> keyword fallback below
        return self.retrieve(query, k)

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
