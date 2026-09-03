"""Self-grown wishes: strict data, rule-only progress, rare generation."""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from dataclasses import asdict, dataclass, field

from .chapters import DAY_MIN, ChapterRecord, make_interlude, make_pursuit, interlude_days, memory_id
from .core import MemoryItem

SCALES = ("small", "major")
STATUSES = ("active", "completed", "failed", "abandoned")
REQUIREMENT_KINDS = ("action_count", "location_visits", "talk_count", "friendship", "trust",
                     "money", "money_gain", "event_count")
ALLOWED_ACTIONS = ("sleep", "eat", "work", "rest", "idle", "arrive", "talk_start", "repaired")
ALLOWED_EVENTS = ("day_summary", "repaired", "meetup_arranged", "met_up", "transition")

MATERIAL_MIN_COUNT = 3
MATERIAL_MIN_IMPORTANCE = 10
MATERIAL_MAX = 8
GENERATION_RETRY_MIN_DAYS = 3
GENERATION_RETRY_MAX_DAYS = 7
GENERATION_BASE_COOLDOWN_DAYS = 7
MAJOR_SOFT_TARGET = 3
MAJOR_THRESHOLD_STEP = 2
MIN_WISH_DAYS = 2
MAX_WISH_DAYS = 120
PROGRESS_MEMORY_EVERY = 3
COUNTED_EVENT_KEYS_MAX = 256
GENERATION_ROLLING_DAYS = 14
ABANDON_MIN_DAYS = 5
ABANDON_STALE_DAYS = 3
_FRUSTRATION_WORDS = ("failed", "couldn't", "cannot", "unable", "stuck", "setback",
                      "frustrat", "blocked", "avoided", "no progress", "gave up")


@dataclass
class Requirement:
    kind: str
    threshold: float
    current: float = 0.0
    unit: str = "count"
    completed: bool = False
    target: str = ""
    baseline: float = 0.0

    @classmethod
    def from_dict(cls, raw: dict) -> "Requirement | None":
        if not isinstance(raw, dict) or set(raw) - {
            "kind", "threshold", "current", "unit", "completed", "target", "baseline"
        }:
            return None
        try:
            if any(isinstance(raw.get(k, 0), bool) or not isinstance(raw.get(k, 0), (int, float))
                   for k in ("threshold", "current", "baseline")):
                return None
            r = cls(kind=str(raw["kind"]), threshold=float(raw["threshold"]),
                    current=float(raw.get("current", 0)), unit=str(raw.get("unit", "count")),
                    target=str(raw.get("target", "")), baseline=float(raw.get("baseline", 0)))
            if (r.kind not in REQUIREMENT_KINDS or not _finite_positive(r.threshold)
                    or not all(math.isfinite(x) for x in (r.current, r.baseline))
                    or not isinstance(raw.get("target", ""), str)
                    or not isinstance(raw.get("unit", "count"), str)):
                return None
            r.completed = r.current >= r.threshold
            return r
        except (KeyError, TypeError, ValueError, OverflowError):
            return None


