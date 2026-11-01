"""Seed world: 7 locations, 10 residents with distinct routines/personalities.

Names are shown in Chinese in the UI; ids stay pinyin. The descriptive text the
prompts read (speech style, goals, memories, secrets, the seeded pursuits) is
written twice: English, and -- for the zh-native era -- a faithful Traditional
Chinese transcription of the same settings (see the ``*_ZH`` tables; nothing added,
removed or changed). Structural values (occupation, traits, ids, routines,
personality numbers, relationships) are one set, shared by both.
"""

from __future__ import annotations

from backend.app.agents import chapters as chapters_mod
from backend.app.agents import texts
from backend.app.agents.agent import Agent
from backend.app.agents.core import AgentState, MemoryItem, Profile
from backend.app.agents.routine import Routine, RoutineEntry, hm
from backend.app.world.world import Location


# ---- speech style (2026-08 formal-server decision) -------------------------
# One line per resident: WRITE THE BEHAVIOUR, not an adjective; one locked tell
# each; complements (never repeats) their traits. English (internal rule) and
# wired into the dialogue prompt by build_agents (see Profile.speech_style).
# Oula carries the only literal verbal tic ("跟你說——"), kept in zh so it surfaces
# verbatim in Chinese dialogue.
SPEECH_STYLE = {
    "jiji": "Waves off questions about herself with a joke and flips it back on you, "
            "fishing for your news (partly to keep the cafe's troubles covered).",
    "ange": "Short, matter-of-fact sentences; measures everything against how it's done at the bakery.",
    "oula": "Talks fast, always half-way to the next stop; drops news in headlines; "
            "opens with the verbal tic \"跟你說——\" (his alone).",
    "lengyue": "Slow and precise; names the state she reads in you before getting to the point.",
    "azong": "Unhurried; reaches for vivid, picture-painting metaphors.",
    "xixi": "Hesitant, starts a sentence and pulls it back -- until a technical topic "
            "flips a switch and he turns instantly, fluently articulate.",
    "aisi": "Spare wording, answers the literal question; frames everything as a problem to be solved.",
    "xue": "Softens the ground before she speaks, says \"it's nothing\" when it isn't, "
           "apologizes even when it isn't her fault.",
    "long": "Says whatever comes to mind on the spot, unpolished, heedless of where it lands.",
    "kuaizheng": "Few words and slow, brief and mild, almost never asks a question back.",
}
# zh-native era: the same voices, transcribed. ㄐㄐ's line names no pronoun -- the
# English says "herself" for a resident whose gender is male; Chinese needs neither.
SPEECH_STYLE_ZH = {
    "jiji": "被問到自己的事時,總用一句玩笑帶過,再把話題丟回你身上,打聽你的近況(一部分是為了掩飾咖啡館的困境)。",
    "ange": "句子簡短、就事論事;什麼事都拿烘焙坊的做法來比較。",
    "oula": "說話很快,人總是已經走到半路要趕下一站;講消息像在念頭條;開口必帶口頭禪「跟你說——」(只有他會這樣說)。",
    "lengyue": "說話緩慢而精準;切入正題前,會先點出她從你身上讀到的狀態。",
    "azong": "不疾不徐;喜歡用鮮明、畫面感十足的比喻。",
    "xixi": "說話猶豫,話說到一半又收回去——直到碰上技術話題,像開關被打開,他瞬間變得流暢又能言善道。",
    "aisi": "用字精簡,只回答字面上的問題;把每件事都當成待解的問題來看。",
    "xue": "開口前總會先鋪墊緩和;明明有事卻說「沒什麼」;就算不是她的錯也會道歉。",
    "long": "想到什麼就當場說什麼,不修飾,也不管話會落在誰身上。",
    "kuaizheng": "話少又慢,簡短溫和,幾乎從不反問。",
}

# Standing aims, seed memories and secrets, transcribed (keyed by the English).
GOALS_ZH = {
    "Make the cafe popular": "讓咖啡館受歡迎",
    "Make the bakery more popular than the cafe": "讓烘焙坊比咖啡館更受歡迎",
    "Get to know every single person in town": "認識鎮上的每一個人",
    "Keep the town healthy": "守護小鎮的健康",
    "Build a steady freelance client base without losing balance": "建立穩定的接案客源,同時不失去生活的平衡",
    "Keep the town's machines running -- and get paid what I'm owed": "讓鎮上的機器都好好運轉——而且該拿的錢一分都不能少",
    "Tend his garden well": "把他的花園照顧好",
}
MEMORIES_ZH = {
    "{agent:xue} mentioned she wants to resign from her job.": "{agent:xue}提過她想辭掉工作。",
    "{agent:xue} likes black coffee.": "{agent:xue}喜歡喝黑咖啡。",
    "Noticed I look forward to {agent:azong} showing up every morning more than I'd admit.":
        "我發現自己每天早上都期待{agent:azong}出現,期待的程度超過我願意承認的。",
    "The {landmark:installation} in {loc:park} is about a third done.": "{loc:park}的{landmark:installation}大約完成了三分之一。",
    "Argued with the manager last week.": "上週和主管吵了一架。",
    "{loc:cafe} feels like a safe place.": "{loc:cafe}讓人覺得安心。",
}


