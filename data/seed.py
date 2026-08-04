"""Seed world: 7 locations, 10 residents with distinct routines/personalities.

Names are shown in Chinese in the UI; ids stay pinyin and all internal text
(memories, secrets, goals) stays English -- the internal-English rule.
"""

from __future__ import annotations

from backend.app.agents.agent import Agent
from backend.app.agents.core import AgentState, MemoryItem, Profile
from backend.app.agents.routine import Routine, RoutineEntry, hm
from backend.app.world.world import Location


def build_locations() -> list[Location]:
    # 7 places on the 800x520 canvas (river hugs the left edge < x=60): two homes
    # at the top corners, the two shops mid-band as economic rivals, park + office
    # anchoring the lower corners, market on the right as the second social pole.
    return [
        Location("home_a", "Riverside House", "home", x=120, y=120),
        Location("home_b", "Hillside Apartment", "home", x=685, y=105),
        Location("cafe", "Moonlight Cafe", "cafe", x=340, y=185, owner="jiji", price=5.0),
        Location("bakery", "Sunrise Bakery", "bakery", x=400, y=405, owner="ange", price=4.0),
        Location("market", "Old Street Market", "market", x=610, y=215),
        Location("office", "Townsend Office", "office", x=690, y=375),
        Location("park", "Old Oak Park", "park", x=155, y=330, landmarks=[
            # Aisi's interactive light installation, seeded a third done -- an
            # engineering piece she builds up over afternoons in the park until
            # it lights up completely.
            {"id": "installation", "name": "the interactive light installation",
             "state": "in_progress", "progress": 0.3, "created_by": "aisi"},
        ]),
    ]


def _routine(entries: list[tuple[int, str, str]]) -> Routine:
    return Routine([RoutineEntry(t, a, l) for (t, a, l) in entries])


def _rel(agent: Agent, other_id: str, friendship: float, trust: float) -> None:
    r = agent.rel(other_id)
    r.friendship = float(friendship)
    r.trust = float(trust)


