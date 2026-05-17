# PaperHub

> Paper knowledge base + research assistant — a modern-stack successor to [paper2slides-plus](https://github.com/whats2000/paper2slides-plus).

[![CI](https://img.shields.io/github/actions/workflow/status/whats2000/PaperHub/ci.yml?branch=main&label=CI&logo=github)](https://github.com/whats2000/PaperHub/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/release/python-3120/)
[![Node](https://img.shields.io/badge/node-20+-339933?logo=node.js&logoColor=white)](https://nodejs.org/)
[![Type-checked: mypy --strict](https://img.shields.io/badge/types-mypy%20--strict-2ea44f)](https://mypy.readthedocs.io/)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](#contributing)
[![Spec: v1.11](https://img.shields.io/badge/spec-v1.11-orange)](docs/superpowers/specs/2026-05-17-paperhub-srs.md)

PaperHub turns a chat box into a research workspace: import an arXiv paper (LaTeX source, figures, bibliography preserved) and ask grounded questions about it in natural language — every answer carries inline `(§section, p.page)` citations, and every model call lands in an auditable trace log.

It also serves as a concrete reference implementation of a **multi-model, tool-routing AI platform**: a Router Agent classifies the user's request, dispatches to a specialised sub-agent (Research, SQL, Report), and routes through scope-checked MCP tools — so you can see, *and audit*, which tool answered what.

## Features

- **High-fidelity import** — three-tier source ladder (see [SRS §1.1](docs/superpowers/specs/2026-05-17-paperhub-srs.md#11-first-principle--preserve-paper2slides-plus-capabilities)):
  1. **LaTeX source** via [`takashiishida/arxiv-latex-mcp`](https://github.com/takashiishida/arxiv-latex-mcp) + raw e-print archive (`.tex` + figures + `.bib` + `.sty` preserved on disk — needed for slide generation)
  2. **Marker** ([`datalab-to/marker`](https://github.com/datalab-to/marker)) equation-preserving Markdown — for local PDFs / non-arXiv DOIs (containerised; Phase B)
  3. Raw text extraction — lossy last resort, flagged as low-fidelity
- **Grounded RAG Q&A** — two-stage retrieval (dense top-`min(50, ⌈corpus/3⌉)` → cross-encoder rerank → top-5) with mandatory page-level source citations. Refuses to answer if no relevant chunks indexed.
- **Multi-provider LLM** via [LiteLLM](https://github.com/BerriAI/litellm) — Anthropic Claude, OpenAI, Google Gemini, Ollama. Set one API key and matching model IDs.
- **Tool-Call Tracer** — every MCP call, scope rejection, LLM call writes one row to `tool_calls` with redacted args; the Trace UI surfaces it inline next to the answer.
- **Scope-checked MCP** — every outbound tool call validated against a typed `McpToolScope` before dispatch. Filesystem paths refuse `..` traversal (CVE-2025-53109 regression test). Bytes redacted before the audit log.
- **Modern chat UI** — React 18 + Vite + Tailwind 4 + SSE streaming + zustand state.
- **Strict typing** — Pydantic v2 + TypedDict + `mypy --strict` clean. `Any` prohibited in public interfaces.
- **CI-gated** — `ruff` + `mypy --strict` + `pytest` (backend) + `tsc` + `eslint` + `vitest` + `vite build` (frontend) on every PR.

## Status

| Phase | Scope | Status |
|---|---|---|
| **A — Foundations + paper_qa vertical slice** | Settings, SQLite schema, Pydantic models, LiteLLM adapter, YAML prompt registry, Chroma vector store, Tool-Call Tracer, MCP scope-checker, RAG pipeline, Router (binary), Research Agent, `/papers/import` LaTeX-first, `/chat` SSE, chat UI, CI | ✅ **Done** (tag `phase-a-complete`, 94 unit + 3 live-e2e tests passing) |
| **B — Multi-intent platform** | Marker container, agentic search→read→decide import flow, full 6-intent Router, SQL Agent (NL2SQL), Report Agent + slide pipeline + Slide Editor UI (paper2slides-plus port), relation analysis, projects | Pending |
| **C — Evaluation + polish** | LLM-as-judge eval harness with Cohen's κ calibration, NFR-01 latency tuning, batch import, cost dashboard | Pending |

See [docs/superpowers/specs/2026-05-17-paperhub-implementation-design.md](docs/superpowers/specs/2026-05-17-paperhub-implementation-design.md) for the 3-phase implementation design and per-phase FR mapping.

## Quick start

### Prerequisites

- **Python 3.12** (via [pyenv](https://github.com/pyenv/pyenv) or your distro)
- **[uv](https://github.com/astral-sh/uv)** (`pip install uv` or `winget install astral-sh.uv`) — PaperHub uses `uv` exclusively, never `pip`
- **Node.js 20+** + npm (for the frontend)
- **PowerShell 5.1+** (Windows) or any POSIX shell
- An API key for one of: Anthropic / OpenAI / **Google Gemini** (the default `.env.example` preset)
- *(Optional)* Docker — for running [GROBID](https://grobid.readthedocs.io) (Phase B reference extraction) and Marker (Phase B PDF extraction)

### Setup

```powershell
# Clone
git clone https://github.com/whats2000/PaperHub.git
cd PaperHub

# Backend
cd backend
uv sync                                          # installs Python deps + creates .venv
Copy-Item .env.example .env                      # edit .env to add your API key
                                                  # default preset is Gemini — set GEMINI_API_KEY
uv run pytest -m "not e2e" -q                    # 94 tests pass in ~10 s
cd ..

# Frontend
cd frontend
npm install
npm run test                                     # 5 vitest tests pass
cd ..
```

### Run it

```powershell
# Terminal 1 — backend
cd backend
uv run uvicorn paperhub.api.app:create_app --factory --port 8765 --reload

# Terminal 2 — frontend
cd frontend
npm run dev                                      # opens http://localhost:5173
```

Then in the chat UI: ask a chitchat question first (instant — routes to chitchat); then import a paper and ask about it:

```powershell
# Import a real arXiv paper (Tier 1 LaTeX, ~14 s — downloads + unpacks the e-print)
curl -X POST http://127.0.0.1:8765/papers/import `
     -H "Content-Type: application/json" `
     -d '{"arxiv_id":"1706.03762"}'

# Then ask in the chat UI or via curl:
curl -N -X POST http://127.0.0.1:8765/chat `
     -H "Content-Type: application/json" `
     -d '{"message":"What architecture does this paper propose?","session_id":null}'
```

The answer streams back with inline `(§sec, p.N)` citations and the Tool-Trace panel shows the routing decision + retrieval step in real time.

## Development

```powershell
cd backend

# Quality gates (also run in CI)
uv run ruff format --check .                     # formatter check
uv run ruff check .                              # linter
uv run mypy                                      # type-check (--strict)
uv run pytest -m "not e2e" -q                    # unit + integration tests

# Live end-to-end tests (need GEMINI_API_KEY or ANTHROPIC_API_KEY + network)
uv run pytest -m e2e -v

# Apply migrations to a fresh DB
uv run python -c "from paperhub.data.db import apply_migrations; from pathlib import Path; apply_migrations(Path('.paperhub-workspace/paperhub.db'))"
```

```powershell
cd frontend

npm run typecheck                                # tsc --noEmit
npm run lint                                     # eslint
npm run test                                     # vitest
npm run build                                    # production build → dist/
```

### Pre-commit hooks

```powershell
pip install pre-commit                           # one-time
pre-commit install                               # in repo root — installs the hook
```

Runs `ruff format --check` + `ruff check` + `mypy --strict` on every commit (backend-only — frontend gates run in CI).

### Project layout

```
PaperHub/
├── backend/                                     # Python 3.12 + FastAPI + LangGraph + LiteLLM
│   ├── paperhub/
│   │   ├── api/                                 # FastAPI app, /health, /chat (SSE), /papers/import
│   │   ├── agents/                              # Router, Research Agent, AgentState
│   │   ├── llm/                                 # LlmAdapter Protocol + LiteLlmAdapter + prompt registry
│   │   ├── rag/                                 # Chunker, Embedder, Retriever (two-stage funnel)
│   │   ├── mcp/                                 # Scope-checker, MCP client, FastMCP tool wraps
│   │   ├── data/                                # SQLite migrations, Pydantic models, Chroma driver
│   │   ├── tracing/                             # Tool-Call Tracer + secret/path redactor
│   │   └── config.py                            # Typed Settings (pydantic-settings)
│   ├── tests/                                   # pytest (mirrors paperhub/ layout)
│   └── .env.example                             # Gemini-preset env template
├── frontend/                                    # React 18 + Vite + Tailwind 4 + TypeScript
│   └── src/{App,components/{Sidebar,ChatPane},api/,store/}
├── docs/
│   ├── superpowers/specs/                       # SRS (v1.11) + implementation design
│   ├── superpowers/plans/                       # Per-phase implementation plans
│   └── KNOWN-TYPE-GAPS.md                       # Registry of all # type: ignore comments
├── scripts/
│   └── smoke.ps1                                # Boots backend + frontend + runs e2e
├── .github/workflows/ci.yml                     # GitHub Actions
├── CLAUDE.md                                    # House rules for AI sessions in this repo
└── README.md                                    # this file
```

## Architecture at a glance

```
                          ┌──────────────────────────────────┐
                          │  React + Vite + Tailwind UI      │
                          │  Sidebar · ChatPane · TraceView  │
                          └─────────────────┬────────────────┘
                                            │ SSE (POST /chat)
                                            ▼
       ┌────────────────────────────────────────────────────────────┐
       │  FastAPI app  ─  Router Agent (LiteLLM structured output)  │
       │       │                                                    │
       │       ├──> Research Agent ──> Retriever (RAG, 2-stage)     │
       │       │                       └─ Chroma + bge embedder     │
       │       │                                                    │
       │       └──> chitchat path                                   │
       │                                                            │
       │  Tool-Call Tracer (every step → tool_calls + SSE event)    │
       └────────────────────────────────────────────────────────────┘
                                            │
                                            ▼
       ┌────────────────────────────────────────────────────────────┐
       │  MCP scope-checker + dispatcher                            │
       │  arxiv_latex (Tier 1)  │  arxiv (Tier 3 fallback)  │  grobid  │
       │  [Phase B] pdf_extract (Marker container)                  │
       │  [Phase B] latex · filesystem · sqlite                     │
       └────────────────────────────────────────────────────────────┘
                                            │
                                            ▼
                                  arxiv.org · Anthropic / OpenAI / Gemini
```

## Configuration

All config flows through `paperhub.config.Settings` (pydantic-settings, prefix `PAPERHUB_`). See `backend/.env.example` for the full list — common ones:

| Variable | Default | What it does |
|---|---|---|
| `PAPERHUB_WORKSPACE_ROOT` | (required) | Where papers / Chroma / SQLite live |
| `PAPERHUB_DB_PATH` | (required) | SQLite file path |
| `PAPERHUB_ROUTER_MODEL` | `claude-haiku-4-5` | Small/cheap classifier (LiteLLM model ID) |
| `PAPERHUB_GENERATION_MODEL` | `claude-sonnet-4-6` | Flagship for answer generation |
| `PAPERHUB_JUDGE_MODEL` | `claude-haiku-4-5` | Eval-harness judge (MUST differ from generation) |
| `PAPERHUB_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Local sentence-transformer |
| `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | (one required) | LiteLLM picks provider by model-ID prefix |
| `PAPERHUB_GROBID_URL` | `http://localhost:8070` | Optional — Phase B reference extraction |
| `PAPERHUB_VECTOR_BACKEND` | `chroma` | Or `sqlite-vec` (opt-in) |

## Contributing

PRs welcome. Before you start:

1. **Read [`CLAUDE.md`](CLAUDE.md)** — house rules (conventions, the §1.1 First Principle, MCP discipline). Yes, even if you're not an AI; the conventions apply to humans too.
2. **Read [SRS §1.1](docs/superpowers/specs/2026-05-17-paperhub-srs.md#11-first-principle--preserve-paper2slides-plus-capabilities)** — the binding constraint that paper2slides-plus capabilities must be preserved, not simplified away.
3. **Test discipline:** for any new behavior crossing an external boundary (LLM call, MCP server, network API), add BOTH a `pytest` unit test (with `FakeAdapter` / mocked dispatcher) AND a live-e2e test in `tests/integration/` marked `@pytest.mark.e2e`. "Unit tests pass" is not enough.
4. **Conventional Commits:** `action(scope): what you do` — imperative, lowercase scope. See recent `git log --oneline` for examples.
5. **Strict typing:** `mypy --strict` clean. Every `# type: ignore[<code>]` MUST have a row in [`docs/KNOWN-TYPE-GAPS.md`](docs/KNOWN-TYPE-GAPS.md) with rationale + removal trigger.
6. **All paper-import / extraction work MUST follow the three-tier source-fidelity ladder.** Lower-fidelity extractors (`pdfminer` raw text, HTML→Markdown) are prohibited as primary artifact sources.

### Where to start

- **Phase B work** — start with the implementation design's [§4 phase table](docs/superpowers/specs/2026-05-17-paperhub-implementation-design.md) and pick a deliverable. Marker container deployment, the agentic search→read→decide flow, and the slide pipeline port from `reference/paper2slides-plus/` are the natural first ones.
- **Bug reports / quality fixes** — file an issue with the failing command + expected vs actual output; we'll triage.
- **Adding LLM providers** — LiteLLM already supports 100+; usually just an env-var + model-ID change is enough. Add a `.env.example` preset and a live-e2e test that skips when the relevant API key is missing.

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgements

- [paper2slides-plus](https://github.com/whats2000/paper2slides-plus) — the predecessor whose slide pipeline + LaTeX-first preference PaperHub inherits verbatim
- [`takashiishida/arxiv-latex-mcp`](https://github.com/takashiishida/arxiv-latex-mcp) — Tier 1 LaTeX extractor
- [`datalab-to/marker`](https://github.com/datalab-to/marker) — Tier 2 equation-preserving PDF→Markdown
- [`blazickjp/arxiv-mcp-server`](https://github.com/blazickjp/arxiv-mcp-server) — Tier 3 markdown fallback
- [LiteLLM](https://github.com/BerriAI/litellm) — multi-provider LLM client
- [LangGraph](https://github.com/langchain-ai/langgraph) — agent state + graph orchestration
- [Chroma](https://github.com/chroma-core/chroma) — local vector store
- [sentence-transformers](https://github.com/UKPLab/sentence-transformers) + [BAAI BGE](https://huggingface.co/BAAI/bge-small-en-v1.5) — embedder + reranker
- [GROBID](https://grobid.readthedocs.io/) — reference-list extraction (Phase B)
