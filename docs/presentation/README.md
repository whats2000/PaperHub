# 專案介紹簡報

繁體中文，約 10 分鐘，**17 投影片**。PaperHub 專案的整體介紹簡報。

## 建立 / 重建投影片

```powershell
cd D:\GitHub\PaperHub\docs\presentation
npm install                # 第一次：拉 pptxgenjs
node build_deck.js         # 產生 .pptx
```

成功後會寫入 repo 根目錄：

```
Wrote: D:\GitHub\PaperHub\PaperHub_專案介紹.pptx
```

雙擊即可在 PowerPoint / Keynote / WPS / LibreOffice Impress 開啟。
所有截圖直接從 `docs/screenshots/` 內嵌進檔案，分發時不需另附資料夾。

> **檔案位置**：`.pptx` 寫到 repo 根目錄而非 `docs/presentation/`，因為
> 根目錄已被 `.gitignore` 排除 `*.pptx`，避免 binary 進入 git。

## 投影片大綱

| #  | 標題                                        | 重點                                |
| -- | ------------------------------------------- | ----------------------------------- |
| 1  | Cover（黑底）                               | 專案開場                            |
| 2  | Problem — 三大痛點                          | 問題定義與情境理解                  |
| 3  | Journey — 研究流程四階段                    | 使用者旅程                          |
| 4  | Solution — 四個承諾                         | 核心價值主張                        |
| 5  | **Architecture — 真實架構圖**               | 系統設計完整性                      |
| 6  | Positioning — 與 ChatGPT/NotebookLM/PaperQA | 市場定位與差異                      |
| 7  | Demo I · Citation Canvas（HEADLINE）        | 旗艦功能 Demo                       |
| 8  | Demo II · Agentic Paper QA                  | 階層式檢索 Demo                     |
| 9  | Demo III · Slide Pipeline                   | 投影片產出 Demo                     |
| 10 | Demo IV · Library & Memory                  | 圖書館與記憶 Demo                   |
| 11 | **AI Tech Depth — 4 個關鍵技術決策**        | AI 技術應用深度                     |
| 12 | Trust（黑底）— 為什麼可信                   | 信任設計理念                        |
| 13 | Real-world Fit — 落地場景 + Docker 部署     | 落地場景與部署                      |
| 14 | Metrics — 真實量測（30 篇 / 19-20 / 1,036） | 成效與量測數據                      |
| 15 | Summary — 三句話總結                        | 總結                                |
| 16 | Team — 六人團隊 / 四角色                    | 團隊分工                            |
| 17 | Q&A（黑底）                                 | 收尾與提問                          |

## 設計

- **配色：** Swiss Modernist — 純白 `#FFFFFF` + 炭墨 `#0A0A0A` + 鈷藍 `#0047AB`。
  刻意避開 cream + terracotta + serif italic（會聯想到 Anthropic / Claude
  品牌）。鈷藍對應 Wired / IBM / Pentagram 系。
- **字體：** Arial Black（顯示）+ Calibri / Helvetica（內文）+ Microsoft
  JhengHei（中文）+ Consolas（mono / 終端機）。
- **視覺主題：**
  - 每張內容頁：頁首厚黑橫線、`NN /17` 等寬頁碼、ALL CAPS 章節標籤、右上
    `PAPERHUB` 文字標識；頁尾細灰線 + `PG.NN`。
  - 三張暗色頁（封面 / Trust 中場 / Q&A 收尾）製造節奏 chapter break。
  - 強類型對比：標題用 Microsoft JhengHei Bold + 鈷藍重點字；大數字用 Arial
    Black 80–200pt；技術細節用 Consolas mono ALL CAPS。
- **架構圖（S5）：** vector box-and-arrows，非文字 stack。USER → UI →
  Orchestrator（內含 ROUTER 與 6 agents）↔ SQLite，下游 LLM / RAG / MCP。
- **資料源：** S14 三大數字皆來自真實可驗 — `30 篇` 從
  `backend/workspace/papers_cache/{arxiv,upload}/` 計數；`19/20` benchmark
  結果在 `backend/benchmark/results/SCORED-REPORT.md`；`1,036` 測試數
  `uv run pytest --collect-only` + `npm test -- --run` 可數。

## 修改

直接編輯 `build_deck.js` 然後重跑 `node build_deck.js`。每張投影片是
`pres.addSlide()` 內的獨立 block，互不相依，只動其中一張不會影響其他頁。

### 常用調整位置

- **顏色配置：** 檔案頭 `const C = { ... }` — 改 `cobalt` 或 `ink` 一鍵
  換主色。
- **字體：** `const F = { ... }` — `display` / `bold` / `sans` / `zh` /
  `mono` 五個 slot。
- **TOTAL 頁數：** `const TOTAL = 17;` — 加 / 減 slide 時記得更新，並逐個
  `pageHeader(s, N, ...)` / `pageFooter(s, N)` 重新編號。
- **截圖路徑：** 檔案頭 `const SHOT = ...` 指向 `docs/screenshots/`。

## 驗證 / 視覺檢查

```powershell
cd D:\GitHub\PaperHub\docs\presentation
# 用 LibreOffice 轉 PDF（headless），再用 pdftoppm 拆圖逐頁看
soffice --headless --convert-to pdf ..\..\PaperHub_專案介紹.pptx
pdftoppm -jpeg -r 90 PaperHub_專案介紹.pdf slide
```

每頁 JPG 可拖進任何看圖工具掃描 typography overlap / content 遮擋等問題。
