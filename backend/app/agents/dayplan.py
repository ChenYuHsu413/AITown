"""The morning micro-decision: "how do I want to spend today?" (活化 phase B).

Once a day, as the town turns over, every resident gets one cheap call that reads
their own yesterday, mood, wishes, chapter (labelled stuck when it is), people and
usual timetable, and answers with a first-person intent plus at most a few concrete
changes to the day. "Nothing special" is a legal -- and the common -- answer.

Rules own everything around that answer:
  * priority: today's own plan > a wish's drive > the routine; sleep and the hard
    physiology rules (low energy) are never the plan's to change;
  * a slot must name a real place, keep inside waking hours, and fit the money
    (a meal out needs the price in hand); work only happens at a work place;
  * skipping work is legal, gated by personality: the stated wish to skip must
    exceed the resident's conscientiousness. A shop owner who skips shuts the shop
    for the day (no takings; customers find it closed and remember it).

The plan is private: only its consequences (a closed shop, someone somewhere
unusual, the skipped-work beat) are visible to others.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import pacing
from . import mood as mood_mod
from . import transitions as transitions_mod
from . import wishes as wishes_mod
from .routine import RoutineEntry

if TYPE_CHECKING:
    from ..world.world import World
    from .agent import Agent

DAY = 24 * 60
DOING_ACTION = {"visit": "idle", "rest": "rest", "idle": "idle", "work": "work", "eat": "eat"}
_DOW = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_HOME_WORDS = ("home", "my place", "家")


def _hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def routine_segments(agent: "Agent", day: int) -> list[tuple[int, int, str, str]]:
    """Today's timetable as (from, to, action, location) runs, sampled every 30 min."""
    dow = (day - 1) % 7
    out: list[list] = []
    for m in range(0, DAY, 30):
        e = agent.routine.current(m, dow)
        if out and out[-1][2] == e.action and out[-1][3] == e.location:
            out[-1][1] = m + 30
        else:
            out.append([m, m + 30, e.action, e.location])
    return [tuple(x) for x in out]


def off_today(agent: "Agent", world: "World", day: int) -> bool:
    dow = (day - 1) % 7
    if dow in agent.profile.off_days:
        return True
    for loc in world.locations.values():
        if loc.price > 0 and loc.owner in (agent.id, agent.state.employer) and loc.owner and dow in loc.closed_days:
            return True
    return False


def _duty(agent: "Agent") -> str:
    c = float(agent.profile.personality.get("conscientiousness", 0.5))
    return ("very dutiful" if c >= 0.8 else "dutiful" if c >= 0.6
            else "easygoing" if c >= 0.4 else "free-spirited")


