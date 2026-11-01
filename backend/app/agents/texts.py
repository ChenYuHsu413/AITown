"""World text in the town's own language.

Two jobs, both keyed by the generation language (``builders.lang_is_zh()``):

1. **The rule-layer text catalogue.** Every memory, narrative and fallback line the
   rules write lives here once, in English and in Traditional Chinese. A zh-native
   town writes Chinese at the source -- nothing is translated afterwards -- while an
   English run (and every English test) gets byte-for-byte the text it always had.
   Placeholders ({agent:id}, {loc:id}, {landmark:id}) are structure, not language,
   and stay as they are in both.

2. **Language-aware word matching.** The rules match text by shared words (a theme,
   a worry, a goal). Chinese has no spaces, so ``text.split()`` sees a whole
   sentence as one word and every such match silently stops firing. ``word_set``
   keeps the old English behaviour exactly and adds CJK character bigrams (minus
   bigrams built on function characters), so the same ">= 2 shared words" rules
   work in both languages.
"""

from __future__ import annotations

import re

from ..llm.prompts import builders

_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_CJK_RUN = re.compile(r"[㐀-䶿一-鿿豈-﫿]+")
# Characters that carry grammar rather than topic: a bigram touching one says
# nothing about what a sentence is about ("我們", "的光", "了一").
_ZH_FUNCTION = set("的了是我你妳他她它們在和與跟也都就很不沒有這那個一著把被讓給對要會說想得地嗎呢吧啊呀喔哦嗯又還再才已經更最而且但或所以因為如果自己什麼怎麼")


def zh_native() -> bool:
    return builders.lang_is_zh()


def has_cjk(text: str) -> bool:
    return bool(_CJK.search(text or ""))


def cjk_bigrams(text: str) -> set[str]:
    out: set[str] = set()
    for run in _CJK_RUN.findall(text or ""):
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if not (_ZH_FUNCTION & set(bg)):
                out.add(bg)
    return out


def word_set(text: str, strip: str = ".,;:!?'\"") -> set[str]:
    """Lower-case words of ``text`` (split on whitespace, ``strip`` trimmed -- exactly
    the old English matching) plus its CJK topic bigrams."""
    words = {w.strip(strip).lower() for w in (text or "").split()}
    return words | cjk_bigrams(text)


_PLACEHOLDER = re.compile(r"\{(?:agent|loc|landmark):([^}]+)\}")


def tokens(text: str) -> set[str]:
    """Search tokens for Chinese (or mixed) text: placeholder ids, latin words, CJK
    topic bigrams, and the id of every resident named by their zh name -- so "安哥",
    "{agent:ange}" and "Ange" all meet on "ange". (A one-character or bopomofo name
    like 雪 / ㄐㄐ yields no bigram; the roster pass is what makes it findable.)
    Callers use it only for text with CJK in it; pure English keeps its old matching."""
    t = text or ""
    out = {m.lower() for m in _PLACEHOLDER.findall(t)}
    rest = _PLACEHOLDER.sub(" ", t)
    out |= set(re.findall(r"[a-z0-9]{2,}", rest.lower()))
    out |= cjk_bigrams(rest)
    for en, zh in builders.roster_pairs():
        if zh and zh in rest:
            out.add(en.lower())
    return out


def name_query(agent_id: str) -> str:
    """A memory query for "things about this person": the capitalized pinyin, as it
    always was in English; the zh name beside it in a zh-native town."""
    q = (agent_id or "").capitalize()
    return f"{q} {display_name(agent_id)}" if zh_native() else q


def mentions(text: str, agent_id: str) -> bool:
    """Does ``text`` name this resident -- by id/pinyin, placeholder, or zh name?"""
    if not agent_id:
        return False
    t = text or ""
    if re.search(rf"\b{re.escape(agent_id)}\b", t, re.I):
        return True
    zh = display_name(agent_id)
    return bool(zh) and zh != agent_id and zh in t


def display_name(agent_id: str) -> str:
    """The name a resident is written with in this language: the zh name in a
    zh-native town, the capitalized pinyin otherwise."""
    if zh_native():
        for en, zh in builders.roster_pairs():
            if en.lower() == (agent_id or "").lower() and zh:
                return zh
    return (agent_id or "").capitalize()


# ---- the catalogue ------------------------------------------------------------
# key -> (English, Traditional Chinese). str.format fields; doubled braces are the
# literal placeholders the frontend resolves.

