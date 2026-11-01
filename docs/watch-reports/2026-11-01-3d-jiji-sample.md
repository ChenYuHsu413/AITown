# 裁決請求:3D 居民樣品 ㄐㄐ(核可後才量產其餘九人)

全鎮 3D 化大單 C 節要求「先交樣品再量產」。ㄐㄐ 樣品已完成;**其餘九人在你核可前不會製作**(世界部分 A/B/D/E/F 同時以佔位小人推進)。

## 請看

`C:\Users\jolen\AppData\Local\Temp\claude\C--Users-jolen-Documents-WorkSpace-AITown\4810b586-efe3-4d4b-a6b0-42bb0f060526\scratchpad\jiji\`
- `jiji-turntable.png`:轉台六角度(正、斜 45°、側、斜後、背、另一側)
- `jiji-head.png`:頭與臉近照(正面、斜側)
- `jiji-walk.gif`:走路一個步伐週期 16 幀(左側面、右斜側);逐幀 `jiji-walk-00…15.png`,總覽 `jiji-walk-sheet.png`
- `jiji-states.png`:待機(微晃、呼吸、轉頭)、說話(點頭、小手勢)、坐長椅(膝彎)、躺床睡

渲染條件同小鎮:ACES、PCF 柔影、GTAO、頂點色漸層。

## 樣品規格

- 比例:大圓頭 + 小身體,頭:身 ≈ 1:1.45
- 四肢:真關節——大腿/膝/小腿/鞋;肩/上臂/肘/前臂/手(非膠囊豆形)
- 臉:圓眼帶高光、腮紅、小微笑(無表情變化)
- 髮型:實體幾何亂翹短髮(後梳髮殼、蓋額瀏海、鬢角、頭頂三撮翹毛)
- 衣著:上衣 = 居民色 `--r-jiji`;**圍裙**(依「經營咖啡館」加)

**請你確認的外觀設定**(子代理先選了佔位值,未自行決定任何主觀設定):圍裙、髮色、褲色、鞋色。

## 已知待修(子代理自列)

- 睡姿時頭稍微陷進枕頭;zzz 與被子在室內剖面(D)一併加入
- 側看圍裙下擺偏硬,像一塊板
- 手臂關節處看得出一點分段感

## 可調項(全在 JSON,不必改程式碼)

`frontend/js/town3d/residents.json`:
- `proportions`:頭半徑、軀幹、髖高、大腿/小腿長、手臂長與粗細、鞋
- `motion`:步頻、腿與手臂擺幅、膝彎、上下彈跳、呼吸、搖擺、點頭幅度與間隔、坐高
- `residents.jiji`:髮型與髮色、上衣(`top:"main"` = 居民色)、褲、鞋、身高、體型、配件
- 已備好供九人使用:髮型 messy / short / long / ponytail / bun / bob / buzz / hat;配件 圍裙、拐杖(蒯政)、`stoop` 駝背參數

程式:新增 `figure.js`(`Figure` 類,`pose(state, dt)`:walk / idle / talk / sit / sleep)、`clay.js`(從 world.js 抽出共用工具,外觀不變)。尚未 commit,待核可。

## 請回覆

「過」→ 依同一套語言量產其餘九人(髮型/髮色、服裝、體型三層差異,蒯政拐杖或駝背)。
「改」→ 指出比例 / 臉 / 髮型 / 衣著哪一項,改完再交一版樣品。
