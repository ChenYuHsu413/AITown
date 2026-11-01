"""The zh-native town: Chinese at the source, and the gates turned around to match.

    python -m unittest tests.test_zh_native -v

The new era writes its knowledge in Traditional Chinese -- nothing is translated
afterwards -- so every gate that used to keep English knowledge English now holds
Chinese knowledge to a Chinese standard, and every rule that matched text by its
words must still find them without spaces. English runs keep their old behaviour;
each test pins its language with ``zh()`` / ``en()`` instead of relying on the
import-order of the other test modules.
"""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest import mock

os.environ["AI_TOWN_LIVE"] = "0"
os.environ["AI_TOWN_DB_URL"] = ""

from backend.app.agents import chapters as chapters_mod
from backend.app.agents import texts
from backend.app.agents.decision import (
    DecisionEngine, _knowledge_lang_ok, _wish_text_zh_ok, belief_text_ok, gender_ok,
    reflection_zh_ok, zh_quality_reason)
from backend.app.llm.embeddings import MockEmbedding
from backend.app.llm.factory import build_router
from backend.app.llm.prompts import builders
from backend.app.llm.providers.mock import MockProvider
from backend.app.simulation.engine import SimulationEngine
from backend.app.world.world import World
from data.seed import build_agents, build_locations


def zh():
    return mock.patch.dict(os.environ, {"AI_TOWN_LANG": "zh-TW"})


def en():
    return mock.patch.dict(os.environ, {"AI_TOWN_LANG": "en"})


def make_world():
    world = World(build_locations(), build_agents())
    engine = SimulationEngine(world, DecisionEngine(build_router(live=False)))   # publishes the roster
    return world, engine


class Catalogue(unittest.TestCase):
    def test_the_same_key_speaks_each_language(self):
        with en():
            self.assertEqual(texts.T("talked_with", other="ange", loc="cafe"),
                             "Talked with {agent:ange} at {loc:cafe}.")
        with zh():
            self.assertEqual(texts.T("talked_with", other="ange", loc="cafe"),
                             "在{loc:cafe}和{agent:ange}聊了聊。")

    def test_the_conversation_counter_reads_both_templates(self):
        self.assertEqual(texts.talked_with_partner("Talked with {agent:ange} at {loc:cafe}."), "ange")
        self.assertEqual(texts.talked_with_partner("在{loc:cafe}和{agent:ange}聊了聊。"), "ange")
        self.assertIsNone(texts.talked_with_partner("在{loc:cafe}吃了一頓飯。"))

    def test_chapters_write_the_towns_language(self):
        world, _ = make_world()
        with zh():
            ch = chapters_mod.make_ordinary(world.agents["lengyue"], 3)
            self.assertEqual(ch.title, "平常的日子")
            self.assertIn("醫生", ch.narrative)
            self.assertIn("章節", chapters_mod.make_interlude("relieved", 3, 6).title)
        with en():
            self.assertIn("doctor", chapters_mod.make_ordinary(world.agents["lengyue"], 3).narrative)


class WordsWithoutSpaces(unittest.TestCase):
    def setUp(self):
        make_world()

    def test_bigrams_skip_function_characters(self):
        bg = texts.cjk_bigrams("我在公園看到那座光影裝置")
        self.assertIn("公園", bg)
        self.assertIn("裝置", bg)
        self.assertNotIn("我在", bg)

    def test_a_theme_matches_a_chinese_memory(self):
        theme = chapters_mod.theme_keywords("完成公園裡的互動光影裝置")
        words = texts.word_set("我今天又去公園弄那座裝置了")
        self.assertGreaterEqual(len(words & set(theme)), chapters_mod.BIOGRAPHY_TOPIC_MIN)

    def test_names_meet_on_the_id(self):
        # a one-character zh name yields no bigram; the roster pass makes it findable
        self.assertIn("xue", texts.tokens("雪今天看起來很累"))
        self.assertIn("ange", texts.tokens("在{loc:cafe}和{agent:ange}聊了聊。"))
        with zh():
            self.assertTrue(texts.mentions("我和安哥聊了很久", "ange"))

    def test_the_mock_embedding_sees_chinese_similarity(self):
        emb = MockEmbedding()
        a = asyncio.run(emb.embed("我想多花時間待在螢火公園"))
        b = asyncio.run(emb.embed("我想常常去螢火公園走走"))
        c = asyncio.run(emb.embed("麵包店的生意最近很差"))

        def cos(x, y):
            return sum(i * j for i, j in zip(x, y))
        self.assertGreater(cos(a, b), cos(a, c))
        self.assertGreater(cos(a, b), 0.2)

    def test_english_embeddings_are_unchanged(self):
        # pure English still goes through the old whitespace split
        emb = MockEmbedding()
        v = asyncio.run(emb.embed("I want to visit the park"))
        words = [w for w in "i want to visit the park".split() if len(w) >= 2]
        acc = [0.0] * emb.dim
        for w in words:
            for i, x in enumerate(emb._word_vec(w)):
                acc[i] += x
        n = sum(x * x for x in acc) ** 0.5
        self.assertEqual(v, [x / n for x in acc])


