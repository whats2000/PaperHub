# PaperHub — orientation for Claude Code

This file is loaded into every Claude Code session that opens this repo. Read it first; everything else in the project follows from here.

## What you're working on

PaperHub is a paper-aware chat client with multi-agent tool-routing, an in-repo RAG knowledge base, an in-repo slide pipeline, and a Citation Canvas so every cited chunk traces back to source. It is decomposed from two reference projects (`paper2slides-plus`, `Intro2GenAI-hw1`) — useful utilities are copied + adapted, not run as services.

**Authoritative spec:** [docs/superpowers/specs/2026-05-17-paperhub-srs.md](docs/superpowers/specs/2026-05-17-paperhub-srs.md) (**v2.10**). Any architecture / schema / scope question is answered there before code. The two-layer schema (`paper_content` for unique papers, `papers` for per-session membership) and the deferred slide-rendering framework choice are the two most load-bearing decisions to keep in mind. v2.7 captured the four-stage `paper_search` decomposition (Parser → Processor [Discover→Resolve] → Finalizer → Synthesizer) and the operational hardening round (opt-in CUDA wheels, device auto-detect, arxiv-ingest resilience, MCP registry cooldown + retry, Windows Proactor loop fix); v2.8 isolated the embedder + reranker into a sibling `paperhub-modelserver` process so weights survive `uvicorn --reload`; v2.9 wired the Composer's paperclip to a new multipart `POST /papers/upload` (PDF) + the existing JSON `POST /papers` (arXiv-ID); v2.10 rebuilt `paper_qa` from dense-RAG map-reduce into an **agentic hierarchical pipeline** (per-paper subagent navigates each paper's section TOC via `list_sections`/`read_section`, flagship finalizer reads the raw cited chunks) + added the **agent-flow observability policy** (every agent step records full reconstruct-able state to `tool_calls`).

## Implementation plan

The SRS is decomposed into 7 sequential plans, each producing working/testable software:

| Plan | Status | Document |
| --- | --- | --- |
| A — Backend foundation + Router-only chat | **complete** | [2026-05-17-paperhub-A-backend-foundation.md](docs/superpowers/plans/2026-05-17-paperhub-A-backend-foundation.md) |
| B — Frontend foundation | **complete** | [2026-05-18-paperhub-B-frontend-foundation.md](docs/superpowers/plans/2026-05-18-paperhub-B-frontend-foundation.md) |
| C — Paper Pipeline + Research Agent | **complete** | [2026-05-18-paperhub-C-paper-pipeline-research-agent.md](docs/superpowers/plans/2026-05-18-paperhub-C-paper-pipeline-research-agent.md) |
| D — Search results + Reference Sources + Citation Canvas | pending | not yet written |
| E — SQL Agent + sqlite MCP | pending | not yet written |
| F — Slide Pipeline + Report Agent | pending | not yet written |
| G — Compare view + paperhub.* MCP + filesystem MCP | pending | not yet written |