def material(agent: "Agent", world: "World", decisions, day: int) -> dict:
    """What this resident wakes up with. Everything here is their own."""
    from .decision import _resolve_placeholders
    res = lambda t: _resolve_placeholders(t, world)          # noqa: E731
    day_start = (day - 1) * DAY
    y0 = day_start - DAY
    yesterday = [res(m.text) for m in agent.memory.items
                 if y0 <= m.minute < day_start and m.importance >= 4 and m.kind != "biography"][-6:]
    talked = [world.agents[x].name for x, mm in agent.state.last_talk_minute.items()
              if y0 <= mm < day_start and x in world.agents]
    wishes = []
    for w in wishes_mod.active_wishes(agent):
        line = f"[{w.scale}] {w.title}: {w.statement}" if w.title else w.statement
        if w.expires_on:
            line += f" ({max(0, w.expires_on - day)} days left)"
        wishes.append(res(line))
    ch = agent.chapter
    chapter = ""
    if ch is not None:
        days_in = max(0, day - ch.started_on)
        # The narrative itself is already on the card; this adds only its age and,
        # for a pursuit going nowhere, says so plainly.
        chapter = f"This chapter of your life has run {days_in} days"
        if ch.chapter_type == "pursuit" and days_in >= pacing.DAYPLAN_STUCK_DAYS:
            w = wishes_mod.wish_by_chapter(agent, ch.id)
            if w is None or w.progress == 0:
                chapter += ", and nothing in it has moved -- it is stuck"
        chapter += "."
    rels = sorted(((world.agents[o], r) for o, r in agent.relationships.items() if o in world.agents),
                  key=lambda x: -x[1].friendship)
    people = [f"{o.name} ({transitions_mod.rel_stage(r.friendship)})" for o, r in rels[:3]]
    tense = [o.name for o, r in rels if getattr(r, "conflict", 0) >= 40]
    usual = [f"{_hhmm(a)}-{_hhmm(b)} {act} at {res('{loc:' + loc + '}')}"
             for a, b, act, loc in routine_segments(agent, day) if act != "sleep"]
    promise = ""
    m = agent.state.pending_meetup
    if m and m.get("promised") and day_start <= m.get("minute", -1) < day_start + DAY:
        promise = (f"You promised to meet {world.agents[m['partner']].name if m['partner'] in world.agents else m['partner']}"
                   f" at {res('{loc:' + m['location'] + '}')} around {_hhmm(m['minute'] - day_start)} today.")
    places = [res("{loc:" + lid + "}") for lid, loc in world.locations.items()
              if loc.kind != "home" or lid == agent.home]
    from . import actions as actions_mod
    objects = []
    for lid in world.locations:
        for line in actions_mod.scene_lines(world, lid, day_start):
            objects.append(f"{line} ({res('{loc:' + lid + '}')})")
    did = [res(m.text) for m in agent.memory.items
           if "action" in (m.tags or []) and m.minute >= day_start - 7 * DAY][-4:]
    return {
        "day": day, "dow": _DOW[(day - 1) % 7], "rain": bool(world.effect_active("rain")),
        # A day off is also any day the timetable simply has no work in it (a weekend).
        "off": off_today(agent, world, day)
               or not any(act == "work" for _, _, act, _ in routine_segments(agent, day)),
        "mood": mood_mod.describe(agent, world),
        "duty": _duty(agent), "usual": usual, "yesterday": yesterday, "talked": talked,
        "wishes": wishes, "chapter": chapter, "people": people, "tense": tense,
        "promise": promise, "places": places, "money": int(agent.state.money),
        "objects": objects[-6:], "did": did,
    }


def _place(world: "World", agent: "Agent", raw: object) -> str:
    from .decision import DecisionEngine
    name = str(raw or "").strip()
    if any(w in name.lower() for w in _HOME_WORDS) and not DecisionEngine._place_id(world, name):
        return agent.home
    lid = DecisionEngine._place_id(world, name)
    loc = world.locations.get(lid)
    if loc is not None and loc.kind == "home" and lid != agent.home:
        return ""                                   # never someone else's home uninvited
    return lid


def _action_place(world: "World", agent: "Agent", raw: object) -> str:
    """Where a free action may happen: anywhere open to them -- a public place, a
    shop as a customer, their own home (``_place`` already refuses others' homes)."""
    return _place(world, agent, raw)


def _names_someone(text: str, agent: "Agent", world: "World") -> bool:
    from . import texts as texts_mod
    return any(texts_mod.mentions(text, o) for o in world.agents if o != agent.id)


# Controlling another resident -- the one thing a free action may never do. Mentioning
# someone is fine (waiting for them somewhere, a note or a gift left for them, an
# invitation, even a provocation: all of those are the other person's INPUT, and what
# they make of it is theirs to decide on their own turn). What is refused: an action
# that states the other person's response or outcome, or that handles their
# belongings or their body.
_OUTCOME_BEFORE = r"(?:讓|叫|要|使|逼|逼著|說服|勸|求)"            # 讓X答應 / 說服X
_OUTCOME_PAIR = r"(?:和好|復合|和解|在一起|交往|說開|講和|重修舊好)"   # 和X和好
_OUTCOME_AFTER = r"(?:答應|同意|原諒|接受|喜歡上|愛上|收下|回覆|回應|點頭|笑了|哭了|感動|道歉)"  # X答應了
_BODY = r"(?:抱|擁抱|牽|親|吻|摸|拍|扶|推|拉|揉|握住)"               # 抱X
_PROPERTY = r"(?:修|拿|動|搬|借|用|換|丟|整理|打掃|收拾|翻|開|拆|改|佔)"  # 修好X的…


