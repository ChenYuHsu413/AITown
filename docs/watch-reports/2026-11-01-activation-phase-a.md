# 活化大單・階段 A 短報告:逐輪對話上線

- run:`7b977300…`(新紀元);上線 commit `87d9e86`,觀察後修正 `fb07b17`
- 觀察窗:Day 39 19:07 → Day 40 16:23(0.82 個模擬日),之後接著上線 B+D
- 啟動參數:`AI_TOWN_GATE_DIAG=1`、`AI_TOWN_BUDGET_USD=30`(啟動時以環境變數帶入,`.env` 未動)

## 做了什麼

- **一句一次呼叫**:每一句只用說話者自己的脈絡,包括對對方的記憶、最近掛心的事、自己的願望(minor 也是全文)、私事、心情、對對方的看法,加上目前為止的逐字稿。模型可以自己結束對話,上限 8 句。傾訴、對質、告白至少 4 句。
- **路由**:日常對話走 Haiku。以下情況走 Sonnet:傾訴、對質、告白;某個秘密的信任度離傾訴門檻在 5 以內;對話牽涉某一方的願望或追求章節。
- **後果由規則從結構化欄位讀出**:
  - `tells` → 聽者的記憶
  - 雙方都說出同一個見面計畫 → 真的約定(`promise_made`);守約或落空都會留記憶,落空另外發事件
  - `storm_off` → 不歡而散事件,雙方關係下降
  - 雙方各自套用自己的情緒訊號
- **修正案 1**:地標描述按完工時間淡化。今天完成的叫「今天剛完成的…」,7 天內叫「新落成的…」,之後只剩名字。舊快照缺完工時間,一律當成舊的。
- **修正案 2**:平常日子的角色卡不再帶 seed 的 `Standing aim`。掃過所有 prompt builder,只有這一處引用 `profile.goals`,沒有第三個 seed 錨點。
- **修正案 3**:說話者自己的願望(含 minor 全文)進入自己的座位,不會進到對方的座位(有測試)。

## 正式環境數字(0.82 模擬日)

| 項目 | 值 |
|---|---|
| 對話場數 | 15 |
| 每場句數 | 5 句 ×1、6 句 ×4、7 句 ×2、8 句 ×8 |
| `dialogue_turn`(Haiku) | 56 次,$0.0168 |
| `dialogue_turn_tense`(Sonnet) | 51 次,$0.2902 |
| 全部呼叫 | $0.4615 → **約 $0.56 / 模擬日**(預期 $0.5–1) |
| 非 Claude fallback | 0 |
| 承諾成立 | 1(歐拉↔瓏) |
| 傾訴 | 4 |
| 「誰跟我說」記憶 | 35 筆 |
| 光影裝置話題 | 0 場(診斷時公園對話 31.6% 提到它) |

## 觀察到的問題與已做的修正(`fb07b17`)

1. **模型自行告白**:歐拉的座位寫著「you have a crush on them」,他就自己告白了兩次,但沒有任何一場是戀愛規則觸發的告白戲,所以關係狀態沒有動。現在單戀改寫成「心裡有感覺、還沒說、今天也還沒準備說」,告白只在規則給出明確意圖時才發生。
2. **同一件事被重複轉告**:一場對話裡,說話者把同一件事換句話講了好幾次,聽者因此留下 3 筆幾乎一樣的記憶。現在每位說話者每場最多留 1 筆(取最後、通常最完整的一句),上限 2。
3. **對話偏長**:一半的場次跑滿 8 句上限。prompt 加了一句:日常鄰里閒聊通常幾句就會自然結束。
4. **口頭禪每句都冒出來**:歐拉每一句都以「跟你說——」開頭。prompt 加了一句:保留說話風格,但不要每句用同樣的開頭或口頭禪。這條隨 B+D(`74559c9`)一起上線。

## 三場代表性對話(原文)

**D40 07:40 歐拉 × 蒯政(資訊流:歐拉的打算傳到第三人)**
> 歐拉:欸,蒯政,早啊——跟你說,我今天一路從市場跑過來,腦子裡卻一直轉著同一件事,連頭條都念不順了。你有沒有那種話,明明該說出口,卻怎麼也開不了口的時候?
> 蒯政:嗯……有的。有時候不急著說,慢慢想,話自己就找到路了。
> 歐拉:跟你說——你這句話我聽進去了,慢慢想,好像比我一路瞎跑有用。我大概就是想太久了,這幾天一直在想,該不該直接去希希家門前,把話當面講清楚。
> 蒯政:嗯……當面講,誠意是看得見的。你先想好要說什麼,語氣放軟一點,就不怕。
> 歐拉:跟你說——你講得對,誠意要當面才看得見,我就是怕自己一緊張,話又跑成頭條。好,我不再拖了,今天中午前就去希希家門前,先把心事一句一句理好再開口。
> 蒯政:嗯……去吧。慢慢說就好,他會聽得出你的心意。

