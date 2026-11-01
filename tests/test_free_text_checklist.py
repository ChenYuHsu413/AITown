"""The free-text checklist, enforced (docs/free-text-checklist.md).

    python -m unittest tests.test_free_text_checklist -v

Every path that turns model output into something residents say or remember must
carry: the name + gender roster in its prompt, the zh quality gate, the pronoun
gate (gender_ok, which fails loud when the roster is missing), and GATE_DIAG
logging. Twice now a new path shipped without part of this; these tests pin each
activation path to the list. Cases are the real Era-3 Day-2 lines.
"""

from __future__ import annotations

import asyncio
import os
import unittest
import unittest.mock

os.environ["AI_TOWN_LIVE"] = "0"
os.environ["AI_TOWN_LANG"] = "en"
os.environ["AI_TOWN_DB_URL"] = ""

from backend.app.agents import dayplan as dayplan_mod
from backend.app.agents.decision import DecisionEngine
from backend.app.llm.factory import build_router
from backend.app.llm.prompts import builders
from backend.app.llm.providers.base import LLMResult
from backend.app.simulation.engine import DAY_MIN, SimulationEngine
from backend.app.world.world import World
from data.seed import build_agents, build_locations, seed_secrets

ZH = {"AI_TOWN_LANG": "zh-TW"}
BAD_LINE = "跟你說——希希那張紙我沒看過內容,只知道她寫了好幾條,想一條一條問你。"      # Era 3, Day 2 (Xixi is male)
GOOD_LINE = "跟你說——希希那張紙我沒看過內容,只知道他寫了好幾條,想一條一條問你。"
NOW = DAY_MIN + 13 * 60


def make_world():
    router = build_router(live=False)
    world = World(build_locations(), build_agents())
    engine = SimulationEngine(world, DecisionEngine(router))
    seed_secrets(engine.decisions.secrets)
    engine.bootstrap(6 * 60)
    return world, engine


def rejects_before():
    return builders.GATE_REJECTS.get("generation_gender", 0)


class DialogueTurn(unittest.TestCase):
    def setUp(self):
        self.world, self.engine = make_world()
        self.oula, self.aisi = self.world.agents["oula"], self.world.agents["aisi"]
        for a in (self.oula, self.aisi):
            a.state.location = "home_a"
        self.plan = asyncio.run(self.engine.decisions.start_conversation(self.oula, self.aisi, self.world, NOW))

    def gate(self, **parsed):
        r = LLMResult(text="", parsed={"text": GOOD_LINE, **parsed}, provider="fake", model="f")
        return self.engine.decisions._turn_ok(r, self.oula, self.plan)

    def test_prompt_carries_the_roster_and_the_speakers_identity(self):
        with unittest.mock.patch.dict(os.environ, ZH):
            msgs = builders.dialogue_turn_prompt(self.plan.ctx["oula"], [], 1, 8, 2)
        system = msgs[0]["content"]
        self.assertIn("希希 is male", system)
        self.assertIn("You voice ONE character, 歐拉", system)
        self.assertIn("never refers to themselves in the third person", system)

    def test_a_misgendered_line_is_refused_and_counted(self):
        with unittest.mock.patch.dict(os.environ, ZH):
            n = rejects_before()
            self.assertTrue(self.gate())
            self.assertFalse(self.gate(text=BAD_LINE))
            self.assertEqual(builders.GATE_REJECTS["generation_gender"], n + 1)

    def test_calling_a_male_listener_ni_is_refused(self):
        """Era 3, Day 3: Azong to Ange (male), face to face: "妳要不要在門口掛塊手繪的小黑板"."""
        world, engine = self.world, self.engine
        azong, ange = world.agents["azong"], world.agents["ange"]
        azong.state.location = ange.state.location = "market"
        plan = asyncio.run(engine.decisions.start_conversation(azong, ange, world, NOW + 60))
        bad = LLMResult(text="", parsed={"text": "妳要不要在門口掛塊手繪的小黑板,寫上今天新出爐的口味?"}, provider="f", model="f")
        good = LLMResult(text="", parsed={"text": "你要不要在門口掛塊手繪的小黑板,寫上今天新出爐的口味?"}, provider="f", model="f")
        quoted = LLMResult(text="", parsed={"text": "冷月跟我說「妳要小心」,我才想到要提醒你。"}, provider="f", model="f")
        with unittest.mock.patch.dict(os.environ, ZH):
            self.assertFalse(engine.decisions._turn_ok(bad, azong, plan))
            self.assertTrue(engine.decisions._turn_ok(good, azong, plan))
            self.assertTrue(engine.decisions._turn_ok(quoted, azong, plan))
            back = LLMResult(text="", parsed={"text": "妳說得對,成本我回去就列。"}, provider="f", model="f")
            self.assertTrue(engine.decisions._turn_ok(back, ange, plan))      # Ange to Azong (female): fine

    def test_misgendered_news_is_refused_too(self):
        with unittest.mock.patch.dict(os.environ, ZH):
            self.assertFalse(self.gate(tells="希希寫了好幾條問題,她卡在紅外線那條最久。"))

    def test_the_gate_fails_loud_without_a_roster(self):
        with unittest.mock.patch.object(builders, "_ROSTER", []):
            with self.assertRaises(builders.RosterNotLoadedError):
                self.gate()


