"""World state serialization -- the substrate for snapshot & resume.

``capture`` turns the whole live simulation (clock, every agent's state /
relationships / episodic memory, the rumor registry, per-location economy,
active world effects, the daily dialogue budget) into one JSON-safe dict.
``restore`` rebuilds that state onto a freshly-seeded world in place.

Resilience is the point: restore *overlays* snapshot values onto already-valid
dataclass instances field by field, so a payload missing a field (an older
schema, or a hand-truncated one) simply keeps the seed default instead of
crashing. A catastrophically bad payload raises, and the caller falls back to a
fresh start -- a broken snapshot must never block startup.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from ..agents.agent import Relationship
from ..agents.core import AgentState, Belief, MemoryItem
from ..social.rumors import Rumor, RumorVersion
from ..social.secrets import Secret

if TYPE_CHECKING:
    from ..agents.decision import DecisionEngine
    from ..world.world import World
    from .engine import SimulationEngine

# Bump when the payload shape changes. Restore is additive/defensive, so a new
# field just needs a sensible dataclass default -- old snapshots keep loading.
#   v1 -> v2: added per-agent semantic memory (beliefs).
#   v2 -> v3: added the secret registry (secrets & confiding).
#   v3 -> v4: added per-location landmarks (world objects & their progress).
#   v4 -> v5: town expansion to 10 residents + 2 locations. Restore is unchanged --
#             it only overlays agents/locations present in the payload, so an old
#             5-agent snapshot loads with the new 5 residents kept at seed state.
SCHEMA_VERSION = 5


# ---- capture -------------------------------------------------------------


def capture(engine: "SimulationEngine", world: "World", decisions: "DecisionEngine") -> dict:
    """Serialize the whole world to a single JSON-safe dict."""
    return {
        "schema_version": SCHEMA_VERSION,
        "minute": engine.now,
        "agents": {aid: _capture_agent(a) for aid, a in world.agents.items()},
        "rumors": {rid: dataclasses.asdict(r) for rid, r in decisions.rumors.rumors.items()},
        "secrets": {sid: dataclasses.asdict(s) for sid, s in decisions.secrets.secrets.items()},
        "locations": {
            lid: {
                "revenue": loc.revenue,
                "revenue_today": loc.revenue_today,
                "revenue_week": loc.revenue_week,
                "landmarks": [dict(lm) for lm in loc.landmarks],
            }
            for lid, loc in world.locations.items()
        },
        "active_effects": [dict(e) for e in world.active_effects],
        "dialogue": {"day": decisions._dialogue_day, "count": decisions._dialogues_today},
    }


def _capture_agent(agent) -> dict:
    return {
        "state": dataclasses.asdict(agent.state),
        "relationships": {oid: dataclasses.asdict(r) for oid, r in agent.relationships.items()},
        "memory": {
            "items": [dataclasses.asdict(m) for m in agent.memory.items],
            "importance_since_reflection": agent.memory.importance_since_reflection,
        },
        "semantic": {"beliefs": [dataclasses.asdict(b) for b in agent.semantic.beliefs]},
    }


# ---- restore -------------------------------------------------------------


def restore(payload: dict, engine: "SimulationEngine", world: "World",
            decisions: "DecisionEngine") -> int:
    """Rebuild live state from ``payload`` in place. Returns the sim minute the
    caller should ``bootstrap`` from. Raises only on a payload that isn't a
    usable dict -- missing individual fields are tolerated (defaults kept)."""
    if not isinstance(payload, dict):
        raise ValueError(f"snapshot payload is {type(payload).__name__}, not a dict")

    minute = int(payload.get("minute", engine.now))

    for aid, adata in (payload.get("agents") or {}).items():
        agent = world.agents.get(aid)
        if agent is not None and isinstance(adata, dict):
            _restore_agent(agent, adata)

    if "rumors" in payload:
        decisions.rumors.rumors = _restore_rumors(payload.get("rumors") or {})

    if "secrets" in payload:                     # v3+; older snapshots keep the empty registry
        decisions.secrets.secrets = _restore_secrets(payload.get("secrets") or {})

    for lid, ldata in (payload.get("locations") or {}).items():
        loc = world.locations.get(lid)
        if loc is not None and isinstance(ldata, dict):
            if "revenue" in ldata:
                loc.revenue = float(ldata["revenue"])
            if "revenue_today" in ldata:
                loc.revenue_today = float(ldata["revenue_today"])
            if "revenue_week" in ldata:  # v5+; older snapshots keep 0.0
                loc.revenue_week = float(ldata["revenue_week"])
            if "landmarks" in ldata:     # v4+; older snapshots keep the seed landmarks
                loc.landmarks = [dict(lm) for lm in (ldata.get("landmarks") or []) if isinstance(lm, dict)]

    if "active_effects" in payload:
        world.active_effects = [dict(e) for e in (payload.get("active_effects") or [])]

    dialogue = payload.get("dialogue") or {}
    if "day" in dialogue:
        decisions._dialogue_day = int(dialogue["day"])
    if "count" in dialogue:
        decisions._dialogues_today = int(dialogue["count"])

    return minute


def _overlay(obj, data: dict) -> None:
    """Copy only known, present fields from ``data`` onto dataclass ``obj``,
    leaving every absent field at its current (seed default) value."""
    names = {f.name for f in dataclasses.fields(obj)}
    for key, value in data.items():
        if key in names:
            setattr(obj, key, value)


def _restore_agent(agent, adata: dict) -> None:
    _overlay(agent.state, adata.get("state") or {})

    if "relationships" in adata:
        rels = {}
        for oid, rdata in (adata.get("relationships") or {}).items():
            rel = Relationship()
            _overlay(rel, rdata or {})
            rels[oid] = rel
        agent.relationships = rels

    if "memory" in adata:
        mem = adata.get("memory") or {}
        items = []
        for idata in mem.get("items", []):
            if not isinstance(idata, dict):
                continue
            item = MemoryItem(minute=idata.get("minute", 0), text=idata.get("text", ""))
            _overlay(item, idata)
            items.append(item)
        agent.memory.items = items
        agent.memory.importance_since_reflection = mem.get("importance_since_reflection", 0)

    if "semantic" in adata:                      # v2+; a v1 snapshot simply keeps the empty default
        sem = adata.get("semantic") or {}
        beliefs = []
        for bd in sem.get("beliefs", []):
            if not isinstance(bd, dict):
                continue
            b = Belief(subject=bd.get("subject", ""), text=bd.get("text", ""))
            _overlay(b, bd)
            beliefs.append(b)
        agent.semantic.beliefs = beliefs


def _restore_rumors(rdata: dict) -> dict:
    out: dict[str, Rumor] = {}
    for rid, data in rdata.items():
        if not isinstance(data, dict):
            continue
        rumor = Rumor(
            id=data.get("id", rid),
            origin=data.get("origin", ""),
            created_minute=data.get("created_minute", 0),
        )
        for key in ("subject", "sentiment", "resolved", "resolved_minute", "outcome", "from_secret_id"):
            if key in data:
                setattr(rumor, key, data[key])
        rumor.versions = [
            RumorVersion(
                agent_id=v.get("agent_id", ""), text=v.get("text", ""),
                heard_from=v.get("heard_from", ""), minute=v.get("minute", 0),
            )
            for v in data.get("versions", []) if isinstance(v, dict)
        ]
        out[rid] = rumor
    return out


def _restore_secrets(sdata: dict) -> dict:
    out: dict[str, Secret] = {}
    for sid, data in sdata.items():
        if not isinstance(data, dict):
            continue
        secret = Secret(id=data.get("id", sid), owner=data.get("owner", ""), text=data.get("text", ""))
        for key in ("sensitivity", "created_minute", "leaked", "leaked_by"):
            if key in data:
                setattr(secret, key, data[key])
        confided = data.get("confided_to")
        if isinstance(confided, dict):
            secret.confided_to = {str(k): int(v) for k, v in confided.items()}
        out[sid] = secret
    return out
