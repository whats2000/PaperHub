---
title: PaperHub — Implementation Design
status: approved
created: 2026-05-17
scope: full-system implementation
companion_to: 2026-05-17-paperhub-srs.md
---

# PaperHub — Implementation Design

This document is the implementation-design companion to [the PaperHub SRS v1.6](./2026-05-17-paperhub-srs.md). The SRS defines *what* PaperHub must do (12 FRs, 11 NFRs, 12 acceptance criteria); this document defines *how* we will build it — repository layout, cross-cutting foundations, phasing, agent topology, data schema, MCP integration, React UI, and testing strategy.

**v1.6 alignment note.** This design previously enumerated 9 phases and 8 client-side MCP tools and described a hand-rolled `AnthropicAdapter`. Per SRS v1.6 it now describes **3 phases (A, B, C)**, **5 client-side MCP tools** (`arxiv`, `grobid`, `latex`, `filesystem`, `sqlite`), and a **LiteLLM-backed `LlmAdapter`**. Sections updated below are §2 (repo layout), §3 (foundations + model picks), §4 (phase table), §7 (MCP table), §11 (SRS traceability). Sections not affected (agent topology, data schema, frontend, testing) are unchanged.

The design was authored from the SRS. Two prior projects (`paper2slides-plus`, `Intro2GenAI-hw1`) sit under a gitignored `reference/` folder and are consulted for **UX inspiration and pattern reference only** — no code-import path. See §8.

---

## 1. Architectural decisions locked before design

These decisions framed the rest of the design and are not revisited below.

| Decision | Choice | Rationale |
|---|---|---|
| Backend stack | Python 3.12 + FastAPI + LangGraph | SRS §Part 3; LangGraph gives explicit state and per-step tracing hooks. |
| Frontend stack | React 18 + Vite + Tailwind, custom (Open WebUI-style layout) | SRS NFR-05; custom UI needed for relation graph + slide editor + trace viewer. |
| Reuse posture | Reference projects are **UX inspiration only**, not a reuse mandate. Every module is authored fresh against the SRS. | Avoids inheriting shape decisions made for different problems. |
| Scope cut | None. All 12 FRs are in scope for v1. | User requirement: full system, no MVP slice deferred. The phasing in §4 is implementation **order**, not feature deferral — every phase ships before v1 is declared done. |
| Timeline posture | No fixed deadline — build it right. | Permits proper tests, strict typing, and full NFR coverage from day 1. |
| Implementation strategy | **Vertical-slice expansion**: ship one complete end-to-end path first (`paper_qa`), then widen one intent at a time. | Integration bugs surface early; every phase ends with a runnable system. |

## 2. Repository layout

```
paperhub/
├── backend/                          # Python 3.12, uv-managed
│   ├── pyproject.toml
│   ├── paperhub/
│   │   ├── __init__.py
│   │   ├── api/                      # FastAPI surface
│   │   │   ├── app.py                # ASGI app + middleware
│   │   │   ├── routes/               # chat, papers, projects, trace, eval
│   │   │   └── schemas.py            # Pydantic request/response models
│   │   ├── agents/
│   │   │   ├── router.py             # Router Agent
│   │   │   ├── research.py           # Research Agent (RAG)
│   │   │   ├── sql_agent.py          # NL2SQL Agent
│   │   │   ├── report.py             # Report / Slides Agent
│   │   │   └── state.py              # LangGraph shared state (TypedDict)
│   │   ├── llm/
│   │   │   ├── adapter.py            # LlmAdapter Protocol + LiteLlmAdapter + FakeAdapter
│   │   │   ├── prompts.py            # YAML-driven prompt manager
│   │   │   └── prompts.yaml
│   │   ├── rag/
│   │   │   ├── chunker.py
│   │   │   ├── embedder.py
│   │   │   └── retriever.py          # 2-stage: dense search + reranker
│   │   ├── mcp/
│   │   │   ├── server.py             # custom paperhub.* MCP server (always ours)
│   │   │   ├── client.py             # MCP client + scope-checker (always ours)
│   │   │   ├── scopes.py             # typed McpToolScope declarations
│   │   │   ├── launchers.yaml        # how each MCP server is spawned (path + args + env)
│   │   │   └── tools/                # v1.6: 3 servers we wrap/build; 2 are reused upstream
│   │   │       ├── grobid_server.py       # WRAP — ~40 LoC over kermitt2/grobid-client-python
│   │   │       ├── sqlite_server.py       # WRAP — ~50 LoC over sqlite3 (table allow-list + schema)
│   │   │       └── latex_server.py        # BUILD — ~150 LoC pdflatex + chktex in sandbox
│   │   │   # NOT in repo (configured in launchers.yaml, run via uv/uvx/npx):
│   │   │   #   arxiv       — REUSE blazickjp/arxiv-mcp-server
│   │   │   #   filesystem  — REUSE @modelcontextprotocol/server-filesystem (pinned post-CVE-2025-53109/53110)
│   │   │   # Dropped in v1.6 (may return in v2): semantic_scholar, crossref, web_search
│   │   ├── data/
│   │   │   ├── db.py                 # SQLite (+ DuckDB optional) connection
│   │   │   ├── models.py             # Pydantic data models
│   │   │   ├── migrations/           # raw SQL files, applied at startup
│   │   │   └── vectors.py            # Chroma (default) / sqlite-vec (opt-in) driver
│   │   ├── tracing/
│   │   │   ├── tracer.py             # Tool-Call Tracer (decorator + ctx mgr)
│   │   │   └── redactor.py           # secret/path redaction
│   │   ├── eval/                     # FR-12 evaluation harness
│   │   │   ├── tasks.yaml            # task suite definitions
│   │   │   └── runner.py
│   │   └── config.py                 # settings via pydantic-settings + .env
│   └── tests/                        # pytest, mirrors paperhub/ layout
└── frontend/                         # React 18 + Vite + Tailwind
    ├── package.json
    ├── src/
    │   ├── App.tsx
    │   ├── components/
    │   │   ├── Sidebar/              # chat history + projects
    │   │   ├── ChatPane/             # streaming, citations, tool-trace inline
    │   │   ├── PaperPanel/           # list + Cytoscape relation graph
    │   │   ├── SlideEditor/          # page-level editing
    │   │   └── TraceViewer/          # tool-call DAG, single-step replay
    │   ├── api/                      # typed client generated from OpenAPI
    │   └── store/                    # zustand or similar
    └── tests/                        # vitest + react-testing-library
```

