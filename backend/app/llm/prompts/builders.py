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

from ... import pacing

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


# ---- zh-native knowledge text ---------------------------------------------
# A zh-native town writes its knowledge (reflections, rumors, biographies, wishes)
# in Chinese at the source -- nothing is translated afterwards. These pick, per
# prompt, between the English-canonical wording each prompt always had and its
# zh-native counterpart; JSON keys, ids and enum values stay English in both.
ZH_KNOWLEDGE = (" Write every human-readable text value in natural Traditional Chinese (zh-TW) "
                "-- this is the town's own language, not a translation -- and name people by "
                "their Chinese names. Keep all JSON keys, ids and enum values in English.")


def knowledge_lang(english: str) -> str:
    """The "which language do I write in" sentence of a knowledge prompt."""
    return ZH_KNOWLEDGE if lang_is_zh() else english


def knowledge_roster() -> str:
    """The cast lists for a knowledge prompt: English (pinyin) forms for the
    English-canonical town, Chinese forms for a zh-native one."""
    if lang_is_zh():
        return roster_directive() + roster_gender_directive()
    return roster_directive(english_only=True) + roster_gender_directive(english_only=True)


def card_name(agent: "Agent") -> str:
    """How a knowledge prompt names the resident it is about: the pinyin (== id)
    in English, so beliefs come back with names the sim resolves; the zh name
    with its id in a zh-native town."""
    if lang_is_zh():
        return f"{agent.profile.name} ({agent.id.capitalize()})"
    return agent.id.capitalize()


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
    "wish_feasibility": 0, "wish_deviation": 0, "wish_novelty": 0, "wish_novelty_struct": 0,
    # zh-native knowledge that came back in English / soup / empty (decision.reflection_zh_ok).
    "zh_quality": 0,
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
    chapter, so a finished matter drops out of the card the moment it closes. The
    seed's standing aims (profile.goals) are never shown: an ordinary chapter carrying
    the same seed goal into every prompt for weeks was a seed anchor (Day-31 diagnosis)."""
    from ...agents import chapters as chapters_mod
    p = agent.profile
    from ...agents import texts as texts_mod
    traits = ", ".join(texts_mod.trait_text(t) for t in p.traits)
    card = (f"{name or p.name}, {p.age}, {texts_mod.occupation_text(p.occupation)}. "
            f"Traits: {traits}. {chapters_mod.narrative(agent)}")
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


_BOND_PHRASE = {"stranger": "someone you barely know", "acquaintance": "an acquaintance",
                "friend": "a friend", "close": "one of your closest people"}


def dialogue_turn_prompt(ctx: dict, history: list[dict], turn_no: int, max_turns: int,
                         min_turns: int) -> list[dict]:
    """ONE line of a turn-by-turn conversation, written from inside ONE speaker.

    ``ctx`` is that speaker's private context, assembled at initiation
    (decision._speaker_ctx): only what THEY know -- their own memories, wishes,
    private matters, mood and view of the partner. The partner's inner state never
    appears; all they know of it is what has been said aloud (``history``). That is
    the information asymmetry: two people, two prompts, one shared transcript."""
    me, other = ctx["name"], ctx["partner"]
    accused = ctx.get("role") == "accused"
    fields = ('{"text": "...", "end": true|false, "sentiment": 0..1, "trust_signal": 0..1, '
              '"conflict_signal": 0..1, "tells": "", "plan": null, "storm_off": false'
              + (', "admitted": true|false' if accused else "") + "}")
    system = (
        f"You voice ONE character, {me}, in a face-to-face conversation with {other} in a life "
        f"simulation, and write only {me}'s next spoken line. You know only what {me} knows: "
        f"their own memories, feelings, plans and private matters (given below), plus whatever "
        f"{other} has said aloud. You cannot see into {other}'s head, and you never decide what "
        f"{other} feels, does or agrees to. Ground the line in the conversation so far and in "
        f"{me}'s own life; when something real is on {me}'s mind, don't fall back on generic "
        f"small talk. Keep {me}'s way of speaking, but don't open every line the same way or "
        f"repeat a catchphrase line after line. Let the talk end when it naturally would: everyday "
        f"chats between neighbours often wrap up in a few lines. Respond ONLY with JSON: {fields}\n"
        f"- text: what {me} says now, aloud, 1-3 short sentences.\n"
        f"- end: true when this line closes the conversation (a goodbye, or nothing left to say).\n"
        f"- sentiment / trust_signal / conflict_signal: how the exchange feels to {me} right now "
        f"(warmth, growing trust, friction; each 0..1).\n"
        f"- tells: when this line gives {other} a concrete piece of news they didn't have "
        f"(something that happened, a plan, a worry {me} chose to share), restate it as one short "
        f"third-person sentence; otherwise \"\".\n"
        f"- plan: only when {me} proposes, or agrees to, a concrete meeting with {other}: "
        f'{{"place": "<place name>", "day": "today"|"tomorrow", "hour": 8-21}}; otherwise null.\n'
        f"- storm_off: true only if {me} is upset enough to walk away mid-conversation; it ends the talk."
        + (f"\n- admitted: whether {me} owns up to it. By the end {me} must clearly admit or deny."
           if accused else "")
        + f"\nReferential anchor (critical): {me} speaks to {other} directly as \"you\" (你/妳) or "
          f"by name -- \"you\" and \"{other}\" are the SAME person, so never put them side by side "
          f"(never \"你和{other}\" / \"you and {other}\"); and {me} never refers to themselves in "
          f"the third person by name. Do not invent concrete facts that aren't in the context "
          f"(deadlines, sums of money, named events)."
        + roster_directive() + roster_gender_directive() + _lang_directive()
        + dialogue_locale_directive() + " Task: dialogue_turn."
    )
    lines = [f"Scene: {ctx['time'] + ' ' if ctx.get('time') else ''}at {ctx['place']}."]
    if ctx.get("nearby"):
        lines.append(f"Nearby: {ctx['nearby']}.")
    if ctx.get("objects"):
        lines.append("Also here: " + "; ".join(ctx["objects"]) + ".")
    lines.append(f"You are {ctx['card']} Your mood: {ctx['mood']}.")
    bond = f"To you, {other} is {_BOND_PHRASE.get(ctx.get('bond', ''), 'someone you know')}"
    if ctx.get("romance"):
        bond += f" ({ctx['romance']})"
    lines.append(bond + ".")
    if ctx.get("impression"):
        lines.append(f"Your lasting impression of {other}: \"{ctx['impression']}\"")
    if ctx.get("memories"):
        lines.append(f"What you remember about {other}:\n" + "\n".join(f"- {m}" for m in ctx["memories"]))
    if ctx.get("recent"):
        lines.append("Lately on your mind:\n" + "\n".join(f"- {m}" for m in ctx["recent"]))
    if ctx.get("wishes"):
        lines.append("What you want right now (your own; nobody else knows unless you say it):\n"
                     + "\n".join(f"- {w}" for w in ctx["wishes"]))
    if ctx.get("private"):
        lines.append("Private matters (yours alone -- share only if it truly fits the moment):\n"
                     + "\n".join(f"- {x}" for x in ctx["private"]))
    lines.extend(ctx.get("intents") or [])
    if history:
        lines.append("Conversation so far:\n" + "\n".join(f"{t['speaker']}: {t['text']}" for t in history))
    else:
        lines.append("Nobody has spoken yet -- you open.")
    pace = f"This is line {turn_no} of at most {max_turns}."
    if turn_no < min_turns:
        pace += (f" This matters too much to cut short: don't end before line {min_turns}."
                 if min_turns > 2 else f" Don't end on this line; let {other} answer.")
    elif turn_no >= max_turns:
        pace += " It is the last line: let it close the conversation."
    lines.append(pace)
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}]


def day_plan_prompt(agent: "Agent", mat: dict) -> list[dict]:
    """The morning micro-decision (agents/dayplan.py): one resident, waking up,
    deciding how they want to spend today. Everything in it is their own."""
    me = agent.name
    system = (
        f"You decide how ONE resident of a small town, {me}, wants to spend today, as they "
        f"wake up. Most days are ordinary and that is fine: if nothing pulls at {me}, say so "
        f"(nothing_special: true) and keep the usual day. When something does -- yesterday, a "
        f"mood, a wish, a promise, a chapter that has stalled -- let it shape today in a small, "
        f"concrete way. You decide only {me}'s own day: never what anyone else does or feels, "
        f"and sleep is not yours to change. Respond ONLY with JSON: "
        '{"intent": "...", "nothing_special": true|false, "skip_work": 0..1, '
        '"slots": [{"from": 14, "to": 16, "place": "<place name>", "doing": "visit|rest|idle|work|eat"}], '
        '"actions": [{"hour": 15, "place": "<place name>", "what": "...", "leaves": "", "cost": 0}]}'
        "\n- intent: one first-person sentence: how they mean to spend today, and why."
        "\n- slots: at most 3 changes to the usual day, whole hours from 7 to 22, each at most "
        "4 hours; [] when nothing changes. \"visit\" means spending time at a place."
        "\n- skip_work: how much they want to skip work today, honestly (0 = not at all). "
        "Only meaningful on a work day."
        "\n- actions: at most 2 small things they want to DO today, by themselves, beyond where "
        "they spend time -- sketch something, tidy or plant something, put up a notice, try "
        "something new. \"what\" is a short phrase of what they do, without the subject; "
        "\"leaves\" is something it leaves behind in that place for others to see, short, or "
        "\"\"; \"cost\" is the money it takes (0 if none). Only their own doing: never another "
        "person's reaction, belongings or body, and nobody else's home. Something done for or "
        "with another person is not an action -- that happens when they talk -- so an action "
        "names no one. It may fall inside a stretch they already spend at that place. [] when "
        "nothing."
        + roster_directive() + roster_gender_directive() + _lang_directive()
        + dialogue_locale_directive() + " Task: day_plan."
    )
    head = f"Today is Day {mat['day']}, {mat['dow']}"
    if mat.get("rain"):
        head += ", and it is raining"
    if mat.get("off"):
        head += ". It is your day off"
    lines = [head + ".",
             f"You are {character_card(agent)} Your mood: {mat['mood']}. "
             f"By nature you are {mat['duty']}. Money on hand: {mat['money']}.",
             "Your usual day:\n" + "\n".join(f"- {u}" for u in mat["usual"])]
    if mat.get("yesterday") or mat.get("talked"):
        y = "Yesterday:"
        if mat.get("yesterday"):
            y += "\n" + "\n".join(f"- {m}" for m in mat["yesterday"])
        if mat.get("talked"):
            y += "\nYou talked with: " + ", ".join(mat["talked"]) + "."
        lines.append(y)
    if mat.get("wishes"):
        lines.append("What you want right now:\n" + "\n".join(f"- {w}" for w in mat["wishes"]))
    if mat.get("chapter"):
        lines.append(mat["chapter"])
    if mat.get("promise"):
        lines.append(mat["promise"])
    if mat.get("people"):
        p = "People closest to you: " + ", ".join(mat["people"]) + "."
        if mat.get("tense"):
            p += " Things are tense with: " + ", ".join(mat["tense"]) + "."
        lines.append(p)
    lines.append("Places in town: " + ", ".join(mat["places"]) + ".")
    if mat.get("objects"):
        lines.append("Things people have left around town:\n" + "\n".join(f"- {o}" for o in mat["objects"]))
    if mat.get("did"):
        lines.append("Things you did lately:\n" + "\n".join(f"- {d}" for d in mat["did"]))
    lines.append(f"How does {me} want to spend today?")
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}]


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
                "one sentence, same gist."
                + knowledge_lang(" Write in English (this is internal knowledge, not spoken lines).")
                + " Respond ONLY with JSON: {\"text\": \"...\"}. Task: distort."
                + knowledge_roster()
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
                "person."
                + knowledge_lang(" Write in English (this is internal knowledge, not spoken lines).")
                + ' Respond ONLY with JSON: {"text": "...", "sentiment": -1..1} where sentiment is how '
                "damaging/negative it is for that person. Task: leak."
                + knowledge_roster()
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
                + ('place (the English form or id); "text" is one COMPLETE descriptive sentence in '
                   'Traditional Chinese; "sentiment" is how positive(+1)/negative(-1) the impression is. '
                   'GOOD text: "雪似乎越來越下定決心要離開她的工作". BAD text: "還好". '
                   if lang_is_zh() else
                   'place; "text" is one COMPLETE descriptive English sentence; "sentiment" is how '
                   "positive(+1)/negative(-1) the impression is. "
                   'GOOD text: "Xue seems increasingly determined to leave her job". BAD text: "ok". ')
                +
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
                + knowledge_lang("Write all text in English (this is internal knowledge, translated for "
                                 "display separately).")
                + " Task: reflection."
                + knowledge_roster()
            ),
        },
        {
            "role": "user",
            "content": (
                # Present the reflecting agent by their English name too, so beliefs
                # come back with roster names the sim can resolve.
                f"{character_card(agent, name=card_name(agent))}\n"
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
                + ('"biography_line" is ONE sentence (at most ~60 characters), FIRST PERSON (我), in '
                   'Traditional Chinese, that ' if lang_is_zh() else
                   '"biography_line" is ONE sentence (max ~35 words), FIRST PERSON, in English, that ')
                + f"sums up what this chapter was and how it ended -- tone: {tone}. It must be "
                "specific to the memories given (no invented facts). Any duration, count or "
                "specific number may ONLY come from the listed memories or the day span given "
                "below; if the material doesn't say, leave it out entirely. "
                f'"emotional_residue" is exactly one of: {residues} -- the mood colouring the next '
                'few days. "memory_refs" lists the ids of the listed memories the line draws on. '
                + forbid +
                " Task: chapter_closure."
                + knowledge_roster()
                + (ZH_KNOWLEDGE if lang_is_zh() else "")
            ),
        },
        {
            "role": "user",
            "content": (
                f"{character_card(agent, name=card_name(agent))}\n"
                f"Chapter now closing: \"{ch.get('title', '')}\" -- goal: {ch.get('goal') or ch.get('title', '')}. "
                f"Outcome: {outcome}. It ran from Day {material['window']['start_day']} to "
                f"Day {material['window']['end_day']} (about {material['window']['days']} days).\n"
                f"Memories from this chapter:\n{mems}\n"
                f"People during this chapter:\n{rels}"
                + ("\nPromises during this chapter:\n" + "\n".join(f"  - {p}" for p in material["promises"])
                   if material.get("promises") else "")
                + ("\nThings they set out to do during this chapter:\n"
                   + "\n".join(f"  - {p}" for p in material["actions"])
                   if material.get("actions") else "")
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
    # Ids, not display names: a requirement target is matched against these. The
    # roster elsewhere in the prompt shows people as "Aisi", so say plainly that a
    # target is the lower-case id.
    strangers = ", ".join(material["no_regular_overlap_with"]) or "(nobody)"
    people_ids = ", ".join(sorted(x[0].lower() for x in roster_genders())) or "-"
    # Each held wish carries its true scale: a model shown bare titles once called a
    # minor wish "major" in its own decline reason.
    holding = lines(material["active_wishes"],
                    lambda w: f"  [{w.get('scale', '?')}] \"{w['title']}\": {w['statement']}",
                    "  (nothing)")
    # A recently finished wish: a new one that carries it on is a sequel, and says so.
    done = "".join(
        (f"你最近完成了〈{w['title']}〉(Day {w['ended_on']})" if lang_is_zh()
         else f"You recently completed \"{w['title']}\" (Day {w['ended_on']})")
        + " -- a new wish may carry it on, but then as its next chapter, not a rerun.\n"
        for w in material.get("recent_completed") or [])
    residue = (f"\nHow the last chapter left them feeling: {material['residue']}."
               if material.get("residue") else "")
    if material.get("mood"):
        residue += f"\nTheir mood these days: {material['mood']}."
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
        f'- "expires_in_days" is how long they would give themselves -- about '
        f'{pacing.SPAN_DAYS["major"]} days for a major wish and {pacing.SPAN_DAYS["minor"]} '
        f'for a minor one, unless their own life clearly asks for more or less.\n\n'
        "Requirements are how the world will observe progress. Use ONLY these kinds:\n"
        "  location_visits (target = a location id) -- arriving there, N times\n"
        "  talk_count      (target = a resident id) -- conversations with that person\n"
        "  meetups_kept    (target = a resident id) -- arranged meetings kept\n"
        "  friendship      (target = a resident id) -- reaching a friendship level 0-100\n"
        "  trust           (target = a resident id) -- reaching a trust level 0-100\n"
        "  action_count    (target = work|rest|idle) -- doing that, N times\n"
        "  money_gain      (no target) -- earning that much more than they have now\n"
        "  event_witnessed (target = an event verb) -- PASSIVE, cannot carry a major wish\n"
        "At most 8 requirements, and keep them few and concrete.\n"
        f"A requirement 'target' is an ID, always lower-case, never a display name. "
        f"Resident ids: {people_ids}. Location ids are the ones listed under their week "
        f"below.\n\n"
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
        + knowledge_roster()
        + knowledge_lang(" Write all text in English (this is internal knowledge, translated for display "
                         "separately).")
        + " Task: wish_generation."
    )
    user = (
        f"{character_card(agent, name=card_name(agent))}\n"
        f"Personality: {traits}.{residue}\n\n"
        f"The chapters of their life so far (their own words, at each closing):\n{bio}\n\n"
        f"What is on their mind lately:\n{mems}\n\n"
        f"Closest to them:\n{close}\nFriction with: {friction}\n\n"
        f"Their week, as it already stands:\n{routine}\n"
        f"Places their routine never takes them: {unvisited}\n"
        f"People they never regularly cross paths with: {strangers}\n\n"
        f"Already holding (scale in brackets):\n{holding}\n"
        f"{done}"
        + ("Things they did lately, of their own accord:\n"
           + "".join(f"  - {t}\n" for t in material["my_actions"]) if material.get("my_actions") else "")
        + ("Things people have left around town:\n"
           + "".join(f"  - {t}\n" for t in material["town_objects"]) if material.get("town_objects") else "")
        + f"They may propose a wish of scale: {', '.join(allowed_scales) or '(none -- decline)'}."
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
