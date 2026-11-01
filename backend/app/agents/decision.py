"""Decision System -- the 4-level funnel.

    Simulation tick
          ↓
    Need decision?
     ┌────┴─────┐
     NO         YES
     │           │
    Rules      LLM Router (cheap → normal → smart)

Level 0 (rules)  : follow routine, low-energy rest, sleep, movement
Level 1 (cheap)  : "should I talk to this person?", memory importance
Level 2 (normal) : dialogue generation, off-routine action choice
Level 3 (smart)  : end-of-day reflection

Every decision also returns a DecisionTrace so nothing is a black box.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import random
import re
import unicodedata
from dataclasses import dataclass, field

from .. import pacing
from ..llm.prompts import builders
from ..llm.router import LLMRouter
from ..llm.usage import LLMCall
from ..social.rumors import RumorRegistry
from ..social.secrets import SecretRegistry
from ..world.world import Observation, World
from . import actions as actions_mod
from . import chapters as chapters_mod
from . import dayplan as dayplan_mod
from . import mood as mood_mod
from . import romance as romance_mod
from . import transitions as transitions_mod
from . import texts
from . import wishes as wishes_mod
from .agent import Agent
from .core import Belief, MemoryItem
from .routine import RoutineEntry

BELIEF_CONTEXT_MIN = 0.3      # a belief this confident (or more) colours dialogue + trust math

RUMOR_DISTORT_CHANCE = 0.35   # probability a shared rumor mutates in the retelling

CONFIDE_TRUST_BASE = 55       # base trust needed to confide; the bar rises with the secret's sensitivity
CONFIDE_MAX_P = 0.5           # cap on the per-conversation confide probability
LEAK_MAX_P = 0.35            # cap on the per-conversation leak probability

# Per-PROVIDER timeout for dialogue generation (seconds). A provider slower than
# this is abandoned and the chain falls through to the next one (recorded normally),
# so a genuinely hung model hands off instead of dropping the whole conversation to
# the mock floor. Set ABOVE DeepSeek's full latency tail (measured p95 ~18s, worst-
# case tail to the low 40s) on purpose: DeepSeek is the quality anchor for zh, and
# the free fallbacks (Gemma) fail the strict zh gate far more often, so cutting
# DeepSeek early trades a slow turn for a likely mock floor. Give it the room to
# answer; hand off only when it truly hangs. Widened 40s -> 60s under the "rather
# slow than canned" policy: the user explicitly accepts a slower turn, and the
# non-blocking engine means the extra wait only makes the two speakers "brew" a
# little longer -- it never freezes the town. Env-overridable.
try:
    DIALOGUE_PROVIDER_TIMEOUT_S: float = float(os.environ.get("AI_TOWN_DIALOGUE_PROVIDER_TIMEOUT", "60") or "60")
except ValueError:
    DIALOGUE_PROVIDER_TIMEOUT_S = 60.0

# Per-provider timeout for the cheap tasks that run SYNCHRONOUSLY inside a tick
# (should_talk, decision, distort, leak). These block the pacing loop until they
# return, so a single hung provider here would freeze the whole town (clock, agents,
# broadcasts) -- the observed night "freeze". 10s is generous for a cheap/short call;
# past that we abandon the provider and fall through the chain, so any one hung
# provider steals at most ~10s instead of parking the loop. Dialogue/reflection are
# backgrounded (own longer timeouts) and deliberately excluded. Env-overridable.
try:
    SYNC_CALL_TIMEOUT_S: float = float(os.environ.get("AI_TOWN_SYNC_CALL_TIMEOUT", "10") or "10")
except ValueError:
    SYNC_CALL_TIMEOUT_S = 10.0

TALK_COOLDOWN_MIN = 90        # don't re-approach the same person within 90 sim-minutes
TALK_DURATION_MIN = 10
LOW_ENERGY = 20

# Social initiative (maybe_arrange_meetup): a resident may, at most once every few days,
# arrange to meet a friend at a shared free window later that day.
MEETUP_DAILY_P = 0.18            # per-eligible-agent daily chance to try to arrange one
MEETUP_FRIEND_MIN = 45          # only invite someone you're at least this friendly with
MEETUP_PERSON_COOLDOWN = 3      # a person initiates at most once per this many sim-days
MEETUP_PAIR_COOLDOWN = 7        # a given pair meets at most once per this many sim-days
MEETUP_WINDOW_MIN = 180         # attend within this many sim-minutes of the appointed time, else drop
MEETUP_SOCIAL_ACTIONS = frozenset({"idle", "rest", "eat"})  # routine states open to a drop-in

# Words that named a PRIOR run's world -- e.g. a park "mural" (壁畫), long since
# replaced by a light installation. A generated turn mentioning one is almost
# certainly stale context bleeding through (the old culprit was a hardcoded mock
# line; the real model is fenced against inventing facts). We LOG it rather than
# reject -- the false-positive rate on a legitimate mention is unknown -- so the
# frequency can be watched and confirmed trending to zero. Extend as new artifacts
# from retired runs surface.
STALE_WORLD_TERMS = ("mural", "壁畫")

# Social tiering (pure rules, before the cheap should_talk call). Close friends
# chat freely; acquaintances less; near-strangers rarely -- so the cheap-tier call
# volume stays bounded as the town grows and social circles form on their own.
SOCIAL_TIER_FRIEND = 55       # friendship >= this -> full rate
SOCIAL_TIER_ACQUAINT = 35     # 35..55 -> x0.6; below -> x0.3
INTROVERT_EXTRAVERSION = 0.45  # quiet personalities (Leo, Grace) damp their rate a further x0.7


_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
# Latin letters glued straight onto CJK with no space -- the tell of weak-model
# "character soup" (e.g. "吃asks导"). Real zh keeps English proper nouns spaced
# and capitalized, so this stays clear of "覺得 David 最近...".
_GLUE_RE = re.compile(r"[㐀-䶿一-鿿][a-z]{3,}|[a-z]{3,}[㐀-䶿一-鿿]")


# Diagnostic mode: when set, every zh-gate rejection prints the offending turn text
# plus a reason code (grep '[gate-reject]'), so a sampling run can judge whether the
# gate is false-positiving on legitimate dialogue. Off by default -- pure observability,
# never changes behaviour.
_GATE_DIAG = os.environ.get("AI_TOWN_GATE_DIAG") == "1"


def _has_cjk(text: str) -> bool:
    """True if the text contains any CJK -- used to reject a zh hallucination in
    English-canonical knowledge (distort/leak), which must stay English so the
    version chain has one language and the display layer can translate it."""
    return bool(_CJK_RE.search(text or ""))


def zh_quality_reason(text: str) -> str | None:
    """Chinese quality gate for zh-native free text (reflection, rumor, wish,
    biography): the dialogue gibberish rules (corruption, glued mixed script, stuck
    loops, under 60% CJK -- which is what "a big chunk of English" looks like) plus an
    empty/dodge answer. None when the text is clean."""
    t = (text or "").strip()
    if not t or len(t) < 4 or t.lower().strip(" .!?。!?") in _BELIEF_JUNK:
        return "empty"
    return _zh_reject_reason(t)


def _knowledge_lang_ok(text: str) -> bool:
    """Is this generated knowledge text in the town's language? A zh-native town
    holds the Chinese quality gate; an English town rejects a stray zh line, as it
    always has (one language per version chain)."""
    if builders.lang_is_zh():
        return zh_quality_reason(text) is None
    return not _has_cjk(text)


_BELIEF_JUNK = {
    "ok", "okay", "none", "n/a", "na", "good", "fine", "bad", "nothing", "idk",
    "unknown", "yes", "no", "true", "false", "null", "nil", "...", "…", "n a",
}


def belief_text_ok(text: str, world: "World") -> bool:
    """Quality gate for reflection free text (lasting impressions + new secrets):
    reject the model's throwaway filler so 'ok' never lands in memory. Rejects
    generic non-answers; otherwise a CJK sentence (dense script) needs only a few
    characters, while a Latin sentence needs >=15 chars and either >=4 words or an
    explicit reference to a known resident/place. CJK-aware so historical Chinese
    knowledge is never mistaken for filler."""
    t = (text or "").strip()
    if not t:
        return False
    if t.lower().strip(" .!?\"'…").replace("/", " ").strip() in _BELIEF_JUNK:
        return False
    if builders.lang_is_zh() and _zh_reject_reason(t) is not None:
        return False                      # zh-native: English or soup is not a belief
    if _CJK_RE.search(t):                 # zh: a handful of characters is already a real sentence
        return len(t) >= 6
    if len(t) < 15:
        return False
    words = t.split()
    if len(words) >= 4:
        return True
    names = ({a.name.lower() for a in world.agents.values()}
             | {a.id for a in world.agents.values()}
             | {loc.name.lower() for loc in world.locations.values()}
             | set(world.locations))
    return any(w.lower().strip(".,;:!?'\"") in names for w in words)


def _zh_reject_reason(text: str) -> str | None:
    """Gibberish gate for a single zh dialogue turn, as a reason code (or None if the
    turn is clean). This is the precondition for putting a new model on the zh chain.
    Rejects the character-soup weak models emit:
      - the Unicode replacement char (encoding corruption),
      - latin letters glued into CJK with no spacing (mixed-script soup),
      - a long run of identical characters (a stuck decode loop),
      - fewer than 60% CJK among the letters (excluding punctuation/space) --
        i.e. the "Chinese" turn isn't actually mostly Chinese.
    A rejected turn drops the whole conversation to the next provider / mock.

    Refined against a live sample of rejections (task 1.2): the stuck-loop rule now
    fires at 6+ (was 4+) identical chars and never on CJK, so a genuine laugh/stall
    ("哈哈哈哈", "嗯嗯嗯嗯", "……") -- normal casual zh -- is not mistaken for soup; and
    the CJK-ratio check ignores ASCII digits/symbols so a turn quoting a time, price,
    or number ("晚上 7:30 老地方") is not demoted to a canned line."""
    if "�" in text:
        return "replacement-char"
    if _GLUE_RE.search(text):
        return "latin-glue"
    # A stuck decode loop repeats the SAME non-CJK char many times (e.g. "!!!!!!!");
    # in Chinese, repeated characters are ordinary emphasis/laughter, so exempt CJK
    # and only trip on a long run (6+).
    m = re.search(r"(.)\1{5,}", text)
    if m and not _CJK_RE.match(m.group(1)):
        return "char-repeat"
    # Count script only over "word" characters: CJK + Latin letters. Punctuation,
    # spaces, digits and symbols don't count either way, so a turn that legitimately
    # carries a clock time or a price isn't dragged under the 60% CJK bar by its digits.
    cjk = latin = 0
    for c in text:
        if _CJK_RE.match(c):
            cjk += 1
        elif c.isalpha():
            latin += 1
    total = cjk + latin
    if total == 0:
        return "no-letters"
    if cjk / total < 0.6:
        return f"low-cjk({cjk}/{total})"
    return None


def _zh_text_ok(text: str) -> bool:
    """Bool wrapper over ``_zh_reject_reason``; a rejected turn drops the whole
    conversation to the next provider / mock."""
    return _zh_reject_reason(text) is None


def _zh_reject_reason_legacy(text: str) -> str | None:
    """The pre-refinement gate, kept ONLY for the diagnostic comparison (task 1.2):
    a turn the legacy gate rejected but ``_zh_reject_reason`` now passes is a recovered
    false-positive -- direct evidence the refinement was warranted. Not used in the
    live path."""
    if "�" in text:
        return "replacement-char"
    if _GLUE_RE.search(text):
        return "latin-glue"
    if re.search(r"(.)\1{3,}", text):
        return "char-repeat"
    letters = [c for c in text if not c.isspace() and not unicodedata.category(c).startswith("P")]
    if not letters:
        return "no-letters"
    cjk = sum(1 for c in letters if _CJK_RE.match(c))
    if cjk / len(letters) < 0.6:
        return f"low-cjk({cjk}/{len(letters)})"
    return None


def _dialogue_ok(result) -> bool:
    """Structural quality gate for a generated conversation (passed to the router
    so a bad result is retried / falls through instead of landing on-screen):
    real turns, each with non-empty, sensibly-sized text. Catches truncated JSON
    and the empty-"text" JSON that Groq's 70b intermittently emits.

    In a zh run it ALSO runs the gibberish gate per turn (``_zh_text_ok``), which
    is what lets a Chinese-capable OpenRouter model sit on the zh chain safely:
    any character-soup it emits is rejected and falls through to mock. English
    runs are unaffected (the zh check only applies when the language is zh)."""
    parsed = result.parsed
    if not isinstance(parsed, dict):
        return False
    turns = parsed.get("turns")
    if not isinstance(turns, list) or not turns or len(turns) > 12:
        return False
    zh = builders.lang_is_zh()
    prov = f"{getattr(result, 'provider', '?')}/{getattr(result, 'model', '?')}"
    for t in turns:
        if not isinstance(t, dict):
            return False
        text = str(t.get("text", "")).strip()
        if not text or len(text) > 500:
            return False
        if zh:
            reason = _zh_reject_reason(text)
            if reason is not None:
                if _GATE_DIAG:  # task 1.2: capture the rejected sample for over-kill review
                    print(f"[gate-reject] {prov} reason={reason} text={text[:140]!r}", flush=True)
                return False
            if _GATE_DIAG:  # a turn the OLD gate would have wrongly killed but we now pass
                legacy = _zh_reject_reason_legacy(text)
                if legacy is not None:
                    print(f"[gate-rescue] {prov} was={legacy} text={text[:140]!r}", flush=True)
    return True


# ---- pronoun gates -----------------------------------------------------------------
# Two mechanical backstops behind the roster instructions in the prompts. Both are
# deliberately narrow: they fire only where the text is unambiguously wrong, because
# a false reject costs a retry and, worse, teaches nobody anything.

_EN_MASC = re.compile(r"\b(he|him|his)\b", re.I)
_EN_FEM = re.compile(r"\b(she|her|hers)\b", re.I)
_EN_FIRST = re.compile(r"\b(I|I'm|I've|I'd|I'll|my|me|myself)\b")
# 他/她 as an actual pronoun -- 他人 ("other people"), 其他 ("other") and 他們/她們
# are not somebody's pronoun and must not trip the gate.
_ZH_THIRD = re.compile(r"(?<!其)[他她](?!人|們)")
_ZH_MASC = re.compile(r"(?<!其)他(?!人|們)")
_ZH_FEM = re.compile(r"她(?!們)")
_ZH_FIRST = re.compile(r"我")


def _named_residents(text: str) -> set:
    """Residents named in the text, by either name form."""
    out = set()
    for en, zh, _g in builders.roster_genders():
        if re.search(rf"\b{re.escape(en)}\b", text, re.I) or (zh and zh in text):
            out.add(en.lower())
    return out


def person_shift_ok(source_en: str, translated_zh: str) -> bool:
    """Translation gate. A first-person English line whose Chinese has dropped 我
    entirely and speaks in the third person has had its speaker replaced -- and to do
    that the model had to invent a gender, which is where the mis-gendering came from.
    Reject that shape; everything else passes, including a first-person line that
    legitimately refers to somebody else in the third person (it still keeps 我).

    Deliberately roster-free: this is pure grammar over the two texts and asks nobody's
    gender, so it works with no world loaded. Do NOT add a roster precondition here --
    unlike the gender gates below, an empty roster cannot blind it."""
    if not source_en or not translated_zh:
        return True
    if not _EN_FIRST.search(source_en):
        return True                                  # not first person -> not our business
    if _EN_MASC.search(source_en) or _EN_FEM.search(source_en):
        # The source itself speaks of someone in the third person, so a 他/她 in the
        # translation has a legitimate origin -- and Chinese routinely drops the "I"
        # of a framing verb ("I heard that..." -> 「聽說...」), which is good style,
        # not a lost speaker. Only a source with NO third person at all can have had
        # its speaker invented.
        return True
    if not _ZH_THIRD.search(translated_zh):
        return True                                  # no third-person pronoun -> fine
    return bool(_ZH_FIRST.search(translated_zh))     # 我 survived -> the person was kept


def translate_gender_ok(source_en: str, translated_zh: str) -> bool:
    """Translation gender gate -- the companion to ``person_shift_ok``. That one
    catches a lost speaker; this catches a kept speaker with an invented gender:
    the English names exactly one resident, and the Chinese reaches for the pronoun
    the roster forbids ("Aisi seems reliable" -> 「我可以跟他更親近」 for a woman).

    Same philosophy as ``gender_ok``: multi-name text is left alone, because
    deciding whose pronoun is whose would be guesswork.

    Raises ``RosterNotLoadedError`` when the cast has not been published -- without it
    this check answers "fine" to everything (see builders.require_roster)."""
    builders.require_roster("translate_gender_ok")
    if not source_en or not translated_zh:
        return True
    named = _named_residents(source_en)
    if len(named) != 1:
        return True
    gender = builders.gender_of(next(iter(named)))
    he, she = bool(_ZH_MASC.search(translated_zh)), bool(_ZH_FEM.search(translated_zh))
    if gender == "male":
        return not (she and not he)
    if gender == "female":
        return not (he and not she)
    return True


def gender_ok(text: str) -> bool:
    """Generation gate. When a piece of free text names exactly ONE resident and then
    uses a pronoun contradicting that resident's roster gender, it is wrong with no
    ambiguity to hide behind. Multi-name text is left alone -- resolving which pronoun
    belongs to whom is guesswork, and a wrong guess would reject good output.

    English pronouns (he/she) and Chinese ones (他/她 -- never 他人, 其他, 他們, 她們)
    are both checked, so a zh-native town is held to the same rule.

    Raises ``RosterNotLoadedError`` when the cast has not been published -- without it
    this check answers "fine" to everything (see builders.require_roster)."""
    builders.require_roster("gender_ok")
    if not text:
        return True
    named = _named_residents(text)
    if len(named) != 1:
        return True
    gender = builders.gender_of(next(iter(named)))
    he = bool(_EN_MASC.search(text) or _ZH_MASC.search(text))
    she = bool(_EN_FEM.search(text) or _ZH_FEM.search(text))
    if gender == "male":
        return not (she and not he)
    if gender == "female":
        return not (he and not she)
    return True


def _wish_text_gender_ok(parsed: object) -> bool:
    """Every free-text field a generated wish would land in the world -- the same
    single-name gender rule the reflection path uses."""
    if not isinstance(parsed, dict) or parsed.get("no_wish"):
        return True
    for key in ("title", "statement", "motivation", "narrative"):
        if not gender_ok(str(parsed.get(key, "") or "")):
            return False
    return True


def _note_gender_gate(passed: bool) -> bool:
    """Count a rejection as it happens, then hand the verdict back to the router."""
    if not passed:
        builders.note_gate_reject("generation_gender")
    return passed


def reflection_gender_ok(parsed: object) -> bool:
    """Run ``gender_ok`` over every free-text field a reflection lands in memory.
    Checks the roster up front, so an empty payload cannot report "fine" without the
    roster ever having been consulted."""
    builders.require_roster("reflection_gender_ok")
    if not isinstance(parsed, dict):
        return True
    texts = [str(x) for x in (parsed.get("insights") or [])]
    texts += [str(b.get("text", "")) for b in (parsed.get("beliefs") or []) if isinstance(b, dict)]
    secret = parsed.get("new_secret")
    if isinstance(secret, dict):
        texts.append(str(secret.get("text", "")))
    for t in texts:
        if not gender_ok(t):
            if _GATE_DIAG:
                print(f"[gender-reject] {t[:120]!r}", flush=True)
            return False
    return True


# ---- zh-native quality gate (reflection / wish) -------------------------------------
# The gibberish gate turned around: a zh-native town's knowledge must BE Chinese, so a
# reflection or a wish that comes back in English, as soup, or as an empty dodge falls
# through to the next provider instead of landing in memory. English runs pass
# untouched. Counted as ``zh_quality`` in the gate counters.

def _zh_texts_ok(texts_: list) -> bool:
    if not builders.lang_is_zh():
        return True
    for t in texts_:
        reason = zh_quality_reason(t)
        if reason is not None:
            if _GATE_DIAG:
                print(f"[zh-quality-reject] reason={reason} text={t[:120]!r}", flush=True)
            return False
    return True


def reflection_zh_ok(parsed: object) -> bool:
    if not isinstance(parsed, dict):
        return True
    texts_ = [str(x) for x in (parsed.get("insights") or [])]
    texts_ += [str(b.get("text", "")) for b in (parsed.get("beliefs") or []) if isinstance(b, dict)]
    secret = parsed.get("new_secret")
    if isinstance(secret, dict):
        texts_.append(str(secret.get("text", "")))
    return _zh_texts_ok(texts_)


def _wish_text_zh_ok(parsed: object) -> bool:
    if not isinstance(parsed, dict) or parsed.get("no_wish"):
        return True
    title = str(parsed.get("title", "") or "")
    if builders.lang_is_zh() and _zh_reject_reason(title) is not None:   # a title may be short
        return False
    return _zh_texts_ok([str(parsed.get(k, "") or "") for k in ("statement", "motivation", "narrative")
                         if k in parsed and (k != "narrative" or parsed.get(k))])


def _note_zh_gate(passed: bool) -> bool:
    if not passed:
        builders.note_gate_reject("zh_quality")
    return passed


# ---- referential-integrity gate (anchored pronouns; no self-third-person) ----------
# Two lenient heuristics that catch only OBVIOUS breakdown, so normal reporting and
# third-party mentions ("Aisi and Azong") pass untouched:
#   (a) the speaker juxtaposes the LISTENER as if a separate person -- "你和{listener}"
#   (b) the speaker names THEMSELVES as a third-person subject -- "{self}覺得" / "是{self}"
# Quoted spans (reported speech) are exempt. A failure fails the dialogue gate -> the
# router retries / falls through (same path as any bad turn).
_REF_QUOTE_RE = re.compile(r"[「『“][^」』”]*[」』”]|\"[^\"]*\"")
_REF_SELF_VERBS = "覺得|認為|想|說|會|要|喜歡|知道|希望|打算|決定|問|告訴|擔心|以為"
_REF_EN_SELF_VERBS = r"thinks?|feels?|says?|said|wants?|knows?|will|would|hopes?|worries|decides?"


def _ref_name_forms(agent: "Agent") -> set[str]:
    """Every way this speaker's name can surface in a turn: the display (zh) name AND
    the pinyin/English form, since a weak model slips between them."""
    return {s for s in (getattr(agent, "name", "").strip(), agent.id.capitalize()) if s}


def _referential_ok(parsed: object, a: "Agent", b: "Agent") -> bool:
    turns = parsed.get("turns") if isinstance(parsed, dict) else None
    if not isinstance(turns, list):
        return True                                   # structural gate owns this case
    a_forms, b_forms = _ref_name_forms(a), _ref_name_forms(b)
    for t in turns:
        if not isinstance(t, dict):
            continue
        speaker = str(t.get("speaker", "")).strip()
        body = _REF_QUOTE_RE.sub(" ", str(t.get("text", "") or ""))   # drop quoted (reported) spans
        if speaker in a_forms:
            self_forms, partner_forms = a_forms, b_forms
        elif speaker in b_forms:
            self_forms, partner_forms = b_forms, a_forms
        else:
            continue                                  # unknown speaker label -> can't anchor; skip (lenient)
        for pn in partner_forms:                      # (a) listener juxtaposed as a third party
            if re.search(rf"[你妳您][和跟與、]\s*{re.escape(pn)}", body) \
                    or re.search(rf"{re.escape(pn)}\s*[和跟與、]\s*[你妳您]", body) \
                    or re.search(rf"\byou\s+and\s+{re.escape(pn)}\b", body, re.I) \
                    or re.search(rf"\b{re.escape(pn)}\s+and\s+you\b", body, re.I):
                return False
        for sn in self_forms:                         # (b) speaker names themselves in 3rd person
            if re.search(rf"(?<!我){re.escape(sn)}\s*(?:{_REF_SELF_VERBS})", body) \
                    or re.search(rf"(?<!我)是{re.escape(sn)}(?=[。.!?！？，,、\s]|$)", body) \
                    or re.search(rf"\b{re.escape(sn)}\s+(?:{_REF_EN_SELF_VERBS})\b", body, re.I):
                return False
    return True


# Memory text stores {agent:id}/{loc:id}/{landmark:id} placeholders. For the LLM
# context we resolve them to the names the model should write with: pinyin/English
# in an English town, the Chinese names in a zh-native one (an English name inside
# Chinese context invites English back out). The UI resolves the same placeholders
# at display time.
_PLACEHOLDER_RE = re.compile(r"\{(agent|loc|landmark):([a-z_]+)\}")


def _resolve_placeholders(text: str, world: "World") -> str:
    def repl(m: "re.Match") -> str:
        kind, key = m.group(1), m.group(2)
        if kind == "agent":
            return texts.display_name(key)                # pinyin, or the zh name
        if kind == "loc":
            loc = world.locations.get(key)
            if loc is None:
                return key
            return (loc.name_zh or loc.name) if texts.zh_native() else loc.name
        for loc in world.locations.values():              # landmark: find by id
            for lm in loc.landmarks:
                if lm.get("id") == key:
                    return texts.landmark_name(lm) or key
        return key
    return _PLACEHOLDER_RE.sub(repl, text)


def _resolve_mems(mems: list[str], world: "World") -> list[str]:
    return [_resolve_placeholders(m, world) for m in mems]


@dataclass
class Decision:
    action: str                        # move | work | rest | eat | sleep | talk | idle
    target_location: str | None = None
    talk_partner: str | None = None
    duration: int = 15                 # minutes until next natural decision
    level: int = 0                     # 0 rules / 1 cheap / 2 normal / 3 smart
    reason: str = ""
    narrative_verb: str = ""           # non-routine story beat for the engine to publish (e.g. "seek_out")
    narrative_target: str = ""         # agent_id the narrative beat is aimed at
    confront_text: str = ""            # opener injected into the dialogue when confronting over a rumor
    confront_rumor_id: str = ""        # the rumor being confronted (set alongside confront_text)
    is_meetup: bool = False            # a kept social appointment -> conversation bypasses the daily cap


@dataclass
class DecisionTrace:
    minute: int
    agent_id: str
    observation: str
    retrieved_memories: list[str]
    decision: Decision
    model: str = "rules"
    wish_bonus: bool = False       # a wish weighted the social pre-gate for this decision

    def render(self) -> str:
        mems = "\n".join(f"    {i+1}. {m}" for i, m in enumerate(self.retrieved_memories)) or "    (none)"
        return (
            f"  Agent: {self.agent_id}\n"
            f"  Observation: {self.observation}\n"
            f"  Memories:\n{mems}\n"
            f"  Decision: {self.decision.action}"
            + (f" -> {self.decision.talk_partner}" if self.decision.talk_partner else "")
            + f"  (L{self.decision.level}, {self.model}"
            + (", wish-bonus" if self.wish_bonus else "") + ")\n"
            f"  Reason: {self.decision.reason}"
        )


@dataclass
class ConvPlan:
    """A conversation captured at initiation. Every pre-dialogue effect (rumor
    shares, leaks, confides -- each a memory/relationship mutation) is already
    applied and each speaker's private context is snapshotted, so the slow
    turn-by-turn generation can run in a background task without the world's state
    drifting under it. ``settle_conversation`` consumes the result.

    Turn by turn: one LLM call per line, each written from inside ONE speaker with
    only that speaker's context (``ctx[speaker_id]``) plus the shared transcript.
    ``turns`` accumulates as lines land, so a retry round resumes where the last
    one stopped instead of starting the conversation over."""
    a: Agent
    b: Agent
    location: str
    init_minute: int
    ctx: dict                        # agent_id -> that speaker's private context (see _speaker_ctx)
    order: tuple                     # (first speaker, second speaker)
    task: str                        # dialogue_turn | dialogue_turn_tense (smart tier)
    min_turns: int
    max_turns: int
    max_tokens: int                  # per line
    is_confront: bool
    confront_rumor_id: str
    shared_rumors: list              # fwd/rev/leak descriptors -> engine publishes at initiation
    confided: list                   # confide descriptors -> engine publishes at initiation
    confession: dict | None
    turns: list = field(default_factory=list)       # lines generated so far
    providers: list = field(default_factory=list)   # "provider/model" per line

    def preview(self, agent_id: str) -> str:
        """The user-side prompt this speaker would see for an opening line -- what
        the model can know about the scene from their seat (tests, diagnostics)."""
        msgs = builders.dialogue_turn_prompt(self.ctx[agent_id], [], 1, self.max_turns, self.min_turns)
        return msgs[-1]["content"]


@dataclass
class DialogueResult:
    """The assembled exchange: the router-result shape the engine already handles."""
    parsed: dict
    provider: str
    model: str


@dataclass
class DecisionEngine:
    router: LLMRouter
    traces: list[DecisionTrace] = field(default_factory=list)
    rumors: RumorRegistry = field(default_factory=RumorRegistry)
    secrets: SecretRegistry = field(default_factory=SecretRegistry)
    # Per-sim-day dialogue budget (live only) -- keeps free-tier token spend
    # bounded. 0 = unlimited (mock / headless), so run_day is untouched.
    dialogue_cap: int = 0
    _dialogues_today: int = 0
    _dialogue_day: int = -1
    # Chapter-closure signal (set by the engine): (agent, outcome, trigger, reason).
    # Fired when a decision-layer event ends a pursuit -- today, a resolved secret
    # whose theme is the chapter's (see _resolve_secret). The engine owns the
    # pipeline (LLM call + atomic apply + event); this layer only raises the flag.
    on_chapter_signal: object = None
    # Seeds the wish drive's deterministic dice with the persistence run id, so the
    # same day replays the same way across restarts (set by the engine).
    wish_run_seed: str = ""
    # Embedding provider for the wish novelty gate. Defaults to the free mock; the
    # host swaps in the persistence one when a DB is attached.
    embedder: object = None
    # Beats the decision layer raises outside a conversation (a promise that came to
    # nothing) -- the engine drains and publishes them after each decision.
    outbox: list = field(default_factory=list)

    def drain_outbox(self) -> list:
        out, self.outbox = self.outbox, []
        return out

    def __post_init__(self) -> None:
        if self.embedder is None:
            from ..llm.embeddings import MockEmbedding
            self.embedder = MockEmbedding()
        live = any(p.name != "mock" for chain in self.router.tiers.values() for p in chain)
        self._live = live
        if live and self.dialogue_cap == 0:
            try:
                self.dialogue_cap = int(os.environ.get("AI_TOWN_DIALOGUE_CAP", str(pacing.DIALOGUE_CAP)))
            except ValueError:
                self.dialogue_cap = pacing.DIALOGUE_CAP

    # Social initiative is a live-mode beat (it drives real LLM conversations); the mock
    # baseline stays deterministic. AI_TOWN_MEETUPS=1 force-enables it for testing.
    @property
    def _meetups_enabled(self) -> bool:
        return self._live or os.environ.get("AI_TOWN_MEETUPS") == "1"

    def _roll_day(self, now: int) -> None:
        day = now // (24 * 60)
        if day != self._dialogue_day:
            self._dialogue_day = day
            self._dialogues_today = 0

    def dialogue_cap_reached(self, now: int) -> bool:
        """True once today's dialogue budget is spent -- the should_talk gate then
        says 'no' by rule, saving even the cheap LLM call. Resets each sim-day."""
        if self.dialogue_cap <= 0:
            return False
        self._roll_day(now)
        return self._dialogues_today >= self.dialogue_cap

    async def decide(
        self, agent: Agent, world: World, obs: Observation, now: int
    ) -> Decision:
        minute_of_day = now % (24 * 60)
        dow = (now // (24 * 60)) % 7        # 0=Mon .. 6=Sun (Day 1 = Monday)
        entry = agent.routine.current(minute_of_day, dow)
        # Today's own plan (the morning micro-decision) outranks the timetable and a
        # wish's drive; sleep is never its to change (entry_for refuses it).
        plan_hit = dayplan_mod.entry_for(agent, now, entry)
        plan_until = None
        if plan_hit is not None:
            entry, plan_until = plan_hit
        today = now // (24 * 60) + 1
        # Free actions (agents/actions.py): an hour that slipped by is a failure; at
        # the planned hour the rules decide whether the doing happens, and it lands
        # once the resident is actually there. The slot itself is then plain time
        # spent at the place.
        for act in dayplan_mod.lapsed_actions(agent, now):
            self.outbox.append(actions_mod.fail(agent, act, texts.T("fail_time"), now))
        due = dayplan_mod.action_due(agent, now)        # a doing inside a same-place stretch
        if due is not None:
            why = actions_mod.failure_reason(agent, world, due, now)
            if why:
                self.outbox.append(actions_mod.fail(agent, due, why, now))
            elif agent.state.location == due["location"] and agent.state.current_action != "sleep":
                beat, obj = actions_mod.land(agent, world, due, now)
                self.outbox.append(beat)
                if obj is not None:
                    self.outbox.append({"verb": "__object__", "object": obj})
        if plan_hit is not None and entry.action == "free":
            act = dayplan_mod.action_at(agent, entry.start)
            why = actions_mod.failure_reason(agent, world, act, now) \
                if act is not None and act["state"] == "planned" else ""
            if act is None or act["state"] != "planned" or why:
                if why:
                    self.outbox.append(actions_mod.fail(agent, act, why, now))
                plan_hit, plan_until = None, None       # back to the usual day
                entry = agent.routine.current(minute_of_day, dow)
            else:
                if agent.state.location == act["location"] and agent.state.current_action != "sleep":
                    beat, obj = actions_mod.land(agent, world, act, now)
                    self.outbox.append(beat)
                    if obj is not None:
                        self.outbox.append({"verb": "__object__", "object": obj})
                entry = RoutineEntry(entry.start, "idle", entry.location)
        obs_text = obs.describe(world)
        self._record_observations(agent, world, now)
        memories: list[str] = []
        model_used = "rules"

        decision: Decision | None = None
        wish_drive: dict | None = None
        wish_bonus = False          # a wish weighted the social pre-gate (see below)

        # ---- Level 2 (resume): chasing a rumor's source to confront ---
        if agent.state.seek_target and agent.state.current_action != "sleep":
            seek_dec = self._resume_seek(agent, world)
            if seek_dec is not None:
                self.traces.append(DecisionTrace(
                    minute=now, agent_id=agent.id, observation=obs_text,
                    retrieved_memories=[], decision=seek_dec, model="rules",
                ))
                return seek_dec
            # gave up (source unreachable) -> fall through to the routine

        # ---- Level 2 (resume): keeping a social appointment -----------
        if agent.state.pending_meetup and agent.state.current_action != "sleep":
            meet_dec = self._resume_meetup(agent, world, now)
            if meet_dec is not None:
                self.traces.append(DecisionTrace(
                    minute=now, agent_id=agent.id, observation=obs_text,
                    retrieved_memories=[], decision=meet_dec, model="rules",
                ))
                return meet_dec
            # not time yet / lapsed -> fall through to the routine

        # ---- Level 0: hard rules --------------------------------------
        if agent.state.current_action == "sleep" and entry.action == "sleep":
            decision = Decision("sleep", duration=agent.routine.next_boundary(minute_of_day, dow) - minute_of_day,
                                reason="asleep per routine")
        elif agent.state.energy <= LOW_ENERGY and entry.action not in ("sleep", "rest"):
            decision = Decision("rest", duration=30, reason=f"energy {agent.state.energy} <= {LOW_ENERGY}")

        # ---- Repair dispatch: the town's tech drops everything for a fault ----
        if decision is None and agent.profile.occupation == "Repair Technician" \
                and agent.state.current_action != "sleep":
            target = next((lid for lid, l in world.locations.items() if l.broken), None)
            if target is not None:
                if agent.state.location != target:
                    decision = Decision("move", target_location=target, duration=10,
                                        reason=f"heading to a repair job at {target}",
                                        narrative_verb="repair_go", narrative_target=target)
                else:
                    decision = Decision("work", duration=120,
                                        reason=f"repairing the equipment at {target}",
                                        narrative_verb="repair", narrative_target=target)

        # ---- Wish drive (Level 0, pure rules; see agents/wishes.py) ----
        # Considered only AFTER sleep, low energy, repair duty and a kept meetup have
        # had their say, and only for a slot the routine was already spending on
        # rest/idle. What comes back is a *suggestion*: every precondition below
        # (talk cooldown, dialogue cap, the partner's refusal, closing days, rain)
        # still applies, and nothing here touches requirement progress.
        if decision is None and not agent.state.pending_concern and plan_hit is None:
            wish_drive = wishes_mod.next_directive(
                agent, world, now, entry.action, run_seed=self.wish_run_seed)

        # ---- Level 1: social trigger (cheap LLM) ----------------------
        # Once the day's dialogue budget is spent, decline by rule -- no should_talk
        # LLM call either. Agents still meet and move; they just chat less.
        # A wish may nominate who is worth approaching, but never that they must be.
        wish_social = wish_drive if (wish_drive or {}).get("action") == "talk_bias" else None
        candidates = [wish_social["target"]] if wish_social else list(obs.arrivals)
        if decision is None and candidates and not self.dialogue_cap_reached(now):
            partner_id = candidates[0]
            partner = world.agents[partner_id]
            last = agent.state.last_talk_minute.get(partner_id, -10**9)
            partner_free = (
                partner.state.busy_until <= now
                and partner.state.current_action != "sleep"
            )
            # A wish that needs time with THIS person makes them worth approaching,
            # whatever the timetable says the hour is for. The drive proper stays
            # confined to rest/idle -- this is only a weight on the pre-gate, spends
            # no attempt and records no blocked day, so it costs the routine nothing.
            # (Scoping it to the drive's window was over-narrow: the pair whose only
            # overlap is a meal would never have got it.)
            wish_bonus = (wishes_mod.wants_contact(agent, partner_id)
                          and partner.state.location == agent.state.location)
            if partner_free and now - last >= TALK_COOLDOWN_MIN and agent.state.current_action != "sleep" \
                    and self._social_gate(agent, partner_id, now,
                                          wish_target=bool(wish_social) or wish_bonus):
                memories = await agent.memory.retrieve_async(
                    f"{texts.name_query(partner.id)} {obs.location}", k=5, location=obs.location)
                res = await self.router.generate(
                    task="should_talk",
                    messages=builders.should_talk_prompt(agent, partner.name, _resolve_mems(memories, world)),
                    agent_id=agent.id,
                    sim_minute=now,
                    schema={"type": "object"},
                    cache_key=f"{agent.id}|{partner_id}|{agent.state.mood}|{obs.location}",
                    max_tokens=160 if builders.lang_is_zh() else 60,   # zh needs more tokens for the same words
                    per_call_timeout=SYNC_CALL_TIMEOUT_S,   # in-tick: never park the pacing loop on a hung provider
                )
                model_used = f"{res.provider}/{res.model}"
                if isinstance(res.parsed, dict) and res.parsed.get("talk"):
                    decision = Decision(
                        "talk",
                        talk_partner=partner_id,
                        duration=TALK_DURATION_MIN,
                        level=1,
                        reason=str(res.parsed.get("reason", "wants to chat")),
                    )
            # The approach didn't happen (cooldown, cap, gate, or a plain "no"):
            # for the wish that suggested it, today was a blocked day.
            if wish_social is not None and decision is None:
                self._note_wish_blocked(agent, wish_social["wish_id"], now)

        # ---- Level 2: react to a rumor about oneself ------------------
        if agent.state.pending_concern and agent.state.current_action != "sleep":
            concern = agent.state.pending_concern
            agent.state.pending_concern = None  # react only once, whatever we choose
            told_by = world.agents.get(concern.get("told_by", ""))
            rumor = self.rumors.rumors.get(concern.get("rumor_id", ""))
            mine = [v for v in rumor.versions if v.agent_id == agent.id] if rumor else []
            heard = mine[-1].text if mine else "something about me"
            memories = await agent.memory.retrieve_async(heard, k=5)
            observation = (
                f"You heard people are saying something about you: {heard}. "
                f"{told_by.name if told_by else 'Someone'} told you this."
            )
            res = await self.router.generate(
                task="decision",
                messages=builders.decision_prompt(
                    agent, observation, _resolve_mems(memories, world), ["continue_routine", "seek_out"]
                ),
                agent_id=agent.id,
                sim_minute=now,
                schema={"type": "object"},
                max_tokens=160 if builders.lang_is_zh() else 80,
                per_call_timeout=SYNC_CALL_TIMEOUT_S,   # in-tick: never park the pacing loop on a hung provider
            )
            model_used = f"{res.provider}/{res.model}"
            action = str(res.parsed.get("action", "")) if isinstance(res.parsed, dict) else ""
            # Confront the SOURCE of the rumor, not just the messenger. Fall back
            # to the messenger only if the origin is unknown (or is the messenger).
            origin = world.agents.get(rumor.origin) if rumor else None
            target = origin or told_by
            # Betrayal: if this rumor was leaked from a secret, the owner knows only
            # someone they confided in could have known -- so they confront the LEAKER,
            # not the rumor's chain origin.
            if rumor and rumor.from_secret_id:
                secret = self.secrets.secrets.get(rumor.from_secret_id)
                leaker = world.agents.get(secret.leaked_by) if secret and secret.leaked_by else None
                if leaker is not None:
                    target = leaker
            if action == "seek_out" and target is not None:
                confront_text = texts.T("confront_opener", heard=heard)
                if target.state.location == agent.state.location and target.state.current_action != "sleep":
                    decision = Decision(  # already together -> confront right now
                        action="talk", talk_partner=target.id, duration=TALK_DURATION_MIN,
                        level=2, reason=f"confronting {target.name} about a rumor",
                        confront_text=confront_text, confront_rumor_id=rumor.id if rumor else "",
                    )
                else:                     # elsewhere -> go find them, then confront on arrival
                    agent.state.seek_target = target.id
                    agent.state.seek_text = confront_text
                    agent.state.seek_rumor_id = rumor.id if rumor else ""
                    agent.state.seek_tries = 0
                    decision = Decision(
                        action="move", target_location=target.state.location,
                        duration=10, level=2,
                        reason=f"heard a rumor about self; seeking out {target.name}",
                        narrative_verb="seek_out", narrative_target=target.id,
                    )
            # continue_routine: leave decision as-is so the routine default fills it.

        # ---- Level 0 default: follow the routine ----------------------
        if decision is None:
            dest = entry.location
            # Day off: a shop owner on their shop's closing day, or an agent with a
            # personal weekly rest day (the postman on Sunday). They don't work or
            # patrol -- they relax at home in the morning and drift out as a free
            # agent later (meals still redirect them around town, incl. a rival's shop).
            own_shop = next((l for l in world.locations.values()
                             if l.owner == agent.id and l.price > 0), None)
            # Shop staff rest on their employer's shop's closing day, same as the owner.
            staff_shop = next((l for l in world.locations.values()
                               if l.owner == agent.state.employer and l.price > 0), None) \
                if agent.state.employer else None
            off_today = (dow in agent.profile.off_days) \
                or (own_shop is not None and dow in own_shop.closed_days) \
                or (staff_shop is not None and dow in staff_shop.closed_days)
            day_off_rest = off_today and (
                entry.action == "work"
                or (entry.action == "rest" and entry.location != agent.home)
            )
            if day_off_rest:
                if minute_of_day < 12 * 60:
                    dest = agent.home                          # sleep in / relax at home
                else:
                    opts = ["park", "market", agent.home]
                    dest = opts[(minute_of_day // 160) % len(opts)]   # drift around town
            # Interlude (just closed a chapter, see chapters.py): routine adherence dips
            # a little -- now and then a work/rest slot becomes an aimless wander to a
            # public place. The roll is sticky per (agent, 2-hour window) so a wander
            # is a real stretch there, not a move-and-come-straight-back; deterministic
            # so mock runs reproduce.
            interlude_drift = False
            if chapters_mod.in_interlude(agent) and not day_off_rest \
                    and entry.action in ("work", "rest") and minute_of_day >= 9 * 60:
                window = now // 120
                seed = int(hashlib.sha256(f"drift|{agent.id}|{window}".encode()).hexdigest()[:8], 16)
                if random.Random(seed).random() < chapters_mod.INTERLUDE_DRIFT_P:
                    opts = [p for p in ("park", "market", "cafe") if p in world.locations]
                    if opts:
                        dest = opts[window % len(opts)]
                        interlude_drift = True
            # Don't loiter at a shuttered door. If the routine points us at a shop
            # that's shunned (a bad rumor about its owner) or closed for the owner's
            # weekly day off, redirect -- whatever the action was. The owner is the
            # only one who may enter their own shop while it's closed (stock/clean).
            dest_loc = world.locations.get(dest)
            shunned = agent.state.avoid_location and dest == agent.state.avoid_location
            is_owned_shop = dest_loc is not None and dest_loc.owner and dest_loc.price > 0
            closed = (is_owned_shop and (dow in dest_loc.closed_days or dest_loc.closed_on == today)
                      and dest_loc.owner != agent.id)
            if entry.action == "eat" and (shunned or closed):
                # Take our custom to a rival shop that's open and affordable
                # (bakery <-> cafe); if none, eat at home -- so a closing day feeds
                # the other shop's takings.
                rival = next(
                    (lid for lid, loc in world.locations.items()
                     if loc.price > 0 and loc.owner and lid != dest and dow not in loc.closed_days
                     and loc.closed_on != today and agent.state.money >= loc.price),
                    None,
                )
                if closed:
                    self._note_closed(agent, dest, rival or agent.home, now)
                dest = rival or agent.home
            elif closed:
                # A non-meal routine (rest/idle) aimed at a closed shop: drift to a
                # public space instead of standing at the door.
                alt = next((p for p in ("market", "park") if p in world.locations), agent.home)
                self._note_closed(agent, dest, alt, now)
                dest = alt

            # ---- world events (pure Level 0, no LLM) ------------------
            rain = world.effect_active("rain")
            festival = world.effect_active("festival")
            # Festival: spend downtime (rest slots) at the festivities; work and
            # sleep are untouched, so people only drift over once they're off.
            if festival and entry.action == "rest":
                dest = festival["location"]
            # Rain (overrides festival): no one goes to a park -- head home instead,
            # and those already at the park leave on their next decision.
            park_rained_out = False
            if rain:
                d_loc = world.locations.get(dest)
                if d_loc is not None and d_loc.kind == "park":
                    dest = agent.home
                    park_rained_out = True
            # The festival crowd is in good spirits.
            if festival and agent.state.location == festival["location"]:
                agent.state.mood = "happy"

            # A wish directive may spend this one discretionary slot -- but it yields
            # to a day off and to being rained out, exactly like the routine does.
            # A move still goes through World.execute, so progress can only ever come
            # from the resulting `arrive` event, never from the intention itself.
            drive_action = ""
            if wish_drive is not None and wish_social is None:
                if day_off_rest or park_rained_out:
                    self._note_wish_blocked(agent, wish_drive["wish_id"], now)
                else:
                    want = wish_drive["action"]
                    want_loc = wish_drive.get("location", agent.state.location)
                    if want == "move" and want_loc != agent.state.location \
                            and want_loc != agent.state.avoid_location:
                        dest, drive_action = want_loc, "move"
                    elif want in wishes_mod.DRIVE_ACTIONS:
                        dest, drive_action = agent.state.location, want
                    else:
                        self._note_wish_blocked(agent, wish_drive["wish_id"], now)

            until_next = agent.routine.next_boundary(minute_of_day, dow) - minute_of_day
            if plan_until is not None:
                until_next = min(until_next, plan_until - minute_of_day)
            nxt = dayplan_mod.next_start(agent, now)       # don't sleepwalk past one's own plan
            if nxt is not None:
                until_next = min(until_next, nxt - minute_of_day)
            if agent.state.location != dest:
                decision = Decision(
                    "move", target_location=dest, duration=10,
                    reason=("private intention: heading to {}".format(dest) if drive_action == "move"
                            else f"interlude: drifting over to {dest}" if interlude_drift
                            else f"routine: head to {dest}"),
                )
            else:
                act = ("rest" if (park_rained_out or day_off_rest or interlude_drift)
                       else drive_action or entry.action)
                reason = ("routine: rest (rained out)" if park_rained_out
                          else "day off" if day_off_rest
                          else "interlude: drifting, nothing to push forward" if interlude_drift
                          else f"private intention: {drive_action}" if drive_action
                          else f"routine: {entry.action}")
                decision = Decision(
                    act, duration=max(15, min(until_next, 120)), reason=reason,
                )
            if plan_hit is not None and decision.reason.startswith("routine"):
                decision.reason = decision.reason.replace("routine", "today's own plan", 1)
            model_used = "rules" if decision.level == 0 else model_used

        trace = DecisionTrace(
            minute=now,
            agent_id=agent.id,
            observation=obs_text,
            retrieved_memories=memories,
            decision=decision,
            model=model_used,
            wish_bonus=wish_bonus,
        )
        self.traces.append(trace)
        return decision

    def _note_closed(self, agent: Agent, shop: str, went_to: str, now: int) -> None:
        """Leave a light 'found it closed' memory when a routine gets rerouted around
        a shop's day off -- at most once per shop per sim-day (no water-treading).
        Fuels gossip ("the cafe wasn't even open, wasted a trip")."""
        day = now // (24 * 60)
        if agent.state.closed_reroute_notes.get(shop) == day:
            return
        agent.state.closed_reroute_notes[shop] = day
        agent.memory.add(MemoryItem(
            minute=now, importance=1,
            text=texts.T("closed_reroute", shop=shop, went_to=went_to),
        ))

    @staticmethod
    def _note_wish_blocked(agent: Agent, wish_id: str, now: int) -> None:
        wish = next((w for w in agent.wishes if w.id == wish_id and w.status == "active"), None)
        if wish is not None:
            wishes_mod.note_blocked(agent, wish, now)

    def _social_gate(self, agent: Agent, partner_id: str, now: int,
                     wish_target: bool = False) -> bool:
        """Tiered pre-check before the cheap should_talk call. Returns True to let
        the LLM decide, False to skip by rule. Probability keys off how close the
        two already are (a fresh pair defaults to the neutral friendship 30, i.e.
        a stranger) and dampens for quiet personalities. Deterministic per
        (pair, minute) so mock runs stay reproducible."""
        rel = agent.relationships.get(partner_id)
        f = rel.friendship if rel is not None else 30.0
        p = 1.0 if f >= SOCIAL_TIER_FRIEND else 0.6 if f >= SOCIAL_TIER_ACQUAINT else 0.3
        if agent.profile.extraversion < INTROVERT_EXTRAVERSION:
            p *= 0.7
        # Interlude: a little more socially forward than usual (see chapters.py).
        if chapters_mod.in_interlude(agent):
            p *= chapters_mod.INTERLUDE_SOCIAL_MULT
        # A wish makes this person worth approaching -- a bias on the pre-check only;
        # the cheap should_talk call still gets the final say.
        if wish_target:
            p = min(1.0, p * wishes_mod.DRIVE_SOCIAL_GATE_MULT)
        # Post-rejection awkwardness: for a week the pair can barely face each other.
        if agent.state.awkward_until.get(partner_id, -1) >= now // (24 * 60):
            p *= romance_mod.AWKWARD_TALK_MULT
        if p >= 1.0:
            return True
        seed = int(hashlib.sha256(f"talk|{agent.id}|{partner_id}|{now}".encode()).hexdigest()[:8], 16)
        return random.Random(seed).random() < p

    def _record_observations(self, agent: Agent, world: World, now: int) -> None:
        if agent.state.current_action != "sleep":
            actions_mod.notice_objects(agent, world, now)    # what others left here
        self._record_landmark_observations(agent, world, now)

    def _record_landmark_observations(self, agent: Agent, world: World, now: int) -> None:
        """Pure-rules observable-activity memory. Noticing a neighbour at work on a
        landmark, or a landmark that has visibly advanced since you last looked
        (>= 0.25), leaves a light (importance 2) memory; ordinary rest/eat around
        you never does. Throttled per agent via ``seen_landmark_progress`` so a
        long stint beside the mural doesn't spam the same sighting."""
        if agent.state.current_action == "sleep":
            return
        loc = world.locations.get(agent.state.location)
        if loc is None or not loc.landmarks:
            return
        seen = agent.state.seen_landmark_progress
        for lm in loc.landmarks:
            cb = lm.get("created_by")
            if cb == agent.id:
                continue  # your own work earns the completion memory, not sightings
            lid, cur = lm["id"], lm["progress"]
            prev = seen.get(lid)
            worker = None
            if cb:
                o = world.agents.get(cb)
                if o is not None and o.state.location == agent.state.location \
                        and o.state.current_action == "work":
                    worker = o
            jumped = prev is not None and (cur - prev) >= 0.25
            if worker is not None and (prev is None or jumped):
                agent.memory.add(MemoryItem(
                    minute=now, importance=2,
                    text=texts.T("saw_working", worker=worker.id, lid=lid, loc=loc.id),
                ))
                seen[lid] = cur
            elif jumped:
                agent.memory.add(MemoryItem(
                    minute=now, importance=2,
                    text=texts.T("landmark_progress", lid=lid, loc=loc.id),
                ))
                seen[lid] = cur
            elif prev is None:
                seen[lid] = cur  # first sight -> baseline it, no memory yet

    def _resume_seek(self, agent: Agent, world: World) -> Decision | None:
        """Continue a confrontation-in-progress. Returns a talk (reached them),
        a move (chase again, max 2 tries), or None (gave up -> clears state)."""
        target = world.agents.get(agent.state.seek_target)
        text = agent.state.seek_text
        rumor_id = agent.state.seek_rumor_id
        if target is not None and target.state.location == agent.state.location \
                and target.state.current_action != "sleep":
            agent.state.seek_target = ""
            agent.state.seek_text = ""
            agent.state.seek_rumor_id = ""
            agent.state.seek_tries = 0
            return Decision(
                action="talk", talk_partner=target.id, duration=TALK_DURATION_MIN,
                level=2, reason=f"confronting {target.name} about a rumor",
                confront_text=text, confront_rumor_id=rumor_id,
            )
        if target is not None and agent.state.seek_tries < 2:
            agent.state.seek_tries += 1
            return Decision(
                action="move", target_location=target.state.location, duration=10,
                level=2, reason=f"still looking for {target.name}",
                narrative_verb="seek_out", narrative_target=target.id,
            )
        agent.state.seek_target = ""   # unreachable -> give up
        agent.state.seek_text = ""
        agent.state.seek_rumor_id = ""
        agent.state.seek_tries = 0
        return None

    # ---- social initiative: arranged meetups -------------------------

    def _social_venue(self, world: "World") -> str:
        """A public place both parties can head to for a meetup (cafe first, else park,
        else any non-home location)."""
        for pref in ("cafe", "park"):
            if pref in world.locations:
                return pref
        for lid, loc in world.locations.items():
            if getattr(loc, "kind", "") != "home":
                return lid
        return next(iter(world.locations), "")

    def _common_free_window(self, a: Agent, b: Agent, world: "World", now: int) -> tuple[int, str] | None:
        """Find the earliest daytime slot today where BOTH agents' routines are in a
        drop-in-friendly state (idle/rest/eat -- never asleep or working), with at least
        an hour of lead. Location: where they'd already both be, else a public venue.
        Returns (absolute sim-minute, location_id) or None."""
        day_start = (now // (24 * 60)) * (24 * 60)
        dow = (now // (24 * 60)) % 7
        for t_mod in range(10 * 60, 20 * 60, 30):         # 10:00 .. 19:30, half-hour steps
            minute_abs = day_start + t_mod
            if minute_abs < now + 60:                     # need real lead time
                continue
            ea, eb = a.routine.current(t_mod, dow), b.routine.current(t_mod, dow)
            if ea.action in MEETUP_SOCIAL_ACTIONS and eb.action in MEETUP_SOCIAL_ACTIONS:
                loc = ea.location if ea.location == eb.location else self._social_venue(world)
                if loc:
                    return minute_abs, loc
        return None

    def maybe_arrange_meetup(self, agent: Agent, world: "World", now: int) -> dict | None:
        """Once-a-day roll: ``agent`` may invite a friend to meet later today. Honors the
        per-person and per-pair cooldowns, asks the friend (a rejection is possible), and
        on acceptance finds a shared free window and sets the mirrored ``pending_meetup``
        on both. Returns an outcome dict for the engine to publish
        ({"verb": "meetup_arranged"|"meetup_declined", "a", "b", "minute"?, "location"?}),
        or None (no attempt / no window). Disabled under mock so the baseline is untouched."""
        if not self._meetups_enabled:
            return None
        day = now // (24 * 60)
        st = agent.state
        if day - st.last_meetup_day < MEETUP_PERSON_COOLDOWN:
            return None
        if st.pending_meetup is not None:
            return None
        # Deterministic per (day, agent): reproducible, and independent of call order.
        rng = random.Random(int(hashlib.sha256(f"meetup|{day}|{agent.id}".encode()).hexdigest()[:8], 16))
        p_try = MEETUP_DAILY_P * (chapters_mod.INTERLUDE_MEETUP_MULT if chapters_mod.in_interlude(agent) else 1.0)
        # Someone an active wish wants time with is worth asking more often -- this
        # raises the ATTEMPT rate only; the friendship bar, both cooldowns, the
        # shared-free-window search and their right to decline are untouched.
        wish_partner = wishes_mod.social_target(agent)
        if wish_partner:
            p_try = max(p_try, wishes_mod.DRIVE_MEETUP_PROBABILITY)
        if rng.random() >= p_try:
            return None
        # Candidate friends: friendly enough, not on the per-pair cooldown, free of an
        # existing appointment, and not the agent themselves.
        cands = []
        for other in world.agents.values():
            if other.id == agent.id or other.state.pending_meetup is not None:
                continue
            rel = agent.relationships.get(other.id)   # read-only: don't create empty entries
            if rel is None or rel.friendship < MEETUP_FRIEND_MIN:
                continue
            if day - st.meetup_with_day.get(other.id, -100) < MEETUP_PAIR_COOLDOWN:
                continue
            cands.append(other)
        if not cands:
            return None
        cands.sort(key=lambda o: agent.rel(o.id).friendship, reverse=True)
        # A wish target that already cleared every candidate rule is picked first;
        # otherwise the ordinary "one of your closest few" choice stands.
        preferred = next((o for o in cands if o.id == wish_partner), None)
        b = preferred if preferred is not None else cands[rng.randrange(min(3, len(cands)))]
        # The friend may decline -- warmer relationships accept more readily; a sour mood
        # dampens it. Record the per-pair cooldown either way so they don't re-ask daily.
        st.meetup_with_day[b.id] = day
        b.state.meetup_with_day[agent.id] = day
        p_accept = min(0.95, 0.30 + 0.006 * b.rel(agent.id).friendship)
        if b.state.mood in ("upset", "worried", "anxious"):
            p_accept *= 0.6
        if rng.random() >= p_accept:
            return {"verb": "meetup_declined", "a": agent.id, "b": b.id}
        window = self._common_free_window(agent, b, world, now)
        if window is None:
            return None                                  # willing, but no shared gap today
        minute_abs, loc = window
        st.last_meetup_day = day                         # per-person throttle on the INITIATOR
        appt = {"partner": "", "location": loc, "minute": minute_abs}
        st.pending_meetup = {**appt, "partner": b.id}
        b.state.pending_meetup = {**appt, "partner": agent.id}
        return {"verb": "meetup_arranged", "a": agent.id, "b": b.id,
                "minute": minute_abs, "location": loc}

    def _promise_missed(self, agent: Agent, m: dict, partner_pm: dict | None, now: int) -> None:
        """A meeting promised in conversation lapsed: each side remembers it (no
        blame -- the rules don't know whose fault it was), and the town hears of it
        once. The first side to give up raises the event and flags the partner's
        mirror so the second doesn't raise it again (the flag rides the snapshot)."""
        if not m.get("promised"):
            return
        agent.memory.add(MemoryItem(
            minute=now, importance=4, kind="conversation", tags=["promise"],
            text=texts.T("promise_missed", other=m["partner"], loc=m["location"])))
        mood_mod.nudge(agent, -pacing.MOOD_PROMISE_MISSED, texts.T("note_promise_missed", other=m["partner"]))
        if m.get("missed_reported"):
            return
        if partner_pm and partner_pm.get("partner") == agent.id and partner_pm.get("minute") == m.get("minute"):
            partner_pm["missed_reported"] = True
        self.outbox.append({"verb": "promise_missed", "actor": agent.id, "target": m["partner"],
                            "location": m["location"]})

    def _resume_meetup(self, agent: Agent, world: "World", now: int) -> Decision | None:
        """Drive a kept appointment: once the appointed time arrives, head to the venue
        and, when both are there and free, start the (cap-exempt) conversation. Returns a
        move/idle/talk Decision, or None (not time yet, or the window lapsed -> cleared)."""
        m = agent.state.pending_meetup
        if not m or agent.state.current_action == "sleep":
            return None
        if now < m["minute"]:
            return None                                  # not yet -- live the normal routine
        partner = world.agents.get(m["partner"])
        pm = partner.state.pending_meetup if partner else None
        if now > m["minute"] + MEETUP_WINDOW_MIN:
            agent.state.pending_meetup = None            # missed the window
            self._promise_missed(agent, m, pm, now)
            return None
        if partner is None or not pm or pm.get("partner") != agent.id:
            agent.state.pending_meetup = None            # partner isn't coming any more
            self._promise_missed(agent, m, pm, now)
            return None
        loc = m["location"]
        if agent.state.location != loc:
            return Decision(action="move", target_location=loc, duration=10, level=2,
                            reason=f"heading to meet {partner.name}",
                            narrative_verb="meetup_go", narrative_target=partner.id)
        if (partner.state.location == loc and partner.state.current_action != "sleep"
                and partner.state.busy_until <= now):
            agent.state.pending_meetup = None            # engine clears the partner's on initiation
            return Decision(action="talk", talk_partner=partner.id, duration=TALK_DURATION_MIN,
                            level=2, is_meetup=True, reason=f"catching up with {partner.name}")
        return Decision(action="idle", duration=10, level=2,
                        reason=f"waiting at {loc} for {partner.name}")

    # ---- Level 2: one-call conversation ------------------------------

    async def _maybe_share_rumor(self, a: Agent, b: Agent, world: World, now: int) -> dict | None:
        """Decide (in the decision layer) whether ``a`` passes a rumor to ``b``.
        On success: records b's memory + the registry spread, and returns a
        descriptor for the engine to publish as an event. Returns None when
        nothing is shared (the common case -- keeps rumor-free runs untouched)."""
        candidates = [
            (r, v) for r, v in self.rumors.known_by(a.id)
            if not self.rumors.knows(r.id, b.id) and not r.resolved  # resolved rumors stop spreading
        ]
        if not candidates:
            return None
        rumor, version = max(candidates, key=lambda rv: rv[1].minute)  # most recently heard

        p = (
            0.25
            + 0.5 * a.profile.extraversion
            + (0.2 if "gossipy" in a.profile.traits else 0.0)
            + (0.15 if "talkative" in a.profile.traits else 0.0)
        )
        p = min(p, 0.95)
        seed = int(hashlib.sha256(f"{a.id}|{b.id}|{rumor.id}|{now}".encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)
        if rng.random() >= p:
            return None

        text = version.text
        if rng.random() < RUMOR_DISTORT_CHANCE:
            res = await self.router.generate(
                task="distort",
                messages=builders.distort_prompt(text),
                agent_id=a.id,
                sim_minute=now,
                schema={"type": "object"},
                max_tokens=160 if builders.lang_is_zh() else 80,
                per_call_timeout=SYNC_CALL_TIMEOUT_S,   # in-tick (start_conversation): don't park the loop
            )
            new = str(res.parsed.get("text") or "") if isinstance(res.parsed, dict) else ""
            if new and _knowledge_lang_ok(new):   # wrong language -> keep the prior version
                text = new

        if rumor.subject and rumor.subject == b.id:
            # The rumor made it back to the person it is about -- no relationship
            # math against oneself; instead it lands hard and demands a reaction.
            b.memory.add(MemoryItem(
                minute=now, text=texts.T("rumor_about_me", teller=a.id, text=text),
                importance=8, kind="rumor", rumor_id=rumor.id,
            ))
            if rumor.sentiment < 0:
                b.state.mood = "upset"
            b.state.pending_concern = {"rumor_id": rumor.id, "told_by": a.id}
        else:
            b.memory.add(MemoryItem(
                minute=now, text=texts.T("heard_from", teller=a.id, text=text),
                importance=4, kind="rumor", rumor_id=rumor.id,
            ))
            # Relationship math stays in Python; the rumor's polarity moves it.
            if rumor.subject:
                rel = b.rel(rumor.subject)
                rel.trust += rumor.sentiment * 5
                rel.friendship += rumor.sentiment * 2
                rel.clamp()
                if rumor.sentiment <= -0.4:
                    # A strongly negative rumor about a shopkeeper drives custom away:
                    # b now shuns any shop that subject runs (economy bite).
                    shop = next((lid for lid, loc in world.locations.items()
                                 if loc.owner == rumor.subject and loc.price > 0), "")
                    if shop:
                        b.state.avoid_location = shop
                    if rel.friendship > 55:
                        b.state.mood = "worried"  # a friend is being badmouthed
            # Sharing gossip brings the two a little closer.
            gossip_bond = b.rel(a.id)
            gossip_bond.friendship += 1
            gossip_bond.clamp()

        self.rumors.record_spread(rumor.id, a.id, b.id, text, now)
        return {"rumor_id": rumor.id, "text": text, "from": a.id, "to": b.id}

    # ---- secrets: confiding & leaking --------------------------------

    def _resolve_secret(self, owner: Agent, secret, now: int, resolution: str) -> bool:
        """Lay a secret to rest and leave the owner a memory of it (importance 5) --
        letting go is itself worth remembering and gives later reflection something
        to grow a 'lighter lately' impression from. Idempotent (once per secret)."""
        if not self.secrets.resolve(secret.id, now, resolution):
            return False
        owner.memory.add(MemoryItem(minute=now, text=resolution, importance=5, kind="reflection"))
        self.rewrite_goals_on_resolve(owner, secret, now)   # the worry's goal moves forward too
        owner.memory.suppress_theme(self.secret_subject(secret),
                                    self.secret_theme_keywords(secret), now)  # old anxiety fades
        # A laid-to-rest worry that IS the current pursuit ends that chapter (Xixi
        # finally asking Aisi; Xue coming to terms with the job question).
        if self.on_chapter_signal is not None and chapters_mod.secret_matches_chapter(owner.chapter, secret):
            self.on_chapter_signal(owner, "completed", "secret_resolved", resolution)
        return True

    # ---- wish generation: one LLM call + three gates (phase 2b) --------------

    def _attempt_row(self, agent_id: str, attempt_no: int, spent_before: int,
                     outcome: str, gate: str = "", reason: str = "") -> dict:
        """One row of the generation ledger (db/models.WishAttemptRow). ``reason`` is
        redacted before it is stored, and the cost is the spend THIS attempt added --
        the chain may have burned several providers getting here."""
        cost = sum(c.estimated_cost for c in self.router.usage.calls[spent_before:]
                   if c.agent_id == agent_id and c.task_type == "wish_generation")
        return {"attempt_no": attempt_no, "outcome": outcome, "gate": gate,
                "reason": wishes_mod.ledger_reason(reason), "cost_usd": round(cost, 6),
                "wish_id": ""}      # stamped by the engine on the attempt that landed

    async def grow_wish(self, agent: Agent, world: World, day: int, residue: str = "",
                        attempts: list | None = None) -> tuple:
        """Ask this resident's own history what they now want, and let three
        mechanical gates decide whether the answer may exist. Returns
        ``(clean, outcome)`` where outcome is one of:

            ok        -- a wish survived; ``clean`` is ready to install
            declined  -- the reflection said this life is not asking for anything
            gates     -- every attempt was rejected; the reasons were fed back
            failed    -- the chain could not answer at all

        There is no template fallback on purpose. A resident whose life produces
        nothing to want gets ordinary days, which is a truthful outcome; a
        manufactured wish would not be.

        ``attempts``, when given, is filled with one ledger row per attempt made --
        including the ones that came to nothing, which is the whole point (see
        db/models.WishAttemptRow). It is an out-parameter rather than part of the
        return value so a caller still gets the partial record if this raises."""
        material = wishes_mod.generation_material(agent, world, day, residue)
        # Memory text is stored with {agent:id}/{loc:id}/{landmark:id} placeholders.
        # Every other free-text prompt resolves them first; this one must too, or the
        # model reads "{landmark:installation}" and may well write it back out.
        for item in material["biography"] + material["memories"]:
            item["text"] = _resolve_placeholders(item["text"], world)
        for key in ("town_objects", "my_actions"):
            material[key] = [_resolve_placeholders(t, world) for t in material.get(key) or []]
        rejection = ""
        log = attempts if attempts is not None else []
        last_hit = ""                    # the old wish the previous attempt ran into, if any
        for attempt in range(1 + wishes_mod.GENERATION_RETRIES):
            spent_before = len(self.router.usage.calls)
            try:
                res = await self.router.generate(
                    task="wish_generation",
                    messages=builders.wish_generation_prompt(agent, material, rejection),
                    agent_id=agent.id, sim_minute=day * 24 * 60,
                    schema={"type": "object"}, max_tokens=800 if builders.lang_is_zh() else 500,
                    validate=lambda r: (_note_zh_gate(_wish_text_zh_ok(r.parsed))
                                        and _note_gender_gate(_wish_text_gender_ok(r.parsed))),
                    per_call_timeout=45.0, no_floor=True,
                )
            except builders.RosterNotLoadedError:
                raise
            except Exception as err:
                print(f"[wish] generation chain failed for {agent.id}: {err!r}", flush=True)
                log.append(self._attempt_row(agent.id, attempt + 1, spent_before,
                                             "gen_failed", reason=repr(err)))
                return None, "failed"
            parsed = res.parsed if isinstance(res.parsed, dict) else None
            if parsed is None:
                log.append(self._attempt_row(agent.id, attempt + 1, spent_before, "gen_failed",
                                             reason="the chain answered with no usable object"))
                return None, "failed"
            if parsed.get("no_wish"):
                # A decline is data, not a non-event: the model's own reason is the
                # only account of why this life is not asking for anything today.
                declined = str(parsed.get("reason", ""))
                print(f"[wish] {agent.id} declined: {declined[:90]}", flush=True)
                log.append(self._attempt_row(agent.id, attempt + 1, spent_before,
                                             "declined", reason=declined))
                return None, "declined"

            clean, problems = wishes_mod.validate_generation(parsed, agent, world, day, material)
            if problems:                                        # gate 1: feasibility + provenance
                gate = "wish_feasibility"
                builders.note_gate_reject(gate)
                rejection = "; ".join(problems)
            else:
                ok, why = wishes_mod.deviation_ok(agent, world, clean["requirements"])
                if not ok:                                      # gate 2: does it change anything?
                    gate = "wish_deviation"
                    builders.note_gate_reject(gate)
                    rejection = why
                elif not (struct := wishes_mod.structural_collision(
                        agent, clean["requirements"], day))[0]:  # gate 3a: the same doings again?
                    gate = "wish_novelty_struct"
                    builders.note_gate_reject(gate)
                    rejection = struct[1]
                else:
                    vector = await self.embedder.embed(
                        wishes_mod.novelty_text(clean["statement"], clean["requirements"]))
                    ok, why = wishes_mod.novelty_ok(vector, agent, world)
                    if not ok:                                  # gate 3: have we been here before?
                        gate = "wish_novelty"
                        builders.note_gate_reject(gate)
                        rejection = why
                    else:
                        clean["embedding"] = vector
                        log.append(self._attempt_row(agent.id, attempt + 1, spent_before, "born"))
                        return clean, "ok"
            # The proposal's own words are NOT recorded -- only which gate turned it
            # away and why, with any quoted wording redacted out.
            log.append(self._attempt_row(agent.id, attempt + 1, spent_before,
                                         "gate_rejected", gate, rejection))
            print(f"[wish] {agent.id} proposal rejected (attempt {attempt + 1}): "
                  f"{rejection[:120]}", flush=True)
            # Twice in a row into the same old wish: the feedback is not turning this
            # life another way, and a third call would only pay for the same answer.
            hit = struct[2] if gate == "wish_novelty_struct" else ""
            if hit and hit == last_hit:
                print(f"[wish] {agent.id} keeps returning to the same wish -- no further try today",
                      flush=True)
                break
            last_hit = hit
        return None, "gates"

    # ---- chapter closure: the one LLM call (smart tier, rare) ----------------

    async def closure_reflection(self, agent: Agent, world: World, material: dict,
                                 outcome: str) -> dict | None:
        """Ask the smart tier for the biography line + residue + memory refs. Returns
        the validated dict, or None on any failure (chain exhausted, timeout, junk) --
        the engine then uses the rule-layer template line, so closure never blocks.
        ``no_floor``: a canned mock line must not become someone's life story when a
        real chain exists (mock-only runs still serve the deterministic mock)."""
        try:
            res = await self.router.generate(
                task="chapter_closure",
                # The model reads the memories with names, not {agent:id} placeholders
                # (the gate below still checks ids and numbers against the originals).
                messages=builders.chapter_closure_prompt(
                    agent, {**material, **{k: [{**m, "text": _resolve_placeholders(m["text"], world)}
                                               for m in material.get(k) or []]
                                           for k in ("memories", "aftermath")},
                               "promises": [_resolve_placeholders(p, world)
                                            for p in material.get("promises") or []],
                               "actions": [_resolve_placeholders(p, world)
                                           for p in material.get("actions") or []]},
                    outcome, chapters_mod.relationship_summary_lines(material, world)),
                agent_id=agent.id, sim_minute=material["window"]["end_minute"],
                schema={"type": "object"}, max_tokens=300 if builders.lang_is_zh() else 200,
                validate=lambda r: (chapters_mod.validate_closure_output(r.parsed, material) is not None
                                    and _note_gender_gate(gender_ok(
                                        str((r.parsed or {}).get("biography_line", ""))
                                        if isinstance(r.parsed, dict) else ""))),
                per_call_timeout=30.0, no_floor=True,
            )
        except builders.RosterNotLoadedError:
            raise      # a bare gender gate is a programming error -- never a template fallback
        except Exception as err:
            print(f"[chapter] closure reflection failed for {agent.id} ({err!r}); using template line", flush=True)
            return None
        return chapters_mod.validate_closure_output(res.parsed, material)

    @staticmethod
    def secret_subject(secret) -> str:
        """Who/what a secret is about -- the seeded ``about`` id, else the first
        proper-noun name in the text (so a pre-``about`` secret still resolves)."""
        if secret.about:
            return secret.about.lower()
        for w in secret.text.split():
            c = w.strip(".,;:!?'\"")
            if c.istitle() and len(c) > 2 and c.lower() not in ("i",):
                return c.lower()
        return ""

    @staticmethod
    def secret_theme_keywords(secret) -> set:
        """The distinctive words of a secret's worry -- used to fade pre-resolution
        memories of the same theme."""
        return ({w.strip(".,;:!?'\"").lower() for w in secret.text.split() if len(w) > 3}
                - DecisionEngine._GOAL_STOP) | texts.cjk_bigrams(secret.text)

    def rebuild_suppressed_themes(self, world: World) -> None:
        """Re-derive every agent's suppressed themes from the resolved secrets --
        called after a snapshot restore, so the down-weight survives a resume without
        needing its own snapshot slot (the secrets are the source of truth)."""
        for a in world.agents.values():
            a.memory.suppressed = []
        for s in self.secrets.secrets.values():
            if s.resolved:
                owner = world.agents.get(s.owner)
                if owner is not None:
                    before = s.resolved_minute if s.resolved_minute >= 0 else 10 ** 12
                    owner.memory.suppress_theme(self.secret_subject(s), self.secret_theme_keywords(s), before)

    # Common short words to ignore when matching a goal to a secret's theme.
    _GOAL_STOP = {"want", "have", "been", "that", "this", "with", "from", "they", "them",
                  "their", "about", "every", "time", "work", "just", "keep", "make", "your",
                  "into", "will", "would", "could", "should", "there", "when", "what"}

    @staticmethod
    def goal_matches_secret(goal_text: str, secret) -> bool:
        """A goal is 'about' a secret when it names the secret's subject or shares
        enough distinctive words with it."""
        g = texts.word_set(goal_text)
        about = (secret.about or "").lower()
        if about and (about in g or (texts.zh_native() and texts.mentions(goal_text, about))):
            return True
        kw = DecisionEngine.secret_theme_keywords(secret)
        if about:
            kw.add(about)
        return len(kw & g) >= 2

    @staticmethod
    def forward_goal(goal_text: str, secret) -> str:
        """Rewrite an anxious goal into a moving-forward one (story advances, not
        erased). Handles the "ask X to teach me Y" shape precisely; otherwise a
        gentle generic reframe toward the secret's subject."""
        m = re.search(r"ask (\w+) to teach me (.+?)(?:\.|$| but| every)", goal_text, re.I)
        if m:
            return texts.T("goal_forward_teach", what=m.group(2).strip(), who=m.group(1).capitalize())
        m = re.search(r"請(.+?)教我(.+?)(?:[。,,]|$)", goal_text)   # the zh shape of the same goal
        if m:
            return texts.T("goal_forward_teach", what=m.group(2).strip(), who=m.group(1).strip())
        about = texts.display_name(secret.about) if secret.about else ""
        if not about:
            for w in secret.text.split():           # else pull a name out of the secret text
                if w.istitle() and len(w) > 2:
                    about = w
                    break
        return texts.T("goal_forward_generic", who=about) if about else ""

    def rewrite_goals_on_resolve(self, owner: Agent, secret, now: int) -> list[tuple[str, str]]:
        """Rewrite any of the owner's goals that were about this now-settled worry.
        Returns (old, new) pairs; leaves an importance-4 memory of the shift."""
        changed: list[tuple[str, str]] = []
        for g in owner.profile.goals:
            old = str(g.get("goal", ""))
            if not self.goal_matches_secret(old, secret):
                continue
            new = self.forward_goal(old, secret)
            if new and new != old:
                g["goal"] = new
                owner.memory.add(MemoryItem(
                    minute=now, importance=4, kind="reflection",
                    text=texts.T("goal_forward", new=new)))
                changed.append((old, new))
        return changed

    def _maybe_confide(self, a: Agent, b: Agent, now: int) -> dict | None:
        """Decide whether ``a`` confides one of their OWN secrets in ``b`` -- pure
        Python, no LLM. Gated on trust vs. the secret's sensitivity (more private ->
        higher bar); each secret is confided to a person at most once; a low mood
        makes confiding likelier. Resolved secrets are never confided. Confiding a
        worry straight to the person it is *about* resolves it. Returns a descriptor
        (for the prompt + a confide event) or None (the common case)."""
        rel = a.rel(b.id)
        eligible = [
            s for s in self.secrets.active_secrets_of(a.id)           # resolved secrets are laid to rest
            if not self.secrets.knows(s.id, b.id)                      # b isn't owner and hasn't been told
            and rel.trust >= CONFIDE_TRUST_BASE + s.sensitivity * 30
        ]
        if not eligible:
            return None
        # If a worry is *about* the listener, opening up to them is the whole point --
        # prefer it over a merely weightier unrelated secret.
        about_matches = [s for s in eligible if s.about and s.about == b.id]
        secret = max(about_matches or eligible, key=lambda s: s.sensitivity)
        p = min(CONFIDE_MAX_P,
                0.15 + rel.trust / 200 + (0.1 if a.state.mood in ("worried", "upset") else 0.0))
        seed = int(hashlib.sha256(f"confide|{a.id}|{b.id}|{secret.id}|{now}".encode()).hexdigest()[:8], 16)
        if random.Random(seed).random() >= p:
            return None
        b.memory.add(MemoryItem(
            minute=now, text=texts.T("confided", owner=a.id, text=secret.text),
            importance=int(4 + secret.sensitivity * 4), kind="secret", secret_id=secret.id,
        ))
        self.secrets.record_confide(secret.id, b.id, now)
        rel.trust += 3; rel.clamp()                                    # confiding deepens the confider's trust
        br = b.rel(a.id); br.friendship += 4; br.clamp()              # being trusted feels good
        # Confiding to the very person the worry is about closes it out.
        if secret.about and secret.about == b.id:
            self._resolve_secret(
                a, secret, now,
                f"{a.id.capitalize()} finally found the courage to open up to {b.id.capitalize()}.")
        return {"secret_id": secret.id, "text": secret.text, "from": a.id, "to": b.id}

    async def _maybe_leak(self, b: Agent, c: Agent, world: World, now: int) -> dict | None:
        """Decide whether ``b`` leaks a secret they were confided (owner != b) to
        ``c``, turning it into a rumor. Rare: high-trust confidants almost never
        leak, a low-trust gossip is the danger. On a leak the secret becomes a
        rumor (leak_prompt gives third-person wording + sentiment) and enters the
        ordinary rumor lifecycle; returns a share-rumor descriptor (published as
        share_rumor -- the world only sees a rumor appear, never the leak itself)."""
        candidates = [
            s for s in self.secrets.secrets.values()
            if not s.leaked and not s.resolved                        # a resolved worry is no longer leak-worthy
            and s.owner != b.id and b.id in s.confided_to
            and s.owner != c.id and not self.secrets.knows(s.id, c.id)
        ]
        if not candidates:
            return None
        secret = candidates[0]
        trust_to_owner = b.rel(secret.owner).trust
        p = (1 - trust_to_owner / 100) * 0.3 * (1.2 - secret.sensitivity)
        if "gossipy" in b.profile.traits or "talkative" in b.profile.traits:
            p *= 1.5
        p = min(LEAK_MAX_P, p)
        seed = int(hashlib.sha256(f"leak|{b.id}|{c.id}|{secret.id}|{now}".encode()).hexdigest()[:8], 16)
        if random.Random(seed).random() >= p:
            return None

        owner = world.agents.get(secret.owner)
        owner_name = texts.display_name(secret.owner) if owner else secret.owner
        res = await self.router.generate(       # rephrase to a 3rd-person rumor + appraise, in one call
            task="leak", messages=builders.leak_prompt(owner_name, secret.text),
            agent_id=b.id, sim_minute=now, schema={"type": "object"}, max_tokens=160 if builders.lang_is_zh() else 80,
            per_call_timeout=SYNC_CALL_TIMEOUT_S,   # in-tick (start_conversation): don't park the loop
        )
        parsed = res.parsed if isinstance(res.parsed, dict) else {}
        text = str(parsed.get("text") or "").strip()
        if not text or not _knowledge_lang_ok(text):   # wrong language -> a plain fallback
            text = texts.T("leak_fallback", owner=owner_name)
        try:
            sentiment = max(-1.0, min(1.0, float(parsed.get("sentiment", -0.3))))
        except (TypeError, ValueError):
            sentiment = -0.3

        rumor = self.rumors.seed(
            agent_id=c.id, text=text, minute=now, subject=secret.owner,
            sentiment=sentiment, from_secret_id=secret.id,
        )
        self.secrets.mark_leaked(secret.id, leaked_by=b.id)
        # c now holds the leaked rumor: record it + apply the recipient relationship math.
        c.memory.add(MemoryItem(
            minute=now, text=texts.T("heard_from", teller=b.id, text=text), importance=5, kind="rumor",
            rumor_id=rumor.id))
        rel = c.rel(secret.owner)
        rel.trust += sentiment * 5; rel.friendship += sentiment * 2; rel.clamp()
        if sentiment <= -0.4:
            shop = next((lid for lid, loc in world.locations.items()
                         if loc.owner == secret.owner and loc.price > 0), "")
            if shop:
                c.state.avoid_location = shop
        gossip_bond = c.rel(b.id); gossip_bond.friendship += 1; gossip_bond.clamp()
        return {"rumor_id": rumor.id, "text": text, "from": b.id, "to": c.id,
                "leak": True, "subject": secret.owner, "secret_text": secret.text}

    async def start_conversation(
        self, a: Agent, b: Agent, world: World, now: int, confront_text: str | None = None,
        confront_rumor_id: str = "", count_against_cap: bool = True,
    ) -> ConvPlan:
        """Initiation (runs synchronously w.r.t. the world clock, in the tick):
        decide gossip / leaks / confides now -- each mutates memory + relationships
        while both parties are about to be locked -- snapshot each side's retrieved
        memories and impressions, and build the dialogue prompt. Returns a ``ConvPlan``
        the engine hands to a background task; the world keeps advancing while the
        dialogue itself generates, so only the two participants freeze.

        ``confront_text``/``confront_rumor_id`` make the exchange a rumor
        confrontation (``a`` opens with it) that settles that rumor. The plan's
        ``shared_rumors``/``confided`` are already-decided descriptors for the engine
        to publish at initiation."""
        is_confront = bool(confront_text and confront_rumor_id)
        fwd = await self._maybe_share_rumor(a, b, world, now)   # a -> b
        rev = await self._maybe_share_rumor(b, a, world, now)   # b -> a (lets a stationary initiator hear too)
        # Leaking a confided secret surfaces AS a rumor share (same descriptor shape).
        leak_fwd = await self._maybe_leak(a, b, world, now)     # a leaks a secret confided to a, to b
        leak_rev = await self._maybe_leak(b, a, world, now)
        shared_rumors = [sr for sr in (fwd, rev, leak_fwd, leak_rev) if sr]
        # Confiding is separate from gossip: a shares one of their OWN secrets with b.
        confide_fwd = self._maybe_confide(a, b, now)
        confide_rev = self._maybe_confide(b, a, now)
        confided = [c for c in (confide_fwd, confide_rev) if c]
        # Confession: if either has resolved to confess to the other, this solo scene
        # becomes it. The outcome is settled by the rules now; the scene just plays it.
        confession = None
        if a.state.pending_confession == b.id:
            confession = {"from": a.id, "to": b.id, "from_name": a.name, "to_name": b.name,
                          "accepted": b.rel(a.id).romance >= romance_mod.ACCEPT_ROMANCE}
        elif b.state.pending_confession == a.id:
            confession = {"from": b.id, "to": a.id, "from_name": b.name, "to_name": a.name,
                          "accepted": a.rel(b.id).romance >= romance_mod.ACCEPT_ROMANCE}
        # Query by the English/pinyin name, matching how memories are stored (a 2-char
        # zh name gets filtered to noise by the bag-of-words embedder -- see MockEmbedding).
        # The place + whatever is explicitly on the table (a rumor, a confrontation)
        # feed biography surfacing only -- the ordinary top-k query is unchanged.
        a_topic = " ".join(x for x in (confront_text or "", (fwd or leak_fwd or {}).get("text", "")) if x)
        b_topic = " ".join(x for x in ((rev or leak_rev or {}).get("text", ""),) if x)
        a_mem = await a.memory.retrieve_async(texts.name_query(b.id), k=3, location=a.state.location, topic=a_topic)
        b_mem = await b.memory.retrieve_async(texts.name_query(a.id), k=3, location=b.state.location, topic=b_topic)
        # A held impression of the other person rides into the dialogue context, so
        # the model naturally carries the weight of the relationship's history.
        a_imp = self._impression_of(a, b.id)
        b_imp = self._impression_of(b, a.id)
        intimate = is_confront or bool(confide_fwd or confide_rev) or confession is not None
        place = world.locations.get(a.state.location)
        place_name = (((place.name_zh or place.name) if texts.zh_native() else place.name)
                      if place else a.state.location)
        scene = {"time": builders.time_of_day(now), "place": place_name,
                 "nearby": self._nearby_landmark(world, a.state.location, now),
                 "objects": [_resolve_placeholders(x, world)
                             for x in actions_mod.scene_lines(world, a.state.location, now)]}
        a_intents: list[str] = []
        b_intents: list[str] = []
        a_role = b_role = ""
        a_mention = (fwd or leak_fwd or {}).get("text")
        b_mention = (rev or leak_rev or {}).get("text")
        if is_confront:
            a_role, b_role = "confronter", "accused"
            a_intents.append(
                f"You came to ask {b.name} to their face whether this came from them -- in your own "
                f"words, as a direct question (do not read it out verbatim): {confront_text}")
            b_intents.append(f"{a.name} sought you out and seems to have something serious to ask you.")
        elif a_mention:
            a_intents.append(f"Something you want to bring up: {a_mention}")
        if b_mention:
            b_intents.append(f"Something you want to bring up: {b_mention}")
        for cf, intents, partner in ((confide_fwd, a_intents, b), (confide_rev, b_intents, a)):
            if cf:
                intents.append(
                    f"You have decided to open up to {partner.name} about a private worry. Background "
                    f"(say it in the first person, in your own words, never quoted): {cf['text']}")
        first, second = a, b
        if confession:
            fr = a if confession["from"] == a.id else b
            to = b if fr is a else a
            (a_intents if fr is a else b_intents).append(
                f"You have decided this is the moment to tell {to.name} you have romantic feelings "
                f"for them -- sincere, a little nervous.")
            (a_intents if to is a else b_intents).append(
                (f"If {fr.name} opens up about romantic feelings: you feel the same way, and you say "
                 f"yes, warmly.") if confession["accepted"] else
                (f"If {fr.name} opens up about romantic feelings: you don't feel the same, and you "
                 f"turn them down gently and kindly, never cruelly."))
            first, second = fr, to
        elif confide_rev and not confide_fwd and not is_confront:
            first, second = b, a                       # the one opening up speaks first
        ctx = {
            a.id: self._speaker_ctx(a, b, world, now, _resolve_mems(a_mem, world), a_imp, a_intents,
                                    a_role, scene, (confide_fwd or {}).get("secret_id", "")),
            b.id: self._speaker_ctx(b, a, world, now, _resolve_mems(b_mem, world), b_imp, b_intents,
                                    b_role, scene, (confide_rev or {}).get("secret_id", "")),
        }
        tense = intimate or self._tense_pair(a, b)
        min_turns = pacing.DIALOGUE_INTIMATE_MIN_TURNS if intimate else 2
        # Count the conversation against the daily budget at initiation (serialized
        # in the tick), so the cap stays honest even though generation is async.
        # A kept meetup is exempt: it's a deliberate social beat, not routine chatter.
        self._roll_day(now)
        if count_against_cap:
            self._dialogues_today += 1
        return ConvPlan(
            a=a, b=b, location=a.state.location, init_minute=now,
            ctx=ctx, order=(first, second),
            task="dialogue_turn_tense" if tense else "dialogue_turn",
            min_turns=min_turns, max_turns=max(min_turns, pacing.DIALOGUE_MAX_TURNS),
            # one line + its JSON fields; Chinese runs ~2-3x the tokens per character
            max_tokens=320 if builders.lang_is_zh() else 220,
            is_confront=is_confront, confront_rumor_id=confront_rumor_id,
            shared_rumors=shared_rumors, confided=confided, confession=confession,
        )

    async def plan_day(self, agent: Agent, world: World, day: int, now: int) -> dict:
        """The morning micro-decision (agents/dayplan.py). One cheap call; a chain
        failure raises ProvidersExhausted (no canned plan -- the usual day stands)."""
        mat = dayplan_mod.material(agent, world, self, day)

        def ok(r: object) -> bool:
            p = getattr(r, "parsed", None)
            return (isinstance(p, dict) and bool(str(p.get("intent") or "").strip())
                    and _knowledge_lang_ok(str(p.get("intent"))))
        res = await self.router.generate(
            task="day_plan", messages=builders.day_plan_prompt(agent, mat), agent_id=agent.id,
            sim_minute=now, schema={"type": "object"},
            max_tokens=400 if builders.lang_is_zh() else 260, validate=ok,
            per_call_timeout=DIALOGUE_PROVIDER_TIMEOUT_S, no_floor=True)
        return dayplan_mod.validate(res.parsed, agent, world, day)

    # A crush is carried privately: saying it out loud is the romance track's call
    # (a confession scene arrives as an explicit intent), never a line's whim.
    _ROMANCE_PHRASE = {"crushing": "you have feelings for them that you have not told them, "
                                   "and you are not ready to say them out loud",
                       "dating": "you two are dating", "partners": "you two are partners"}

    def _speaker_ctx(self, me: Agent, other: Agent, world: World, now: int, mems: list,
                     impression: str | None, intents: list, role: str, scene: dict,
                     confiding_secret: str = "") -> dict:
        """Everything ONE speaker brings into the conversation -- and nothing of the
        other's inner life. Their memories of the partner (retrieved), what has been
        on their mind lately (their own recent weighty memories), their own active
        wishes in full (minor ones included), their private matters, mood, and how
        they see the partner."""
        day = now // (24 * 60) + 1
        seen = set(mems)
        recent: list[str] = []
        for m in reversed(me.memory.items[-80:]):
            if len(recent) >= 3:
                break
            # a closed chapter's memories (weight < 1) stay out, as they do from retrieval
            if (m.minute < now - 2 * 24 * 60 or m.importance < 4 or m.kind == "biography"
                    or m.weight < 1.0):
                continue
            t = _resolve_placeholders(m.text, world)
            if t not in seen:
                seen.add(t)
                recent.append(t)
        recent.reverse()
        wishes: list[str] = []
        for w in wishes_mod.active_wishes(me):
            line = f"[{w.scale}] {w.title}: {w.statement}" if w.title else w.statement
            if w.expires_on:
                line += f" ({max(0, w.expires_on - day)} days left)"
            wishes.append(_resolve_placeholders(line, world))
        private = [_resolve_placeholders(x.text, world) for x in self.secrets.active_secrets_of(me.id)
                   if x.id != confiding_secret][:2]
        rel = me.rel(other.id)
        return {
            "name": me.name, "partner": other.name,
            "card": builders.character_card(me, speech=True), "mood": mood_mod.describe(me, world),
            "time": scene["time"], "place": scene["place"], "nearby": scene["nearby"],
            "objects": list(scene.get("objects") or []),
            "bond": transitions_mod.rel_stage(rel.friendship),
            "romance": self._ROMANCE_PHRASE.get(getattr(rel, "romance_stage", "") or "", ""),
            "impression": impression, "memories": list(mems), "recent": recent,
            "wishes": wishes, "private": private, "intents": list(intents), "role": role,
        }

    def _tense_pair(self, a: Agent, b: Agent) -> bool:
        """Hard rules for the smart tier beyond confide/confront/confession: a secret
        whose confide bar is within reach, or a talk that bears on a wish or pursuit
        (one side's active wish or pursuit is about the other)."""
        margin = pacing.DIALOGUE_TENSE_TRUST_MARGIN
        for x, y in ((a, b), (b, a)):
            trust = x.rel(y.id).trust
            for sec in self.secrets.active_secrets_of(x.id):
                if (not self.secrets.knows(sec.id, y.id)
                        and abs(trust - (CONFIDE_TRUST_BASE + sec.sensitivity * 30)) <= margin):
                    return True
            for w in wishes_mod.active_wishes(x):
                if any(getattr(r, "target", "") == y.id for r in w.requirements):
                    return True
            ch = x.chapter
            if ch is not None and ch.chapter_type == "pursuit" and texts.mentions(ch.narrative or "", y.id):
                return True
        return False

    async def generate_conversation(self, plan: ConvPlan) -> object:
        """The slow half of a conversation, run in a background task: one LLM call
        per line. Each provider is bounded by ``DIALOGUE_PROVIDER_TIMEOUT_S``;
        ``no_floor`` makes a whole-chain failure on any line raise
        ``ProvidersExhausted`` (never a canned line) -- the engine brews and retries,
        and the retry resumes from the lines already spoken."""
        async def call(sp: Agent, messages: list, validate) -> object:
            return await self.router.generate(
                task=plan.task, messages=messages, agent_id=sp.id,
                sim_minute=plan.init_minute, schema={"type": "object"},
                max_tokens=plan.max_tokens, validate=validate,
                per_call_timeout=DIALOGUE_PROVIDER_TIMEOUT_S, no_floor=True)
        return await self._run_turns(plan, call)

    async def mock_dialogue(self, plan: ConvPlan) -> object:
        """Deterministic floor -- used only once every retry round is spent, so the
        two participants always unlock with a valid scene. It finishes the
        conversation from wherever the live lines stopped. Records each call to
        usage (it bypasses ``router.generate``) so the floor is visible."""
        mock = self.router.tiers["normal"][-1]   # the mock floor is always the chain's last tier

        async def call(sp: Agent, messages: list, validate) -> object:
            res = await mock.generate(messages, schema={"type": "object"}, max_tokens=plan.max_tokens)
            self.router.usage.record(LLMCall(
                sim_minute=plan.init_minute, agent_id=sp.id, task_type=plan.task,
                provider=res.provider, model=res.model,
                input_tokens=res.input_tokens, output_tokens=res.output_tokens,
                latency_ms=res.latency_ms, estimated_cost=0.0))
            return res
        return await self._run_turns(plan, call)

    async def _run_turns(self, plan: ConvPlan, call) -> DialogueResult:
        first, second = plan.order
        while len(plan.turns) < plan.max_turns:
            n = len(plan.turns)
            sp = first if n % 2 == 0 else second
            messages = builders.dialogue_turn_prompt(plan.ctx[sp.id], plan.turns, n + 1,
                                                     plan.max_turns, plan.min_turns)
            res = await call(sp, messages, lambda r, sp=sp: self._turn_ok(r, sp, plan))
            turn = self._clean_turn(res.parsed, sp, plan.ctx[sp.id].get("role") == "accused")
            plan.turns.append(turn)
            plan.providers.append(f"{res.provider}/{res.model}")
            if turn["storm_off"] or (turn["end"] and len(plan.turns) >= plan.min_turns):
                break
        top = collections.Counter(plan.providers).most_common(1)
        provider, _, model = (top[0][0] if top else "mock/mock-1").partition("/")
        return DialogueResult(parsed=self._aggregate(plan), provider=provider, model=model)

    @staticmethod
    def _turn_ok(r: object, sp: Agent, plan: ConvPlan) -> bool:
        """Per-line quality gate: the same structural/zh gate and referential anchor
        the one-shot dialogue had, applied to this single line."""
        p = getattr(r, "parsed", None)
        if not isinstance(p, dict) or not str(p.get("text") or "").strip():
            return False
        shaped = DialogueResult(parsed={"turns": [{"speaker": sp.name, "text": str(p["text"]).strip()}]},
                                provider=getattr(r, "provider", "?"), model=getattr(r, "model", "?"))
        if not _dialogue_ok(shaped):
            return False
        if not _referential_ok(shaped.parsed, plan.a, plan.b):
            if _GATE_DIAG:
                print(f"[ref-reject] {shaped.provider}/{shaped.model} {plan.a.id}<->{plan.b.id}", flush=True)
            return False
        return True

    @staticmethod
    def _clean_turn(parsed: object, sp: Agent, accused: bool) -> dict:
        p = parsed if isinstance(parsed, dict) else {}

        def unit(key: str, default: float) -> float:
            try:
                return max(0.0, min(1.0, float(p.get(key, default))))
            except (TypeError, ValueError):
                return default
        text = str(p.get("text") or "").strip()
        for form in (sp.name, sp.id.capitalize()):     # a model sometimes labels its own line
            for sep in (":", "\uff1a"):
                if form and text.startswith(form + sep):
                    text = text[len(form) + 1:].strip()
        plan = p.get("plan")
        if not (isinstance(plan, dict) and str(plan.get("place") or "").strip()):
            plan = None
        admitted = p.get("admitted")
        return {"speaker": sp.name, "speaker_id": sp.id, "text": text,
                "end": p.get("end") is True, "storm_off": p.get("storm_off") is True,
                "sentiment": unit("sentiment", 0.5), "trust_signal": unit("trust_signal", 0.0),
                "conflict_signal": unit("conflict_signal", 0.0),
                "tells": str(p.get("tells") or "").strip(), "plan": plan,
                "admitted": admitted if accused and isinstance(admitted, bool) else None}

    @staticmethod
    def _aggregate(plan: ConvPlan) -> dict:
        """Fold the lines into the exchange: overall signals, each side's OWN
        signals (how it felt to them), and the accused's last explicit verdict."""
        keys = (("sentiment", 0.5), ("trust_signal", 0.0), ("conflict_signal", 0.0))

        def mean(ts: list) -> dict:
            return {k: (round(sum(t[k] for t in ts) / len(ts), 3) if ts else d) for k, d in keys}
        turns = plan.turns
        per_side = {ag.id: mean([t for t in turns if t["speaker_id"] == ag.id] or turns)
                    for ag in (plan.a, plan.b)}
        verdicts = [t["admitted"] for t in turns if t["admitted"] is not None]
        return {"turns": turns, **mean(turns), "per_side": per_side,
                "admitted": verdicts[-1] if verdicts else False}

    @staticmethod
    def _warn_stale_world_terms(a: Agent, b: Agent, turns: object, now: int) -> None:
        """Light observability gate: log (never reject) a generated turn that names a
        prior run's world (see STALE_WORLD_TERMS), so any lingering stale-context bleed
        is visible in the log and its frequency can be watched to zero."""
        if not isinstance(turns, list):
            return
        for turn in turns:
            text = str(turn.get("text", "")) if isinstance(turn, dict) else ""
            low = text.lower()
            for term in STALE_WORLD_TERMS:
                if term.lower() in low:
                    print(f"[stale-world] minute {now}: {a.id}/{b.id} dialogue mentions "
                          f"'{term}' -> {text[:90]!r}", flush=True)
                    return

    def settle_conversation(
        self, plan: ConvPlan, world: World, res: object
    ) -> tuple[list[dict], dict, dict | None, dict | None, list[dict], list[dict]]:
        """Settlement (synchronous, runs when the background generation returns):
        parse the exchange, write both sides' conversation memory, apply each side's
        own relationship signals, and resolve milestone / romance / confrontation and
        the consequences read off the lines (news passed on, a promise, a walk-out).
        Returns ``(turns, signals, confrontation, milestone, romance_events, beats)``
        for the engine to publish (stamped at the conversation's initiation minute)."""
        a, b, now = plan.a, plan.b, plan.init_minute
        parsed = res.parsed if isinstance(res.parsed, dict) else {}
        turns = parsed.get("turns", [])
        self._warn_stale_world_terms(a, b, turns, now)   # observe (don't block) stale-world bleed
        signals = {
            "sentiment": float(parsed.get("sentiment", 0.5)),
            "trust_signal": float(parsed.get("trust_signal", 0.0)),
            "conflict_signal": float(parsed.get("conflict_signal", 0.0)),
        }
        # Both sides remember the conversation (importance via cheap tier
        # is skipped here: conversation gets a flat importance; a real
        # importance call is wired for notable events in the engine).
        a.memory.add(MemoryItem(
            minute=now, text=texts.T("talked_with", other=b.id, loc=plan.location),
            importance=3, kind="conversation"))
        b.memory.add(MemoryItem(
            minute=now, text=texts.T("talked_with", other=a.id, loc=plan.location),
            importance=3, kind="conversation"))
        f_before = a.rel(b.id).friendship                     # for the relationship-milestone check
        per_side = parsed.get("per_side") or {}
        a.apply_conversation_signals(b.id, **(per_side.get(a.id) or signals))
        b.apply_conversation_signals(a.id, **(per_side.get(b.id) or signals))
        for x, y in ((a, b), (b, a)):
            sig = per_side.get(x.id) or signals
            before = x.state.mood_valence
            mood_mod.from_conversation(x, sig["sentiment"], sig["conflict_signal"])
            moved = x.state.mood_valence - before
            if abs(moved) >= mood_mod.NOTE_MIN:
                x.state.mood_note = texts.T("note_talk_good" if moved > 0 else "note_talk_bad", other=y.id)
        a.state.last_talk_minute[b.id] = now
        b.state.last_talk_minute[a.id] = now
        milestone = self._detect_milestone(a, b, f_before, now)

        # Romance: a confession settles by rule (its own scene just played); any
        # other warm exchange nudges the track and may tip a side into crushing.
        romance_events: list[dict] = []
        if plan.confession is not None:
            romance_events.append(self._settle_confession(world, plan.confession, now))
        else:
            self._grow_romance(a, b, world, signals["sentiment"], bool(plan.confided), now)

        confrontation = None
        if plan.is_confront:
            confrontation = self._settle_confrontation(a, b, world, plan.confront_rumor_id, parsed, now)
        beats = self._settle_turn_consequences(plan, world, turns, now)
        return turns, signals, confrontation, milestone, romance_events, beats

    def _settle_turn_consequences(self, plan: ConvPlan, world: World, turns: list, now: int) -> list[dict]:
        """Rules read structured signals off the lines -- the model never applies a
        consequence itself. News one side handed over becomes the listener's memory;
        a meeting both sides voiced becomes a real appointment; a walk-out sours the
        pair and is a beat the town sees."""
        a, b = plan.a, plan.b
        beats: list[dict] = []
        # One piece of news per speaker per conversation: a speaker restates the same
        # thing line after line, and the last version is usually the fullest.
        last_tell: dict[str, str] = {}
        for t in turns:
            tell = t.get("tells") or ""
            if tell and len(tell) <= 120 and _knowledge_lang_ok(tell):
                last_tell[t.get("speaker_id", "")] = tell
        for speaker_id, tell in list(last_tell.items())[:pacing.DIALOGUE_TELLS_MAX]:
            listener = b if speaker_id == a.id else a
            listener.memory.add(MemoryItem(
                minute=now, importance=4, kind="conversation",
                text=texts.T("told_me", teller=speaker_id, text=tell)))
        promise = self._promise_from(plan, world, turns, now)
        if promise is not None:
            beats.append(promise)
        storm = next((t for t in turns if t.get("storm_off")), None)
        if storm is not None:
            x = a if storm.get("speaker_id") == a.id else b
            y = b if x is a else a
            x.memory.add(MemoryItem(minute=now, importance=5, kind="conversation",
                                    text=texts.T("stormed_off_self", other=y.id, loc=plan.location)))
            y.memory.add(MemoryItem(minute=now, importance=5, kind="conversation",
                                    text=texts.T("stormed_off_other", other=x.id, loc=plan.location)))
            for p_, q_ in ((x, y), (y, x)):
                mood_mod.nudge(p_, -pacing.MOOD_STORM_OFF, texts.T("note_storm", other=q_.id))
                r = p_.rel(q_.id)
                r.friendship -= pacing.DIALOGUE_STORM_OFF_FRIENDSHIP
                r.trust -= pacing.DIALOGUE_STORM_OFF_TRUST
                r.clamp()
            beats.append({"verb": "stormed_off", "actor": x.id, "target": y.id, "location": plan.location})
        return beats

    @staticmethod
    def _place_id(world: World, raw: object) -> str:
        """A place named in a line -> its location id ("" when it names nowhere we know)."""
        name = str(raw or "").strip().lower()
        if not name:
            return ""
        for lid, loc in world.locations.items():
            if name in {lid.lower(), (loc.name or "").lower(), (loc.name_zh or "").lower()}:
                return lid
        for lid, loc in world.locations.items():   # "the cafe" / a shortened zh name
            for full in ((loc.name or "").lower(), (loc.name_zh or "").lower()):
                if len(name) >= 2 and full and (name in full or full in name):
                    return lid
        return ""

    def _promise_from(self, plan: ConvPlan, world: World, turns: list, now: int) -> dict | None:
        """A promise needs both voices: one side proposes a concrete meeting and the
        other names the same place later. The last voiced day/hour wins. It becomes
        a real appointment on the existing meetup machinery, flagged ``promised`` so
        keeping or missing it is remembered."""
        voiced = [t for t in turns if t.get("plan")]
        if len({t.get("speaker_id") for t in voiced}) < 2:
            return None
        last = voiced[-1]
        earlier = next((t for t in voiced if t.get("speaker_id") != last.get("speaker_id")), None)
        loc = self._place_id(world, last["plan"].get("place"))
        if not loc or earlier is None or self._place_id(world, earlier["plan"].get("place")) != loc:
            return None
        try:
            hour = max(8, min(21, int(last["plan"].get("hour", 18))))
        except (TypeError, ValueError):
            hour = 18
        when = "tomorrow" if str(last["plan"].get("day", "")).strip().lower() == "tomorrow" else "today"
        day0 = (now // (24 * 60)) * (24 * 60)
        minute = day0 + (24 * 60 if when == "tomorrow" else 0) + hour * 60
        a, b = plan.a, plan.b
        if minute < now + 60 or a.state.pending_meetup or b.state.pending_meetup:
            return None
        appt = {"location": loc, "minute": minute, "promised": True}
        a.state.pending_meetup = {**appt, "partner": b.id}
        b.state.pending_meetup = {**appt, "partner": a.id}
        day = now // (24 * 60)
        a.state.meetup_with_day[b.id] = day
        b.state.meetup_with_day[a.id] = day
        for x, y in ((a, b), (b, a)):
            x.memory.add(MemoryItem(
                minute=now, importance=5, kind="conversation", tags=["promise"],
                text=texts.T("promise_made", other=y.id, loc=loc, when=texts.T("when_" + when), hour=hour)))
        return {"verb": "promise_made", "actor": a.id, "target": b.id, "location": loc, "at": minute}

    # ---- romance (an independent track; see romance.py) --------------

    @staticmethod
    def _romantic_setting(world: World, location_id: str, now: int) -> bool:
        """A festival anywhere, or the park after dark -- settings that amplify romance."""
        if world.effect_active("festival"):
            return True
        loc = world.locations.get(location_id)
        if loc is None or loc.kind != "park":
            return False
        h = (now % (24 * 60)) // 60
        return h >= 19 or h < 6

    @staticmethod
    def _set_pair_stage(a: Agent, b: Agent, stage: str, now: int) -> None:
        for x, y in ((a, b), (b, a)):
            r = x.rel(y.id)
            r.romance_stage = stage
            r.romance_stage_minute = now

    def _grow_romance(self, a: Agent, b: Agent, world: World, sentiment: float,
                      confided: bool, now: int) -> None:
        """Warm exchanges between people already friends nudge romance up (symmetric),
        independent of the friendship number itself. A side crossing the crush line
        quietly enters 'crushing'."""
        if not romance_mod.eligible_pair(a, b):
            return
        rel_ab, rel_ba = a.rel(b.id), b.rel(a.id)
        if not (sentiment >= romance_mod.GROW_SENTIMENT_MIN or confided):
            return
        if rel_ab.friendship < romance_mod.GROW_FRIEND_MIN or rel_ba.friendship < romance_mod.GROW_FRIEND_MIN:
            return
        setting = self._romantic_setting(world, a.state.location, now)
        base = romance_mod.growth(a, b, sentiment, setting, confided)
        if base <= 0:
            return
        # Directional: each side's gain is scaled by their own orientation toward the
        # other's gender, so an asymmetric attraction is expressible.
        rel_ab.romance = min(100.0, rel_ab.romance + base * romance_mod.orientation_coeff(a, b))
        rel_ba.romance = min(100.0, rel_ba.romance + base * romance_mod.orientation_coeff(b, a))
        self._maybe_crush(a, b, now)
        self._maybe_crush(b, a, now)

    def _maybe_crush(self, x: Agent, y: Agent, now: int) -> None:
        """x's romance for y past the crush line -> the pair enters 'crushing' (once)
        and x starts quietly keeping the feeling -- a real secret (about=y) that can
        be confided, leaked into a rumor, and find its way back to y."""
        rel = x.rel(y.id)
        if rel.romance < romance_mod.CRUSH or rel.romance_stage != "none":
            return
        self._set_pair_stage(x, y, "crushing", now)
        if not any(s.about == y.id and not s.resolved and romance_mod.is_crush_text(s.text)
                   for s in self.secrets.secrets_of(x.id)):
            s = self.secrets.add(x.id, romance_mod.crush_secret_text(y.id), 0.7, now)
            s.about = y.id

    def _settle_confession(self, world: World, conf: dict, now: int) -> dict:
        """Apply a confession's rule-decided outcome. Accept -> the pair starts dating;
        reject -> the confessor's romance takes a hit, a sour mood, and a week of
        awkwardness dampening their talk rate. Returns a chronicle descriptor."""
        a = world.agents[conf["from"]]
        b = world.agents[conf["to"]]
        a.state.pending_confession = ""
        day = now // (24 * 60)
        if conf["accepted"]:
            self._set_pair_stage(a, b, "dating", now)
            a.rel(b.id).romance = max(a.rel(b.id).romance, romance_mod.CONFESS_ROMANCE)
            a.memory.add(MemoryItem(minute=now, importance=8, kind="reflection",
                text=texts.T("confessed_yes", other=b.id)))
            b.memory.add(MemoryItem(minute=now, importance=8, kind="reflection",
                text=texts.T("confessed_to_me_yes", other=a.id)))
            return {"verb": "romance_dating", "a": a.id, "b": b.id}
        rel = a.rel(b.id)
        rel.romance = max(0.0, rel.romance - romance_mod.REJECT_ROMANCE_HIT)
        a.state.mood = "upset"
        a.state.awkward_until[b.id] = day + romance_mod.AWKWARD_DAYS
        b.state.awkward_until[a.id] = day + romance_mod.AWKWARD_DAYS
        a.memory.add(MemoryItem(minute=now, importance=7, kind="reflection",
            text=texts.T("confessed_no", other=b.id)))
        b.memory.add(MemoryItem(minute=now, importance=8, kind="reflection",
            text=texts.T("confessed_to_me_no", other=a.id)))
        return {"verb": "romance_rejected", "a": a.id, "b": b.id}

    def _detect_milestone(self, a: Agent, b: Agent, f_before: float, now: int) -> dict | None:
        """A conversation that moves ``a``'s friendship for ``b`` across a stage
        boundary (stranger/acquaintance/friend/close) is a visible beat. Debounced
        to at most one milestone per pair per 3 sim-days so a pair hovering on a
        threshold doesn't spam. Symmetric: the debounce day is stamped on both."""
        s_before = transitions_mod.rel_stage(f_before)
        s_after = transitions_mod.rel_stage(a.rel(b.id).friendship)
        if s_after == s_before:
            return None
        day = now // (24 * 60)
        last = max(a.state.rel_stage_day.get(b.id, -100), b.state.rel_stage_day.get(a.id, -100))
        if day - last < 3:
            return None
        a.state.rel_stage_day[b.id] = day
        b.state.rel_stage_day[a.id] = day
        up = transitions_mod.stage_rank(s_after) > transitions_mod.stage_rank(s_before)
        return {"a": a.id, "b": b.id, "stage": s_after, "up": up,
                "fa": round(a.rel(b.id).friendship), "fb": round(b.rel(a.id).friendship)}

    def _settle_confrontation(
        self, a: Agent, b: Agent, world: World, rumor_id: str, parsed: dict, now: int
    ) -> dict:
        """Confrontation consequences (pure Python math -- the LLM only emits the
        admit/deny verdict). ``a`` confronted ``b`` about ``rumor_id``. Whatever
        the answer, the rumor is now settled and stops spreading."""
        admitted = bool(parsed.get("admitted", False))
        outcome = "admitted" if admitted else "denied"
        rumor = self.rumors.rumors.get(rumor_id)
        self.rumors.resolve(rumor_id, now, outcome)   # the propagation terminus

        # Truth is out: customers who were shunning the subject's shop drift back.
        subj = rumor.subject if rumor else ""
        if subj:
            shops = {lid for lid, loc in world.locations.items() if loc.owner == subj}
            for ag in world.agents.values():
                if ag.state.avoid_location in shops:
                    ag.state.avoid_location = ""

        # Betrayal: this rumor came from a secret ``a`` confided in ``b``. That is far
        # worse than idle gossip -- the owner is certain and the trust math bites hard.
        secret = self.secrets.secrets.get(rumor.from_secret_id) if rumor else None
        is_betrayal = bool(secret and secret.leaked_by == b.id)

        rel_ab = a.rel(b.id)
        if is_betrayal:
            rel_ab.trust -= 20
            rel_ab.friendship -= 12
            rel_ab.conflict += 15
            rel_ba = b.rel(a.id)
            rel_ba.conflict += 6          # the shame of being caught betraying a confidence
            rel_ba.clamp()
        elif admitted:
            rel_ab.trust -= 12
            rel_ab.conflict += 10
            rel_ab.friendship -= 6
            rel_ba = b.rel(a.id)
            rel_ba.conflict += 5          # the sting of being caught out
            rel_ba.clamp()
        else:
            rel_ab.trust -= 5
            rel_ab.conflict += 4          # lingering half-belief
        rel_ab.clamp()
        a.state.mood = "neutral"          # matter settled, whatever the outcome -- mood lands

        if is_betrayal:
            a.memory.add(MemoryItem(
                minute=now, importance=9, kind="conversation",
                text=texts.T("confront_leak_self", other=b.id),
            ))
            b.memory.add(MemoryItem(
                minute=now, importance=8, kind="conversation",
                text=texts.T("confront_leak_other", other=a.id),
            ))
        else:
            a.memory.add(MemoryItem(
                minute=now, importance=6, kind="conversation",
                text=texts.T("confront_rumor_admitted" if admitted else "confront_rumor_denied", other=b.id),
            ))
            b.memory.add(MemoryItem(
                minute=now, importance=6, kind="conversation",
                text=texts.T("confronted_rumor_admitted" if admitted else "confronted_rumor_denied",
                             other=a.id),
            ))
        return {"rumor_id": rumor_id, "outcome": outcome, "admitted": admitted, "betrayal": is_betrayal}

    # ---- semantic memory (beliefs) -----------------------------------

    @staticmethod
    def _nearby_landmark(world: World, location_id: str, now: int) -> str | None:
        """A one-line environment note for a conversation happening where a landmark
        stands (e.g. ``"the mural, 70% complete"``) -- real models weave the setting
        into the talk on their own. A finished one is described by its age (see
        texts.aged_name): "now complete" forever read as news weeks later."""
        loc = world.locations.get(location_id)
        if loc is None or not loc.landmarks:
            return None
        lm = loc.landmarks[0]
        name = texts.landmark_name(lm)
        if lm.get("state") == "completed":
            done = lm.get("completed_minute")
            age = (now - done) // (24 * 60) if isinstance(done, int) else None
            return texts.aged_name(name, age, pacing.FRESH_DAYS)
        return texts.T("aged_progress", name=name, pct=int(lm["progress"] * 100))

    @staticmethod
    def _impression_of(agent: Agent, other_id: str) -> str | None:
        """The agent's lasting impression of ``other_id`` -- but only once it's
        confident enough to matter (else dialogue/trust stay uncoloured). A belief
        down-weighted by a chapter closure needs proportionally more confidence."""
        b = agent.semantic.about(other_id)
        return b.text if b is not None and b.confidence * b.weight >= BELIEF_CONTEXT_MIN else None

    @staticmethod
    def _resolve_subject(agent: Agent, world: World, raw: object) -> tuple[str, str] | None:
        """Map a belief's subject NAME (from the model) back to an id. Returns
        (id, display_name) or None when it matches nobody/nowhere (then dropped)."""
        n = str(raw or "").strip().lower()
        if not n:
            return None
        # Beliefs are now English-canonical, so the model names people by their
        # pinyin (== the agent id); still accept the zh name for older payloads.
        if n in ("self", agent.name.lower(), agent.id):
            return ("self", agent.name)
        for a in world.agents.values():
            if n in (a.name.lower(), a.id):
                return (a.id, a.name)
        for loc in world.locations.values():
            if n in (loc.name.lower(), loc.id, (loc.name_zh or "").lower()):
                return (loc.id, loc.name)
        return None

    def _form_beliefs(self, agent: Agent, world: World, raw: object, now: int) -> list[dict]:
        """Merge the reflection's proposed beliefs into the agent's semantic
        memory (pure Python). Same subject -> reinforce (confidence up, source
        count up, new wording wins); new subject -> add, capped at 0.6. Returns
        one descriptor per formed/reinforced belief for the engine to publish."""
        events: list[dict] = []
        if not isinstance(raw, list):
            return events
        for rb in raw[:2]:                                # at most 2 per reflection
            if not isinstance(rb, dict):
                continue
            text = str(rb.get("text", "")).strip()
            if not belief_text_ok(text, world):           # quality gate: no "ok"/filler impressions
                if text:
                    print(f'[belief] rejected low-quality: "{text}"', flush=True)
                continue
            resolved = self._resolve_subject(agent, world, rb.get("subject", ""))
            if resolved is None:                          # unmatched subject -> drop
                continue
            sid, sname = resolved
            try:
                conf = max(0.0, min(1.0, float(rb.get("confidence", 0.4))))
            except (TypeError, ValueError):
                conf = 0.4
            try:
                sent = max(-1.0, min(1.0, float(rb.get("sentiment", 0.0))))
            except (TypeError, ValueError):
                sent = 0.0

            existing = agent.semantic.about(sid)
            if existing is not None:                      # reinforce the same slot
                existing.text = text
                existing.confidence = min(1.0, existing.confidence * 0.7 + conf * 0.5)
                existing.sentiment = sent
                existing.source_count += 1
                existing.last_reinforced_minute = now
                final_conf = existing.confidence
            else:                                         # brand-new impression -> cap at 0.6
                final_conf = min(0.6, conf)
                agent.semantic.beliefs.append(Belief(
                    subject=sid, text=text, confidence=final_conf, sentiment=sent,
                    formed_minute=now, last_reinforced_minute=now, source_count=1,
                ))
                agent.semantic._prune()
            events.append({"subject_id": sid, "subject_name": sname, "text": text,
                           "confidence": round(final_conf, 2)})
        return events

    # ---- Level 3: reflection -----------------------------------------

    def should_reflect(self, agent: Agent) -> bool:
        """Cheap synchronous gate (run in the tick): has enough weight accumulated?
        Individualized -- quiet background characters carry a higher threshold, so
        they reflect less often and spend less smart-tier without being silenced."""
        return agent.memory.importance_since_reflection >= agent.profile.reflection_threshold

    async def reflect(
        self, agent: Agent, world: World, now: int
    ) -> tuple[list[str], list[dict], bool, list[dict]]:
        """The reflection body -- run in a background task (it's a slow smart-tier
        call). Does NOT reset ``importance_since_reflection``; the engine resets it at
        initiation so a second reflection can't pile up while this one generates."""
        day_start = now - (now % (24 * 60))
        events = _resolve_mems(agent.memory.today(day_start), world)
        # The agent's own still-open worries ride into the reflection so it can judge
        # which, if any, recent experience has laid to rest.
        open_secrets = self.secrets.active_secrets_of(agent.id)
        # Life decisions (a transition, a confession, a proposal) are offered only
        # when nothing is already staged -- reflection can't queue two at once.
        offer = []
        if not agent.state.pending_transition and not agent.state.pending_confession:
            if now // (24 * 60) - agent.state.last_transition_day >= transitions_mod.TRANSITION_COOLDOWN_DAYS:
                offer += [(t.id, t.label_text()) for t in transitions_mod.available_for(agent, world)]
            offer += self._romance_options(agent, world, now)
        res = await self.router.generate(
            task="reflection",
            messages=builders.reflection_prompt(agent, events, open_secrets, offer or None),
            agent_id=agent.id,
            sim_minute=now,
            schema={"type": "object"},
            max_tokens=300 if builders.lang_is_zh() else 200,
            # Mechanical backstop behind the roster: an insight/belief/secret that
            # names one resident and mis-genders them is rejected, so the chain
            # retries rather than writing it into memory forever.
            validate=lambda r: (_note_zh_gate(reflection_zh_ok(r.parsed))
                                and _note_gender_gate(reflection_gender_ok(r.parsed))),
            # belief/secret/life_decision quality must never be a canned mock line:
            # a total chain failure raises ProvidersExhausted so the engine can brew a
            # pause and retry rather than settle for the floor (task 3).
            no_floor=True,
        )
        insights: list[str] = []
        belief_events: list[dict] = []
        secret_born = False
        romance_events: list[dict] = []
        if isinstance(res.parsed, dict):
            # Insights land straight in memory, so gate them like beliefs -- a dodge
            # ("ok") or filler must never become a remembered reflection. (The router
            # already rejects a junk reflection; this is the belt-and-suspenders for a
            # floor result that slipped through.)
            insights = [str(x) for x in res.parsed.get("insights", []) if belief_text_ok(str(x), world)]
            belief_events = self._form_beliefs(agent, world, res.parsed.get("beliefs", []), now)
            secret_born = self._maybe_new_secret(agent, world, res.parsed.get("new_secret"), now)
            self._resolve_reflected_secrets(agent, res.parsed.get("resolved_secret_ids"), open_secrets, now)
            if offer:
                romance_events = self._stage_life_decision(agent, world, res.parsed.get("life_decision"), now)
        for ins in insights:
            agent.memory.add(MemoryItem(minute=now, text=ins, importance=5, kind="reflection"))
        return insights, belief_events, secret_born, romance_events

    def _resolve_reflected_secrets(self, agent: Agent, raw: object, open_secrets: list, now: int) -> None:
        """Resolve the secrets reflection judged settled. Only ids that were actually
        offered to this reflection (the agent's own open secrets) are honoured, so a
        hallucinated id can't touch someone else's secret."""
        if not isinstance(raw, list):
            return
        by_id = {s.id: s for s in open_secrets}
        for sid in raw:
            secret = by_id.get(str(sid))
            if secret is not None:
                self._resolve_secret(
                    agent, secret, now,
                    f"{agent.id.capitalize()} has come to terms with this; it no longer weighs on them.")

    def _stage_life_decision(self, agent: Agent, world: World, raw: object, now: int) -> list[dict]:
        """Route a reflection's life decision: a transition (staged for the next day
        boundary), a confession (armed for the next solo talk), or a proposal (settled
        now). Returns chronicle descriptors for anything settled immediately."""
        if not isinstance(raw, dict):
            return []
        tid = str(raw.get("action", "")).strip()
        reason = str(raw.get("reason", "")).strip()
        tmpl = transitions_mod.REGISTRY.get(tid)
        if tmpl is not None:
            if agent.state.pending_transition or not tmpl.precondition(agent, world):
                return []
            agent.state.pending_transition = tid
            agent.state.pending_transition_reason = reason
            print(f"[transition] {agent.id} decided '{tid}' (applies next settlement)", flush=True)
            return []
        if tid.startswith("confess_to_"):
            target = world.agents.get(tid[len("confess_to_"):])
            if target is None or agent.state.pending_confession:
                return []
            rel = agent.rel(target.id)
            if rel.romance < romance_mod.CONFESS_ROMANCE or rel.friendship < romance_mod.CONFESS_FRIEND:
                return []
            agent.state.pending_confession = target.id
            agent.state.last_confess_day = now // (24 * 60)
            print(f"[romance] {agent.id} resolved to confess to {target.id}", flush=True)
            return []
        if tid.startswith("propose_to_"):
            return self._settle_proposal(agent, world.agents.get(tid[len("propose_to_"):]), now)
        return []

    def _romance_options(self, agent: Agent, world: World, now: int) -> list[tuple[str, str]]:
        """Life-decision options on the romance track: confess (own romance & friendship
        high enough, off cooldown) or propose (dating long enough)."""
        opts: list[tuple[str, str]] = []
        day = now // (24 * 60)
        can_confess = (not agent.state.pending_confession
                       and day - agent.state.last_confess_day >= romance_mod.CONFESS_COOLDOWN_DAYS)
        for other in world.agents.values():
            if other.id == agent.id or not romance_mod.eligible_pair(agent, other):
                continue
            rel = agent.rel(other.id)
            if (can_confess and rel.romance_stage in ("none", "crushing")
                    and rel.romance >= romance_mod.CONFESS_ROMANCE
                    and rel.friendship >= romance_mod.CONFESS_FRIEND):
                opts.append((f"confess_to_{other.id}", f"confess your feelings to {other.id.capitalize()}"))
            elif (rel.romance_stage == "dating"
                    and day - rel.romance_stage_minute // (24 * 60) >= romance_mod.DATING_TO_PARTNER_DAYS):
                opts.append((f"propose_to_{other.id}", f"ask {other.id.capitalize()} to make it permanent"))
        return opts

    def _settle_proposal(self, a: Agent, b: Agent | None, now: int) -> list[dict]:
        """A proposal is accepted when the other side is deep enough in (romance >= 70)
        and they've been dating long enough. Success -> partners + a small weekend
        together-time tweak."""
        if b is None or a.rel(b.id).romance_stage != "dating":
            return []
        if b.rel(a.id).romance < romance_mod.PARTNER_ROMANCE:
            return []
        self._set_pair_stage(a, b, "partners", now)
        for x, y in ((a, b), (b, a)):
            x.memory.add(MemoryItem(minute=now, importance=8, kind="reflection",
                text=texts.T("partners", other=y.id)))
        self._partner_routine_tweak(a, b)
        return [{"verb": "romance_partners", "a": a.id, "b": b.id}]

    @staticmethod
    def _partner_routine_tweak(a: Agent, b: Agent) -> None:
        """Partners drift together in the evenings: add a shared weekend park hour to
        both (once)."""
        from .routine import Routine, RoutineEntry
        for x in (a, b):
            we = list(x.routine._weekend)
            if any(e.start == 20 * 60 + 30 and e.location == "park" for e in we):
                continue
            we.append(RoutineEntry(20 * 60 + 30, "rest", "park"))
            x.routine = Routine(list(x.routine.entries), we)

    def _maybe_ignite(self, agent: Agent, world: World, now: int) -> None:
        """Organic spark: once a pair has spent enough waking time together and both
        are open to romance, a low chance drops a 'you keep noticing them' nudge into
        the agent's memory -- material for the next reflection and warmer talks.
        Deterministic per (pair, day); one spark per reflection; 14-day cooldown."""
        if agent.profile.romantic_inclination < romance_mod.IGNITE_INCL_MIN:
            return
        day = now // (24 * 60)
        for other in world.agents.values():
            if (other.id == agent.id or not romance_mod.eligible_pair(agent, other)
                    or other.profile.romantic_inclination < romance_mod.IGNITE_INCL_MIN):
                continue
            rel = agent.rel(other.id)
            if rel.romance_stage != "none":
                continue
            if agent.state.copresence.get(other.id, 0) < romance_mod.IGNITE_MINUTES:
                continue
            if day - agent.state.ignite_day.get(other.id, -100) < romance_mod.IGNITE_COOLDOWN_DAYS:
                continue
            seed = int(hashlib.sha256(f"ignite|{agent.id}|{other.id}|{day}".encode()).hexdigest()[:8], 16)
            # The spark is likelier toward a gender this agent leans to (orientation).
            if random.Random(seed).random() >= romance_mod.IGNITE_PROB * romance_mod.orientation_coeff(agent, other):
                continue
            agent.state.ignite_day[other.id] = day
            # The spark itself: time-together turns into the first flicker of romance,
            # independent of how deep the friendship is. Conversations grow it from here.
            rel.romance = min(100.0, rel.romance + romance_mod.IGNITE_ROMANCE_KICK)
            self._maybe_crush(agent, other, now)
            agent.memory.add(MemoryItem(
                minute=now, importance=4, kind="reflection",
                text=texts.T("noticing", other=other.id)))
            return

    def _maybe_new_secret(self, agent: Agent, world: World, raw: object, now: int) -> bool:
        """Add a private matter surfaced by reflection (owner = the reflecting
        agent). Capped at 4 per agent so it can't run away. Returns True if one was
        born (the engine then publishes a content-free ``secret_born`` beat)."""
        if not isinstance(raw, dict):
            return False
        text = str(raw.get("text", "")).strip()
        if not belief_text_ok(text, world):               # same quality gate as beliefs
            if text:
                print(f'[secret] rejected low-quality: "{text}"', flush=True)
            return False
        if len(self.secrets.secrets_of(agent.id)) >= 4:
            return False
        try:
            sensitivity = float(raw.get("sensitivity", 0.5))
        except (TypeError, ValueError):
            sensitivity = 0.5
        self.secrets.add(agent.id, text, sensitivity, now)
        return True