def _forms(agent_id: str, world: "World") -> list[str]:
    from . import texts as texts_mod
    a = world.agents[agent_id]
    return [f for f in {a.name, texts_mod.display_name(agent_id), agent_id.capitalize()} if f]


def controls_someone(text: str, agent: "Agent", world: "World") -> str:
    """The resident this action would control ("" = it controls nobody)."""
    import re
    for oid in world.agents:
        if oid == agent.id:
            continue
        for n in _forms(oid, world):
            e = re.escape(n)
            if (re.search(rf"{_OUTCOME_BEFORE}{e}", text)
                    or re.search(rf"(?:和|跟|與){e}[^,，。;；]{{0,3}}{_OUTCOME_PAIR}", text)
                    or re.search(rf"{e}[^,，。;；]{{0,3}}{_OUTCOME_AFTER}", text)
                    or re.search(rf"{_BODY}(?:了|著)?{e}", text)
                    or re.search(rf"{_PROPERTY}[^,，。;；]{{0,4}}{e}的", text)):
                return oid
    return ""


def addressee(text: str, agent: "Agent", world: "World") -> str:
    """The resident a one-way delivery is for: the first one named ("" = nobody)."""
    from . import texts as texts_mod
    hits = [(text.find(n), oid) for oid in world.agents if oid != agent.id
            for n in _forms(oid, world) if n and n in text]
    return min(hits)[1] if hits else ""


def validate_actions(raw: object, agent: "Agent", world: "World", day: int, slots: list) -> tuple:
    """The free-action contract, plan side: a real, permitted place, waking hours, a
    sane cost, readable Chinese, and control of nobody else (never another person's
    response, belongings or body -- see controls_someone). Naming someone is fine: a
    thing left FOR them becomes a delivery with a recipient, which they find on their
    own turn. Returns (actions, dropped)."""
    from .decision import _knowledge_lang_ok
    dow = (day - 1) % 7
    out: list[dict] = []
    dropped: list[str] = []
    for a in (raw if isinstance(raw, list) else [])[:pacing.FREE_ACTIONS_PER_DAY]:
        if not isinstance(a, dict):
            continue
        what = str(a.get("what") or "").strip().rstrip("。")
        leaves = str(a.get("leaves") or "").strip().rstrip("。")
        try:
            hour = int(a.get("hour"))
            cost = max(0, int(a.get("cost") or 0))
        except (TypeError, ValueError):
            dropped.append("action: bad hour/cost"); continue
        loc = _action_place(world, agent, a.get("place"))
        if not what or len(what) > 60 or not _knowledge_lang_ok(what):
            dropped.append("action: unreadable"); continue
        if leaves and (len(leaves) > 40 or not _knowledge_lang_ok(leaves)):
            leaves = ""
        if not loc:
            dropped.append("action: no such place"); continue
        if world.locations[loc].kind == "home" and loc != agent.home:
            dropped.append("action: someone else's home"); continue
        from .decision import gender_ok
        if controls_someone(what + "," + leaves, agent, world):
            dropped.append(f"action: controls someone else ({what[:24]})"); continue
        if not gender_ok(what) or (leaves and not gender_ok(leaves)):
            dropped.append(f"action: wrong pronoun ({what[:24]})"); continue
        recipient = addressee(what + "," + leaves, agent, world) if leaves else ""
        owner = world.locations[loc].owner
        if leaves and owner and owner != agent.id and owner != recipient:
            leaves = ""        # a customer may not leave things in a shop -- unless it is for its owner
        if not 7 <= hour <= 21 or agent.routine.current(hour * 60, dow).action == "sleep":
            dropped.append("action: sleep time"); continue
        if cost > pacing.FREE_ACTION_MAX_COST:
            dropped.append("action: too costly"); continue
        fr, to = hour * 60, hour * 60 + 60
        clash = [x for x in slots if fr < x["to"] and x["from"] < to]
        # Inside a stretch they already mean to spend at that very place, the doing
        # simply happens there; a stretch somewhere else is a real conflict.
        within = bool(clash) and all(x["location"] == loc and x["action"] != "free" for x in clash)
        if clash and not within:
            dropped.append(f"action: overlaps ({what[:24]})"); continue
        idx = len(out)
        out.append({"what": what, "leaves": leaves, "cost": cost, "location": loc,
                    "from": fr, "to": to, "state": "planned", "within": within,
                    "recipient": recipient if leaves else ""})
        if not within:
            slots.append({"from": fr, "to": to, "location": loc, "action": "free", "act": idx})
    return out, dropped


