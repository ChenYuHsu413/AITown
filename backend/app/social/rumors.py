"""Rumor propagation registry -- the telephone-game substrate.

A rumor is one piece of information that flows between agents through
conversation. Each retelling is recorded as a ``RumorVersion`` so the
whole chain (who heard what, from whom, when) stays fully traceable --
including how the wording drifts as it is passed along.

Phase-3 scope: in-memory only. Rumor *memories* still persist through the
existing ``EpisodicMemory.on_add`` hook; a dedicated rumors table is a
later migration.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass
class RumorVersion:
    agent_id: str        # who holds this version
    text: str            # the wording this person remembers
    heard_from: str      # who they heard it from ("" for the origin)
    minute: int


@dataclass
class Rumor:
    id: str              # short uuid
    origin: str          # originating agent_id
    created_minute: int
    subject: str = ""    # agent_id the rumor is about ("" if none)
    sentiment: float = 0.0  # -1 negative .. +1 positive; fixed at seed, distortion never changes it
    versions: list[RumorVersion] = field(default_factory=list)
    # End-of-life: once the subject confronts the source, the rumor is settled
    # and stops propagating (candidates skip resolved rumors -- see decision.py).
    resolved: bool = False
    resolved_minute: int = -1
    outcome: str = ""    # "admitted" | "denied" ("" while unresolved)


class RumorRegistry:
    """Owns every rumor and its version chain."""

    def __init__(self) -> None:
        self.rumors: dict[str, Rumor] = {}

    def seed(self, agent_id: str, text: str, minute: int,
             subject: str = "", sentiment: float = 0.0) -> Rumor:
        """Create a rumor and its first (origin) version."""
        rid = uuid.uuid4().hex[:8]
        rumor = Rumor(
            id=rid,
            origin=agent_id,
            created_minute=minute,
            subject=subject,
            sentiment=sentiment,
            versions=[RumorVersion(agent_id=agent_id, text=text, heard_from="", minute=minute)],
        )
        self.rumors[rid] = rumor
        return rumor

    def knows(self, rumor_id: str, agent_id: str) -> bool:
        rumor = self.rumors.get(rumor_id)
        return rumor is not None and any(v.agent_id == agent_id for v in rumor.versions)

    def resolve(self, rumor_id: str, minute: int, outcome: str) -> None:
        """Settle a rumor after a confrontation. From now on it is skipped when
        picking rumors to share -- this is the propagation terminus."""
        rumor = self.rumors.get(rumor_id)
        if rumor is None:
            return
        rumor.resolved = True
        rumor.resolved_minute = minute
        rumor.outcome = outcome

    def record_spread(self, rumor_id: str, from_agent: str, to_agent: str, text: str, minute: int) -> None:
        rumor = self.rumors.get(rumor_id)
        if rumor is None:
            return
        rumor.versions.append(
            RumorVersion(agent_id=to_agent, text=text, heard_from=from_agent, minute=minute)
        )

    def known_by(self, agent_id: str) -> list[tuple[Rumor, RumorVersion]]:
        """Every rumor this agent knows, paired with their latest version of it."""
        out: list[tuple[Rumor, RumorVersion]] = []
        for rumor in self.rumors.values():
            mine = [v for v in rumor.versions if v.agent_id == agent_id]
            if mine:
                out.append((rumor, mine[-1]))
        return out
