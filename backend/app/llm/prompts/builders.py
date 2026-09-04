"""Context Builder.

The single biggest token sink in agent sims is resending an agent's entire
life every call. These builders assemble a *lean* context instead:

    static character summary  (~1-2 lines)
    current state             (~1 line)
    top-k relevant memories   (~5 lines)
    observation               (~1 line)
    instruction               (~2 lines)
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid circular imports at runtime
    from ...agents.agent import Agent


def lang_code() -> str:
    """Current generation language (AI_TOWN_LANG), normalized lower-case. 'en' default."""
    return os.environ.get("AI_TOWN_LANG", "en").strip().lower() or "en"


def lang_is_zh() -> bool:
    return lang_code().startswith("zh")


def _lang_directive() -> str:
    """Localize only the *free-text* the reader sees (dialogue lines, insights,
    retold gossip). JSON keys and action/enum values MUST stay English -- the
    decision layer parses those literally. Off (empty) unless AI_TOWN_LANG asks
    for a non-English language, so mock/English runs are byte-for-byte unchanged.
    """
    lang = os.environ.get("AI_TOWN_LANG", "en").strip().lower()
    if lang in ("", "en", "en-us", "english"):
        return ""
    if lang.startswith("zh"):
        name = "Traditional Chinese (zh-TW)" if lang not in ("zh-cn", "zh-hans") else "Simplified Chinese"
        return (
            f" Write every human-readable text value (e.g. \"text\", \"insights\") "
            f"in natural {name}. Keep all JSON keys and any action/enum values in English."
        )
    return (
        f" Write every human-readable text value in natural '{lang}'. "
        f"Keep all JSON keys and any action/enum values in English."
    )


def time_of_day(minute: int) -> str:
    h = (minute % 1440) // 60
    return ("late night" if h < 6 else "morning" if h < 11 else "midday" if h < 14
            else "afternoon" if h < 18 else "evening" if h < 22 else "night")


# ---- name roster (anti-hallucination) --------------------------------------
# The town's full cast, set once at startup. Every free-text prompt lists it and
# forbids inventing other names -- so a weak model can't turn "Lengyue" into a
# hallucinated "楊鈺瑩". Stored as (english_pinyin, zh, gender) triples; gender is
# "" when unknown and only steers dialogue pronouns.
_ROSTER: list[tuple[str, str, str]] = []


def set_roster(pairs: list) -> None:
    """Accepts (en, zh) or (en, zh, gender); missing gender is stored as ''."""
    global _ROSTER
    _ROSTER = [(p[0], p[1], p[2] if len(p) > 2 else "") for p in pairs]


# The town's places, (english_name, zh_name), set once at startup alongside the
# roster. Used only to steer zh dialogue toward the Chinese place names.
_PLACES: list[tuple[str, str]] = []


def set_places(pairs: list[tuple[str, str]]) -> None:
    global _PLACES
    _PLACES = [(en, zh) for en, zh in pairs if en and zh]


def roster_directive(english_only: bool = False) -> str:
    if not _ROSTER:
        return ""
    names = ", ".join(f"{en} ({zh})" if zh and zh != en else en for en, zh, _g in _ROSTER)
    out = (f" The only people who exist in this town are: {names}. "
           f"Refer to a person by exactly one of these names and never invent any other name.")
    if english_only:
        out += " Always use the English (first shown) form of each name."
    return out


def roster_gender_directive(english_only: bool = False) -> str:
    """List each resident's fixed gender so the model uses the right pronouns -- a
    mis-gendered 'he'/'she' written into a memory/belief is stored forever (the sim
    can only fix pronouns at the zh display layer, not in the English source). Empty
    when no genders are set (older callers), so English/mock runs are unaffected.

    ``english_only`` picks the English (pinyin) name form, to match a prompt that
    generates English text ('Xixi is male' -> the model writes 'Xixi ... he'); the
    default uses the zh name, for zh-output prompts (dialogue, translate)."""
    genders = [(en, zh, g) for en, zh, g in _ROSTER if g]
    if not genders:
        return ""
    parts = ", ".join(f"{(en if english_only else (zh or en))} is {g}" for en, zh, g in genders)
    return (f" Each resident has a fixed gender -- use the matching pronouns for that "
            f"person throughout (he/him/his for male, she/her/hers for female; 他/她) and "
            f"never mis-gender anyone: {parts}.")


def roster_pairs() -> list[tuple[str, str]]:
    return [(en, zh) for en, zh, _g in _ROSTER]


def roster_genders() -> list[tuple[str, str, str]]:
    """(english_name, zh_name, gender) for every resident whose gender is known."""
    return [(en, zh, g) for en, zh, g in _ROSTER if g]


def gender_of(name: str) -> str:
    """'male' / 'female' / '' -- accepts either name form, case-insensitively."""
    n = (name or "").strip().lower()
    for en, zh, g in _ROSTER:
        if n in (en.lower(), (zh or "").lower()):
            return g
    return ""


# ---- gate counters ----------------------------------------------------------
# Both pronoun gates live in different modules (the translation one in the server,
# the generation one in the decision layer) but report here, so /api/usage has a
# single place to read them from. Cheap: two integers.
# ``roster_missing`` should stay 0 forever: a gate that needs the roster now raises
# instead of passing, so a non-zero value means something ran a gate bare and
# swallowed the exception.
GATE_REJECTS: dict[str, int] = {
    "translate_person": 0, "translate_gender": 0, "generation_gender": 0, "roster_missing": 0,
    # The three gates a grown wish must survive (phase 2b, see agents/wishes.py).
    "wish_feasibility": 0, "wish_deviation": 0, "wish_novelty": 0,
}


def note_gate_reject(kind: str) -> None:
    GATE_REJECTS[kind] = GATE_REJECTS.get(kind, 0) + 1


class RosterNotLoadedError(RuntimeError):
    """A gender check was asked for before the cast was published.

    The roster is set once, from the world, in ``SimulationEngine.__init__``. Without
    it ``roster_genders()`` is empty, every "is this resident male or female?" question
    answers "unknown", and a gender gate would wave through exactly the text it exists
    to catch. Silence is the dangerous failure here, so the gates raise this instead --
    a maintenance script that forgot to build an engine gets told, loudly, rather than
    reporting a clean bill of health it never actually checked."""


def roster_loaded() -> bool:
    return bool(roster_genders())


def require_roster(where: str = "") -> None:
    """Fail loudly (and leave a trace) when a roster-dependent check runs bare."""
    if roster_loaded():
        return
    note_gate_reject("roster_missing")
    raise RosterNotLoadedError(
        f"the name/gender roster is empty{f' ({where})' if where else ''} -- a gender check "
        f"cannot run and must not silently pass. Build the world first: "
        f"SimulationEngine(World(build_locations(), build_agents()), DecisionEngine(router)) "
        f"publishes it via builders.set_roster().")


def dialogue_locale_directive() -> str:
    """zh runs only: tell the model to speak people's and places' Chinese names,
    never the English/pinyin sign-name form (a weak model otherwise copies
    "Hearth Bakery"/"Ange" straight into the spoken line). Empty in English, so
    en/mock runs are byte-for-byte unchanged. The display layer substitutes these
    deterministically regardless, so this is a soft steer, not the guarantee."""
    if not lang_is_zh():
        return ""
    names = "; ".join(f"{en}={zh}" for en, zh, _g in _ROSTER if zh and zh != en)
    places = "; ".join(f"{en}={zh}" for en, zh in _PLACES if zh and zh != en)
    parts = []
    if names:
        parts.append(f"people ({names})")
    if places:
        parts.append(f"places ({places})")
    if not parts:
        return ""
    return (" When a spoken line names a person or place, use its Chinese name, "
            "never the English/pinyin form: " + "; ".join(parts) + ".")


def character_card(agent: "Agent", name: str | None = None, speech: bool = False) -> str:
    """Self-description = the fixed personality (traits, never changes) + the current
    life chapter's narrative ("where I am right now"). A pursuit's goal lives on the
    chapter, so a finished matter drops out of the card the moment it closes. Standing
    aims (profile.goals) only show during ordinary days -- an interlude or a pursuit
    is what the character is about right then."""
    from ...agents import chapters as chapters_mod
    p = agent.profile
    traits = ", ".join(p.traits)
    card = f"{name or p.name}, {p.age}, {p.occupation}. Traits: {traits}. {chapters_mod.narrative(agent)}"
    if chapters_mod.chapter_type(agent) == "ordinary" and p.goals:
        card += f" Standing aim: {p.goals[0]['goal']}."
    # Speech style is only worth its tokens where the reader hears the voice --
    # i.e. dialogue; should_talk/decision/reflection leave it off.
    if speech and p.speech_style:
        card += f" Speech style: {p.speech_style}"
    return card


def state_line(agent: "Agent") -> str:
    s = agent.state
    return f"Now at {s.location}, mood {s.mood}, energy {s.energy}/100, doing '{s.current_action}'."


def memories_block(memories: list[str]) -> str:
    if not memories:
        return "No relevant memories."
    return "Relevant memories:\n" + "\n".join(f"- {m}" for m in memories)


def should_talk_prompt(agent: "Agent", other_name: str, memories: list[str]) -> list[dict]:
    return [
        {
            "role": "system",
            "content": "You decide social micro-choices for a life-sim character. "
            'Respond ONLY with JSON: {"talk": true|false, "reason": "..."}. Task: should_talk.'
            + roster_gender_directive(english_only=True),
        },
        {
            "role": "user",
            "content": (
                f"{character_card(agent)}\n{state_line(agent)}\n"
                f"{memories_block(memories)}\n"
                f"{other_name} is nearby. Should {agent.profile.name} start a conversation?"
            ),
        },
    ]


def dialogue_prompt(
    a: "Agent", b: "Agent", a_mem: list[str], b_mem: list[str], max_turns: int = 4,
    a_wants_to_mention: str | None = None, b_wants_to_mention: str | None = None,
    is_confrontation: bool = False, time_hint: str = "",
    a_impression: str | None = None, b_impression: str | None = None,
    a_confide: str | None = None, b_confide: str | None = None,
    nearby_landmark: str | None = None,
    confession: dict | None = None,
) -> list[dict]:
    scene = f"Scene: {time_hint + ' ' if time_hint else ''}at {a.state.location}.\n"
    if nearby_landmark:
        scene += f"Nearby: {nearby_landmark}.\n"
    user = (
        f"Simulate a conversation between {a.profile.name} and {b.profile.name}. "
        f"Maximum {max_turns} turns.\n{scene}"
        f"A: {character_card(a, speech=True)} {state_line(a)}\n{memories_block(a_mem)}\n"
        f"B: {character_card(b, speech=True)} {state_line(b)}\n{memories_block(b_mem)}"
    )
    # Lasting impressions colour how they treat each other, beyond today's memories.
    if a_impression:
        user += f"\n{a.profile.name}'s lasting impression of {b.profile.name}: \"{a_impression}\""
    if b_impression:
        user += f"\n{b.profile.name}'s lasting impression of {a.profile.name}: \"{b_impression}\""
    if a_wants_to_mention:
        # A confrontation opener is a private worry raised to someone's face, not a
        # line to recite: ask for it in the confronter's own words.
        if is_confrontation:
            user += (f"\n{a.profile.name} wants to raise this with {b.profile.name} to their face, "
                     f"in their own words as a direct question/accusation (do NOT read it out "
                     f"verbatim): {a_wants_to_mention}")
        else:
            user += f"\n{a.profile.name} wants to bring up: {a_wants_to_mention}"
    if b_wants_to_mention:
        user += f"\n{b.profile.name} wants to bring up: {b_wants_to_mention}"
    # Confiding is private and vulnerable, not gossip. Feed the secret only as
    # BACKGROUND: the character must voice it themselves, in the first person and
    # addressing the listener directly -- never quote the third-person description.
    if a_confide:
        user += (f"\n{a.profile.name} has decided to open up to {b.profile.name} about a private "
                 f"worry. The secret (background, do NOT quote verbatim): {a_confide}. "
                 f"{a.profile.name} should express this in the first person, in their own words, "
                 f"naturally addressing {b.profile.name} directly.")
    if b_confide:
        user += (f"\n{b.profile.name} has decided to open up to {a.profile.name} about a private "
                 f"worry. The secret (background, do NOT quote verbatim): {b_confide}. "
                 f"{b.profile.name} should express this in the first person, in their own words, "
                 f"naturally addressing {a.profile.name} directly.")
    # A confession scene: the outcome is already decided by the rules; the model just
    # plays it. First-person, sincere, and it lands the way it's going to land.
    if confession:
        fn, tn = confession["from_name"], confession["to_name"]
        if confession.get("accepted"):
            user += (f"\n{fn} has decided this is the moment to confess romantic feelings to {tn}. "
                     f"{fn} opens up in the first person -- sincere, a little nervous. {tn} feels the same "
                     f"way and, across the exchange, happily says yes. Let it land warmly.")
        else:
            user += (f"\n{fn} has decided this is the moment to confess romantic feelings to {tn}. "
                     f"{fn} opens up in the first person -- sincere and nervous. But {tn} does not feel the "
                     f"same and gently, awkwardly turns {fn} down. Keep it kind, never cruel.")
    # Referential anchor -- the two speakers are face-to-face, so each addresses the
    # OTHER as "you", and "you" and that person's name are the SAME individual. The two
    # failure modes we've seen (juxtaposing the listener as if a third party -- "you and
    # {them}" -- and a speaker naming THEMSELVES in the third person) are called out by
    # name so the model can't slide into them.
    a_n, b_n = a.profile.name, b.profile.name
    coherence = (
        f" Referential anchor (critical): this is a face-to-face exchange between exactly "
        f"two people, {a_n} and {b_n}. When {a_n} speaks they address {b_n} directly as "
        f"\"you\" (你/妳) or by name -- but \"you\" and \"{b_n}\" are THE SAME person, so "
        f"never place them side by side as if two people (never \"你和{b_n}\" / \"妳和{b_n}\" "
        f"/ \"you and {b_n}\"); and {a_n} never refers to themselves in the third person by "
        f"name (never \"{a_n}覺得…\" / \"是{a_n}\" / \"it's {a_n}\" when {a_n} is the speaker). "
        f"The same rules hold for {b_n}. Do not invent concrete facts that aren't in the "
        f"context (deadlines, sums of money, named events); keep background grounded in "
        f"what you're given."
    )
    # Confide / confront are emotionally significant beats: a one-line version reads
    # as broken, so ask for real back-and-forth.
    intimate = bool(a_confide or b_confide or is_confrontation or confession)
    depth = (" This is an emotionally significant exchange: write at least 4 turns so it "
             "builds naturally and never lands as a single line.") if intimate else ""
    system = (
        "You write short, natural conversations for a life simulation. "
        "Ground it in the time of day, the place, each character's current mood and "
        "their recent memories; vary the opening and don't fall back on generic "
        "small talk when they already know each other. "
        "Respond ONLY with JSON: "
        '{"turns": [{"speaker": "...", "text": "..."}], '
        '"sentiment": 0..1, "trust_signal": 0..1, "conflict_signal": 0..1}.'
    )
    if is_confrontation:
        # This is a confrontation: B is being asked to their face whether they
        # started the rumor. B must clearly own up or deny, and the verdict is
        # reported as an extra boolean so the sim can settle the rumor.
        system = (
            f"You write a short, tense confrontation for a life simulation. "
            f"{b.profile.name} is being confronted to their face about whether they "
            f"spread a rumor. In the conversation {b.profile.name} must clearly either "
            f"admit or deny it. Respond ONLY with JSON: "
            '{"turns": [{"speaker": "...", "text": "..."}], '
            '"sentiment": 0..1, "trust_signal": 0..1, "conflict_signal": 0..1, '
            '"admitted": true|false} where "admitted" is whether '
            f"{b.profile.name} admitted to it."
        )
    return [
        {"role": "system",
         "content": (system + coherence + roster_directive() + roster_gender_directive()
                     + depth + _lang_directive() + dialogue_locale_directive())},
        {"role": "user", "content": user},
    ]


def appraise_prompt(text: str) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "You judge whether a statement is positive or negative for the person it is "
                "about, in a life simulation. Respond ONLY with JSON: {\"sentiment\": -1..1} "
                "(-1 very negative, +1 very positive). Task: appraise."
                + roster_directive()
                + roster_gender_directive(english_only=True)
            ),
        },
        {"role": "user", "content": text},
    ]


def distort_prompt(text: str) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "You retell a piece of gossip in a life simulation, passing it along to "
                "someone new. Rewrite it in your own words, allowing slight memory drift -- "
                "one sentence, same gist. Write in English (this is internal knowledge, not "
                "spoken lines). Respond ONLY with JSON: {\"text\": \"...\"}. Task: distort."
                + roster_directive(english_only=True)
                + roster_gender_directive(english_only=True)
            ),
        },
        {"role": "user", "content": f"Retell this: {text}"},
    ]


def leak_prompt(owner_name: str, secret_text: str) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "You turn someone's private secret into a piece of third-person gossip about them, as it "
                "would first be whispered to another person. One sentence, same substance, named in the third "
                "person. Write in English (this is internal knowledge, not spoken lines). "
                'Respond ONLY with JSON: {"text": "...", "sentiment": -1..1} where sentiment is how '
                "damaging/negative it is for that person. Task: leak."
                + roster_directive(english_only=True)
                + roster_gender_directive(english_only=True)
            ),
        },
        {"role": "user", "content": f"{owner_name}'s secret: {secret_text}"},
    ]


def decision_prompt(agent: "Agent", observation: str, memories: list[str], actions: list[str]) -> list[dict]:
    return [
        {
            "role": "system",
            "content": "You make one decision for a life-sim character. "
            'Respond ONLY with JSON: {"action": "...", "target": "...", "reason": "..."}. Task: decision.'
            + roster_gender_directive(english_only=True),
        },
        {
            "role": "user",
            "content": (
                f"{character_card(agent)}\n{state_line(agent)}\n"
                f"{memories_block(memories)}\n"
                f"Observation: {observation}\n"
                f"Available actions: {', '.join(actions)}.\n"
                "Choose one action."
            ),
        },
    ]


def reflection_prompt(agent: "Agent", day_events: list[str], open_secrets: list | None = None,
                      transitions: list | None = None) -> list[dict]:
    # The character's own still-open worries, offered back so reflection can retire
    # the ones today's experience has settled. Ids are opaque handles the sim maps
    # back to secrets; the model only judges which are done.
    secrets_block = ""
    resolved_key = ""
    if open_secrets:
        lines = "\n".join(
            f"  [{s.id}] {s.text}" + (f" (this worry concerns {s.about.capitalize()})" if s.about else "")
            for s in open_secrets
        )
        secrets_block = "\nYour still-open private worries:\n" + lines
        resolved_key = '"resolved_secret_ids": ["<id>", ...], '
    # Available life changes (each precondition already passed). Reflection may pick
    # at most one -- but only when the accumulated evidence is overwhelming.
    transitions_block = ""
    decision_key = ""
    if transitions:
        opts = "\n".join(f"  [{tid}] {label}" for tid, label in transitions)
        transitions_block = "\nLife changes you could choose right now:\n" + opts
        decision_key = '"life_decision": {"action": "<id>", "reason": "..."} | null, '
    return [
        {
            "role": "system",
            "content": (
                "You produce end-of-day reflection for a life-sim character. Respond ONLY with JSON: "
                '{"insights": ["...", "..."], '
                '"beliefs": [{"subject": "<name>", "text": "...", "confidence": 0.0-1.0, "sentiment": -1.0-1.0}], '
                + resolved_key + decision_key +
                '"new_secret": {"text": "...", "sensitivity": 0.0-1.0} | null}. '
                '"insights" are 1-2 short takeaways from today. '
                '"beliefs" are 0-2 LASTING impressions about a specific person or place -- formed ONLY when '
                "today's experience repeats or confirms something you already sensed; a single event is NOT "
                "enough, so prefer an empty list (be sparing). \"subject\" is the exact name of an agent or "
                'place; "text" is one COMPLETE descriptive English sentence; "sentiment" is how '
                "positive(+1)/negative(-1) the impression is. "
                'GOOD text: "Xue seems increasingly determined to leave her job". BAD text: "ok". '
                "Return an empty list rather than a vague or filler impression. "
                '"new_secret" is a private matter this character is quietly keeping from others -- return one '
                "ONLY if today surfaced a clear unspoken worry or hidden truth of their own; otherwise null "
                '(be sparing). "sensitivity" is how private it is. '
                + ('"resolved_secret_ids" are the ids of any listed open worry that TODAY has clearly '
                   "settled or acted on -- you finally did the thing, spoke to the person it concerns, or "
                   "it plainly stopped weighing on you. Include an id ONLY with clear evidence in today's "
                   "events; when in doubt leave it out (prefer an empty list). " if open_secrets else "")
                + ('"life_decision" is a MAJOR life change, chosen from the listed options -- pick one ONLY '
                   "when a long, repeated pattern of experience makes it clearly the right moment (a settled "
                   "resolve built over many days, not a single bad afternoon). This is rare: the vast "
                   "majority of reflections MUST return null. When you do choose, give the id and a reason "
                   "grounded in the accumulated evidence. " if transitions else "")
                + "Write all text in English (this is internal knowledge, translated for display separately). "
                "Task: reflection."
                + roster_directive(english_only=True)
                + roster_gender_directive(english_only=True)
            ),
        },
        {
            "role": "user",
            "content": (
                # Present the reflecting agent by their English name too, so beliefs
                # come back with roster names the sim can resolve.
                f"{character_card(agent, name=agent.id.capitalize())}\n"
                "Events today:\n" + "\n".join(f"- {e}" for e in day_events[-15:])
                + secrets_block + transitions_block
            ),
        },
    ]


def chapter_closure_prompt(agent: "Agent", material: dict, outcome: str,
                           relationship_lines: list[str]) -> list[dict]:
    """The one LLM call of chapter closure (smart tier, rare). Input is rule-assembled:
    the chapter's top memories (with stable ids), a Python-computed relationship
    summary, and the outcome. Output: a first-person English biography line toned by
    the outcome, an emotional residue word, and which memory ids it drew on."""
    from ...agents import chapters as chapters_mod
    ch = material["chapter"]
    tone = {
        "completed": "you did it -- quiet pride or fulfilment, not boasting",
        "failed": "it didn't work out -- honest, a little sore, not self-pitying",
        "abandoned": "you chose to let it go -- settled, owning the choice",
    }.get(outcome, "you did it")
    residues = ", ".join(chapters_mod.RESIDUES)
    mems = "\n".join(f"  [{m['id']}] {m['text']}" for m in material["memories"]) or "  (no specific memories)"
    rels = "\n".join(f"  - {l}" for l in relationship_lines)
    # Aftermath: what happened AFTER the chapter was already over. Given only for a
    # closure that must know whether they ever went back (typically abandoned): it is
    # not part of the pursuit, and it must not be narrated as if it were.
    aftermath = ""
    if material.get("aftermath"):
        w = material.get("aftermath_window") or ["?", "?"]
        aft = "\n".join(f"  [{m['id']}] {m['text']}" for m in material["aftermath"])
        aftermath = (f"\nAfterwards -- the ripples AFTER this chapter was already over (Day {w[0]} to Day {w[1]}; "
                     f"NOT part of the chapter, and they show what they did or did not go back to):\n{aft}")
    forbid = ""
    if material.get("forbid_terms"):
        forbid = (" Never use these words or the meaning they carry, because it did not happen: "
                  + ", ".join(material["forbid_terms"]) + ".")
    return [
        {
            "role": "system",
            "content": (
                "You write the closing line of a chapter in a life-sim character's life. "
                'Respond ONLY with JSON: {"biography_line": "...", "emotional_residue": "<word>", '
                '"memory_refs": ["<id>", ...]}. '
                '"biography_line" is ONE sentence (max ~35 words), FIRST PERSON, in English, that '
                f"sums up what this chapter was and how it ended -- tone: {tone}. It must be "
                "specific to the memories given (no invented facts). Any duration, count or "
                "specific number may ONLY come from the listed memories or the day span given "
                "below; if the material doesn't say, leave it out entirely. "
                f'"emotional_residue" is exactly one of: {residues} -- the mood colouring the next '
                'few days. "memory_refs" lists the ids of the listed memories the line draws on. '
                + forbid +
                " Task: chapter_closure."
                + roster_directive(english_only=True)
                + roster_gender_directive(english_only=True)
            ),
        },
        {
            "role": "user",
            "content": (
                f"{character_card(agent, name=agent.id.capitalize())}\n"
                f"Chapter now closing: \"{ch.get('title', '')}\" -- goal: {ch.get('goal') or ch.get('title', '')}. "
                f"Outcome: {outcome}. It ran from Day {material['window']['start_day']} to "
                f"Day {material['window']['end_day']} (about {material['window']['days']} days).\n"
                f"Memories from this chapter:\n{mems}\n"
                f"People during this chapter:\n{rels}"
                + aftermath
            ),
        },
    ]


def wish_generation_prompt(agent: "Agent", material: dict, rejection: str = "") -> list[dict]:
    """The one LLM call of phase 2b: ask a resident's own history what they now want.

    Everything below is rule-assembled. The prompt's whole job is to make three
    things unmissable -- the wish must come from THIS life (provenance), it must be
    something they could actually do, and it must change their week rather than
    describe it -- and to make declining an honourable answer, because a template
    wish would be worse than no wish."""
    from ...agents import wishes as wishes_mod

    def lines(items, fmt, empty="  (none)"):
        return "\n".join(fmt(x) for x in items) or empty

    bio = lines(material["biography"], lambda m: f"  [{m['id']}] {m['text']}",
                "  (no closed chapters yet -- this would be their first)")
    mems = lines(material["memories"], lambda m: f"  [{m['id']}] (weight {m['importance']}) {m['text']}")
    close = lines(material["relationships"]["closest"],
                  lambda r: f"  {r['id'].capitalize()}: friendship {r['friendship']}, "
                            f"trust {r['trust']}" + (f", friction {r['conflict']}" if r['conflict'] else ""))
    friction = ", ".join(x.capitalize() for x in material["relationships"]["friction"]) or "nobody"
    routine = lines(material["routine"], lambda r: f"  {r['location']}: {', '.join(r['actions'])}")
    unvisited = ", ".join(material["unvisited_locations"]) or "(none -- they get everywhere)"
    strangers = ", ".join(x.capitalize() for x in material["no_regular_overlap_with"]) or "(nobody)"
    holding = lines(material["active_wishes"], lambda w: f"  \"{w['title']}\": {w['statement']}",
                    "  (nothing)")
    residue = (f"\nHow the last chapter left them feeling: {material['residue']}."
               if material.get("residue") else "")
    traits = ", ".join(f"{k} {v}" for k, v in sorted(material["personality"].items()))
    cap = material["capacity"]
    allowed_scales = [s for s in ("major", "minor") if cap.get(s, 0) > 0]
    if material.get("in_pursuit"):
        allowed_scales = [s for s in allowed_scales if s != "major"]
    feedback = ""
    if rejection:
        feedback = ("\n\nYour previous proposal was REJECTED for this reason:\n  "
                    f"{rejection}\nPropose something different that does not repeat the problem.")

    system = (
        "You decide what a character in a life simulation privately comes to want next. "
        "This is not a quest handed to them: it grows out of what they have lived, and it "
        "will quietly steer months of their behaviour, so it must be true to this person.\n\n"
        "Respond ONLY with JSON, either:\n"
        '  {"no_wish": true, "reason": "..."}   -- nothing in this life is asking for anything '
        "right now, which is a perfectly good answer and better than inventing a goal; or\n"
        '  {"title": "...", "statement": "...", "motivation": "...", "scale": "major"|"minor",\n'
        '   "narrative": "...", "expires_in_days": <int>,\n'
        '   "provenance": ["<memory id>", ...],\n'
        '   "requirements": [{"kind": "...", "target": "...", "threshold": <number>}, ...]}\n\n'
        "Field rules:\n"
        '- "statement" is the wish in the character\'s own first-person words, one sentence.\n'
        '- "motivation" is why it matters to them, one sentence. Both stay PRIVATE to them.\n'
        '- "narrative" (major only) is how they would describe their life right now, first '
        "person, 1-2 sentences -- it becomes their self-description while they pursue this.\n"
        '- "provenance" is REQUIRED and must list ids from the material below. These are the '
        "memories the wish grew out of. Never invent an id; cite only ids you were given.\n"
        '- "expires_in_days" is how long they would give themselves.\n\n'
        "Requirements are how the world will observe progress. Use ONLY these kinds:\n"
        "  location_visits (target = a location id) -- arriving there, N times\n"
        "  talk_count      (target = a resident id) -- conversations with that person\n"
        "  meetups_kept    (target = a resident id) -- arranged meetings kept\n"
        "  friendship      (target = a resident id) -- reaching a friendship level 0-100\n"
        "  trust           (target = a resident id) -- reaching a trust level 0-100\n"
        "  action_count    (target = work|rest|idle) -- doing that, N times\n"
        "  money_gain      (no target) -- earning that much more than they have now\n"
        "  event_witnessed (target = an event verb) -- PASSIVE, cannot carry a major wish\n"
        "At most 8 requirements, and keep them few and concrete.\n\n"
        "THREE THINGS WILL REJECT YOUR PROPOSAL MECHANICALLY:\n"
        "1. Impossible for this person -- a target who does not exist, a place that does not "
        "exist, themselves as a social target, or earning money with no way to earn.\n"
        "2. It changes nothing. At least one requirement must ask for something their weekly "
        "routine does NOT already provide (see 'Places their routine never takes them' and "
        "'People they never regularly cross paths with' below). Working, resting and earning "
        "are what their week is already made of and can never be the thing that makes it new.\n"
        "3. It repeats a wish they have held before, or one someone else in town is carrying "
        "right now. Want something that is actually theirs and actually next.\n\n"
        "Prefer the small and specific over the grand and vague. A wish that reaches toward "
        "one person or one place beats one that gestures at a whole life."
        + roster_directive(english_only=True)
        + roster_gender_directive(english_only=True)
        + " Write all text in English (this is internal knowledge, translated for display "
          "separately). Task: wish_generation."
    )
    user = (
        f"{character_card(agent, name=agent.id.capitalize())}\n"
        f"Personality: {traits}.{residue}\n\n"
        f"The chapters of their life so far (their own words, at each closing):\n{bio}\n\n"
        f"What is on their mind lately:\n{mems}\n\n"
        f"Closest to them:\n{close}\nFriction with: {friction}\n\n"
        f"Their week, as it already stands:\n{routine}\n"
        f"Places their routine never takes them: {unvisited}\n"
        f"People they never regularly cross paths with: {strangers}\n\n"
        f"Already holding:\n{holding}\n"
        f"They may propose a wish of scale: {', '.join(allowed_scales) or '(none -- decline)'}."
        f"{feedback}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def translate_prompt(text: str, owner: str = "", owner_gender: str = "") -> list[dict]:
    """Display-layer only: render a piece of English knowledge text into
    Traditional Chinese, with the name roster as a hard mapping so residents keep
    their canonical zh names (Lengyue -> 冷月), and the gender roster so a pronoun
    is fixed to the resident's canonical gender (historical text may say "her" for a
    male resident -- the roster wins).

    ``owner`` is whose text this is. A memory is written in the FIRST person, and a
    translator handed such a line with no idea who is speaking will sometimes rewrite
    it as third-person narration -- at which point it has to invent a gender, and
    guesses from whatever name is nearby. That is the mis-gendering the roster alone
    cannot prevent, because the missing fact is not the gender, it is who "I" is.
    So: name the owner, and forbid the person shift outright."""
    pairs = roster_pairs()
    mapping = "; ".join(f"{en}={zh}" for en, zh in pairs if zh and zh != en)
    guide = f" Use these exact name translations: {mapping}." if mapping else ""
    # Gender roster -> correct 他/她/牠. The source English can be mis-gendered
    # (pre-roster generations), so the roster is authoritative, not the source pronoun.
    gender = roster_gender_directive()
    speaker = ""
    if owner:
        who = f"{owner} ({owner_gender})" if owner_gender else owner
        speaker = (f" This text belongs to {who}: when it speaks in the first person "
                   f"(\"I\", \"my\", \"me\"), that \"I\" is {owner}.")
    person = (" Preserve the grammatical person exactly. First-person text stays first"
              " person -- render \"I\"/\"my\"/\"me\" as 我/我的, and NEVER rewrite it into"
              " third-person narration with 他/她. Only use 他/她 for someone the source"
              " itself refers to in the third person.")
    return [
        {
            "role": "system",
            "content": (
                "You translate short life-sim gossip/impression text from English into natural "
                "Traditional Chinese (zh-TW). Keep it one line, same meaning, no notes or quotes."
                + guide
                + gender
                + " Pronouns (他/她) must follow that gender roster, not the English source"
                  " (which may be mis-gendered)."
                + speaker
                + person
                + " Output ONLY the Traditional Chinese translation -- never repeat or append the"
                  " original English."
                + ' Respond ONLY with JSON: {"text": "..."}. Task: translate.'
            ),
        },
        {"role": "user", "content": text},
    ]