@dataclass
class Wish:
    id: str
    owner_id: str
    title: str
    statement: str
    motivation: str
    scale: str
    status: str
    created_day: int
    ended_day: int = 0
    source_memory_refs: list[dict] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)
    failure_conditions: list[dict] = field(default_factory=list)
    progress: float = 0.0
    last_progress_day: int = 0
    secret_id: str = ""
    related_chapter_id: str = ""
    outcome_reason: str = ""
    counted_event_keys: list[str] = field(default_factory=list)
    progress_marks: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Wish | None":
        try:
            if not isinstance(raw, dict) or not isinstance(raw.get("requirements"), list):
                return None
            string_fields = ("id", "owner_id", "title", "statement", "scale", "status",
                             "secret_id", "related_chapter_id")
            if any(k in raw and not isinstance(raw[k], str) for k in string_fields):
                return None
            int_fields = ("created_day", "ended_day", "last_progress_day", "progress_marks")
            if any(k in raw and (not isinstance(raw[k], int) or isinstance(raw[k], bool))
                   for k in int_fields):
                return None
            reqs = [Requirement.from_dict(r) for r in raw["requirements"]]
            if not reqs or any(r is None for r in reqs):
                return None
            if len({(r.kind, r.target) for r in reqs}) != len(reqs):
                return None
            refs = raw.get("source_memory_refs", [])
            if not isinstance(refs, list) or any(not isinstance(x, dict) for x in refs):
                return None
            safe_refs = []
            for ref in refs:
                if not isinstance(ref.get("id", ""), str) or not isinstance(ref.get("text", ""), str):
                    return None
                safe_refs.append({"id": ref["id"], "text": ref["text"]})
            failure = _validate_failure_conditions(raw.get("failure_conditions", []))
            if failure is None:
                return None
            progress = raw.get("progress", 0.0)
            if (isinstance(progress, bool) or not isinstance(progress, (int, float))
                    or not math.isfinite(progress) or not 0 <= progress <= 1):
                return None
            w = cls(id=str(raw["id"]), owner_id=str(raw["owner_id"]), title=str(raw["title"]),
                    statement=str(raw["statement"]), motivation=str(raw.get("motivation", "")),
                    scale=str(raw["scale"]), status=str(raw.get("status", "active")),
                    created_day=int(raw["created_day"]), requirements=reqs,
                    ended_day=int(raw.get("ended_day", 0)), source_memory_refs=safe_refs,
                    failure_conditions=failure, progress=float(progress),
                    last_progress_day=int(raw.get("last_progress_day", 0)),
                    secret_id=str(raw.get("secret_id", "")),
                    related_chapter_id=str(raw.get("related_chapter_id", "")),
                    outcome_reason=str(raw.get("outcome_reason", "")),
                    progress_marks=int(raw.get("progress_marks", 0)))
            keys = raw.get("counted_event_keys", [])
            if (not isinstance(keys, list) or any(not isinstance(x, str) for x in keys)
                    or len(keys) > COUNTED_EVENT_KEYS_MAX):
                return None
            w.counted_event_keys = keys
            if (w.scale not in SCALES or w.status not in STATUSES
                    or not all((w.id.strip(), w.owner_id.strip(), w.title.strip(), w.statement.strip()))
                    or w.created_day < 1 or w.ended_day < 0
                    or (w.status == "active" and w.ended_day != 0)
                    or (w.status != "active" and w.ended_day < w.created_day)
                    or w.last_progress_day < 0 or w.progress_marks < 0):
                return None
            w.progress = sum(min(1.0, r.current / r.threshold) for r in reqs) / len(reqs)
            return w
        except (KeyError, TypeError, ValueError):
            return None


def active_wish(agent, scale: str | None = None) -> Wish | None:
    return next((w for w in agent.wishes if w.status == "active" and (scale is None or w.scale == scale)), None)


def active_wish_for_chapter(agent, chapter_id: str) -> Wish | None:
    return next((w for w in agent.wishes if w.scale == "major" and w.status == "active"
                 and w.related_chapter_id == chapter_id), None)


def closed_record_for_wish(agent, wish: Wish) -> ChapterRecord | None:
    return next((r for r in reversed(agent.chapter_history)
                 if str(r.chapter.get("id", "")) == wish.related_chapter_id
                 and str(r.chapter.get("related_goal_id", "")) == wish.id), None)


def reconcile(agent, secrets, day: int) -> list[Wish]:
    """Repair interrupted Wish/Chapter pairs without LLM calls or public events."""
    changed: list[Wish] = []
    for wish in agent.wishes:
        if wish.scale != "major":
            continue
        record = closed_record_for_wish(agent, wish)
        current_match = (agent.chapter is not None and agent.chapter.chapter_type == "pursuit"
                         and agent.chapter.id == wish.related_chapter_id
                         and agent.chapter.related_goal_id == wish.id)
        if wish.status == "active" and record is not None:
            if finish(wish, secrets, record.outcome, record.ended_on,
                      "reconciled from closed chapter"):
                changed.append(wish)
        elif wish.status == "active" and not current_match:
            if finish(wish, secrets, "failed", day,
                      "reconciliation: missing or invalid linked pursuit chapter"):
                changed.append(wish)
        elif wish.status != "active" and current_match:
            # The wish outcome is persisted truth; close the dangling chapter without
            # inventing biography or publishing a synthetic chronicle beat.
            old = agent.chapter
            agent.chapter_history.append(ChapterRecord(
                chapter=old.to_dict(), ended_on=max(day, wish.ended_day), outcome=wish.status,
                biography_line="", emotional_residue="", trigger="reconciliation",
                biography_source="reconciliation"))
            residue = "fulfilled" if wish.status == "completed" else (
                "unmoored" if wish.status == "failed" else "relieved")
            agent.chapter = make_interlude(residue, day, day + interlude_days(residue, agent))
            wish.outcome_reason = (wish.outcome_reason + "; " if wish.outcome_reason else "") + \
                                  "reconciliation: dangling pursuit repaired"
            changed.append(wish)
    return changed


