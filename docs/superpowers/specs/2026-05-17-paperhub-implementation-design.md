---
title: PaperHub — Implementation Design
status: approved
created: 2026-05-17
scope: full-system implementation
companion_to: 2026-05-17-paperhub-srs.md
---

# PaperHub — Implementation Design

This document is the implementation-design companion to [the PaperHub SRS v1.2](./2026-05-17-paperhub-srs.md). The SRS defines *what* PaperHub must do (12 FRs, 11 NFRs, 12 acceptance criteria); this document defines *how* we will build it — repository layout, cross-cutting foundations, phasing, agent topology, data schema, MCP integration, React UI, and testing strategy.

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
│   │   │   ├── adapter.py            # Provider Adapter (OpenAI/Anthropic/Ollama)
│   │   │   ├── prompts.py            # YAML-driven prompt manager
│   │   │   └── prompts.yaml
│   │   ├── rag/
│   │   │   ├── chunker.py
│   │   │   ├── embedder.py
│   │   │   └── retriever.py          # 2-stage: dense search + reranker
│   │   ├── mcp/
│   │   │   ├── server.py             # custom paperhub.* MCP server
│   │   │   ├── client.py             # MCP client + scope-checker
│   │   │   ├── scopes.py             # typed McpToolScope declarations
│   │   │   └── tools/                # one wrapper per MCP tool
│   │   │       ├── arxiv.py
│   │   │       ├── semantic_scholar.py
│   │   │       ├── crossref.py
│   │   │       ├── web_search.py
│   │   │       ├── grobid.py
│   │   │       ├── latex.py
│   │   │       ├── filesystem.py
│   │   │       └── sqlite.py
│   │   ├── data/
│   │   │   ├── db.py                 # SQLite (+ DuckDB optional) connection
│   │   │   ├── models.py             # Pydantic data models
│   │   │   ├── migrations/           # raw SQL files, applied at startup
│   │   │   └── vectors.py            # sqlite-vss integration
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
| `llm/adapter.py` | Single async interface `generate(messages, model_tier, response_model) -> BaseModel`. | `response_model` defaults to a structured Pydantic schema; `model_tier ∈ {small, flagship}`. |
| `llm/prompts.py` | YAML-loaded prompt registry with versioning, variable substitution, A/B slots. | All prompts live in `prompts.yaml`; no inline strings in agent code. |
| `data/models.py` | Pydantic models for every persisted entity: `Paper`, `Chunk`, `Project`, `Note`, `ToolCall`, `RunMetadata`, `RoutingDecision`. | Owned by data layer; imported everywhere. |
| `data/vectors.py` | Vector-store driver behind a narrow interface (`add`, `search`, `delete_by_paper`). **Primary**: `sqlite-vss` (co-located in the same SQLite file, no second service). **Fallback**: Chroma (per SRS Key-Decisions row). Swap is one config flag; no agent code changes. | Both backends implement the same Pydantic-typed interface. |
| `mcp/client.py` scope-checker | Validates every MCP call against the declared allow-list **before** dispatching to the MCP server. | Enforces NFR-10; rejections recorded in `tool_calls`. |
| `config.py` | `pydantic-settings`-based config; loads `.env`; exposes a typed `Settings` singleton. | All API keys, model names, paths, MCP scopes flow through here. |

**Two properties baked in from Phase 0** (not added retroactively):

1. **Strict typing.** Every public function signature, FastAPI route, agent step, and LangGraph state field is Pydantic / TypedDict / dataclass. `mypy --strict` (or `pyright`) is on in CI from commit 1. `Any`, `object`, untyped `dict`/`list` are prohibited in public interfaces; `dict[str, Any]` is allowed only at the I/O boundary with an external untyped source and must be parsed into a typed model before crossing one function call.
2. **Every call traced.** The Tool-Call Tracer wraps every model call, tool call, and MCP call from Phase 1 onward. There is no "I'll add observability later" phase.

## 4. Implementation phases

Phase 0 is foundation; each subsequent phase delivers a working end-to-end slice that lights up another set of SRS FRs.

