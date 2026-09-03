"""Unit tests for life-chapter closure (agents/chapters.py).

    python -m unittest tests.test_chapters -v

No DB, no API key: the mock provider serves the closure reflection, and a stub
router stands in for a failing chain.
"""

from __future__ import annotations

import asyncio
import os
import unittest

os.environ["AI_TOWN_LIVE"] = "0"
os.environ["AI_TOWN_LANG"] = "en"
os.environ["AI_TOWN_DB_URL"] = ""

from backend.app.agents import chapters as chapters_mod
from backend.app.agents.core import Belief, MemoryItem
from backend.app.agents.decision import DecisionEngine
from backend.app.llm.factory import build_router
from backend.app.llm.prompts import builders
from backend.app.llm.router import ProvidersExhausted
from backend.app.simulation import snapshot as snapshot_mod
from backend.app.simulation.engine import SimulationEngine
from backend.app.world.world import World
from data.seed import build_agents, build_locations, seed_secrets

DAY = 24 * 60


def make_world():
    router = build_router(live=False)
    world = World(build_locations(), build_agents())
    engine = SimulationEngine(world, DecisionEngine(router))
    seed_secrets(engine.decisions.secrets)
    engine.bootstrap(6 * 60)
    return world, engine


class ApplyClosureAtomic(unittest.TestCase):
    """B3: all five effects of a closure land together, in one synchronous call."""

    def test_all_effects_land_together(self):
        world, engine = make_world()
        aisi = world.agents["aisi"]
        self.assertEqual(aisi.chapter.chapter_type, "pursuit")
        self.assertEqual(aisi.chapter.related_landmark_id, "installation")
        # chapter-related material: the seed memory + a belief about the piece
        aisi.memory.add(MemoryItem(minute=100, importance=6,
                                   text="Spent the whole afternoon wiring {landmark:installation} at {loc:park}."))
        aisi.memory.add(MemoryItem(minute=120, importance=3, text="Had a meal at {loc:cafe}."))
        aisi.semantic.beliefs.append(Belief(
            subject="self", text="The light installation project is the thing I care about most.", confidence=0.6))
        aisi.semantic.beliefs.append(Belief(subject="xixi", text="Xixi is shy but sharp.", confidence=0.5))
        n_before = len(aisi.memory.items)
        goals_before = list(aisi.profile.goals)

        now = 3 * DAY + 15 * 60
        record = chapters_mod.apply_closure(
            aisi, world, "completed", "I built the installation and it finally lit up whole.",
            "fulfilled", [{"id": "abc", "text": "x"}], now, trigger="manual", biography_source="llm")

        self.assertIsNotNone(record)
        # 1. biography memory, nothing deleted
        self.assertEqual(len(aisi.memory.items), n_before + 1)
        bio = aisi.memory.items[-1]
        self.assertEqual(bio.kind, "biography")
        self.assertEqual(bio.source_chapter_id, record.chapter["id"])
        self.assertIn("loc:park", bio.tags)
        self.assertIn("installation", bio.tags)
        # 2. related memories/beliefs down-weighted (0.3), unrelated untouched
        w = {m.text: m.weight for m in aisi.memory.items}
        self.assertAlmostEqual(w["Spent the whole afternoon wiring {landmark:installation} at {loc:park}."], 0.3)
        self.assertAlmostEqual(w["Had a meal at {loc:cafe}."], 1.0)
        self.assertEqual(record.downweighted_memories, 2)      # the seed "a third done" memory + the wiring one
        bw = {b.subject: b.weight for b in aisi.semantic.beliefs}
        self.assertAlmostEqual(bw["self"], 0.3)
        self.assertAlmostEqual(bw["xixi"], 1.0)
        self.assertEqual(len(aisi.semantic.beliefs), 2)         # not deleted
        # 3. history + interlude
        self.assertEqual(len(aisi.chapter_history), 1)
        self.assertEqual(aisi.chapter_history[0].outcome, "completed")
        self.assertEqual(aisi.chapter.chapter_type, "interlude")
        self.assertEqual(aisi.chapter.emotional_residue, "fulfilled")
        self.assertTrue(3 <= aisi.chapter.until_day - aisi.chapter.started_on <= 7)
        # 5. landmark decoupled but still a public world object; work slots retired
        lm = world.locations["park"].landmarks[0]
        self.assertTrue(lm.get("decoupled"))
        self.assertEqual(lm["created_by"], "aisi")
        self.assertFalse(any(e.action == "work" and e.location == "park" for e in aisi.routine.entries))
        self.assertEqual(aisi.profile.goals, goals_before)      # the pursuit never sat on profile.goals
        # the closure must not hasten a reflection call
        self.assertEqual(aisi.memory.importance_since_reflection,
                         sum(m.importance for m in aisi.memory.items[:-1]))

    def test_noop_without_pursuit(self):
        world, _ = make_world()
        jiji = world.agents["jiji"]                    # ordinary (chapter None)
        self.assertIsNone(chapters_mod.apply_closure(jiji, world, "completed", "x", "", [], 0))
        self.assertEqual(jiji.chapter_history, [])


