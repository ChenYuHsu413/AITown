"""Turn-by-turn dialogue (活化 phase A).

    python -m unittest tests.test_dialogue_turns -v

One LLM call per line, each from inside ONE speaker: the speaker's own context
only, the shared transcript, consequences read off structured fields by rules.
"""

from __future__ import annotations

import asyncio
import os
import unittest
import unittest.mock

os.environ["AI_TOWN_LIVE"] = "0"
os.environ["AI_TOWN_LANG"] = "en"
os.environ["AI_TOWN_DB_URL"] = ""

from backend.app import pacing
from backend.app.agents import texts
from backend.app.agents import wishes as wishes_mod
from backend.app.agents.decision import DecisionEngine
from backend.app.llm.factory import build_router
from backend.app.llm.providers.base import LLMResult
from backend.app.simulation.engine import DAY_MIN, SimulationEngine
from backend.app.world.world import World
from data.seed import build_agents, build_locations, seed_secrets

NOW = 2 * DAY_MIN + 10 * 60     # day 3, 10:00


def make_world():
    router = build_router(live=False)
    world = World(build_locations(), build_agents())
    engine = SimulationEngine(world, DecisionEngine(router))
    seed_secrets(engine.decisions.secrets)
    engine.bootstrap(6 * 60)
    return world, engine


def put_at_park(world, *ids):
    for i in ids:
        world.agents[i].state.location = "park"


def scripted(lines):
    """A router.generate stand-in that serves ``lines`` in order (one per call) and
    records each call's task + messages."""
    calls = []

    async def gen(**kw):
        calls.append(kw)
        parsed = lines[min(len(calls) - 1, len(lines) - 1)]
        res = LLMResult(text="", parsed=parsed, provider="fake", model="fake-1")
        assert kw["validate"](res), parsed
        return res
    return gen, calls


def line(text, **kw):
    return {"text": text, "end": False, "sentiment": 0.6, "trust_signal": 0.1, "conflict_signal": 0.0,
            "tells": "", "plan": None, "storm_off": False, **kw}


class MockTurns(unittest.TestCase):
    def test_mock_exchange_is_multi_turn_and_deterministic(self):
        def run():
            world, engine = make_world()
            put_at_park(world, "jiji", "lengyue")
            a, b = world.agents["jiji"], world.agents["lengyue"]

            async def go():
                plan = await engine.decisions.start_conversation(a, b, world, NOW)
                return plan, await engine.decisions.generate_conversation(plan)
            return asyncio.run(go())
        plan1, res1 = run()
        plan2, res2 = run()
        turns = res1.parsed["turns"]
        self.assertGreaterEqual(len(turns), 2)
        self.assertLessEqual(len(turns), pacing.DIALOGUE_MAX_TURNS)
        self.assertEqual([t["text"] for t in turns], [t["text"] for t in res2.parsed["turns"]])
        self.assertEqual([t["speaker_id"] for t in turns][:2], ["jiji", "lengyue"])   # alternating
        self.assertTrue(turns[-1]["end"])
        self.assertEqual(res1.provider, "mock")

    def test_confrontation_runs_at_least_the_intimate_minimum_and_gives_a_verdict(self):
        world, engine = make_world()
        put_at_park(world, "lengyue", "oula")
        a, b = world.agents["lengyue"], world.agents["oula"]

        async def go():
            plan = await engine.decisions.start_conversation(
                a, b, world, NOW, confront_text="people say you have been talking", confront_rumor_id="r1")
            return plan, await engine.decisions.generate_conversation(plan)
        plan, res = asyncio.run(go())
        self.assertEqual(plan.task, "dialogue_turn_tense")
        self.assertGreaterEqual(len(res.parsed["turns"]), pacing.DIALOGUE_INTIMATE_MIN_TURNS)
        self.assertIsInstance(res.parsed["admitted"], bool)
        verdicts = [t["admitted"] for t in res.parsed["turns"] if t["speaker_id"] == "oula"]
        self.assertTrue(any(v is not None for v in verdicts))


