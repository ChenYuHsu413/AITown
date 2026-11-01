# 逐輪對話管線體檢(第三紀元 Day 3,時鐘凍結中)

- run:`222a9a17…`;時鐘由開發者在 Day 3 07:31 暫停。部署修正時用 `AI_TOWN_START_PAUSED=1` 重啟,時鐘維持凍結。
- 修正 commit:`9d01bc0`(歸屬 / 地點)、`5a66a30`(閘門佈線 + 防線清單)、`c5321a1`(凍結重啟、更正工具)
- 測試:**268 個全綠**。新增的 `test_free_text_checklist`,在修正前的程式上會失敗 5/7。

## 結論:兩案同根

新管線上線時,沒有逐項繼承舊防線。`b816652` 的根修只把 roster 接進了**生成端**(prompt)。執行端的 `gender_ok` 只掛在反思、願望、收束這幾條路徑,對話這條從舊的一次性管線起就沒有掛。逐輪對話照搬了這個缺口,晨間意圖也一樣。

## A. 體檢清單

| # | 項目 | 結果 | 證據 |
|---|---|---|---|
| A1 | turn prompt 帶名字 + 性別 roster | ✓ | system 內含 `roster_directive()` + `roster_gender_directive()`(「希希 is male」⋯),全文見文末 |
| A1 | 說話者自己的身分錨點 | ✓ | 「You voice ONE character, 冷月」+ referential anchor(禁止以名字自稱、禁止把「你」和對方並列) |
| A2 | `gender_ok` 掛在對話輪的落地點 | **✗ → 已修** | `_turn_ok` 原本只跑 `_dialogue_ok`(結構 + zh 品質)和 referential,沒有 `gender_ok`。所以這兩句「她」**沒有留下任何閘門紀錄**,`generation_gender` 計數是 0 |
| A2 | zh_quality 掛在對話輪 | ✓ | `_dialogue_ok` → `_zh_reject_reason` |
| A2 | roster fail-loud | ✓(現在由 `gender_ok` 帶入) | 測試在 roster 為空時斷言 `RosterNotLoadedError` |
| A2 | 其他新路徑 | **✗ → 已修** | 晨間意圖(`intent`)、台詞附帶的轉告(`tells`)都沒掛;行動文字有擋但沒計數。另外盤點時發現舊路徑 `distort` / `leak` 也沒掛 |
| A3 | `gender_ok` 的「只提到一位居民」判定 | ✓ 正確 | 看的是**文本裡的名字**,不是對話參與者。歐拉那兩句只點名希希,丟給閘門都判 `False`;只是當時沒有接上 |
| A4 | 輪次 speaker 歸屬(DB) | ✓ 正確 | 冷月 × 希希那場 8 句,DB 的 actor / target 一句不差 |
| A4 | 地點欄(DB) | ✓ 這場正確,但有潛在 bug,已修 | 原本取「結算時說話者的位置」;改成對話實際發生地 `plan.location`(雙方當時都在、都被鎖住) |

### 點火前那一案的分診(補交)

- **氣泡錨到 ㄐㄐ**:**顯示層 bug**。咖啡館裡擠了三個人,名牌避讓時只往上推、不左右移,冷月的名牌被推到 ㄐㄐ 頭頂上方;氣泡又是跟著名牌畫的。已改成錨定在說話者自己頭頂的投影點,並附上名字和一條指向說話者的尾線(`9d01bc0`)。
- **卡片寫潮汐咖啡館、台詞說在爐心烘焙坊**:卡片和 DB 都對,雙方當時確實在咖啡館。那是冷月座位第一句的模型幻覺:她前一場剛和 ㄐㄐ 聊到「沒進帳」「去找安哥拿吐司」。prompt 已補上「你和對方此刻都在某地,不要把兩人放到別處」。範圍:第三紀元 1 句;第二紀元活化後約 2 句,占 1,348 句的約 0.2%。這類錯只在台詞裡,記憶沒有被污染。