class MultiNameEarlier(unittest.TestCase):
    """Developer ruling (decided by replay): in a line naming several residents, a
    他/她 is wrong only when no resident named before it in its sentence fits."""

    def setUp(self):
        make_world()

    def test_the_true_errors_are_refused(self):
        from backend.app.agents.decision import gender_ok
        for t in ["跟你說——渡口市場那幾家我都想去!不過我還沒跟瓏約時間,等她回我再定。ㄐㄐ你最近咖啡館那邊還好吧?",
                  "不用這麼客氣。我只是在等歐拉，她說喝完水就會過來，要一起去渡口市場找安哥拿吐司。",
                  "我今天就去找雪,不過你先別急著跟安哥提這些。至於希希,她最近為技術上的問題煩得很。"]:
            self.assertFalse(gender_ok(t), t)

    def test_correct_multi_name_lines_pass(self):
        from backend.app.agents.decision import gender_ok
        for t in ["希希說他會直接過來,艾斯也說她晚點到。",
                  "安哥說冷月今天很忙,她在診所待了一整天。",
                  "冷月跟我說「他不會來了」,我才想到瓏。"]:                # quoted: someone else's words
            self.assertTrue(gender_ok(t), t)

    def test_the_live_false_refusals_now_pass(self):
        """Era-3 lines the nearest-name reading refused: the pronoun points back past
        the nearest name to the subject."""
        from backend.app.agents.decision import gender_ok
        for t in ["歐拉告訴冷月,他明天傍晚和艾斯約了去螢火公園看光影裝置。",
                  "希希想請艾斯教他寫程式,但每次走近她就僵住開不了口。",
                  "冷月向歐拉表示,她心裡有一件別人私下託付的事,讓她放不下。",
                  "阿總跟我講了件新鮮事:蒯政和希希在研究紅外線感測器,說之後會把結果告訴她。"]:
            self.assertTrue(gender_ok(t), t)

    def test_a_named_man_still_cannot_be_she(self):
        from backend.app.agents.decision import gender_ok
        for t in ["我今天其實跑了好幾趟,可心裡一直掛著瓏,她守了約。",
                  "倒是你，碰到希希的時候他有沒有又在攤子前忙得團團轉？你要是不急著走，就陪我坐一會兒，等瓏來了她也認得你。"]:
            self.assertFalse(gender_ok(t), t)


class LocalHint(unittest.TestCase):
    def test_the_turn_prompt_names_the_pronouns_of_the_people_in_play(self):
        world, engine = make_world()
        oula, aisi = world.agents["oula"], world.agents["aisi"]
        oula.state.location = aisi.state.location = "home_a"
        plan = asyncio.run(engine.decisions.start_conversation(oula, aisi, world, NOW))
        hist = [{"speaker": aisi.name, "text": "希希留了一張紙條給我。"}]
        with unittest.mock.patch.dict(os.environ, ZH):
            user = builders.dialogue_turn_prompt(plan.ctx["oula"], hist, 2, 8, 2)[1]["content"]
        hint = next(l for l in user.splitlines() if l.startswith("Pronouns for the people"))
        self.assertIn("希希 = he (他)", hint)
        self.assertIn("艾斯 = she (她)", hint)
        self.assertIn("歐拉 = he (他)", hint)