class BiographyRetrieval(unittest.TestCase):
    """A2: biography stays out of the ordinary top-k; surfaces on topic or place."""

    def setUp(self):
        self.world, self.engine = make_world()
        self.aisi = self.world.agents["aisi"]
        for i in range(8):
            self.aisi.memory.add(MemoryItem(minute=i * 60, importance=4, text=f"Talked with {{agent:xixi}} at {{loc:cafe}} {i}."))
        chapters_mod.apply_closure(self.aisi, self.world, "completed",
                                   "I built the light installation in the park and it finally lit up.",
                                   "fulfilled", [], 2 * DAY)
        self.bio = self.aisi.memory.items[-1].text

    def test_excluded_by_default(self):
        got = self.aisi.memory.retrieve("Xixi cafe", k=5)
        self.assertNotIn(self.bio, got)
        got = asyncio.run(self.aisi.memory.retrieve_async("Xixi cafe", k=5, location="cafe"))
        self.assertNotIn(self.bio, got)

    def test_surfaces_on_topic(self):
        got = asyncio.run(self.aisi.memory.retrieve_async(
            "Xixi", k=3, location="cafe", topic="how is the light installation doing"))
        self.assertEqual(got[0], self.bio)
        # one theme word alone is not enough (the bar is 2)
        got = asyncio.run(self.aisi.memory.retrieve_async("Xixi", k=3, location="cafe", topic="a light"))
        self.assertNotIn(self.bio, got)

    def test_surfaces_at_place(self):
        got = asyncio.run(self.aisi.memory.retrieve_async("Xixi", k=3, location="park"))
        self.assertEqual(got[0], self.bio)
        self.assertLessEqual(len(got), 3)

    def test_downweight_sinks_related_memories(self):
        # a fresh, related memory scores below an unrelated one once down-weighted
        self.aisi.memory.add(MemoryItem(minute=3 * DAY, importance=9,
                                        text="Tuned the {landmark:installation} lights again at {loc:park}."))
        self.aisi.memory.add(MemoryItem(minute=3 * DAY, importance=4, text="Had coffee with {agent:xixi} at {loc:cafe}."))
        # down-weight applies only at closure time (minute <= now); do it again explicitly
        # by re-running the weight rule to mirror a closure that happened after it.
        for m in self.aisi.memory.items:
            if "installation" in m.text and m.kind != "biography":
                m.weight = 0.3
        self.aisi.memory.invalidate_weights()
        got = self.aisi.memory.retrieve("installation park", k=1)
        self.assertNotIn("Tuned the", got[0])


class SnapshotCompat(unittest.TestCase):
    """An old (v10) snapshot without chapter fields loads; chapters round-trip on v11."""

    def test_old_snapshot_loads_as_ordinary(self):
        world, engine = make_world()
        payload = snapshot_mod.capture(engine, world, engine.decisions)
        # strip every v11 field, as a v10 snapshot would lack them
        payload["schema_version"] = 10
        for adata in payload["agents"].values():
            adata.pop("chapter", None)
            adata.pop("chapter_history", None)
            for m in adata["memory"]["items"]:
                for k in ("weight", "source_chapter_id", "tags"):
                    m.pop(k, None)
            for b in adata["semantic"]["beliefs"]:
                b.pop("weight", None)
        world2, engine2 = make_world()
        for a in world2.agents.values():          # simulate a server whose seed knows chapters
            a.chapter = None
        minute = snapshot_mod.restore(payload, engine2, world2, engine2.decisions)
        self.assertEqual(minute, engine.now)
        for a in world2.agents.values():
            self.assertIsNone(a.chapter)
            self.assertEqual(chapters_mod.chapter_type(a), "ordinary")
            self.assertTrue(all(m.weight == 1.0 for m in a.memory.items))
        # prompts still build (chapter None reads as ordinary days)
        self.assertIn("ordinary stretch", builders.character_card(world2.agents["aisi"]))

    def test_v11_roundtrip(self):
        world, engine = make_world()
        aisi = world.agents["aisi"]
        chapters_mod.apply_closure(aisi, world, "abandoned", "I let the installation go, and that was my call.",
                                   "relieved", [{"id": "1", "text": "t"}], DAY)
        payload = snapshot_mod.capture(engine, world, engine.decisions)
        self.assertEqual(payload["schema_version"], 12)
        world2, engine2 = make_world()
        snapshot_mod.restore(payload, engine2, world2, engine2.decisions)
        a2 = world2.agents["aisi"]
        self.assertEqual(a2.chapter.chapter_type, "interlude")
        self.assertEqual(a2.chapter.emotional_residue, "relieved")
        self.assertEqual(a2.chapter_history[0].outcome, "abandoned")
        self.assertEqual(a2.chapter_history[0].memory_refs, [{"id": "1", "text": "t"}])
        self.assertEqual(a2.memory.items[-1].kind, "biography")
        self.assertIn("loc:park", a2.memory.items[-1].tags)
        self.assertTrue(any(m.weight == 0.3 for m in a2.memory.items))
        self.assertTrue(world2.locations["park"].landmarks[0].get("decoupled"))


