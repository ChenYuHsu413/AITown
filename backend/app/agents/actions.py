"""Free actions and the public objects they leave (活化 phase C).

A resident's morning plan may name up to FREE_ACTIONS_PER_DAY small doings of their
own (agents/dayplan.py validates the plan side). When the hour comes and they are
at the place, the rules -- never the model -- decide whether it happens:

  * it fails, as a real beat with a "wanted to, but couldn't" memory, when the
    money isn't there, the place isn't open, rain keeps them off the park, they
    are worn out, or the hour slips by;
  * it lands as a real beat (notable when it leaves something, otherwise
    ambient), a memory of their own, a real deduction of the money it costs
    (doings only spend; earning stays with the daily settlement), and a chance
    that whoever is there notices;
  * what it leaves becomes a public object at that place: in every scene there
    from then on (described by its age, like a landmark), taken in by passers-by
    on a rule roll, written to the public_objects ledger.

Pure rules, no LLM.
"""

from __future__ import annotations

import hashlib
import random
import uuid
from typing import TYPE_CHECKING

from .. import pacing
from . import texts
from .core import MemoryItem

if TYPE_CHECKING:
    from ..world.world import World
    from .agent import Agent

DAY = 24 * 60
LOW_ENERGY = 20                  # mirrors decision.LOW_ENERGY: too tired to do anything extra


def _roll(*parts: object) -> float:
    seed = int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)
    return random.Random(seed).random()


def objects_at(world: "World", location: str) -> list[dict]:
    """The objects in a place's scene: its newest OBJECTS_PER_PLACE."""
    here = [o for o in world.objects if o.get("location") == location]
    return here[-pacing.OBJECTS_PER_PLACE:]


def scene_lines(world: "World", location: str, now: int) -> list[str]:
    """Objects as a scene shows them -- by age, so a fresh one reads as new."""
    out = []
    for o in objects_at(world, location)[-3:]:
        age = (now - int(o.get("born_minute", now))) // DAY
        out.append(texts.aged_name(o["description"], age, pacing.FRESH_DAYS))
    return out


def failure_reason(agent: "Agent", world: "World", act: dict, now: int) -> str:
    """Why the doing can't happen right now ("" = it can)."""
    loc = world.locations.get(act["location"])
    if loc is None:
        return texts.T("fail_closed")
    day = now // DAY + 1
    dow = (now // DAY) % 7
    if loc.owner and loc.price > 0 and loc.owner != agent.id \
            and (dow in loc.closed_days or loc.closed_on == day):
        return texts.T("fail_closed")
    if loc.kind == "park" and world.effect_active("rain"):
        return texts.T("fail_rain")
    if agent.state.money < act.get("cost", 0):
        return texts.T("fail_money")
    if agent.state.energy <= LOW_ENERGY:
        return texts.T("fail_tired")
    return ""


def fail(agent: "Agent", act: dict, reason: str, now: int) -> dict:
    act["state"] = "failed"
    agent.memory.add(MemoryItem(minute=now, importance=4, kind="reflection", tags=["action"],
                                text=texts.T("action_failed", what=act["what"], loc=act["location"],
                                             reason=reason)))
    return {"verb": "action_failed", "actor": agent.id, "target": "", "location": act["location"],
            "text": act["what"], "detail": reason}


def land(agent: "Agent", world: "World", act: dict, now: int) -> tuple[dict, dict | None]:
    """The doing happens. Returns (event beat, new public object or None)."""
    act["state"] = "done"
    cost = int(act.get("cost", 0))
    if cost:
        agent.state.money -= cost
    loc = act["location"]
    agent.memory.add(MemoryItem(minute=now, importance=5, kind="reflection", tags=["action"],
                                text=texts.T("action_self", loc=loc, what=act["what"])))
    obj = None
    if act.get("leaves"):
        obj = {"id": uuid.uuid4().hex[:10], "location": loc, "creator": agent.id,
               "description": act["leaves"], "born_minute": now}
        world.objects.append(obj)
        agent.memory.add(MemoryItem(minute=now, importance=5, kind="reflection", tags=["action"],
                                    text=texts.T("action_left", loc=loc, what=act["leaves"])))
        agent.state.seen_objects.append(obj["id"])
    for other in world.agents.values():         # whoever is there may notice
        if other.id == agent.id or other.state.location != loc or other.state.current_action == "sleep":
            continue
        if _roll("witness", other.id, agent.id, now) < pacing.ACTION_WITNESS_P:
            other.memory.add(MemoryItem(minute=now, importance=3, kind="observation",
                                        text=texts.T("action_seen", who=agent.id, loc=loc, what=act["what"])))
            if obj is not None:
                other.state.seen_objects.append(obj["id"])
    beat = {"verb": "made_object" if obj else "free_action", "actor": agent.id, "target": "",
            "location": loc, "text": act["leaves"] if obj else act["what"],
            "detail": act["what"]}
    return beat, obj


def notice_objects(agent: "Agent", world: "World", now: int) -> None:
    """A passer-by takes in what others left here -- one roll per object, ever."""
    seen = agent.state.seen_objects
    for o in objects_at(world, agent.state.location):
        if o["id"] in seen or o.get("creator") == agent.id:
            continue
        seen.append(o["id"])
        if _roll("notice", agent.id, o["id"]) < pacing.OBJECT_NOTICE_P:
            agent.memory.add(MemoryItem(minute=now, importance=3, kind="observation",
                                        text=texts.T("object_noticed", loc=o["location"], what=o["description"])))
    if len(seen) > 400:
        del seen[:-400]