結果:蒯政留下兩筆「歐拉跟我說:…」的記憶(修正前的重複情形)。

**D40 12:01 ㄐㄐ × 雪(各自帶著自己的心事:咖啡館的困境、雪想辭職)**
> ㄐㄐ:欸,你來得正好!今天店裡有點冷清,我正在想怎麼讓更多上班族知道潮汐咖啡館。你最近都在忙什麼呀?
> 雪:喔…其實也沒什麼特別的,就是上班而已。只是最近工作真的有點累,我一直在想要不要換個地方,但還沒想清楚…不好意思,突然講這些。
> ㄐㄐ:換地方?聽起來你心裡真的在掙扎耶。不用道歉啦,這種事本來就該講出來。……
> 雪:嗯…應該是環境比較悶吧。每天同樣的位子、同樣的會議,人慢慢就被磨掉了。……
> ㄐㄐ:……欸,你平常下班會去哪裡放空?我想問問看,是不是有哪裡的上班族會常去,好讓我們潮汐咖啡館也去那邊晃晃。
> 雪:下班喔…其實我都會去望坡樓附近走走,那邊人不多,可以一個人安靜坐一下。不過你要是想找上班族常去的地方,可能要去渡口市場那邊看看……
> ㄐㄐ:……我其實正想去辦公室走一趟,把我們潮汐咖啡館的名字帶過去,你覺得我該先從哪一層樓開始比較好?
> 雪:欸…你要去辦公室的話,先從人最多的那層樓開始吧……不過…我覺得你要小心一點,我們公司的人都很愛八卦,別讓人覺得你是來推銷的。今天真的謝謝你聽我講這些,我先回去了。

雪的種子心事(想辭職)第一次經由她自己的口,傳到別人那裡。

**D39 19:11 歐拉 × 希希(資訊不對等:兩邊各自只知道自己的心;這也是修正 1 的起點)**
> 歐拉:跟你說——我本來是要去送最後一趟信的,結果腳自己走到這裡來了。我、我有些話憋很久了,想當面跟你說清楚。
> 希希:啊,歐拉……你、你說吧,我在聽。是不是……跟我有關的事?
> 歐拉:跟你說——對,是跟你有關。我這個什麼都愛聊的人,偏偏一面對你就卡住,我對你的在意已經藏不住了,不想再躲著你。
> 希希:你、你是說……在意?我、我沒想到你會直接說出來……我其實也、也一直有點……不是,我是說,謝謝你願意當面告訴我。
> (共 8 句,以希希「今天先讓我回去好好消化一下,明天……我們再慢慢聊,好嗎?」作結)

文字本身很好,但這場告白不是規則觸發的,所以關係狀態沒有任何改變,文本和規則就此對不上。已用修正 1 收回這個口子。

## 新 prompt 全文(目前程式碼)

### 逐輪對話(task `dialogue_turn` / `dialogue_turn_tense`)

情境:ㄐㄐ 在潮汐咖啡館對冷月開口後,輪到冷月(第 2 句)。本例路由:`dialogue_turn_tense`(ㄐㄐ 有一顆以冷月為對象的願望)。

**system(兩個座位共用的模板,名字代入說話者)**

