"""Free actions and public objects (活化 phase C).

    python -m unittest tests.test_free_actions -v
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
from backend.app.agents import actions as actions_mod
from backend.app.agents import dayplan as dayplan_mod
from backend.app.agents import texts
from backend.app.agents.decision import DecisionEngine
from backend.app.llm.factory import build_router
from backend.app.simulation import snapshot as snapshot_mod
from backend.app.simulation.engine import DAY_MIN, SimulationEngine
from backend.app.world.world import World
from data.seed import build_agents, build_locations, seed_secrets

DAY = 2
DAY0 = (DAY - 1) * DAY_MIN


def make_world():
    router = build_router(live=False)
    world = World(build_locations(), build_agents())
    engine = SimulationEngine(world, DecisionEngine(router))
    seed_secrets(engine.decisions.secrets)
    engine.bootstrap(6 * 60)
    return world, engine


def act(hour=15, place="Firefly Park", what="sketch the pond", leaves="a chalk sketch by the pond", cost=0):
    return {"hour": hour, "place": place, "what": what, "leaves": leaves, "cost": cost}


def plan_for(agent, world, *acts, **extra):
    return dayplan_mod.validate({"intent": "A small thing today.", "actions": list(acts), **extra},
                                agent, world, DAY)


def decide(engine, world, agent, now):
    obs = world.observe(agent, since_minute=now - 1, now=now)
    return asyncio.run(engine.decisions.decide(agent, world, obs, now))


class PlanSide(unittest.TestCase):
    def setUp(self):
        self.world, self.engine = make_world()
        self.kz = self.world.agents["kuaizheng"]          # retired: free afternoons

    def test_a_good_action_is_kept_and_holds_its_hour(self):
        p = plan_for(self.kz, self.world, act())
        self.assertEqual(len(p["actions"]), 1)
        self.assertEqual(p["actions"][0]["location"], "park")
        self.assertIn({"from": 900, "to": 960, "location": "park", "action": "free", "act": 0}, p["slots"])

    def test_the_contract_refuses_another_persons_home(self):
        p = plan_for(self.kz, self.world, act(hour=16, place="Rainlisten House"))    # someone else's home
        self.assertEqual(p["actions"], [])
        p = plan_for(self.kz, self.world, act(what="leave a note for Lengyue"))     # naming is fine
        self.assertEqual(p["actions"][0]["recipient"], "lengyue")

    def test_control_is_refused_but_mention_is_not(self):
        """Day 43-44 regression: these were refused only for naming someone."""
        with unittest.mock.patch.dict(os.environ, {"AI_TOWN_LANG": "zh-TW"}):
            kz = self.kz
            ok = [
                act(place="Hearth Bakery", what="試做希希建議的新配方小樣,記下口感與火候", leaves=""),     # mention
                act(place="Riverrun Office", what="在辦公室門口等蒯政,一起走進去", leaves=""),             # self-positioning
                act(hour=16, place="Riverrun Office", what="寫下想跟冷月說的話,整理成一張小紙條", leaves=""),
            ]
            for a in ok:
                p = plan_for(self.world.agents["azong"], self.world, a)
                self.assertEqual(len(p["actions"]), 1, (a["what"], p["dropped"]))
            blocked = [
                "和冷月和好", "讓瓏答應一起去市集", "說服歐拉留下來", "冷月答應了我的邀約",
                "抱一抱希希", "修好歐拉的腳踏車", "拿走安哥的麵包籃",
            ]
            for what in blocked:
                p = plan_for(kz, self.world, act(what=what, leaves=""))
                self.assertEqual(p["actions"], [], what)
                self.assertTrue(p["dropped"][0].startswith("action: controls someone else"), what)

    def test_a_thing_left_for_someone_is_a_delivery_they_find_on_their_own_turn(self):
        with unittest.mock.patch.dict(os.environ, {"AI_TOWN_LANG": "zh-TW"}):
            xixi = self.world.agents["xixi"]
            p = plan_for(xixi, self.world, act(place="Tide Cafe", what="在角落的桌邊寫下一張小小的留言卡,祝ㄐㄐ的咖啡館生意興隆",
                                               leaves="一張寫給ㄐㄐ的留言卡"))
            a = p["actions"][0]
            self.assertEqual((a["recipient"], a["leaves"]["item"]), ("jiji", "一張寫給ㄐㄐ的留言卡"))   # for the owner: may stay
            xixi.state.day_plan = p
            xixi.state.location, xixi.state.current_action = "cafe", "rest"
            decide(self.engine, self.world, xixi, DAY0 + 15 * 60 + 2)
            obj = self.world.objects[-1]
            self.assertEqual(obj["recipient"], "jiji")
            beat = [b for b in self.engine.decisions.outbox if b["verb"] == "made_object"][0]
            self.assertEqual(beat["target"], "jiji")
            jiji = self.world.agents["jiji"]
            self.assertFalse(any("留言卡" in m.text for m in jiji.memory.items))          # not until he comes by
            jiji.state.location = "cafe"
            actions_mod.notice_objects(jiji, self.world, DAY0 + 16 * 60)
            found = [m for m in jiji.memory.items if "留言卡" in m.text]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].importance, 5)
            self.assertIn("delivery", found[0].tags)
            self.assertEqual(jiji.rel("xixi").friendship, self.world.agents["jiji"].rel("xixi").friendship)  # no reaction applied

    def test_limits(self):
        p = plan_for(self.kz, self.world, act(cost=pacing.FREE_ACTION_MAX_COST + 1), act(hour=22),
                     act(hour=10))
        self.assertEqual(p["dropped"][:2], ["action: too costly", "action: sleep time"])
        self.assertEqual(len(p["actions"]), 0)                    # the cap is 2: the third is never read
        p = plan_for(self.kz, self.world, act(hour=10), act(hour=11), act(hour=12))
        self.assertEqual(len(p["actions"]), pacing.FREE_ACTIONS_PER_DAY)

    def test_a_doing_inside_a_stretch_at_the_same_place_is_fine(self):
        p = plan_for(self.kz, self.world, act(hour=15),
                     slots=[{"from": 14, "to": 17, "place": "Firefly Park", "doing": "visit"}])
        self.assertEqual(len(p["actions"]), 1)
        self.assertTrue(p["actions"][0]["within"])
        self.assertEqual([s["action"] for s in p["slots"]], ["idle"])          # no extra slot
        p = plan_for(self.kz, self.world, act(hour=15),
                     slots=[{"from": 14, "to": 17, "place": "Tide Cafe", "doing": "visit"}])
        self.assertEqual(p["actions"], [])                                     # elsewhere: a real clash

    def test_a_doing_inside_a_stretch_lands_when_its_hour_comes(self):
        p = plan_for(self.kz, self.world, act(hour=15),
                     slots=[{"from": 14, "to": 17, "place": "Firefly Park", "doing": "visit"}])
        self.kz.state.day_plan = p
        self.kz.state.location, self.kz.state.current_action = "park", "idle"
        decide(self.engine, self.world, self.kz, DAY0 + 14 * 60 + 30)
        self.assertEqual(self.world.objects, [])                               # not its hour yet
        decide(self.engine, self.world, self.kz, DAY0 + 15 * 60 + 5)
        self.assertEqual(p["actions"][0]["state"], "done")
        self.assertEqual(len(self.world.objects), 1)

    def test_nothing_may_be_left_in_someone_elses_shop(self):
        p = plan_for(self.kz, self.world, act(place="Tide Cafe", what="read a whole book", leaves="a bookmark"))
        self.assertIsNone(p["actions"][0]["leaves"])


class Execution(unittest.TestCase):
    def setUp(self):
        self.world, self.engine = make_world()
        self.kz = self.world.agents["kuaizheng"]
        self.kz.state.day_plan = plan_for(self.kz, self.world, act(cost=5))
        self.kz.state.location, self.kz.state.current_action = "park", "rest"
        self.money0 = self.kz.state.money

    def test_it_lands_with_a_memory_a_deduction_and_a_public_object(self):
        d = decide(self.engine, self.world, self.kz, DAY0 + 15 * 60 + 2)
        self.assertEqual(d.action, "idle")
        self.assertEqual(self.kz.state.money, self.money0 - 5)
        self.assertEqual(self.kz.state.day_plan["actions"][0]["state"], "done")
        obj = self.world.objects[-1]
        self.assertEqual((obj["location"], obj["creator"], obj["description"]),
                         ("park", "kuaizheng", "a chalk sketch by the pond"))
        mem = [m.text for m in self.kz.memory.items]
        self.assertIn(texts.T("action_self", loc="park", what="sketch the pond"), mem)
        verbs = [b["verb"] for b in self.engine.decisions.outbox]
        self.assertEqual(verbs, ["made_object", "__object__"])
        # once landed, it does not land again
        decide(self.engine, self.world, self.kz, DAY0 + 15 * 60 + 30)
        self.assertEqual(len(self.world.objects), 1)

    def test_the_engine_publishes_it_and_writes_the_ledger(self):
        written = []
        self.engine.on_public_object = written.append
        self.engine.scheduler = type(self.engine.scheduler)()     # only Kuaizheng is due
        self.engine._last_day = DAY0 // DAY_MIN                    # the day is already settled
        self.engine.scheduler.schedule("kuaizheng", DAY0 + 15 * 60 + 2)
        asyncio.run(self.engine.tick())
        self.assertEqual([o["description"] for o in written], ["a chalk sketch by the pond"])
        ev = [e for e in self.engine.bus.events if e.verb == "made_object"]
        self.assertEqual((ev[-1].actor, ev[-1].location, ev[-1].obj["item"]),
                         ("kuaizheng", "park", "a chalk sketch by the pond"))
        self.assertEqual(ev[-1].text, "")                          # no words written on a sketch
        self.assertEqual(ev[-1].tier, "notable")
        self.assertEqual(self.engine.action_stats["made_object"], 1)

    def test_no_money_means_a_failure_beat_and_memory(self):
        self.kz.state.money = 1
        decide(self.engine, self.world, self.kz, DAY0 + 15 * 60 + 2)
        self.assertEqual(self.world.objects, [])
        beat = self.engine.decisions.outbox[-1]
        self.assertEqual((beat["verb"], beat["detail"]), ("action_failed", texts.T("fail_money")))
        self.assertIn(texts.T("action_failed", what="sketch the pond", loc="park", reason=texts.T("fail_money")),
                      [m.text for m in self.kz.memory.items])

    def test_rain_keeps_it_off_the_park(self):
        self.world.set_effect("rain", "", DAY0 + DAY_MIN)
        decide(self.engine, self.world, self.kz, DAY0 + 15 * 60 + 2)
        self.assertEqual(self.engine.decisions.outbox[-1]["detail"], texts.T("fail_rain"))

    def test_an_hour_that_slips_by_is_a_failure(self):
        self.kz.state.location = "home_b"
        decide(self.engine, self.world, self.kz, DAY0 + 16 * 60 + 5)
        self.assertEqual(self.engine.decisions.outbox[-1]["detail"], texts.T("fail_time"))
        self.assertEqual(self.kz.state.day_plan["actions"][0]["state"], "failed")


class JointWording(unittest.TestCase):
    """Developer ruling: a doing is told from the doer's side; the other person's
    presence and part are the world's to decide."""

    def setUp(self):
        self.world, self.engine = make_world()
        self.xixi = self.world.agents["xixi"]

    def plan(self, what, place="Tide Cafe", leaves=""):
        with unittest.mock.patch.dict(os.environ, {"AI_TOWN_LANG": "zh-TW"}):
            return plan_for(self.xixi, self.world, act(place=place, what=what, leaves=leaves))

    def test_a_done_together_wording_is_refused_an_intent_is_not(self):
        p = self.plan("跟安哥一起看燈座接線的東西")                    # the D46 wording
        self.assertEqual(p["actions"], [])
        self.assertTrue(p["dropped"][0].startswith("action: states a shared outcome"))
        p = self.plan("去找安哥,想一起看燈座接線")
        self.assertEqual((p["actions"][0]["mode"], p["actions"][0]["seek"]), ("seek", "ange"))

    def test_a_shared_outcome_is_sent_back_once_with_the_reason(self):
        from backend.app.llm.providers.base import LLMResult
        answers = [
            {"intent": "今天想多跟安哥相處。", "actions": [act(place="Tide Cafe", what="跟安哥一起看燈座接線的東西", leaves="")]},
            {"intent": "今天想多跟安哥相處。", "actions": [act(place="Tide Cafe", what="去找安哥,想一起看燈座接線", leaves="")]},
        ]
        seen = []

        async def gen(**kw):
            seen.append(kw["messages"][-1]["content"])
            return LLMResult(text="", parsed=answers[len(seen) - 1], provider="fake", model="f")
        with unittest.mock.patch.dict(os.environ, {"AI_TOWN_LANG": "zh-TW"}),                 unittest.mock.patch.object(self.engine.decisions.router, "generate", gen):
            plan = asyncio.run(self.engine.decisions.plan_day(self.xixi, self.world, DAY, DAY0))
        self.assertEqual(len(seen), 2)
        self.assertIn("previous answer was refused", seen[1])
        self.assertEqual(plan["actions"][0]["seek"], "ange")

    def test_a_greeting_is_routed_to_talk(self):
        p = self.plan("在市場裡找冷月,主動打個招呼,問她最近過得如何", place="Ferry Crossing Market")  # the D46 wording
        self.assertEqual((p["actions"][0]["mode"], p["actions"][0]["seek"]), ("greet", "lengyue"))

    def go(self, what, partner_at):
        p = self.plan(what)
        self.xixi.state.day_plan = p
        self.xixi.state.location, self.xixi.state.current_action = "cafe", "idle"
        ange = self.world.agents["ange"]
        ange.state.location, ange.state.current_action, ange.state.busy_until = partner_at, "idle", 0
        decide(self.engine, self.world, self.xixi, DAY0 + 15 * 60 + 2)
        return p["actions"][0], [m.text for m in self.xixi.memory.items]

    def test_finding_someone_who_is_there_hands_them_to_the_social_trigger(self):
        calls = []

        async def gen(**kw):
            calls.append(kw["task"])
            from backend.app.llm.providers.base import LLMResult
            return LLMResult(text="", parsed={"talk": True, "reason": "came to find him"}, provider="fake", model="f")
        with unittest.mock.patch.object(self.engine.decisions.router, "generate", gen),                 unittest.mock.patch.object(self.engine.decisions, "_social_gate", lambda *a, **k: True):
            a, mem = self.go("去找安哥,想一起看燈座接線", "cafe")
        self.assertEqual(a["state"], "done")
        self.assertIn("should_talk", calls)
        self.assertTrue(any("去找" in m for m in mem))
        self.assertFalse(any("一起看" in m and "想" not in m for m in mem))   # no shared outcome remembered

    def test_finding_someone_who_is_not_there_is_a_missed_meeting(self):
        a, mem = self.go("去找安哥,想一起看燈座接線", "bakery")
        self.assertEqual(a["state"], "failed")
        beat = self.engine.decisions.outbox[-1]
        self.assertEqual((beat["verb"], beat["detail"]), ("action_failed", texts.T("fail_missed_person", who="ange")))
        self.assertTrue(any("沒做成" in m or "But" in m or "couldn't" in m for m in mem))


