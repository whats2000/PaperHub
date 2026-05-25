# PaperHub — orientation for Claude Code

This file is loaded into every Claude Code session that opens this repo. Read it first; everything else in the project follows from here.

## What you're working on

PaperHub is a paper-aware chat client with multi-agent tool-routing, an in-repo RAG knowledge base, an in-repo slide pipeline, and a Citation Canvas so every cited chunk traces back to source. It is decomposed from two reference projects (`paper2slides-plus`, `Intro2GenAI-hw1`) — useful utilities are copied + adapted, not run as services.

**Authoritative spec:** [docs/superpowers/specs/2026-05-17-paperhub-srs.md](docs/superpowers/specs/2026-05-17-paperhub-srs.md) (**v2.17**). 
Any architecture / schema / scope question is answered there before code. 
The two-layer schema (`paper_content` for unique papers, `papers` for per-session membership) and the deferred slide-rendering framework choice are the two most load-bearing decisions to keep in mind. v2.7 captured the four-stage `paper_search` decomposition (Parser → Processor [Discover→Resolve] → Finalizer → Synthesizer) and the operational hardening round (opt-in CUDA wheels, device auto-detect, arxiv-ingest resilience, MCP registry cooldown + retry, Windows Proactor loop fix); v2.8 isolated the embedder + reranker into a sibling `paperhub-modelserver` process so weights survive `uvicorn --reload`; v2.9 wired the Composer's paperclip to a new multipart `POST /papers/upload` (PDF) + the existing JSON `POST /papers` (arXiv-ID); v2.10 rebuilt `paper_qa` from dense-RAG map-reduce into an **agentic hierarchical pipeline** (per-paper subagent navigates each paper's section TOC via `list_sections`/`read_section`, flagship finalizer reads the raw cited chunks) + added the **agent-flow observability policy** (every agent step records full reconstruct-able state to `tool_calls`); v2.11 added **router context dispatch** — the history-aware router resolves the latest turn's anaphora into a self-contained `resolved_query` (new `RoutingDecision` field) written to `AgentState.effective_query`, which every downstream agent reads (via `paperhub.agents.state.effective_query`, fallback to raw `user_message`) so a bare follow-up like "推薦幾篇" carries the topic from prior turns; a new `intent="clarify"` surfaces a deliberate clarifying question instead of an empty-results re-ask. v2.12 added a sibling **`paper_suggest`** intent for topic recommendation ("recommend a few papers on X"), distinct from `paper_search` (resolve a specific named paper): it reuses the whole search pipeline, swapping only two prompts — a Parser that decomposes the topic into 2–4 **intersection-anchored** angles (each keeps the topic's domain, e.g. "flow matching *for discrete diffusion*", never bare "flow matching") and a recommendation-toned Synthesizer; the auto-attach Finalizer is reused unchanged. v2.13 shipped **Plan D — the Citation Canvas** (merged to `main`): a right-side reading panel (mirrors the left chat history; push layout) with a paper switcher, opened by the Composer's References toggle or by clicking an inline `[chunk:N]` citation. Clicking a citation switches to + scrolls to + highlights the cited chunk in BOTH the LaTeX-rendered HTML (deterministic `<span id="phchunk-N">` sentinels injected at ingest → highlight the full chunk from one sentinel to the **next sentinel in document order**, with text-search + section-heading fallbacks) AND the source PDF (react-pdf text layer located via the same prefix matcher, highlighted with a **geometry overlay** computed from the page viewport — NOT `customTextRenderer`, which mis-aligns on figure pages). Two load-bearing fixes: a ~20s **PDF↔PDF swap freeze** (tearing down + mounting react-pdf `<Document>` in one synchronous click flush) resolved by `DeferredRemount` (unmount, then mount the new reader on a fresh macrotask); and multi-id citation markers (`[chunk:a, b]`) now parse. v2.13 also added **router language propagation**: the router detects the user's latest-turn language into `RoutingDecision.response_language` → `AgentState.response_language` (read via `paperhub.agents.state.response_language`, fallback "the user's language"), and every final-response prompt writes in that language while keeping `[chunk:<id>]` markers + paper titles verbatim — so a Chinese question is answered in Chinese. v2.15 made **chat sessions + their message records cross-device** (the backend DB is the single source of truth; they were browser-localStorage-only, so a chat was invisible on other devices and a stale local id FK-crashed the chat endpoint): new `GET /sessions` (lists *meaningful* sessions — ≥1 message OR a non-default title; 'New chat' empties excluded), `GET /sessions/{id}/messages` (replays content + the run's routing decision + persisted paper-search cards), and `POST /sessions/{id}/restore`. The frontend `useSessionsSync` **strictly mirrors** the DB — adds listed sessions, prunes any local session whose backend row is gone (incl. a cached copy of a chat deleted on another device), keeping only unsent drafts — and **re-syncs the active session's message record from the DB on every activation** (replace, skipping an in-flight streaming turn so live state isn't clobbered). Deletes are authoritative + immediate: empty+unnamed → hard-delete, *meaningful* → **soft-delete** via a new `chat_sessions.deleted_at` tombstone (Undo = restore); `purge_deleted_sessions` reclaims tombstones past `PAPERHUB_SESSION_RETENTION_DAYS` (default 30) at startup. A stale client `session_id` can no longer FK-crash `_new_run` (`_ensure_session` → `INSERT OR IGNORE`); the first user message persists the session title backend-side. Paper-search result cards persist per turn on `runs.search_results_json` and replay cross-device (the dev-only trace stays streaming-only — cards are the user-facing record). A **boot banner** prints once the whole stack (DB, vectors, model server, MCP) is wired AND model warm-up resolves, so the UI's transient connect-while-booting errors aren't mistaken for a failed boot. v2.16/v2.17 shipped **Plan E — Library Intelligence** (merged to `main`): a `library_stats` NL→SQL agent (Planner → read-only `sql.query` → self-repair → Answer) backed by a new **in-process read-only sqlite MCP server** (`/mcp-sql`, `list_tables`/`describe`/`query`) gated by a deterministic table allowlist + `validate_read_only_sql` (sqlglot; rejects writes/PRAGMA/`memories`); the `library_stats` "my library" scoping was fixed to query `paper_content` (the full deduplicated index) not `papers WHERE session_id` (this chat's references). DuckDB was removed from the SRS. v2.17 added the **session/global Memory subsystem** (homework functional points): a `memories` table + a write-only **in-process memory MCP server** (`/mcp-memory`, `recall`/`add`/`edit`/`forget`) with deterministic scope enforcement (NFR-05 → `status='rejected'`), a **rule-based safety gate** (`classify_memory_safety` refuses secrets/keys/PII + dangerous directives), **LLM conflict-detection → supersede** (`add_memory_with_supersede`: single atomic insert + flip the stale row to `status='superseded'` with `supersedes`/`superseded_by` chain), and **active-only recall**. Scope mapping is **`session` = project / `global` = user**. A REST surface (`GET/POST /memories`, `PATCH`/`DELETE /memories/{id}`, ownership via `X-Paperhub-Session-Id`) drives a frontend **Memory Manager** drawer (Canvas-style animated push-column; view/edit/(de)activate/delete + add; works in an empty chat for global-only memories). Active memories surface to **every answering agent** via the unconditional `build_active_memory_block` (chitchat, paper_search/paper_suggest synthesize, paper_qa finalizer, sql answer) — NOT the router (a small per-turn classifier that can only act on language) — and a remembered language preference **overrides** the router-detected `response_language` per a precedence line in each answer prompt.

## Implementation plan

The SRS is decomposed into 7 sequential plans, each producing working/testable software:

| Plan | Status | Document |
| --- | --- | --- |
| A — Backend foundation + Router-only chat | **complete** | [2026-05-17-paperhub-A-backend-foundation.md](docs/superpowers/plans/2026-05-17-paperhub-A-backend-foundation.md) |
| B — Frontend foundation | **complete** | [2026-05-18-paperhub-B-frontend-foundation.md](docs/superpowers/plans/2026-05-18-paperhub-B-frontend-foundation.md) |
| C — Paper Pipeline + Research Agent | **complete** | [2026-05-18-paperhub-C-paper-pipeline-research-agent.md](docs/superpowers/plans/2026-05-18-paperhub-C-paper-pipeline-research-agent.md) |
| D — Search results + Reference Sources + Citation Canvas | **complete** | [2026-05-21-paperhub-D-citation-canvas.md](docs/superpowers/plans/2026-05-21-paperhub-D-citation-canvas.md) |
| E — SQL Agent + sqlite MCP + session/global memory governance | **complete** | [2026-05-22-paperhub-E-library-intelligence.md](docs/superpowers/plans/2026-05-22-paperhub-E-library-intelligence.md) |
| F — Slide Pipeline + Report Agent | **F1 shipped; F2 shipped; F2.1 shipped; F3 shipped (PhD-grade slide agent); F4 pending** (`feat/plan-F-slide-pipeline`, SRS v2.20) | [F1 — generation + viewing](docs/superpowers/plans/2026-05-23-paperhub-F1-slide-generation-viewing.md) (shipped, generation internals superseded by F3) · F2 — Marker ingestion + PaperAsset (shipped) · [F2.1 — Marker as optional async add-on](docs/superpowers/plans/2026-05-24-paperhub-F2.1-async-marker-upgrade.md) (shipped) · **F3 — PhD-grade slide agent** (shipped, see topology below) · [F4 — presentation + editing](docs/superpowers/plans/2026-05-23-paperhub-F4-slide-presentation-editing.md) |
| G — Compare view + paperhub.* MCP + filesystem MCP | pending | not yet written |

When a plan is in flight, it has a corresponding `feat/plan-X-...` branch. The next plan to write is the one whose dependencies are met (see each plan's "depends on" row in the SRS).

## Conventions

- **Commits:** Conventional Commits — `action(scope): imperative subject` (`feat`, `fix`, `docs`, `chore`, `test`, `refactor`). Body wraps at 72 cols.
- **Python tooling:** `uv` — never invoke `pip`, `python -m venv`, or system python. From `backend/`: `uv run pytest`, `uv run ruff check src tests`, `uv run mypy src`.
- **Shell:** PowerShell on Windows. Use PowerShell syntax (`;` to chain, `$LASTEXITCODE`, backtick line continuation). Bash also available but PowerShell is the default.
- **Workflow:** spec → plan → subagent-driven implementation per task → spec compliance review → code quality review → next task. See [superpowers:subagent-driven-development] for the loop.
- **System binaries:** `pandoc` is an optional dependency used by the Paper Pipeline to render LaTeX → HTML for the Citation Canvas. If absent, the pipeline falls back to `pylatexenc` (pure Python, lower quality). Install via `winget install pandoc` on Windows or your package manager elsewhere. **`pdflatex` (TeX Live / MikTeX) is a HARD requirement for the `slides` intent (Plan F)** — the Report Agent compiles a Beamer deck. If absent, a `slides` turn returns a clear "install a LaTeX distribution" message instead of generating (the rest of the app is unaffected). Install via `winget install MiKTeX.MiKTeX` on Windows; the `metropolis` Beamer theme + Fira fonts give the best output but the pipeline falls back to a built-in theme if they're missing.
- **`marker` (docker-compose service, v2.19 / Plan F2)** — the PDF ingestion engine (`datalab-to/marker`), the project's first compose service (`docker-compose.yml` at repo root). `docker compose up -d marker` builds + runs it on `:8002`; the backend's Paper Pipeline calls it over HTTP for **PDF-only** papers (arXiv papers keep the LaTeX-source path). It returns structured blocks → the unified **`PaperAsset`** (figures+captions, equations→LaTeX, sections) cached under `papers_cache/<key>/asset/`. Notes: (1) the image bakes torch + Surya models via `uv`; the BuildKit cache + a `marker-models` named volume mean a rebuild/failure never re-downloads. (2) **VRAM use scales with page CONTENT density, not page count** — `PAPERHUB_MARKER_MAX_PAGES` (default **1**) makes the backend batch the PDF (Marker's `page_range`, absolute page numbers, blocks concatenated) to bound per-call VRAM. A single dense two-column page (e.g. a medical-journal article) produces 200+ Surya OCR text lines that already saturate ~6 GB VRAM; batching >1 such page tips into the CUDA shared-memory fallback (a 5-page batch took **21 min**; the per-call client timeout is 1800 s). Raise it for bigger GPUs or sparse single-column papers. Existing pre-F2 papers (no `asset/` dir) are migrated by `paperhub-backfill-assets` (Marker for PDF/arxiv-via-pdf sources, LaTeX for arxiv source; idempotent, strictly sequential — concurrent conversions would OOM the GPU). (3) Set **`GEMINI_API_KEY`** (host env / repo-root `.env`) to enable Marker's **`use_llm` accuracy pass** (better tables/math/layout) — performance-over-price; keyless runs without it. (4) After editing `marker_service/app.py`, `docker compose build marker` to pick it up. Tests mock the Marker HTTP client (no Docker needed); only real PDF ingestion needs the service.
- **`open-websearch` (optional, npm)** — no-key multi-engine web-search MCP server. Used by the **Discoverer** stage of the v2.7 four-stage `paper_search` subgraph (Parser → Processor [Discover→Resolve] → Finalizer → Synthesizer). Install: `npm install -g open-websearch`. The backend's MCP registry can **auto-spawn** the daemon as a managed subprocess (config in `mcp_servers.toml`); operators can also run it standalone via `open-websearch` (with `MODE=http`, listens on `:3000`). If absent, the registry has no reachable `web` server, the Discoverer falls back gracefully (Parser short-circuit + direct Resolver), and behaviour reverts to v2.4 papers-only. Same optional-external posture as `pandoc`. The `paperhub-papers` MCP server is mounted IN-PROCESS at `/mcp` and requires no external install — it ships with the backend.
- **GPU operators (optional)** — torch defaults to CPU-only on a clean `uv sync` (small wheel, fast install). For CUDA boxes: `uv sync --extra cu124` / `--extra cu126` / `--extra cu130` swaps to the matching CUDA torch wheel. Device is auto-detected at runtime via `paperhub.pipelines._device.resolve_device()` (CUDA → MPS → CPU walk); override with `PAPERHUB_DEVICE=cpu|cuda|cuda:1|mps`. The embedder + cross-encoder reranker run in the sibling `paperhub-modelserver` process (v2.8) and pass `device=` explicitly so GPU operators don't get silent CPU inference.
- **Test discipline:** every implementation task is TDD. Failing test first, minimal impl, commit.
- **Fix-now policy (no deferred logical issues):** If a review surfaces an issue, fix it before the next task. **Blockers must be fixed. Non-blocker LOGICAL issues must ALSO be fixed.** Only pure stylistic preferences (naming, comment wording with no semantic difference) may be deferred. Deferred logical items have a track record of becoming critical at the next stage — silent shadowing, partial-write windows, schema drift, masked errors — so we close them at source. The "known follow-ups" sections below are for items genuinely out-of-scope (e.g., waiting on a future plan's surface), not for "we'll get to it later." When in doubt, fix it now.
- **Agent-flow observability policy (load-bearing):** for any agent flow (paper_search, paper_qa subagent, finalizer, any future multi-LLM-call topology), every step's `tool_calls` row MUST record enough state to **reconstruct the agent context entirely** from the DB alone. Concretely: record the IDs of every resource the step touched (chunk IDs read, chunk IDs cited, section names listed, paper IDs dispatched, tool-call argument values + tool-result payloads), and the step's final output text. **Do NOT record the rendered prompt** — prompts are templates filled from state, so the input state is sufficient. With this contract, debugging is a SQL query (`SELECT * FROM tool_calls WHERE run_id = X`), not a one-off instrumentation script. **Iron rule: do NOT propose, hypothesize, or commit any fix to an agent-flow bug without first reading the actual recorded pipeline run.** No "I think the LLM is doing X" without evidence from the trace; no "the prompt is too lenient" without a run that shows what the LLM actually saw + returned. If the trace is too thin to determine root cause, the FIRST fix is to enrich the tracer's `record_result` payload — then re-run, then diagnose. The concrete how-to is the next section.

## Agent-flow tracing — how to write a traced step

**Any new agent flow MUST follow the record principle from its first commit** — wrap every model/MCP/pipeline step in a `Tracer` step and record enough state to reconstruct it from the DB. The shape:

```python
async with tracer.step(agent="research", tool="paper_qa:subagent", model=model) as step:
    step.record_args({...})        # input state: IDs, query, params
    ...                            # do the work
    step.record_result({...})      # output state: IDs touched + final text (NOT the prompt)
    # step.mark_error("reason")    # optional: force status='error' without raising
```

The tracer auto-captures `step_index`, `latency_ms`, `status`/`error`, redaction of args+result (keys + `$HOME`), and survives `CancelledError` — don't duplicate those. Name tools `<agent>:<stage>` (the Trace panel asserts on the names). What to put in `record_result`: the IDs of every resource the step touched (chunks read/cited, sections listed, papers dispatched, tool args + results) and the step's final output — enough that the trace answers "what did this stage see and decide?" without re-running.

### Tracing back a chat session (any agent flow)

When a turn misbehaves, reconstruct it from SQLite — no instrumentation script, no guessing. The workspace DB is `backend/workspace/paperhub.db`.

1. **Find the run.** A session has one `runs` row per turn:
   ```sql
   SELECT id, status, routing_decision_json FROM runs WHERE session_id = ? ORDER BY id DESC;
   ```
2. **See the step DAG** (which agent/stage fired, status, latency):
   ```powershell
   uv run paperhub-replay --run-id <N>
   ```
3. **Read the full recorded state of any step** — this is where the reconstruct-able payload lives (per the record principle above):
   ```sql
   SELECT step_index, tool, args_redacted_json, result_summary_json, error
   FROM tool_calls WHERE run_id = ? ORDER BY step_index;
   ```
   `result_summary_json` holds the IDs touched + the stage's output (chunk IDs read/cited, sections listed, resolved/emitted candidates, the LLM's final text). That payload — not a re-run, not the prompt — is what you diagnose from.

This works identically for every flow (paper_search, paper_qa subagent, finalizer, any future topology) precisely because they all obey the record principle. **Iron rule (restated): read the recorded run before proposing any agent-flow fix.** If the trace can't answer the question, the first fix is to enrich that step's `record_result`, then re-run.

## Backend quality gates

Before any PR, from `backend/`:

```powershell
uv run pytest -v          # 34+ tests as of Plan A
uv run ruff check src tests
uv run mypy src           # --strict via pyproject
```

**pytest measures SYNTAX + MECHANISM, NOT process correctness.** A stubbed-adapter test proves the wiring compiles and the control flow runs — it does NOT prove the real LLM obeys a prompt (language adherence, figure grounding, citation discipline), that the SSE stream emits, or that state persists/replays. **The actual correctness test is a live user-simulation + reading the recorded trace. Run it ONCE when a whole PLAN PHASE is fully done — NOT after each individual task / functional point** (per-task verification stays pytest/ruff/mypy only; don't interrupt the user for a real-API run mid-plan). Treat "pytest green" as necessary-but-insufficient; a plan is not "verified" until a real `:8000` run confirms it at the end.

### Real-API test process (run against the user's live backend on `:8000`)

Do NOT write a committed script for this, and do NOT boot your own backend — use the one the user runs (frontend + modelserver + MCP wired). The procedure:

1. **Check `:8000` is live** — `curl -s -m 3 http://127.0.0.1:8000/health`. **If it is NOT reachable, STOP and ASK the user to start the backend** (e.g. `scripts/start.ps1`); do not spin up your own instance to work around it (a separate instance has stale code / wrong wiring and races the user's DB).
2. **Call the API as a user would** (the same HTTP calls the frontend makes — `curl`/`Invoke-RestMethod`, ad-hoc): `POST /sessions` → `POST /papers` (add a paper for paper/slide flows) → `POST /chat` with a real `user_message`; read the streamed SSE result. Use the actual scenario under test (e.g. the user's exact wording, the target language).
3. **Verify the recorded trace** for that run from SQLite (the agent-flow record principle): `uv run paperhub-replay --run-id <N>` or `SELECT step_index, tool, status, result_summary_json FROM tool_calls WHERE run_id = ?` — confirm the right stages fired, `status=ok`, and the recorded state matches the answer/deck (right figures cited, language honored, no hallucinated keys, …).
4. **When the API checks pass, ASK the user to open the frontend and confirm the result visually** (the chat card, the deck/Slides panel, the citation highlight, the streamed trace) — the final human-in-the-loop sign-off. Note any change that needs a `:8000` restart (backend code) or a frontend rebuild to be visible.

This catches the class of bug unit tests miss (a prompt the model ignores, an SSE stage that emits nothing, a card that doesn't replay). It is a required gate at **plan-phase completion** (not per task).

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
- **External MCP daemons are spawned with `subprocess.Popen`, not asyncio**
  (and via the boot script, not the worker, on the supported path). uvicorn's
  `use_subprocess` (`reload or workers > 1`) makes its loop factory directly
  instantiate a `SelectorEventLoop` on Windows — bypassing the
  `WindowsProactorEventLoopPolicy` set in `app.py` — and `SelectorEventLoop`
  raises `NotImplementedError` on `asyncio.create_subprocess_exec`. So under
  the documented `--reload` dev flow, an in-worker asyncio spawn of
  `open-websearch` always failed silently on Windows. The fix: every
  `launch`-declaring MCP server is launched via `paperhub.mcp.launcher.launch_detached`
  (a detached `subprocess.Popen`, loop-independent — same primitive the model
  server uses). `scripts/start.ps1` runs `paperhub-mcp-up` (reads
  `mcp_servers.toml`, launches all `has_launch` servers) before the backend;
  the in-worker registry autostart is a bare-`uvicorn` fallback. Spawned
  daemons are **detach-and-leak** (NOT terminated on worker shutdown) so
  reloads don't re-pay the ~25s npx cold start. Skip with `start.ps1 -NoWebSearch`.
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
- `backend/scripts/` — operator-facing scripts + `start.ps1` (orchestrates external MCP daemons via `paperhub-mcp-up` + modelserver + backend)
- `workspace/` (gitignored) — runtime data: `paperhub.db`, future `papers_cache/`, future `chroma/`
- `reference/` — copied source from `paper2slides-plus` and `Intro2GenAI-hw1` (read-only reference; do not edit in place — copy + adapt into `backend/src/`)
- `docs/superpowers/specs/` — SRS (**v2.15 current**)
- `docs/superpowers/plans/` — implementation plans

## Plan A known follow-ups

All Plan A follow-ups closed during Plan C cleanup pass.

## Plan B known follow-ups

Items genuinely blocked on future plan surfaces (not lazy-deferred per the fix-now policy):

1. ~~Bundle code-split (currently ~418 KB raw JS) — natural split point lands with Plan D's Citation Canvas component (lazy-load via React.lazy + Suspense). Cannot split usefully before that surface exists.~~ **Closed in Plan D**: `CitationCanvas` is now lazy-loaded via `React.lazy` + `Suspense` in `ChatPage.tsx`, code-split into its own chunk (`CitationCanvas-*.js`, ~2.8 KB). The main bundle is still large; further splitting remains available as future work but the first split point promised here is delivered.
2. `RejectionPill` is wired but unreachable until Plan E SQL-allowlist or Plan G MCP-permission rejects a tool_call with `status="rejected"`. No frontend change needed; verify the pill renders when those plans land.

## Plan C known follow-ups

All Plan C follow-ups closed. The last open item — PDF-upload section navigation — was closed by font-size heading detection in `extract_pdf_with_headings` (chunker `sections=`/`strip_comments=` params, wired through the 3 PDF ingest branches + `paperhub-reingest`), so `kind='pdf_upload'` papers now get a real `sections_json` the paper_qa subagent can navigate. Shipped-round history (v2.4–v2.12) lives in the SRS changelog + the top-of-file summary.

## Plan D known follow-ups

Plan D is shipped + merged to `main` (v2.13). One genuinely out-of-scope item remains (not lazy-deferred):

1. **HTML exact-chunk highlight only fires for chunks that have a `dom_id`.** A chunk whose sentinel landed in math/dangerous spans is skipped at ingest (`postprocess_sentinels` → `dom_id=None`), so it falls back to text-search (block) or section-heading scroll instead of the exact marker→next-marker range. Closing this is a **backend** sentinel-placement improvement (`pipelines/sentinels.py` — safer injection points / a fallback anchor for skipped chunks), not a frontend change. The frontend resolver already degrades gracefully.

## Plan E known follow-ups

Plan E is shipped + merged to `main` (v2.16/v2.17). Genuinely out-of-scope items (not lazy-deferred):

1. **`library_stats` auto-attach is prose-only.** The SQL agent answers with numbers + the SQL it ran but does not surface result rows as attachable cards the way `paper_search` does — out of scope for a stats answer.
2. **Memory recall is unconditional all-active, not semantic.** `build_active_memory_block` returns every active memory (≤20, global + session) so a standing directive always surfaces; an FTS/semantic relevance filter (the `build_memory_context_block` path) is kept for reference but unused by the answer agents. If a user's active-memory set grows large, the clean split is "language resolved cheaply + content recalled by relevance." Env-flagged semantic recall remains a stub.
3. **One legacy memory row (id=3) has charset corruption** from an earlier dev session; cosmetic, delete via the Memory Manager if it surfaces.

## Restricted operations

Per the user's global CLAUDE.md, the following require **explicit per-instance approval** — do not auto-run:

- `git push` (any variant), `git merge`/`rebase`/`cherry-pick` onto shared branches
- `gh pr create`, `gh pr merge`, `gh pr review`, `gh pr/issue comment`
- Anything that posts externally or modifies upstream state

Local-only operations (commit, branch, stash, local edits) are fine to proceed on. When in doubt, describe the exact command and wait.

## Pointers to common questions

- "Why are chat sessions/records the same on every device now?" → v2.15: the backend DB is the source of truth. `GET /sessions` + `GET /sessions/{id}/messages` + the frontend `useSessionsSync` strict mirror (prune anything not in the DB; re-sync the active session's messages from the DB on each activation, skipping a streaming turn). Sessions soft-delete (`chat_sessions.deleted_at`, Undo via `POST /sessions/{id}/restore`); empty+unnamed hard-delete; `purge_deleted_sessions` reclaims tombstones past `PAPERHUB_SESSION_RETENTION_DAYS`. Paper-search cards persist on `runs.search_results_json`. SRS v2.15 changelog.
- "Why does `library_stats` count `paper_content`, not `papers`?" → "my library" means the full deduplicated index (every unique paper), not this chat's references (`papers WHERE session_id`). v2.16 fix. The SQL agent only touches a read-only table allowlist via the in-process sqlite MCP (`/mcp-sql`), guarded by `validate_read_only_sql`.
- "Where does memory live + how is it governed?" → `memories` table; write MCP at `/mcp-memory` (`recall`/`add`/`edit`/`forget`) with deterministic scope enforcement. Add path = safety gate (`classify_memory_safety`) → LLM conflict-detect → `add_memory_with_supersede` (atomic insert + flip stale row to `superseded`). Recall is active-only. Scope: `session`=project, `global`=user. REST `/memories` drives the Memory Manager UI. SRS v2.17.
- "Why does a remembered 'reply in Japanese' apply to every answer?" → active memories are injected into EVERY answering agent (chitchat, paper_search/suggest synth, paper_qa finalizer, sql answer) via the unconditional `build_active_memory_block` — NOT the router (a small classifier that can only act on language) — and each answer prompt has a precedence line: a remembered language overrides the router-detected `response_language`. v2.17 + the b14a93d commit.
- "Why two layers (paper_content + papers)?" → SRS §III-7 v2.2 changelog
- "Why was the F1 slide generator redesigned (SRS v2.19)?" → A real-API test showed F1 produced conference-UNusable decks: hallucinated/wrong figures (crude PyMuPDF `img_N` + cross-paper filename collision), overflowing frames never fixed (the `_revise` was a no-op + Overfull is a warning), incomplete notes. The redesign = **F2 (Marker ingestion → `PaperAsset`)** + **F3 (PhD-grade slide agent)**; the old F2 (presentation+editing) became **F4**. Marker (datalab-to/marker, a docker-compose service) replaces PyMuPDF for PDF papers (real figures+captions, equations→LaTeX, structured sections); arXiv keeps the LaTeX-source path; both → one `PaperAsset`. The slide agent is rebuilt to `sl_resolve → understand → narrate → draft(slide+note pairs) → coherence → assemble → verify_figures (deterministic no-hallucination) → compile (Overfull-aware loop) → notes_finalize → emit`, with three hard contracts (concise slides/rich notes, no non-existent figures, self-correcting layout). Cost no object; quality paramount.
- "How are slides generated? (F3 — current)" → Plan F3 (SRS v2.19): the **Report Agent** is a traced LangGraph subgraph (`agents/report_graph.py`) with the topology `sl_resolve → sl_understand (per-paper PaperBrief, fan-out) → sl_narrate (one cross-paper TalkOutline) → sl_draft (per-slide concise frame ⟂ rich note, fan-out) → sl_coherence → sl_assemble (stage real figures by deck-unique key) → sl_verify_figures → sl_compile (Overfull-aware revise loop) → sl_notes_finalize → sl_emit`. Three hard contracts: **(1) concise slides / rich notes** — frames stay brief, notes carry deep explanation; **(2) no hallucinated figures** — `sl_assemble` builds a deck-wide collision-free figure inventory from F2's `PaperAsset` (keys `p{idx}-{figure_id}`), stages real files into `slides/figures/`; `sl_verify_figures` (`verify_and_fix_graphics`) deterministically replaces any `\includegraphics{KEY}` whose key is not in the inventory with `\textit{[figure omitted]}`; **(3) self-correcting layout** — `sl_compile` loops `pdflatex` up to 3 times, calling `sl_revise` on Overfull/layout errors between attempts. `sl_notes_finalize` maps drafted notes to PDF page numbers deterministically (`finalize_notes`: always produces exactly `page_count` entries, gap pages → "(continued)"). One deck per session (`decks` table); a `deck` SSE event (fields: `deck_id`, `session_id`, `page_count`, `title`, `status`, `contributing_papers`, `has_notes`) drives the frontend deck chip + **Slides panel**. Consumes F2's `PaperAsset` (arXiv: LaTeX-source path; PDF-only: Marker-ingested blocks → figures+captions+equations+sections).
- "How were slides generated in F1? (historical, superseded)" → Plan F1 (SRS v2.18, §III-5.3): the **Report Agent** was `sl_resolve → sl_plan → sl_sections (asyncio.gather fan-out) → assemble → compile → notes → emit`. It used crude PyMuPDF `img_N` figure extraction (cross-paper filename collision), a no-op `_revise` (Overfull never fixed), and incomplete notes. Superseded by F3.
- "Why was the slide framework Marp/Slidev/Beamer decision resolved to Beamer?" → SRS v2.18: conference-ready academic output needs lossless math + real figures + tikz; the cost (hard `pdflatex` dep + compile-fix loop) is accepted. The compile loop runs `pdflatex` off the event loop via `asyncio.to_thread`.
- "How does Compare-mode tracing work?" → SRS §III-7 + FR-04 (one `run_id`, `branch='A'|'B'` discriminator on `tool_calls`)
- "How does the Citation Canvas resolve clicks?" → SRS FR-03 + §III-5.1 Paper Pipeline "Render to HTML" stage
- "What if a paper is referenced from two sessions?" → only one `paper_content` row + cache dir; two `papers` rows; chunks deduped
- "Where do figures live for slides after the cache split?" → SRS §III-5.3 step 4a (figure-path resolution at emit time)
- "Why is `paper_search` four LLM stages?" → SRS v2.7 entry + §III-3 Research Agent row (single-prompt mega-agent failure mode + the decomposition's disjoint-tool-palette guarantee)
- "Why does a bare follow-up like '推薦幾篇' / 'recommend a few' now work?" → the history-aware router resolves anaphora into a self-contained `resolved_query`; downstream agents read `effective_query` (router-set, fallback to raw `user_message`) so the topic from prior turns is carried. When even history can't resolve it, the router returns `intent="clarify"` with a question. SRS v2.11 + Plan C v2.11 sections.
- "How does the Discoverer avoid the quoting-kills-DuckDuckGo footgun?" → `paperhub.search_web(paper_hint, extra_terms)` structured-output wrapper hides the free-text query field (SRS v2.7 + Plan C Task v2.7-2)
- "Why is torch CPU-only by default?" → opt-in CUDA wheels via `uv sync --extra cu126` (Plan C Task v2.7-3 + CLAUDE.md GPU operators bullet)
- "Why does the embedder live in a separate process?" → SRS v2.8 + Plan C v2.8 section. Surviving `uvicorn --reload` requires the model weights to live OUTSIDE the worker; auto-spawn with detached subprocess + reuse-via-`/health`-probe means the modelserver outlives any number of backend edits.
- "How do I see the modelserver's logs?" → either run `uv run paperhub-modelserver` directly in a second shell (overrides auto-spawn by being already-reachable when the backend boots), or use `scripts/start.ps1` which orchestrates both processes with visible stdout. Default auto-spawn pipes stdout to DEVNULL (detachment requirement).
- "Tests are failing with `httpx.ConnectError` on embedder calls?" → conftest sets `PAPERHUB_INPROCESS_MODELS=1` at module-import time. If you bypassed conftest (running pytest with `--no-header --confcutdir=/elsewhere`), set the env var manually before pytest starts.