## 3. Cross-cutting foundations

Built once in Phase 0 and used by every later phase. Each is independently testable and has a stable typed interface so later phases do not reshape it.

| Foundation | Purpose | Notes |
|---|---|---|
| `agents/state.py` | `TypedDict` for LangGraph shared state — `messages`, `routing_decision`, `tool_results`, `run_id`, `step_index`. | Per NFR-11, strict typing throughout. |
| `tracing/tracer.py` | A context manager + decorator wrapping every model call, tool call, and MCP call. Writes one row per step to `tool_calls`. | One source of truth for FR-11 trace UI and FR-12 eval. |
| `llm/adapter.py` | `LlmAdapter` Protocol with one async interface `generate(messages, model_tier, response_model) -> BaseModel`. **Production implementation = `LiteLlmAdapter` — a thin wrapper over `litellm.acompletion()`** that does provider routing + structured-output via `response_format={"type":"json_schema","json_schema":{"name":..., "strict": True, "schema": Model.model_json_schema()}}` and parses the result back into the typed `response_model`. `FakeAdapter` returns canned Pydantic instances for tests. | LiteLLM handles Anthropic / OpenAI / Ollama + retry/fallback/cost-tracking, so PaperHub owns only ~80 LoC of glue. Satisfies NFR-03 (pluggable providers) via LiteLLM's 100+ provider matrix. |
| `llm/prompts.py` | YAML-loaded prompt registry with versioning, variable substitution, A/B slots. | All prompts live in `prompts.yaml`; no inline strings in agent code. |
| `data/models.py` | Pydantic models for every persisted entity: `Paper`, `Chunk`, `Project`, `Note`, `ToolCall`, `RunMetadata`, `RoutingDecision`. | Owned by data layer; imported everywhere. |
| `data/vectors.py` | Vector-store driver behind a narrow interface (`add`, `search`, `delete_by_paper`). **Default backend on all platforms**: **Chroma** (clean Windows wheels, no native build step, persistent local mode). **Opt-in alternative**: `sqlite-vec` (the maintained successor to `sqlite-vss`, with reliable Windows binaries) for users who want everything in one `.db` file. Selection is via `Settings.vector_backend ∈ {"chroma", "sqlite-vec"}`; no agent code changes. | Both backends implement the same Pydantic-typed interface. The default is the lowest-friction Windows path; `sqlite-vec` is enabled per-user, not assumed. |
| `mcp/client.py` scope-checker | Validates every MCP call against the declared allow-list **before** dispatching to the MCP server. | Enforces NFR-10; rejections recorded in `tool_calls`. |
| `config.py` | `pydantic-settings`-based config; loads `.env`; exposes a typed `Settings` singleton. | All API keys, model names, paths, MCP scopes flow through here. |

**Two properties baked in from Phase 0** (not added retroactively):

1. **Strict typing.** Every public function signature, FastAPI route, agent step, and LangGraph state field is Pydantic / TypedDict / dataclass. `mypy --strict` (or `pyright`) is on in CI from commit 1. `Any`, `object`, untyped `dict`/`list` are prohibited in public interfaces; `dict[str, Any]` is allowed only at the I/O boundary with an external untyped source and must be parsed into a typed model before crossing one function call. **Upstream-boundary exception (per SRS NFR-11):** narrow `# type: ignore[<specific-error-code>]` is permitted at LangGraph and MCP-SDK call sites where the upstream type stubs are themselves incomplete; each occurrence cites the mypy error code, is confined to a single statement, and is logged in `docs/KNOWN-TYPE-GAPS.md` (created in Phase 0) so the team can remove the ignore comment once upstream fixes ship. Bare `# type: ignore` with no code fails CI.
2. **Every call traced.** The Tool-Call Tracer wraps every model call, tool call, and MCP call from Phase 1 onward. There is no "I'll add observability later" phase.

**Default model picks (pinned in Phase 0, overridable per-user via `Settings`):**

| Slot | Default | Rationale |
|---|---|---|
| Routing / classification (small tier) | `claude-haiku-4-5` (or `gpt-4o-mini` if user only has OpenAI) | Cheap, fast structured output; suffices for 6-way intent classification. |
| Generation (flagship tier) | `claude-sonnet-4-6` | RAG synthesis quality is the binding constraint; flagship per generation call. |
| Eval judge | `claude-haiku-4-5` (fixed, **must be different from generation tier**) | Avoids judge/generator collusion. Pinned independently of generation model. |
| Embedder | `text-embedding-3-small` (OpenAI; falls back to local `bge-small-en-v1.5` if Ollama is the only configured provider) | Cheap, strong on academic text; local fallback keeps the system offline-capable. |
| Reranker | `bge-reranker-base` (local, runs on CPU in a few hundred ms / batch-50) | No second API key; ~30 MB; predictable Windows behavior. |

These picks resolve the four "Embedding model choice / Reranker choice / Judge model" items previously listed in §12 Open Questions. The 500–1000-token chunk size from SRS §RAG is fixed.

