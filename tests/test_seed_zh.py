"""The zh-native seed is a transcription, not a new cast.

    python -m unittest tests.test_seed_zh -v

Every structural setting -- gender, orientation, the five personality dimensions,
romance inclination, occupation, traits, routines, relationships, secret
sensitivities -- must be identical in both languages; only the words change.
"""

from __future__ import annotations

import dataclasses
import os
import unittest
from unittest import mock

os.environ["AI_TOWN_LIVE"] = "0"
os.environ["AI_TOWN_DB_URL"] = ""

from backend.app.agents import texts
from backend.app.social.secrets import SecretRegistry
from data import seed


def build(lang):
    with mock.patch.dict(os.environ, {"AI_TOWN_LANG": lang}):
        agents = {a.id: a for a in seed.build_agents()}
        reg = SecretRegistry()
        seed.seed_secrets(reg)
        locs = {l.id: l for l in seed.build_locations()}
    return agents, list(reg.secrets.values()), locs


class SameCast(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.en, cls.en_secrets, cls.en_locs = build("en")
        cls.zh, cls.zh_secrets, cls.zh_locs = build("zh-TW")

    def test_structure_is_identical(self):
        self.assertEqual(set(self.en), set(self.zh))
        for aid, a in self.en.items():
            b = self.zh[aid]
            for f in ("id", "name", "age", "occupation", "gender", "traits", "personality",
                      "daily_wage", "off_days", "romantic_inclination", "orientation_bias",
                      "reflection_threshold"):
                self.assertEqual(getattr(a.profile, f), getattr(b.profile, f), f"{aid}.{f}")
            self.assertEqual([g["priority"] for g in a.profile.goals], [g["priority"] for g in b.profile.goals])
            self.assertEqual([(e.start, e.action, e.location) for e in a.routine.entries],
                             [(e.start, e.action, e.location) for e in b.routine.entries])
            self.assertEqual({k: dataclasses.asdict(v) for k, v in a.relationships.items()},
                             {k: dataclasses.asdict(v) for k, v in b.relationships.items()})
            self.assertEqual([(m.minute, m.importance, m.kind) for m in a.memory.items],
                             [(m.minute, m.importance, m.kind) for m in b.memory.items])
            self.assertEqual(a.chapter.chapter_type if a.chapter else None,
                             b.chapter.chapter_type if b.chapter else None)
        self.assertEqual([(s.owner, s.sensitivity, s.about) for s in self.en_secrets],
                         [(s.owner, s.sensitivity, s.about) for s in self.zh_secrets])

    def test_every_description_is_transcribed(self):
        for aid, b in self.zh.items():
            self.assertTrue(texts.has_cjk(b.profile.speech_style), aid)
            for g in b.profile.goals:
                self.assertTrue(texts.has_cjk(g["goal"]), g)
            for m in b.memory.items:
                self.assertTrue(texts.has_cjk(m.text), m.text)
            if b.chapter is not None:
                self.assertTrue(texts.has_cjk(b.chapter.goal + b.chapter.title + b.chapter.narrative), aid)
        for s in self.zh_secrets:
            self.assertTrue(texts.has_cjk(s.text), s.text)
        self.assertEqual(set(seed.SPEECH_STYLE), set(seed.SPEECH_STYLE_ZH))
        self.assertEqual(set(seed.SEED_PURSUITS), set(seed.SEED_PURSUITS_ZH))

    def test_english_is_untouched(self):
        self.assertEqual(self.en["xue"].memory.items[0].text, "Argued with the manager last week.")
        self.assertEqual(self.en["aisi"].chapter.goal, "Finish the interactive light installation in the park")

    def test_the_rules_still_find_their_hooks_in_chinese(self):
        # the pursuit's landmark, the quit-job transition and the confide target all
        # match the transcribed text exactly as they matched the English
        self.assertEqual(self.zh["aisi"].chapter.related_landmark_id, "installation")
        from backend.app.agents import chapters as chapters_mod
        from backend.app.agents import transitions as transitions_mod
        quit_ = transitions_mod.REGISTRY["quit_job"]
        self.assertTrue(chapters_mod.goal_matches_chapter(self.zh["xue"].chapter, *quit_.clears_goal))
        xue_secret = next(s for s in self.zh_secrets if s.owner == "xue")
        self.assertTrue(any(k in xue_secret.text for k in quit_.resolves_secret_kw))
        from backend.app.agents.decision import DecisionEngine
        xixi_secret = next(s for s in self.zh_secrets if s.owner == "xixi")
        with mock.patch.dict(os.environ, {"AI_TOWN_LANG": "zh-TW"}):
            self.assertEqual(DecisionEngine.forward_goal(self.zh["xixi"].chapter.goal, xixi_secret),
                             "繼續跟艾斯學寫程式")


if __name__ == "__main__":
    unittest.main()
