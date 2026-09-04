"""Integration tests for wishes -- everything runs through the real scheduler and
the real decision funnel; nothing here calls a wish helper to fake a step.

    python -m unittest tests.test_wish_integration -v

Covers spec F1 (seed -> drive -> move -> arrive -> progress -> terminal),
F3 (the multi-step work path), F6 (closure integration) and F9 (LLM budget).
"""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest import mock

os.environ["AI_TOWN_LIVE"] = "0"
os.environ["AI_TOWN_LANG"] = "en"
os.environ["AI_TOWN_DB_URL"] = ""

from backend.app.agents import chapters as chapters_mod
from backend.app.agents import wishes as wishes_mod
from backend.app.agents.decision import DecisionEngine
from backend.app.llm.factory import build_router
from backend.app.simulation.engine import DAY_MIN, SimulationEngine
from backend.app.world.world import World
from data.seed import build_agents, build_locations, seed_secrets

START = 6 * 60


def make_engine():
    router = build_router(live=False)
    world = World(build_locations(), build_agents())
    engine = SimulationEngine(world, DecisionEngine(router))
    seed_secrets(engine.decisions.secrets)
    engine.bootstrap(START)
    return world, engine


def seed_wish(engine, world, agent_id, **over):
    body = {"scale": "minor", "title": "T", "statement": "A statement long enough to pass.",
            "narrative": "I am quietly working on something of my own.",
            "requirements": [{"kind": "location_visits", "target": "cafe", "threshold": 2}]}
    body.update(over)
    agent = world.agents[agent_id]
    day = engine.now // DAY_MIN + 1
    clean, problems = wishes_mod.validate_seed(body, agent, world, day)
    assert not problems, problems
    return engine.seed_wish(agent, clean, day)


async def run_to(engine, end_minute):
    await engine.run_until(end_minute)
    await engine.drain(end_minute)


# The drive's daily dice are deliberately probabilistic; these tests are about the
# mechanism, so they force the roll. The pacing constants themselves are asserted
# in test_wishes.DrivePacing.
def always_drive():
    return mock.patch.multiple(wishes_mod, DRIVE_MINOR_PROBABILITY=1.0, DRIVE_MAJOR_PROBABILITY=1.0)


class SeedToCompletion(unittest.TestCase):
    """F1: the whole loop, through the ordinary decision path, with no LLM."""

    def test_minor_location_wish_progresses_only_from_arrive_events(self):
        world, engine = make_engine()
        agent = world.agents["kuaizheng"]              # retired: plenty of rest slots
        with always_drive():
            wish = seed_wish(engine, world, "kuaizheng",
                             requirements=[{"kind": "location_visits", "target": "cafe", "threshold": 2}])
            self.assertEqual(wish.requirements[0].progress, 0)
            asyncio.run(run_to(engine, START + 3 * DAY_MIN))

        arrivals = [e for e in engine.bus.events
                    if e.verb == "arrive" and e.actor == "kuaizheng" and e.location == "cafe"]
        self.assertGreaterEqual(len(arrivals), 2, "the drive never actually walked him to the cafe")
        # progress equals the number of DISTINCT arrive events, never more
        self.assertEqual(wish.requirements[0].progress, min(2, len(arrivals)))
        self.assertEqual(wish.status, "completed")
        closed = [e for e in engine.bus.events if e.verb == "wish_closed" and e.actor == "kuaizheng"]
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].text, "completed")
        # a move the drive asked for is a real routed decision, not a teleport
        drive_moves = [t for t in engine.decisions.traces
                       if t.agent_id == "kuaizheng" and "private intention" in t.decision.reason]
        self.assertTrue(drive_moves)
        self.assertTrue(all(t.decision.action in ("move", "rest", "idle", "work") for t in drive_moves))
        # The wish itself never reaches a model. (Other residents' phase-1 chapters
        # may close during these days -- that is not this wish's doing, so the
        # assertion is scoped to its owner.)
        owner_calls = {c.task_type for c in engine.decisions.router.usage.calls
                       if c.agent_id == "kuaizheng"}
        self.assertNotIn("chapter_closure", owner_calls)
        self.assertEqual(agent.chapter_history, [])

    def test_minor_completion_leaves_the_chapter_alone_and_writes_no_biography(self):
        world, engine = make_engine()
        agent = world.agents["kuaizheng"]
        self.assertIsNone(agent.chapter)
        with always_drive():
            seed_wish(engine, world, "kuaizheng",
                      requirements=[{"kind": "location_visits", "target": "cafe", "threshold": 1}])
            asyncio.run(run_to(engine, START + 2 * DAY_MIN))
        self.assertEqual(agent.wishes[0].status, "completed")
        self.assertIsNone(agent.chapter)                      # F6: minor never touches chapters
        self.assertEqual(agent.chapter_history, [])
        self.assertFalse([m for m in agent.memory.items if m.kind == "biography"])
        self.assertEqual([e.verb for e in engine.bus.events if e.verb == "chapter_closed"], [])
        # ...but it does leave one ordinary memory
        self.assertIn(wishes_mod.MINOR_CLOSE_TEXT["completed"], [m.text for m in agent.memory.items])


