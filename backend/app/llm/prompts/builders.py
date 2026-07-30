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

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid circular imports at runtime
    from ...agents.agent import Agent


def character_card(agent: "Agent") -> str:
    p = agent.profile
    traits = ", ".join(p.traits)
    return (
        f"{p.name}, {p.age}, {p.occupation}. Traits: {traits}. "
        f"Top goal: {p.goals[0]['goal'] if p.goals else 'none'}."
    )


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
    a_wants_to_mention: str | None = None,
) -> list[dict]:
    user = (
        f"Simulate a conversation between {a.profile.name} and {b.profile.name}. "
        f"Maximum {max_turns} turns.\n"
        f"A: {character_card(a)} {state_line(a)}\n{memories_block(a_mem)}\n"
        f"B: {character_card(b)} {state_line(b)}\n{memories_block(b_mem)}"
    )
    if a_wants_to_mention:
        user += f"\n{a.profile.name} wants to bring up: {a_wants_to_mention}"
    return [
        {
            "role": "system",
            "content": (
                "You write short, natural conversations for a life simulation. "
                "Respond ONLY with JSON: "
                '{"turns": [{"speaker": "...", "text": "..."}], '
                '"sentiment": 0..1, "trust_signal": 0..1, "conflict_signal": 0..1}.'
            ),
        },
        {"role": "user", "content": user},
    ]


def distort_prompt(text: str) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "You retell a piece of gossip in a life simulation, passing it along to "
                "someone new. Rewrite it in your own words, allowing slight memory drift -- "
                "one sentence, same gist. Respond ONLY with JSON: {\"text\": \"...\"}. Task: distort."
            ),
        },
        {"role": "user", "content": f"Retell this: {text}"},
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


def reflection_prompt(agent: "Agent", day_events: list[str]) -> list[dict]:
    return [
        {
            "role": "system",
            "content": "You produce end-of-day reflection insights for a life-sim character. "
            'Respond ONLY with JSON: {"insights": ["...", "..."]}. Task: reflection.',
        },
        {
            "role": "user",
            "content": (
                f"{character_card(agent)}\n"
                "Events today:\n" + "\n".join(f"- {e}" for e in day_events[-15:])
            ),
        },
    ]