# ---- romantic inclination (2026-08 formal-server decision) ------------------
# A single 0-1 scalar per resident = openness to a romantic relationship (NOT
# orientation, NOT a fixed pairing -- who falls for whom is left to emerge). 0
# would opt a character out of the line entirely. Xue/Aisi rise dynamically via
# the life-transition hooks (quit_job -> Xue 0.55; installation done -> Aisi 0.50).
ROMANTIC_INCLINATION = {
    "jiji": 0.60, "azong": 0.60, "oula": 0.55, "lengyue": 0.50, "xue": 0.40,
    "xixi": 0.35, "ange": 0.30, "aisi": 0.30, "long": 0.15, "kuaizheng": 0.10,
}


# Orientation bias (2026-08 user decision, per person). Coefficient (0-1) for a
# romance by the other's gender: {"same": ..., "other": ...}. Directional, so an
# unrequited cross-orientation attraction is expressible. Unlisted -> the default
# {"same": 0.2, "other": 1.0}.
ORIENTATION_BIAS = {
    "lengyue": {"same": 1.0, "other": 0.2},
    "oula":    {"same": 1.0, "other": 0.2},
    "xue":     {"same": 0.5, "other": 0.5},
}


# ---- conscientiousness (2026-09 developer decision) -------------------------
# The fifth personality dimension. The other four are written inline in each
# Profile; this one arrives later and is applied in the loop at the end of
# build_agents, next to speech style and romantic inclination -- so the ten values
# stay readable as one table instead of scattered across ten constructors.
#
# It is the sole personal input to how long a resident carries a frustrated wish
# before letting it go (see agents/wishes.abandonment_threshold): higher means
# they hold on longer. The extremes are deliberate -- Oula at 0.1 drops things
# almost as soon as they stop being fun, Xixi and Aisi at 1.0 will hold on through
# a week of solid obstruction -- and must not be "smoothed" toward the middle.
CONSCIENTIOUSNESS = {
    "jiji": 0.5, "ange": 0.7, "oula": 0.1, "lengyue": 0.4, "azong": 0.9,
    "xixi": 1.0, "aisi": 1.0, "xue": 0.8, "long": 0.5, "kuaizheng": 0.5,
}


# ---- life chapters (2026-09 chapter-closure phase 1) ------------------------
# An in-progress matter is a *pursuit chapter*, not a static goal: it lives on
# agent.chapter (goal text, title, narrative) and is closed by the pipeline in
# agents/chapters.py when the matter ends (landmark done, life transition, the
# worry's secret laid to rest, or God Mode). The other seven residents' goals are
# open-ended standing aims and stay on profile.goals (ordinary chapter).
# Field order: (goal, title, narrative, landmark_id).
SEED_PURSUITS = {
    "aisi": ("Finish the interactive light installation in the park",
             "Building the light installation",
             "I'm in the middle of building an interactive light installation in the park -- "
             "afternoons and weekends go into it, and I won't rest easy until it lights up whole.",
             "installation"),
    "xixi": ("Work up the courage to ask Aisi to teach me programming",
             "Working up the courage to ask Aisi",
             "I've been trying to work up the courage to ask Aisi to teach me programming; "
             "every time I'm near her I rehearse it and then freeze.",
             ""),
    "xue": ("Figure out whether to quit her job",
            "Deciding whether to quit",
            "I'm quietly weighing whether to quit my job -- the office wears on me, and I "
            "haven't decided which way to jump.",
            ""),
}


SEED_PURSUITS_ZH = {
    "aisi": ("完成公園裡的互動光影裝置",
             "打造光影裝置",
             "我正在公園裡打造一座互動光影裝置——下午和週末都投入在上面,直到它整座亮起來之前,我都無法安心。",
             "installation"),
    "xixi": ("鼓起勇氣請艾斯教我寫程式",
             "鼓起勇氣去問艾斯",
             "我一直想鼓起勇氣請艾斯教我寫程式;每次靠近她,我都在心裡排練一遍,然後就僵住了。",
             ""),
    "xue": ("想清楚要不要辭掉工作",
            "決定要不要辭職",
            "我正在默默考慮要不要辭掉工作——辦公室讓我身心俱疲,我還沒決定要往哪邊跳。",
            ""),
}


