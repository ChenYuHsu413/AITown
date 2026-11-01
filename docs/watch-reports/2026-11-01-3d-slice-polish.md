# 3D 切片打磨輪交付 — 黏土微縮模型五件套(待眼睛過關)

- 頁面:`/town3d-preview`;commit `cfe3a15`
- 檔案:`frontend/js/town3d/world.js`(重寫)、`frontend/js/town3d/slice.json`;主頁 `/`、`main.js`、`glass.css` 未動
- 驗證環境:8020;未碰 8010

## 截圖

`C:\Users\jolen\AppData\Local\Temp\claude\C--Users-jolen-Documents-WorkSpace-AITown\4810b586-efe3-4d4b-a6b0-42bb0f060526\scratchpad\shots4\`
- 全畫面:`day.png` · `dusk.png` · `night.png`
- 建築特寫:`closeup.png` · `closeup-night.png`
- 低角度(看天空漸層與島基分層):`low-day.png` · `low-dusk.png`
- AO 對照:`day-noAO.png`(關掉 AO)

注意:截圖的場景時刻為手動設定以取得日/暮/夜,頂欄時鐘顯示的是伺服器實際時間,兩者不一致屬正常。

## A. 渲染管線

| 項目 | 實作 |
|---|---|
| 色調映射 | ACESFilmic + sRGB 輸出;曝光白天 0.92、夜晚 1.15 |
| 柔影 | **偏離規格**:three r186 已移除 `PCFSoftShadowMap`(設了只會警告並退回 PCF)。改用 `PCFShadowMap` + `shadow.radius = 6`,陰影貼圖 4096 |
| AO(必做) | EffectComposer:RenderPass → GTAOPass → OutputPass(同為 0.186.1),4× MSAA HalfFloat render target,半徑 2.2;屋簷下、建築底部、樹下有接觸暗化(對照 `day-noAO.png`) |
| 天空 | CanvasTexture 垂直漸層(上深下淺),隨時段變色:白天霧青、黃昏桃、夜晚深藍;霧色與天空底色一致 |
| 水面 | 420m 平面,vertex shader 疊三層正弦慢浪 + flatShading 切面;島周一圈半透明淺水邊 |

## B. 幾何

- 全部圓角:量體、部件、家具都是 RoundedBox / 膠囊 / 球體,無直角方塊。
- 窗:凸出白框 + 窗台 + 內縮深色玻璃,寬窗加中梃;夜間玻璃自發光。
- 門:內凹門洞、門板內退、門把、小雨棚含托架、台階。
- 屋頂:兩片 0.3 厚屋面含出簷、膠囊屋脊、同牆色山牆;煙囪含帽蓋。
- 遮陽棚:條紋板下緣半圓扇貝邊。
- 牆面:頂點色由下往上漸亮、底部偏暖;東西向面有明暗差,疊細微顆粒。
- 島基:草皮 / 棕土層 / 米色分層石,三層圓角。
- 廣場:小磚錯縫、窄灰縫、逐磚色相與明度差;大棋盤格移除。
- 樹冠:四顆不同綠的球體融合。
- 居民:球頭 + 膠囊身 + 髮帽,腳下柔邊接觸影隨彈跳縮放。
- 版面微調:一盞路燈原本與噴泉重疊、一棵樹遮住噴泉,已移開;相機目標 y 配合加厚的島基調為 3.5。

## C. 自檢(對照 Townscaper 參考圖)

子代理從 Steam appdetails API 抓了 4 張 Townscaper 縮圖做並排比對(只用於比對,存在 scratchpad,未 commit、不出貨)。

| 項目 | 結果 |
|---|---|
| 柔和陰影 | ✔ |
| AO | ✔ |
| 圓角 | ✔ |
| 牆面漸層 | ✔ |
| 水面 | ✔ |

與參考圖仍有落差:水面無倒影(Townscaper 有);黃昏水色偏灰濁;預設俯角下看不到天空漸層。

## 效能

- 1400×900:AO 開 59–61 fps、AO 關 60–61 fps(vsync 鎖 60,看不出差)。
- 每幀 GPU 時間(每次 render 後同步量):AO 開 5.2–6.2 ms、AO 關 2.1 ms → GTAO 約 +3–4 ms,遠低於 16.7 ms 預算。
- 降級:連續 3 秒 < 45 fps → 自動關 AO、pixel ratio 降為 1,陰影保留。
- 僅 headless Chrome GPU 路徑實測;上一輪已知軟體繪圖(SwiftShader)只有 3 fps。

## 我的審查意見

- 黏土感明顯成立:AO 是最大的單項提升,屋簷下與接地處的柔暗讓量體「坐」在地上;夜景窗光 + 路燈光池最接近 Townscaper。
- 仍可再修的三點(子代理已列):水面倒影、黃昏水色、預設俯角下的天空露出。另外路燈仍是細黑桿,與圓潤語言不搭——全鎮化時建議改粗短圓柱。

**待你的眼睛過關**:過 → 出全鎮 3D 化大單;不過 → 指出哪一項,再修一輪切片。
