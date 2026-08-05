"""Agent = Profile + State + Memory + Routine + Relationships.

The LLM is *not* the agent; it is one pluggable component inside the
decision system (see decision.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .core import AgentState, EpisodicMemory, Profile, SemanticMemory
from .routine import Routine


@dataclass
class Relationship:
    friendship: float = 30.0   # 0..100
    trust: float = 30.0
    conflict: float = 0.0
    # Romance track -- fully independent of friendship/trust. Directional (a one-sided
    # crush is real); the stage is pair-level (kept equal both ways). See romance.py.
    romance: float = 0.0       # 0..100
    romance_stage: str = "none"          # none | crushing | dating | partners
    romance_stage_minute: int = -1       # when the current stage began

    def clamp(self) -> None:
        self.friendship = max(0.0, min(100.0, self.friendship))
        self.trust = max(0.0, min(100.0, self.trust))
        self.conflict = max(0.0, min(100.0, self.conflict))
        self.romance = max(0.0, min(100.0, self.romance))


@dataclass
class Agent:
    profile: Profile
    state: AgentState
    routine: Routine
    memory: EpisodicMemory = field(default_factory=EpisodicMemory)
    semantic: SemanticMemory = field(default_factory=SemanticMemory)
    relationships: dict[str, Relationship] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.profile.id

    @property
    def name(self) -> str:
        return self.profile.name

    @property
    def home(self) -> str:
        """Where this agent lives -- the location of their 'sleep' routine entry."""
        for e in self.routine.entries:
            if e.action == "sleep":
                return e.location
        return self.state.location

    def rel(self, other_id: str) -> Relationship:
        return self.relationships.setdefault(other_id, Relationship())

    def apply_conversation_signals(
        self, other_id: str, sentiment: float, trust_signal: float, conflict_signal: float
    ) -> None:
        """Relationship math stays in Python -- the LLM only emits signals,
        it never sets 'friendship = 82' directly (it would drift).

        Semantic-memory inertia: a held negative impression of the other person
        (confidence >= 0.3, sentiment < 0) dampens relationship *gains* to 0.7x
        and amplifies relationship *hits* to 1.3x -- so a bad impression takes
        more good interactions to repair, and sours faster."""
        r = self.rel(other_id)
        df = (sentiment - 0.4) * 5
        dt = trust_signal * 4
        dc = conflict_signal * 6 - 0.5

        b = self.semantic.about(other_id)
        if b is not None and b.confidence >= 0.3 and b.sentiment < 0:
            df = df * 0.7 if df > 0 else df * 1.3
            dt = dt * 0.7 if dt > 0 else dt * 1.3
            if dc > 0:               # more conflict lands harder when the impression is already bad
                dc *= 1.3

        r.friendship += df
        r.trust += dt
        r.conflict += dc
        r.clamp()
