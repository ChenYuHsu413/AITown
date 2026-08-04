"""Seed world: 7 locations, 10 agents with distinct routines/personalities."""

from __future__ import annotations

from backend.app.agents.agent import Agent
from backend.app.agents.core import AgentState, MemoryItem, Profile
from backend.app.agents.routine import Routine, RoutineEntry, hm
from backend.app.world.world import Location


def build_locations() -> list[Location]:
    # Coordinates rebalanced for 7 places on the 800x520 canvas (river hugs the
    # left edge < x=60): two homes at the top corners, the two shops mid-band as
    # economic rivals, park + office anchoring the lower corners, market on the
    # right as the second social pole.
    return [
        Location("home_a", "Riverside House", "home", x=120, y=120),
        Location("home_b", "Hillside Apartment", "home", x=685, y=105),
        Location("cafe", "Moonlight Cafe", "cafe", x=340, y=185, owner="alice", price=5.0),
        Location("bakery", "Sunrise Bakery", "bakery", x=400, y=405, owner="rosa", price=4.0),
        Location("market", "Old Street Market", "market", x=610, y=215),
        Location("office", "Townsend Office", "office", x=690, y=375),
        Location("park", "Old Oak Park", "park", x=155, y=330, landmarks=[
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

    # ---- expansion: 5 new residents ---------------------------------------
    rosa = Agent(
        profile=Profile(
            id="rosa", name="Rosa", age=38, occupation="Baker",
            personality={"extraversion": 0.6, "agreeableness": 0.5, "openness": 0.6, "neuroticism": 0.4},
            traits=["hardworking", "proud", "competitive"],
            goals=[{"goal": "Make the bakery more popular than the cafe", "priority": 0.85}],
            daily_wage=0.0,   # income is the bakery's revenue, like Alice
        ),
        state=AgentState(location="home_b"),
        routine=_routine([
            (hm(5, 30), "work", "bakery"),        # opens at dawn
            (hm(12, 0), "eat", "bakery"),
            (hm(13, 0), "work", "bakery"),
            (hm(19, 0), "eat", "home_b"),
            (hm(21, 0), "rest", "home_b"),
            (hm(22, 30), "sleep", "home_b"),
        ]),
    )

    ken = Agent(
        profile=Profile(
            id="ken", name="Ken", age=29, occupation="Postman",
            personality={"extraversion": 0.9, "agreeableness": 0.7, "openness": 0.6, "neuroticism": 0.3},
            traits=["cheerful", "gossipy", "restless"],
            goals=[{"goal": "Get to know every single person in town", "priority": 0.8}],
            daily_wage=55.0,
        ),
        state=AgentState(location="home_a"),
        routine=_routine([                        # a full-town round -- the rumor superconductor
            (hm(6, 30), "eat", "bakery"),          # early bread run -> gives Rosa her first custom
            (hm(7, 30), "rest", "market"),
            (hm(9, 0), "rest", "cafe"),
            (hm(10, 30), "rest", "office"),
            (hm(12, 0), "eat", "cafe"),
            (hm(13, 30), "rest", "park"),
            (hm(15, 0), "rest", "bakery"),
            (hm(16, 30), "rest", "market"),
            (hm(18, 30), "eat", "home_a"),
            (hm(21, 0), "sleep", "home_a"),
        ]),
    )

    mei = Agent(
        profile=Profile(
            id="mei", name="Mei", age=52, occupation="Market Vendor",
            personality={"extraversion": 0.5, "agreeableness": 0.7, "openness": 0.5, "neuroticism": 0.3},
            traits=["observant", "frugal", "wise"],
            goals=[{"goal": "Quietly save enough to retire in peace", "priority": 0.7}],
            daily_wage=40.0,
            reflection_threshold=35,              # a background character -> reflects less often
        ),
        state=AgentState(location="home_b"),
        routine=_routine([
            (hm(6, 0), "eat", "home_b"),
            (hm(7, 0), "work", "market"),
            (hm(12, 30), "eat", "market"),
            (hm(13, 30), "work", "market"),
            (hm(18, 0), "eat", "home_b"),
            (hm(20, 0), "rest", "home_b"),
            (hm(22, 0), "sleep", "home_b"),
        ]),
    )

    leo = Agent(
        profile=Profile(
            id="leo", name="Leo", age=19, occupation="Student",
            personality={"extraversion": 0.4, "agreeableness": 0.6, "openness": 0.8, "neuroticism": 0.5},
            traits=["shy", "curious", "artistic"],
            goals=[{"goal": "Work up the courage to ask Emma about painting", "priority": 0.75}],
            daily_wage=15.0,   # part-time
        ),
        state=AgentState(location="home_a"),
        routine=_routine([
            (hm(8, 0), "eat", "bakery"),          # student breakfast -> more of Rosa's custom
            (hm(9, 0), "work", "home_a"),         # studying at home
            (hm(13, 0), "eat", "cafe"),
            (hm(14, 30), "rest", "park"),         # afternoons near the mural, where Emma works
            (hm(18, 0), "eat", "home_a"),
            (hm(19, 30), "work", "home_a"),
            (hm(23, 0), "sleep", "home_a"),
        ]),
    )

    grace = Agent(
        profile=Profile(
            id="grace", name="Grace", age=61, occupation="Retired Nurse",
            personality={"extraversion": 0.3, "agreeableness": 0.8, "openness": 0.5, "neuroticism": 0.3},
            traits=["quiet", "kind", "private"],
            goals=[{"goal": "Tend her garden well", "priority": 0.6}],
            daily_wage=42.0,   # pension
            reflection_threshold=35,              # the most private resident -> a spare inner life
        ),
        state=AgentState(location="home_b"),
        routine=_routine([
            (hm(6, 30), "eat", "home_b"),
            (hm(7, 30), "rest", "market"),        # a quiet early-morning errand
            (hm(9, 30), "rest", "home_b"),        # home with the garden most of the day
            (hm(14, 0), "rest", "park"),          # an occasional walk
            (hm(16, 0), "rest", "home_b"),
            (hm(18, 30), "eat", "home_b"),
            (hm(21, 0), "sleep", "home_b"),
        ]),
    )

    return [alice, bob, carol, david, emma, rosa, ken, mei, leo, grace]


# Initial private matters, tied to the existing personalities. Seeded into the
# SecretRegistry at boot; they only ever surface if an agent trusts someone
# enough to confide (see decision._maybe_confide). Kept in English (internal rule).
SEED_SECRETS = [
    ("bob", "I've secretly been interviewing at a company in another city.", 0.8),
    ("emma", "I haven't told my parents I switched my major to art.", 0.6),
    ("alice", "The cafe is quietly losing money and I'm scared it won't last the year.", 0.7),
    # Expansion. Rosa's rivalry with Alice is built-in; Mei's is a *positive* secret
    # (leaking it earns her a good name -- exercises the benign-leak path); Leo's is
    # a one-way admiration waiting on the day his trust in Emma clears the bar.
    ("rosa", "I've been copying some of Alice's cafe ideas.", 0.7),
    ("mei", "I lend money quietly to neighbors who are struggling.", 0.5),
    ("leo", "I admire Emma's art but I'm too nervous to talk to her.", 0.4),
]


def seed_secrets(registry, minute: int = 0) -> None:
    """Plant the initial secrets into a SecretRegistry (fresh start only; a
    resumed run restores its own from the snapshot)."""
    for owner, text, sensitivity in SEED_SECRETS:
        registry.add(owner, text, sensitivity, minute)
