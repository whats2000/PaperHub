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
| B — Frontend foundation | **complete** (see [implementation notes](docs/superpowers/plans/2026-05-18-paperhub-B-frontend-foundation-NOTES.md) for tooling-version deviations) | [2026-05-18-paperhub-B-frontend-foundation.md](docs/superpowers/plans/2026-05-18-paperhub-B-frontend-foundation.md) |
| C — Paper Pipeline + Research Agent | pending | not yet written |
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
npm test          # Vitest + RTL + MSW; ~21 tests as of Plan B
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
