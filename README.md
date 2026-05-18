# PaperHub

> A paper-aware chat client with multi-agent tool routing, an in-repo RAG knowledge base, a multi-paper slide pipeline, and a Citation Canvas that traces every cited chunk back to its source.

PaperHub is built UX-first. Every retrieved chunk has a clickable provenance trail, every generation step writes an audit row, and every chat turn is reconstructible from SQLite alone. The backend assembles a small set of capabilities — paper ingest, RAG, NL→SQL, slide generation — behind a single chat interface with intent-routed agents.

---

## Status

| Layer | Status |
| --- | --- |
| Backend foundation (FastAPI + LangGraph + SQLite + Tracer + Router) | Plan A complete |
| Paper Pipeline + Research Agent | Plan C — not started |
| Frontend (React + Vite + Tailwind + Zustand) | Plan B — not started |
| SQL Agent + library_stats | Plan E — not started |
| Slide Pipeline + Report Agent | Plan F — not started |
| Compare view + MCP surfaces | Plan G — not started |

See the [implementation plan](#implementation-plan) section below for the full decomposition.

---

## At a glance

- **Language:** Python 3.11 (backend), TypeScript + React (frontend, Plan B)
- **Backend:** FastAPI, LangGraph, LiteLLM, SQLite (`aiosqlite`), Pydantic v2
- **RAG:** Chroma + `sentence-transformers/BAAI/bge-small-en-v1.5` + cross-encoder rerank (Plan C)
- **Slides:** framework deliberately deferred — Marp / Slidev / Beamer-via-LangGraph (Plan F)
- **Tooling:** `uv` for Python, `pytest` + `ruff` + `mypy --strict`, Conventional Commits
- **Auth model:** local-only single-user; no auth surface in Plan A–G

---

## Quick start

After cloning, install both halves:

```powershell
cd backend; uv sync                  # Python deps from uv.lock
cd ..\frontend; npm install          # JS deps from package-lock.json
```

### Run the dev stack

Open two terminals (or use a tmux/Windows Terminal split).

**Terminal 1 — backend** (FastAPI + LangGraph, hot-reload on save, port 8000):

```powershell
cd backend
uv run uvicorn paperhub.app:app --reload --port 8000
```

**Terminal 2 — frontend** (Vite + React, hot-reload on save, port 5173):

```powershell
cd frontend
npm run dev
```

Open `http://localhost:5173`. The frontend posts to the backend via CORS.

To exercise the chat path without configuring an LLM key, set the mock env vars before starting the backend:

```powershell
$env:PAPERHUB_ROUTER_MOCK   = '{"intent":"chitchat","model_tier":"small","confidence":0.9,"reasoning":"dev"}'
$env:PAPERHUB_CHITCHAT_MOCK = "Hello from PaperHub!"
uv run uvicorn paperhub.app:app --reload --port 8000
```

To run against a real LLM (Gemini by default), copy `backend/.env.example` to `backend/.env`, fill in `GEMINI_API_KEY`, and start the backend without the mock vars.

### One-shot smoke scripts

Backend-only mocked round-trip + SQLite replay:

```powershell
cd backend
.\scripts\smoke_chat.ps1
```

Backend-only against a real LLM (requires `backend/.env`):

```powershell
cd backend
.\scripts\smoke_chat_real.ps1
```

Full end-to-end smoke (boots backend + frontend, asserts SSE round-trip, exits non-zero on failure — suitable for CI):

```powershell
.\scripts\smoke_e2e.ps1
```

### Replay a past chat turn from SQLite

```powershell
cd backend
uv run paperhub-replay --run-id 1
```

Full quality gates (must pass before any PR):

```powershell
uv run pytest -v
uv run ruff check src tests
uv run mypy src
```

---

## Architecture (one screen)

```
┌─────────────────┐       SSE      ┌──────────────────────────────────────────┐
│  React shell    │ <───────────── │ FastAPI · POST /chat                     │
│  (Plan B)       │                │                                          │
│  - Composer     │                │ ┌──────────────────────────────────────┐ │
│  - Routing badge│                │ │ LangGraph turn                       │ │
│  - Trace panel  │                │ │   Router ─► chitchat | paper_qa |    │ │
│  - Canvas       │                │ │             paper_search | slides |  │ │
│  - Compare      │                │ │             library_stats            │ │
└─────────────────┘                │ └──────────────────────────────────────┘ │
                                   │      │                                    │
                                   │      ▼                                    │
                                   │ ┌─────────┐  ┌──────────┐  ┌──────────┐ │
                                   │ │ LiteLLM │  │ Chroma   │  │ SQLite   │ │
                                   │ │ adapter │  │ (RAG)    │  │ (audit + │ │
                                   │ │         │  │          │  │  schema) │ │
                                   │ └─────────┘  └──────────┘  └──────────┘ │
                                   └──────────────────────────────────────────┘
```

Every model call, MCP call, and pipeline step writes a `tool_calls` row before returning. Compare-mode turns share one `run_id` with a `branch` discriminator (`'A'`/`'B'`). Paper content is **deduplicated**: each unique paper has one `paper_content` row, one cache dir under `workspace/papers_cache/`, and one set of chunks + Chroma vectors, regardless of how many sessions reference it.

Full architecture lives in the SRS — see [Documentation](#documentation).

---

## Implementation plan

The SRS is decomposed into 7 sequential implementation plans. Each ships working, testable software on its own.

| # | Plan | Ships |
| --- | --- | --- |
| **A** | Backend foundation + Router-only chat | FastAPI app, 7-table SQLite schema, tracer, LiteLLM adapter, Router + chitchat, SSE /chat, replay CLI |
| **B** | Frontend foundation | Vite + Tailwind + Zustand shell, Sidebar / Composer / MessageBubble, SSE consumer, Routing Badge, Trace panel |
| **C** | Paper Pipeline + Research Agent | Cache-aware ingest, content_key cache lookup, Chroma RAG, paper_search + paper_qa |
| **D** | Search results + Reference Sources + Citation Canvas | UC-1 / UC-2 / UC-3 end-to-end in browser |
| **E** | SQL Agent + library_stats | In-process sqlite MCP, sqlglot guard, NL → SQL |
| **F** | Slide Pipeline + Report Agent | Structure planning, per-section fan-out, figure-path resolution across the cache boundary |
| **G** | Compare view + paperhub.* MCP + filesystem MCP | Compare composer toggle, branch fan-out, external MCP surfaces |

Each plan lives under [`docs/superpowers/plans/`](docs/superpowers/plans/).

---

## Repository layout

```
.
├── backend/
│   ├── src/paperhub/         # FastAPI app + agents + tracer + LiteLLM adapter
│   ├── tests/                # pytest suite (34+ tests, hermetic)
│   ├── scripts/              # smoke_chat.ps1 (mock) + smoke_chat_real.ps1 (live)
│   └── pyproject.toml        # uv project, mypy --strict, ruff config
├── frontend/                 # React + Vite + Tailwind (Plan B)
├── docs/
│   └── superpowers/
│       ├── specs/            # SRS — authoritative architecture document
│       └── plans/            # implementation plans, one per sub-project
├── reference/                # copied source from paper2slides-plus + Intro2GenAI-hw1
├── CLAUDE.md                 # AI-agent orientation for this repo
└── README.md                 # this file
```

`workspace/` (gitignored) holds runtime state — the SQLite database, the future `papers_cache/`, and the future Chroma index.

---

## Documentation

- **System Requirements Specification (SRS)** — [docs/superpowers/specs/2026-05-17-paperhub-srs.md](docs/superpowers/specs/2026-05-17-paperhub-srs.md). The authoritative source for architecture, schema, scope, and acceptance criteria. Read this before changing anything load-bearing.
- **Implementation plans** — [docs/superpowers/plans/](docs/superpowers/plans/). One plan per sub-project; each plan executes via TDD with subagent-driven implementation + spec-compliance + code-quality reviews per task.
- **Backend developer docs** — [backend/README.md](backend/README.md).

---

## Acceptance criteria

From SRS §I-8 — each plan ticks off a subset:

1. Router top-1 accuracy ≥ 80 % on the 16-prompt fixture (Plan A — fixture in place; live-mode verifiable with `PAPERHUB_ROUTER_LIVE=1`)
2. Re-importing a paper into a second session is instant (cache hit, no re-download / re-embed) — Plan C
3. Multi-paper Q&A yields chunks from ≥ 2 distinct `paper_content.id` — Plan C / D
4. Every chat turn produces a `run_id`; `paperhub-replay` reconstructs the full step list — **Plan A complete**
5. Compare-mode renders two side-by-side responses with independent trace panels — Plan G
6. No silent failure: scripted error case (LLM 500 / missing Chroma) shows a visible error in chat AND a red row in the Trace panel — partial in Plan A (the architecture supports it; UI tests in Plan B+)
7. SSE freshness: tokens arrive ≤ 60 s wall-clock after request — Plan C+

---

## License

Not yet specified.
