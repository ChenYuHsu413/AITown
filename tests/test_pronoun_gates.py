"""The two pronoun gates (see decision.person_shift_ok / decision.gender_ok).

    python -m unittest tests.test_pronoun_gates -v

Both are mechanical backstops behind the roster instructions in the prompts. The
cases below are taken from the real contamination found in the 2026-09-04
diagnosis, so a regression here is a regression against evidence.
"""

from __future__ import annotations

import os
import unittest

os.environ["AI_TOWN_LIVE"] = "0"
os.environ["AI_TOWN_LANG"] = "zh-tw"
os.environ["AI_TOWN_DB_URL"] = ""

from backend.app.agents.decision import (
    DecisionEngine, gender_ok, person_shift_ok, reflection_gender_ok, translate_gender_ok)
from backend.app.llm.factory import build_router
from backend.app.llm.prompts import builders
from backend.app.simulation.engine import SimulationEngine
from backend.app.world.world import World
from data.seed import build_agents, build_locations


def make_world():
    world = World(build_locations(), build_agents())
    SimulationEngine(world, DecisionEngine(build_router(live=False)))   # publishes the roster
    return world


class TranslationPersonGate(unittest.TestCase):
    """A first-person line must stay first person; losing 我 means the speaker was
    replaced by a guess, and the guess is where the mis-gendering comes from."""

    def setUp(self):
        make_world()

    def test_rejects_the_real_regressions(self):
        # the three person shifts found in the live translation cache
        self.assertFalse(person_shift_ok(
            "After 31 days of hesitation, I finally asked Aisi to teach me programming, "
            "which feels like a huge relief and a step forward.",
            "經過31天的猶豫，她終於鼓起勇氣請艾斯教她寫程式，感覺如釋重負，也向前跨了一大步。"))
        self.assertFalse(person_shift_ok(
            'Secret: "I am quietly doubting whether my installation can truly connect with people."',
            "艾斯暗自懷疑她的裝置是否真的與人有共鳴，她甚至有些害怕。"))
        self.assertFalse(person_shift_ok(
            "Another ordinary day at the cafe; still no clear direction beyond my standing goal.",
            "又是咖啡店裡平凡的一天；除了站著不動的目標之外，她還是沒什麼明確的方向。"))

    def test_does_not_kill_legitimate_third_person(self):
        # first person that also, correctly, refers to someone else in the third person
        self.assertTrue(person_shift_ok(
            "Xue confided in me: I've been interviewing at a company in Taipei.",
            "雪向我透露：她一直在台北的一家公司面試。"))
        self.assertTrue(person_shift_ok(
            "I had a nice chat with Jiji at the cafe; it was good to connect.",
            "我在咖啡廳和ㄐㄐ聊得很愉快，能和他聯繫上感覺很好。"))
        # pure third-person source -- the gate has no business here
        self.assertTrue(person_shift_ok(
            "Lengyue seems increasingly determined to leave her job.",
            "冷月似乎越來越堅定要離開她的工作。"))
        # A framing verb's "I" is routinely dropped in Chinese ("I heard that..." ->
        # 「聽說...」). That is good style, not a lost speaker -- and the source's own
        # third person is where the 她 legitimately comes from. Real rows from the
        # live cache that an earlier draft of this gate wrongly rejected:
        self.assertTrue(person_shift_ok(
            "I heard Jiji noticed Lengyue acting weird with Xixi, she might be keeping a secret.",
            "聽說ㄐㄐ發現冷月跟希希行為怪怪的，她可能在隱瞞什麼秘密。"))
        self.assertTrue(person_shift_ok(
            "Today I noticed that Xue seems preoccupied, though she didn't say anything directly.",
            "今天注意到雪好像心事重重的，雖然她沒直接講工作的事。"))
        self.assertTrue(person_shift_ok(
            "Jiji is worried that Lengyue's crush on me might cause tension at her cafe.",
            "ㄐㄐ擔心冷月對他的好感會讓她的咖啡廳氣氛變緊張。"))
        # first person with no third-person pronoun in the translation
        self.assertTrue(person_shift_ok(
            "I finished the installation at the park.", "我在公園完成了那個裝置。"))

    def test_ignores_non_pronoun_characters(self):
        """他人 / 其他 / 他們 are not somebody's pronoun."""
        self.assertTrue(person_shift_ok(
            "I shared others' private struggles too readily.",
            "我太輕易分享他人的隱私困境。"))
        self.assertTrue(person_shift_ok(
            "I could not resolve my other feelings.", "我沒解決我其他的情緒。"))

    def test_empty_and_missing_are_safe(self):
        self.assertTrue(person_shift_ok("", "什麼"))
        self.assertTrue(person_shift_ok("I did it.", ""))