class ChineseQualityGate(unittest.TestCase):
    """The gibberish gate turned around: zh-native knowledge must BE Chinese."""

    def setUp(self):
        make_world()

    def test_clean_chinese_passes(self):
        self.assertIsNone(zh_quality_reason("我最近一直在想,要不要跟艾斯坦白。"))

    def test_english_soup_and_empty_are_rejected(self):
        self.assertIsNotNone(zh_quality_reason("I have been thinking about Aisi a lot lately."))
        self.assertIsNotNone(zh_quality_reason("今天我去了cafeteria喝咖啡"))   # latin glued into CJK
        self.assertIsNotNone(zh_quality_reason("好"))
        self.assertIsNotNone(zh_quality_reason(""))

    def test_rumor_language_gate_is_reversed(self):
        with zh():
            self.assertTrue(_knowledge_lang_ok("聽說雪最近在找新工作。"))
            self.assertFalse(_knowledge_lang_ok("Xue has been looking for a new job."))
        with en():
            self.assertTrue(_knowledge_lang_ok("Xue has been looking for a new job."))
            self.assertFalse(_knowledge_lang_ok("聽說雪最近在找新工作。"))

    def test_reflection_and_wish_fall_through_on_english(self):
        with zh():
            self.assertFalse(reflection_zh_ok({"insights": ["Today was a long day."]}))
            self.assertTrue(reflection_zh_ok({"insights": ["今天過得很漫長,但我學到了一些事。"]}))
            self.assertFalse(_wish_text_zh_ok({"title": "Face Aisi", "statement": "我想面對艾斯。",
                                               "motivation": "這件事一直壓在我心上。"}))
            self.assertTrue(_wish_text_zh_ok({"title": "面對艾斯", "statement": "我想弄清楚艾斯的心意。",
                                              "motivation": "這件事一直壓在我心上。"}))
            self.assertTrue(_wish_text_zh_ok({"no_wish": True, "reason": "nothing"}))
        with en():
            self.assertTrue(reflection_zh_ok({"insights": ["Today was a long day."]}))

    def test_belief_gate_rejects_english_in_a_zh_town(self):
        world, _ = make_world()
        with zh():
            self.assertFalse(belief_text_ok("Xue seems determined to leave her job", world))
            self.assertTrue(belief_text_ok("雪似乎越來越想離開她的工作", world))


class ChineseGenderGate(unittest.TestCase):
    def setUp(self):
        make_world()

    def test_chinese_pronouns_are_held_to_the_roster(self):
        self.assertFalse(gender_ok("我跟冷月聊了很久,他看起來很累。"))     # 冷月 is female
        self.assertTrue(gender_ok("我跟冷月聊了很久,她看起來很累。"))
        self.assertFalse(gender_ok("安哥說她明天會早點開店。"))            # 安哥 is male
        self.assertTrue(gender_ok("安哥說他明天會早點開店。"))

    def test_the_exclusions_still_hold(self):
        self.assertTrue(gender_ok("冷月說其他人都還好。"))
        self.assertTrue(gender_ok("冷月擔心他們會誤會。"))
        self.assertTrue(gender_ok("冷月不喜歡麻煩他人。"))

    def test_two_names_are_left_alone(self):
        self.assertTrue(gender_ok("冷月和安哥聊天,他很開心。"))