def material_start_minute(agent, day: int) -> int:
    if agent.chapter_history:
        return agent.chapter_history[-1].ended_on * DAY_MIN
    return max(0, (day - GENERATION_ROLLING_DAYS - 1) * DAY_MIN)


def eligible_material(agent, since_minute: int = -1, day: int | None = None) -> list[dict]:
    floor = since_minute
    if day is not None:
        floor = max(floor, material_start_minute(agent, day) - 1)
    good = [m for m in agent.memory.items if m.minute > floor and m.kind != "biography"
            and m.importance >= 3 and len(m.text.strip()) >= 24
            and not re.fullmatch(r"(?i)(ok|fine|nothing|the usual)[.! ]*", m.text.strip())]
    good.sort(key=lambda m: (m.importance, m.minute), reverse=True)
    return [{"id": memory_id(agent.id, m), "text": m.text, "minute": m.minute,
             "importance": m.importance} for m in good[:MATERIAL_MAX]]


def generation_threshold(active_major_count: int) -> int:
    return MATERIAL_MIN_IMPORTANCE + max(0, active_major_count - MAJOR_SOFT_TARGET) * MAJOR_THRESHOLD_STEP


def generation_eligible(agent, day: int, active_major_count: int) -> tuple[bool, list[dict]]:
    if active_wish(agent, "major") or agent.chapter is not None and agent.chapter.chapter_type != "ordinary":
        return False, []
    if day < agent.wish_next_attempt_day:
        return False, []
    material = eligible_material(agent, agent.wish_last_attempt_minute, day)
    if len(material) < MATERIAL_MIN_COUNT:
        return False, material
    if sum(m["importance"] for m in material) < generation_threshold(active_major_count):
        return False, material
    return True, material


def retry_day(agent_id: str, day: int) -> int:
    seed = int(hashlib.sha256(f"wish-retry|{agent_id}|{day}".encode()).hexdigest()[:8], 16)
    span = GENERATION_RETRY_MAX_DAYS - GENERATION_RETRY_MIN_DAYS + 1
    return day + GENERATION_RETRY_MIN_DAYS + seed % span


def generation_slot(agent_id: str, day: int) -> bool:
    """Deterministically spread ten residents across days (roughly 1/7 per day)."""
    n = int(hashlib.sha256(f"wish-slot|{agent_id}".encode()).hexdigest()[:8], 16)
    return day % GENERATION_BASE_COOLDOWN_DAYS == n % GENERATION_BASE_COOLDOWN_DAYS


def material_for_prompt(agent, world, memories: list[dict], secrets) -> dict:
    last = agent.chapter_history[-1].to_dict() if agent.chapter_history else None
    beliefs = [{"subject": b.subject, "text": b.text, "confidence": b.confidence}
               for b in sorted(agent.semantic.beliefs, key=lambda b: b.confidence, reverse=True)[:5]]
    rels = [{"agent_id": oid, "friendship": round(r.friendship), "trust": round(r.trust)}
            for oid, r in sorted(agent.relationships.items(), key=lambda x: x[1].friendship, reverse=True)[:5]]
    safe_secrets = [{"id": s.id, "text": s.text} for s in secrets.active_secrets_of(agent.id)
                    if getattr(s, "source_kind", "") != "wish"]
    return {"memories": memories, "last_chapter": last, "beliefs": beliefs, "relationships": rels,
            "money": agent.state.money, "occupation": agent.profile.occupation,
            "personality": dict(agent.profile.personality), "secrets": safe_secrets,
            "agents": list(world.agents), "locations": list(world.locations),
            "actions": list(ALLOWED_ACTIONS), "events": list(ALLOWED_EVENTS)}


def _finite_positive(x, lo=1, hi=10000) -> bool:
    return (not isinstance(x, bool) and isinstance(x, (int, float))
            and math.isfinite(float(x)) and lo <= float(x) <= hi)


