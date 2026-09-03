"""Life chapters -- the data model and the rule-layer closure pipeline.

A resident is always *in* a chapter:

    pursuit    a concrete thing they are working toward (finish the installation,
               decide whether to quit) -- the only kind that can be *closed*
    interlude  the short aimless stretch right after a pursuit ends (3-7 sim-days)
    ordinary   plain daily life; the resting state once the interlude lapses

Closing a pursuit is the point of this module: the self-narrative, the memory
weights, the beliefs and the prompt context all turn the page together, so a
resident stops living inside a matter that has already ended.

Everything here is pure rules. The one LLM call in the pipeline (the closure
reflection, see builders.chapter_closure_prompt) produces only the biography
line + emotional residue; ``apply_closure`` is synchronous and atomic, and a
template line stands in when the model fails, so closure never blocks.

``agent.chapter is None`` is treated as an implicit *ordinary* chapter everywhere
(prompt builders, decision rules), so a pre-chapter snapshot loads and behaves
exactly as before until scripts/backfill_chapters.py initializes it.

Phase 2 wish generation runs after ``end_interlude`` at the daily boundary; this
module keeps the chapter transition itself synchronous and model-free.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

from .core import MemoryItem

if TYPE_CHECKING:
    from ..world.world import World
    from .agent import Agent

DAY_MIN = 24 * 60

CHAPTER_TYPES = ("pursuit", "interlude", "ordinary")
OUTCOMES = ("completed", "failed", "abandoned")

# Retrieval weight multiplier applied to every memory/belief the closed chapter was
# about (they are never deleted -- they just stop dominating the narrative).
DOWNWEIGHT_COEFF = 0.3

# Emotional residue vocabulary the closure reflection may return; anything else
# falls back to the outcome's default. Each maps to a base interlude length.
RESIDUES: dict[str, int] = {
    "fulfilled": 4, "relieved": 3, "proud": 4,
    "wistful": 6, "restless": 5, "unmoored": 7,
}
RESIDUE_DEFAULT = {"completed": "fulfilled", "failed": "unmoored", "abandoned": "relieved"}
INTERLUDE_MIN_DAYS, INTERLUDE_MAX_DAYS = 3, 7

# Interlude behaviour tendencies (Level 0 rules; see decision.py).
INTERLUDE_DRIFT_P = 0.15       # per routine decision: wander to a public place instead of the timetable
INTERLUDE_SOCIAL_MULT = 1.3    # should_talk pre-gate probability multiplier
INTERLUDE_MEETUP_MULT = 1.5    # daily meetup-attempt probability multiplier

# Closure material: how many chapter memories the reflection sees.
CLOSURE_MEMORIES_N = 6
# Biography surfacing: a query must share this many theme words with the entry.
BIOGRAPHY_TOPIC_MIN = 2

_STOP = {
    "want", "have", "been", "that", "this", "with", "from", "they", "them", "their",
    "about", "every", "time", "just", "keep", "make", "your", "into", "will", "would",
    "could", "should", "there", "when", "what", "whether", "figure", "finish", "the",
    "and", "for", "her", "his", "him", "she", "our", "out", "more", "than", "really",
    "single", "person", "town", "well", "feel", "feels", "right", "done", "some",
}


def theme_keywords(*texts: str) -> list[str]:
    """Distinctive lower-case words (len > 3, minus stop words) across ``texts`` --
    the theme a chapter is 'about'. Placeholders like {landmark:installation}
    contribute their id (so 'installation' matches later mentions)."""
    out: list[str] = []
    for t in texts:
        for w in (t or "").replace("{", " ").replace("}", " ").replace(":", " ").split():
            c = w.strip(".,;:!?'\"()-").lower()
            if len(c) > 3 and c not in _STOP and c not in out:
                out.append(c)
    return out


def goal_key(goal_text: str) -> str:
    """Stable id for a goal (goals are plain {"goal", "priority"} dicts with no id)."""
    return hashlib.sha1((goal_text or "").strip().lower().encode()).hexdigest()[:8]


def memory_id(agent_id: str, item: MemoryItem) -> str:
    """Stable handle for a memory (memories carry no id of their own)."""
    return hashlib.sha1(f"{agent_id}|{item.minute}|{item.text}".encode()).hexdigest()[:8]


# ---- model ------------------------------------------------------------------


@dataclass
class Chapter:
    """The chapter a resident is currently living."""
    id: str
    chapter_type: str                       # pursuit | interlude | ordinary
    title: str                              # short English line (display layer translates)
    narrative: str                          # 1-2 sentences: "where I am right now" (prompt context)
    started_on: int                         # sim day (1-based)
    related_goal_id: str | None = None      # goal_key(goal) for a pursuit; None otherwise
    goal: str = ""                          # the pursuit's goal text (moved off profile.goals)
    related_landmark_id: str = ""           # landmark this pursuit builds ("" = none)
    theme: list[str] = field(default_factory=list)   # theme_keywords(goal, title, landmark)
    # interlude only
    emotional_residue: str = ""
    until_day: int = 0                      # interlude lapses at the settlement of this day

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Chapter":
        c = cls(id=str(d.get("id") or uuid.uuid4().hex[:8]),
                chapter_type=str(d.get("chapter_type", "ordinary")),
                title=str(d.get("title", "")), narrative=str(d.get("narrative", "")),
                started_on=int(d.get("started_on", 1)))
        for k in ("related_goal_id", "goal", "related_landmark_id", "emotional_residue", "until_day"):
            if k in d:
                setattr(c, k, d[k])
        if isinstance(d.get("theme"), list):
            c.theme = [str(x) for x in d["theme"]]
        return c


@dataclass
class ChapterRecord:
    """A closed chapter, as kept in ``chapter_history`` (append-only)."""
    chapter: dict                           # the Chapter as it was
    ended_on: int                           # sim day
    outcome: str                            # completed | failed | abandoned
    biography_line: str                     # first-person English, the closure's one line
    emotional_residue: str
    memory_refs: list[dict] = field(default_factory=list)   # [{"id", "text"}] the reflection drew on
    trigger: str = ""                       # landmark | transition | secret_resolved | manual | backfill
    biography_source: str = "template"      # "llm" or "template"
    downweighted_memories: int = 0
    downweighted_beliefs: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ChapterRecord":
        r = cls(chapter=dict(d.get("chapter") or {}), ended_on=int(d.get("ended_on", 0)),
                outcome=str(d.get("outcome", "completed")),
                biography_line=str(d.get("biography_line", "")),
                emotional_residue=str(d.get("emotional_residue", "")))
        for k in ("trigger", "biography_source", "downweighted_memories", "downweighted_beliefs"):
            if k in d:
                setattr(r, k, d[k])
        if isinstance(d.get("memory_refs"), list):
            r.memory_refs = [dict(x) for x in d["memory_refs"] if isinstance(x, dict)]
        return r


# ---- constructors ---------------------------------------------------------------


def make_pursuit(goal: str, title: str, narrative: str, day: int,
                 landmark_id: str = "") -> Chapter:
    return Chapter(
        id=uuid.uuid4().hex[:8], chapter_type="pursuit", title=title, narrative=narrative,
        started_on=day, related_goal_id=goal_key(goal), goal=goal,
        related_landmark_id=landmark_id,
        theme=theme_keywords(goal, title, landmark_id),
    )


def make_ordinary(agent: "Agent", day: int) -> Chapter:
    occ = agent.profile.occupation
    return Chapter(
        id=uuid.uuid4().hex[:8], chapter_type="ordinary", title="Ordinary days",
        narrative=(f"Living an ordinary stretch of days as a {occ.lower()}; nothing in "
                   f"particular is pulling at me right now."),
        started_on=day,
    )


_RESIDUE_NARRATIVE = {
    "fulfilled": "I just finished something that mattered to me and feel quietly full; "
                 "for now I'm drifting through the days without a next big thing.",
    "relieved": "Something that weighed on me for a long time is finally over. I'm relieved, "
                "a little empty, and taking a few unhurried days with no plan.",
    "proud": "I saw a big thing through and I'm proud of it; I haven't picked what comes "
             "next, so the days feel loose and unscheduled.",
    "wistful": "A chapter of my life just closed and I keep looking back at it. I'm a bit "
               "aimless right now and not ready to start anything new.",
    "restless": "I just came out the other side of something big. My hands are itching for "
                "the next thing but I haven't found it, so I wander and talk more than usual.",
    "unmoored": "Something I'd built my days around didn't work out. I feel unmoored -- "
                "going through the motions, drifting, more open to company than usual.",
}


def make_interlude(residue: str, day: int, until_day: int) -> Chapter:
    return Chapter(
        id=uuid.uuid4().hex[:8], chapter_type="interlude", title="Between chapters",
        narrative=_RESIDUE_NARRATIVE.get(residue, _RESIDUE_NARRATIVE["fulfilled"]),
        started_on=day, emotional_residue=residue, until_day=until_day,
    )


def interlude_days(residue: str, agent: "Agent") -> int:
    """3-7 sim-days from residue + personality (rules only)."""
    n = RESIDUES.get(residue, 5)
    p = agent.profile.personality
    if p.get("neuroticism", 0.4) >= 0.55:
        n += 1
    if p.get("extraversion", 0.5) >= 0.7:
        n -= 1
    return max(INTERLUDE_MIN_DAYS, min(INTERLUDE_MAX_DAYS, n))


# ---- queries ----------------------------------------------------------------------


def chapter_type(agent: "Agent") -> str:
    return agent.chapter.chapter_type if agent.chapter is not None else "ordinary"


def in_interlude(agent: "Agent") -> bool:
    return chapter_type(agent) == "interlude"


def narrative(agent: "Agent") -> str:
    """The 'where I am right now' line for the character card. An uninitialized
    (None) chapter reads as ordinary days."""
    if agent.chapter is not None and agent.chapter.narrative:
        return agent.chapter.narrative
    return make_ordinary(agent, 1).narrative


def is_related_memory(item: MemoryItem, chapter: Chapter) -> bool:
    """A memory is 'about' a pursuit when it names its landmark or shares >= 2
    theme words with it (the same heuristic the resolved-worry fade uses)."""
    if item.kind == "biography":
        return False
    if chapter.related_landmark_id and f"{{landmark:{chapter.related_landmark_id}}}" in item.text:
        return True
    words = {w.strip(".,;:!?'\"()-").lower() for w in item.text.split()}
    return len(words & set(chapter.theme)) >= BIOGRAPHY_TOPIC_MIN


def is_related_belief(belief, chapter: Chapter) -> bool:
    words = {w.strip(".,;:!?'\"()-").lower() for w in belief.text.split()}
    return len(words & set(chapter.theme)) >= BIOGRAPHY_TOPIC_MIN


def goal_matches_chapter(chapter: Chapter | None, *substrings: str) -> bool:
    """True when a pursuit chapter's goal contains any of the substrings (how a
    life-transition template names the goals it makes moot)."""
    if chapter is None or chapter.chapter_type != "pursuit":
        return False
    g = chapter.goal.lower()
    return any(s and s.lower() in g for s in substrings)


def secret_matches_chapter(chapter: Chapter | None, secret) -> bool:
    """A resolved secret closes a pursuit when they share the theme (>= 2 words) or
    the secret's `about` person is named in the goal."""
    if chapter is None or chapter.chapter_type != "pursuit":
        return False
    about = (getattr(secret, "about", "") or "").lower()
    if about and about in chapter.goal.lower():
        return True
    kw = set(theme_keywords(getattr(secret, "text", "")))
    return len(kw & set(chapter.theme)) >= BIOGRAPHY_TOPIC_MIN