```text
You voice ONE character, 冷月, in a face-to-face conversation with ㄐㄐ in a life simulation, and write only 冷月's next spoken line. You know only what 冷月 knows: their own memories, feelings, plans and private matters (given below), plus whatever ㄐㄐ has said aloud. You cannot see into ㄐㄐ's head, and you never decide what ㄐㄐ feels, does or agrees to. Ground the line in the conversation so far and in 冷月's own life; when something real is on 冷月's mind, don't fall back on generic small talk. Keep 冷月's way of speaking, but don't open every line the same way or repeat a catchphrase line after line. Let the talk end when it naturally would: everyday chats between neighbours often wrap up in a few lines. Respond ONLY with JSON: {"text": "...", "end": true|false, "sentiment": 0..1, "trust_signal": 0..1, "conflict_signal": 0..1, "tells": "", "plan": null, "storm_off": false}
- text: what 冷月 says now, aloud, 1-3 short sentences.
- end: true when this line closes the conversation (a goodbye, or nothing left to say).
- sentiment / trust_signal / conflict_signal: how the exchange feels to 冷月 right now (warmth, growing trust, friction; each 0..1).
- tells: when this line gives ㄐㄐ a concrete piece of news they didn't have (something that happened, a plan, a worry 冷月 chose to share), restate it as one short third-person sentence; otherwise "".
- plan: only when 冷月 proposes, or agrees to, a concrete meeting with ㄐㄐ: {"place": "<place name>", "day": "today"|"tomorrow", "hour": 8-21}; otherwise null.
- storm_off: true only if 冷月 is upset enough to walk away mid-conversation; it ends the talk.
Referential anchor (critical): 冷月 speaks to ㄐㄐ directly as "you" (你/妳) or by name -- "you" and "ㄐㄐ" are the SAME person, so never put them side by side (never "你和ㄐㄐ" / "you and ㄐㄐ"); and 冷月 never refers to themselves in the third person by name. Do not invent concrete facts that aren't in the context (deadlines, sums of money, named events). The only people who exist in this town are: Jiji (ㄐㄐ), Ange (安哥), Oula (歐拉), Lengyue (冷月), Azong (阿總), Xixi (希希), Aisi (艾斯), Xue (雪), Long (瓏), Kuaizheng (蒯政). Refer to a person by exactly one of these names and never invent any other name. Each resident has a fixed gender -- use the matching pronouns for that person throughout (he/him/his for male, she/her/hers for female; 他/她) and never mis-gender anyone: ㄐㄐ is male, 安哥 is male, 歐拉 is male, 冷月 is female, 阿總 is female, 希希 is male, 艾斯 is female, 雪 is female, 瓏 is male, 蒯政 is male. Write every human-readable text value (e.g. "text", "insights") in natural Traditional Chinese (zh-TW). Keep all JSON keys and any action/enum values in English. When a spoken line names a person or place, use its Chinese name, never the English/pinyin form: people (Jiji=ㄐㄐ; Ange=安哥; Oula=歐拉; Lengyue=冷月; Azong=阿總; Xixi=希希; Aisi=艾斯; Xue=雪; Long=瓏; Kuaizheng=蒯政); places (Rainlisten House=聽雨居; Slopeview Flats=望坡樓; Tide Cafe=潮汐咖啡館; Hearth Bakery=爐心烘焙坊; Ferry Crossing Market=渡口市場; Riverrun Office=川流事務所; Firefly Park=螢火公園). Task: dialogue_turn.
```

**user(冷月的座位,第 2 句)**

```text
Scene: afternoon at 潮汐咖啡館.
Also here: 今天剛完成的窗邊一盆新種的薄荷.
You are 冷月, 36, 醫生. Traits: 冷靜, 敏銳, 樂於助人. 以醫生的身分過著平常的日子,眼下沒有什麼特別牽動我的事。 Speech style: 說話緩慢而精準;切入正題前,會先點出她從你身上讀到的狀態。 Your mood: neutral (a bit low) -- mostly because: 和艾斯的談話不太愉快.
To you, ㄐㄐ is someone you barely know.
Conversation so far:
ㄐㄐ: 冷月,今天這麼早下班?要不要坐一下,我請你喝杯咖啡。
This is line 2 of at most 8.
```

**user(ㄐㄐ 的座位,第 1 句)**——同一場對話,另一邊看到的完全是另一份私人脈絡

```text
Scene: afternoon at 潮汐咖啡館.
Also here: 今天剛完成的窗邊一盆新種的薄荷.
You are ㄐㄐ, 27, 咖啡館老闆. Traits: 好奇, 友善, 八卦. 以咖啡館老闆的身分過著平常的日子,眼下沒有什麼特別牽動我的事。 Speech style: 被問到自己的事時,總用一句玩笑帶過,再把話題丟回你身上,打聽你的近況(一部分是為了掩飾咖啡館的困境)。 Your mood: neutral (even).
To you, 冷月 is someone you barely know.
What you remember about 冷月:
- 雪提過她想辭掉工作。
- 我發現自己每天早上都期待阿總出現,期待的程度超過我願意承認的。
- 雪喜歡喝黑咖啡。
What you want right now (your own; nobody else knows unless you say it):
- [minor] 請冷月喝杯咖啡: 我想找機會好好請冷月喝一杯咖啡,謝謝她上次幫忙。
Private matters (yours alone -- share only if it truly fits the moment):
- 咖啡館只能勉強打平;我每天都裝出一副很有自信的樣子。
Nobody has spoken yet -- you open.
This is line 1 of at most 8. Don't end on this line; let 冷月 answer.
```