def validate(raw: object, agent: "Agent", world: "World", day: int) -> dict:
    """The model's answer -> a plan the rules accept. Unacceptable slots are dropped
    one by one (recorded), never the whole morning."""
    from .decision import _knowledge_lang_ok
    p = raw if isinstance(raw, dict) else {}
    intent = str(p.get("intent") or "").strip()
    if len(intent) > 160 or not _knowledge_lang_ok(intent):
        intent = ""
    nothing = p.get("nothing_special") is True
    try:
        desire = max(0.0, min(1.0, float(p.get("skip_work", 0) or 0)))
    except (TypeError, ValueError):
        desire = 0.0
    dow = (day - 1) % 7
    work_places = set(wishes_mod.work_locations(agent, world))
    slots: list[dict] = []
    dropped: list[str] = []
    raw_slots = p.get("slots") if isinstance(p.get("slots"), list) else []
    for s in ([] if nothing else raw_slots)[:pacing.DAYPLAN_MAX_SLOTS]:
        if not isinstance(s, dict):
            continue
        try:
            fr, to = int(s.get("from")), int(s.get("to"))
        except (TypeError, ValueError):
            dropped.append("bad hours"); continue
        doing = str(s.get("doing") or "").strip().lower()
        loc = _place(world, agent, s.get("place"))
        from .decision import DecisionEngine
        named = world.locations.get(DecisionEngine._place_id(world, str(s.get("place") or "")))
        if not loc and named is not None and named.kind == "home":
            dropped.append("someone else's home"); continue
        if not (7 <= fr < to <= 22) or to - fr > pacing.DAYPLAN_MAX_SLOT_HOURS:
            dropped.append("hours out of range"); continue
        if doing not in DOING_ACTION:
            dropped.append("unknown activity"); continue
        if not loc:
            dropped.append("no such place"); continue
        if any(agent.routine.current(m, dow).action == "sleep" for m in range(fr * 60, to * 60, 30)):
            dropped.append("sleep time"); continue
        if any(fr * 60 < x["to"] and x["from"] < to * 60 for x in slots):
            dropped.append("overlaps"); continue
        if doing == "work" and loc not in work_places:
            dropped.append("no work there"); continue
        place = world.locations[loc]
        if doing == "eat" and place.price > 0 and agent.state.money < place.price:
            dropped.append("can't afford it"); continue
        slots.append({"from": fr * 60, "to": to * 60, "location": loc, "action": DOING_ACTION[doing]})
    actions, a_dropped = validate_actions([] if nothing else p.get("actions"), agent, world, day, slots)
    dropped += a_dropped
    work_day = (not off_today(agent, world, day)
                and any(act == "work" for _, _, act, _ in routine_segments(agent, day)))
    duty = float(agent.profile.personality.get("conscientiousness", 0.5))
    skip = (not nothing) and work_day and desire > duty
    return {"day": day, "intent": intent, "slots": slots, "skip_work": skip,
            "skip_desire": round(desire, 2), "dropped": dropped, "actions": actions}