# ---- closure material (rules) ------------------------------------------------------


def _top_related(agent: "Agent", chapter: Chapter, lo: int, hi: int) -> list[MemoryItem]:
    """Chapter-related memories with lo <= minute < hi, top-N by importance x weight,
    returned in time order."""
    related = [m for m in agent.memory.items if lo <= m.minute < hi and is_related_memory(m, chapter)]
    related.sort(key=lambda m: (m.importance * m.weight, m.minute), reverse=True)
    top = related[:CLOSURE_MEMORIES_N]
    top.sort(key=lambda m: m.minute)
    return top


def _mem_dicts(agent: "Agent", items: list[MemoryItem]) -> list[dict]:
    return [{"id": memory_id(agent.id, m), "text": m.text, "minute": m.minute,
             "importance": m.importance} for m in items]


def closure_material(agent: "Agent", world: "World", chapter: Chapter, now: int,
                     ended_minute: int | None = None,
                     aftermath_window: tuple[int, int] | None = None,
                     forbid_terms: tuple[str, ...] = ()) -> dict:
    """Assemble what the closure reflection sees: the chapter's high-importance
    memories (top-N by importance x weight, with stable ids), a Python-computed
    relationship summary, and the window itself -- ALL filtered to the chapter's
    span (``started_on`` through the end day, inclusive). A memory from after the
    chapter ended is never part of the pursuit's story.

    ``ended_minute`` is when the matter actually ended when that is earlier than
    ``now`` (a retroactive closure: the landmark finished on Day 3, the closure runs
    on Day 102). The span given to the model must be the true one -- it is the only
    duration the model may quote.

    ``aftermath_window`` (first_day, last_day): optionally ALSO hand the model the
    related memories from after the chapter closed, labelled as such -- for an
    abandoned chapter the closure should know whether they ever went back to it.
    ``forbid_terms``: words the biography line must not contain (a closure whose
    outcome forbids a meaning, e.g. "asked" for a never-asked question)."""
    start_min = (chapter.started_on - 1) * DAY_MIN
    end_min = now if ended_minute is None else max(start_min, min(ended_minute, now))
    end_excl = (end_min // DAY_MIN + 1) * DAY_MIN          # through the end of the end day
    top = _top_related(agent, chapter, start_min, end_excl)
    related_count = sum(1 for m in agent.memory.items if is_related_memory(m, chapter))

    aftermath: list[dict] = []
    if aftermath_window is not None:
        a_lo = (int(aftermath_window[0]) - 1) * DAY_MIN
        a_hi = int(aftermath_window[1]) * DAY_MIN
        aftermath = _mem_dicts(agent, _top_related(agent, chapter, max(a_lo, end_excl), a_hi))

    # Who they interacted with most during the chapter (conversation memories in
    # the window), who helped (trusted + talked to), who rubbed them wrong (conflict).
    talks: dict[str, int] = {}
    for m in agent.memory.items:
        if (m.kind == "conversation" and start_min <= m.minute < end_excl
                and "Talked with {agent:" in m.text):
            oid = m.text.split("{agent:", 1)[1].split("}", 1)[0]
            talks[oid] = talks.get(oid, 0) + 1
    most = sorted(talks.items(), key=lambda kv: -kv[1])[:3]
    helpers = [oid for oid, _ in most if agent.rel(oid).trust >= 60]
    friction = [oid for oid, r in agent.relationships.items() if r.conflict >= 20]
    return {
        "chapter": chapter.to_dict(),
        "window": {"start_minute": start_min, "end_minute": end_min,
                   "start_day": start_min // DAY_MIN + 1, "end_day": end_min // DAY_MIN + 1,
                   "days": max(1, (end_min - start_min) // DAY_MIN + 1)},
        "memories": _mem_dicts(agent, top),
        "aftermath": aftermath,
        "aftermath_window": list(aftermath_window) if aftermath_window else None,
        "forbid_terms": [t for t in forbid_terms if t],
        "related_count": related_count,
        "relationships": {
            "most_interacted": [{"id": oid, "talks": n} for oid, n in most],
            "helped": helpers,
            "friction": friction,
        },
    }


def relationship_summary_lines(material: dict, world: "World") -> list[str]:
    """Plain-English lines for the prompt (pinyin names; the model stays in the
    English name space)."""
    rel = material["relationships"]
    name = lambda oid: oid.capitalize()   # noqa: E731
    lines = []
    if rel["most_interacted"]:
        lines.append("Spent the most time with: " + ", ".join(
            f"{name(x['id'])} ({x['talks']} talks)" for x in rel["most_interacted"]) + ".")
    if rel["helped"]:
        lines.append("Leaned on / trusted: " + ", ".join(name(x) for x in rel["helped"]) + ".")
    if rel["friction"]:
        lines.append("Some friction with: " + ", ".join(name(x) for x in rel["friction"]) + ".")
    return lines or ["Mostly kept to myself through it."]


# ---- the template floor ---------------------------------------------------------


def template_biography(agent: "Agent", chapter: Chapter, outcome: str) -> str:
    """Rule-layer stand-in for the closure reflection's line (first person, English,
    toned by outcome). Used when the model fails; never blocks closure."""
    what = chapter.goal or chapter.title
    what = what[:1].lower() + what[1:] if what else "that chapter"
    if outcome == "completed":
        return f"I set out to {what} -- and I did it. That part of my life is finished now."
    if outcome == "failed":
        return f"I tried to {what}, and it didn't work out. I'm learning to carry that."
    return f"I meant to {what}, but I chose to let it go. That was my call, and I stand by it."


def validate_closure_output(parsed: object, material: dict) -> dict | None:
    """Hold the reflection's JSON to the quality gate. Returns a clean dict
    {biography_line, emotional_residue, memory_refs} or None (-> template)."""
    if not isinstance(parsed, dict):
        return None
    line = str(parsed.get("biography_line") or "").strip()
    words = line.split()
    if len(line) < 20 or len(words) < 5 or len(line) > 400:
        return None
    if any(ord(ch) > 0x2E7F for ch in line):          # CJK / gibberish -> internal English only
        return None
    low = line.lower()
    if not any(t in low for t in ("i ", "i'", "my ", "me ", "myself")):   # must be first person
        return None
    # No invented figures: a biography is a near-permanent memory, so every number
    # in it must come from the material -- a listed memory or the true day span.
    # (The prompt also forbids it; this is the cheap mechanical backstop.)
    import re as _re
    given = list(material.get("memories", [])) + list(material.get("aftermath", []))
    allowed = set()
    for m in given:
        allowed.update(_re.findall(r"\d+", m.get("text", "")))
    win = material.get("window") or {}
    allowed.update(str(win[k]) for k in ("days", "start_day", "end_day") if k in win)
    if any(n not in allowed for n in _re.findall(r"\d+", line)):
        return None
    # Forbidden meanings for this closure (e.g. "asked" on a question never asked).
    for term in material.get("forbid_terms", []):
        if _re.search(rf"\b{_re.escape(term)}\b", line, _re.I):
            return None
    residue = str(parsed.get("emotional_residue") or "").strip().lower()
    if residue not in RESIDUES:
        residue = ""
    valid_ids = {m["id"]: m["text"] for m in given}
    refs = []
    for r in (parsed.get("memory_refs") or []):
        rid = str(r).strip()
        if rid in valid_ids:
            refs.append({"id": rid, "text": valid_ids[rid]})
    return {"biography_line": line, "emotional_residue": residue, "memory_refs": refs}


# ---- the atomic state change -----------------------------------------------------


def apply_closure(agent: "Agent", world: "World", outcome: str, biography_line: str,
                  residue: str, memory_refs: list[dict], now: int, *,
                  trigger: str = "manual", biography_source: str = "template",
                  coefficient: float = DOWNWEIGHT_COEFF) -> ChapterRecord | None:
    """Close the agent's pursuit chapter. Synchronous and atomic (no awaits): all
    five effects land together --
      1. a ``biography`` memory (high confidence, near-zero decay, low retrieval priority)
      2. every chapter-related memory/belief down-weighted by ``coefficient`` (never deleted)
      3. the chapter pushed into history; ``current_chapter`` becomes an interlude
      4. (the engine publishes ``chapter_closed`` from the returned record)
      5. a related landmark decoupled from its creator (stays a public world object)
    Returns the history record, or None when there is no pursuit to close."""
    ch = agent.chapter
    if ch is None or ch.chapter_type != "pursuit":
        return None
    if outcome not in OUTCOMES:
        outcome = "completed"
    residue = residue if residue in RESIDUES else RESIDUE_DEFAULT[outcome]
    day = now // DAY_MIN + 1

    # 2. down-weight what the chapter was about (memories + beliefs), never delete
    n_mem = 0
    for m in agent.memory.items:
        if m.minute <= now and is_related_memory(m, ch) and m.weight > coefficient:
            m.weight = coefficient
            n_mem += 1
    agent.memory.invalidate_weights()
    n_bel = 0
    for b in agent.semantic.beliefs:
        if is_related_belief(b, ch) and b.weight > coefficient:
            b.weight = coefficient
            n_bel += 1

    # 5. landmark decoupling: public object stays; the creator's active context lets go
    loc_id = ""
    if ch.related_landmark_id:
        for lid, loc in world.locations.items():
            for lm in loc.landmarks:
                if lm.get("id") == ch.related_landmark_id:
                    lm["decoupled"] = True
                    loc_id = lid
        _retire_landmark_routine(agent, loc_id)
    # the pursuit's goal is retired with the chapter (it never sat on profile.goals)
    agent.profile.goals = [g for g in agent.profile.goals
                           if goal_key(str(g.get("goal", ""))) != ch.related_goal_id]

    # 1. the biography memory -- tags carry the theme + the place, for surfacing
    tags = list(ch.theme)
    if loc_id:
        tags.append(f"loc:{loc_id}")
    if ch.related_landmark_id:
        tags.append(f"landmark:{ch.related_landmark_id}")
    bio = MemoryItem(minute=now, text=biography_line, importance=9, kind="biography",
                     source_chapter_id=ch.id, tags=tags)
    before = agent.memory.importance_since_reflection
    agent.memory.add(bio)
    agent.memory.importance_since_reflection = before   # closure never hastens a reflection call

    # 3. history + interlude
    until = day + interlude_days(residue, agent)
    record = ChapterRecord(
        chapter=ch.to_dict(), ended_on=day, outcome=outcome, biography_line=biography_line,
        emotional_residue=residue, memory_refs=list(memory_refs or []), trigger=trigger,
        biography_source=biography_source, downweighted_memories=n_mem, downweighted_beliefs=n_bel,
    )
    agent.chapter_history.append(record)
    agent.chapter = make_interlude(residue, day, until)
    return record


def _retire_landmark_routine(agent: "Agent", loc_id: str) -> None:
    """The creator's 'work at the landmark's place' slots become rest there -- the
    piece is done, the park is just the park again."""
    if not loc_id:
        return
    for table in (agent.routine.entries, agent.routine._weekend):
        for e in table:
            if e.action == "work" and e.location == loc_id:
                e.action = "rest"


def end_interlude(agent: "Agent", day: int) -> Chapter | None:
    """At a day boundary: an interlude past its ``until_day`` lapses into ordinary
    days. Returns the new chapter (for the ``chapter_started`` beat) or None.

    Wish generation is deliberately scheduled by SimulationEngine only after this
    synchronous transition, so a slow provider never blocks daily settlement."""
    ch = agent.chapter
    if ch is None or ch.chapter_type != "interlude" or day < ch.until_day:
        return None
    agent.chapter = make_ordinary(agent, day)
    return agent.chapter


def start_pursuit(agent: "Agent", chapter: Chapter) -> Chapter:
    """Install a pursuit chapter (seed / backfill). The goal lives on the chapter,
    not on profile.goals."""
    agent.profile.goals = [g for g in agent.profile.goals
                           if goal_key(str(g.get("goal", ""))) != chapter.related_goal_id]
    agent.chapter = chapter
    return chapter