_CATALOGUE: dict[str, tuple[str, str]] = {
    # decision.py -- daily life
    "closed_reroute": ("{{loc:{shop}}} was closed today, went to {{loc:{went_to}}} instead.",
                       "{{loc:{shop}}}今天沒開,改去了{{loc:{went_to}}}。"),
    "saw_working": ("Saw {{agent:{worker}}} working on {{landmark:{lid}}} at {{loc:{loc}}}.",
                    "看到{{agent:{worker}}}在{{loc:{loc}}}忙著{{landmark:{lid}}}。"),
    "landmark_progress": ("{{landmark:{lid}}} at {{loc:{loc}}} has come along since I last saw it.",
                          "{{loc:{loc}}}的{{landmark:{lid}}}比我上次看到時又有進展了。"),
    "talked_with": ("Talked with {{agent:{other}}} at {{loc:{loc}}}.",
                    "在{{loc:{loc}}}和{{agent:{other}}}聊了聊。"),
    # rumors & secrets
    "rumor_about_me": ("{{agent:{teller}}} told me people are saying: {text}",
                       "{{agent:{teller}}}跟我說,大家都在傳:{text}"),
    "heard_from": ("Heard from {{agent:{teller}}}: {text}", "聽{{agent:{teller}}}說:{text}"),
    "confided": ("{{agent:{owner}}} confided in me: {text}", "{{agent:{owner}}}私下跟我說了心事:{text}"),
    "leak_fallback": ("{owner} has been hiding something.", "{owner}好像在隱瞞什麼。"),
    "confront_opener": ("I heard people are saying: {heard}. Did this come from you?",
                        "我聽說大家在傳:{heard}。這是從你那裡傳出來的嗎?"),
    "goal_forward": ("That old worry is behind me now -- my focus is on the next chapter: {new}.",
                     "那件舊心事已經過去了,我現在的心思放在下一步:{new}。"),
    "goal_forward_teach": ("Keep learning {what} from {who}", "繼續跟{who}學{what}"),
    "goal_forward_generic": ("Keep building on things with {who}", "繼續和{who}把關係經營下去"),
    "secret_acted_on": ("{owner} acted on it and moved forward.", "{owner}付諸行動,往前走了。"),
    "secret_landmark_done": ("{owner} finished it -- the worry never came true.",
                             "{owner}把它完成了,擔心的事終究沒有發生。"),
    # confrontations
    "confront_leak_self": ("Confronted {{agent:{other}}} for leaking the secret I trusted them with. Betrayed.",
                           "我當面質問{{agent:{other}}},為什麼把我託付的秘密說出去。我被背叛了。"),
    "confront_leak_other": ("{{agent:{other}}} confronted me for leaking their secret.",
                            "{{agent:{other}}}當面質問我,說我洩漏了託付給我的秘密。"),
    "confront_rumor_admitted": ("Confronted {{agent:{other}}} about the rumor. They admitted it.",
                                "我為了那個傳言當面質問{{agent:{other}}}。對方承認了。"),
    "confront_rumor_denied": ("Confronted {{agent:{other}}} about the rumor. They denied it.",
                              "我為了那個傳言當面質問{{agent:{other}}}。對方否認了。"),
    "confronted_rumor_admitted": ("{{agent:{other}}} confronted me about the rumor I started.",
                                  "{{agent:{other}}}當面質問我那個由我起頭的傳言。"),
    "confronted_rumor_denied": ("{{agent:{other}}} confronted me about the rumor I was accused of starting.",
                                "{{agent:{other}}}當面質問我,說那個傳言是我起的頭。"),
    "confronted_detail": ('About the rumor "{rumor}" -> {{agent:{who}}} {outcome}',
                          "關於傳言「{rumor}」:{{agent:{who}}}{outcome}"),
    "confronted_detail_bare": ("{{agent:{who}}} {outcome}", "{{agent:{who}}}{outcome}"),
    "outcome_admitted": ("admitted it", "承認了"),
    "outcome_denied": ("denied it", "否認了"),
    # romance
    "confessed_yes": ("I confessed to {{agent:{other}}} -- and we're together now.",
                      "我向{{agent:{other}}}告白了,我們現在在一起了。"),
    "confessed_to_me_yes": ("{{agent:{other}}} confessed to me, and I said yes.",
                            "{{agent:{other}}}向我告白,我答應了。"),
    "confessed_no": ("I confessed to {{agent:{other}}}, but the feeling wasn't mutual.",
                     "我向{{agent:{other}}}告白了,但對方沒有同樣的感覺。"),
    "confessed_to_me_no": ("{{agent:{other}}} confessed to me; I had to turn them down as gently as I could.",
                           "{{agent:{other}}}向我告白,我只能盡量溫柔地拒絕。"),
    "partners": ("{{agent:{other}}} and I decided to make it permanent.", "我和{{agent:{other}}}決定要長長久久。"),
    "noticing": ("Lately I keep noticing {{agent:{other}}}'s presence more than I'd expect.",
                 "最近我總是不自覺地留意起{{agent:{other}}}。"),
    "crush_secret": ("I think I'm developing feelings for {name}.", "我覺得我好像喜歡上{name}了。"),
    # relationships
    "grew_closer": ("I grew closer to {{agent:{other}}}.", "我和{{agent:{other}}}變得更親近了。"),
    "drifted_apart": ("I drifted apart from {{agent:{other}}}.", "我和{{agent:{other}}}漸漸疏遠了。"),
    # engine.py -- economy, repairs, transitions
    "revenue_today": ("{{loc:{loc}}} made ${amount} today.", "{{loc:{loc}}}今天賺了 ${amount}。"),
    "revenue_week": ("This week {{loc:{loc}}} made ${amount}.", "{{loc:{loc}}}這週賺了 ${amount}。"),
    "repaired_owner": ("{{agent:{tech}}} fixed the equipment at {{loc:{loc}}}. Cost me $15, but at least it works again.",
                       "{{agent:{tech}}}修好了{{loc:{loc}}}的設備。花了我 $15,但至少又能用了。"),
    "repaired_tech": ("Fixed the equipment at {{loc:{loc}}} -- whoever installed it did a sloppy job.",
                      "修好了{{loc:{loc}}}的設備,當初安裝的人做得很馬虎。"),
    "transition_self": ("I made a real change today: I {what}.", "我今天做了一個真正的改變:我{what}。"),
    "transition_heard": ("Heard {{agent:{who}}} {what}.", "聽說{{agent:{who}}}{what}。"),
    # world.py
    "landmark_finished_self": ("I finished {{landmark:{lid}}} at {{loc:{loc}}}. It's done at last.",
                               "我完成了{{loc:{loc}}}的{{landmark:{lid}}},終於做完了。"),
    "landmark_finished_seen": ("{{landmark:{lid}}} at {{loc:{loc}}} is finished — {{agent:{who}}}'s work. It looks wonderful.",
                               "{{loc:{loc}}}的{{landmark:{lid}}}完成了,是{{agent:{who}}}的作品。看起來真棒。"),
    "ate_at": ("Had a meal at {{loc:{loc}}}.", "在{{loc:{loc}}}吃了一頓飯。"),
    "couldnt_afford": ("Couldn't afford to eat at {{loc:{loc}}}.", "錢不夠,沒辦法在{{loc:{loc}}}吃飯。"),
    # chapters.py
    "ordinary_title": ("Ordinary days", "平常的日子"),
    "ordinary_narrative": ("Living an ordinary stretch of days as a {occupation}; nothing in "
                           "particular is pulling at me right now.",
                           "以{occupation}的身分過著平常的日子,眼下沒有什麼特別牽動我的事。"),
    "interlude_title": ("Between chapters", "章節之間"),
    "bio_completed": ("I set out to {what} -- and I did it. That part of my life is finished now.",
                      "我曾下定決心要{what},而我做到了。人生的這一段已經畫下句點。"),
    "bio_failed": ("I tried to {what}, and it didn't work out. I'm learning to carry that.",
                   "我試著{what},但沒能成功。我正在學著接受這件事。"),
    "bio_abandoned": ("I meant to {what}, but I chose to let it go. That was my call, and I stand by it.",
                      "我原本想{what},但我選擇放下。這是我自己的決定,我不後悔。"),
    "that_chapter": ("that chapter", "那一段"),
    # wishes.py
    "wish_frustration": ("Something I have been quietly working toward got blocked again today.",
                         "我一直默默努力的那件事,今天又卡住了。"),
    "wish_progress": ("I took a small real step toward something I have been keeping to myself.",
                      "我朝著一件放在心裡的事,踏出了實實在在的一小步。"),
    "minor_completed": ("A small thing I had set my mind on is done.", "一件我放在心上的小事,完成了。"),
    "minor_failed": ("A small thing I had set my mind on ran out of time.", "一件我放在心上的小事,時間到了還是沒做成。"),
    "minor_abandoned": ("I have quietly let go of a small thing I had set my mind on.",
                        "我悄悄放下了一件原本放在心上的小事。"),
}