def action_at(agent: "Agent", start: int) -> dict | None:
    """The planned free action whose hour starts at ``start`` (minute of day)."""
    for a in (agent.state.day_plan or {}).get("actions") or []:
        if a["from"] == start:
            return a
    return None


def defer_actions(agent: "Agent", lost_from: int, now: int) -> int:
    """Time a resident spent frozen while a conversation was still generating is not
    their own lateness: a planned doing whose hour fell in [lost_from, now) moves to
    start now (same day, before 22:00), as its own stretch at its place. Returns how
    many were moved."""
    plan = agent.state.day_plan
    if not plan or plan.get("day") != now // DAY + 1 or now <= lost_from:
        return 0
    lo, mod = lost_from % DAY, now % DAY
    if lost_from // DAY != now // DAY:
        lo = 0
    moved = 0
    for i, a in enumerate(plan.get("actions") or []):
        if a.get("state") != "planned" or not (a["from"] < mod and a["to"] > lo):
            continue
        if mod + 60 > 22 * 60:
            continue                      # too late today -- the lapse rule will settle it
        plan["slots"] = [s for s in plan.get("slots") or [] if s.get("act") != i]
        a["from"], a["to"], a["within"] = mod, mod + 60, False
        plan["slots"].append({"from": mod, "to": mod + 60, "location": a["location"], "action": "free", "act": i})
        moved += 1
    return moved


def action_due(agent: "Agent", now: int) -> dict | None:
    """A planned doing that rides inside a same-place stretch and whose hour is now."""
    plan = agent.state.day_plan
    if not plan or plan.get("day") != now // DAY + 1:
        return None
    mod = now % DAY
    for a in plan.get("actions") or []:
        if a.get("within") and a.get("state") == "planned" and a["from"] <= mod < a["to"]:
            return a
    return None


def lapsed_actions(agent: "Agent", now: int) -> list[dict]:
    """Free actions whose hour has passed undone -- each is marked failed once."""
    plan = agent.state.day_plan
    if not plan or plan.get("day") != now // DAY + 1:
        return []
    out = []
    for a in plan.get("actions") or []:
        if a.get("state") == "planned" and now % DAY >= a["to"]:
            a["state"] = "failed"
            out.append(a)
    return out


def next_start(agent: "Agent", now: int) -> int | None:
    """Minute-of-day at which today's next plan slot begins (None = no more today),
    so a long routine stretch never runs over the start of the resident's own plan."""
    plan = agent.state.day_plan
    if not plan or plan.get("day") != now // DAY + 1:
        return None
    mod = now % DAY
    starts = [s["from"] for s in plan.get("slots") or [] if s["from"] > mod]
    # ...and a doing that rides inside a stretch: its hour must get a decision too
    starts += [a["from"] for a in plan.get("actions") or []
               if a.get("state") == "planned" and a["from"] > mod]
    return min(starts) if starts else None


def entry_for(agent: "Agent", now: int, routine_entry: RoutineEntry) -> tuple | None:
    """Today's own plan, where it applies at ``now``: (entry, slot end minute-of-day
    or None). Never during sleep."""
    plan = agent.state.day_plan
    if not plan or plan.get("day") != now // DAY + 1 or routine_entry.action == "sleep":
        return None
    mod = now % DAY
    # A doing's own hour wins over a stretch it overlaps (a deferred doing lands
    # inside some other stretch of the day).
    for s in sorted(plan.get("slots") or [], key=lambda s: s.get("action") != "free"):
        if s["from"] <= mod < s["to"]:
            return RoutineEntry(s["from"], s["action"], s["location"]), s["to"]
    if plan.get("skip_work") and routine_entry.action == "work":
        return RoutineEntry(routine_entry.start, "rest", agent.home), None
    return None
