"""Secrets & confiding -- the trust substrate beneath the rumor mill.

A ``Secret`` is something private an agent (its ``owner``) holds. They confide it
only in people they trust enough (gated on the relationship's trust vs. the
secret's sensitivity, see decision.py). A confidant may keep it -- or leak it,
at which point it becomes a rumor and enters the ordinary rumor lifecycle
(spread, distortion, return-to-subject, confrontation). Because only a confidant
could have known, the owner confronts the *leaker*, not the rumor's chain origin.

In-memory only, like the rumor registry; the whole thing rides in the world
snapshot so a resumed town keeps its confidences.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass
class Secret:
    id: str
    owner: str                       # agent_id who holds the secret
    text: str                        # the private matter, in English (internal-English rule)
    sensitivity: float = 0.5         # 0..1; higher = more private, harder to confide, less likely to leak
    created_minute: int = 0
    confided_to: dict[str, int] = field(default_factory=dict)  # agent_id -> minute confided
    leaked: bool = False
    leaked_by: str = ""              # the confidant who turned it into a rumor ("" while intact)


class SecretRegistry:
    """Owns every secret and who has been trusted with it."""

    def __init__(self) -> None:
        self.secrets: dict[str, Secret] = {}

    def add(self, owner: str, text: str, sensitivity: float, minute: int) -> Secret:
        sid = uuid.uuid4().hex[:8]
        secret = Secret(
            id=sid, owner=owner, text=text,
            sensitivity=max(0.0, min(1.0, sensitivity)), created_minute=minute,
        )
        self.secrets[sid] = secret
        return secret

    def secrets_of(self, owner: str) -> list[Secret]:
        return [s for s in self.secrets.values() if s.owner == owner]

    def knows(self, secret_id: str, agent_id: str) -> bool:
        """True if the agent owns the secret or has been confided in."""
        s = self.secrets.get(secret_id)
        return s is not None and (s.owner == agent_id or agent_id in s.confided_to)

    def record_confide(self, secret_id: str, to_agent: str, minute: int) -> None:
        s = self.secrets.get(secret_id)
        if s is not None:
            s.confided_to[to_agent] = minute

    def mark_leaked(self, secret_id: str, leaked_by: str) -> None:
        s = self.secrets.get(secret_id)
        if s is not None:
            s.leaked = True
            s.leaked_by = leaked_by