## 4. Implementation phases

Per SRS v1.6 the build is **three phases**: A (Foundations + first vertical slice), B (the rest of the agent + MCP + slides + relations + projects), C (eval harness + NFR polish). Phasing is implementation order, not feature deferral — every phase ships before v1 is declared done. The vertical-slice expansion strategy is preserved: Phase A produces a working `paper_qa` end-to-end path; Phase B widens to all six intents; Phase C hardens to the NFR targets.

| Phase | Goal | FRs lit up | End-of-phase verification |
|---|---|---|---|
| **A — Foundations + `paper_qa` vertical slice** | Repo scaffold (backend uv + frontend React/Vite/Tailwind), `Settings` singleton, SQLite schema applied at startup, Pydantic models, **`LiteLlmAdapter`** over `litellm.acompletion()` with structured-output via `response_format={"type":"json_schema",...}`, YAML prompt registry, Chroma `VectorStore`, **Tool-Call Tracer** with redactor, scope-checked MCP client + typed `McpToolScope` / `McpInvocation`, `mypy --strict` CI gate, `KNOWN-TYPE-GAPS.md` register. Then the first end-to-end interaction: manual single-paper import via **`arxiv` MCP tool — reuse `blazickjp/arxiv-mcp-server`** (PDF fetch + metadata) → **`grobid` MCP tool — thin wrap over `kermitt2/grobid-client-python` (~40 LoC)**, with PyMuPDF-only fallback if GROBID is not running → 500–1000-token chunking → embedding → vector store → Research Agent with two-stage retrieval (dense top-`min(50, ⌈corpus/3⌉)` → cross-encoder reranker top-5) → grounded generation with page-level source annotation → Chat UI (Sidebar + ChatPane with SSE streaming + RoutingBadge + inline citations + collapsible Tool-Trace). **Router makes a real LiteLLM call** constrained to binary intents `{paper_qa, out_of_scope}` via structured output — exercises the structured-output contract from day 1, not a hard-coded stub. | NFR-11 (strict typing CI), FR-01 (single-paper arXiv + local PDF), FR-03 (RAG QA), FR-11 (audit log + Trace UI), partial FR-08 (binary intent), partial FR-10 (`arxiv` + `grobid`) | Boot the app, import an arXiv paper, ask a question, get a cited answer; trace panel renders the full step DAG; an obviously unrelated question is refused with `intent=out_of_scope`. |
| **B — Multi-intent platform: SQL Agent, MCP hardening, Slides, Relations, Projects** | Router widens to all 6 intents (`paper_qa`, `library_stats`, `research_suggest`, `slides`, `mcp_tool`, `chitchat`); confidence threshold + disambiguation fallback. **SQL Agent**: schema-aware NL2SQL against SQLite read-only with self-repair (≤ 3) and a *Show SQL* toggle in the UI. **MCP layer hardening**: promote the Phase-A scope-checker, ship the remaining client-side tools per v1.6 BOM — **`filesystem` reused** from `@modelcontextprotocol/server-filesystem` (pinned post-CVE-2025-53109/53110 with an EscapeRoute regression test), **`sqlite` wrapped** (~50 LoC: table allow-list + aggregated `schema()` for the SQL Agent prompt), **`latex` built** (~150 LoC: `pdflatex` + `chktex` in a workspace sandbox). Stand up the `paperhub.*` MCP server (`search_library`, `get_paper`, `summarize_paper`, `find_related`, `compose_slides`, `list_runs`, `get_trace`). **Report Agent**: multi-paper slide pipeline (structure planning → per-page generation → `latex` feedback loop ≤ 3 retries → PDF) with the FR-05 hard cap (≤ 5 papers, ≤ 20 pages) enforced as a rule *before* any LLM call. Slide Editor UI for per-page regeneration. **Relation analysis + research-direction**: citation edges from GROBID-extracted references + semantic-similarity edges from the vector store; Cytoscape relation graph in Paper Panel; Research Agent gains topic clustering + gap analysis for FR-04. **Projects + tagging + notes**: full CRUD, reading-status, per-project chat history, sidebar navigation. | FR-02, FR-04, FR-05, FR-06, FR-07, FR-08 (full), FR-09, FR-10 (bulk + `paperhub.*` server), NFR-05 (full), NFR-10 | All six intents route correctly; *"What metric did Chen 2024 use?"*, *"How many RAG papers did I add this year?"*, *"save this PDF to `~/Papers/inbox` and summarize §3"*, *"compose slides from these 3 papers"* all work end-to-end with visible routing decisions and a full audit trail. An external Claude Desktop client can call `paperhub.search_library` and `paperhub.compose_slides`. An out-of-scope filesystem path is rejected by the orchestrator. |
| **C — Evaluation harness + NFR polish + batch import** | **FR-12 evaluation harness**: task-suite YAML (≥ 30 routing prompts, ≥ 30 paper-QA Qs + 20 OOD refusals, ≥ 10 NL2SQL Qs, ≥ 3 slide jobs) + runner that sweeps `model × routing_strategy` and scores routing accuracy, **LLM-as-judge answer correctness (Cohen's κ ≥ 0.7 calibration against ≥ 20 human-scored items, judge = `claude-haiku-4-5` pinned distinct from generation model)**, source-citation rate, SQL executability, latency, USD cost. CI integrates the harness via the **recording LLM adapter** (replays Pydantic-typed fixtures captured offline; cost = 0, runtime ≈ 30 s) so routing-accuracy and SQL-executability are regression-gated on every PR; the full real-API sweep that produces the comparison table is a manual command (`uv run paperhub-eval --real --sweep configs/sweep.yaml`) run before each release. **FR-01 hardening**: batch arXiv import (median per-paper ≤ 60 s; full 10-paper batch ≤ 5 min inclusive of rate-limit pauses; sequential under the arXiv 1-req/3-s ToS — the limit is the bottleneck, not parallelism), DOI import path (deferred from v1 → v2; documented as such in the v2 backlog), exponential-backoff retry on external MCP calls. **Cost dashboard** enforcing NFR-07's ≤ USD 0.30/paper budget. **Latency tuning** to hit NFR-01's warm-cache budgets and verify cold-start budgets. **Trace JSON export, single-step replay verification** of read-only steps (per FR-11), redaction audit. Full `mypy --strict` clean with the `KNOWN-TYPE-GAPS.md` register reviewed and any obsolete `# type: ignore` removed. | FR-01 (full hardening), FR-12, NFR-01, NFR-02, NFR-07, NFR-08, NFR-09 (full) | One command produces the model × routing-strategy comparison table; all NFR acceptance criteria pass; CI fails if routing accuracy or SQL executability regresses below NFR-08 thresholds. |

**Phase sizing note.** Phase A is intentionally the largest of the three because it front-loads every cross-cutting foundation alongside the first user-visible feature. Phase B layers on top of that substrate — six discrete capabilities that each compose with the Phase-A scaffolding rather than reshape it. Phase C is the smallest and is mostly measurement, hardening, and tuning. The "vertical slice" label refers to the user-visible shape (one end-to-end demonstrable interaction by end of Phase A), not engineering effort distribution.

## 5. Agent topology

Single shared graph state, one entry node (`router`), one terminal node (`finalize` — emits response and flushes trace). Sub-agents are sub-graphs so they can be tested in isolation.

```
                      ┌───────────┐
            (start) ──▶│  router   │── routing_decision ──┐
                      └───────────┘                       │
                                                          ▼
        ┌─────────────┬─────────────┬─────────────┬─────────────┬─────────────┐
        ▼             ▼             ▼             ▼             ▼             ▼
   research_qa   library_stats   research_sug    slides        mcp_tool    chitchat
   (sub-graph)   (sub-graph)     (sub-graph)    (sub-graph)   (sub-graph)  (sub-graph)
        │             │             │             │             │             │
        └─────────────┴─────────────┴─────────────┴─────────────┴─────────────┘
                                    │
                                    ▼
                              ┌───────────┐
                              │ finalize  │── response + persisted trace
                              └───────────┘
```

**Shared state** (`agents/state.py`):

```python
class AgentState(TypedDict):
    run_id: UUID                                # set at /chat entry
    user_message: str
    project_id: UUID | None
    routing_decision: RoutingDecision | None    # filled by router
    retrieved_chunks: list[Chunk]               # used by research_qa
    sql_query: str | None                       # used by library_stats
    sql_result: SqlResult | None
    mcp_calls: list[McpInvocation]              # used by mcp_tool
    slide_artifacts: SlideArtifacts | None      # used by slides
    final_response: AgentResponse | None        # filled by finalize
    # No Any, no untyped dict — NFR-11
```

**Per-sub-agent contract**: each sub-graph reads exactly the state fields it needs, writes exactly the state fields it owns, and never reaches across. The router writes only `routing_decision`; `finalize` reads everything but writes only `final_response`.

**Routing decision is structured**, not free-text:

```python
class RoutingDecision(BaseModel):
    intent: Literal["paper_qa","library_stats","research_suggest","slides","mcp_tool","chitchat"]
    confidence: float                     # 0..1
    model_tier: Literal["small","flagship"]
    reasoning: str                        # short explanation, logged for eval
    fallback_to_user: bool = False        # true if confidence < threshold
```

Router emits this via structured output (function-call / JSON schema) — never parses free-text. Below-threshold confidence short-circuits the graph to ask the user.

## 6. Data layer

Migrations live in `data/migrations/`, applied at startup. For analytical (OLAP-shaped) queries the SQL Agent may emit, an **opt-in DuckDB bridge** is available: each such query opens a fresh DuckDB connection that attaches the SQLite file read-only via `INSTALL sqlite; LOAD sqlite; ATTACH 'paperhub.db' AS pdb (TYPE SQLITE, READ_ONLY);`, runs the query, and closes. This is **not a long-lived view** over a shared connection — it is a per-query bridge, so consistency is read-at-attach-time and a `library_stats` query sees everything committed to SQLite before its `ATTACH` ran. Transactional reads continue to hit SQLite directly (per SRS §⑥, v1.4).

```sql
-- Identity & organisation
CREATE TABLE projects (
    id              TEXT PRIMARY KEY,           -- UUID
    name            TEXT NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Papers
CREATE TABLE papers (
    id              TEXT PRIMARY KEY,           -- UUID
    arxiv_id        TEXT UNIQUE,
    doi             TEXT UNIQUE,
    title           TEXT NOT NULL,
    authors_json    TEXT NOT NULL,              -- JSON array
    year            INTEGER,
    abstract        TEXT,
    pdf_path        TEXT NOT NULL,              -- relative to workspace root
    sha256          TEXT NOT NULL,
    primary_topic   TEXT,
    added_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_papers_year_topic ON papers(year, primary_topic);

CREATE TABLE project_papers (
    project_id      TEXT NOT NULL REFERENCES projects(id),
    paper_id        TEXT NOT NULL REFERENCES papers(id),
    reading_status  TEXT CHECK(reading_status IN ('unread','skimmed','deep')),
    PRIMARY KEY (project_id, paper_id)
);

CREATE TABLE tags (
    paper_id        TEXT NOT NULL REFERENCES papers(id),
    tag             TEXT NOT NULL,
    PRIMARY KEY (paper_id, tag)
);

CREATE TABLE notes (
    id              TEXT PRIMARY KEY,
    paper_id        TEXT NOT NULL REFERENCES papers(id),
    body_md         TEXT NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Chunks (text) + vector index (separate sqlite-vss virtual table)
CREATE TABLE chunks (
    id              TEXT PRIMARY KEY,
    paper_id        TEXT NOT NULL REFERENCES papers(id),
    section         TEXT,
    page            INTEGER,
    char_start      INTEGER,
    char_end        INTEGER,
    text            TEXT NOT NULL
);
CREATE INDEX idx_chunks_paper ON chunks(paper_id);
-- Vector index lives outside this SQL schema:
--   * Default backend (Chroma): a sibling on-disk collection at workspace/chroma/,
--     keyed by chunk.id; written by data/vectors.py.
--   * Opt-in backend (sqlite-vec): a vec0 virtual table chunk_vectors(chunk_id, embedding)
--     created in the same .db file at startup when Settings.vector_backend == "sqlite-vec".
-- The agent code only touches the data/vectors.py interface, never the backend directly.

-- Citation edges (FR-02)
CREATE TABLE citations (
    src_paper_id    TEXT NOT NULL REFERENCES papers(id),
    dst_paper_id    TEXT NOT NULL REFERENCES papers(id),
    source          TEXT NOT NULL,             -- 'semantic_scholar' | 'grobid' | 'user'
    PRIMARY KEY (src_paper_id, dst_paper_id)
);

-- Chat / runs
CREATE TABLE chat_sessions (
    id              TEXT PRIMARY KEY,
    project_id      TEXT REFERENCES projects(id),
    title           TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE messages (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES chat_sessions(id),
    role            TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
    content         TEXT NOT NULL,
    run_id          TEXT,                       -- links to tool_calls
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE runs (
    id              TEXT PRIMARY KEY,           -- run_id
    session_id      TEXT REFERENCES chat_sessions(id),
    routing_decision_json TEXT,                 -- full RoutingDecision
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    status          TEXT CHECK(status IN ('running','ok','failed'))
);

-- The single source of truth for FR-11 and FR-12
CREATE TABLE tool_calls (
    run_id          TEXT NOT NULL REFERENCES runs(id),
    step_index      INTEGER NOT NULL,
    parent_step     INTEGER,                    -- for DAG rendering
    agent           TEXT NOT NULL,              -- 'router','research','sql',...
    tool            TEXT NOT NULL,              -- 'llm','vector_search','sqlite','mcp.filesystem',...
    model           TEXT,                       -- e.g. 'claude-sonnet-4-6', null for non-LLM
    args_redacted_json   TEXT NOT NULL,
    result_summary_json  TEXT,
    latency_ms      INTEGER NOT NULL,
    token_in        INTEGER,
    token_out       INTEGER,
    status          TEXT NOT NULL CHECK(status IN ('ok','error','rejected')),
    error           TEXT,
    PRIMARY KEY (run_id, step_index)
);
CREATE INDEX idx_tool_calls_run ON tool_calls(run_id, step_index);
```

The `tool_calls` table is the only persistence the trace UI, the eval harness, and the replay feature read from.

**Persistence model — durability before SSE emission, not single-transaction.** The previous "everything commits at `finalize`" model was incompatible with the SSE streaming contract in §8 (the UI sees `tool_step` events as they happen). The actual model is:

1. At `/chat` entry, insert one `runs` row with `status='running'` and emit a `routing_decision` SSE event after the router resolves.
2. The Tool-Call Tracer commits **each `tool_calls` row in its own short transaction the moment that step completes**, *before* the corresponding `tool_step` SSE event is sent to the frontend. The UI is therefore never shown a step that does not exist in the database, and any SSE event the user saw is reproducible from `tool_calls` after the fact.
3. At `finalize`, a single transaction inserts the assistant `messages` row and updates `runs.status='ok'` (and `runs.finished_at`). Both rows commit together so the chat history never shows an incomplete turn.
4. **Crash-recovery reaper:** a startup task scans for `runs.status='running'` rows older than 30 minutes and marks them `'failed'` with `runs.finished_at = NOW()`. Their `tool_calls` rows remain intact, so a crashed run still has a fully-replayable trace — it is simply marked failed in the index.

**One-turn invariant:** for each user message, the system writes exactly **one `runs` row, one assistant `messages` row (committed at `finalize`), and N `tool_calls` rows** all sharing the same `run_id`. Internal sub-agent steps do *not* produce their own `messages` rows — they are visible only through `tool_calls`. `messages.run_id` is non-null for assistant rows and points to the single `runs` row for that turn; the join from a chat message to its trace is therefore a single FK lookup, not a many-to-many resolution.

## 7. MCP integration

PaperHub is both an MCP **client** (calling external tools) and an MCP **server** (exposing its own primitives).

**Client side — scope-checker is the gate, not the server.** The orchestrator validates every outbound MCP call against a declared scope before dispatching. Scope violations are recorded in `tool_calls` with `status='rejected'` and **never reach the server process**, which means a misbehaving MCP server cannot do something its declaration didn't allow.

```python
class McpToolScope(BaseModel):
    tool_name: str                                # "filesystem", "sqlite", ...
    filesystem_root: Path | None = None           # required for filesystem tool
    sqlite_table_allowlist: list[str] | None = None
    url_domain_allowlist: list[str] | None = None
    write_allowed: bool = False

# One typed args model per (tool, method) — defined in mcp/tools/<tool>.py
class ArxivFetchMetadataArgs(BaseModel):       arxiv_id: str
class ArxivDownloadPdfArgs(BaseModel):         arxiv_id: str
class FilesystemReadArgs(BaseModel):           path: Path
class FilesystemWriteArgs(BaseModel):          path: Path; content: bytes
class SqliteQueryArgs(BaseModel):              sql: str; params: tuple[str | int | float | bool, ...] = ()
class WebSearchArgs(BaseModel):                query: str; max_results: int = 10
# ... one per method across the 8 client-side tools

McpArgs = (
    ArxivFetchMetadataArgs | ArxivDownloadPdfArgs
    | FilesystemReadArgs | FilesystemWriteArgs
    | SqliteQueryArgs | WebSearchArgs
    # | ... (full discriminated union over every (tool, method))
)

class McpInvocation(BaseModel):
    tool: str
    method: str                                   # "write_file", "query", ...
    args: McpArgs                                 # discriminated by (tool, method) at parse time
    # check_scope(invocation, scope) -> Ok | RejectionReason
```

The discriminated union is the documented exception to NFR-11's "no untyped dict at I/O boundary": untyped JSON-RPC arg payloads from the wire are validated into one of the typed schemas above in a single `model_validate` call before crossing into any agent or scope-checker code. This means the scope-checker can read e.g. `args.path` directly with full type information, rather than fishing it out of a `dict[str, ...]`.

Scope declarations live in `config.py` (typed `Settings`), not in YAML — they're code, they get type-checked, and changes to them show up in `git blame`.

**SRS alignment.** SRS v1.3 already folds §⑤ ("External APIs": arXiv, Semantic Scholar, Crossref) and §⑦ ("Rules / Tools": grobid, latex) into §⑧ MCP Tool Layer, so every external integration *except* the LLM provider adapter is an MCP tool. The benefits are uniform: every external call goes through the same tracer, the same scope-checker, and the same `tool_calls` audit row, and external MCP clients (Claude Desktop, Cursor, future Slack bots) get the full integration surface for free. LLM providers stay separate because they have a hot-path interface (`llm/adapter.py`) with token-streaming and structured-output concerns that don't map cleanly onto MCP.

### 7.1 Client-side MCP tools (called by PaperHub agents)

**Five v1 tools** per SRS v1.6. Each declares a typed `McpToolScope` and is enforced by `mcp/client.py` before dispatch. Bibliographic enrichment beyond arXiv (Semantic Scholar / Crossref / web search) is **dropped in v1** — citation edges for FR-02 come from `grobid`'s reference extraction; the dropped tools plug back in unchanged in v2 since the scope-checker contract is uniform.

**Reuse-first policy (SRS v1.5 bill of materials).** Of the 5 v1 tools, 2 run an existing community server unchanged, 2 are thin custom wrappers (~40–50 LoC) around an existing client library, and 1 is built from scratch. PaperHub never wraps a server we could run directly. Concrete package pins below.

| Tool | Scope | Methods | Provenance | Repo / Package |
|---|---|---|---|---|
| `arxiv` | Domain pinned to `arxiv.org`; rate-limit 1 req/3 s (arXiv ToS) | `search(query)`, `fetch_metadata(arxiv_id)`, `download_pdf(arxiv_id) -> path` | **Reuse** | [`blazickjp/arxiv-mcp-server`](https://github.com/blazickjp/arxiv-mcp-server) (PyPI: `arxiv-mcp-server`, install via `uv tool install arxiv-mcp-server`). Auto-enforces the 3 s rate limit; superset of methods. |
| `grobid` | Localhost only (`http://localhost:8070` by default); request-size cap | `process_fulltext(pdf_path)`, `process_header(pdf_path)`, `process_references(pdf_path)` | **Wrap** | Wrap official [`kermitt2/grobid-client-python`](https://github.com/kermitt2/grobid-client-python) in `paperhub/mcp/tools/grobid_server.py` (~40 LoC, FastMCP); adds workspace-root validation on PDF paths. Also the v1 source for citation edges in FR-02 (replaces the dropped `semantic_scholar` tool). Reference: [`JackKuo666/grobid-MCP-Server`](https://github.com/JackKuo666/grobid-MCP-Server). |
| `latex` | Workspace-root sandboxed; per-call timeout 60 s | `compile(tex_path)`, `chktex(tex_path)` | **Build** | No existing server exposes both `chktex` and a workspace sandbox. Build `paperhub/mcp/tools/latex_server.py` (~150 LoC, FastMCP) invoking `pdflatex` + `chktex` as subprocesses inside the sandbox. |
| `filesystem` | Sandboxed to `~/PaperHub/workspace` by default; read + write inside the root only | `read_file`, `write_file`, `list_dir`, `delete_file` | **Reuse** | [`@modelcontextprotocol/server-filesystem`](https://www.npmjs.com/package/@modelcontextprotocol/server-filesystem) (Anthropic official, npm). **Pinned post-CVE-2025-53109/53110** (EscapeRoute path-traversal fixes); regression test added in `tests/mcp/test_filesystem_escape.py` that attempts `..` traversal and asserts rejection. |
| `sqlite` | Read-only; allow-list = `papers, tags, notes, citations, tool_calls, runs, chat_sessions, messages` | `query(sql, params)`, `schema()` | **Wrap** | Official `mcp/server-sqlite` is archived and not read-only-with-allow-list. Build `paperhub/mcp/tools/sqlite_server.py` (~50 LoC, FastMCP) on `sqlite3` with table allow-list enforced and `schema()` returning all whitelisted table schemas for the SQL Agent prompt. Reference: [`hannesrudolph/sqlite-explorer-fastmcp-mcp-server`](https://github.com/hannesrudolph/sqlite-explorer-fastmcp-mcp-server). |

**Total custom MCP code at v1**: ~150 LoC for `latex` + ~40 LoC for `grobid` + ~50 LoC for `sqlite` = **~240 LoC of FastMCP glue** plus configuration for 2 reused servers (and a Node runtime for `filesystem`). The remaining ~1500 LoC originally budgeted for "MCP layer" goes to the SQL Agent, RAG retriever, slide pipeline, and React UI.

### 7.2 Server-side MCP tools (PaperHub-as-server, exposed to external clients)

The `paperhub.*` MCP server makes PaperHub's own capabilities available to external MCP clients (Claude Desktop, Cursor, future automation). Each method is a thin wrapper over an existing sub-agent so external callers get the same logic the UI does, with the same audit log.

| Tool | Method | Backing sub-agent | Notes |
|---|---|---|---|
| `paperhub.search_library` | `search(query, project_id?)` | Research Agent (RAG, single-step) | Returns top-k chunks with citations. |
| `paperhub.get_paper` | `get(paper_id)` | Data layer | Full metadata + notes + tags. |
| `paperhub.find_related` | `find_related(paper_id, limit)` | Relation analysis (Phase 5) | Returns related papers + edge weights. |
| `paperhub.summarize_paper` | `summarize(paper_id, max_words?)` | Research Agent | Grounded summary with section citations. |
| `paperhub.compose_slides` | `compose(paper_ids[], options?)` | Report Agent (Phase 4) | Returns PDF path + per-page metadata. |
| `paperhub.list_runs` | `list(session_id?, since?)` | Trace store | Lets external clients enumerate audit trails. |
| `paperhub.get_trace` | `get(run_id)` | Trace store | Returns the full `tool_calls` DAG as JSON. |

**Fifteen tools total at v1** (8 client-side + 7 `paperhub.*` server methods). This is a real working tool palette for a complete system, not a token set for a demo — a meaningful fraction of production user tasks will genuinely require selecting between `arxiv` vs `semantic_scholar` vs `web_search`, between `sqlite` vs the Research Agent, between in-app slide generation vs `paperhub.compose_slides` over MCP from an external client. That richness is what gives the Router Agent something real to decide.

## 8. Frontend architecture

Five top-level regions, each a focused component tree. Server-state managed by **TanStack Query**; ephemeral UI state by **zustand**. Streaming via Server-Sent Events from FastAPI. Internationalization via **`react-i18next`** (resource files: `locales/zh-TW.json`, `locales/en.json`), satisfying NFR-05's bilingual requirement; locale is user-toggleable from the sidebar and persists in `localStorage`.

```
<App>
├── <Sidebar>            ← chat history, project switcher, paper list entry
├── <ChatPane>           ← message list + composer
│     ├── <MessageList>
│     │     └── <Message>
│     │           ├── <CitationChip>     ← jumps to <PdfViewer>
│     │           └── <TraceInline>      ← collapsed by default; expand for DAG
│     ├── <RoutingBadge>                 ← shows intent + model tier in real time
│     └── <Composer>
├── <PaperPanel>         ← list view + Cytoscape relation graph (tab toggle)
├── <SlideEditor>        ← page list + per-page preview + regenerate button
└── <TraceViewer>        ← full tool-call DAG modal, JSON export, step-replay
```

**Streaming protocol.** SSE event types (defined as a TypeScript discriminated union, generated from the backend Pydantic schemas via `datamodel-code-generator`):

```ts
type SseEvent =
  | { type: "routing_decision"; data: RoutingDecision }
  | { type: "tool_step";        data: ToolCall }      // appears in <TraceInline> as it streams
  | { type: "token";            data: { text: string } }
  | { type: "citation";         data: Citation }
  | { type: "final";            data: AgentResponse }
  | { type: "error";            data: { message: string; rejected_scope?: McpToolScope } };
```

The UI renders the routing decision **before** the first token, so the user can see `intent=paper_qa, model=flagship` *before* the answer streams in. This visibility is a core product feature — users (and operators reviewing the audit log) need to know which capability handled their request without inspecting backend logs.

**Durability-before-emission ordering.** Per §6's persistence model, each `tool_calls` row commits to SQLite *before* the corresponding `tool_step` SSE event is sent. The user therefore never sees a step in the UI that does not exist in the database, and `paperhub.get_trace(run_id)` is guaranteed to return every step the user ever saw — including for in-flight runs (the run is queryable while `status='running'`).

## 9. Testing strategy

| Layer | Tool | Coverage rule |
|---|---|---|
| Unit (pure functions, models, scope-checker, prompt rendering) | `pytest` | Every public function in `tracing/`, `mcp/client.py`, `data/models.py`, `llm/prompts.py`, `agents/state.py` has tests. |
| Agent sub-graphs | `pytest` with a fake LLM adapter that returns canned structured outputs | Each sub-agent tested in isolation: given an input state, asserts the output state. No real model calls. |
| Integration (full LangGraph) | `pytest` with a recording LLM adapter (replays fixtures) | One test per intent: assert the routing decision, the sub-agent invoked, and the shape of `tool_calls` rows. |
| API | `httpx.AsyncClient` against the FastAPI app | One test per route; `/chat` SSE stream parsed and asserted. |
| Frontend unit | `vitest` + `@testing-library/react` | Components rendered in isolation; mocked API client. |
| Frontend E2E | `playwright` against `npm run dev` + a backend started with a recording adapter | Happy path per intent; trace panel renders; out-of-scope MCP call shows rejection. |
| Eval harness | The harness itself doubles as a regression test in CI (small task suite) | A drop in routing accuracy or SQL executability fails the build. |
| Static | `mypy --strict`, `ruff`, `pyright` on frontend's generated types | CI-gating per NFR-11. |

**No mocked LLMs in agent sub-graph tests** — they use a fake adapter that returns Pydantic instances directly, so the schema contract is exercised, not bypassed.

## 10. Reference usage policy

The two `reference/` projects are **read-only inspiration**, not a code-import path.

- The design above was authored from the SRS, not from the references.
- During implementation, opening a reference file to check *"how did they handle X?"* is fine; copy-pasting code is not. If a pattern from a reference is adopted, it is re-typed (or rewritten in our stack) so it goes through our typing / tests / tracer wiring.
- `reference/` stays in `.gitignore` — it never enters the PaperHub repo.

**Prior art consulted (footnote only):** `paper2slides-plus` (for LaTeX feedback-loop pattern and YAML prompt-management pattern); `Intro2GenAI-hw1` (for chat-UI layout and SSE streaming pattern).

## 11. SRS traceability

Every SRS FR and NFR maps to a concrete phase or cross-cutting foundation. If a row below ever falls out of sync with the SRS, this design must be revised.

| SRS item | Realised by |
|---|---|
| FR-01 paper import + indexing | **Phase A** (single arXiv/PDF import) → **Phase C** (batch of 10+; DOI deferred to v2) |
| FR-02 cross-paper relation analysis | **Phase B** (citations from `grobid`-extracted references + vector-similarity edges) |
| FR-03 RAG Q&A | **Phase A** |
| FR-04 research-direction suggestion | **Phase B** (topic clustering + gap analysis over local library only — `semantic_scholar` deferred to v2) |
| FR-05 multi-paper integrated slides | **Phase B** |
| FR-06 tagging + project management | **Phase B** |
| FR-07 interactive slide editing | **Phase B** |
| FR-08 Router Agent + classification | **Phase A** (real LiteLLM structured-output call constrained to binary `{paper_qa, out_of_scope}` — exercises the structured-output contract end-to-end from day 1) → **Phase B** (full 6-intent classifier with confidence threshold + disambiguation fallback) |
| FR-09 NL2SQL | **Phase B** |
| FR-10 MCP tool integration *(v1.6 reuse-first BOM, 5 tools)* | **Phase A** (`arxiv` reused, `grobid` wrapped) → **Phase B** (`filesystem` reused-and-CVE-pinned + EscapeRoute regression test, `sqlite` wrapped, `latex` built from scratch, `paperhub.*` server built) |
| FR-11 tool-call audit log + trace UI | **Phase A** (tracer + UI region from day 1) |
| FR-12 evaluation harness | **Phase C** (task suite + sweep runner); LLM-as-judge rubric (judge model = `claude-haiku-4-5`, κ ≥ 0.7 against ≥ 20 human-scored items) pinned in §3 foundations and exercised by the harness. CI uses the recording adapter; real-API sweep is a manual command. |
| NFR-01 performance targets | **Phase C** — verifies both **warm-cache budgets** (single-paper indexing ≤ 60 s, RAG first-token ≤ 5 s, slide generation ≤ 15 min) and **cold-start budgets** (single-paper indexing ≤ 3 min, first RAG first-token ≤ 15 s) per the v1.4 SRS split. |
| NFR-02 reliability (retries) | **Phase B** (LaTeX retries inside the slide feedback loop) + **Phase C** (exponential-backoff retry on external MCP calls) |
| NFR-03 extensibility (pluggable providers) | `llm/adapter.py` foundation (Phase A) — satisfied by LiteLLM's 100+ provider matrix |
| NFR-04 data security (env-var keys, local SQLite) | `config.py` foundation (Phase A) |
| NFR-05 usability (Open WebUI layout, bilingual, ≤3 clicks) | Chat shell (Phase A) + project nav polish (Phase B) |
| NFR-06 maintainability (modular, YAML prompts) | `llm/prompts.py` foundation (Phase A) |
| NFR-07 cost control (≤ USD 0.30/paper, dashboard) | **Phase C** |
| NFR-08 routing accuracy | **Phase C** (measured by eval harness) |
| NFR-09 auditability + redaction | `tracing/` foundation (Phase A); replay verified in Phase C |
| NFR-10 MCP security boundary | `mcp/client.py` scope-checker foundation (Phase A); fully enforced from Phase B |
| NFR-11 strict typing | Phase A from commit 1; gated in CI. **Upstream-boundary exception register** (`docs/KNOWN-TYPE-GAPS.md`, created in Phase A) holds per-occurrence `# type: ignore[<error-code>]` entries at LangGraph / MCP-SDK call sites where upstream stubs are incomplete; reviewed in Phase C and entries removed when upstream fixes ship. |

## 12. Open questions deferred to implementation plan

Items intentionally not pinned in this design — to be decided during writing-plans:

- Whether `paperhub.*` MCP server runs in-process (one Python process serving both FastAPI and the MCP stdio surface) or as a subprocess (separate process spoken to over stdio/socket). Affects how external clients like Claude Desktop launch PaperHub; current lean is in-process for v1, subprocess for v2 if external automation becomes important.
- Cytoscape relation-graph layout algorithm (`cose-bilkent` vs `cola` vs `dagre`) and edge-weight visualization details (line thickness vs color vs both). Decided during Phase 5 UI work.
- Exact prompt content (the YAML registry is in scope and Phase-0; specific prompt copy and few-shot examples are Phase-1 tasks per intent).

*(Previously open and now resolved: vector-store backend → Chroma default / `sqlite-vec` opt-in (§3, SRS §⑥); embedder → `text-embedding-3-small` with `bge-small-en-v1.5` fallback (§3); reranker → `bge-reranker-base` (§3); judge model → `claude-haiku-4-5` with κ ≥ 0.7 calibration (SRS FR-12); arxiv MCP launcher → `Settings.mcp_arxiv_command = "uvx arxiv-mcp-server"` in Phase A §3 foundations; grobid fallback → PyMuPDF-only when GROBID unreachable per Phase A §3; embedder default → `BAAI/bge-small-en-v1.5` configurable via `Settings.embedding_model`.)*
