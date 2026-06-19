<div align="center">

<img src="frontend/public/favicon.svg" alt="PaperHub logo" width="72" height="72" />

# PaperHub

**一個以論文為核心的聊天客戶端，每一句引用都能追溯回它的來源。**

多代理工具路由 · 內建於儲存庫的 RAG 知識庫 · 代理式逐篇論文檢索 · Citation Canvas（引用畫布）讓每個 `[chunk]` 都連回論文中確切的段落 · 一條會議等級的 Beamer 投影片產製流程，搭配解耦且可編輯的講稿。

![Python](https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-React%2019-3178C6?logo=typescript&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-LangGraph-009688?logo=fastapi&logoColor=white)
![Vite](https://img.shields.io/badge/Vite-Tailwind-646CFF?logo=vite&logoColor=white)
![Lint](https://img.shields.io/badge/lint-ruff-261230?logo=ruff&logoColor=white)
![Types](https://img.shields.io/badge/types-mypy%20--strict-2A6DB2)
![Tests](https://img.shields.io/badge/tests-1297%20backend%20%2B%20541%20frontend-brightgreen)
![Status](https://img.shields.io/badge/release-v2.37.1%20(SRS%20v2.37.1)-success)
![License](https://img.shields.io/badge/license-Apache%202.0-blue)

[English](README.md) · [日本語](README.ja.md) · **繁體中文** · [简体中文](README.zh-CN.md)

</div>

---

PaperHub 以 **使用者體驗優先（UX-first）** 打造。每一段檢索到的 chunk 都有可點擊的來源追溯軌跡，每一個產製步驟都會寫入一筆稽核紀錄，而每一輪對話都能單憑 SQLite 完整重建。單一聊天介面會將每一輪對話路由到正確的專責代理 — 論文搜尋、論文問答、NL→SQL 文獻庫統計、記憶管理，或投影片產製。

## ✨ 它能做什麼

🔎 代理式檢索 · 🧷 Citation Canvas · 🌍 你的語言 · 📊 文獻庫統計 · 🧠 記憶 · 🧭 路由 + 追蹤 · 🌐 探索 · 📎 帶上你的論文 · 🖼️ Beamer 投影片 · 🔱 分叉與回溯 · ➗ 數學 · 💾 任何裝置 · 🔌 原生 MCP

<details>
<summary><b>每一項的說明 →</b></summary>

<br>

- **🔎 代理式檢索。** 每篇論文都有專屬子代理，依章節目錄導覽（而非盲目 top-k）；旗艦模型跨論文綜整。
- **🧷 Citation Canvas。** 行內 `[chunk:N]` 標記連回確切段落 — 點擊即可同時在彩現後的 HTML *以及* 來源 PDF 中標示。
- **🌍 你的語言。** 用中文問就用中文答 — 引用一併保留；被記住的「永遠用 X 語言回覆」會覆蓋逐輪偵測。
- **📊 文獻庫統計。**「我有幾篇論文？」→ 在表格白名單上執行唯讀 SQL，並同時用數字 *以及* 它執行的 SQL 回答。
- **🧠 記憶。** 事實／偏好可逐一聊天或全域保存 — 具安全防線、LLM 衝突 **取代**，以及可編輯／（停）啟用的管理面板。
- **🧭 可見的路由 + 追蹤。** 徽章顯示每輪由哪個代理 + 模型處理；追蹤面板能從 SQLite 重播每一步。
- **🌐 探索。** `paper_search` 即使是模糊指涉（「那篇大家都引用的擴散模型論文」）也能透過網路 + Semantic Scholar 解析。
- **📎 帶上你自己的論文。** 以 arXiv ID、URL 或 PDF 附加 — 去重 + 快取；背景 **Marker** 工作程序把 PDF 升級成真正的圖片、圖說與方程式→LaTeX。
- **🖼️ 會議等級的投影片。** 有依據的 **Beamer 投影片**，且 **絕不引用不存在的圖片**。可選擇加入的任何語言講稿；用聊天 **逐張差異編輯單張投影片**；針對 **螢幕上的投影片** 提問也不會更動投影片。
- **🔱 分叉與回溯。** 從過去任一則訊息分叉新聊天 — 預先填入、可編輯、不自動送出。分叉會帶過參考資料、記憶與投影片，並 **巢狀置於母項之下**。
- **➗ 數學會彩現。** LaTeX（`$…$`、`$$…$$`）透過 KaTeX 彩現為真正的方程式。
- **💾 任何裝置。** 工作階段及其完整紀錄存在後端而非瀏覽器。刪除聊天會在各處一併移除（可復原）。
- **🔌 原生 MCP。** 代理的工具透過 MCP（`/mcp`）提供；外部用戶端（Claude Desktop、Cursor）可連到同一介面。

</details>

---

## 📸 螢幕截圖

**有依據的答覆 — 每一項論述都能追溯回來源。**

| Citation Canvas → 彩現後的 HTML | Citation Canvas → 來源 PDF |
| :---: | :---: |
| [![Citation Canvas highlighting a cited chunk in the rendered paper HTML](docs/screenshots/04-citation-canvas-html.png)](docs/screenshots/04-citation-canvas-html.png) | [![Same citation highlighted in the source PDF via a geometry overlay](docs/screenshots/05-citation-canvas-pdf.png)](docs/screenshots/05-citation-canvas-pdf.png) |
| 點擊任一 `[chunk]` → 在 LaTeX 彩現後的 HTML 中捲動至 + 標示確切的段落。 | …以及原始 PDF 中的同一段落。沒有無依據的論述。 |

**會議等級的投影片 — 解耦、可選擇加入的講稿。**

| 產生（僅投影片） | 依需求加入講稿 |
| :---: | :---: |
| [![Deck chip with a Generate-notes button and the Slides panel open](docs/screenshots/11-slides-generate.png)](docs/screenshots/11-slides-generate.png) | [![Slides panel with the speaker-note pane filled in](docs/screenshots/12-slides-notes-added.png)](docs/screenshots/12-slides-notes-added.png) |
| 一份帶有真實圖片的 Beamer 投影片（無捏造的圖形）— 先有投影片，沒有講稿。 | 講稿是可選擇加入的後續動作，獨立撰寫（且可用任何語言）。 |

**文獻庫智慧 + 記憶。**

| NL→SQL 文獻庫統計 | 工作階段 + 全域記憶 |
| :---: | :---: |
| [![A stats answer showing the numbers and the read-only SQL it ran](docs/screenshots/09-library-stats-sql.png)](docs/screenshots/09-library-stats-sql.png) | [![Memory Manager with session/global groups and supersede badges](docs/screenshots/10-memory-manager.png)](docs/screenshots/10-memory-manager.png) |
| 「我有幾篇論文？」→ 同時用數字 **以及** 確切的 SQL 來回答。 | 被記住的事實／偏好，搭配安全防線 + 衝突取代歷程。 |

**路由 + 可觀測性。**

| 路由徽章 | 追蹤面板（可重播的 DAG） |
| :---: | :---: |
| [![A chat turn tagged with the routing badge showing intent and model](docs/screenshots/02-routing-badge.png)](docs/screenshots/02-routing-badge.png) | [![Expanded trace panel listing each step with latency and status](docs/screenshots/03-trace-panel.png)](docs/screenshots/03-trace-panel.png) |
| 每一輪都會顯示由哪個代理 + 模型處理。 | 每個模型／MCP／流程步驟都是一筆稽核紀錄 — 完整的 DAG 能從 SQLite 重播。 |

**探索 + 帶上你自己的論文。**

| 論文搜尋卡片 | Reference Sources（參考來源）抽屜 |
| :---: | :---: |
| [![Paper-search result cards with Add-as-reference buttons](docs/screenshots/07-paper-search-cards.png)](docs/screenshots/07-paper-search-cards.png) | [![Reference Sources drawer listing the session's enabled papers](docs/screenshots/08-reference-sources.png)](docs/screenshots/08-reference-sources.png) |
| 透過網路 + Semantic Scholar 探索；代理會自動加入它精選的最佳結果。 | 工作階段範圍的參考資料集，可逐篇論文啟用／移除。 |

<details>
<summary>更多 — 應用程式總覽 &amp; 用你的語言回答</summary>

| 介面外殼 | 用你的語言回答 |
| :---: | :---: |
| [![Full PaperHub window: sidebar, chat, composer](docs/screenshots/01-app-overview.png)](docs/screenshots/01-app-overview.png) | [![A Chinese question answered in Chinese with citation markers preserved](docs/screenshots/06-language-adherence.png)](docs/screenshots/06-language-adherence.png) |
| 單一聊天外殼；每一輪都路由到一個專責代理。 | 用任何語言提問 — 答覆隨之而來，引用一併保留。 |

</details>

---

## 🧱 技術堆疊

| 領域 | 選用 |
| --- | --- |
| **後端** | Python 3.11 · FastAPI · LangGraph · LiteLLM · SQLite（`aiosqlite`）· Pydantic v2 |
| **前端** | TypeScript · React 19 · Vite · Tailwind · Zustand · `react-markdown` + KaTeX |
| **檢索** | SQLite `chunks` 表格 — 透過 `list_sections`/`read_section` 進行代理式章節導覽（無向量資料庫） |
| **投影片** | Beamer + `pdflatex`（`metropolis` 主題）· `datalab-to/marker` PDF 擷取，作為 docker-compose 服務（選用、可感知 GPU） |
| **LLM** | 預設 Gemini（任何 LiteLLM 供應商 — 小型子代理、旗艦終結器） |
| **工具鏈** | `uv` · `pytest` · `ruff` · `mypy --strict` · Vitest · ESLint · Conventional Commits |

> [!NOTE]
> 僅限本機、單一使用者。沒有驗證介面 — 指向你自己的 LLM 金鑰，並在你自己的機器上執行。

---

## 🚀 快速開始

### 🐳 以 Docker 執行（推薦 — 只想用這個應用程式）

如果你只想 *執行* PaperHub 而非開發它，整套堆疊都在容器中執行 — **不需安裝 Python、Node，或 LaTeX**。你只需要 [Docker](https://docs.docker.com/get-docker/) 和一把 LLM 金鑰。一道 `docker compose up` 就會啟動全部五個服務（後端、model-server、Marker PDF 擷取、網路搜尋，以及 Web UI），所以投影片（含 **中文／CJK**）、RAG，以及網路探索全都開箱即用。

```bash
git clone https://github.com/whats2000/PaperHub.git
cd PaperHub
cp backend/.env.example backend/.env   # then fill in GEMINI_API_KEY (or your provider's key)

docker compose up -d --build           # CPU; first build downloads TeX Live + Marker weights (a few GB, once)
```

開啟 **http://localhost:8080**。

> [!NOTE]
> **GPU（選用，NVIDIA + [Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)）：** 更快的 Marker PDF 擷取。疊上 GPU override：
> ```bash
> docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
> ```

資料會保存在具名磁碟區中（`paperhub-workspace` = DB + 快取、模型權重、Marker 權重）。`docker compose down` 會停止它；加上 `-v` 則會一併清除資料。

---

### 🛠️ 從原始碼執行（供開發用）

**先決條件：** Python 3.11 + [`uv`](https://docs.astral.sh/uv/)、Node 18+，以及一把 LLM API 金鑰（預設 Gemini）。**投影片產製** 另外需要 `PATH` 上有 LaTeX 發行版（`pdflatex` — 例如 `winget install MiKTeX.MiKTeX`）；若缺少，只有 `slides` 意圖會受影響（它會回傳一則「安裝 LaTeX 發行版」的訊息）。PDF 圖片／方程式擷取可選擇使用 Docker 化的 `marker` 服務（`docker compose up -d marker`）。

```bash
git clone https://github.com/whats2000/PaperHub.git
cd PaperHub

# Install both halves
cd backend && uv sync          # Python deps from uv.lock
cd ../frontend && npm install  # JS deps from package-lock.json
```

設定你的 LLM 金鑰：

```bash
cd backend
cp .env.example .env           # then fill in GEMINI_API_KEY (or your provider's key)
```

#### 執行開發堆疊

**推薦（Windows，一道指令）：** `scripts/start.ps1` 會協調所有
同層的程序 — 它會透過 `paperhub-mcp-up` 啟動外部 MCP 常駐程式
（open-websearch），接著啟動帶有熱重載的後端：

```powershell
# Terminal 1 — backend stack (MCP daemons + FastAPI on :8000)
cd backend
.\scripts\start.ps1
```

```bash
# Terminal 2 — frontend (Vite + React, hot-reload, :5173)
cd frontend
npm run dev
```

開啟 **http://localhost:5173** 並開始聊天。

<details>
<summary>更底層：直接執行 uvicorn</summary>

```bash
cd backend
uv run uvicorn paperhub.app:app --reload --reload-dir src --port 8000
```

注意：這條路徑 **不會** 為你啟動網路搜尋常駐程式。在 Windows 上，
`uvicorn --reload` 會在 `SelectorEventLoop` 上執行，所以 worker 內的自動啟動
會優雅地退回（僅論文）— 請自行用
`uv run paperhub-mcp-up` 啟動網路搜尋（或使用 `scripts/start.ps1`，它會代為處理）。請參閱
[⚙️ 設定](#️-設定) 之下的網路搜尋說明。

</details>

> [!TIP]
> **手邊沒有 API 金鑰？** 用模擬的 LLM 來操練聊天管路（PowerShell）：
> ```powershell
> $env:PAPERHUB_ROUTER_MOCK   = '{"intent":"chitchat","model_tier":"small","confidence":0.9,"reasoning":"dev"}'
> $env:PAPERHUB_CHITCHAT_MOCK = "Hello from PaperHub!"
> uv run uvicorn paperhub.app:app --reload --reload-dir src --port 8000
> ```

---

## ⚙️ 設定

所有設定都放在 `backend/.env`（在 [`.env.example`](backend/.env.example) 中依功能分組）。你最可能會動到的有：

| 變數 | 用途 | 預設 |
| --- | --- | --- |
| `GEMINI_API_KEY` | LLM 供應商憑證（或 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`） | — |
| `PAPERHUB_PAPER_QA_MODEL` | 旗艦終結器（跨論文綜整） | `gemini/gemini-2.5-pro` |
| `PAPERHUB_PAPER_QA_SUBAGENT_MODEL` | 逐篇論文章節導覽器（輕量） | `gemini/gemini-3.1-flash-lite` |
| `PAPERHUB_SEMANTIC_SCHOLAR_API_KEY` | 更高的 Semantic Scholar 速率上限（選用） | — |

**網路搜尋探索（選用）。** 當 `:3000` 上有可連到的 [`open-websearch`](https://www.npmjs.com/package/open-websearch) 常駐程式時，`paper_search` / `paper_suggest` 會多出一個免金鑰的多引擎探索步驟。你不必手動安裝它 — `scripts/start.ps1`（或 `uv run paperhub-mcp-up`）會讀取 `mcp_servers.toml`，並透過 `npx -y` 為你啟動每一個宣告了 `launch` 的 MCP 伺服器，首次執行時會抓取套件（約 25 秒，一次性）：

```bash
cd backend
uv run paperhub-mcp-up          # launches open-websearch on :3000 (skips if already up)
```

當它啟動時，後端的 MCP 註冊表會自動公開 `web.search` / `web.fetch`。當它未啟動時，代理會退回到僅論文的流程 — 無需任何設定。被衍生出的常駐程式會處於分離（detached）狀態，因此能在後端 `--reload` 後存活；明確的關閉是 `start.ps1` 的職責（否則它們會在重新開機時清除）。需要 `PATH` 上有 Node 18+。（`paperhub-papers` 這個 MCP 介面以行內方式（in-process）內建於 `/mcp`；無需安裝。）

---

## 🗺️ 架構（一個畫面）

```
┌─────────────────┐       SSE      ┌───────────────────────────────────────────┐
│  React shell    │ ◄───────────── │ FastAPI · POST /chat                      │
│  - Composer     │                │  ┌─────────────────────────────────────┐  │
│  - Routing badge│                │  │ LangGraph turn                      │  │
│  - Trace panel  │                │  │  Router ─► chitchat | paper_qa |    │  │
│  - Citation     │                │  │           paper_search | slides |   │  │
│    Canvas       │                │  │           library_stats             │  │
└─────────────────┘                │  └─────────────────────────────────────┘  │
                                   │     │                                     │
                                   │     ▼  paper_qa: fan out one subagent     │
                                   │        per paper → section nav →          │
                                   │        flagship finalizer over raw chunks │
                                   │  ┌─────────┐ ┌────────────────────────┐   │
                                   │  │ LiteLLM │ │ SQLite (chunks + audit │   │
                                   │  │ adapter │ │ + schema)              │   │
                                   │  └─────────┘ └────────────────────────┘   │
                                   └───────────────────────────────────────────┘
```

每一次模型呼叫、MCP 呼叫，以及流程步驟，都會在回傳前寫入一筆 `tool_calls` 紀錄 — 足以單憑 `SELECT * FROM tool_calls WHERE run_id = ?` 重建完整的代理脈絡。論文內容是 **去重** 的：每一篇獨特的論文只有一筆 `paper_content` 紀錄 + 一個快取目錄 + 一組 chunk，無論有多少個工作階段參考它。

完整架構請見 [SRS](docs/superpowers/specs/2026-05-17-paperhub-srs.md)。

---

## 📍 狀態

| 計畫 | 範圍 | 狀態 |
| --- | --- | --- |
| **A** | 後端基礎 + 僅路由的聊天 | ✅ 完成 |
| **B** | 前端基礎（React 外殼、SSE、路由徽章、追蹤面板） | ✅ 完成 |
| **C** | 論文流程 + 研究代理（擷取、paper_search、代理式 paper_qa、MCP 層、PDF 上傳） | ✅ 完成 — 已合併（SRS v2.10） |
| **D** | 搜尋結果 + Reference Sources + Citation Canvas（HTML + PDF 段落標示） | ✅ 完成 — 已合併（SRS v2.13） |
| **E** | SQL 代理 + `library_stats`（sqlite MCP）+ 工作階段／全域記憶治理（防線、衝突取代、記憶管理 UI） | ✅ 完成 — 已合併（SRS v2.17） |
| **F** | 投影片流程 + 報告代理 — Marker 擷取（F2/F2.1）、博士等級的投影片代理（F3）、解耦的可選講稿 + 差異編輯 + 長度預算（F4）、會議等級的標題頁中繼資料 + 標題／樣式客製（F4.2） | ✅ 完成 — 已合併（SRS v2.22） |
| **F5** | 投影片簡報模式（觀眾彈出視窗 + `BroadcastChannel` 同步 + 簡報者操作台）+ 演講中問答 + 編輯器語音輸入 | ✅ 完成 — 已合併（SRS v2.26） |
| **G** | 前端 UI 國際化（i18n：`en` / `zh-TW` / `zh-CN` / `ja`）+ 帳號選單（語言／主題切換、關於）+ 以資料庫為後盾的執行期設定面板 | ✅ 完成 — 已合併（SRS v2.31） |
| **H** | 比較檢視 + 檔案系統 / `paperhub.*` MCP | 🔜 已規劃 |

每個計畫都能各自交付可運作、可測試的軟體。計畫放在 [`docs/superpowers/plans/`](docs/superpowers/plans/) 之下。

---

## 🧑‍💻 開發

PaperHub 以 spec → plan → TDD 打造，採用子代理驅動的實作，以及逐項任務的規格符合性 + 程式碼品質審查。

**後端品質關卡**（從 `backend/`）：

```bash
uv run pytest          # 1104 tests, hermetic
uv run ruff check src tests
uv run mypy src        # --strict
```

**前端品質關卡**（從 `frontend/`）：

```bash
npm test               # Vitest + RTL + MSW (386 tests)
npm run typecheck      # tsc --strict
npm run lint           # ESLint flat config
npm run build          # Vite production build
```

**重播任何過去的聊天輪次**，從 SQLite（除錯代理流程）：

```bash
cd backend
uv run paperhub-replay --run-id 1
```

**端對端基準測試** — `pytest` 證明的是接線；[`backend/benchmark/`](backend/benchmark/) 這套工具證明的是 *行為*。它會把 **正在執行的** 後端當作一個模擬的使用者來驅動（附加已快取的論文 → 將提示透過 `/chat` 路由），蒐集依據證據（被引用的 chunk 文字 + 代理追蹤），並為每個案例在正確性 + 依據上評為 **0/1** — 由人工，或透過一個 **LLM-as-Judge**（固定溫度、嚴格依據）。案例是設定驅動的（TOML），所以你可以寫自己的：

```bash
# with the backend running (scripts/start.ps1), from backend/:
scripts/run-benchmark.ps1 -Judge            # 20-case eval (16 paper_qa + 4 slides) + LLM judge
scripts/run-benchmark.ps1 -Resume <prior.json>   # retry only failed cases after a drop
```

> 貢獻的 AI 代理：請先閱讀 [CLAUDE.md](CLAUDE.md) — 它承載了慣例、fix-now 政策，以及代理流程可觀測性規則。

---

## 📂 儲存庫結構

```
.
├── backend/
│   ├── src/paperhub/         # FastAPI app · agents · pipelines · mcp · tracer
│   ├── tests/                # pytest suite (1104 tests, hermetic)
│   ├── benchmark/            # config-driven real-API e2e benchmark + LLM-as-Judge
│   └── pyproject.toml        # uv project · mypy --strict · ruff
├── frontend/                 # React 19 + Vite + Tailwind + Zustand
├── docs/superpowers/
│   ├── specs/                # SRS — authoritative architecture document
│   └── plans/                # implementation plans, one per sub-project
├── reference/                # copied source from paper2slides-plus + Intro2GenAI-hw1
├── CLAUDE.md                 # AI-agent orientation for this repo
└── README.md
```

`workspace/`（被 gitignore）存放執行期狀態 — SQLite 資料庫與論文快取。

---

## 📖 文件

- **[系統需求規格書（System Requirements Specification）](docs/superpowers/specs/2026-05-17-paperhub-srs.md)** — 權威的架構、結構描述、範圍，以及驗收準則（已交付至 **v2.37.1**）。
- **[實作計畫](docs/superpowers/plans/)** — 每個子專案一份，皆透過 TDD 執行。
- **[後端開發者文件](backend/README.md)** — 後端專屬說明。

---

## 📚 引用

如果你在研究中使用 PaperHub 或以它為基礎進行開發，請引用它：

```bibtex
@software{paperhub,
  author  = {Ren-Di, Wu},
  title   = {{PaperHub: A Provenance-First Multi-Agent Research Assistant for Grounded Paper Q\&A and Slide Generation}},
  year    = {2026},
  url     = {https://github.com/whats2000/PaperHub},
  version = {2.37.1}
}
```

---

## 📄 授權

[Apache License 2.0](LICENSE) — © PaperHub 貢獻者。你可以在本授權條款的範圍內使用、修改，以及散布本軟體，其中包含貢獻者明示授予的專利權。