class WorkPath(unittest.TestCase):
    """F3: progress comes from the work Event, never from setting off or arriving."""

    def test_move_then_arrive_do_not_advance_only_the_work_event_does(self):
        world, engine = make_engine()
        agent = world.agents["xue"]                    # office worker: a real work routine
        seen: list = []
        engine.bus.subscribers.append(lambda e: seen.append(e) if e.actor == "xue" else None)
        with always_drive():
            wish = seed_wish(engine, world, "xue",
                             requirements=[{"kind": "action_count", "target": "work", "threshold": 2}])
            asyncio.run(run_to(engine, START + 2 * DAY_MIN))
        works = [e for e in seen if e.verb == "work"]
        moves = [e for e in seen if e.verb in ("arrive", "move")]
        self.assertTrue(works, "he never actually worked")
        self.assertTrue(moves)
        # progress counts work events only, and never exceeds them
        self.assertLessEqual(wish.requirements[0].progress, len(works))
        self.assertGreater(wish.requirements[0].progress, 0)

    def test_a_closed_workplace_is_never_forced(self):
        world, engine = make_engine()
        agent = world.agents["ange"]                   # bakery owner; the bakery shuts on Wednesday
        wish = seed_wish(engine, world, "ange",
                         requirements=[{"kind": "action_count", "target": "work", "threshold": 5}])
        wednesday = 2 * DAY_MIN + 10 * 60              # day 3 == Wednesday
        self.assertFalse(wishes_mod.work_available(agent, world, "bakery", wednesday))
        with always_drive():
            directive = wishes_mod.next_directive(agent, world, wednesday, "rest")
        self.assertIsNone(directive)                   # refused, not forced
        self.assertEqual(wish.drive["blocked_streak"], 1)   # and it counts as a blocked day
        # on Thursday the same wish is actionable again
        thursday = 3 * DAY_MIN + 10 * 60
        self.assertTrue(wishes_mod.work_available(agent, world, "bakery", thursday))

    def test_rain_closes_the_park_to_a_wish_too(self):
        world, engine = make_engine()
        agent = world.agents["kuaizheng"]
        wish = seed_wish(engine, world, "kuaizheng",
                         requirements=[{"kind": "location_visits", "target": "park", "threshold": 3}])
        engine.trigger_world_effect("rain", "", 600)
        with always_drive():
            self.assertIsNone(wishes_mod.next_directive(agent, world, engine.now + 60, "rest"))
        self.assertEqual(wish.drive["blocked_streak"], 1)