def _validate_failure_conditions(raw: object) -> list[dict] | None:
    if not isinstance(raw, list):
        return None
    out = []
    for fc in raw:
        if (not isinstance(fc, dict) or set(fc) != {"kind", "days"}
                or fc.get("kind") != "deadline"
                or not isinstance(fc.get("days"), int) or isinstance(fc.get("days"), bool)
                or not _finite_positive(fc.get("days"), MIN_WISH_DAYS, MAX_WISH_DAYS)):
            return None
        out.append({"kind": "deadline", "days": int(fc["days"])})
    return out


def validate_generation(raw: object, agent, world, material: dict) -> dict | None:
    if not isinstance(raw, dict) or raw.get("no_wish") is True:
        return None
    allowed = {"title", "statement", "motivation", "scale", "source_memory_refs",
               "requirements", "failure_conditions"}
    if set(raw) - allowed:
        return None
    title, statement = str(raw.get("title", "")).strip(), str(raw.get("statement", "")).strip()
    scale = str(raw.get("scale", ""))
    if not title or len(statement) < 12 or scale not in SCALES:
        return None
    provided = {m["id"]: m for m in material["memories"]}
    refs = []
    for rid in raw.get("source_memory_refs", []):
        if str(rid) not in provided:
            return None
        refs.append({"id": str(rid), "text": provided[str(rid)]["text"]})
    if not refs:
        return None
    reqs, seen = [], set()
    for rr in raw.get("requirements", []):
        if not isinstance(rr, dict) or set(rr) - {"kind", "target", "threshold", "unit"}:
            return None
        r = Requirement.from_dict(rr)
        if r is None or r.kind not in REQUIREMENT_KINDS or not _finite_positive(r.threshold):
            return None
        if r.kind in ("talk_count", "friendship", "trust") and r.target not in world.agents:
            return None
        if r.kind == "location_visits" and r.target not in world.locations:
            return None
        if r.kind == "action_count" and r.target not in ALLOWED_ACTIONS:
            return None
        if r.kind == "event_count" and r.target not in ALLOWED_EVENTS:
            return None
        if r.kind in ("friendship", "trust") and r.threshold > 100:
            return None
        key = (r.kind, r.target)
        if key in seen:
            return None
        seen.add(key)
        if r.kind in ("friendship", "trust"):
            r.current = r.baseline = getattr(agent.rel(r.target), r.kind)
        elif r.kind == "money":
            r.current = r.baseline = agent.state.money
        elif r.kind == "money_gain":
            r.baseline = agent.state.money
            r.current = 0
            r.unit = "currency"
        else:
            r.current = r.baseline = 0
        if r.current >= r.threshold:
            return None
        reqs.append(r)
    if not reqs:
        return None
    effort = sum(r.threshold - r.current for r in reqs)
    if scale == "major" and effort < 2:
        return None
    if scale == "small" and effort > 12:
        return None
    failure = _validate_failure_conditions(raw.get("failure_conditions", []))
    if failure is None:
        return None
    return {"title": title[:120], "statement": statement[:400],
            "motivation": str(raw.get("motivation", ""))[:500], "scale": scale,
            "source_memory_refs": refs, "requirements": reqs, "failure_conditions": failure}


def install(agent, secrets, clean: dict, day: int) -> Wish | None:
    if active_wish(agent, clean["scale"]):
        return None
    if (clean["scale"] == "major" and agent.chapter is not None
            and agent.chapter.chapter_type != "ordinary"):
        return None
    w = Wish(id=uuid.uuid4().hex[:8], owner_id=agent.id, title=clean["title"],
             statement=clean["statement"], motivation=clean["motivation"], scale=clean["scale"],
             status="active", created_day=day, source_memory_refs=clean["source_memory_refs"],
             requirements=clean["requirements"], failure_conditions=clean["failure_conditions"],
             last_progress_day=day)
    s = secrets.add(agent.id, w.statement, 0.8, (day - 1) * DAY_MIN,
                    source_kind="wish", source_id=w.id, social_enabled=False)
    w.secret_id = s.id
    if w.scale == "major":
        ch = make_pursuit(w.statement, w.title, "I am focused on a private matter right now.", day)
        ch.related_goal_id = w.id
        w.related_chapter_id = ch.id
        agent.chapter = ch
    agent.wishes.append(w)
    return w