class _ExhaustedRouter:
    """A router whose every call fails the gate -- the closure must fall back."""
    def __init__(self, real):
        self.usage = real.usage
        self.tiers = real.tiers
        self.task_chains = real.task_chains
        self.budget_usd = real.budget_usd

    async def generate(self, **kw):
        raise ProvidersExhausted("all real providers failed the gate")


class LLMFallback(unittest.TestCase):
    """B2: a failed closure reflection never blocks closure -- the template line is used."""

    def test_template_line_when_chain_exhausted(self):
        world, engine = make_world()
        engine.decisions.router = _ExhaustedRouter(engine.decisions.router)
        aisi = world.agents["aisi"]
        record = asyncio.run(engine.close_chapter(aisi, "failed", trigger="manual", reason="test"))
        self.assertIsNotNone(record)
        self.assertEqual(record.biography_source, "template")
        self.assertIn("didn't work out", record.biography_line)
        self.assertEqual(record.emotional_residue, "unmoored")   # outcome default
        self.assertEqual(aisi.chapter.chapter_type, "interlude")
        closed = [e for e in engine.bus.events if e.verb == "chapter_closed"]
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].text, record.biography_line)
        self.assertTrue(closed[0].detail.startswith("failed"))
        self.assertEqual(engine.chapter_stats, {"closed": 1, "llm": 0, "template": 1})

    def test_mock_reflection_is_used_when_available(self):
        world, engine = make_world()
        aisi = world.agents["aisi"]
        record = asyncio.run(engine.close_chapter(aisi, "completed", trigger="manual"))
        self.assertEqual(record.biography_source, "llm")
        self.assertTrue(record.biography_line.startswith("I set out to"))
        self.assertEqual(record.emotional_residue, "fulfilled")
        calls = [c for c in engine.decisions.router.usage.calls if c.task_type == "chapter_closure"]
        self.assertEqual(len(calls), 1)                    # exactly one LLM call per closure

    def test_second_close_is_a_noop(self):
        world, engine = make_world()
        aisi = world.agents["aisi"]
        self.assertIsNotNone(asyncio.run(engine.close_chapter(aisi, "completed")))
        self.assertIsNone(asyncio.run(engine.close_chapter(aisi, "completed")))
        self.assertEqual(len(aisi.chapter_history), 1)


