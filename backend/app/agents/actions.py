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
import re
import uuid
from typing import TYPE_CHECKING

from .. import pacing
from . import texts
from . import wishes as wishes_mod
from .core import MemoryItem

if TYPE_CHECKING:
    from ..world.world import World
    from .agent import Agent

DAY = 24 * 60
LOW_ENERGY = 20                  # mirrors decision.LOW_ENERGY: too tired to do anything extra


def _roll(*parts: object) -> float:
    seed = int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)
    return random.Random(seed).random()


# ---- what a public object is ---------------------------------------------------
#
# An object is stored structured: ``kind`` (one of OBJECT_KINDS), ``item`` (what the
# thing is, a short noun phrase) and ``content`` (the words written on it -- only a
# note or a notice has any). Every sentence about an object is built from these by
# ONE phrase (object_phrase); nothing pastes the model's own wording into a template
# any more, which is how "在螢火公園看到了今天下午我在螢火公園等你—艾斯" happened.

OBJECT_KINDS = ("note", "notice", "craft", "repair", "gift", "other")
WRITTEN_KINDS = ("note", "notice")
_QUOTED_RE = re.compile(r"[「『\"“](.+?)[」』\"”]")
_SIGNED_RE = re.compile(r"[—–]\s*\S{1,4}$")
_KIND_WORDS = (
    ("notice", ("告示", "公告", "海報", "看板", "notice", "poster", "sign")),
    ("note", ("紙條", "字條", "便條", "留言", "卡片", "note", "card")),
    ("gift", ("禮物", "小禮", "gift")),
    ("repair", ("修好", "修補", "整理", "佈置", "布置", "擦亮", "鎖緊", "mend", "fix", "tidy", "arrange")),
    ("craft", ("手作", "做了", "刻", "編", "畫", "sketch", "made", "carve")),
)


def _kind_from(text: str, what: str = "") -> str:
    """The thing's own name decides; the doing behind it only when the name is mute
    ("去公告處留一張字條" is a note left at the notice board, not a notice)."""
    for source in (text, what):
        for kind, words in _KIND_WORDS:
            if any(w in source for w in words):
                return kind
    return "other"


def parse_leaves(raw: object, what: str = "") -> dict | None:
    """The plan's "leaves" -> {kind, item, content}, or None when nothing is left.

    The contract asks for {"kind", "item", "words"}. A plain string -- an older plan,
    a model answering the old way, a stored object from before the split -- is taken
    apart: quoted words become the content and the wrapper around them ("一張寫著「…」
    的小紙條") the item; bare signed words under a doing that leaves a note are the
    note's content; anything else is the item itself."""
    if isinstance(raw, dict):
        kind = str(raw.get("kind") or "").strip().lower()
        item = str(raw.get("item") or "").strip().rstrip("。.")
        content = str(raw.get("words") or raw.get("content") or "").strip().strip("「」『』\"").rstrip("。")
        if kind not in OBJECT_KINDS:
            kind = _kind_from(item, what)
        if kind not in WRITTEN_KINDS:
            content = ""
        if not item and not content:
            return None
        return {"kind": kind, "item": item, "content": content}
    text = str(raw or "").strip().rstrip("。.")
    if not text:
        return None
    m = _QUOTED_RE.search(text)
    if m:
        content = m.group(1).strip()
        item = text[:m.start()] + text[m.end():]
        item = item.replace("寫著的", "").replace("上面寫著", "").replace("寫著", "")
        item = item.strip().strip(":：,， ").strip()
        kind = _kind_from(item, what)
        if kind not in WRITTEN_KINDS:
            kind = "note"
        if item and texts.zh_native() and not item.startswith("一"):
            item = "一張" + item
        return {"kind": kind, "item": item, "content": content}
    if _SIGNED_RE.search(text) and _kind_from(what) in WRITTEN_KINDS:
        return {"kind": _kind_from(what), "item": "", "content": text}     # bare words, signed
    return {"kind": _kind_from(text, what), "item": text, "content": ""}


def object_fields(o: dict) -> dict:
    """{kind, item, content} of a stored object -- one from before the split too."""
    if "kind" in o:
        return {"kind": o.get("kind") or "other", "item": o.get("item") or "", "content": o.get("content") or ""}
    return (parse_leaves(o.get("description", ""), o.get("note", ""))
            or {"kind": "other", "item": "", "content": ""})


def object_phrase(o: dict) -> str:
    """The one noun phrase for an object, in the town's language: "一張字條,上面寫著
    「…」", "一隻漂流木刻的小鳥"."""
    f = object_fields(o)
    item = f["item"] or texts.T("obj_item_" + (f["kind"] if f["kind"] in OBJECT_KINDS else "other"))
    return texts.T("obj_written", item=item, content=f["content"]) if f["content"] else item


def objects_at(world: "World", location: str) -> list[dict]:
    """The objects in a place's scene: its newest OBJECTS_PER_PLACE."""
    here = [o for o in world.objects if o.get("location") == location]
    return here[-pacing.OBJECTS_PER_PLACE:]


