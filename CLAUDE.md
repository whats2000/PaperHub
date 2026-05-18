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
- **`open-websearch` (optional, npm)** — no-key multi-engine web-search MCP server. Required for the v2 `paper_search` prompt (discover-then-refine flow). Install: `npm install -g open-websearch`. Run: `open-websearch serve` in a separate shell — the daemon listens on `:3000`. If absent, the backend's MCP registry simply doesn't expose `web.*` tools and the Research Agent falls back to the v1 (papers-only) prompt. Same posture as `pandoc`. The `paperhub-papers` MCP server is mounted IN-PROCESS at `/mcp` and requires no external install — it ships with the backend.
- **Test discipline:** every implementation task is TDD. Failing test first, minimal impl, commit.
- **Fix-now policy (no deferred logical issues):** If a review surfaces an issue, fix it before the next task. **Blockers must be fixed. Non-blocker LOGICAL issues must ALSO be fixed.** Only pure stylistic preferences (naming, comment wording with no semantic difference) may be deferred. Deferred logical items have a track record of becoming critical at the next stage — silent shadowing, partial-write windows, schema drift, masked errors — so we close them at source. The "known follow-ups" sections below are for items genuinely out-of-scope (e.g., waiting on a future plan's surface), not for "we'll get to it later." When in doubt, fix it now.

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

- **uvicorn `--reload` + concurrent `uv sync`**: if you run pytest in one shell
  while `uvicorn --reload` is active in another, the reload watcher will see
  uv's atomic-install temp dirs in `.venv/Lib/site-packages/` and trigger a
  mid-install reload → `ImportError: cannot import name 'Tokenizer' from
  'tokenizers'`. Mitigation: launch uvicorn with `--reload-exclude '.venv/**'`,
  or stop the dev server before running tests.

## Where things live

- `backend/src/paperhub/` — application code (db, models, tracing, llm, agents, api, cli)
- `backend/tests/` — pytest suite; fixtures under `tests/fixtures/`
- `backend/scripts/` — operator-facing smoke scripts
- `workspace/` (gitignored) — runtime data: `paperhub.db`, future `papers_cache/`, future `chroma/`
- `reference/` — copied source from `paper2slides-plus` and `Intro2GenAI-hw1` (read-only reference; do not edit in place — copy + adapt into `backend/src/`)
- `docs/superpowers/specs/` — SRS (v2.2 current)
- `docs/superpowers/plans/` — implementation plans

## Plan A known follow-ups

All Plan A follow-ups closed during Plan C cleanup pass.

## Plan B known follow-ups

Items genuinely blocked on future plan surfaces (not lazy-deferred per the fix-now policy):

1. Bundle code-split (currently ~418 KB raw JS) — natural split point lands with Plan D's Citation Canvas component (lazy-load via React.lazy + Suspense). Cannot split usefully before that surface exists.
2. Replace hardcoded `session_id: null` in `useChatStream.ts` once backend session-creation API ships. Backend currently auto-creates a session per chat request; once `POST /sessions` exists, frontend should create + persist a session id.
3. `RejectionPill` is wired but unreachable until Plan E SQL-allowlist or Plan G MCP-permission rejects a tool_call with `status="rejected"`. No frontend change needed; verify the pill renders when those plans land.

## Plan C known follow-ups

All Plan C follow-ups closed.

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
