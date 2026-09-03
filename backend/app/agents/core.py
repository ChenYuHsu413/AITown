"""Agent core data: Profile (static), State (dynamic), Memory (episodic).

Phase 1 memory is a plain in-process list with keyword relevance --
deliberately no pgvector yet. The retrieval interface is already shaped
like the future vector version (query -> top-k), so swapping in
embeddings later won't touch callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Profile:
    id: str
    name: str
    age: int
    occupation: str
    personality: dict[str, float]           # big-five-ish, 0..1
    gender: str = ""                         # "male" | "female" -- steers dialogue pronouns only
    speech_style: str = ""                   # one-line "how they talk", injected into dialogue prompts
    romantic_inclination: float = 0.0        # 0..1 openness to romance (0 = opts out of the line)
    # Directional propensity for a romance by the other person's gender (0..1). A's
    # coefficient toward B = orientation_bias["same" if same gender else "other"].
    orientation_bias: dict = field(default_factory=lambda: {"same": 0.2, "other": 1.0})
    traits: list[str] = field(default_factory=list)
    goals: list[dict] = field(default_factory=list)  # {"goal": str, "priority": float}
    daily_wage: float = 0.0                 # flat daily income paid at each day boundary
    reflection_threshold: int = 25          # importance to accumulate before a Level-3 reflection fires
                                            # (raise it for quiet background characters -> fewer smart-tier calls)
    off_days: list[int] = field(default_factory=list)  # weekly rest days (0=Mon..6=Sun) beyond the weekend
                                            # table -- e.g. the postman's Sunday. Shop owners' days off come
                                            # from their shop's closed_days, not here.

    @property
    def extraversion(self) -> float:
        return self.personality.get("extraversion", 0.5)


@dataclass
class AgentState:
    location: str
    energy: int = 80                        # 0..100
    money: float = 100.0                    # wallet; spent on meals, topped up by wages/revenue
    mood: str = "neutral"
    current_action: str = "idle"
    busy_until: int = 0                     # sim minute; can't be interrupted before
    last_talk_minute: dict[str, int] = field(default_factory=dict)  # per-partner cooldown
    pending_concern: dict | None = None     # {"rumor_id", "told_by"} when a rumor about self must be reacted to
    seek_target: str = ""                   # agent_id being chased down to confront over a rumor
    seek_text: str = ""                     # the confrontation opener to use on arrival
    seek_rumor_id: str = ""                 # the rumor being confronted (moves with seek_target/seek_text)
    seek_tries: int = 0                     # chase attempts so far (give up after 2)
    avoid_location: str = ""                # a shop to shun after hearing a bad rumor about its owner
    meals_bought: int = 0                   # lifetime paid meals (throttles the "had a meal" memory)
    last_meal_slot: int = -1                # (day, eat-slot) already paid for -- stops double-charging
    seen_landmark_progress: dict[str, float] = field(default_factory=dict)  # landmark_id -> progress last noticed
    closed_reroute_notes: dict[str, int] = field(default_factory=dict)  # shop_id -> sim-day already noted "found it closed"
    # Life transitions (see agents/transitions.py). A decided change is staged here
    # and applied at the next daily settlement (clean day boundary); a 7-day cooldown
    # keeps lives from lurching. ``employer`` is the shop owner who pays this agent's
    # wage (shop staff) -- "" for the self-supporting.
    pending_transition: str = ""            # transition template id awaiting apply ("" = none)
    pending_transition_reason: str = ""     # the reflection's reason, kept for the chronicle detail
    last_transition_day: int = -100         # sim-day of the last applied transition (cooldown anchor)
    employer: str = ""                      # agent_id who pays this agent's daily wage ("" = none)
    # Relationship milestones: sim-day a stage-change was last recorded per partner,
    # so a pair crossing a threshold back and forth doesn't spam the chronicle.
    rel_stage_day: dict[str, int] = field(default_factory=dict)  # partner_id -> sim-day of last milestone
    # Romance bookkeeping (see romance.py). co-presence minutes drive the organic
    # "spark"; the rest are cooldowns/one-shots so the line doesn't thrash.
    copresence: dict[str, int] = field(default_factory=dict)     # partner_id -> minutes spent co-located
    ignite_day: dict[str, int] = field(default_factory=dict)     # partner_id -> sim-day of last spark nudge
    awkward_until: dict[str, int] = field(default_factory=dict)  # partner_id -> sim-day the post-rejection chill lifts
    pending_confession: str = ""            # partner_id this agent has resolved to confess to (next solo talk)
    last_confess_day: int = -100            # sim-day of the last confession attempt (cooldown anchor)
    # Social initiative (see decision.maybe_arrange_meetup): a standing appointment to
    # meet a friend at a place/time today. Both parties carry the mirror. Cleared when
    # kept or when the window lapses.
    pending_meetup: dict | None = None      # {"partner": id, "location": id, "minute": abs sim-min}
    last_meetup_day: int = -100             # sim-day this agent last INITIATED a meetup (per-person throttle)
    meetup_with_day: dict[str, int] = field(default_factory=dict)  # partner_id -> sim-day last met (per-pair throttle)


@dataclass
class MemoryItem:
    minute: int
    text: str
    importance: int = 1                     # 1..10
    kind: str = "observation"               # observation | conversation | reflection | rumor | secret | biography
    rumor_id: str = ""                      # set when this memory records a rumor
    secret_id: str = ""                     # set when this memory records a confided secret
    # Chapter closure (see agents/chapters.py): a closed chapter's memories keep
    # their text but their retrieval score is scaled by ``weight`` (default 1.0;
    # 0.3 once the chapter is over -- never deleted). A ``biography`` memory is the
    # chapter's one-line legacy: ``source_chapter_id`` traces it back to the
    # history record, ``tags`` (theme words + "loc:<id>") decide when it surfaces.
    weight: float = 1.0
    source_chapter_id: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class Belief:
    """A long-term impression distilled from repeated experience -- the semantic
    layer above episodic memory. One belief per subject (an agent_id, "self", or
    a location id). ``sentiment`` (-1..1) drives trust inertia; ``confidence``
    grows with reinforcement and decays when left unreinforced."""

    subject: str                            # agent_id | "self" | location_id
    text: str                               # one-line long-term impression (English)
    confidence: float = 0.3                 # 0..1
    sentiment: float = 0.0                  # -1 negative .. +1 positive
    formed_minute: int = 0
    last_reinforced_minute: int = 0
    source_count: int = 1                   # experiences backing this belief
    weight: float = 1.0                     # chapter-closure down-weight (gates prompt context; see chapters.py)


class SemanticMemory:
    """Every agent's small set of lasting impressions (max 8). Formation and
    reinforcement happen in the reflection flow; decay runs at each day boundary."""

    def __init__(self) -> None:
        self.beliefs: list[Belief] = []

    def about(self, subject: str) -> "Belief | None":
        for b in self.beliefs:
            if b.subject == subject:
                return b
        return None

    def decay(self, amount: float = 0.02, floor: float = 0.15) -> None:
        """Erode every belief a little (call once per sim-day); drop the faded."""
        for b in self.beliefs:
            b.confidence -= amount
        self.beliefs = [b for b in self.beliefs if b.confidence >= floor]

    def _prune(self, cap: int = 8) -> None:
        """Keep at most ``cap`` beliefs -- evict the least-confident."""
        if len(self.beliefs) > cap:
            self.beliefs.sort(key=lambda b: b.confidence, reverse=True)
            del self.beliefs[cap:]


class EpisodicMemory:
    """Append-only list + naive keyword top-k retrieval.

    Two optional hooks wired by the persistence layer:
      - on_add: called for every new memory (mirror to DB)
      - vector_search: async (query, k) -> list[str]; when set,
        retrieve_async delegates to pgvector instead of keywords.
    """

    def __init__(self) -> None:
        self.items: list[MemoryItem] = []
        self.importance_since_reflection: int = 0
        self.on_add = None            # Callable[[MemoryItem], None] | None
        self.vector_search = None     # async (query, k) -> list[str] | None
        # Themes of resolved worries: reflection/rumor memories from BEFORE a worry
        # was settled get their retrieval weight halved, so old anxiety fades from
        # the narrative instead of dominating it. Each: (subject, keyword frozenset,
        # before_minute). A memory matches when it names the subject, or shares two
        # distinctive theme words -- and only if it predates the resolution.
        self.suppressed: list[tuple[str, frozenset, int]] = []

    def suppress_theme(self, subject: str, keywords, before_minute: int) -> None:
        kws = frozenset(w.lower() for w in keywords if w)
        subj = (subject or "").lower()
        if subj or kws:
            self.suppressed.append((subj, kws, before_minute))

    def penalty(self, text: str, kind: str, minute: int) -> float:
        """0.5 for a reflection/rumor memory that predates a resolved worry and is
        about it (names the subject or shares >=2 theme words); 1.0 otherwise."""
        if not self.suppressed or kind not in ("reflection", "rumor"):
            return 1.0
        words = {w.strip(".,;:!?'\"").lower() for w in text.split()}
        for subj, kws, before in self.suppressed:
            if minute < before and ((subj and subj in words) or len(kws & words) >= 2):
                return 0.5
        return 1.0

    def add(self, item: MemoryItem) -> None:
        self.items.append(item)
        self.importance_since_reflection += item.importance
        if item.weight != 1.0:
            self._weights = None
        if self.on_add is not None:
            self.on_add(item)

    # ---- chapter-closure weights (see agents/chapters.py) -------------------
    # Down-weighted memories keep their text; only their retrieval score shrinks.
    # The pgvector retriever re-ranks by these same in-memory weights (looked up
    # by text), so the snapshot is the single source of truth for them.
    _weights: dict[str, float] | None = None

    def invalidate_weights(self) -> None:
        self._weights = None

    def weight_of(self, text: str) -> float:
        if self._weights is None:
            self._weights = {m.text: m.weight for m in self.items if m.weight != 1.0}
        return self._weights.get(text, 1.0)

    @property
    def has_downweights(self) -> bool:
        self.weight_of("")               # build the lookup if needed
        return bool(self._weights)

    # ---- biography surfacing -------------------------------------------------
    # A biography memory never enters the ordinary top-k. It surfaces only when
    # (1) the query shares >= BIOGRAPHY_TOPIC_MIN theme words with it (the topic is
    # explicitly on the table), or (2) the agent is at the place it is tagged with.
    BIOGRAPHY_TOPIC_MIN = 2

    def biography_hits(self, query: str, location: str = "") -> list[str]:
        q_words = {w.strip(".,;:!?'\"()-").lower() for w in query.split()}
        out: list[str] = []
        for m in self.items:
            if m.kind != "biography":
                continue
            if "private:wish" in m.tags:
                continue
            theme = {t for t in m.tags if ":" not in t}
            by_topic = len(q_words & theme) >= self.BIOGRAPHY_TOPIC_MIN
            by_place = bool(location) and f"loc:{location}" in m.tags
            if by_topic or by_place:
                out.append(m.text)
        return out

    async def retrieve_async(self, query: str, k: int = 5, location: str = "",
                             topic: str = "") -> list[str]:
        """``location`` / ``topic`` only feed biography surfacing (place match; what
        the conversation is explicitly about) -- the ordinary top-k query is unchanged."""
        bio = self.biography_hits(f"{query} {topic}".strip(), location)
        if self.vector_search is not None:
            try:
                got = await self.vector_search(query, k)
                return self._merge_bio(bio, got, k)
            except Exception:
                pass  # DB hiccup -> keyword fallback below
        return self._merge_bio(bio, self._rank(query, k), k)

    @staticmethod
    def _merge_bio(bio: list[str], rest: list[str], k: int) -> list[str]:
        if not bio:
            return rest
        seen = set(bio)
        return (bio + [t for t in rest if t not in seen])[:max(k, len(bio))]

    def retrieve(self, query: str, k: int = 5, location: str = "") -> list[str]:
        """Keyword top-k (see ``_rank``) with biography entries prepended only when
        ``biography_hits`` says the topic/place is live. Same contract as the
        vector search: (query, k) -> list[str]."""
        return self._merge_bio(self.biography_hits(query, location), self._rank(query, k), k)

    def _rank(self, query: str, k: int) -> list[str]:
        """Score = keyword overlap + importance + recency, scaled by the chapter
        weight. Biography entries never rank here."""
        q_words = {w for w in query.lower().split() if len(w) > 2}
        scored: list[tuple[float, MemoryItem]] = []
        latest = self.items[-1].minute if self.items else 0
        for m in self.items:
            if m.kind == "biography":
                continue
            overlap = len(q_words & set(m.text.lower().split()))
            recency = 1.0 - min((latest - m.minute) / (24 * 60), 1.0)
            score = ((overlap * 2.0 + m.importance * 0.3 + recency)
                     * self.penalty(m.text, m.kind, m.minute) * m.weight)
            if score > 0:
                scored.append((score, m))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [m.text for _, m in scored[:k]]

    def today(self, day_start_minute: int) -> list[str]:
        return [m.text for m in self.items if m.minute >= day_start_minute]