class ClosureIntegration(unittest.TestCase):
    """F6: a major wish's three endings all run the phase-1 closure pipeline."""

    def _seed_major(self, engine, world, agent_id="oula", **over):
        body = {"scale": "major", "title": "A private aim",
                "statement": "I want to spend far more time at the market.",
                "narrative": "I am quietly set on something of my own right now.",
                "requirements": [{"kind": "location_visits", "target": "market", "threshold": 40}]}
        body.update(over)
        return seed_wish(engine, world, agent_id, **body)

    def test_abandoned_major_closes_the_chapter_and_the_biography_cites_frustration(self):
        world, engine = make_engine()
        agent = world.agents["oula"]
        agent.profile.personality["conscientiousness"] = 0.05
        wish = self._seed_major(engine, world)
        self.assertEqual(agent.chapter.chapter_type, "pursuit")
        self.assertEqual(agent.chapter.id, wish.chapter_id)
        # Real blocked days, recorded through the ordinary entry point.
        for day in (2, 3, 4, 5, 6):
            wishes_mod.note_blocked(agent, wish, (day - 1) * DAY_MIN + 11 * 60)
        self.assertGreater(wish.frustration_count, 0)

        async def settle():
            engine.now = 7 * DAY_MIN
            engine._last_day = 6
            engine._settle_wishes()              # the daily rule-layer evaluation
            engine.drain_wish_terminals()
            await engine.drain(engine.now + 5)   # let the closure pipeline finish
        asyncio.run(settle())

        self.assertEqual(wish.status, "abandoned")
        closed = [e for e in engine.bus.events if e.verb == "chapter_closed" and e.actor == "oula"]
        self.assertEqual(len(closed), 1)
        self.assertTrue(closed[0].detail.startswith("abandoned"))
        record = agent.chapter_history[-1]
        self.assertEqual(record.outcome, "abandoned")
        self.assertEqual(agent.chapter.chapter_type, "interlude")
        # The material the closure reflection was handed included the frustration.
        closed_chapter = chapters_mod.Chapter.from_dict(record.chapter)
        material = chapters_mod.closure_material(agent, world, closed_chapter, engine.now)
        self.assertIn(wishes_mod.FRUSTRATION_TEXT, [m["text"] for m in material["memories"]])
        self.assertTrue(any(m.kind == "biography" for m in agent.memory.items))

    def test_expired_major_fails_and_closes_once(self):
        world, engine = make_engine()
        agent = world.agents["oula"]
        wish = self._seed_major(engine, world, expires_on=3)

        async def settle():
            engine.now = 5 * DAY_MIN
            engine._last_day = 4
            engine._settle_wishes()
            await engine.drain(engine.now + 5)
        asyncio.run(settle())
        self.assertEqual(wish.status, "failed")
        self.assertEqual(agent.chapter_history[-1].outcome, "failed")
        self.assertEqual(len([e for e in engine.bus.events
                              if e.verb == "chapter_closed" and e.actor == "oula"]), 1)
        self.assertEqual(len([e for e in engine.bus.events
                              if e.verb == "wish_closed" and e.actor == "oula"]), 1)

    def test_a_chapter_closed_by_something_else_settles_the_linked_wish(self):
        world, engine = make_engine()
        agent = world.agents["oula"]
        wish = self._seed_major(engine, world)
        asyncio.run(engine.close_chapter(agent, "completed", trigger="manual", reason="external"))
        self.assertEqual(wish.status, "completed")
        self.assertEqual(len([e for e in engine.bus.events
                              if e.verb == "wish_closed" and e.actor == "oula"]), 1)

    def test_the_public_beat_never_names_the_private_chapter(self):
        world, engine = make_engine()
        secret_title = "A private aim"
        wish = self._seed_major(engine, world)
        asyncio.run(engine.close_chapter(world.agents["oula"], "completed", trigger="manual"))
        for ev in engine.bus.events:
            for field in (ev.text, ev.text_en, ev.detail):
                self.assertNotIn(secret_title, field or "")
                self.assertNotIn(wish.statement, field or "")
        closed = next(e for e in engine.bus.events if e.verb == "chapter_closed")
        self.assertIn("a private chapter", closed.detail)


class LLMBudget(unittest.TestCase):
    """F9: a headless mock day costs exactly what it cost before wishes existed."""

    def test_a_day_with_a_wish_makes_no_extra_llm_calls(self):
        base_world, base_engine = make_engine()
        asyncio.run(run_to(base_engine, START + DAY_MIN))
        baseline = len(base_engine.decisions.router.usage.calls)

        world, engine = make_engine()
        with always_drive():
            seed_wish(engine, world, "kuaizheng",
                      requirements=[{"kind": "location_visits", "target": "cafe", "threshold": 2}])
            asyncio.run(run_to(engine, START + DAY_MIN))
        with_wish = len(engine.decisions.router.usage.calls)
        tasks = {c.task_type for c in engine.decisions.router.usage.calls}
        print(f"\n[metric] mock day LLM calls: baseline {baseline} -> with a seeded wish {with_wish}")
        self.assertNotIn("chapter_closure", tasks)
        # A wish may shift WHICH conversations happen (it biases who is approached),
        # but it must not introduce a new kind of call or a systematic increase.
        self.assertLessEqual(with_wish, baseline)


if __name__ == "__main__":
    unittest.main()