When a plan is in flight, it has a corresponding `feat/plan-X-...` branch. The next plan to write is the one whose dependencies are met (see each plan's "depends on" row in the SRS).

## Conventions

- **Commits:** Conventional Commits — `action(scope): imperative subject` (`feat`, `fix`, `docs`, `chore`, `test`, `refactor`). Body wraps at 72 cols.
- **Python tooling:** `uv` — never invoke `pip`, `python -m venv`, or system python. From `backend/`: `uv run pytest`, `uv run ruff check src tests`, `uv run mypy src`.
- **Shell:** PowerShell on Windows. Use PowerShell syntax (`;` to chain, `$LASTEXITCODE`, backtick line continuation). Bash also available but PowerShell is the default.
- **Workflow:** spec → plan → subagent-driven implementation per task → spec compliance review → code quality review → next task. See [superpowers:subagent-driven-development] for the loop.
- **System binaries:** `pandoc` is an optional dependency used by the Paper Pipeline to render LaTeX → HTML for the Citation Canvas. If absent, the pipeline falls back to `pylatexenc` (pure Python, lower quality). Install via `winget install pandoc` on Windows or your package manager elsewhere.
- **`open-websearch` (optional, npm)** — no-key multi-engine web-search MCP server. Used by the **Discoverer** stage of the v2.7 four-stage `paper_search` subgraph (Parser → Processor [Discover→Resolve] → Finalizer → Synthesizer). Install: `npm install -g open-websearch`. The backend's MCP registry can **auto-spawn** the daemon as a managed subprocess (config in `mcp_servers.toml`); operators can also run it standalone via `open-websearch` (with `MODE=http`, listens on `:3000`). If absent, the registry has no reachable `web` server, the Discoverer falls back gracefully (Parser short-circuit + direct Resolver), and behaviour reverts to v2.4 papers-only. Same optional-external posture as `pandoc`. The `paperhub-papers` MCP server is mounted IN-PROCESS at `/mcp` and requires no external install — it ships with the backend.
- **GPU operators (optional)** — torch defaults to CPU-only on a clean `uv sync` (small wheel, fast install). For CUDA boxes: `uv sync --extra cu124` / `--extra cu126` / `--extra cu130` swaps to the matching CUDA torch wheel. Device is auto-detected at runtime via `paperhub.pipelines._device.resolve_device()` (CUDA → MPS → CPU walk); override with `PAPERHUB_DEVICE=cpu|cuda|cuda:1|mps`. The embedder + cross-encoder reranker singletons pass `device=` explicitly so GPU operators don't get silent CPU inference. **In-flight (post-Plan C):** an inference-server extraction is underway to move the embedder + reranker out of the backend process; the lazy-singleton-with-`device=` shape is what keeps that migration low-churn.
- **Test discipline:** every implementation task is TDD. Failing test first, minimal impl, commit.
- **Fix-now policy (no deferred logical issues):** If a review surfaces an issue, fix it before the next task. **Blockers must be fixed. Non-blocker LOGICAL issues must ALSO be fixed.** Only pure stylistic preferences (naming, comment wording with no semantic difference) may be deferred. Deferred logical items have a track record of becoming critical at the next stage — silent shadowing, partial-write windows, schema drift, masked errors — so we close them at source. The "known follow-ups" sections below are for items genuinely out-of-scope (e.g., waiting on a future plan's surface), not for "we'll get to it later." When in doubt, fix it now.
- **Agent-flow observability policy (load-bearing):** for any agent flow (paper_search, paper_qa subagent, finalizer, any future multi-LLM-call topology), every step's `tool_calls` row MUST record enough state to **reconstruct the agent context entirely** from the DB alone. Concretely: record the IDs of every resource the step touched (chunk IDs read, chunk IDs cited, section names listed, paper IDs dispatched, tool-call argument values + tool-result payloads), and the step's final output text. **Do NOT record the rendered prompt** — prompts are templates filled from state, so the input state is sufficient. With this contract, debugging is a SQL query (`SELECT * FROM tool_calls WHERE run_id = X`), not a one-off instrumentation script. **Iron rule: do NOT propose, hypothesize, or commit any fix to an agent-flow bug without first reading the actual recorded pipeline run.** No "I think the LLM is doing X" without evidence from the trace; no "the prompt is too lenient" without a run that shows what the LLM actually saw + returned. If the trace is too thin to determine root cause, the FIRST fix is to enrich the tracer's `record_result` payload — then re-run, then diagnose. The concrete how-to is the next section.

## Agent-flow tracing — how to write a traced step

Every model call, MCP call, and pipeline stage is wrapped in a `Tracer` step. The tracer (`paperhub.tracing.tracer.Tracer`) is constructed per-run (`Tracer(conn, run_id=…, branch=…)`) and threaded into every agent function. The shape is always:

```python
async with tracer.step(agent="research", tool="paper_qa:subagent", model=model) as step:
    step.record_args({...})        # INPUT STATE at step open — IDs, query, params
    ...                            # do the work (LLM call, DB read, tool dispatch)
    step.record_result({...})      # OUTPUT STATE at step close — see field guide below
    # step.mark_error("reason")    # optional: force status='error' without raising
```

What the tracer does for you automatically — **do not duplicate these**:
- `step_index` (monotonic per run), `latency_ms` (wall clock around the `with`), `status` (`ok`, or `error` on exception / `mark_error`), `error` text.
- **Redaction** of `args` + `result` (API keys `sk-…`/`AIza…`, `$HOME` paths) before they hit the DB — via `paperhub.tracing.redactor.redact`. You record plain dicts; redaction is transparent.
- Survives `CancelledError` (client disconnect) — the row is still written.

`tool` naming convention: `<agent>:<stage>` (e.g. `paper_search:parse`, `paper_search:resolve`, `paper_qa:subagent`, `paper_qa:finalize`). Keep names stable — the frontend Trace panel + smoke scripts assert on them.

**`record_result` field guide** (the reconstruct-from-DB contract, per shipped flow):

| Step | Must record |
| --- | --- |
| `paper_search:parse` | `requests` (parsed `{hint, kind}` list) + `llm_content` (raw model output before dedup) |
| `paper_search:discover_plan` | per-iteration `content` + `tool_calls`; the `web.search` sub-step records the actual top-N hits, not just a count |
| `paper_search:resolve` | the SS `query`, `hits` count, the `picked` paper_id, `top` hits, `source` |
| `paper_search:synthesize` | `resolved` (`[{paper_id, title}]`), `not_found` (hints), `content` (prose) |
| `paper_search:finalize` | `emitted_candidates` (`[{paper_id, title, finalize}]`), `resolved_count`, `not_found` |
| `paper_qa:subagent` | `chunks_read_ids`, `chunks_cited_ids`, `listed_sections`, `llm_turns` (per-turn content + tool calls), `tool_call_log` (per-tool args + chunk IDs returned), `final_summary`, `reads_used` |
| `paper_qa:finalize` | `n_papers`, `n_chunks` (args) + streamed `length` (result) |

When you add a new agent step, record the analogous IDs + the final text — enough that `SELECT result_summary_json FROM tool_calls WHERE run_id=? AND tool=?` answers "what did this stage see and decide?" without re-running. New multi-LLM topologies follow the same rule from their first commit, not as a later retrofit.

## Backend quality gates

Before any PR, from `backend/`:

```powershell
uv run pytest -v          # 34+ tests as of Plan A
uv run ruff check src tests
uv run mypy src           # --strict via pyproject
```

End-to-end smoke (mocked LLM, no API key needed):

```powershell
.\scripts\smoke_chat.ps1
```

End-to-end smoke (real LLM, requires `backend/.env` with provider key — see `.env.example`):

```powershell
.\scripts\smoke_chat_real.ps1
```

`smoke_chat_real.ps1` runs two sub-tests: a chitchat turn (legacy) AND a paper_search turn that asserts the MCP dispatch path. The paper_search sub-test auto-detects whether `open-websearch serve` is reachable on `:3000` — daemon UP asserts the v2 `web.search` → `papers.search_semantic_scholar` chain; daemon DOWN asserts the v1 papers-only fallback (zero `web.*` tool_calls rows).

MCP-surface smokes (added in Plan C v2.5/v2.6):

```powershell
.\scripts\smoke_mcp_papers.ps1    # always runnable — boots its own backend on :8770 and exercises the in-process FastMCP `papers` server via the MCP wire protocol
.\scripts\smoke_mcp_web.ps1       # requires `open-websearch serve` running on :3000; exits 1 with a "start the daemon" hint when down
```

Replay any past run from SQLite:

```powershell
uv run paperhub-replay --run-id <N>
```

## Frontend quality gates

Before any PR, from `frontend/`:

```powershell
npm test          # Vitest + RTL + MSW; 25 tests as of Plan B
npm run typecheck # tsc strict
npm run lint      # ESLint flat config
npm run build     # Vite production build
```

End-to-end smoke (backend + frontend together, mocked LLM, from repo root):

```powershell
.\scripts\smoke_e2e.ps1
```

## Dev-environment caveats

- **Model server is a sibling process** (v2.8, see SRS §III-6). The embedder
  (~110 MB SentenceTransformer) and reranker (~80 MB CrossEncoder) live in
  `paperhub.modelserver` running on `127.0.0.1:8001`, NOT inside the uvicorn
  worker. Plain `uv run uvicorn paperhub.app:app --reload --reload-dir src`
  auto-spawns it on first boot via `paperhub.modelserver.spawn.ensure_running`
  — every subsequent `--reload` of the worker reuses the same modelserver
  (TCP probe of `/health` returns 200, spawn is skipped). The subprocess is
  detached (Windows `CREATE_NEW_PROCESS_GROUP` / Unix `start_new_session`)
  with `stdout=DEVNULL`, so it outlives reload cycles. Cleanup is
  intentionally manual: `taskkill /f /im python.exe` filtered, OS reboot, or
  use `scripts/start.ps1` which terminates it in a `finally` block. Opt out
  with `PAPERHUB_INPROCESS_MODELS=1` (loads models in the worker — tests use
  this; expect the embedder to reload on every backend edit).
- **uvicorn `--reload` + concurrent `uv sync`**: if you run pytest in one shell
  while `uvicorn --reload` is active in another, the reload watcher will see
  uv's atomic-install temp dirs in `.venv/Lib/site-packages/` and trigger a
  mid-install reload → `ImportError: cannot import name 'Tokenizer' from
  'tokenizers'`. Mitigation: launch uvicorn with `--reload-dir src` so it
  only watches the source tree (NOT the venv), or stop the dev server
  before running tests.

## Where things live

- `backend/src/paperhub/` — application code (db, models, tracing, llm, agents, api, cli)
- `backend/src/paperhub/modelserver/` — separate FastAPI app hosting embedder + reranker (v2.8)
- `backend/tests/` — pytest suite; fixtures under `tests/fixtures/`
- `backend/scripts/` — operator-facing smoke scripts + `start.ps1` (orchestrates modelserver + backend)
- `workspace/` (gitignored) — runtime data: `paperhub.db`, future `papers_cache/`, future `chroma/`
- `reference/` — copied source from `paper2slides-plus` and `Intro2GenAI-hw1` (read-only reference; do not edit in place — copy + adapt into `backend/src/`)
- `docs/superpowers/specs/` — SRS (**v2.10 current**)
- `docs/superpowers/plans/` — implementation plans

## Plan A known follow-ups

All Plan A follow-ups closed during Plan C cleanup pass.

## Plan B known follow-ups

Items genuinely blocked on future plan surfaces (not lazy-deferred per the fix-now policy):

1. Bundle code-split (currently ~418 KB raw JS) — natural split point lands with Plan D's Citation Canvas component (lazy-load via React.lazy + Suspense). Cannot split usefully before that surface exists.
2. ~~Replace hardcoded `session_id: null` in `useChatStream.ts`~~ — closed in the Plan C v2.4 round; frontend now learns `backend_session_id` from the first SSE event and threads it through subsequent POSTs. (Original concern was that backend session-creation didn't exist; `POST /sessions` shipped in Plan C v2.4 follow-up.)
3. `RejectionPill` is wired but unreachable until Plan E SQL-allowlist or Plan G MCP-permission rejects a tool_call with `status="rejected"`. No frontend change needed; verify the pill renders when those plans land.

## Plan C known follow-ups

Plan C as-shipped includes the v2.4 (suggest-only + SS-primary), v2.5 (MCP client + open-webSearch + paperhub-papers FastMCP), v2.6 stabilisation, v2.7 (four-stage paper_search decomposition + opt-in CUDA + device auto-detect), v2.8 (model server isolation), v2.9 (PDF upload + arXiv-ID manual import), and v2.10 (agentic hierarchical paper_qa + chunker hardening + agent-flow observability) rounds. v2.10 rebuilt `paper_qa` into a per-paper-subagent + finalizer subgraph (section-TOC navigation via `list_sections`/`read_section`), added the `paper_content.sections_json` column + `paperhub-reingest` CLI, and applied the agent-flow observability policy to both paper_qa and paper_search. See [docs/superpowers/plans/2026-05-18-paperhub-C-paper-pipeline-research-agent.md](docs/superpowers/plans/2026-05-18-paperhub-C-paper-pipeline-research-agent.md) Plan C v2.4 / v2.5 / v2.6 / v2.7 / v2.8 / v2.9 / v2.10 sections.

Items genuinely blocked on future plan surfaces (not lazy-deferred per the fix-now policy):

1. ~~**Inference server extraction (in flight, NOT yet a numbered plan)**~~ — closed in v2.8 as a Plan C cleanup pass rather than a separate Plan I. Embedder + reranker now run in `paperhub.modelserver` (FastAPI on `:8001`); the backend's `_HttpEmbedder` / `_HttpReranker` talk to it over httpx. Auto-spawned by lifespan, survives `uvicorn --reload` because the subprocess is detached (Windows `CREATE_NEW_PROCESS_GROUP` / Unix `start_new_session`). See SRS v2.8 + Plan C v2.8 section.
2. **PDF-upload section navigation (v2.10 known gap)** — papers ingested via `POST /papers/upload` (`kind='pdf_upload'`) have empty `sections_json` because the chunker only detects LaTeX `\section{...}`. The paper_qa per-paper subagent's `list_sections` returns `[]`, so the LLM can't navigate a PDF-only paper by section. Fix is either PyMuPDF heading detection (font-size heuristic, mirroring the title extractor) at chunk time, or a subagent "read-all-chunks" fallback when the TOC is empty. Not blocking arxiv/LaTeX papers (which dominate the demo); scoped as a follow-up round.

## Restricted operations

Per the user's global CLAUDE.md, the following require **explicit per-instance approval** — do not auto-run:

- `git push` (any variant), `git merge`/`rebase`/`cherry-pick` onto shared branches
- `gh pr create`, `gh pr merge`, `gh pr review`, `gh pr/issue comment`
- Anything that posts externally or modifies upstream state

Local-only operations (commit, branch, stash, local edits) are fine to proceed on. When in doubt, describe the exact command and wait.

## Pointers to common questions

- "Why two layers (paper_content + papers)?" → SRS §III-7 v2.2 changelog
- "Why is the slide framework deferred?" → SRS §III-5.3
- "How does Compare-mode tracing work?" → SRS §III-7 + FR-04 (one `run_id`, `branch='A'|'B'` discriminator on `tool_calls`)
- "How does the Citation Canvas resolve clicks?" → SRS FR-03 + §III-5.1 Paper Pipeline "Render to HTML" stage
- "What if a paper is referenced from two sessions?" → only one `paper_content` row + cache dir; two `papers` rows; chunks deduped
- "Where do figures live for slides after the cache split?" → SRS §III-5.3 step 4a (figure-path resolution at emit time)
- "Why is `paper_search` four LLM stages?" → SRS v2.7 entry + §III-3 Research Agent row (single-prompt mega-agent failure mode + the decomposition's disjoint-tool-palette guarantee)
- "How does the Discoverer avoid the quoting-kills-DuckDuckGo footgun?" → `paperhub.search_web(paper_hint, extra_terms)` structured-output wrapper hides the free-text query field (SRS v2.7 + Plan C Task v2.7-2)
- "Why is torch CPU-only by default?" → opt-in CUDA wheels via `uv sync --extra cu126` (Plan C Task v2.7-3 + CLAUDE.md GPU operators bullet)
- "Why does the embedder live in a separate process?" → SRS v2.8 + Plan C v2.8 section. Surviving `uvicorn --reload` requires the model weights to live OUTSIDE the worker; auto-spawn with detached subprocess + reuse-via-`/health`-probe means the modelserver outlives any number of backend edits.
- "How do I see the modelserver's logs?" → either run `uv run paperhub-modelserver` directly in a second shell (overrides auto-spawn by being already-reachable when the backend boots), or use `scripts/start.ps1` which orchestrates both processes with visible stdout. Default auto-spawn pipes stdout to DEVNULL (detachment requirement).
- "Tests are failing with `httpx.ConnectError` on embedder calls?" → conftest sets `PAPERHUB_INPROCESS_MODELS=1` at module-import time. If you bypassed conftest (running pytest with `--no-header --confcutdir=/elsewhere`), set the env var manually before pytest starts.