_RESIDUE_NARRATIVE_ZH = {
    "fulfilled": "我剛完成一件對我很重要的事,心裡踏實而滿足;眼下就這樣過著日子,還沒有下一件大事。",
    "relieved": "壓在心上很久的事終於結束了。我鬆了一口氣,也有點空,打算悠閒地過幾天,不做任何計畫。",
    "proud": "我把一件大事堅持到了最後,我為此感到驕傲;下一步要做什麼還沒決定,日子過得鬆散而沒有安排。",
    "wistful": "我人生的一個章節剛剛結束,我總忍不住回頭去看。現在有點漫無目的,還沒準備好開始新的事。",
    "restless": "我剛走過一件大事。手癢著想做下一件事,卻還沒找到,所以比平常更常到處晃、找人聊天。",
    "unmoored": "我圍繞著過日子的那件事沒有成功。我覺得失去了依靠,日子照常過,卻像在漂流,也比平常更想有人陪。",
}


_TALKED_WITH = (re.compile(r"^Talked with \{agent:([^}]+)\}"),
                re.compile(r"^在\{loc:[^}]+\}和\{agent:([^}]+)\}聊了聊"))


def talked_with_partner(text: str) -> str | None:
    """The other resident of a "talked with" memory, in either language, else None."""
    for pat in _TALKED_WITH:
        m = pat.match(text or "")
        if m:
            return m.group(1)
    return None


