# PaperHub — orientation for Claude Code

This file is loaded into every Claude Code session that opens this repo. Read it first; everything else in the project follows from here.

## What you're working on

PaperHub is a paper-aware chat client with multi-agent tool-routing, an agentic SQLite section-navigation knowledge base (no vector store, v2.27), an in-repo slide pipeline, and a Citation Canvas so every cited chunk traces back to source. It is decomposed from two reference projects (`paper2slides-plus`, `Intro2GenAI-hw1`) — useful utilities are copied + adapted, not run as services.

**Authoritative spec:** [docs/superpowers/specs/2026-05-17-paperhub-srs.md](docs/superpowers/specs/2026-05-17-paperhub-srs.md) (**v2.37.1** latest spec; shipped through **v2.37.1** — **slide source grounding + manual & structured-citation editing** (per-slide `% cite:` grounding → Sources strip → Citation Canvas; content-only LaTeX frame/whole-deck editors with keep-last-good-on-failure recompile; a deterministic per-slide reference editor; whole-section highlight) + **slide deck-length fixes** (LLM length extraction, configurable default, degenerate-output gate) + **LLM model fallback** (v2.36); **new-session paper attach** (lazy-create the backend session so a fresh chat can take its first paper) + **attach processing feedback** (v2.35); **SQL Agent → intelligent ReAct agent** with curated `library_stats` attachable cards (E1) (v2.34); **Plan F6.1** slide narrative planning + the PaperDigest/targeted-read gather rework + always-on streaming (v2.33); **Plan G** — UI i18n across 8 namespaces × 4 locales + account menu + a DB-backed runtime Settings panel (v2.31)). 
Any architecture / schema / scope question is answered there before code. 
The two-layer schema (`paper_content` for unique papers, `papers` for per-session membership) and the deferred slide-rendering framework choice are the two most load-bearing decisions to keep in mind. The full v2.4-v2.33 feature history and rationale live in the SRS Revision History; read it there for any deeper why-does-X question rather than duplicating it here.

## Implementation plan

The SRS is decomposed into 7 sequential plans, each producing working/testable software:

| Plan | Status | Document |
| --- | --- | --- |
| A — Backend foundation + Router-only chat | **complete** | [2026-05-17-paperhub-A-backend-foundation.md](docs/superpowers/plans/2026-05-17-paperhub-A-backend-foundation.md) |
| B — Frontend foundation | **complete** | [2026-05-18-paperhub-B-frontend-foundation.md](docs/superpowers/plans/2026-05-18-paperhub-B-frontend-foundation.md) |
| C — Paper Pipeline + Research Agent | **complete** | [2026-05-18-paperhub-C-paper-pipeline-research-agent.md](docs/superpowers/plans/2026-05-18-paperhub-C-paper-pipeline-research-agent.md) |
| D — Search results + Reference Sources + Citation Canvas | **complete** | [2026-05-21-paperhub-D-citation-canvas.md](docs/superpowers/plans/2026-05-21-paperhub-D-citation-canvas.md) |
| E — SQL Agent + sqlite MCP + session/global memory governance | **complete** | [2026-05-22-paperhub-E-library-intelligence.md](docs/superpowers/plans/2026-05-22-paperhub-E-library-intelligence.md) |
| F — Slide Pipeline + Report Agent | **complete** | [F1](docs/superpowers/plans/2026-05-23-paperhub-F1-slide-generation-viewing.md) · [F2.1](docs/superpowers/plans/2026-05-24-paperhub-F2.1-async-marker-upgrade.md) · [F4](docs/superpowers/plans/2026-05-25-paperhub-F4-slide-decoupling-editing.md) · [F4.2](docs/superpowers/plans/2026-05-27-paperhub-F4.2-slide-style-customization.md) · [F4.3](docs/superpowers/plans/2026-05-29-paperhub-F4.3-non-arxiv-pdf-ingestion.md) · [F5](docs/superpowers/plans/2026-06-05-paperhub-F5-presentation-voice.md) |
| F6 — Slide narrative planning + grounding traceback + theme | **F6.1 complete** (planning + PaperDigest/targeted-read gather rework + streaming); F6.2 (Sources panel) · F6.3 (theme) pending | [F6.1](docs/superpowers/plans/2026-06-12-paperhub-F6.1-slide-narrative-planning.md) |
| G — Frontend UI i18n + account menu + DB-backed runtime Settings panel | **complete** | [2026-06-09-paperhub-G-i18n-settings-panel.md](docs/superpowers/plans/2026-06-09-paperhub-G-i18n-settings-panel.md) |
| H — Compare view + paperhub.* MCP + filesystem MCP | pending (deferred behind G) | not yet written |