class Asymmetry(unittest.TestCase):
    def test_each_seat_sees_only_its_own_inner_life(self):
        world, engine = make_world()
        put_at_park(world, "jiji", "lengyue")
        a, b = world.agents["jiji"], world.agents["lengyue"]
        sec = engine.decisions.secrets.active_secrets_of("jiji")
        plan = asyncio.run(engine.decisions.start_conversation(a, b, world, NOW))
        mine, theirs = plan.preview("jiji"), plan.preview("lengyue")
        for s in sec:
            if s.id not in {c["secret_id"] for c in plan.confided}:
                self.assertIn(s.text, mine)
            self.assertNotIn(s.text, theirs)
        self.assertEqual(mine.count("Traits:"), 1)
        self.assertEqual(theirs.count("Traits:"), 1)

    def test_wish_bearing_talk_goes_to_the_smart_tier(self):
        world, engine = make_world()
        put_at_park(world, "jiji", "lengyue")
        a, b = world.agents["jiji"], world.agents["lengyue"]
        engine.decisions.secrets.secrets.clear()                    # no confide bar in reach
        plan = asyncio.run(engine.decisions.start_conversation(a, b, world, NOW))
        self.assertEqual(plan.task, "dialogue_turn")
        clean, errs = wishes_mod.validate_seed(
            {"scale": "minor", "title": "Know her", "statement": "I want to really know Lengyue.",
             "requirements": [{"kind": "talk_count", "target": "lengyue", "threshold": 3}]},
            a, world, NOW // DAY_MIN + 1)
        self.assertIsNotNone(clean, errs)
        wishes_mod.install(a, clean, NOW // DAY_MIN + 1)
        a.state.busy_until = b.state.busy_until = 0
        plan2 = asyncio.run(engine.decisions.start_conversation(a, b, world, NOW + 30))
        self.assertEqual(plan2.task, "dialogue_turn_tense")
        self.assertIn("I want to really know", plan2.preview("jiji"))       # own wish, full text
        self.assertNotIn("I want to really know", plan2.preview("lengyue"))


class Attribution(unittest.TestCase):
    """Era-3 report: a line seemed to come from a bystander. Each published line must
    name its real speaker and partner and the place the talk happened."""

    def test_every_line_carries_its_speaker_partner_and_place(self):
        world, engine = make_world()
        for i in ("lengyue", "xixi", "jiji"):                      # jiji: a bystander at the same place
            world.agents[i].state.location = "cafe"
            world.agents[i].state.busy_until = 0
            world.agents[i].state.current_action = "idle"
        engine.decisions.secrets.secrets.clear()
        lengyue, xixi = world.agents["lengyue"], world.agents["xixi"]
        texts_by_seat = {"lengyue": ["冷月一", "冷月二", "冷月三"], "xixi": ["希希一", "希希二", "希希三"]}
        n = {"lengyue": 0, "xixi": 0}

        async def gen(**kw):
            who = kw["agent_id"]
            t = texts_by_seat[who][n[who]]; n[who] += 1
            res = LLMResult(text="", parsed=line(t, end=(n["xixi"] == 3)), provider="fake", model="f")
            return res

        async def go():
            engine.now = NOW
            with unittest.mock.patch.object(engine.decisions.router, "generate", gen):
                await engine._initiate_conversation(lengyue, "xixi")
                lengyue.state.location = "park"                    # a stale/drifted position at settlement
                await asyncio.gather(*list(engine._tasks))
        asyncio.run(go())
        says = [e for e in engine.bus.events if e.verb == "say"]
        self.assertEqual([e.actor for e in says], ["lengyue", "xixi"] * 3)
        self.assertEqual([e.target for e in says], ["xixi", "lengyue"] * 3)
        self.assertEqual({e.location for e in says}, {"cafe"})
        for e in says:                                             # each line is that speaker's own words
            self.assertTrue(e.text.startswith({"lengyue": "冷月", "xixi": "希希"}[e.actor]), e.text)
        self.assertNotIn("jiji", {e.actor for e in says} | {e.target for e in says})
        self.assertEqual([e.minute for e in says], list(range(NOW, NOW + 6)))


class Consequences(unittest.TestCase):
    def _talk(self, lines):
        world, engine = make_world()
        put_at_park(world, "jiji", "lengyue")
        engine.decisions.secrets.secrets.clear()
        a, b = world.agents["jiji"], world.agents["lengyue"]
        gen, calls = scripted(lines)

        async def go():
            plan = await engine.decisions.start_conversation(a, b, world, NOW)
            with unittest.mock.patch.object(engine.decisions.router, "generate", gen):
                res = await engine.decisions.generate_conversation(plan)
            return plan, res, engine.decisions.settle_conversation(plan, world, res)
        plan, res, settled = asyncio.run(go())
        return world, engine, plan, res, settled, calls

    def test_one_call_per_line_and_the_model_may_end_it(self):
        _, _, plan, res, settled, calls = self._talk([line("Morning."), line("Morning to you.", end=True)])
        self.assertEqual(len(calls), 2)
        self.assertEqual([c["agent_id"] for c in calls], ["jiji", "lengyue"])
        jiji = plan.a.name
        self.assertIn(f"{jiji}: Morning.", calls[1]["messages"][-1]["content"])   # the transcript so far
        self.assertEqual(len(settled[0]), 2)

    def test_the_hard_cap_holds(self):
        _, _, plan, res, _, calls = self._talk([line("Go on.")])
        self.assertEqual(len(calls), pacing.DIALOGUE_MAX_TURNS)

    def test_news_becomes_the_listeners_memory(self):
        world, *_ = self._talk([line("The bakery is closing early this week.",
                                     tells="Jiji says the bakery is closing early this week."),
                                line("Good to know.", end=True)])
        mem = [m.text for m in world.agents["lengyue"].memory.items]
        self.assertIn(texts.T("told_me", teller="jiji", text="Jiji says the bakery is closing early this week."), mem)
        self.assertFalse(any("closing early" in m.text for m in world.agents["jiji"].memory.items
                             if m.kind == "conversation" and "told me" in m.text))

    def test_the_same_news_restated_lands_once_per_speaker(self):
        world, *_ = self._talk([line("I've been thinking about the bakery.", tells="Jiji is worried about the bakery."),
                                line("Oh?"),
                                line("It may close early.", tells="Jiji says the bakery may close early this week."),
                                line("I see.", end=True)])
        told = [m.text for m in world.agents["lengyue"].memory.items if "told me" in m.text]
        self.assertEqual(told, [texts.T("told_me", teller="jiji", text="Jiji says the bakery may close early this week.")])

    def test_a_promise_needs_both_voices_and_becomes_an_appointment(self):
        world, engine, plan, res, settled, _ = self._talk([
            line("Coffee tomorrow at the cafe?", plan={"place": "Tide Cafe", "day": "tomorrow", "hour": 15}),
            line("Sure, three o'clock at the cafe.", end=True, plan={"place": "cafe", "day": "tomorrow", "hour": 15}),
        ])
        beats = settled[5]
        self.assertEqual([b["verb"] for b in beats], ["promise_made"])
        a, b = world.agents["jiji"], world.agents["lengyue"]
        self.assertEqual(a.state.pending_meetup["partner"], "lengyue")
        self.assertTrue(a.state.pending_meetup["promised"])
        self.assertEqual(a.state.pending_meetup["minute"], (NOW // DAY_MIN + 1) * DAY_MIN + 15 * 60)
        self.assertEqual(b.state.pending_meetup["partner"], "jiji")

    def test_a_one_sided_proposal_is_not_a_promise(self):
        world, _, _, _, settled, _ = self._talk([
            line("Coffee tomorrow?", plan={"place": "cafe", "day": "tomorrow", "hour": 15}),
            line("We'll see.", end=True)])
        self.assertEqual(settled[5], [])
        self.assertIsNone(world.agents["jiji"].state.pending_meetup)

    def test_storming_off_ends_it_and_sours_both(self):
        world, engine = make_world()
        a0 = world.agents["jiji"].rel("lengyue").friendship
        world, engine, plan, res, settled, calls = self._talk([
            line("Why would you say that?", conflict_signal=0.8, sentiment=0.1),
            line("I'm done here.", storm_off=True, conflict_signal=0.9, sentiment=0.0),
            line("never reached")])
        self.assertEqual(len(calls), 2)
        self.assertEqual([b["verb"] for b in settled[5]], ["stormed_off"])
        self.assertEqual(settled[5][0]["actor"], "lengyue")
        self.assertLess(world.agents["jiji"].rel("lengyue").friendship, a0)

    def test_each_side_feels_its_own_signals(self):
        world, *_ = self._talk([line("Lovely to see you!", sentiment=1.0),
                                line("Hm.", sentiment=0.0, end=True)])
        ja, lj = world.agents["jiji"].rel("lengyue"), world.agents["lengyue"].rel("jiji")
        w0, _ = make_world()
        self.assertGreater(ja.friendship, w0.agents["jiji"].rel("lengyue").friendship)
        self.assertLess(lj.friendship, w0.agents["lengyue"].rel("jiji").friendship)


class AnswersLand(unittest.TestCase):
    """Era 3, D6: Azong asked Oula where the registered letter went; he answered,
    then talked about Xixi's note, and only the note survived as her memory."""

    def run_talk(self, lines, azong_wish=True):
        from backend.app.agents import wishes as wishes_mod
        world, engine = make_world()
        for i in ("azong", "oula"):
            world.agents[i].state.location = "cafe"
        engine.decisions.secrets.secrets.clear()
        azong, oula = world.agents["azong"], world.agents["oula"]
        if azong_wish:
            clean, errs = wishes_mod.validate_seed(
                {"scale": "minor", "title": "把那封掛號信的下落問清楚",
                 "statement": "我想親自去問問歐拉,把那封掛號信到底交給了誰弄個明白。",
                 "requirements": [{"kind": "talk_count", "target": "oula", "threshold": 3}]},
                azong, world, NOW // DAY_MIN + 1)
            self.assertIsNotNone(clean, errs)
            wishes_mod.install(azong, clean, NOW // DAY_MIN + 1)
        gen, _ = scripted(lines)

        async def go():
            plan = await engine.decisions.start_conversation(oula, azong, world, NOW)
            with unittest.mock.patch.object(engine.decisions.router, "generate", gen):
                res = await engine.decisions.generate_conversation(plan)
            engine.decisions.settle_conversation(plan, world, res)
        with unittest.mock.patch.dict(os.environ, {"AI_TOWN_LANG": "zh-TW"}):
            asyncio.run(go())
        return [m for m in azong.memory.items if "跟我說" in m.text or "told me" in m.text]

    AZONG_CASE = [
        line("早啊阿總,我剛送完一趟信。"),
        line("早啊歐拉。你那封掛號是寄給艾斯的吧?"),
        line("對啊,是艾斯的掛號,她一拿到就笑得合不攏嘴。",
             tells="歐拉說寄給艾斯的那封掛號信已經送到她手上了。"),
        line("那就好。"),
        line("跟你說——希希那張紙條還貼在公園,我想找他問問。",
             tells="歐拉想找到希希,聽他說說那張紙條背後的事。", end=True),
    ]

    def test_the_answer_to_her_own_question_lands_and_weighs_more(self):
        told = self.run_talk(self.AZONG_CASE)
        texts_ = [m.text for m in told]
        self.assertTrue(any("掛號信已經送到" in t for t in texts_), texts_)
        self.assertTrue(any("紙條" in t for t in texts_), texts_)          # the other news lands too
        ans = next(m for m in told if "掛號信已經送到" in m.text)
        self.assertEqual(ans.importance, 5)                               # bears on her active wish
        self.assertIn("answer", ans.tags)

    def test_an_answer_unrelated_to_her_aims_lands_at_the_usual_weight(self):
        told = self.run_talk(self.AZONG_CASE, azong_wish=False)
        ans = next(m for m in told if "掛號信已經送到" in m.text)
        self.assertEqual(ans.importance, 4)

    def test_answers_land_beyond_the_cap_but_unasked_news_stays_capped(self):
        lines = [line("早啊。"), line("你今天去了哪?"), line("市場。", tells="歐拉今天去了渡口市場。")]
        for news in ["瓏在修長椅", "安哥換了新烤箱", "雪買了一盆花"]:
            lines += [line("嗯。"), line(f"還有,{news}。", tells=f"歐拉說{news}。")]
        lines[-1]["end"] = True
        with unittest.mock.patch.object(pacing, "DIALOGUE_MAX_TURNS", 12):
            told = self.run_talk(lines, azong_wish=False)
        self.assertEqual(len(told), 1 + pacing.DIALOGUE_TELLS_MAX)        # the answer + capped others
        self.assertTrue(any("渡口市場" in m.text for m in told))


class PromiseDays(unittest.TestCase):
    """Era-3 report: a meeting agreed on D3 for "tomorrow 15:00" was still "tomorrow"
    in both residents' memories on D4. The day is stored ({day:N}) and the word is
    worked out when the memory is read."""

    def promise(self, now, day_word, hour):
        from backend.app.agents.decision import _resolve_placeholders
        world, engine = make_world()
        for i in ("jiji", "lengyue"):
            world.agents[i].state.location = "cafe"
        engine.decisions.secrets.secrets.clear()
        a, b = world.agents["jiji"], world.agents["lengyue"]
        lines = [line("Meet then?", plan={"place": "cafe", "day": day_word, "hour": hour}),
                 line("Yes.", end=True, plan={"place": "cafe", "day": day_word, "hour": hour})]
        gen, _ = scripted(lines)

        async def go():
            plan = await engine.decisions.start_conversation(a, b, world, now)
            with unittest.mock.patch.object(engine.decisions.router, "generate", gen):
                res = await engine.decisions.generate_conversation(plan)
            engine.decisions.settle_conversation(plan, world, res)
        asyncio.run(go())
        mem = next(m.text for m in a.memory.items if "promise" in (m.tags or []))
        return a.state.pending_meetup["minute"], mem, (lambda at: _resolve_placeholders(mem, world, at))

    def test_today_three_pm_is_today(self):
        minute, mem, read = self.promise(NOW, "today", 15)                # day 3, 10:00
        self.assertEqual(minute, (NOW // DAY_MIN) * DAY_MIN + 15 * 60)
        self.assertIn("{day:3}", mem)
        self.assertIn("today", read(NOW))
        self.assertIn("yesterday", read(NOW + DAY_MIN))

    def test_tomorrow_is_the_next_day_and_reads_as_today_once_it_comes(self):
        minute, mem, read = self.promise(NOW, "tomorrow", 15)
        self.assertEqual(minute, (NOW // DAY_MIN + 1) * DAY_MIN + 15 * 60)
        self.assertIn("{day:4}", mem)
        self.assertIn("tomorrow", read(NOW))
        self.assertIn("today", read((NOW // DAY_MIN + 1) * DAY_MIN + 8 * 60))   # the D4 bug
        self.assertNotIn("tomorrow", read((NOW // DAY_MIN + 1) * DAY_MIN + 8 * 60))

    def test_across_midnight(self):
        late = (NOW // DAY_MIN) * DAY_MIN + 23 * 60 + 50                  # day 3, 23:50
        minute, mem, read = self.promise(late, "tomorrow", 9)
        self.assertEqual(minute, (late // DAY_MIN + 1) * DAY_MIN + 9 * 60)
        self.assertIn("tomorrow", read(late))
        self.assertIn("today", read(late + 20))                           # 00:10 on day 4

    def test_unknown_reading_moment_is_an_absolute_day(self):
        from backend.app.agents.decision import _resolve_placeholders
        world, _ = make_world()
        self.assertEqual(_resolve_placeholders("{day:4}", world), "on Day 4")


class PromisesKeptAndMissed(unittest.TestCase):
    def test_missed_promise_is_remembered_by_both_and_raised_once(self):
        world, engine = make_world()
        a, b = world.agents["jiji"], world.agents["lengyue"]
        appt = {"location": "cafe", "minute": NOW, "promised": True}
        a.state.pending_meetup = {**appt, "partner": "lengyue"}
        b.state.pending_meetup = {**appt, "partner": "jiji"}
        late = NOW + 24 * 60
        engine.decisions._resume_meetup(a, world, late)
        engine.decisions._resume_meetup(b, world, late)
        out = engine.decisions.drain_outbox()
        self.assertEqual([o["verb"] for o in out], ["promise_missed"])
        for x, other in ((a, "lengyue"), (b, "jiji")):
            self.assertIsNone(x.state.pending_meetup)
            self.assertIn(texts.T("promise_missed", other=other, loc="cafe"), [m.text for m in x.memory.items])

    def test_an_ordinary_lapsed_meetup_stays_quiet(self):
        world, engine = make_world()
        a = world.agents["jiji"]
        a.state.pending_meetup = {"location": "cafe", "minute": NOW, "partner": "lengyue"}
        engine.decisions._resume_meetup(a, world, NOW + 24 * 60)
        self.assertEqual(engine.decisions.drain_outbox(), [])


class AgedLandmark(unittest.TestCase):
    def test_a_finished_thing_stops_being_news(self):
        world, engine = make_world()
        lm = world.locations["park"].landmarks[0]
        lm["state"], lm["progress"] = "completed", 1.0
        name = texts.landmark_name(lm)
        lm["completed_minute"] = NOW
        d = engine.decisions._nearby_landmark
        self.assertEqual(d(world, "park", NOW + 60), texts.T("aged_today", name=name))
        self.assertEqual(d(world, "park", NOW + 3 * DAY_MIN), texts.T("aged_fresh", name=name))
        self.assertEqual(d(world, "park", NOW + pacing.FRESH_DAYS * DAY_MIN), name)
        del lm["completed_minute"]                                       # an old snapshot: reads as old
        self.assertEqual(d(world, "park", NOW), name)
        self.assertNotIn("now complete", d(world, "park", NOW))


if __name__ == "__main__":
    unittest.main()