def seed_chapters(agents: list[Agent], day: int = 1) -> None:
    """Install the seeded pursuit chapters (fresh start only; a resumed run restores
    its own from the snapshot). Residents without a pursuit stay chapter=None,
    which every reader treats as ordinary days."""
    pursuits = SEED_PURSUITS_ZH if texts.zh_native() else SEED_PURSUITS
    for a in agents:
        spec = pursuits.get(a.id)
        if spec is None:
            continue
        goal, title, narrative, landmark_id = spec
        chapters_mod.start_pursuit(
            a, chapters_mod.make_pursuit(goal, title, narrative, day, landmark_id=landmark_id))


def build_locations() -> list[Location]:
    # 7 places on the 800x520 canvas (river hugs the left edge < x=60): two homes
    # at the top corners, the two shops mid-band as economic rivals, park + office
    # anchoring the lower corners, market on the right as the second social pole.
    return [
        # "水岸光影" place-name set (2026-08 formal-server decision). ids never change.
        Location("home_a", "Rainlisten House", "home", name_zh="聽雨居", x=120, y=120),
        Location("home_b", "Slopeview Flats", "home", name_zh="望坡樓", x=685, y=105),
        # Two shops open 7 days but each takes one weekly day off, staggered so the
        # town always has one open: the cafe rests Monday, the bakery Wednesday.
        # Tide × Hearth (water/fire) carries the old Moonlight × Sunrise rivalry mirror.
        Location("cafe", "Tide Cafe", "cafe", name_zh="潮汐咖啡館", x=340, y=185, owner="jiji", price=5.0,
                 closed_days=[0]),   # Monday
        Location("bakery", "Hearth Bakery", "bakery", name_zh="爐心烘焙坊", x=400, y=405, owner="ange", price=4.0,
                 closed_days=[2]),   # Wednesday
        Location("market", "Ferry Crossing Market", "market", name_zh="渡口市場", x=610, y=215),
        Location("office", "Riverrun Office", "office", name_zh="川流事務所", x=690, y=375),
        Location("park", "Firefly Park", "park", name_zh="螢火公園", x=155, y=330, landmarks=[
            # Aisi's interactive light installation, seeded a third done -- an
            # engineering piece she builds up over afternoons in the park until
            # it lights up completely.
            {"id": "installation", "name": "the interactive light installation", "name_zh": "互動光影裝置",
             "state": "in_progress", "progress": 0.3, "created_by": "aisi"},
        ]),
    ]