class ChineseBiography(unittest.TestCase):
    MATERIAL = {"memories": [{"id": "aa", "text": "我在公園待了3個下午。"}],
                "window": {"days": 12, "start_day": 2, "end_day": 14}}

    def test_a_chinese_first_person_line_passes(self):
        with zh():
            out = chapters_mod.validate_closure_output(
                {"biography_line": "我花了12天,終於把那座裝置完成了。", "emotional_residue": "proud"},
                self.MATERIAL)
            self.assertIsNotNone(out)

    def test_english_third_person_and_invented_numbers_fail(self):
        with zh():
            for line in ("I finally finished the installation after all.",
                         "她花了很多天,終於把那座裝置完成了。",
                         "我花了30天,終於把那座裝置完成了。"):
                self.assertIsNone(chapters_mod.validate_closure_output(
                    {"biography_line": line}, self.MATERIAL), line)

    def test_the_template_fallback_is_chinese(self):
        world, _ = make_world()
        with zh():
            ch = chapters_mod.make_pursuit("完成公園的光影裝置", "做裝置", "我正在做裝置。", 1)
            self.assertEqual(chapters_mod.template_biography(world.agents["aisi"], ch, "completed"),
                             "我曾下定決心要完成公園的光影裝置,而我做到了。人生的這一段已經畫下句點。")


class Prompts(unittest.TestCase):
    def setUp(self):
        self.world, self.engine = make_world()

    def test_knowledge_prompts_ask_for_chinese(self):
        a = self.world.agents["xue"]
        with zh():
            sys = builders.reflection_prompt(a, ["在{loc:cafe}和{agent:ange}聊了聊。"])[0]["content"]
            self.assertIn("Traditional Chinese", sys)
            self.assertNotIn("Write all text in English", sys)
            self.assertIn("安哥 is male", sys)
        with en():
            sys = builders.reflection_prompt(a, ["x"])[0]["content"]
            self.assertIn("Write all text in English", sys)

    def test_held_wishes_carry_their_true_scale(self):
        mat = {"biography": [], "memories": [{"id": "abcd1234", "importance": 5, "text": "x"}],
               "relationships": {"closest": [], "friction": []}, "routine": [],
               "unvisited_locations": [], "no_regular_overlap_with": [],
               "active_wishes": [{"title": "Find quiet with Ange", "statement": "s", "scale": "minor"}],
               "personality": {"openness": 0.5}, "capacity": {"major": 1, "minor": 1}}
        user = builders.wish_generation_prompt(self.world.agents["lengyue"], mat)[1]["content"]
        self.assertIn('[minor] "Find quiet with Ange"', user)


class MockFloorSpeaksChinese(unittest.TestCase):
    def test_reflection_floor_is_chinese_in_a_zh_town(self):
        with zh():
            res = asyncio.run(MockProvider().generate(
                [{"role": "system", "content": "Task: reflection."}, {"role": "user", "content": "x"}],
                schema={"type": "object"}))
            self.assertTrue(all(texts.has_cjk(i) for i in res.parsed["insights"]))


class ClaudeFirst(unittest.TestCase):
    def test_claude_heads_every_chain_when_a_key_is_set(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "AI_TOWN_LANG": "zh-TW"}):
            router = build_router(live=True)
        heads = {t: f"{c[0].name}/{c[0].model}" for t, c in router.tiers.items()}
        self.assertEqual(heads["cheap"], "anthropic/claude-haiku-5-5")
        self.assertEqual(heads["normal"], "anthropic/claude-haiku-5-5")
        self.assertEqual(heads["smart"], "anthropic/claude-sonnet-5-5")
        for task, chain in router.task_chains.items():
            self.assertEqual(chain[0].name, "anthropic", task)
            self.assertEqual(chain[-1].name, "mock", task)        # the floor stays last
        self.assertEqual(router.task_chains["wish_generation"][0].model, "claude-sonnet-5-5")

    def test_no_key_leaves_the_chains_as_they_were(self):
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            router = build_router(live=True)
        self.assertFalse(any(p.name == "anthropic" for c in router.tiers.values() for p in c))

    def test_budget_default_is_ten(self):
        from backend.app.llm.factory import _budget_from_env
        env = {k: v for k, v in os.environ.items() if k != "AI_TOWN_BUDGET_USD"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(_budget_from_env(), 10.0)


class TranslationRetired(unittest.TestCase):
    def test_a_zh_native_town_never_calls_the_translator(self):
        from backend.app import server
        sim = server.Sim()
        with zh(), mock.patch.object(sim.router, "generate") as gen:
            out = asyncio.run(sim.translate_text("Xue seems tired lately."))
        gen.assert_not_called()
        self.assertEqual(out, "雪 seems tired lately.")          # shown as written, names normalized
        with zh():
            self.assertEqual(sim.backfill_translations(), 0)


if __name__ == "__main__":
    unittest.main()
