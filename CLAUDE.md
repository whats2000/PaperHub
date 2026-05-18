# PaperHub — orientation for Claude Code

This file is loaded into every Claude Code session that opens this repo. Read it first; everything else in the project follows from here.

## What you're working on

PaperHub is a paper-aware chat client with multi-agent tool-routing, an in-repo RAG knowledge base, an in-repo slide pipeline, and a Citation Canvas so every cited chunk traces back to source. It is decomposed from two reference projects (`paper2slides-plus`, `Intro2GenAI-hw1`) — useful utilities are copied + adapted, not run as services.

**Authoritative spec:** [docs/superpowers/specs/2026-05-17-paperhub-srs.md](docs/superpowers/specs/2026-05-17-paperhub-srs.md) (v2.2). Any architecture / schema / scope question is answered there before code. The two-layer schema (`paper_content` for unique papers, `papers` for per-session membership) and the deferred slide-rendering framework choice are the two most load-bearing decisions to keep in mind.

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
- **Test discipline:** every implementation task is TDD. Failing test first, minimal impl, commit.

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

## Where things live

- `backend/src/paperhub/` — application code (db, models, tracing, llm, agents, api, cli)
- `backend/tests/` — pytest suite; fixtures under `tests/fixtures/`
- `backend/scripts/` — operator-facing smoke scripts
- `workspace/` (gitignored) — runtime data: `paperhub.db`, future `papers_cache/`, future `chroma/`
- `reference/` — copied source from `paper2slides-plus` and `Intro2GenAI-hw1` (read-only reference; do not edit in place — copy + adapt into `backend/src/`)
- `docs/superpowers/specs/` — SRS (v2.2 current)
- `docs/superpowers/plans/` — implementation plans

## Plan A known follow-ups

Non-blocking polish flagged during Plan A reviews. Pick these up in a cleanup PR or fold into Plan B as opportunity allows:

1. Expose `Tracer.connection` public property → remove `# noqa: SLF001` in `agents/router.py`.
2. Replace tracer's positional 14-tuple INSERT with named bindings (schema-evolution safety).
3. Drop redundant `apply_schema(conn)` from `api/chat.py` (lifespan already runs it).
4. Drop redundant `await conn.commit()` after `executescript` in `db/migrate.py`.
5. Add explicit `ON DELETE` policy to `papers.paper_content_id`.
6. Decide on FK constraint for `messages.run_id` (currently a soft int reference).
7. Sanitise exception strings before writing them to `messages.content` on the error path of `api/chat.py`.
8. Add SSE error-path + mid-stream cancellation tests for `api/chat.py`.

## Plan B known follow-ups

Tracked here. Highlights:

1. Bundle size 418 KB JS raw — code-split when Citation Canvas + Compare-split land.
2. Drop dead deps in `frontend/package.json`: `autoprefixer`, `postcss`, `tailwindcss-animate`, redundant `@typescript-eslint/*`.
3. Refactor 5 chat-store actions to a shared `updateAssistantMessage` helper.
4. Replace hardcoded `session_id: null` in `useChatStream.ts` once backend session persistence ships.
5. Wire `RejectionPill` when Plan E/G surfaces `status==="rejected"` tool_calls.
6. Replace MessageBubble's inline streaming-dots markup with `<LoadingDots />`.

## Plan C known follow-ups

Non-blocking polish flagged during Plan C reviews. Pick these up in a cleanup PR or fold into Plan D as opportunity allows:

1. **Drop unused `chunker.target` parameter** in `pipelines/chunker.py`. The signature declares `target: int = 800` but the body only uses `hard`. Either implement target-aware early-close at natural boundaries, or drop the parameter. Spec defect carried through Task 4.
2. **Task 8 SQLite transaction polish:** wrap `_persist_paper_content` + `_persist_chunks` in a single transaction (commit once after chunks) to eliminate the partial-write window. Replace `# type: ignore[index]` on `last_insert_rowid()` fetches with `assert row is not None` guards for consistency. Replace bare `assert` in `_link_to_session` with explicit `RuntimeError` for prod safety.
3. **Define a `Reranker` Protocol** in `rag/reranker.py` so `retriever.py` doesn't import the private `_CrossEncoderReranker` name. Mirrors the `Embedder` Protocol pattern.
4. **Schema migration gap:** `paper_content.abstract` was added in Plan C Task 10 but `migrate.py` uses `CREATE TABLE IF NOT EXISTS`, which silently skips column additions on pre-existing DBs. Add a proper migration step (ALTER TABLE) or a versioned migrations table before any team-shared dev DB exists.
5. **Graph / API divergence:** `agents/graph.py` still registers `_stub_paper_search` and `_stub_paper_qa` as graph nodes; the API path bypasses the graph entirely and dispatches directly. If a future task wires `graph.ainvoke()` back into the request path (e.g., for Compare-mode in Plan G), the stubs will silently shadow the real handlers. Update graph wiring before then.
6. **Extract `chat.py`/`papers.py` chroma-fallback helper.** The `getattr(request.app.state, "chroma", None) or ChromaStore(settings.chroma_dir)` pattern is duplicated in both files. Move to a shared `api/deps.py` helper.
7. **`papers.py` assert in production path:** `attach_from_library` uses `assert papers_row is not None` after `INSERT OR IGNORE`+`SELECT`. Replace with `if papers_row is None: raise HTTPException(500, ...)` so `-O` mode doesn't degrade to AttributeError.
8. **`papers.paper_content_id` FK missing `ON DELETE` policy** — generalised Plan A follow-up. Decide CASCADE vs RESTRICT and document.
9. **SQL `LIKE` `%`-stripping for `search_library` and `/papers/library` `q` filter** is a Plan F follow-up — FTS5 will replace `LIKE` and obviate the escape concern. **Note (field test):** the LIKE pattern is also broken for multi-word queries — `%transformers attention%` won't match "transformers and attention". Until FTS5 lands, the agent should be prompted to call `search_library` with single keywords, or pull the FTS5 migration forward into Plan D.
10. **`paper_qa_v1.yaml`** embeds `{chunks_context}` in the system prompt, defeating prompt caching when chunks change between turns. Move to user turn or per-call cache key when Plan E perf work lands.
11. **Empty-references `paper_qa` double-emits** the "No references are enabled…" string as both a `token` event and a `final` event with identical content. Frontend renders correctly but it's a minor wart in the SSE stream.
12. **`paper_qa:retrieve` cold start ~5s** even for `corpus_size=0` — likely embedder/reranker first-load. Fine for prod; pre-warm the singletons in lifespan if test runtimes become a concern.

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