class MorningPlan(unittest.TestCase):
    def test_prompt_carries_the_roster(self):
        world, engine = make_world()
        xue = world.agents["xue"]
        with unittest.mock.patch.dict(os.environ, ZH):
            msgs = builders.day_plan_prompt(xue, dayplan_mod.material(xue, world, engine.decisions, 2))
        self.assertIn("希希 is male", msgs[0]["content"])

    def test_a_misgendered_intent_is_refused(self):
        world, engine = make_world()
        verdicts = []

        async def gen(**kw):
            for intent in ("今天想去找希希,聽聽她最近在忙什麼。", "今天想去找希希,聽聽他最近在忙什麼。"):
                verdicts.append(kw["validate"](LLMResult(text="", parsed={"intent": intent}, provider="f", model="f")))
            return LLMResult(text="", parsed={"intent": "今天想去找希希,聽聽他最近在忙什麼。"}, provider="f", model="f")
        with unittest.mock.patch.dict(os.environ, ZH), \
                unittest.mock.patch.object(engine.decisions.router, "generate", gen):
            asyncio.run(engine.decisions.plan_day(world.agents["xue"], world, 2, DAY_MIN))
        self.assertEqual(verdicts, [False, True])

    def test_a_misgendered_action_is_dropped_and_counted(self):
        world, engine = make_world()
        with unittest.mock.patch.dict(os.environ, ZH):
            n = rejects_before()
            p = dayplan_mod.validate({"intent": "x", "actions": [
                {"hour": 15, "place": "Firefly Park", "what": "去找希希,想約她聊聊", "leaves": "", "cost": 0}]},
                world.agents["aisi"], world, 2)
        self.assertEqual(p["actions"], [])
        self.assertTrue(p["dropped"][0].startswith("action: wrong pronoun"))
        self.assertEqual(builders.GATE_REJECTS["generation_gender"], n + 1)


class RumorPaths(unittest.TestCase):
    def test_a_misgendered_leak_falls_back(self):
        world, engine = make_world()
        d = engine.decisions
        sec = next(iter(d.secrets.secrets.values()))
        owner = world.agents[sec.owner]
        b, c = [a for a in world.agents.values() if a.id != owner.id][:2]
        d.secrets.record_confide(sec.id, b.id, NOW)
        name = owner.name
        wrong = "他" if world.agents[sec.owner].profile.gender == "female" else "她"

        async def gen(**kw):
            return LLMResult(text="", parsed={"text": f"{name}好像在隱瞞什麼,{wrong}最近很不對勁。", "sentiment": -0.4},
                             provider="f", model="f")
        with unittest.mock.patch.dict(os.environ, ZH),                 unittest.mock.patch("backend.app.agents.decision.LEAK_MAX_P", 1.0),                 unittest.mock.patch.object(b.profile, "traits", ["gossipy"]),                 unittest.mock.patch.object(d.router, "generate", gen),                 unittest.mock.patch("random.Random.random", lambda self: 0.0):
            out = asyncio.run(d._maybe_leak(b, c, world, NOW))
        self.assertIsNotNone(out)
        self.assertNotIn(wrong, out["text"])


class GateRetrySticks(unittest.TestCase):
    """Developer ruling: a quality-gate refusal is retried on the same provider and
    never handed down the fallback chain to a weaker model."""

    def _router(self, *names):
        from backend.app.llm.providers.base import LLMProvider
        from backend.app.llm.router import LLMRouter
        calls = []

        class P(LLMProvider):
            def __init__(self, name):
                self.name = name

            async def generate(self, messages, **kw):
                calls.append(self.name)
                return LLMResult(text="bad" if self.name == "claude" else "good", provider=self.name)

        chain = [P(n) for n in names]
        return LLMRouter(tiers={"cheap": chain, "normal": chain, "smart": chain}), calls

    def test_a_refused_line_waits_instead_of_descending(self):
        from backend.app.llm.router import GATE_RETRIES, ProvidersExhausted
        router, calls = self._router("claude", "kimi", "mock")
        with self.assertRaises(ProvidersExhausted):
            asyncio.run(router.generate(task="dialogue_turn", messages=[], no_floor=True,
                                        validate=lambda r: r.text == "good"))
        self.assertEqual(calls, ["claude"] * (1 + GATE_RETRIES))

    def test_only_the_floor_remains_for_a_caller_that_keeps_it(self):
        router, calls = self._router("claude", "kimi", "mock")
        out = asyncio.run(router.generate(task="dialogue_turn", messages=[],
                                          validate=lambda r: r.text == "good"))
        self.assertEqual(out.provider, "mock")
        self.assertNotIn("kimi", calls)

    def test_a_hard_error_still_falls_through(self):
        from backend.app.llm.providers.base import LLMProvider
        from backend.app.llm.router import LLMRouter

        class Down(LLMProvider):
            name = "claude"

            async def generate(self, messages, **kw):
                raise TimeoutError()

        class Up(LLMProvider):
            name = "kimi"

            async def generate(self, messages, **kw):
                return LLMResult(text="good", provider="kimi")

        chain = [Down(), Up()]
        router = LLMRouter(tiers={"cheap": chain, "normal": chain, "smart": chain})
        out = asyncio.run(router.generate(task="dialogue_turn", messages=[]))
        self.assertEqual(out.provider, "kimi")


if __name__ == "__main__":
    unittest.main()
