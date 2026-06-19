<div align="center">

<img src="frontend/public/favicon.svg" alt="PaperHub logo" width="72" height="72" />

# PaperHub

**一个论文感知的聊天客户端，每一句被引用的话都能追溯回它的来源。**

多智能体工具路由 · 仓库内置的 RAG 知识库 · 智能体式的逐篇论文检索 · 一个把每个 `[chunk]` 链接回论文中确切段落的 Citation Canvas（引用画布）· 一条会议级的 Beamer 幻灯片流水线，配有解耦、可编辑的演讲备注。

![Python](https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-React%2019-3178C6?logo=typescript&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-LangGraph-009688?logo=fastapi&logoColor=white)
![Vite](https://img.shields.io/badge/Vite-Tailwind-646CFF?logo=vite&logoColor=white)
![Lint](https://img.shields.io/badge/lint-ruff-261230?logo=ruff&logoColor=white)
![Types](https://img.shields.io/badge/types-mypy%20--strict-2A6DB2)
![Tests](https://img.shields.io/badge/tests-1297%20backend%20%2B%20541%20frontend-brightgreen)
![Status](https://img.shields.io/badge/release-v2.37.1%20(SRS%20v2.37.1)-success)
![License](https://img.shields.io/badge/license-Apache%202.0-blue)

[English](README.md) · [日本語](README.ja.md) · [繁體中文](README.zh-TW.md) · **简体中文**

</div>

---

PaperHub 以**用户体验优先**的理念构建。每个被检索到的文本块都有一条可点击的溯源链路，每个生成步骤都会写入一行审计记录，每个聊天回合都可以仅凭 SQLite 完整重建。单一的聊天界面会把每个回合路由到合适的专家智能体——论文检索、论文问答、自然语言转 SQL 的文献库统计、记忆管理，或幻灯片生成。

## ✨ 它能做什么

🔎 智能体检索 · 🧷 Citation Canvas · 🌍 你的语言 · 📊 文献库统计 · 🧠 记忆 · 🧭 路由 + 追踪 · 🌐 发现 · 📎 带上你的论文 · 🖼️ Beamer 幻灯片 · 🔱 分叉与回溯 · ➗ 数学 · 💾 任意设备 · 🔌 原生 MCP

<details>
<summary><b>每一项的说明 →</b></summary>

<br>

- **🔎 智能体检索。** 每篇论文都有专属子智能体，沿章节目录导航（而非盲目 top-k）；旗舰模型跨论文综合。
- **🧷 Citation Canvas。** 行内 `[chunk:N]` 标记链接回确切段落——点击即可在渲染后的 HTML *以及* 源 PDF 中同时高亮。
- **🌍 你的语言。** 用中文提问就用中文作答——引用照样保留；被记住的「始终用 X 语言回复」会覆盖逐回合检测。
- **📊 文献库统计。**「我有多少篇论文？」→ 在一张表的白名单上运行只读 SQL，并用数字 *以及* 它运行过的 SQL 作答。
- **🧠 记忆。** 事实/偏好可按聊天或处处保存——带安全闸门、LLM 冲突 **取代**，以及可编辑/(取消)启用的管理面板。
- **🧭 可见的路由 + 追踪。** 徽标显示每个回合由哪个智能体 + 模型处理；追踪面板从 SQLite 回放每一步。
- **🌐 发现。** `paper_search` 甚至能把含糊的指代（「那篇人人都引的扩散模型论文」）通过 Web + Semantic Scholar 解析。
- **📎 带上你自己的论文。** 通过 arXiv ID、URL 或 PDF 添加——去重并缓存；后台 **Marker** 工作进程把 PDF 升级为真实的图片、题注与公式转 LaTeX。
- **🖼️ 会议级幻灯片。** 有据可依的 **Beamer 演示文稿**，且 **绝不引用不存在的图**。可选、任意语言的演讲备注；通过聊天 **对单张幻灯片做差异化编辑**；针对 **当前屏幕上的幻灯片** 提问也不改动演示文稿。
- **🔱 分叉与回溯。** 从过往任意一条消息分叉新聊天——预填充、可编辑、不自动发送。分叉会带上参考文献、记忆与演示文稿，并 **嵌套在父级之下**。
- **➗ 数学公式渲染。** LaTeX（`$…$`、`$$…$$`）通过 KaTeX 渲染为真实公式。
- **💾 任意设备。** 会话及其完整记录存在后端而非浏览器。删除聊天会处处删除（带撤销）。
- **🔌 原生 MCP。** 智能体的工具通过 MCP（`/mcp`）提供；外部客户端（Claude Desktop、Cursor）可触达同一接口。

</details>

---

## 📸 截图

**有据可依的回答——每个论断都能追溯回来源。**

| Citation Canvas → 渲染后的 HTML | Citation Canvas → 源 PDF |
| :---: | :---: |
| [![Citation Canvas highlighting a cited chunk in the rendered paper HTML](docs/screenshots/04-citation-canvas-html.png)](docs/screenshots/04-citation-canvas-html.png) | [![Same citation highlighted in the source PDF via a geometry overlay](docs/screenshots/05-citation-canvas-pdf.png)](docs/screenshots/05-citation-canvas-pdf.png) |
| 点击任意 `[chunk]` → 滚动到并高亮 LaTeX 渲染 HTML 中的确切段落。 | ……以及原始 PDF 中的同一段落。没有无依据的论断。 |

**会议级幻灯片——解耦、可选的备注。**

| 生成（仅幻灯片） | 按需添加的演讲备注 |
| :---: | :---: |
| [![Deck chip with a Generate-notes button and the Slides panel open](docs/screenshots/11-slides-generate.png)](docs/screenshots/11-slides-generate.png) | [![Slides panel with the speaker-note pane filled in](docs/screenshots/12-slides-notes-added.png)](docs/screenshots/12-slides-notes-added.png) |
| 一份带真实图片的 Beamer 演示文稿（没有臆造的图形）——先出幻灯片，不含备注。 | 备注是一个可选的后续操作，单独撰写（且可用任意语言）。 |

**文献库智能 + 记忆。**

| 自然语言转 SQL 的文献库统计 | 会话级 + 全局记忆 |
| :---: | :---: |
| [![A stats answer showing the numbers and the read-only SQL it ran](docs/screenshots/09-library-stats-sql.png)](docs/screenshots/09-library-stats-sql.png) | [![Memory Manager with session/global groups and supersede badges](docs/screenshots/10-memory-manager.png)](docs/screenshots/10-memory-manager.png) |
| 「我有多少篇论文？」→ 用具体数字**以及**确切的 SQL 来作答。 | 被记住的事实/偏好，带安全闸门 + 冲突取代历史。 |

**路由 + 可观测性。**

| 路由徽标 | 追踪面板（可回放的 DAG） |
| :---: | :---: |
| [![A chat turn tagged with the routing badge showing intent and model](docs/screenshots/02-routing-badge.png)](docs/screenshots/02-routing-badge.png) | [![Expanded trace panel listing each step with latency and status](docs/screenshots/03-trace-panel.png)](docs/screenshots/03-trace-panel.png) |
| 每个回合都显示由哪个智能体 + 模型处理。 | 每个模型/MCP/流水线步骤都是一行审计记录——完整的 DAG 可从 SQLite 回放。 |

**论文发现 + 带上你自己的论文。**

| 论文检索卡片 | 参考来源抽屉 |
| :---: | :---: |
| [![Paper-search result cards with Add-as-reference buttons](docs/screenshots/07-paper-search-cards.png)](docs/screenshots/07-paper-search-cards.png) | [![Reference Sources drawer listing the session's enabled papers](docs/screenshots/08-reference-sources.png)](docs/screenshots/08-reference-sources.png) |
| 通过 Web + Semantic Scholar 发现论文；智能体会自动添加它的最佳选择。 | 会话范围的参考文献集合，可逐篇启用/移除。 |

<details>
<summary>更多——应用总览 &amp; 用你的语言作答</summary>

| 整体界面 | 用你的语言作答 |
| :---: | :---: |
| [![Full PaperHub window: sidebar, chat, composer](docs/screenshots/01-app-overview.png)](docs/screenshots/01-app-overview.png) | [![A Chinese question answered in Chinese with citation markers preserved](docs/screenshots/06-language-adherence.png)](docs/screenshots/06-language-adherence.png) |
| 一个聊天界面；每个回合都路由到一个专家智能体。 | 用任意语言提问——回答随之跟进，引用照样保留。 |

</details>

---

## 🧱 技术栈

| 领域 | 选型 |
| --- | --- |
| **后端** | Python 3.11 · FastAPI · LangGraph · LiteLLM · SQLite（`aiosqlite`）· Pydantic v2 |
| **前端** | TypeScript · React 19 · Vite · Tailwind · Zustand · `react-markdown` + KaTeX |
| **检索** | SQLite `chunks` 表——通过 `list_sections`/`read_section` 进行智能体式章节导航（无向量库） |
| **幻灯片** | Beamer + `pdflatex`（`metropolis` 主题）· `datalab-to/marker` PDF 解析，作为 docker-compose 服务（可选，可感知 GPU） |
| **LLM** | 默认 Gemini（支持任意 LiteLLM 提供方——小型子智能体、旗舰收尾模型） |
| **工具链** | `uv` · `pytest` · `ruff` · `mypy --strict` · Vitest · ESLint · Conventional Commits |

> [!NOTE]
> 仅本地、单用户。没有鉴权层——把它指向你自己的 LLM 密钥，在你自己的机器上运行即可。

---

## 🚀 快速开始

### 🐳 用 Docker 运行（推荐——只想使用应用）

如果你想*运行* PaperHub 而不是开发它，整套技术栈都在容器中运行——**无需安装 Python、Node 或 LaTeX**。你只需要 [Docker](https://docs.docker.com/get-docker/) 和一个 LLM 密钥。一条 `docker compose up` 就会启动全部五个服务（后端、模型服务器、Marker PDF 解析、Web 检索，以及 Web UI），因此幻灯片（含**中文/CJK**）、RAG 和 Web 发现都开箱即用。

```bash
git clone https://github.com/whats2000/PaperHub.git
cd PaperHub
cp backend/.env.example backend/.env   # then fill in GEMINI_API_KEY (or your provider's key)

docker compose up -d --build           # CPU; first build downloads TeX Live + Marker weights (a few GB, once)
```

打开 **http://localhost:8080**。

> [!NOTE]
> **GPU（可选，NVIDIA + [Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)）：** 更快的 Marker PDF 解析。叠加 GPU 覆盖配置：
> ```bash
> docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
> ```

数据持久化在命名卷中（`paperhub-workspace` = 数据库 + 缓存、模型权重、Marker 权重）。`docker compose down` 会停止它；加上 `-v` 还会一并清除数据。

---

### 🛠️ 从源码运行（用于开发）

**前置条件：** Python 3.11 + [`uv`](https://docs.astral.sh/uv/)、Node 18+，以及一个 LLM API 密钥（默认 Gemini）。**幻灯片生成**还额外需要 `PATH` 上有一个 LaTeX 发行版（`pdflatex`——例如 `winget install MiKTeX.MiKTeX`）；没有它时只有 `slides` 意图会受影响（它会返回一条「请安装一个 LaTeX 发行版」的消息）。PDF 图片/公式提取可以选择性地使用 Docker 化的 `marker` 服务（`docker compose up -d marker`）。

```bash
git clone https://github.com/whats2000/PaperHub.git
cd PaperHub

# Install both halves
cd backend && uv sync          # Python deps from uv.lock
cd ../frontend && npm install  # JS deps from package-lock.json
```

配置你的 LLM 密钥：

```bash
cd backend
cp .env.example .env           # then fill in GEMINI_API_KEY (or your provider's key)
```

#### 运行开发技术栈

**推荐（Windows，一条命令）：** `scripts/start.ps1` 会编排所有同级进程——它先通过 `paperhub-mcp-up` 启动外部 MCP 守护进程（open-websearch），随后启动带热重载的后端：

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

打开 **http://localhost:5173** 并开始聊天。

<details>
<summary>更底层：直接运行 uvicorn</summary>

```bash
cd backend
uv run uvicorn paperhub.app:app --reload --reload-dir src --port 8000
```

注意：这条路径**不会**为你启动 Web 检索守护进程。在 Windows 上，`uvicorn --reload` 运行在 `SelectorEventLoop` 上，因此进程内的自动启动会优雅降级（仅论文）——请自行用 `uv run paperhub-mcp-up` 启动 Web 检索（或使用 `scripts/start.ps1`，它会代劳）。参见 [配置](#️-配置) 下的 Web 检索说明。

</details>

> [!TIP]
> **手头没有 API 密钥？** 用被 mock 的 LLM 来跑通聊天管线（PowerShell）：
> ```powershell
> $env:PAPERHUB_ROUTER_MOCK   = '{"intent":"chitchat","model_tier":"small","confidence":0.9,"reasoning":"dev"}'
> $env:PAPERHUB_CHITCHAT_MOCK = "Hello from PaperHub!"
> uv run uvicorn paperhub.app:app --reload --reload-dir src --port 8000
> ```

---

## ⚙️ 配置

所有设置都在 `backend/.env` 中（在 [`.env.example`](backend/.env.example) 中按功能分组）。你大概率会改动的那些：

| 变量 | 用途 | 默认值 |
| --- | --- | --- |
| `GEMINI_API_KEY` | LLM 提供方凭据（或 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`） | — |
| `PAPERHUB_PAPER_QA_MODEL` | 旗舰收尾模型（跨论文综合） | `gemini/gemini-2.5-pro` |
| `PAPERHUB_PAPER_QA_SUBAGENT_MODEL` | 逐篇论文的章节导航器（轻量） | `gemini/gemini-3.1-flash-lite` |
| `PAPERHUB_SEMANTIC_SCHOLAR_API_KEY` | 更高的 Semantic Scholar 速率限制（可选） | — |

**Web 检索发现（可选）。** 当一个 [`open-websearch`](https://www.npmjs.com/package/open-websearch) 守护进程在 `:3000` 上可触达时，`paper_search` / `paper_suggest` 会获得一个免密钥的多引擎发现步骤。你无需手动安装它——`scripts/start.ps1`（或 `uv run paperhub-mcp-up`）会读取 `mcp_servers.toml`，并通过 `npx -y` 为你启动每一个声明了 `launch` 的 MCP 服务器，`npx -y` 会在首次运行时获取该包（约 25 秒，一次性）：

```bash
cd backend
uv run paperhub-mcp-up          # launches open-websearch on :3000 (skips if already up)
```

当它启动后，后端的 MCP 注册表会自动暴露 `web.search` / `web.fetch`。当它未启动时，智能体会回退到仅论文的流程——无需任何配置。被启动的守护进程是分离（detached）的，因此能在后端 `--reload` 时存活；显式关闭是 `start.ps1` 的职责（否则它们会在重启时清除）。需要 `PATH` 上有 Node 18+。（`paperhub-papers` MCP 接口在进程内于 `/mcp` 提供服务；无需安装。）

---

## 🗺️ 架构（一屏看完）

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

每一次模型调用、MCP 调用和流水线步骤在返回前都会写入一行 `tool_calls`——其状态足以仅凭 `SELECT * FROM tool_calls WHERE run_id = ?` 重建完整的智能体上下文。论文内容是**去重**的：每篇唯一论文只有一行 `paper_content` + 一个缓存目录 + 一套文本块，无论有多少个会话引用它。

完整架构见 [SRS](docs/superpowers/specs/2026-05-17-paperhub-srs.md)。

---

## 📍 状态

| 计划 | 范围 | 状态 |
| --- | --- | --- |
| **A** | 后端基础 + 仅路由的聊天 | ✅ 已完成 |
| **B** | 前端基础（React 界面、SSE、路由徽标、追踪面板） | ✅ 已完成 |
| **C** | 论文流水线 + 研究智能体（导入、paper_search、智能体式 paper_qa、MCP 层、PDF 上传） | ✅ 已完成——已合并（SRS v2.10） |
| **D** | 检索结果 + 参考来源 + Citation Canvas（HTML + PDF 段落高亮） | ✅ 已完成——已合并（SRS v2.13） |
| **E** | SQL 智能体 + `library_stats`（sqlite MCP）+ 会话/全局记忆治理（闸门、冲突取代、记忆管理 UI） | ✅ 已完成——已合并（SRS v2.17） |
| **F** | 幻灯片流水线 + 报告智能体——Marker 解析（F2/F2.1）、博士级幻灯片智能体（F3）、解耦的可选备注 + 差异化编辑 + 长度预算（F4）、会议级元数据标题页 + 标题/样式定制（F4.2） | ✅ 已完成——已合并（SRS v2.22） |
| **F5** | 幻灯片演示模式（观众弹出窗口 + `BroadcastChannel` 同步 + 演讲者驾驶舱）+ 演讲中问答 + 输入框语音输入 | ✅ 已完成——已合并（SRS v2.26） |
| **G** | 前端 UI 国际化（i18n：`en` / `zh-TW` / `zh-CN` / `ja`）+ 账户菜单（语言／主题切换、关于）+ 以数据库为后盾的运行期设置面板 | ✅ 完成 — 已合并（SRS v2.31） |
| **H** | 对比视图 + 文件系统 / `paperhub.*` MCP | 🔜 已规划 |

每个计划都独立交付可工作、可测试的软件。计划存放在 [`docs/superpowers/plans/`](docs/superpowers/plans/)。

---

## 🧑‍💻 开发

PaperHub 按 spec → plan → TDD 构建，采用子智能体驱动的实现，并对每个任务做规范合规性 + 代码质量审查。

**后端质量闸门**（从 `backend/`）：

```bash
uv run pytest          # 1104 tests, hermetic
uv run ruff check src tests
uv run mypy src        # --strict
```

**前端质量闸门**（从 `frontend/`）：

```bash
npm test               # Vitest + RTL + MSW (386 tests)
npm run typecheck      # tsc --strict
npm run lint           # ESLint flat config
npm run build          # Vite production build
```

**回放任意过往聊天回合**（从 SQLite，用于调试智能体流程）：

```bash
cd backend
uv run paperhub-replay --run-id 1
```

**端到端基准测试**——`pytest` 证明接线正确；[`backend/benchmark/`](backend/benchmark/) 测试套件证明*行为*正确。它把**实时**后端当作一个模拟用户来驱动（附加缓存论文 → 通过 `/chat` 路由提示词），收集依据证据（被引文本块文本 + 智能体追踪），并就正确性 + 依据为每个用例打 **0/1** 分——由人工或通过一个 **LLM-as-Judge**（固定温度、严格依据）。用例是配置驱动的（TOML），因此你可以编写自己的：

```bash
# with the backend running (scripts/start.ps1), from backend/:
scripts/run-benchmark.ps1 -Judge            # 20-case eval (16 paper_qa + 4 slides) + LLM judge
scripts/run-benchmark.ps1 -Resume <prior.json>   # retry only failed cases after a drop
```

> 参与贡献的 AI 智能体：请先阅读 [CLAUDE.md](CLAUDE.md)——它承载了各项约定、fix-now 策略，以及智能体流程可观测性规则。

---

## 📂 仓库结构

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

`workspace/`（已被 gitignore）保存运行时状态——SQLite 数据库和论文缓存。

---

## 📖 文档

- **[系统需求规格说明书（System Requirements Specification）](docs/superpowers/specs/2026-05-17-paperhub-srs.md)**——权威的架构、模式、范围和验收标准（已交付至 **v2.37.1**）。
- **[实现计划](docs/superpowers/plans/)**——每个子项目一份，均通过 TDD 执行。
- **[后端开发者文档](backend/README.md)**——后端专属说明。

---

## 📚 引用

如果你在研究中使用了 PaperHub 或在其基础上构建，请引用它：

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

## 📄 许可证

[Apache License 2.0](LICENSE) — © PaperHub contributors。你可以在该许可证条款下使用、修改和分发本软件，其中包含贡献者的明确专利权授予。