def build_agents() -> list[Agent]:
    # jiji (Cafe Owner) -- inherits Alice: outgoing, gossipy, the cafe's stress.
    jiji = Agent(
        profile=Profile(
            id="jiji", name="ㄐㄐ", age=27, occupation="Cafe Owner",
            personality={"extraversion": 0.8, "agreeableness": 0.7, "openness": 0.6, "neuroticism": 0.3},
            traits=["curious", "friendly", "gossipy"],
            goals=[{"goal": "Make the cafe popular", "priority": 0.8}],
            daily_wage=0.0,   # income is the cafe's revenue
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
    jiji.memory.add(MemoryItem(0, "{agent:xue} mentioned she wants to resign from her job.", importance=6))
    jiji.memory.add(MemoryItem(0, "{agent:xue} likes black coffee.", importance=2))

    # ange (Baker) -- inherits Rosa: the rivalry with the cafe is built in.
    ange = Agent(
        profile=Profile(
            id="ange", name="安哥", age=38, occupation="Baker",
            personality={"extraversion": 0.6, "agreeableness": 0.5, "openness": 0.6, "neuroticism": 0.4},
            traits=["hardworking", "proud", "competitive"],
            goals=[{"goal": "Make the bakery more popular than the cafe", "priority": 0.85}],
            daily_wage=0.0,   # income is the bakery's revenue
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

    # oula (Postman) -- inherits Ken: cheerful, gossipy, a full-town daily round.
    oula = Agent(
        profile=Profile(
            id="oula", name="歐拉", age=34, occupation="Postman",
            personality={"extraversion": 0.9, "agreeableness": 0.7, "openness": 0.6, "neuroticism": 0.3},
            traits=["cheerful", "gossipy", "restless"],
            goals=[{"goal": "Get to know every single person in town", "priority": 0.8}],
            daily_wage=55.0,
        ),
        state=AgentState(location="home_a"),
        routine=_routine([                        # the rumor superconductor
            (hm(6, 30), "eat", "bakery"),         # early bread run -> gives Ange her first custom
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

    # lengyue (Doctor) -- inherits Carol: calm, observant, helpful.
    lengyue = Agent(
        profile=Profile(
            id="lengyue", name="冷月", age=36, occupation="Doctor",
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

    # azong (Freelance Designer) -- rewritten: an easygoing third-space regular who
    # drifts between the cafe, market and park, so she meets many people naturally.
    azong = Agent(
        profile=Profile(
            id="azong", name="阿總", age=35, occupation="Freelance Designer",
            personality={"extraversion": 0.65, "agreeableness": 0.7, "openness": 0.8, "neuroticism": 0.4},
            traits=["easygoing", "creative", "sociable"],
            goals=[{"goal": "Build a steady freelance client base without losing balance", "priority": 0.75}],
            daily_wage=50.0,   # irregular freelance income, flattened
        ),
        state=AgentState(location="home_b"),
        routine=_routine([
            (hm(8, 0), "eat", "home_b"),
            (hm(9, 30), "work", "cafe"),          # works from the cafe most mornings
            (hm(12, 30), "eat", "cafe"),
            (hm(14, 0), "rest", "market"),        # afternoon errands / people-watching
            (hm(15, 30), "work", "home_b"),
            (hm(18, 30), "eat", "home_b"),
            (hm(20, 0), "rest", "park"),
            (hm(22, 30), "sleep", "home_b"),
        ]),
    )

    # xixi (Student) -- inherits Leo: shy, curious, tech-leaning (was artistic).
    xixi = Agent(
        profile=Profile(
            id="xixi", name="希希", age=19, occupation="Student",
            personality={"extraversion": 0.4, "agreeableness": 0.6, "openness": 0.8, "neuroticism": 0.5},
            traits=["shy", "curious", "technical"],
            goals=[{"goal": "Work up the courage to ask Aisi to teach me programming", "priority": 0.75}],
            daily_wage=15.0,   # part-time
        ),
        state=AgentState(location="home_a"),
        routine=_routine([
            (hm(8, 0), "eat", "bakery"),          # student breakfast -> more of Ange's custom
            (hm(9, 0), "work", "home_a"),         # studying at home
            (hm(13, 0), "eat", "cafe"),
            (hm(14, 30), "rest", "park"),         # afternoons near the installation, where Aisi works
            (hm(18, 0), "eat", "home_a"),
            (hm(19, 30), "work", "home_a"),
            (hm(23, 0), "sleep", "home_a"),
        ]),
    )

    # aisi (Engineer) -- Emma's routine skeleton, rewritten for engineering: remote
    # work in the morning, building the installation in the park by afternoon, code
    # at night. She is the installation's creator.
    aisi = Agent(
        profile=Profile(
            id="aisi", name="艾斯", age=24, occupation="Engineer",
            personality={"extraversion": 0.5, "agreeableness": 0.6, "openness": 0.95, "neuroticism": 0.5},
            traits=["creative", "focused", "night-owl"],
            goals=[{"goal": "Finish the interactive light installation in the park", "priority": 0.85}],
            daily_wage=30.0,
        ),
        state=AgentState(location="home_a"),
        routine=_routine([
            (hm(9, 0), "eat", "home_a"),
            (hm(9, 30), "work", "home_a"),        # morning remote work
            (hm(13, 30), "eat", "cafe"),
            (hm(14, 30), "work", "park"),         # afternoons: building the installation
            (hm(19, 0), "eat", "home_a"),
            (hm(20, 0), "work", "home_a"),        # coding at night
            (hm(23, 30), "sleep", "home_a"),
        ]),
    )
    aisi.memory.add(MemoryItem(0, "The {landmark:installation} in {loc:park} is about a third done.", importance=5))

    # xue (Office Worker) -- inherits Bob: quiet, stressed, weighing whether to quit.
    xue = Agent(
        profile=Profile(
            id="xue", name="雪", age=31, occupation="Office Worker",
            personality={"extraversion": 0.4, "agreeableness": 0.6, "openness": 0.5, "neuroticism": 0.6},
            traits=["quiet", "loyal", "stressed"],
            goals=[{"goal": "Figure out whether to quit her job", "priority": 0.9}],
            daily_wage=60.0,
        ),
        state=AgentState(location="home_b"),
        routine=_routine([
            (hm(7, 0), "eat", "home_b"),
            (hm(8, 0), "work", "office"),
            (hm(12, 0), "eat", "cafe"),           # lunch at the cafe -> encounters
            (hm(13, 0), "work", "office"),
            (hm(17, 30), "rest", "park"),
            (hm(19, 0), "eat", "home_b"),
            (hm(22, 30), "sleep", "home_b"),
        ]),
    )
    xue.memory.add(MemoryItem(0, "Argued with the manager last week.", importance=7))
    xue.memory.add(MemoryItem(0, "{loc:cafe} feels like a safe place.", importance=4))

    # long (Market Vendor) -- inherits Mei: frugal, wise, quietly generous.
    long = Agent(
        profile=Profile(
            id="long", name="瓏", age=52, occupation="Market Vendor",
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

    # kuaizheng (Retired) -- inherits Grace: the quietest resident, home most of the day.
    kuaizheng = Agent(
        profile=Profile(
            id="kuaizheng", name="蒯政", age=61, occupation="Retired",
            personality={"extraversion": 0.3, "agreeableness": 0.8, "openness": 0.5, "neuroticism": 0.3},
            traits=["quiet", "kind", "private"],
            goals=[{"goal": "Tend his garden well", "priority": 0.6}],
            daily_wage=42.0,   # pension
            reflection_threshold=35,
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

    # ---- seed relationships: a small friend group + one one-way admiration ----
    # Lengyue / Azong / Oula are close in age (34-36) and start as friends.
    _rel(lengyue, "oula", 60, 55); _rel(oula, "lengyue", 60, 55)
    _rel(lengyue, "azong", 60, 55); _rel(azong, "lengyue", 60, 55)
    # Oula <-> Azong left at the default -- they develop on their own.
    # Xixi looks up to Aisi (one-way); she barely knows him yet (default).
    _rel(xixi, "aisi", 45, 40)

    return [jiji, ange, oula, lengyue, azong, xixi, aisi, xue, long, kuaizheng]


# Initial private matters, tied to the new personalities. Seeded into the
# SecretRegistry at boot; they only surface if an agent trusts someone enough to
# confide (see decision._maybe_confide). Kept in English (internal rule).
SEED_SECRETS = [
    ("xue", "I've been interviewing at a company in Taipei.", 0.8),                       # the resignation line
    ("ange", "I check Jiji's cafe menu every week to keep my prices lower.", 0.7),        # the quiet price war
    ("jiji", "The cafe barely breaks even; I put on a confident face every day.", 0.7),
    ("aisi", "I'm secretly worried my installation is too ambitious to finish.", 0.5),    # a creator's self-doubt
    ("xixi", "I want to ask Aisi to teach me programming but I freeze every time.", 0.4), # the admiration line
    ("azong", "I've been undercharging clients because I'm afraid to lose them.", 0.55),  # the freelancer's fear
    # long's quiet generosity and kuaizheng's inner life are left for reflection to grow.
]


def seed_secrets(registry, minute: int = 0) -> None:
    """Plant the initial secrets into a SecretRegistry (fresh start only; a
    resumed run restores its own from the snapshot)."""
    for owner, text, sensitivity in SEED_SECRETS:
        registry.add(owner, text, sensitivity, minute)