# Occupations are structural ids (rules compare them, e.g. "job seeker"); this is
# only how one is written in a zh-native town's prose.
_OCCUPATION_ZH = {   # the wording the UI already used (frontend OCCS)
    "cafe owner": "咖啡館老闆", "baker": "烘焙師", "postman": "郵差", "doctor": "醫生",
    "freelance designer": "自由設計師", "student": "學生", "engineer": "工程師",
    "office worker": "上班族", "repair technician": "修理師傅", "retired": "退休",
    "job seeker": "求職中", "cafe staff": "咖啡館員工", "bakery staff": "烘焙坊員工",
    "freelancer": "自由接案者",
}

# Traits are structural too ("gossipy" moves the gossip odds); this is their prose.
# The UI's wording (frontend TRAITS), plus the three it never needed.
_TRAIT_ZH = {
    "curious": "好奇", "friendly": "友善", "gossipy": "八卦", "quiet": "安靜", "loyal": "忠誠",
    "stressed": "焦慮", "calm": "冷靜", "observant": "敏銳", "helpful": "樂於助人",
    "creative": "有創意", "night-owl": "夜貓子", "hardworking": "勤奮", "proud": "自豪",
    "competitive": "好勝", "cheerful": "開朗", "restless": "閒不住", "shy": "害羞",
    "kind": "善良", "private": "內斂", "technical": "技術控", "focused": "專注",
    "easygoing": "隨和", "sociable": "善交際",
    "blunt": "直率", "self-assured": "自信", "abrasive": "說話帶刺",
}


def trait_text(trait: str) -> str:
    return _TRAIT_ZH.get(trait, trait) if zh_native() else trait


def landmark_name(lm: dict) -> str:
    return (lm.get("name_zh") or lm.get("name", "")) if zh_native() else lm.get("name", "")


def occupation_text(occupation: str) -> str:
    if zh_native():
        return _OCCUPATION_ZH.get((occupation or "").lower(), occupation)
    return occupation


def goal_clause(goal: str) -> str:
    """A zh goal as the object of "set out to ...": no leading 我想/我要/我希望,
    no closing full stop."""
    return re.sub(r"^我(?:想要|想|要|希望)", "", (goal or "").strip()).rstrip("。")


def T(key: str, **kw) -> str:
    en, zh = _CATALOGUE[key]
    return (zh if zh_native() else en).format(**kw)


def residue_narrative(residue: str, english: dict[str, str]) -> str:
    """Interlude self-description for a residue -- ``english`` is the caller's own
    table, so the English wording stays where it has always lived."""
    if zh_native():
        return _RESIDUE_NARRATIVE_ZH.get(residue, _RESIDUE_NARRATIVE_ZH["fulfilled"])
    return english.get(residue, english["fulfilled"])