## B. 修復

- **代名詞閘門**(計數 + `GATE_DIAG` 的 `[gender-reject]` log)接上:每一句台詞的 `text` 和 `tells`、晨間 `intent`、行動的 `what` / `leaves`;舊路徑 `distort`(拒收時保留前一版)和 `leak`(拒收時改用中性句)。
- 台詞地點改取對話發生地;氣泡錨點修正。
- **制度化**:新增 [`docs/free-text-checklist.md`](../free-text-checklist.md),列出新自由文本路徑上線前必須逐項打勾的 7 道防線,附現有路徑盤點,README 的 docs 索引也連過去了。`tests/test_free_text_checklist.py` 用真實踩到的那幾句,把每條路徑釘在清單上。
- 已知限制,未改:`gender_ok` 不判斷同時點名兩人以上的句子。冷月那句「等歐拉,她說…安哥」因此仍然會放行。要不要放寬,請你裁決。

## C. 污染清理(第三紀元)

用同一個閘門掃描第三紀元的全部台詞和記憶,共 4 筆。只把錯的代名詞換成那位居民的正確性別,其他文字一字不動。執行前快照已備份到 `snapshot_archive` id 12(工具:`scripts/fix_text_records.py`,先 dry-run)。

| # | 類型 | 時間 | 誰 | 原文 → 更正 |
|---|---|---|---|---|
| 1 | 台詞(事件 #84117) | D2 21:04 | 歐拉 → 艾斯 | 「只知道**她**寫了好幾條⋯**她**說紅外線那條**她**卡最久⋯**她**應該晚點會過去找你」→ 他 ×4 |
| 2 | 台詞(事件 #84119) | D2 21:06 | 歐拉 → 艾斯 | 「叫**她**直接去螢火公園找你⋯**她**應該會問得很仔細」→ 他 ×2 |
| 3 | 晨間意圖記憶(#16445) | D2 00:18 | 艾斯 | 「看看**她**那個記帳網頁能不能幫上忙」→ 他 |
| 4 | 晨間意圖記憶(#16534) + 快照裡的今日打算 | D3 00:38 | 冷月 | 「**她**的紅外線感測器問題我也有點好奇」→ 他 |

- 第 3、4 筆不是由台詞衍生的,而是晨間意圖這條路徑同樣沒掛閘門造成的。轉告記憶裡沒有被波及的。
- 清理後重新掃描:**0 筆**。正式紀元和第二紀元都沒有動。

## D. 復跑

A、B、C 都完成後:恢復時鐘,抽 3 場復跑後的真實對話,驗證說話者、地點、性別、氣泡四方一致。結果補在下方。

## 附:實際的 turn prompt 全文(目前程式碼)

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
Scene: afternoon at 潮汐咖啡館. You and ㄐㄐ are both here, at 潮汐咖啡館, right now -- don't place the two of you anywhere else.
Also here: 今天剛完成的窗邊一盆新種的薄荷.
You are 冷月, 36, 醫生. Traits: 冷靜, 敏銳, 樂於助人. 以醫生的身分過著平常的日子,眼下沒有什麼特別牽動我的事。 Speech style: 說話緩慢而精準;切入正題前,會先點出她從你身上讀到的狀態。 Your mood: neutral (a bit low) -- mostly because: 和艾斯的談話不太愉快.
To you, ㄐㄐ is someone you barely know.
Conversation so far:
ㄐㄐ: 冷月,今天這麼早下班?要不要坐一下,我請你喝杯咖啡。
This is line 2 of at most 8.
```

**user(ㄐㄐ 的座位,第 1 句)**——同一場對話,另一邊看到的完全是另一份私人脈絡

```text
Scene: afternoon at 潮汐咖啡館. You and 冷月 are both here, at 潮汐咖啡館, right now -- don't place the two of you anywhere else.
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