| Phase | Goal | FRs lit up | End-of-phase verification |
|---|---|---|---|
| **0 — Foundations** | Repo scaffold, FastAPI app shell, React+Tailwind shell, SQLite schema + migrations applied at startup, Pydantic models, LLM Provider Adapter, prompt registry, **Tool-Call Tracer** wired into the call graph, scope-checked MCP client (stub), strict-typing CI. | NFR-11; groundwork for FR-08 and FR-11 | App boots; `/health` returns; `tool_calls` is empty but writable. |
| **1 — First vertical slice: `paper_qa`** | Manual single-paper import (arXiv ID *or* local PDF; batch import deferred to Phase 8) via **`arxiv` MCP tool** (PDF fetch + header metadata) → **`grobid` MCP tool** (structured header extraction) → text extraction → **500–1000-token** chunking (per SRS §RAG) → embedding → vector store → Research Agent (**two-stage retrieval: dense top-50 → cross-encoder reranker top-5**, per SRS §RAG) → grounded generation with page-level source annotation → Chat UI shows answer + inline citation + Tool-Trace panel populated from `tool_calls`. Router Agent is a hard-coded stub that always returns `intent=paper_qa, confidence=1.0` — the structured-output contract is exercised end-to-end from day 1; only the classification logic is deferred to Phase 2. | FR-01 (manual import), FR-03, FR-11, partial FR-08, partial FR-10 (`arxiv`, `grobid` tools) | Ask a question about an imported paper, get a cited answer, see the full trace. |
| **2 — Router + `library_stats` via SQL Agent** | Router becomes real: classifies between `paper_qa` and `library_stats`. SQL Agent: schema-aware NL2SQL against SQLite (read-only), self-repair loop (≤ 3), result formatting, "Show SQL" toggle in UI. | FR-08, FR-09 | Same chat box answers both *"What metric did Chen 2024 use?"* and *"How many RAG papers did I add this year?"* with visible routing decision. |
| **3 — MCP layer hardening + remaining tools** | Promote the Phase-0 scope-checker stub to production: typed `McpToolScope` declarations in `mcp/scopes.py`, rejection rows in `tool_calls` with `status='rejected'`. Ship the rest of the client-side MCP tools not yet built: `filesystem`, `sqlite`, `web_search`, `crossref`. Stand up the `paperhub.*` MCP server (`search_library`, `get_paper`, `summarize_paper`, `list_runs`, `get_trace`). Router adds `mcp_tool` intent. Trace UI shows MCP calls and scope decisions; rejected calls render with a clear reason. | FR-10 (bulk), NFR-10 | *"Save this PDF to `~/Papers/inbox` and summarize §3"* succeeds; an out-of-scope path is rejected by the orchestrator; an external Claude Desktop client can call `paperhub.search_library`. |
| **4 — Report Agent: multi-paper slides + page-level editing** | Slide pipeline: structure planning → per-page generation → **`latex` MCP tool** (`compile` + `chktex`) with feedback loop (≤ 3 retries) → PDF. Adds `paperhub.compose_slides` to the server-side MCP surface. **Slide caps enforced as rules**: ≤ 5 input papers, ≤ 20 pages per run (per SRS Part 2 cost-guardrail row). Slide Editor UI for per-page regeneration. Router adds `slides` intent. | FR-05, FR-07, partial FR-10 (`latex` tool + `paperhub.compose_slides`) | Pick N papers, click *Compose Slides*, edit page 4, recompile that page only. |
| **5 — Relation analysis + research-direction suggestion** | **`semantic_scholar` MCP tool** for citation graph, related-paper lookup, author overlap; **`grobid` MCP tool** (already present from Phase 1) reused for in-PDF reference extraction; semantic-similarity edges from the vector store. Cytoscape relation graph in Paper Panel. Research Agent gains topic clustering + gap analysis → recommendation. Adds `paperhub.find_related` to the server-side MCP surface. Router adds `research_suggest` intent. **UC-3 of the SRS (research-direction → multi-paper slides) is realized by chaining Phase 5 output into Phase 4's Report Agent**; the split is implementation-only and transparent to the user. | FR-02, FR-04, partial FR-10 (`semantic_scholar` tool + `paperhub.find_related`) | Relation graph renders; topic-driven suggestions return 3–5 directions with supporting papers, *Compose Slides* one-click hand-off works. |
| **6 — Project management + tagging + notes** | Projects CRUD, tags, reading-status, notes, chat-history per project, sidebar navigation. | FR-06, NFR-05 | Multi-project workflow; tag / note operations within 1 s. |
| **7 — Evaluation harness** | Task-suite YAML, runner that sweeps `model × routing_strategy`, scores: routing accuracy, answer correctness, citation rate, SQL executability, latency, cost; exports comparison table. Wired into CI as a regression gate. | FR-12, NFR-08 | One command produces the comparison table; CI fails if routing accuracy or SQL executability regresses. |
| **8 — NFR polish + batch import** | **FR-01 hardening**: batch import for 10+ arXiv IDs (per Acceptance #1) parallelizing `arxiv` + `grobid` MCP calls under their rate-limit scopes, DOI import path via the **`crossref` MCP tool**, exponential-backoff retry on external MCP calls. Cost dashboard. Latency tuning to hit **NFR-01 targets** (single-paper indexing < 60 s; RAG first-token < 5 s; slide generation < 15 min; Acceptance #1: 95% of a 10-paper batch under 60 s). Cost-guardrail enforcement (≤ USD 0.30 per paper, NFR-07). Trace JSON export, replay-step verification, redaction audit, full `mypy --strict` clean. | FR-01 (full), NFR-01, NFR-02, NFR-07, NFR-09 (full) | All NFR acceptance criteria pass; Acceptance #1 batch-import target met. |

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

Migrations live in `data/migrations/`, applied at startup. A read-only DuckDB view layered on the same SQLite file is available for the SQL Agent's analytical queries (NFR-09 distinction between transactional and analytical reads).

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
-- vss0 virtual table: chunk_vectors(chunk_id, embedding)

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

The `tool_calls` table is the only persistence the trace UI, the eval harness, and the replay feature read from. Tracer writes are inside the same SQLite transaction as the `messages` insert at `finalize`, so a successful turn either fully appears in both tables or not at all.

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

class McpInvocation(BaseModel):
    tool: str
    method: str                                   # "write_file", "query", ...
    args: dict[str, str | int | float | bool]    # typed at boundary
    # check_scope(invocation, scope) -> Ok | RejectionReason
```

Scope declarations live in `config.py` (typed `Settings`), not in YAML — they're code, they get type-checked, and changes to them show up in `git blame`.

**Design refinement on the SRS:** SRS §⑤ ("External APIs": arXiv, Semantic Scholar, Crossref) and §⑧ ("MCP Tools": filesystem, sqlite, web_search, paperhub.*) are merged in this implementation. Every external integration *except* the LLM provider adapter is exposed as an MCP tool. The benefits are uniform: every external call goes through the same tracer, the same scope-checker, and the same `tool_calls` audit row, and external MCP clients (Claude Desktop, Cursor, future Slack bots) get the full integration surface for free. LLM providers stay separate because they have a hot-path interface (`llm/adapter.py`) with token-streaming and structured-output concerns that don't map cleanly onto MCP. This is recorded as a deliberate SRS-level deviation — the upcoming SRS v1.3 patch should reflect it.

### 7.1 Client-side MCP tools (called by PaperHub agents)

Eight tools across four groups. Each declares a typed `McpToolScope` and is enforced by `mcp/client.py` before dispatch.

**Bibliographic / metadata APIs** *(replace SRS §⑤ "External APIs" except LLM providers)*

| Tool | Scope | Methods | Notes |
|---|---|---|---|
| `arxiv` | Domain pinned to `arxiv.org`; rate-limit 1 req/3 s (arXiv ToS) | `search(query)`, `fetch_metadata(arxiv_id)`, `download_pdf(arxiv_id) -> path` | Used by Phase 1 (single-paper import) and Phase 8 (batch import). Wraps `arxiv` Python client; result models are Pydantic. |
| `semantic_scholar` | Domain pinned to `api.semanticscholar.org`; rate-limit per API key | `paper(paper_id)`, `citations(paper_id)`, `references(paper_id)`, `recommended(paper_ids[])`, `author(author_id)` | Used by Phase 5 (citation graph + research-direction); also useful in Phase 3 for `paper_qa`-adjacent enrichment. |
| `crossref` | Domain pinned to `api.crossref.org` | `lookup_doi(doi)` | DOI → metadata fallback when arXiv has no record. Used in Phase 8 for the DOI import path. |
| `web_search` | Domain allow-list = `arxiv.org, semanticscholar.org, doi.org, openreview.net`; rate-limited | `search(query, max_results)` | General fallback when none of the above resolve a query. |

**Local deterministic tools** *(replace SRS §⑦ "Rules / Tools" with the same MCP surface, for tracing uniformity)*

| Tool | Scope | Methods | Notes |
|---|---|---|---|
| `grobid` | Localhost only (`http://localhost:8070` by default); request-size cap | `process_fulltext(pdf_path)`, `process_header(pdf_path)`, `process_references(pdf_path)` | Structured PDF metadata + reference list. Used by Phase 1 (header) and Phase 5 (references). |
| `latex` | Workspace-root sandboxed; per-call timeout 60 s | `compile(tex_path)`, `chktex(tex_path)` | `pdflatex` + linter feedback loop; used by Phase 4 (slides). Exposing as MCP lets external clients also drive the slide pipeline. |

**Filesystem & local DB**

| Tool | Scope | Methods | Notes |
|---|---|---|---|
| `filesystem` | Sandboxed to `~/PaperHub/workspace` by default; read + write inside the root only | `read_file`, `write_file`, `list_dir`, `delete_file` | Save downloaded PDFs, export decks / trace JSON. |
| `sqlite` | Read-only; allow-list = `papers, tags, notes, citations, tool_calls, runs, chat_sessions, messages` | `query(sql, params)`, `schema()` | Power the SQL Agent's NL2SQL queries; the `schema()` method feeds the SQL Agent's prompt. |

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

Five top-level regions, each a focused component tree. Server-state managed by **TanStack Query**; ephemeral UI state by **zustand**. Streaming via Server-Sent Events from FastAPI.

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
| FR-01 paper import + indexing | Phase 1 (single-paper) → **Phase 8** (batch of 10+, DOI path) |
| FR-02 cross-paper relation analysis | Phase 5 |
| FR-03 RAG Q&A | Phase 1 |
| FR-04 research-direction suggestion | Phase 5 |
| FR-05 multi-paper integrated slides | Phase 4 (+ chained from Phase 5 for SRS UC-3) |
| FR-06 tagging + project management | Phase 6 |
| FR-07 interactive slide editing | Phase 4 |
| FR-08 Router Agent + classification | Phase 1 (stub) → **Phase 2 (real)** |
| FR-09 NL2SQL | Phase 2 |
| FR-10 MCP tool integration | Phase 1 (`arxiv`, `grobid`) → Phase 3 (bulk: `filesystem`, `sqlite`, `web_search`, `crossref`, plus `paperhub.*` server) → Phase 4 (`latex`, `paperhub.compose_slides`) → Phase 5 (`semantic_scholar`, `paperhub.find_related`) |
| FR-11 tool-call audit log + trace UI | Phase 0 (tracer) + Phase 1 (UI surfacing) |
| FR-12 evaluation harness | Phase 7 |
| NFR-01 performance targets | Phase 8 |
| NFR-02 reliability (retries) | Phase 4 (LaTeX retries) + Phase 8 (external API retries) |
| NFR-03 extensibility (pluggable providers) | `llm/adapter.py` foundation (Phase 0) |
| NFR-04 data security (env-var keys, local SQLite) | `config.py` foundation (Phase 0) |
| NFR-05 usability (Open WebUI layout, bilingual, ≤3 clicks) | Phase 1 (shell) + Phase 6 (project nav polish) |
| NFR-06 maintainability (modular, YAML prompts) | `llm/prompts.py` foundation (Phase 0) |
| NFR-07 cost control (≤ USD 0.30/paper, dashboard) | Phase 8 |
| NFR-08 routing accuracy | Phase 7 (measured by eval harness) |
| NFR-09 auditability + redaction | `tracing/` foundation (Phase 0); replay verified in Phase 8 |
| NFR-10 MCP security boundary | `mcp/client.py` scope-checker foundation (Phase 0); enforced from Phase 3 |
| NFR-11 strict typing | Phase 0 from commit 1; gated in CI |

## 12. Open questions deferred to implementation plan

Items intentionally not pinned in this design — to be decided during writing-plans:

- Embedding model choice (local `sentence-transformers` vs OpenAI `text-embedding-3-small`) — cost vs latency trade-off; either way the 500–1000-token chunking from SRS §RAG is fixed.
- Reranker choice (`bge-reranker` vs Cohere API) — same trade-off; the top-50 → top-5 funnel shape is fixed.
- Whether `paperhub.*` MCP server runs in-process or as a subprocess — affects deployability for external clients.
- Cytoscape layout algorithm and edge-weight visualization details.
- Exact prompt content (the YAML registry is in scope; specific prompt copy is a Phase-1 task).