def scene_lines(world: "World", location: str, now: int) -> list[str]:
    """Objects as a scene shows them -- by age, so a fresh one reads as new."""
    out = []
    for o in objects_at(world, location)[-3:]:
        age = (now - int(o.get("born_minute", now))) // DAY
        name = object_phrase(o)
        # a thing left is new, not "finished" -- a landmark's wording does not fit it
        out.append(texts.T("obj_new", name=name) if age < pacing.FRESH_DAYS else name)
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
    # A doing planned for a wish that the world turned away is a blocked day for that
    # wish -- the same distinct-day counting and frustration memories as the drive.
    wish = next((w for w in agent.wishes if w.id == act.get("wish") and w.status == "active"), None)
    if wish is not None:
        wishes_mod.note_blocked(agent, wish, now)
    agent.memory.add(MemoryItem(minute=now, importance=4, kind="reflection", tags=["action"],
                                text=texts.T("action_failed", what=act["what"], loc=act["location"],
                                             reason=reason)))
    return {"verb": "action_failed", "actor": agent.id, "target": "", "location": act["location"],
            "text": act["what"], "detail": reason}


def person_here(world: "World", act: dict) -> bool:
    who = world.agents.get(act.get("seek") or "")
    return (who is not None and who.state.location == act["location"]
            and who.state.current_action != "sleep")


def approach(agent: "Agent", world: "World", act: dict, now: int) -> dict:
    """Finding someone who is here: the doer's own side only (they went and found
    them). Whatever happens between them is the conversation's to produce."""
    act["state"] = "done"
    loc, who = act["location"], act["seek"]
    if act.get("mode") != "greet":                # a greeting leaves no doing of its own
        agent.memory.add(MemoryItem(minute=now, importance=4, kind="reflection", tags=["action"],
                                    text=texts.T("action_seek", loc=loc, who=who, what=act["what"])))
        for other in world.agents.values():
            if other.id in (agent.id, who) or other.state.location != loc or other.state.current_action == "sleep":
                continue
            if _roll("witness", other.id, agent.id, now) < pacing.ACTION_WITNESS_P:
                other.memory.add(MemoryItem(minute=now, importance=3, kind="observation",
                                            text=texts.T("action_seek_seen", actor=agent.id, loc=loc, who=who)))
    return {"verb": "free_action", "actor": agent.id, "target": who, "location": loc,
            "text": act["what"], "detail": act["what"]}


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
    left = parse_leaves(act.get("leaves"), act["what"])
    if left:
        obj = {"id": uuid.uuid4().hex[:10], "location": loc, "creator": agent.id, **left,
               "born_minute": now,
               "recipient": act.get("recipient", ""), "note": act["what"] if act.get("recipient") else ""}
        obj["description"] = object_phrase(obj)    # the ledger's readable column; never parsed back
        world.objects.append(obj)
        agent.memory.add(MemoryItem(minute=now, importance=5, kind="reflection", tags=["action"],
                                    text=texts.T("action_left", loc=loc, what=object_phrase(obj))))
        agent.state.seen_objects.append(obj["id"])
    for other in world.agents.values():         # whoever is there may notice
        if other.id == agent.id or other.state.location != loc or other.state.current_action == "sleep":
            continue
        if _roll("witness", other.id, agent.id, now) < pacing.ACTION_WITNESS_P:
            other.memory.add(MemoryItem(minute=now, importance=3, kind="observation",
                                        text=texts.T("action_seen", who=agent.id, loc=loc, what=act["what"])))
            if obj is not None:
                other.state.seen_objects.append(obj["id"])
    beat = {"verb": "made_object" if obj else "free_action", "actor": agent.id,
            "target": (obj or {}).get("recipient", ""),
            "location": loc, "text": obj["content"] if obj else act["what"],
            "detail": act["what"],
            "obj": {k: obj[k] for k in ("kind", "item", "content")} if obj else None}
    return beat, obj


def notice_objects(agent: "Agent", world: "World", now: int) -> None:
    """A passer-by takes in what others left here -- one roll per object, ever. A thing
    left FOR this resident is found for certain when they come by: what they make of
    it is theirs (it is only material for their own next decisions and talks)."""
    seen = agent.state.seen_objects
    for o in objects_at(world, agent.state.location):
        if o["id"] in seen or o.get("creator") == agent.id:
            continue
        seen.append(o["id"])
        if o.get("recipient") == agent.id:
            agent.memory.add(MemoryItem(minute=now, importance=5, kind="observation", tags=["delivery"],
                                        text=texts.T("found_for_me", who=o["creator"], loc=o["location"],
                                                     what=object_phrase(o), note=o.get("note") or object_phrase(o))))
            continue
        if _roll("notice", agent.id, o["id"]) < pacing.OBJECT_NOTICE_P:
            agent.memory.add(MemoryItem(minute=now, importance=3, kind="observation",
                                        text=texts.T("object_noticed", loc=o["location"], what=object_phrase(o))))
    if len(seen) > 400:
        del seen[:-400]