When a plan is in flight, it has a corresponding `feat/plan-X-...` branch. The next plan to write is the one whose dependencies are met (see each plan's "depends on" row in the SRS).

## Conventions

- **Commits:** Conventional Commits — `action(scope): imperative subject` (`feat`, `fix`, `docs`, `chore`, `test`, `refactor`). Body wraps at 72 cols.
- **Python tooling:** `uv` — never invoke `pip`, `python -m venv`, or system python. From `backend/`: `uv run pytest`, `uv run ruff check src tests`, `uv run mypy src`.
- **Shell:** PowerShell on Windows. Use PowerShell syntax (`;` to chain, `$LASTEXITCODE`, backtick line continuation). Bash also available but PowerShell is the default.
- **Workflow:** spec → plan → subagent-driven implementation per task → spec compliance review → code quality review → next task. See [superpowers:subagent-driven-development] for the loop.
- **Finishing / releasing a branch:** ALWAYS run the **`paperhub-merge-prep`** skill first — it updates the release files the generic flow misses (the four README locales' badges + citation, the three version manifests + their lockfiles, the SRS revision-history row, the CLAUDE.md pointers), then stops for merge/tag/push approval. The local **`finishing-a-development-branch`** skill overrides `superpowers:finishing-a-development-branch` to enforce this — do NOT finish/merge a PaperHub branch without merge-prep. Any commit fix-up obeys the **`safe-amend`** skill (never amend a pushed commit; the user pushes out of band, so `git fetch` + remote check is mandatory before any amend).
- **System binaries:** `pandoc` is an optional dependency used by the Paper Pipeline to render LaTeX → HTML for the Citation Canvas. If absent, the pipeline falls back to `pylatexenc` (pure Python, lower quality). Install via `winget install pandoc` on Windows or your package manager elsewhere. **`pdflatex` (TeX Live / MikTeX) is a HARD requirement for the `slides` intent (Plan F)** — the Report Agent compiles a Beamer deck. If absent, a `slides` turn returns a clear "install a LaTeX distribution" message instead of generating (the rest of the app is unaffected). Install via `winget install MiKTeX.MiKTeX` on Windows; the `metropolis` Beamer theme + Fira fonts give the best output but the pipeline falls back to a built-in theme if they're missing.
- **`marker` (docker-compose service, v2.19 / Plan F2)** — the PDF ingestion engine (`datalab-to/marker`), the project's first compose service (`docker-compose.yml` at repo root). `docker compose up -d marker` builds + runs it on `:8002`; the backend's Paper Pipeline calls it over HTTP for **PDF-only** papers (arXiv papers keep the LaTeX-source path). It returns structured blocks → the unified **`PaperAsset`** (figures+captions, equations→LaTeX, sections) cached under `papers_cache/<key>/asset/`. Notes: (1) the image bakes torch + Surya models via `uv`; the BuildKit cache + a `marker-models` named volume mean a rebuild/failure never re-downloads. (2) **VRAM use scales with page CONTENT density, not page count** — `PAPERHUB_MARKER_MAX_PAGES` (default **1**) makes the backend batch the PDF (Marker's `page_range`, absolute page numbers, blocks concatenated) to bound per-call VRAM. A single dense two-column page (e.g. a medical-journal article) produces 200+ Surya OCR text lines that already saturate ~6 GB VRAM; batching >1 such page tips into the CUDA shared-memory fallback (a 5-page batch took **21 min**; the per-call client timeout is 1800 s). Raise it for bigger GPUs or sparse single-column papers. Existing pre-F2 papers (no `asset/` dir) are migrated by `paperhub-backfill-assets` (Marker for PDF/arxiv-via-pdf sources, LaTeX for arxiv source; idempotent, strictly sequential — concurrent conversions would OOM the GPU). (3) Set **`GEMINI_API_KEY`** (host env / repo-root `.env`) to enable Marker's **`use_llm` accuracy pass** (better tables/math/layout) — performance-over-price; keyless runs without it. (4) After editing `marker_service/app.py`, `docker compose build marker` to pick it up. Tests mock the Marker HTTP client (no Docker needed); only real PDF ingestion needs the service.
- **`open-websearch` (optional, npm)** — no-key multi-engine web-search MCP server. Used by the **Discoverer** stage of the v2.7 four-stage `paper_search` subgraph (Parser → Processor [Discover→Resolve] → Finalizer → Synthesizer). Install: `npm install -g open-websearch`. The backend's MCP registry can **auto-spawn** the daemon as a managed subprocess (config in `mcp_servers.toml`); operators can also run it standalone via `open-websearch` (with `MODE=http`, listens on `:3000`). If absent, the registry has no reachable `web` server, the Discoverer falls back gracefully (Parser short-circuit + direct Resolver), and behaviour reverts to v2.4 papers-only. Same optional-external posture as `pandoc`. The `paperhub-papers` MCP server is mounted IN-PROCESS at `/mcp` and requires no external install — it ships with the backend.
- **Test discipline:** every implementation task is TDD. Failing test first, minimal impl, commit.
- **Render-change validation (load-bearing — NEVER assume a render fix is side-effect-free).** Any change to the HTML render path (`pipelines/renderer.py`, `table_figures.py`, `figures.py`, `sentinels.py`, the `paper_pipeline` render stages) MUST be validated against a **COPY of the workspace** before touching the real one — a render tweak that "obviously" only fixes one paper has repeatedly perturbed others. The procedure: (1) copy the workspace (`papers_cache` + `paperhub.db`) to an isolated dir and rewrite the copy's DB `source_dir_path`/`source_path` to the copy location; (2) re-process the copy from **cached source — skip re-download** (`paperhub-reingest` re-chunks, `paperhub-rerender-html` re-renders); (3) **diff the copy against the original on BOTH axes**: `source.html` (the change must touch ONLY the intended broken markup — e.g. the empty check/cross cells — and leave every other paper byte-identical) AND **chunks** (`char_start`/`char_end`/`text` per `chunks` row MUST be identical — the render path and the chunk path are separate, `chunk_text` runs on `source.flattened.tex` while the fix runs on `source.render.tex`, so chunking must be provably unaffected, NOT assumed). (4) Only after the copy diff confirms "fixes the broken part, breaks nothing else, chunking unchanged" do you re-process the real workspace + the deploy cache (full ingest via `backend/scripts/ingest_paper.ps1`'s mechanism). A pure-function render substitution should be a no-op on papers lacking the targeted construct — PROVE it (`fn(render_tex) == render_tex` for the untouched set), don't claim it.
- **Frontend i18n (every new/changed UI feature must include it):** **avoid fixed/hardcoded language text in components** — render user-facing strings through `t("<namespace>:<key>", "English fallback")` (`react-i18next`; `en` is the source-of-truth catalog under `src/locales/{en,zh-TW,zh-CN,ja}/*.json`) and add the key to **all four** locales. Backend-provided labels/help (e.g. the settings registry's `label`/`help`) get localized frontend-side by key with the backend English as the `t()` fallback — don't render the raw backend string. `src/locales/parity.test.ts` fails the suite if any locale is missing a key. Stays literal by design: agent-trace tool ids, `[chunk:N]` markers, paper titles, code, enum/model-id values.
- **Fix-now policy (no deferred logical issues):** If a review surfaces an issue, fix it before the next task. **Blockers must be fixed. Non-blocker LOGICAL issues must ALSO be fixed.** Only pure stylistic preferences (naming, comment wording with no semantic difference) may be deferred. Deferred logical items have a track record of becoming critical at the next stage — silent shadowing, partial-write windows, schema drift, masked errors — so we close them at source. The "known follow-ups" sections below are for items genuinely out-of-scope (e.g., waiting on a future plan's surface), not for "we'll get to it later." When in doubt, fix it now.
- **Agent-flow observability policy (load-bearing):** for any agent flow (paper_search, paper_qa subagent, finalizer, any future multi-LLM-call topology), every step's `tool_calls` row MUST record enough state to **reconstruct the agent context entirely** from the DB alone. Concretely: record the IDs of every resource the step touched (chunk IDs read, chunk IDs cited, section names listed, paper IDs dispatched, tool-call argument values + tool-result payloads), and the step's final output text. **Do NOT record the rendered prompt** — prompts are templates filled from state, so the input state is sufficient. With this contract, debugging is a SQL query (`SELECT * FROM tool_calls WHERE run_id = X`), not a one-off instrumentation script. **Iron rule: do NOT propose, hypothesize, or commit any fix to an agent-flow bug without first reading the actual recorded pipeline run.** No "I think the LLM is doing X" without evidence from the trace; no "the prompt is too lenient" without a run that shows what the LLM actually saw + returned. If the trace is too thin to determine root cause, the FIRST fix is to enrich the tracer's `record_result` payload — then re-run, then diagnose. The concrete how-to is the next section.
- **Enrich-then-diagnose rule for external-API calls (corollary of the above). IRON RULE — VIOLATING THIS HAS COST THE PROJECT MULTIPLE DEBUG SESSIONS; TREAT AS LOAD-BEARING.** This is OPPORTUNISTIC, not blanket: you don't pre-record everything from every external call. The rule fires when, AND ONLY when, you sit down to diagnose a failure and find the existing trace can't answer your question. At that moment the ONLY allowed next action is to enrich the relevant step's `record_result` **in this same commit**, then re-run the failing scenario, then diagnose from the enriched trace. NOT "I'll note this as a follow-up", NOT "let me probe the external service directly to see what it returned" (the trace should have answered it after enrichment), NOT "guess and patch". The enrichment is the FIRST fix of the debugging session; the actual bug fix comes after.

  **TRIGGER PHRASES — if you find yourself thinking or typing any of these about a step's recorded result, you are about to violate this rule; enrich the step's `record_result` instead:**
  - "the trace doesn't show ..." / "we can't tell from the trace whether ..."
  - "this would help future debugging" / "we should record this for next time"
  - "I'll note this as a follow-up" / "out of scope for this commit"
  - "let me probe the external service directly to see what it returned"
  - "I don't know what happened upstream, but ..."

  What to add when the trigger fires (typical for an HTTP-backed external call): the request payload, the HTTP status code, the first ~500 chars of the body (or the parsed response object), any error/header signal like `retry-after`. A summarised result like `{"hits": 0, "source": "ss_by_title"}` cannot distinguish a true empty from a 429 silently coerced to "no hits", a 5xx, a malformed response, or a wrong-query-parameter bug. **Redact secrets** (keys, bearer tokens) but never the structure / status / shape. Default is still "record only what the next stage needs to reconstruct state"; this rule kicks in only when that default has proven insufficient on a real bug.

- **MCP tool return-payload convention (the meta-in-payload rule).** Diagnostic context for an MCP call has to travel **in the response payload itself** — there is no cross-task side channel. The MCP HTTP request runs in the mounted sub-app's task tree; the resolver/caller is in a different task; a `ContextVar` stashed at the SS module *cannot* reach the caller. So when an MCP tool wraps external work that the caller may need to diagnose (an HTTP call, a subprocess, a privileged DB hit), the tool's return MUST be enveloped as:

  ```python
  {"result": <original payload>, "_meta": {<diagnostic keys>}}
  ```

  `_meta` is an open dict — fields are tool-specific. For HTTP-backed tools, the canonical set is `{source, http_status, attempts, body_head, retry_after, url_path, count}` (same fields the enrich-then-diagnose rule above already names). For local/SQL/FTS tools, at least `{source, count, <query-form-as-sent>}`. Callers MUST unwrap via the shared `paperhub.agents._mcp_result.normalize_mcp_result` (or its successor) which peels the `{"result": X}` single-key wrapper — and attach `_meta` to the tracer step's `record_result` so the trace contains the diagnostic without a re-run. **NEVER `isinstance(mcp_call_result, list)` on a raw return.** FastMCP auto-wraps `list` returns as `{"result": [...]}`; a naked isinstance check evaluates False and silently drops every hit. This bug literally cost the project a multi-day debugging session against `paper_search` (the resolver dropped every title-only SS hit because `_StubRegistry` in tests returned a bare list while prod returned the wrapped dict — tests green, prod silently broken). When in doubt, unwrap first, then check.

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

**Two distinct surfaces; pick by purpose:**

- **Sweeps / behaviour gates** — multiple cases at once, scored, regressionable: the **`backend/benchmark/` harness** (config-driven, committed). Drives the live backend as a simulated user (attach cached papers → route prompts through `/chat`), collects grounding evidence (cited chunk text + agent trace) into a JSON + Markdown report, and scores each case **0/1** on correctness + grounding — by hand or via the built-in **LLM-as-Judge** (`benchmark/judge.py`, fixed temperature 0, strict grounding). Cases live in TOML (`cases.example.toml` = 20-case eval; `cases.smoke.toml` = one per intent). Run from `backend/`: `scripts/run-benchmark.ps1 [-Judge] [-Only ids] [-Resume prior.json]`. See [`backend/benchmark/README.md`](backend/benchmark/README.md). **Use this at plan-phase completion or when adding a regression case.**
- **One-off bug reproduction** — a single user message / trace to diagnose: **direct API call**. `POST /sessions` → optional `POST /papers` → `POST /chat` with the exact user wording → read `runs.id ORDER BY id DESC LIMIT 1` → trace it via `paperhub-replay --run-id <N>` or `SELECT step_index, tool, status, result_summary_json FROM tool_calls WHERE run_id = ?`. **Use this for "investigate why run X did Y" — the harness is overkill for a single trace and won't preserve the user's exact failing input shape.**

Still do NOT boot your own backend — use the one the user runs (frontend + MCP wired); a separate instance has stale code / wrong wiring and races the user's DB. The procedure either path follows:

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

- **External MCP daemons are spawned with `subprocess.Popen`, not asyncio**
  (and via the boot script, not the worker, on the supported path). uvicorn's
  `use_subprocess` (`reload or workers > 1`) makes its loop factory directly
  instantiate a `SelectorEventLoop` on Windows — bypassing the
  `WindowsProactorEventLoopPolicy` set in `app.py` — and `SelectorEventLoop`
  raises `NotImplementedError` on `asyncio.create_subprocess_exec`. So under
  the documented `--reload` dev flow, an in-worker asyncio spawn of
  `open-websearch` always failed silently on Windows. The fix: every
  `launch`-declaring MCP server is launched via `paperhub.mcp.launcher.launch_detached`
  (a detached `subprocess.Popen`, loop-independent). `scripts/start.ps1` runs
  `paperhub-mcp-up` (reads `mcp_servers.toml`, launches all `has_launch`
  servers) before the backend; the in-worker registry autostart is a
  bare-`uvicorn` fallback. Spawned daemons are **detach-and-leak** (NOT
  terminated on worker shutdown) so reloads don't re-pay the ~25s npx cold
  start. Skip with `start.ps1 -NoWebSearch`.
- **uvicorn `--reload` + concurrent `uv sync`**: if you run pytest in one shell
  while `uvicorn --reload` is active in another, the reload watcher will see
  uv's atomic-install temp dirs in `.venv/Lib/site-packages/` and trigger a
  mid-install reload → `ImportError: cannot import name 'Tokenizer' from
  'tokenizers'`. Mitigation: launch uvicorn with `--reload-dir src` so it
  only watches the source tree (NOT the venv), or stop the dev server
  before running tests.

## Where things live

- `backend/src/paperhub/` — application code (db, models, tracing, llm, agents, api, cli)
- `backend/tests/` — pytest suite; fixtures under `tests/fixtures/`
- `backend/benchmark/` — config-driven real-API e2e benchmark harness (driver/config/resolve/scorer/runner + `judge.py` LLM-as-Judge); TOML cases; `results/` gitignored. The committed real-API behaviour gate (supersedes `smoke_*.ps1`).
- `backend/scripts/` — operator-facing scripts + `start.ps1` (orchestrates external MCP daemons via `paperhub-mcp-up` + backend) + `run-benchmark.ps1` (benchmark launcher)
- `workspace/` (gitignored) — runtime data: `paperhub.db`, `papers_cache/`
- `reference/` — copied source from `paper2slides-plus` and `Intro2GenAI-hw1` (read-only reference; do not edit in place — copy + adapt into `backend/src/`)
- `docs/superpowers/specs/` — SRS (**v2.37.1 current**; shipped through **v2.37.1**)
- `docs/superpowers/plans/` — implementation plans
- `docs/presentation/` — **project introduction deck** build script (`build_deck.js` via `pptxgenjs`) + README. A 17-slide Swiss-modernist deck (Traditional Chinese content) in cobalt + Arial Black + Microsoft JhengHei. Run `cd docs/presentation && npm install && node build_deck.js` to output `PaperHub_專案介紹.pptx` at the repo root (`*.pptx` is gitignored). Screenshots are embedded directly from `docs/screenshots/`. To rework the layout / change colors / change the slide count, edit `build_deck.js` and re-run.

## Known follow-ups (open items only)

Plans A–G are shipped + merged; closed follow-ups live in the SRS Revision History. Genuinely open, out-of-scope-when-written items (audited 2026-06-13 against `main`):

- **(E) Memory recall is all-active, not semantic** *(deferred by design)* — `build_active_memory_block` (5 live call sites) returns every active memory (≤20); the FTS/semantic `build_memory_context_block` has **zero call sites** (dead code, kept intentionally so a standing directive like "respond in Japanese" always surfaces). Revisit if a user's active set grows large.

## Plan G review — anything blocking Plan H?

**No.** Plan G (i18n + DB-backed Settings panel, v2.31) shipped; its one notable YAGNI cut — *"no live model-availability validation"* — was **closed by the v2.32 readiness gate** (`GET /settings/readiness`). Nothing in G blocks **Plan H** (Compare view + `paperhub.*` MCP + filesystem MCP): H reuses the already-shipped MCP client infra (add one `[[server]]` block to `mcp_servers.toml`).

## Restricted operations

Per the user's global CLAUDE.md, the following require **explicit per-instance approval** — do not auto-run:

- `git push` (any variant), `git merge`/`rebase`/`cherry-pick` onto shared branches
- `gh pr create`, `gh pr merge`, `gh pr review`, `gh pr/issue comment`
- Anything that posts externally or modifies upstream state

Local-only operations (commit, branch, stash, local edits) are fine to proceed on. When in doubt, describe the exact command and wait.

## Pointers to common questions

One-liners — the full version lives in the SRS (its Revision History is authoritative). Read the SRS § / version / file named at the end of each.

- **Two layers (`paper_content` + `papers`)** → unique papers vs per-session membership; chunks dedup on `paper_content`. SRS §III-7 (v2.2).
- **No vector store (v2.27)** → `paper_qa` navigates sections via `list_sections`/`read_section` + the SQLite `chunks` table (since v2.10); backend is CPU-only + torch-free. SRS §III-5.4.
- **`paper_search` = 4 LLM stages** → Parser → Processor[Discover→Resolve] → Finalizer → Synthesizer (disjoint tool palettes vs a mega-agent). SRS v2.7 + §III-3.
- **`paper_suggest` vs `paper_search`** → topic recommendation vs resolve-a-named-paper; same pipeline, swaps Parser (intersection-anchored angles) + Synthesizer. SRS v2.12.
- **Bare follow-up ("推薦幾篇") works** → router resolves anaphora into `resolved_query`; agents read `effective_query`; `intent="clarify"` when unresolvable. SRS v2.11.
- **`library_stats` counts `paper_content`, not `papers`** → "my library" = full dedup index. Read-only sqlite MCP `/mcp-sql` + `validate_read_only_sql`. SRS v2.16.
- **Memory** → `memories` table + write MCP `/mcp-memory`; add = safety gate → conflict-detect → `add_memory_with_supersede`; active-only recall; scope `session`=project / `global`=user. SRS v2.17.
- **Remembered language applies everywhere** → injected into every answering agent via `build_active_memory_block` (not the router); overrides router-detected `response_language`. SRS v2.17.
- **Slides (F3)** → `agents/report_graph.py` subgraph `sl_resolve→understand→narrate→draft→coherence→assemble→verify_figures→compile→notes_finalize→emit`; contracts: concise slides/rich notes, no hallucinated figures, self-correcting layout. SRS v2.19.
- **Slide notes decoupled (F4)** → GENERATE = slides only; an existing deck routes via `classify_deck_command` to NOTES / EDIT / regenerate; `decks.speaker_notes_json` is a derived cache. SRS v2.21.
- **Title page / deck style (F4.2)** → real editable `\titlepage` frame + `edit_title`/`edit_preamble` deck-command actions (`get_preamble`→…→`replace_preamble`). SRS v2.22.
- **Slide-deck length** → `parse_slide_budget` (explicit count wins, else minutes×0.75, else 15, clamp [8,30]). SRS v2.21.
- **Beamer (not Marp/Slidev)** → lossless math + figures + tikz; cost = `pdflatex` dep + compile-fix loop (off the event loop). SRS v2.18.
- **Slide-aware QA (v2.29)** → on-screen slide answered via `paper_qa` (no deck mutation); composer eye-chip gates `build_slide_context`; router: deck *question*→paper_qa, *command*→slides. SRS v2.29.
- **Fork-a-message (v2.30)** → rollback/branch (NOT edit-in-place) via `POST /sessions/{id}/fork`; `db/fork.py:fork_session` copies everything above the message (deck best-effort post-commit); forked text prefills the composer; sidebar lineage via `forked_from_session_id`. SRS v2.30.
- **`ss:` tries an OA-URL list (F4.3)** → SS papers often lack `openAccessPdf.url`; with `PAPERHUB_UNPAYWALL_EMAIL` the dispatcher tries every Unpaywall OA URL, else an amber "Manual download" card. Never UA-spoof anti-bot. SRS F4.3.
- **Cross-device sessions (v2.15)** → backend DB is source of truth; `GET /sessions` + `/sessions/{id}/messages` + `useSessionsSync` strict mirror; soft-delete tombstones (`deleted_at`). SRS v2.15.
- **Presentation page-sync (F5)** → `present.html` 2nd Vite entry fetches the deck PDF; SlidesPanel broadcasts `{page}` over `BroadcastChannel`; `presenting`/`currentPageBySession` live in the slides store. SRS v2.26.
- **Voice input (F5)** → composer mic via Web Speech (`lib/speech.ts`); continuous, manual send; hidden where unsupported; TTS deferred. SRS v2.26.
- **First-run config gate + tour (v2.32)** → `GET /settings/readiness` pre-flights a 1-token ping per gate model; composer locks + tour fire only on a *definitive* failure (`hasBlockingConfigIssue`); verified config cached in localStorage. SRS v2.32.
- **Citation Canvas click resolution** → SRS FR-03 + §III-5.1 "Render to HTML".
- **Citation Canvas scroll/crash (v2.23)** → `scrollIntoViewStable` eases scrollTop to a moving target; defer wrap+scroll to a macrotask. SRS v2.23.
- **Compare-mode tracing** → one `run_id`, `branch='A'|'B'` on `tool_calls`. SRS §III-7 + FR-04.
- **`:8080` empty after `docker compose` rebuild (v2.24)** → nginx caches the backend IP at startup → 502; fix = embedded DNS resolver + variable `proxy_pass` in `frontend/nginx.conf`. SRS v2.24.
- **Figures for slides after the cache split** → SRS §III-5.3 step 4a.