class ClosureMaterialWindow(unittest.TestCase):
    """A: material is filtered to the chapter span; aftermath only when asked for."""

    def setUp(self):
        self.world, self.engine = make_world()
        self.aisi = self.world.agents["aisi"]
        mem = self.aisi.memory
        mem.add(MemoryItem(minute=1 * DAY + 60, importance=6, text="Wired {landmark:installation} at {loc:park} all afternoon."))
        mem.add(MemoryItem(minute=2 * DAY + 60, importance=8, text="I finished {landmark:installation} at {loc:park}. It's done at last."))
        mem.add(MemoryItem(minute=2 * DAY + 900, importance=5, text="Talked with {agent:xixi} at {loc:park}.", kind="conversation"))
        mem.add(MemoryItem(minute=40 * DAY, importance=7, text="I need to focus on my light installation project."))
        mem.add(MemoryItem(minute=41 * DAY, importance=5, text="Talked with {agent:oula} at {loc:cafe}.", kind="conversation"))
        mem.add(MemoryItem(minute=90 * DAY, importance=9, text="The light installation deadline feels distant when my attention drifts."))

    def test_out_of_window_memories_excluded(self):
        mat = chapters_mod.closure_material(self.aisi, self.world, self.aisi.chapter, 100 * DAY,
                                            ended_minute=2 * DAY + 60)
        texts = [m["text"] for m in mat["memories"]]
        self.assertTrue(any("Wired" in t for t in texts))
        self.assertTrue(any("I finished" in t for t in texts))
        self.assertFalse(any("focus on my light" in t for t in texts))      # Day 41: after the end
        self.assertFalse(any("deadline feels distant" in t for t in texts))  # Day 91: after the end
        self.assertEqual(mat["window"]["start_day"], 1)
        self.assertEqual(mat["window"]["end_day"], 3)
        self.assertEqual(mat["aftermath"], [])
        # relationship summary counts only in-window talks
        ids = [x["id"] for x in mat["relationships"]["most_interacted"]]
        self.assertIn("xixi", ids)
        self.assertNotIn("oula", ids)

    def test_aftermath_window_is_labelled_separately(self):
        mat = chapters_mod.closure_material(self.aisi, self.world, self.aisi.chapter, 100 * DAY,
                                            ended_minute=2 * DAY + 60, aftermath_window=(34, 64),
                                            forbid_terms=("finished",))
        self.assertEqual([m["text"] for m in mat["aftermath"]], ["I need to focus on my light installation project."])
        self.assertFalse(any("focus on my light" in m["text"] for m in mat["memories"]))
        prompt = builders.chapter_closure_prompt(self.aisi, mat, "abandoned", ["x"])
        self.assertIn("Afterwards", prompt[1]["content"])
        self.assertIn("Day 34 to Day 64", prompt[1]["content"])
        self.assertIn("Never use these words", prompt[0]["content"])
        # forbidden term gate + aftermath ids accepted as refs
        aft_id = mat["aftermath"][0]["id"]
        self.assertIsNone(chapters_mod.validate_closure_output(
            {"biography_line": "I finished the piece I set out to build, in the end."}, mat))
        ok = chapters_mod.validate_closure_output(
            {"biography_line": "I let the installation go and never really went back to it.",
             "emotional_residue": "relieved", "memory_refs": [aft_id]}, mat)
        self.assertEqual(ok["memory_refs"], [{"id": aft_id, "text": "I need to focus on my light installation project."}])


class PromptAndSignals(unittest.TestCase):
    def test_character_card_uses_chapter_narrative(self):
        world, _ = make_world()
        aisi, jiji = world.agents["aisi"], world.agents["jiji"]
        self.assertIn("building an interactive light installation", builders.character_card(aisi))
        self.assertNotIn("Top goal", builders.character_card(aisi))
        self.assertIn("Standing aim: Make the cafe popular", builders.character_card(jiji))   # ordinary keeps its aim
        chapters_mod.apply_closure(aisi, world, "completed", "I finished the installation at last.", "restless", [], DAY)
        card = builders.character_card(aisi)
        self.assertNotIn("installation", card)
        self.assertIn("itching for the next thing", card)

    def test_validate_closure_output(self):
        material = {"memories": [{"id": "deadbeef", "text": "x"}]}
        ok = chapters_mod.validate_closure_output(
            {"biography_line": "I finished the piece I'd been building for weeks.", "emotional_residue": "proud",
             "memory_refs": ["deadbeef", "nope"]}, material)
        self.assertEqual(ok["emotional_residue"], "proud")
        self.assertEqual(ok["memory_refs"], [{"id": "deadbeef", "text": "x"}])
        self.assertIsNone(chapters_mod.validate_closure_output({"biography_line": "ok"}, material))
        self.assertIsNone(chapters_mod.validate_closure_output(
            {"biography_line": "She finished the piece she had been building for weeks."}, material))  # not 1st person
        self.assertIsNone(chapters_mod.validate_closure_output(
            {"biography_line": "我終於完成了那個裝置，真的很開心。"}, material))                      # internal English only

    def test_secret_resolution_signals_closure(self):
        world, engine = make_world()
        xixi, aisi = world.agents["xixi"], world.agents["aisi"]
        secret = next(s for s in engine.decisions.secrets.secrets_of("xixi") if s.about == "aisi")
        fired = []
        engine.decisions.on_chapter_signal = lambda a, o, tr, r: fired.append((a.id, o, tr))
        engine.decisions._resolve_secret(xixi, secret, DAY, "Xixi finally opened up to Aisi.")
        self.assertEqual(fired, [("xixi", "completed", "secret_resolved")])

    def test_interlude_lapses_into_ordinary(self):
        world, engine = make_world()
        aisi = world.agents["aisi"]
        chapters_mod.apply_closure(aisi, world, "completed", "I finished it at last, and it lit up.", "relieved", [], DAY)
        until = aisi.chapter.until_day
        self.assertIsNone(chapters_mod.end_interlude(aisi, until - 1))
        new = chapters_mod.end_interlude(aisi, until)
        self.assertEqual(new.chapter_type, "ordinary")
        self.assertIs(aisi.chapter, new)


if __name__ == "__main__":
    unittest.main()