class TranslationGenderGate(unittest.TestCase):
    """The companion to the person gate: speaker kept, gender invented."""

    def setUp(self):
        make_world()

    def test_rejects_a_pronoun_the_roster_forbids(self):
        # the one row the person gate could not catch (person preserved, 他 for a woman)
        self.assertFalse(translate_gender_ok(
            "Aisi seems like a reliable person I can grow closer to.",
            "艾斯看起來是個可靠的人，我可以跟他變得更親近。"))
        self.assertFalse(translate_gender_ok(
            "Xixi hesitates before speaking.", "希希開口前會猶豫，她總是這樣。"))

    def test_accepts_the_correct_pronoun(self):
        self.assertTrue(translate_gender_ok(
            "Aisi seems like a reliable person I can grow closer to.",
            "艾斯看起來是個可靠的人，我可以跟她變得更親近。"))
        self.assertTrue(translate_gender_ok(
            "Xixi hesitates before speaking.", "希希開口前會猶豫，他總是這樣。"))

    def test_leaves_multi_name_and_nameless_text_alone(self):
        # two residents named -> whose pronoun is whose is guesswork
        self.assertTrue(translate_gender_ok(
            "I heard Jiji noticed Lengyue acting weird, she might be keeping a secret.",
            "聽說ㄐㄐ發現冷月怪怪的，她可能在隱瞞什麼。"))
        # no resident named at all
        self.assertTrue(translate_gender_ok(
            "Someone seemed distant today.", "今天有人看起來有點疏離，他沒說什麼。"))
        self.assertTrue(translate_gender_ok("", "他"))
        self.assertTrue(translate_gender_ok("Aisi is here.", ""))

    def test_ignores_non_pronoun_characters(self):
        self.assertTrue(translate_gender_ok(
            "Aisi shares things with me she hides from others.",
            "艾斯跟我分享了她不向他人展現的事。"))          # 他人 must not count as 他


class GenerationGenderGate(unittest.TestCase):
    """Free text that names exactly one resident and mis-genders them is rejected."""

    def setUp(self):
        make_world()

    def test_rejects_the_real_regressions(self):
        self.assertFalse(gender_ok(
            "Chatted with Xixi at Rainlisten House — she seems to be in a good place lately."))
        self.assertFalse(gender_ok(
            "Ange confided that she may have feelings for me, which is surprising and flattering."))
        self.assertFalse(gender_ok(
            "Aisi trusted me with a real vulnerability about his art."))
        self.assertFalse(gender_ok(
            "Azong seems to enjoy my company, as he came to talk twice today."))

    def test_accepts_correct_pronouns(self):
        self.assertTrue(gender_ok("Xixi keeps freezing before he can ask his question."))
        self.assertTrue(gender_ok("Aisi finished her installation and she looked relieved."))
        self.assertTrue(gender_ok("冷月似乎越來越堅定要離開她的工作。"))

    def test_does_not_touch_ambiguous_or_nameless_text(self):
        # two residents named -> which pronoun belongs to whom is guesswork
        self.assertTrue(gender_ok(
            "Lengyue confided she has feelings for Oula, but I worry she noticed my interest in Xixi."))
        # no resident named at all
        self.assertTrue(gender_ok("Today followed the usual rhythm, and she seemed content."))
        self.assertTrue(gender_ok("I made real progress on something of my own."))
        self.assertTrue(gender_ok(""))

    def test_reflection_payload_is_checked_field_by_field(self):
        bad_insight = {"insights": ["Xixi mentioned she is tired."], "beliefs": []}
        bad_belief = {"insights": [], "beliefs": [{"subject": "Xixi", "text": "Xixi hides her worry."}]}
        bad_secret = {"insights": [], "new_secret": {"text": "Ange told me she is struggling."}}
        good = {"insights": ["Xixi mentioned he is tired."],
                "beliefs": [{"subject": "Aisi", "text": "Aisi is proud of her work."}],
                "new_secret": {"text": "I keep something to myself."}}
        for payload in (bad_insight, bad_belief, bad_secret):
            self.assertFalse(reflection_gender_ok(payload), payload)
        self.assertTrue(reflection_gender_ok(good))
        self.assertTrue(reflection_gender_ok(None))
        self.assertTrue(reflection_gender_ok({}))


class GateCounters(unittest.TestCase):
    def test_counters_exist_and_move(self):
        before = dict(builders.GATE_REJECTS)
        for key in ("translate_person", "translate_gender", "generation_gender"):
            builders.note_gate_reject(key)
            self.assertEqual(builders.GATE_REJECTS[key], before[key] + 1)


class TranslatePromptContext(unittest.TestCase):
    """The prompt now names the speaker and forbids the person shift."""

    def setUp(self):
        make_world()

    def test_owner_and_person_rule_are_present(self):
        msgs = builders.translate_prompt(
            "After 31 days of hesitation, I finally asked Aisi to teach me programming.",
            owner="希希", owner_gender="male")
        sys_msg = msgs[0]["content"]
        self.assertIn("This text belongs to 希希 (male)", sys_msg)
        self.assertIn('that "I" is 希希', sys_msg)
        self.assertIn("Preserve the grammatical person exactly", sys_msg)
        self.assertIn("NEVER rewrite it into third-person narration", sys_msg)
        self.assertIn("希希 is male", sys_msg)          # the roster is still there

    def test_owner_is_optional(self):
        sys_msg = builders.translate_prompt("Something happened.")[0]["content"]
        self.assertNotIn("This text belongs to", sys_msg)
        self.assertIn("Preserve the grammatical person exactly", sys_msg)

    def test_appraise_now_carries_the_gender_roster(self):
        sys_msg = builders.appraise_prompt("Xixi is upset.")[0]["content"]
        self.assertIn("Xixi is male", sys_msg)


if __name__ == "__main__":
    unittest.main()
