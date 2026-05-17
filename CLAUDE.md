# CLAUDE.md — PaperHub house rules

Project context, conventions, and constraints for AI sessions working in this repo. Read this before making changes.

## What PaperHub is

**PaperHub** is a modern-stack rewrite + extension of [paper2slides-plus](https://github.com/whats2000/paper2slides-plus). It is a **multi-model and tool-routing AI platform** for academic paper workflows:

- **Inherits** from the predecessor: single arXiv paper → Beamer slide deck with equations + figures preserved, page-level slide editing, LaTeX feedback loop.
- **Adds**: paper knowledge base + RAG Q&A, agentic Router with sub-agents (Research / SQL / Report), MCP tool layer with audit trail, multi-paper integrated slides, evaluation harness.

Stack: **Python 3.12 + FastAPI + LangGraph + LiteLLM** (backend) · **React 18 + Vite + Tailwind 4 + TypeScript** (frontend) · **SQLite + Chroma** (data) · **MCP** for tool integration.

## The First Principle — read this before any framework change

Per **SRS §1.1**: *"Modernizing the framework MUST NOT destroy working logic the predecessor already delivers."*

Three enforcement clauses (all binding):

1. **Source-format fidelity ladder.** Every paper import follows the three-tier hierarchy:
   - **Tier 1 — raw LaTeX source** (lossless): for arXiv, download the unpacked e-print archive (figures + bib + sty + .tex) AND keep the flattened LaTeX for RAG. Same path paper2slides-plus uses.
   - **Tier 2 — Marker** (equation-preserving Markdown — `$...$` inline math survives): for local PDFs / DOIs without LaTeX. **Runs in an isolated Docker container**, not as a PaperHub Python dependency.
   - **Tier 3 — raw text extraction** (lossy last resort): only when Tiers 1 and 2 both fail. Sets `papers.notes_md='low_fidelity_extraction'`. Slide pipeline MAY refuse Tier-3 inputs.
2. **No feature removal — improve, don't simplify.** Every paper2slides-plus capability MUST be present in PaperHub. Performance / fidelity / UX improvements are encouraged; simplifications that reduce the user-visible capability surface are prohibited.
3. **Convergence, not replacement.** When a phase choice compromises a later-phase feature, fix by extending the earlier phase's contract, not by deferring the principle.

If you're tempted to drop or weaken a predecessor capability, **stop and re-read this section**.

## Where things live

```
docs/superpowers/
  specs/2026-05-17-paperhub-srs.md                          ← authoritative SRS (currently v1.11)
  specs/2026-05-17-paperhub-implementation-design.md        ← 3-phase implementation design
  plans/2026-05-17-paperhub-phase-a-foundations-and-qa.md   ← Phase A plan + actual-completion appendix

backend/                              ← Python 3.12, uv-managed
  paperhub/
    config.py                         ← typed Settings singleton (env-driven)
    api/{app.py,routes/,sse.py}       ← FastAPI surface + SSE event types
    agents/{router,research,state}.py ← LangGraph agents
    llm/{adapter,prompts,prompts.yaml}← LlmAdapter Protocol + LiteLlmAdapter + FakeAdapter + YAML prompt registry
    rag/{chunker,embedder,retriever}  ← two-stage RAG funnel
    mcp/{client,scopes,launchers}     ← MCP scope-checker + dispatcher; lifespan-owned sessions
    mcp/tools/grobid_server.py        ← FastMCP wraps for tools we build
    data/{db,models,vectors,migrations} ← SQLite migrations + Pydantic models + Chroma
    tracing/{tracer,redactor}         ← Tool-Call Tracer (FR-11) + secret redaction
  tests/                              ← pytest (mirrors paperhub/ layout)
  .env.example                        ← Gemini-preset by default; copy to .env
  pyproject.toml                      ← uv project; mypy --strict; ruff; pydantic.mypy plugin

frontend/                             ← React + Vite + Tailwind 4 + Vitest
  src/{App,components/,api/,store/}

reference/                            ← gitignored — predecessor projects for reference only
  paper2slides-plus/                  ← LaTeX-first import + Beamer pipeline reference
  Intro2GenAI-hw1/                    ← chat UI / multi-model routing reference

docs/KNOWN-TYPE-GAPS.md               ← every `# type: ignore[<code>]` MUST have a row here
```

## Hard conventions

### Commit messages — Conventional Commits

Format: `action(scope): what you do` (imperative subject, lowercase scope).

Actions: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `perf`, `style`, `build`, `ci`.

Examples:
- `feat(import): add raw e-print archive download for Tier 1`
- `fix(mcp): properly type tracer, redact binary content end-to-end`
- `docs(srs): bump to v1.10 — Tier 1 is unpacked e-print archive`
- `chore: ruff format chunker + test_config`

### Python toolchain

- **`uv` only** — never `pip`, never `python -m venv`. All Python commands run as `uv run …`. CI is `uv sync --frozen`.
- **PowerShell** (Windows native) is the primary shell. Bash works too but PowerShell is what `.ps1` scripts assume.
- **`mypy --strict`** in CI. No bare `# type: ignore` — every ignore needs a `[<error-code>]` AND a row in `docs/KNOWN-TYPE-GAPS.md`.
- **`ruff check` + `ruff format --check`** in CI. No formatting drift.
- **Pydantic v2 / TypedDict / dataclass** for every interface (SRS NFR-11). No `Any`, no untyped `dict` in public function signatures or model-level interfaces. `dict[str, Any]` allowed only at the I/O boundary with an external untyped source, and must be parsed into a typed model before crossing one function call.

### Settings + env vars

- All env-derived config flows through `paperhub.config.Settings` (pydantic-settings).
- **`env_prefix="PAPERHUB_"`** — PaperHub-owned settings use the prefix (e.g. `PAPERHUB_WORKSPACE_ROOT`).
- API keys use `AliasChoices` to accept BOTH the prefixed form AND the ecosystem-standard bare form (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`).
- `get_settings()` calls `dotenv.load_dotenv(override=False)` then re-exports `*_api_key` fields back to `os.environ` so LiteLLM (which reads from `os.environ`) sees them whether they came from `.env`, the shell, or the prefix.
- **NEVER read `backend/.env` directly** — it contains secrets. Tests read keys via `os.environ` at runtime.

### Testing discipline

- **Unit tests (`pytest -m "not e2e"`)** — fast, network-free, use `FakeAdapter` / `FakeEmbedder` / mocked dispatchers. CI runs these on every PR.
- **Live e2e tests (`pytest -m e2e`)** — make real network calls to arXiv + real LLM (Gemini/Anthropic/OpenAI). Skip gracefully if no API key configured. **REQUIRED for any new behavior that crosses an external boundary.** "Unit tests pass" is not enough — if you wire a new MCP server, a new LLM call, or a new external API, you owe a live e2e that exercises it end-to-end.
- Test layout mirrors `paperhub/` exactly. `tests/api/`, `tests/data/`, `tests/llm/`, `tests/mcp/`, `tests/rag/`, `tests/tracing/`, `tests/integration/`.

### MCP discipline

- Every MCP tool flows through `mcp/client.py`'s `McpClient.call()` — which runs the scope-checker BEFORE dispatch AND writes a `tool_calls` audit row.
- `McpInvocation.args` is a Pydantic discriminated union; never a raw `dict`.
- Scope rejections write `status='rejected'` to `tool_calls` and raise `McpScopeViolation` — never silently fail.
- MCP sessions (`stdio_client` + `ClientSession`) are **owned by FastAPI's lifespan**, NOT lazy-launched per request. Per-request `__aenter__` calls cause anyio cancel-scope task-mismatch crashes on cleanup.

### Approval gates (per global CLAUDE.md)

Local, reversible work is fine to proceed without asking: `git add`, `git commit`, branch create/switch, file edits, builds, tests.

**Per-instance explicit approval required:**
- `git push` (any variant, especially `--force`)
- `git merge` / `git rebase` / `git cherry-pick` onto shared branches
- Writing/editing GitHub PR or issue comments
- Submitting PR reviews/approvals (`gh pr review`)
- Opening/closing/merging PRs (`gh pr create`, `gh pr merge`)

When about to take a restricted action, stop, describe the exact command, wait for confirmation.

## Current phase status

- **Phase A — Foundations + paper_qa vertical slice — DONE.** Tagged at `phase-a-complete` on branch `feat/phase-a-foundations`. 94 unit tests + 3 live e2e against real arxiv + Gemini. See the actual-completion appendix in `docs/superpowers/plans/2026-05-17-paperhub-phase-a-foundations-and-qa.md`.
- **Phase B — Pending.** Adds Marker container deployment, agentic search→read→decide→download flow, full 6-intent Router, SQL Agent (NL2SQL), Report Agent + slide pipeline + Slide Editor UI (the paper2slides-plus port — preserves all predecessor capabilities per §1.1), relation analysis + research-direction, project management.
- **Phase C — Pending.** Eval harness, NFR polish, batch import.

## Things that will trip you up

- **`backend/.env` is in `.gitignore` and untrackable.** Don't try to read it (auto-classifier blocks it). Tests use `os.environ` at runtime.
- **`SentenceTransformer` model loading must be lazy** (in `Embedder._ensure_loaded`). Eager `__init__` crashes with Windows OS error 1455 ("paging file too small") on memory-constrained boxes.
- **chromadb 1.x cosine distance is `[0, 2]`** (not `[0, 1]`). Score normalization: `score = 1.0 - dist / 2.0` → maps to `[0, 1]`.
- **`executescript()` does an implicit COMMIT before running** — don't wrap in `BEGIN`/`COMMIT` from Python. Wrap atomic DDL in `BEGIN;` / `COMMIT;` inside the `.sql` file instead.
- **arxiv-latex-mcp returns ONLY the flattened LaTeX text**, not the raw archive. For the slide pipeline we ALSO download the raw `.tar.gz` via `arxiv.Result.download_source()` and unpack it.
- **Phase A only wires 3 MCP tools** (`arxiv`, `arxiv_latex`, `grobid`); the other 3 in SRS §⑧ (`latex`, `filesystem`, `sqlite`, `pdf_extract`) are Phase B work.
- **PowerShell `Out-File -Encoding utf8` writes a BOM.** Use `New-Item -ItemType File` for empty files, or `[System.IO.File]::WriteAllText(path, "")` / `Set-Content -Encoding utf8NoBOM` (PS 6+) for content.

## When in doubt

- Read SRS §1.1 if you're touching the import / extraction / slide pipeline.
- Read the Phase A plan's actual-completion appendix if you're wondering "what's already done."
- Read `docs/KNOWN-TYPE-GAPS.md` before adding any `# type: ignore`.
- Read `docs/superpowers/specs/2026-05-17-paperhub-implementation-design.md` for the cross-cutting foundations + the 3-phase plan.
- Use **Conventional Commits**. Use **uv**. Use **PowerShell**. Type strictly. Trace every MCP call. Don't break paper2slides-plus capabilities.