def _routine(weekday: list[tuple[int, str, str]],
             weekend: list[tuple[int, str, str]] | None = None) -> Routine:
    def mk(es):
        return [RoutineEntry(t, a, l) for (t, a, l) in es]
    return Routine(mk(weekday), mk(weekend) if weekend is not None else None)


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
            gender="male", traits=["curious", "friendly", "gossipy"],
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
    # Romance-emergence seed (the ㄐㄐ×阿總 test the formal-server checklist calls for):
    # narrative material only, no pre-set romance value -- the mechanism must grow it.
    jiji.memory.add(MemoryItem(
        0, "Noticed I look forward to {agent:azong} showing up every morning more than I'd admit.",
        importance=5, kind="reflection"))

    # ange (Baker) -- inherits Rosa: the rivalry with the cafe is built in.
    ange = Agent(
        profile=Profile(
            id="ange", name="安哥", age=38, occupation="Baker",
            personality={"extraversion": 0.6, "agreeableness": 0.5, "openness": 0.6, "neuroticism": 0.4},
            gender="male", traits=["hardworking", "proud", "competitive"],
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
            gender="male", traits=["cheerful", "gossipy", "restless"],
            goals=[{"goal": "Get to know every single person in town", "priority": 0.8}],
            daily_wage=55.0,
            off_days=[6],   # postman rests Sunday -> the gossip engine idles, a weekly rumor lull
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
            gender="female", traits=["calm", "observant", "helpful"],
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
        ], weekend=[                              # weekend: sleep in, then out and about
            (hm(9, 0), "eat", "home_a"),
            (hm(10, 30), "rest", "park"),
            (hm(12, 30), "eat", "cafe"),
            (hm(14, 0), "rest", "market"),
            (hm(16, 0), "rest", "park"),
            (hm(19, 0), "eat", "home_a"),
            (hm(22, 30), "sleep", "home_a"),
        ]),
    )

    # azong (Freelance Designer) -- rewritten: an easygoing third-space regular who
    # drifts between the cafe, market and park, so she meets many people naturally.
    azong = Agent(
        profile=Profile(
            id="azong", name="阿總", age=35, occupation="Freelance Designer",
            personality={"extraversion": 0.65, "agreeableness": 0.7, "openness": 0.8, "neuroticism": 0.4},
            gender="female", traits=["easygoing", "creative", "sociable"],
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
        ], weekend=[                              # weekend: no client work, more social drifting
            (hm(9, 0), "eat", "home_b"),
            (hm(10, 30), "rest", "cafe"),
            (hm(12, 30), "eat", "cafe"),
            (hm(14, 0), "rest", "market"),
            (hm(16, 0), "rest", "park"),
            (hm(19, 0), "eat", "home_b"),
            (hm(22, 30), "sleep", "home_b"),
        ]),
    )

    # xixi (Student) -- inherits Leo: shy, curious, tech-leaning (was artistic).
    xixi = Agent(
        profile=Profile(
            id="xixi", name="希希", age=19, occupation="Student",
            personality={"extraversion": 0.4, "agreeableness": 0.6, "openness": 0.8, "neuroticism": 0.5},
            gender="male", traits=["shy", "curious", "technical"],
            goals=[],          # his "ask Aisi to teach me" matter is a pursuit chapter (SEED_PURSUITS)
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
        ], weekend=[                              # weekend: sleep in, long afternoons at the park
            (hm(9, 30), "eat", "bakery"),
            (hm(11, 0), "rest", "park"),
            (hm(13, 0), "eat", "cafe"),
            (hm(14, 30), "rest", "park"),
            (hm(17, 0), "rest", "market"),
            (hm(19, 0), "eat", "home_a"),
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
            gender="female", traits=["creative", "focused", "night-owl"],
            goals=[],          # the installation is a pursuit chapter (SEED_PURSUITS), closed on landmark_done
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
        ], weekend=[                              # weekend: no day-job -> a full installation sprint
            (hm(9, 30), "eat", "home_a"),
            (hm(10, 30), "work", "park"),         # into the park early
            (hm(13, 30), "eat", "cafe"),
            (hm(14, 30), "work", "park"),         # long build session
            (hm(19, 0), "eat", "home_a"),
            (hm(20, 0), "work", "park"),          # night-owl finishing touches on the lights
            (hm(23, 30), "sleep", "home_a"),
        ]),
    )
    aisi.memory.add(MemoryItem(0, "The {landmark:installation} in {loc:park} is about a third done.", importance=5))

    # xue (Office Worker) -- inherits Bob: quiet, stressed, weighing whether to quit.
    xue = Agent(
        profile=Profile(
            id="xue", name="雪", age=31, occupation="Office Worker",
            personality={"extraversion": 0.4, "agreeableness": 0.6, "openness": 0.5, "neuroticism": 0.6},
            gender="female", traits=["quiet", "loyal", "stressed"],
            goals=[],          # "quit or not" is a pursuit chapter (SEED_PURSUITS), closed by a transition
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
        ], weekend=[                              # weekend: office empty, sleep in, unwind in town
            (hm(9, 0), "eat", "home_b"),
            (hm(10, 30), "rest", "cafe"),
            (hm(12, 0), "eat", "cafe"),
            (hm(14, 0), "rest", "market"),
            (hm(16, 30), "rest", "park"),
            (hm(19, 0), "eat", "home_b"),
            (hm(22, 30), "sleep", "home_b"),
        ]),
    )
    xue.memory.add(MemoryItem(0, "Argued with the manager last week.", importance=7))
    xue.memory.add(MemoryItem(0, "{loc:cafe} feels like a safe place.", importance=4))

    # long (Repair Technician) -- 2026-08 rewrite: blunt tradesman, the town's second
    # high-mobility character. Takes jobs at home, then criss-crosses town to fix
    # things. The market stall is released (market stays a public space); once the
    # breakdown mechanism lands, the roaming below is swapped for repair dispatch.
    long = Agent(
        profile=Profile(
            id="long", name="瓏", age=52, occupation="Repair Technician",
            personality={"extraversion": 0.5, "agreeableness": 0.25, "openness": 0.5, "neuroticism": 0.3},
            gender="male", traits=["blunt", "self-assured", "abrasive"],
            goals=[{"goal": "Keep the town's machines running -- and get paid what I'm owed", "priority": 0.7}],
            daily_wage=40.0,                      # base from odd jobs; repair fees add on top
            reflection_threshold=35,              # a background character -> reflects less often
        ),
        state=AgentState(location="home_b"),
        routine=_routine([                        # take jobs at home, then out around town
            (hm(6, 30), "eat", "home_b"),
            (hm(8, 0), "work", "home_b"),         # sizing up jobs / prepping tools
            (hm(10, 0), "rest", "market"),
            (hm(11, 30), "rest", "office"),
            (hm(13, 0), "eat", "market"),
            (hm(14, 30), "rest", "park"),
            (hm(16, 0), "rest", "bakery"),
            (hm(17, 30), "rest", "cafe"),
            (hm(19, 0), "eat", "home_b"),
            (hm(21, 30), "rest", "home_b"),
            (hm(22, 30), "sleep", "home_b"),
        ]),
    )

    # kuaizheng (Retired) -- inherits Grace: the quietest resident, home most of the day.
    kuaizheng = Agent(
        profile=Profile(
            id="kuaizheng", name="蒯政", age=61, occupation="Retired",
            personality={"extraversion": 0.3, "agreeableness": 0.8, "openness": 0.5, "neuroticism": 0.3},
            gender="male", traits=["quiet", "kind", "private"],
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

    residents = [jiji, ange, oula, lengyue, azong, xixi, aisi, xue, long, kuaizheng]
    zh = texts.zh_native()
    for a in residents:                       # attach each resident's locked speech style + inclination
        a.profile.speech_style = (SPEECH_STYLE_ZH if zh else SPEECH_STYLE).get(a.id, "")
        if zh:                                # the same settings, in the town's language
            for g in a.profile.goals:
                g["goal"] = GOALS_ZH.get(g["goal"], g["goal"])
            for m in a.memory.items:
                m.text = MEMORIES_ZH.get(m.text, m.text)
        a.profile.romantic_inclination = ROMANTIC_INCLINATION.get(a.id, 0.45)
        a.profile.personality["conscientiousness"] = CONSCIENTIOUSNESS[a.id]
        if a.id in ORIENTATION_BIAS:
            a.profile.orientation_bias = dict(ORIENTATION_BIAS[a.id])
    seed_chapters(residents)                  # the three in-progress matters become pursuit chapters
    return residents


# Initial private matters, tied to the new personalities. Seeded into the
# SecretRegistry at boot; they only surface if an agent trusts someone enough to
# confide (see decision._maybe_confide). Kept in English (internal rule).
# Optional 4th field `about` = the agent this worry concerns; confiding straight to
# that person resolves the secret (the "opening up" was the whole point).
SEED_SECRETS = [
    ("xue", "I've been interviewing at a company in Taipei.", 0.8),                       # the resignation line
    ("ange", "I check Jiji's cafe menu every week to keep my prices lower.", 0.7),        # the quiet price war
    ("jiji", "The cafe barely breaks even; I put on a confident face every day.", 0.7),
    ("aisi", "I'm secretly worried my installation is too ambitious to finish.", 0.5),    # a creator's self-doubt
    ("xixi", "I want to ask Aisi to teach me programming but I freeze every time.", 0.4,  # the admiration line
     "aisi"),                                                                             # -> resolves once he opens up to Aisi
    ("azong", "I've been undercharging clients because I'm afraid to lose them.", 0.55),  # the freelancer's fear
    # long's quiet generosity and kuaizheng's inner life are left for reflection to grow.
]
SECRETS_ZH = {
    "I've been interviewing at a company in Taipei.": "我最近一直在台北的一家公司面試。",
    "I check Jiji's cafe menu every week to keep my prices lower.": "我每週都會去看ㄐㄐ咖啡館的菜單,好讓我的價格壓得比較低。",
    "The cafe barely breaks even; I put on a confident face every day.": "咖啡館只能勉強打平;我每天都裝出一副很有自信的樣子。",
    "I'm secretly worried my installation is too ambitious to finish.": "我偷偷擔心我的裝置野心太大,根本做不完。",
    "I want to ask Aisi to teach me programming but I freeze every time.": "我想請艾斯教我寫程式,可是每次都僵住。",
    "I've been undercharging clients because I'm afraid to lose them.": "我一直把報價壓得太低,因為我怕失去客戶。",
}


def seed_secrets(registry, minute: int = 0) -> None:
    """Plant the initial secrets into a SecretRegistry (fresh start only; a
    resumed run restores its own from the snapshot)."""
    zh = texts.zh_native()
    for owner, text, sensitivity, *rest in SEED_SECRETS:
        s = registry.add(owner, SECRETS_ZH.get(text, text) if zh else text, sensitivity, minute)
        if rest and rest[0]:
            s.about = rest[0]