class FrozenTime(unittest.TestCase):
    def test_a_doing_lost_to_a_generating_conversation_is_deferred_not_failed(self):
        world, engine = make_world()
        kz = world.agents["kuaizheng"]
        kz.state.day_plan = plan_for(kz, world, act(hour=15))
        moved = dayplan_mod.defer_actions(kz, DAY0 + 14 * 60 + 50, DAY0 + 16 * 60 + 5)
        self.assertEqual(moved, 1)
        a = kz.state.day_plan["actions"][0]
        self.assertEqual((a["from"], a["to"], a["state"]), (16 * 60 + 5, 17 * 60 + 5, "planned"))
        self.assertEqual([s["from"] for s in kz.state.day_plan["slots"] if s.get("act") == 0], [16 * 60 + 5])
        kz.state.location, kz.state.current_action = "park", "rest"
        decide(engine, world, kz, DAY0 + 16 * 60 + 10)
        self.assertEqual(a["state"], "done")

    def test_a_deferred_doing_outranks_the_stretch_it_lands_in(self):
        """Live Day 46: deferred into a cafe stretch, the doing at the office lapsed."""
        world, engine = make_world()
        kz = world.agents["kuaizheng"]
        kz.state.day_plan = plan_for(kz, world, act(hour=15, place="Riverrun Office"),
                                     slots=[{"from": 16, "to": 18, "place": "Tide Cafe", "doing": "visit"}])
        dayplan_mod.defer_actions(kz, DAY0 + 14 * 60 + 50, DAY0 + 16 * 60 + 5)
        kz.state.location, kz.state.current_action = "cafe", "idle"
        d = decide(engine, world, kz, DAY0 + 16 * 60 + 10)
        self.assertEqual((d.action, d.target_location), ("move", "office"))

    def test_a_doing_inside_a_stretch_gets_a_decision_at_its_hour(self):
        """Live Day 47: idle at the market 07:31 -> 09:00 slept through an 08:00 doing."""
        world, engine = make_world()
        kz = world.agents["kuaizheng"]
        kz.state.day_plan = plan_for(kz, world, act(hour=8, place="Ferry Crossing Market", leaves=""),
                                     slots=[{"from": 7, "to": 9, "place": "Ferry Crossing Market", "doing": "visit"}])
        kz.state.location, kz.state.current_action = "market", "idle"
        now = DAY0 + 7 * 60 + 31
        d = decide(engine, world, kz, now)
        self.assertLessEqual(now + d.duration, DAY0 + 8 * 60)

    def test_nothing_moves_when_the_hour_was_not_lost(self):
        world, engine = make_world()
        kz = world.agents["kuaizheng"]
        kz.state.day_plan = plan_for(kz, world, act(hour=18))
        self.assertEqual(dayplan_mod.defer_actions(kz, DAY0 + 14 * 60, DAY0 + 16 * 60), 0)