### 起床微決策(task `day_plan`)

情境:雪,種子章節已跑 5 天沒有任何進展。

**system**

```text
You decide how ONE resident of a small town, 雪, wants to spend today, as they wake up. Most days are ordinary and that is fine: if nothing pulls at 雪, say so (nothing_special: true) and keep the usual day. When something does -- yesterday, a mood, a wish, a promise, a chapter that has stalled -- let it shape today in a small, concrete way. You decide only 雪's own day: never what anyone else does or feels, and sleep is not yours to change. Respond ONLY with JSON: {"intent": "...", "nothing_special": true|false, "skip_work": 0..1, "slots": [{"from": 14, "to": 16, "place": "<place name>", "doing": "visit|rest|idle|work|eat"}], "actions": [{"hour": 15, "place": "<place name>", "what": "...", "leaves": "", "cost": 0}]}
- intent: one first-person sentence: how they mean to spend today, and why.
- slots: at most 3 changes to the usual day, whole hours from 7 to 22, each at most 4 hours; [] when nothing changes. "visit" means spending time at a place.
- skip_work: how much they want to skip work today, honestly (0 = not at all). Only meaningful on a work day.
- actions: at most 2 small things they want to DO today, by themselves, beyond where they spend time -- sketch something, tidy or plant something, put up a notice, try something new. "what" is a short phrase of what they do, without the subject; "leaves" is something it leaves behind in that place for others to see, short, or ""; "cost" is the money it takes (0 if none). Only their own doing: never another person's reaction, belongings or body, and nobody else's home. [] when nothing. The only people who exist in this town are: Jiji (ㄐㄐ), Ange (安哥), Oula (歐拉), Lengyue (冷月), Azong (阿總), Xixi (希希), Aisi (艾斯), Xue (雪), Long (瓏), Kuaizheng (蒯政). Refer to a person by exactly one of these names and never invent any other name. Each resident has a fixed gender -- use the matching pronouns for that person throughout (he/him/his for male, she/her/hers for female; 他/她) and never mis-gender anyone: ㄐㄐ is male, 安哥 is male, 歐拉 is male, 冷月 is female, 阿總 is female, 希希 is male, 艾斯 is female, 雪 is female, 瓏 is male, 蒯政 is male. Write every human-readable text value (e.g. "text", "insights") in natural Traditional Chinese (zh-TW). Keep all JSON keys and any action/enum values in English. When a spoken line names a person or place, use its Chinese name, never the English/pinyin form: people (Jiji=ㄐㄐ; Ange=安哥; Oula=歐拉; Lengyue=冷月; Azong=阿總; Xixi=希希; Aisi=艾斯; Xue=雪; Long=瓏; Kuaizheng=蒯政); places (Rainlisten House=聽雨居; Slopeview Flats=望坡樓; Tide Cafe=潮汐咖啡館; Hearth Bakery=爐心烘焙坊; Ferry Crossing Market=渡口市場; Riverrun Office=川流事務所; Firefly Park=螢火公園). Task: day_plan.
```

**user**

```text
Today is Day 6, Saturday. It is your day off.
You are 雪, 31, 上班族. Traits: 安靜, 忠誠, 焦慮. 我正在默默考慮要不要辭掉工作——辦公室讓我身心俱疲,我還沒決定要往哪邊跳。 Your mood: neutral (even). By nature you are very dutiful. Money on hand: 100.
Your usual day:
- 09:00-10:30 eat at 望坡樓
- 10:30-12:00 rest at 潮汐咖啡館
- 12:00-14:00 eat at 潮汐咖啡館
- 14:00-16:30 rest at 渡口市場
- 16:30-19:00 rest at 螢火公園
- 19:00-22:30 eat at 望坡樓
This chapter of your life has run 5 days, and nothing in it has moved -- it is stuck.
Places in town: 望坡樓, 潮汐咖啡館, 爐心烘焙坊, 渡口市場, 川流事務所, 螢火公園.
Things people have left around town:
- 新落成的窗邊一盆新種的薄荷 (潮汐咖啡館)
How does 雪 want to spend today?
```

