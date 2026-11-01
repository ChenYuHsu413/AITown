# 新自由文本路徑上線前的防線清單

**適用範圍**:任何把模型輸出變成居民說出口的話、記住的事、或其他居民看得見的文字的路徑。這包括新的 task、舊 task 新增的文字欄位,以及新的落地點(記憶、事件、物件描述)。

**為什麼要制度化**:同一類坑已經踩了兩次。
- **第一次(D53)**:性別 roster 只接在對話上,反思這條路徑把寫錯代名詞的英文寫進了記憶。根修 `b816652` 把 roster 接進所有生成點。
- **第二次(第三紀元 Day 2)**:逐輪對話上線時,prompt 帶了 roster,但每一句的落地點沒有掛 `gender_ok`。舊的一次性對話也沒掛,新管線延續了同一個缺口。結果「她」指希希(男性)的台詞直接上了畫面,閘門計數器是 0。

## 上線前逐項打勾

| # | 防線 | 怎麼做 | 怎麼驗 |
|---|---|---|---|
| 1 | **名字 + 性別 roster 進 prompt** | system 裡加 `roster_directive()` + `roster_gender_directive()`(英文輸出用 `english_only=True`) | 測試斷言 prompt 含「希希 is male」之類的句子 |
| 2 | **說話者身分錨點**(有「我」的文本) | 明說這是誰的第一人稱,禁止用名字自稱(見 `dialogue_turn_prompt` 的 referential anchor) | prompt 斷言 |
| 3 | **zh 品質閘門** | `_zh_reject_reason` / `_knowledge_lang_ok`(結構化文字用後者) | 餵一段英文或亂碼,斷言拒收 |
| 4 | **代名詞閘門** | `_note_gender_gate(gender_ok(text))`,**每一個**會落地的文字欄位都要過(例如台詞的 `text` 和 `tells` 要各自檢查) | 餵真實的錯誤案例,斷言拒收,且 `GATE_REJECTS["generation_gender"]` +1 |
| 5 | **roster fail-loud** | `gender_ok` 內建 `require_roster`,不要用 try/except 吞掉它 | 測試在 `_ROSTER` 為空時斷言 `RosterNotLoadedError` |
| 6 | **GATE_DIAG 診斷 log** | 拒收時印 `[gender-reject]` / `[gate-reject]` / `[ref-reject]`,帶 provider 和文字片段 | 開 `AI_TOWN_GATE_DIAG=1` 跑一次,log 裡看得到 |
| 7 | **拒收後的去處** | 由 router 換 provider 或重試;不能落 mock 罐頭,也不能讓錯的文字落地 | `no_floor=True`,或丟棄並記錄原因(例如行動的 `dropped`) |

**測試**:每條路徑在 `tests/test_free_text_checklist.py` 都要有對應的案例,而且要用真實踩到的那一句。新路徑沒有加進這支測試,就不算完成。

## 目前的路徑盤點(2026-11-01)

| 路徑 | task | 落地點 | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|---|---|
| 逐輪台詞 | `dialogue_turn(_tense)` | 事件(say)、聽者記憶 | ✓ | ✓ | ✓ | ✓ text + tells | ✓ | ✓ |
| 晨間意圖 | `day_plan` | 本人記憶 | ✓ | ✓(第一人稱) | ✓ | ✓ intent | ✓ | ✓ |
| 自由行動文字 / 物件 | `day_plan.actions` | 記憶、事件、`public_objects` | ✓ | ✓ | ✓ | ✓ what + leaves(item、words) | ✓ | 記入 `dropped`;物件存 kind / item / content,所有句子都由 `actions.object_phrase` 組 |
| 反思 / 信念 / 秘密 | `reflection` | 記憶、信念 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| 願望 | `wish_generation` | 願望 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| 收束傳記句 | `chapter_closure` | 傳記記憶 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| 謠言轉述 / 洩漏 | `distort` / `leak` | 謠言、記憶 | ✓ | — | ✓ | ✓(這次補上:錯了就保留前一版,或改用中性句) | ✓ | ✓ |

**已知限制**:`gender_ok` 只判斷「只點名一位居民」的句子,同時點名兩人以上的句子不猜代名詞歸屬,一律放行。例如「等歐拉,她說…安哥」會漏掉。要不要放寬,等開發者裁決。