class Objects(unittest.TestCase):
    def test_passers_by_take_it_in_once_and_scenes_show_it(self):
        world, engine = make_world()
        world.objects.append({"id": "o1", "location": "park", "creator": "kuaizheng",
                              "description": "a chalk sketch by the pond", "born_minute": DAY0})
        jiji = world.agents["jiji"]
        jiji.state.location = "park"
        with unittest.mock.patch.object(pacing, "OBJECT_NOTICE_P", 1.0):
            actions_mod.notice_objects(jiji, world, DAY0 + 60)
            actions_mod.notice_objects(jiji, world, DAY0 + 120)
        seen = [m for m in jiji.memory.items if "chalk sketch" in m.text]
        self.assertEqual(len(seen), 1)
        lines = actions_mod.scene_lines(world, "park", DAY0 + 60)
        self.assertEqual(lines, [texts.T("obj_new", name="a chalk sketch by the pond")])
        self.assertEqual(actions_mod.scene_lines(world, "park", DAY0 + 30 * DAY_MIN),
                         ["a chalk sketch by the pond"])
        lengyue = world.agents["lengyue"]
        lengyue.state.location = "park"
        plan = asyncio.run(engine.decisions.start_conversation(jiji, lengyue, world, DAY0 + 600))
        self.assertIn("Also here: " + texts.T("obj_new", name="a chalk sketch by the pond"),
                      plan.preview("jiji"))

    def test_wish_and_closure_material_see_actions_objects_and_promises(self):
        from backend.app.agents import chapters as chapters_mod
        from backend.app.agents.core import MemoryItem
        from backend.app.llm.prompts import builders
        world, engine = make_world()
        aisi = world.agents["aisi"]
        world.objects.append({"id": "o1", "location": "park", "creator": "kuaizheng",
                              "description": "a chalk sketch by the pond", "born_minute": DAY0})
        aisi.memory.add(MemoryItem(minute=DAY0 + 60, importance=5, kind="reflection", tags=["action"],
                                   text=texts.T("action_self", loc="park", what="sketch the pond")))
        aisi.memory.add(MemoryItem(minute=DAY0 + 90, importance=5, kind="conversation", tags=["promise"],
                                   text=texts.T("promise_kept", other="jiji", loc="cafe")))
        from backend.app.agents import wishes as wishes_mod
        mat = wishes_mod.generation_material(aisi, world, DAY)
        self.assertEqual(len(mat["town_objects"]), 1)
        self.assertEqual(len(mat["my_actions"]), 1)
        blob = builders.wish_generation_prompt(aisi, mat)[1]["content"]
        self.assertIn("Things they did lately", blob)
        self.assertIn("a chalk sketch by the pond", blob)
        cm = chapters_mod.closure_material(aisi, world, aisi.chapter, DAY0 + 600)
        self.assertEqual(len(cm["actions"]), 1)
        self.assertEqual(len(cm["promises"]), 1)

    def test_v15_snapshot_loads_with_no_objects(self):
        world, engine = make_world()
        world.objects.append({"id": "o1", "location": "park", "creator": "kuaizheng",
                              "description": "x", "born_minute": 1})
        payload = snapshot_mod.capture(engine, world, engine.decisions)
        w2, e2 = make_world()
        snapshot_mod.restore(payload, e2, w2, e2.decisions)
        self.assertEqual(len(w2.objects), 1)
        payload.pop("objects")
        payload["schema_version"] = 15
        w3, e3 = make_world()
        snapshot_mod.restore(payload, e3, w3, e3.decisions)
        self.assertEqual(w3.objects, [])