def _event_key(ev) -> str:
    return hashlib.sha1(f"{ev.minute}|{ev.verb}|{ev.actor}|{ev.target}|{ev.location}|{ev.text}".encode()).hexdigest()[:12]


def update_from_event(wish: Wish, agent, ev, day: int) -> bool:
    if wish.status != "active": return False
    key = _event_key(ev)
    if key in wish.counted_event_keys: return False
    changed = False
    for r in wish.requirements:
        hit = ((r.kind == "action_count" and ev.actor == agent.id and ev.verb == r.target)
               or (r.kind == "location_visits" and ev.actor == agent.id and ev.verb == "arrive" and ev.location == r.target)
               or (r.kind == "talk_count" and ev.verb == "talk_start"
                   and {ev.actor, ev.target} == {agent.id, r.target})
               or (r.kind == "event_count" and ev.verb == r.target and ev.actor == agent.id))
        if hit and not r.completed:
            r.current += 1; r.completed = r.current >= r.threshold; changed = True
    if changed:
        wish.counted_event_keys.append(key)
        del wish.counted_event_keys[:-COUNTED_EVENT_KEYS_MAX]
        wish.last_progress_day = day
        wish.progress = sum(min(1, r.current / r.threshold) for r in wish.requirements) / max(1, len(wish.requirements))
    return changed


def update_state(wish: Wish, agent, day: int) -> bool:
    changed = False
    for r in wish.requirements:
        old = r.current
        if r.kind == "friendship" and r.target in agent.relationships: r.current = agent.rel(r.target).friendship
        elif r.kind == "trust" and r.target in agent.relationships: r.current = agent.rel(r.target).trust
        elif r.kind == "money": r.current = agent.state.money
        elif r.kind == "money_gain": r.current = max(0, agent.state.money - r.baseline)
        r.completed = r.current >= r.threshold
        changed |= r.current != old
    if changed:
        wish.last_progress_day = day
        wish.progress = sum(min(1, r.current/r.threshold) for r in wish.requirements)/max(1,len(wish.requirements))
    return changed


def outcome(wish: Wish, day: int) -> tuple[str, str] | None:
    if wish.requirements and all(r.completed for r in wish.requirements):
        return "completed", "all observable requirements were met"
    for fc in wish.failure_conditions:
        if fc["kind"] == "deadline" and day >= wish.created_day + fc["days"]:
            return "failed", f"deadline passed after {fc['days']} days"
    return None


def finish(wish: Wish, secrets, status: str, day: int, reason: str) -> bool:
    if wish.status != "active" or status not in STATUSES[1:]: return False
    wish.status, wish.ended_day, wish.outcome_reason = status, day, reason
    secrets.resolve(wish.secret_id, (day - 1) * DAY_MIN, reason, from_wish=True)
    return True


def abandonment_score(agent, wish: Wish, day: int, refs: list[MemoryItem]) -> float:
    stale = max(0, day - wish.last_progress_day)
    frustration = min(0.45, len(refs) * 0.12) + min(0.25, stale * 0.03)
    recent_penalty = max(0, ABANDON_STALE_DAYS - stale) * 0.12
    resistance = (agent.profile.personality.get("conscientiousness", 0.5) * 0.75
                  + wish.progress * 0.65 + recent_penalty)
    if wish.scale == "small": frustration += 0.15
    return frustration - resistance


def validate_abandon(agent, wish: Wish, day: int, memory_refs: list[str],
                     allowed_refs: set[str] | None = None) -> tuple[bool, list[dict]]:
    if wish.status != "active" or day - wish.created_day < ABANDON_MIN_DAYS or day == wish.last_progress_day:
        return False, []
    by_id = {memory_id(agent.id, m): m for m in agent.memory.items
             if m.minute >= (wish.created_day - 1) * DAY_MIN and m.importance >= 4}
    refs = [by_id[r] for r in memory_refs if r in by_id and (allowed_refs is None or r in allowed_refs)]
    refs = [m for m in refs if any(w in m.text.lower() for w in _FRUSTRATION_WORDS)]
    if not refs: return False, []
    return abandonment_score(agent, wish, day, refs) >= 0, [{"id": memory_id(agent.id,m),"text":m.text} for m in refs]
