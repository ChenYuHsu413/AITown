"""Integration test: headless multi-day mock run with a chapter closure.

    python -m unittest tests.test_closure_integration -v

Verifies the event flow chapter_closed -> interlude behaviour -> chapter_started,
the natural landmark_done trigger, and measures the acceptance metric (F): how often
Aisi's dialogue context mentions the installation before vs after closure, and
that her biography surfaces when she is at the park.
"""

from __future__ import annotations

import asyncio
import os
import unittest

os.environ["AI_TOWN_LIVE"] = "0"
os.environ["AI_TOWN_LANG"] = "en"
os.environ["AI_TOWN_DB_URL"] = ""

from backend.app.agents import chapters as chapters_mod
from backend.app.agents.decision import DecisionEngine
from backend.app.llm.factory import build_router
from backend.app.simulation.engine import DAY_MIN, SimulationEngine
from backend.app.world.world import World
from data.seed import build_agents, build_locations, seed_secrets


def make_engine():
    router = build_router(live=False)
    world = World(build_locations(), build_agents())
    engine = SimulationEngine(world, DecisionEngine(router))
    seed_secrets(engine.decisions.secrets)
    return world, engine


class PromptProbe:
    """Records every dialogue prompt Aisi takes part in (what the model can see)."""
    def __init__(self, engine, agent_id="aisi"):
        self.engine, self.agent_id = engine, agent_id
        self.prompts: list[tuple[int, str, str]] = []   # (minute, location, user prompt text)
        orig = engine.decisions.start_conversation

        async def wrapped(a, b, world, now, **kw):
            plan = await orig(a, b, world, now, **kw)
            if agent_id in (a.id, b.id):
                self.prompts.append((now, a.state.location, plan.messages[-1]["content"]))
            return plan
        engine.decisions.start_conversation = wrapped


async def run_days(engine, start_minute, days):
    engine.bootstrap(start_minute) if engine.scheduler.peek_minute() is None else None
    end = start_minute + days * DAY_MIN
    await engine.run_until(end)
    await engine.drain(end)
    return end


class ClosureFlow(unittest.TestCase):

    def test_manual_close_then_interlude_then_started(self):
        world, engine = make_engine()
        aisi = world.agents["aisi"]
        probe = PromptProbe(engine)

        async def scenario():
            engine.bootstrap(6 * 60)
            # Day 1-3: the pursuit is live.
            await engine.run_until(3 * DAY_MIN)
            await engine.drain(3 * DAY_MIN)
            before = list(probe.prompts)
            # Manual trigger (God Mode path) at the start of day 4 -- unless the
            # landmark already finished and closed it naturally.
            if aisi.chapter.chapter_type == "pursuit":
                rec = await engine.close_chapter(aisi, "completed", trigger="manual", reason="test")
                self.assertIsNotNone(rec)
            close_minute = engine.now
            until = aisi.chapter.until_day
            # Run through the interlude and 2 more days.
            days = (until - (close_minute // DAY_MIN + 1)) + 2
            end = (close_minute // DAY_MIN + 1 + days) * DAY_MIN
            await engine.run_until(end)
            await engine.drain(end)
            after = [p for p in probe.prompts if p[0] > close_minute]
            return before, after, close_minute

        before, after, close_minute = asyncio.run(scenario())

        verbs = [(e.minute, e.verb, e.actor) for e in engine.bus.events if e.verb in ("chapter_closed", "chapter_started")]
        closed = [v for v in verbs if v[1] == "chapter_closed" and v[2] == "aisi"]
        started = [v for v in verbs if v[1] == "chapter_started" and v[2] == "aisi"]
        self.assertEqual(len(closed), 1, verbs)
        self.assertEqual(len(started), 1, verbs)
        self.assertLess(closed[0][0], started[0][0])
        self.assertEqual(aisi.chapter.chapter_type, "ordinary")
        self.assertEqual(aisi.chapter_history[0].outcome, "completed")

        # interlude behaviour: at least one aimless drift decision during the interlude
        drifts = [t for t in engine.decisions.traces
                  if t.agent_id == "aisi" and "interlude" in t.decision.reason]
        self.assertGreater(len(drifts), 0)
        self.assertTrue(all(closed[0][0] <= t.minute < started[0][0] for t in drifts))

        # acceptance metric (F): installation mentions in Aisi's dialogue context
        def rate(ps):
            return (sum(1 for _, _, txt in ps if "installation" in txt.lower()) / len(ps)) if ps else 0.0
        r_before, r_after = rate(before), rate(after)
        # what surfaces after closure must be the biography (place/topic), not the old context
        after_at_park = [p for p in after if p[1] == "park"]
        after_elsewhere = [p for p in after if p[1] != "park"]
        bio = next(m.text for m in aisi.memory.items if m.kind == "biography")
        print(f"\n[metric] aisi dialogue prompts mentioning 'installation': "
              f"before {r_before:.0%} ({len(before)} prompts) -> after {r_after:.0%} ({len(after)} prompts); "
              f"after@park {rate(after_at_park):.0%} ({len(after_at_park)}), "
              f"after elsewhere {rate(after_elsewhere):.0%} ({len(after_elsewhere)})")
        self.assertGreater(len(before), 0)
        self.assertEqual(r_before, 1.0)                      # the pursuit narrative was in every card
        self.assertLess(r_after, r_before)
        for _, loc, txt in after_elsewhere:
            self.assertNotIn("installation", txt.lower())    # away from the park: gone from her context
        for _, loc, txt in after_at_park:
            self.assertIn(bio, txt)                          # at the park: the biography surfaces
        if after_at_park:
            print(f"[metric] biography surfaced at the park: \"{bio}\"")

    def test_landmark_done_closes_naturally(self):
        world, engine = make_engine()
        aisi = world.agents["aisi"]

        async def scenario():
            engine.bootstrap(6 * 60)
            end = 8 * DAY_MIN
            await engine.run_until(end)
            await engine.drain(end)
        asyncio.run(scenario())
        lm = world.locations["park"].landmarks[0]
        self.assertEqual(lm["state"], "completed")
        self.assertTrue(lm.get("decoupled"))
        done = [e for e in engine.bus.events if e.verb == "landmark_done"]
        closed = [e for e in engine.bus.events if e.verb == "chapter_closed" and e.actor == "aisi"]
        self.assertEqual(len(done), 1)
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].minute, done[0].minute)
        self.assertEqual(aisi.chapter_history[0].trigger, "landmark")
        # the worry-resolution hook fired in the same tick and must NOT double-close
        self.assertEqual(len(aisi.chapter_history), 1)
        calls = [c for c in engine.decisions.router.usage.calls
                 if c.task_type == "chapter_closure" and c.agent_id == "aisi"]
        self.assertEqual(len(calls), 1)                  # exactly one LLM call per closure
        # (Xixi's "ask Aisi" pursuit may close in the same run via the secret_resolved
        # signal when he confides in her -- a separate closure with its own single call.)
        for a in world.agents.values():
            n = sum(1 for c in engine.decisions.router.usage.calls
                    if c.task_type == "chapter_closure" and c.agent_id == a.id)
            self.assertEqual(n, len(a.chapter_history), a.id)


if __name__ == "__main__":
    unittest.main()