class WishBlocked(unittest.TestCase):
    """A doing planned for an active wish that the world turns away is a blocked day
    for that wish (Era 3: Azong went looking for Oula eight times over nine days and
    the pressure on her letter wish stayed at 0.25)."""

    SEEK_OULA = "去找歐拉,想當面問那封掛號信有沒有交給別人"

    def setUp(self):
        from backend.app.agents import wishes as wishes_mod
        self.wishes = wishes_mod
        self.env = unittest.mock.patch.dict(os.environ, {"AI_TOWN_LANG": "zh-TW"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.world, self.engine = make_world()
        self.azong = self.world.agents["azong"]
        clean, errs = wishes_mod.validate_seed(
            {"scale": "minor", "title": "把那封掛號信的下落問清楚",
             "statement": "我想親自去問問歐拉,把瓏託我打聽的那封掛號信到底交給了誰弄個明白。",
             "requirements": [{"kind": "talk_count", "target": "oula", "threshold": 3}]},
            self.azong, self.world, 1)
        self.assertIsNotNone(clean, errs)
        self.wish = wishes_mod.install(self.azong, clean, 1) or wishes_mod.active_wishes(self.azong)[0]

    def planned(self, what):
        acts, dropped = dayplan_mod.validate_actions(
            [{"hour": 15, "place": "office", "what": what, "cost": 0}], self.azong, self.world, DAY, [])
        self.assertEqual(len(acts), 1, dropped)
        return acts[0]

    def miss(self, a, day, hour=15):
        actions_mod.fail(self.azong, dict(a), texts.T("fail_missed_person", who="oula"),
                         (day - 1) * DAY_MIN + hour * 60)

    def test_the_doing_carries_the_wish_it_was_planned_for(self):
        self.assertEqual(self.planned(self.SEEK_OULA)["wish"], self.wish.id)

    def test_eight_misses_in_a_row_build_real_pressure(self):
        a = self.planned(self.SEEK_OULA)
        for day in range(3, 11):
            self.miss(a, day)
        st = self.wish.drive
        self.assertEqual(st["blocked_streak"], 8)
        self.assertGreaterEqual(self.wish.frustration_count, 2)       # the frustration pipeline ran
        self.assertGreater(self.wishes.abandonment_pressure(self.wish),
                           self.wishes.abandonment_threshold(self.azong, self.wish, 10))

    def test_two_misses_on_one_day_count_once(self):
        a = self.planned(self.SEEK_OULA)
        self.miss(a, 3, 11)
        self.miss(a, 3, 15)
        self.assertEqual(self.wish.drive["blocked_streak"], 1)

    def test_an_unrelated_doing_that_fails_does_not_count(self):
        a = self.planned("去事務所把報價單重新整理一遍")
        self.assertEqual(a["wish"], "")
        self.miss(a, 3)
        self.assertEqual(self.wishes.abandonment_pressure(self.wish), 0)


class StructuredObjects(unittest.TestCase):
    """An object is kind / item / content, and every sentence about it is built from
    those once (Era 3: "在螢火公園看到了今天下午我在螢火公園長椅邊等你—艾斯。")."""

    def setUp(self):
        self.env = unittest.mock.patch.dict(os.environ, {"AI_TOWN_LANG": "zh-TW"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_the_era3_strings_come_apart(self):
        P = actions_mod.parse_leaves
        cases = [
            ("一張寫著「想當面聊聊紙條的事,今天下午在螢火公園等你—歐拉」的小紙條", "",
             {"kind": "note", "item": "一張小紙條", "content": "想當面聊聊紙條的事,今天下午在螢火公園等你—歐拉"}),
            ("一張字跡輕快的小紙條:「今天下午在螢火公園,有空的話可以陪我走走嗎?—ㄐㄐ」", "",
             {"kind": "note", "item": "一張字跡輕快的小紙條", "content": "今天下午在螢火公園,有空的話可以陪我走走嗎?—ㄐㄐ"}),
            ("寫著「感測器的事,今天下午我在螢火公園長椅邊等你—瓏」的小字條", "",
             {"kind": "note", "item": "一張小字條", "content": "感測器的事,今天下午我在螢火公園長椅邊等你—瓏"}),
            ("今天下午我在螢火公園長椅邊等你,想當面把話說清楚—歐拉", "在長椅邊留個字條約冷月見面",
             {"kind": "note", "item": "", "content": "今天下午我在螢火公園長椅邊等你,想當面把話說清楚—歐拉"}),
            ("一張寫著「爐心烘焙坊新品試吃,歡迎路過嚐嚐—安哥」的新告示", "",
             {"kind": "notice", "item": "一張新告示", "content": "爐心烘焙坊新品試吃,歡迎路過嚐嚐—安哥"}),
            ("一張手寫的新品試吃告示", "", {"kind": "notice", "item": "一張手寫的新品試吃告示", "content": ""}),
        ]
        for raw, what, want in cases:
            self.assertEqual(P(raw, what), want, raw)

    def test_the_contract_shape_is_taken_as_is(self):
        self.assertEqual(actions_mod.parse_leaves({"kind": "craft", "item": "一隻漂流木刻的小鳥", "words": "x"}),
                         {"kind": "craft", "item": "一隻漂流木刻的小鳥", "content": ""})   # only notes carry words
        self.assertIsNone(actions_mod.parse_leaves(None))
        self.assertIsNone(actions_mod.parse_leaves({"kind": "note", "item": "", "words": ""}))

    def test_one_phrase_for_every_sentence(self):
        o = {"kind": "note", "item": "", "content": "今天下午我在長椅邊等你—歐拉"}
        self.assertEqual(actions_mod.object_phrase(o), "一張字條,上面寫著「今天下午我在長椅邊等你—歐拉」")
        seen = texts.T("object_noticed", loc="park", what=actions_mod.object_phrase(o))
        self.assertTrue(seen.endswith("看到了一張字條,上面寫著「今天下午我在長椅邊等你—歐拉」。"), seen)

    def test_a_made_object_event_carries_the_structure(self):
        world, engine = make_world()
        oula = world.agents["oula"]
        oula.state.location = "park"
        act_ = {"what": "在長椅邊留個字條約冷月見面", "location": "park", "cost": 0, "recipient": "lengyue",
                "leaves": {"kind": "note", "item": "", "content": "今天下午我在長椅邊等你—歐拉"}}
        beat, obj = actions_mod.land(oula, world, act_, DAY0 + 15 * 60)
        self.assertEqual(beat["obj"], {"kind": "note", "item": "", "content": "今天下午我在長椅邊等你—歐拉"})
        self.assertEqual(beat["text"], "今天下午我在長椅邊等你—歐拉")
        self.assertEqual(obj["description"], "一張字條,上面寫著「今天下午我在長椅邊等你—歐拉」")


if __name__ == "__main__":
    unittest.main()
