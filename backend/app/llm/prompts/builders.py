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


def roster_gender_directive() -> str:
    """List each resident's fixed gender so the model uses the right pronouns --
    the sim can't fix a mis-gendered 'he'/'she' at the display layer. Empty when
    no genders are set (older callers), so English/mock runs are unaffected."""
    genders = [(en, zh, g) for en, zh, g in _ROSTER if g]
    if not genders:
        return ""
    parts = ", ".join(f"{zh or en} is {g}" for en, zh, g in genders)
    return (f" Each resident has a fixed gender -- use the correct pronoun and never "
            f"call a woman 'he' or a man 'she': {parts}.")


def roster_pairs() -> list[tuple[str, str]]:
    return [(en, zh) for en, zh, _g in _ROSTER]


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
    p = agent.profile
    traits = ", ".join(p.traits)
    card = (
        f"{name or p.name}, {p.age}, {p.occupation}. Traits: {traits}. "
        f"Top goal: {p.goals[0]['goal'] if p.goals else 'none'}."
    )
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
            'Respond ONLY with JSON: {"talk": true|false, "reason": "..."}. Task: should_talk.',
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
    # A general coherence rule for every conversation: the two speakers are
    # face-to-face, so neither may talk about the other in the third person.
    coherence = (
        " Keep the exchange internally consistent: the two speakers are together, so a "
        "speaker must never refer to the person they are talking to in the third person "
        "-- they address each other directly as \"you\"."
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
            ),
        },
        {"role": "user", "content": f"{owner_name}'s secret: {secret_text}"},
    ]


def decision_prompt(agent: "Agent", observation: str, memories: list[str], actions: list[str]) -> list[dict]:
    return [
        {
            "role": "system",
            "content": "You make one decision for a life-sim character. "
            'Respond ONLY with JSON: {"action": "...", "target": "...", "reason": "..."}. Task: decision.',
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


def translate_prompt(text: str) -> list[dict]:
    """Display-layer only: render a piece of English knowledge text into
    Traditional Chinese, with the name roster as a hard mapping so residents keep
    their canonical zh names (Lengyue -> 冷月)."""
    pairs = roster_pairs()
    mapping = "; ".join(f"{en}={zh}" for en, zh in pairs if zh and zh != en)
    guide = f" Use these exact name translations: {mapping}." if mapping else ""
    return [
        {
            "role": "system",
            "content": (
                "You translate short life-sim gossip/impression text from English into natural "
                "Traditional Chinese (zh-TW). Keep it one line, same meaning, no notes or quotes."
                + guide
                + ' Respond ONLY with JSON: {"text": "..."}. Task: translate.'
            ),
        },
        {"role": "user", "content": text},
    ]
