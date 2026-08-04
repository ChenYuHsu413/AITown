"""Seed world: 5 locations, 5 agents with distinct routines/personalities."""

from __future__ import annotations

from backend.app.agents.agent import Agent
from backend.app.agents.core import AgentState, MemoryItem, Profile
from backend.app.agents.routine import Routine, RoutineEntry, hm
from backend.app.world.world import Location


def build_locations() -> list[Location]:
    return [
        Location("home_a", "Riverside House", "home", x=130, y=140),
        Location("home_b", "Hillside Apartment", "home", x=660, y=110),
        Location("cafe", "Moonlight Cafe", "cafe", x=395, y=250, owner="alice", price=5.0),
        Location("office", "Townsend Office", "office", x=620, y=370),
        Location("park", "Old Oak Park", "park", x=175, y=400, landmarks=[
            # Emma's mural, seeded half-done -- it echoes her memory of it being
            # "half finished" and becomes real once she paints it to completion.
            {"id": "mural", "name": "the mural", "state": "in_progress",
             "progress": 0.5, "created_by": "emma"},
        ]),
    ]


def _routine(entries: list[tuple[int, str, str]]) -> Routine:
    return Routine([RoutineEntry(t, a, l) for (t, a, l) in entries])


def build_agents() -> list[Agent]:
    alice = Agent(
        profile=Profile(
            id="alice", name="Alice", age=27, occupation="Cafe Owner",
            personality={"extraversion": 0.8, "agreeableness": 0.7, "openness": 0.6, "neuroticism": 0.3},
            traits=["curious", "friendly", "gossipy"],
            goals=[{"goal": "Make the cafe popular", "priority": 0.8}],
            daily_wage=0.0,   # no salary -- Alice's income is the cafe's revenue
        ),
        state=AgentState(location="home_a"),
        routine=_routine([
            (hm(7, 30), "eat", "home_a"),
            (hm(8, 30), "work", "cafe"),
            (hm(12, 0), "eat", "cafe"),
            (hm(13, 0), "work", "cafe"),
            (hm(18, 0), "eat", "home_a"),
            (hm(19, 30), "rest", "home_a"),
            (hm(23, 0), "sleep", "home_a"),
        ]),
    )
    alice.memory.add(MemoryItem(0, "Bob mentioned he wants to resign from his job.", importance=6))
    alice.memory.add(MemoryItem(0, "Bob likes black coffee.", importance=2))

    bob = Agent(
        profile=Profile(
            id="bob", name="Bob", age=31, occupation="Office Worker",
            personality={"extraversion": 0.4, "agreeableness": 0.6, "openness": 0.5, "neuroticism": 0.6},
            traits=["quiet", "loyal", "stressed"],
            goals=[{"goal": "Figure out whether to quit his job", "priority": 0.9}],
            daily_wage=60.0,
        ),
        state=AgentState(location="home_b"),
        routine=_routine([
            (hm(7, 0), "eat", "home_b"),
            (hm(8, 0), "work", "office"),
            (hm(12, 0), "eat", "cafe"),          # lunch at Alice's cafe -> encounters
            (hm(13, 0), "work", "office"),
            (hm(17, 30), "rest", "park"),
            (hm(19, 0), "eat", "home_b"),
            (hm(22, 30), "sleep", "home_b"),
        ]),
    )
    bob.memory.add(MemoryItem(0, "Argued with the manager last week.", importance=7))
    bob.memory.add(MemoryItem(0, "Alice's cafe feels like a safe place.", importance=4))

    carol = Agent(
        profile=Profile(
            id="carol", name="Carol", age=45, occupation="Doctor",
            personality={"extraversion": 0.6, "agreeableness": 0.8, "openness": 0.7, "neuroticism": 0.2},
            traits=["calm", "observant", "helpful"],
            goals=[{"goal": "Keep the town healthy", "priority": 0.7}],
            daily_wage=80.0,
        ),
        state=AgentState(location="home_a"),
        routine=_routine([
            (hm(6, 30), "eat", "home_a"),
            (hm(7, 30), "work", "office"),
            (hm(12, 30), "eat", "cafe"),
            (hm(13, 30), "work", "office"),
            (hm(18, 30), "rest", "park"),
            (hm(20, 0), "eat", "home_a"),
            (hm(22, 0), "sleep", "home_a"),
        ]),
    )

    david = Agent(
        profile=Profile(
            id="david", name="David", age=68, occupation="Retired Teacher",
            personality={"extraversion": 0.7, "agreeableness": 0.9, "openness": 0.8, "neuroticism": 0.1},
            traits=["wise", "talkative", "nostalgic"],
            goals=[{"goal": "Stay connected with people", "priority": 0.9}],
            daily_wage=45.0,   # pension
        ),
        state=AgentState(location="home_b"),
        routine=_routine([
            (hm(6, 0), "eat", "home_b"),
            (hm(9, 0), "rest", "park"),
            (hm(11, 0), "rest", "cafe"),          # long cafe mornings -> social hub
            (hm(14, 0), "rest", "park"),
            (hm(18, 0), "eat", "home_b"),
            (hm(21, 30), "sleep", "home_b"),
        ]),
    )

    emma = Agent(
        profile=Profile(
            id="emma", name="Emma", age=22, occupation="Art Student",
            personality={"extraversion": 0.5, "agreeableness": 0.6, "openness": 0.95, "neuroticism": 0.5},
            traits=["creative", "dreamy", "night-owl"],
            goals=[{"goal": "Finish the park mural", "priority": 0.85}],
            daily_wage=30.0,   # part-time student job
        ),
        state=AgentState(location="home_a"),
        routine=_routine([
            (hm(9, 30), "eat", "home_a"),
            (hm(10, 30), "work", "park"),         # painting the mural
            (hm(13, 30), "eat", "cafe"),
            (hm(14, 30), "work", "park"),
            (hm(19, 0), "eat", "home_a"),
            (hm(20, 0), "work", "home_a"),        # sketching at night
            (hm(24 * 60 - 30), "sleep", "home_a"),
        ]),
    )
    emma.memory.add(MemoryItem(0, "The mural near the park is half finished.", importance=5))

    return [alice, bob, carol, david, emma]


# Initial private matters, tied to the existing personalities. Seeded into the
# SecretRegistry at boot; they only ever surface if an agent trusts someone
# enough to confide (see decision._maybe_confide). Kept in English (internal rule).
SEED_SECRETS = [
    ("bob", "I've secretly been interviewing at a company in another city.", 0.8),
    ("emma", "I haven't told my parents I switched my major to art.", 0.6),
    ("alice", "The cafe is quietly losing money and I'm scared it won't last the year.", 0.7),
]


def seed_secrets(registry, minute: int = 0) -> None:
    """Plant the initial secrets into a SecretRegistry (fresh start only; a
    resumed run restores its own from the snapshot)."""
    for owner, text, sensitivity in SEED_SECRETS:
        registry.add(owner, text, sensitivity, minute)
