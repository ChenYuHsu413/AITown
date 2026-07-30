"""Agent = Profile + State + Memory + Routine + Relationships.

The LLM is *not* the agent; it is one pluggable component inside the
decision system (see decision.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .core import AgentState, EpisodicMemory, Profile
from .routine import Routine


@dataclass
class Relationship:
    friendship: float = 30.0   # 0..100
    trust: float = 30.0
    conflict: float = 0.0

    def clamp(self) -> None:
        self.friendship = max(0.0, min(100.0, self.friendship))
        self.trust = max(0.0, min(100.0, self.trust))
        self.conflict = max(0.0, min(100.0, self.conflict))


@dataclass
class Agent:
    profile: Profile
    state: AgentState
    routine: Routine
    memory: EpisodicMemory = field(default_factory=EpisodicMemory)
    relationships: dict[str, Relationship] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.profile.id

    @property
    def name(self) -> str:
        return self.profile.name

    def rel(self, other_id: str) -> Relationship:
        return self.relationships.setdefault(other_id, Relationship())

    def apply_conversation_signals(
        self, other_id: str, sentiment: float, trust_signal: float, conflict_signal: float
    ) -> None:
        """Relationship math stays in Python -- the LLM only emits signals,
        it never sets 'friendship = 82' directly (it would drift)."""
        r = self.rel(other_id)
        r.friendship += (sentiment - 0.4) * 5
        r.trust += trust_signal * 4
        r.conflict += conflict_signal * 6 - 0.5
        r.clamp()
