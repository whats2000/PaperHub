# Contributing to PaperHub

Thanks for your interest in PaperHub! This document explains how to set up the
project, the conventions we follow, and the quality gates every change must pass
before it can be merged.

By participating, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## Table of contents

- [Ways to contribute](#ways-to-contribute)
- [Project layout](#project-layout)
- [Development setup](#development-setup)
- [Quality gates](#quality-gates)
- [Commit conventions](#commit-conventions)
- [Branching & pull requests](#branching--pull-requests)
- [Testing discipline](#testing-discipline)
- [Reporting bugs & requesting features](#reporting-bugs--requesting-features)
- [Security issues](#security-issues)

## Ways to contribute

- **Report a bug** — open a [Bug report](https://github.com/whats2000/PaperHub/issues/new?template=bug_report.yml).
- **Request a feature** — open a [Feature request](https://github.com/whats2000/PaperHub/issues/new?template=feature_request.yml).
- **Improve docs** — typo fixes, clarifications, and examples are always welcome.
- **Submit code** — pick up an open issue or propose a change (please open an
  issue first for anything non-trivial so we can agree on the approach).

## Project layout

PaperHub is a Python (FastAPI + LangGraph) backend and a TypeScript (React 19 +
Vite) frontend. The authoritative spec lives in
[`docs/superpowers/specs/`](docs/superpowers/specs/) and implementation plans in
[`docs/superpowers/plans/`](docs/superpowers/plans/).

| Path | What lives there |
| --- | --- |
| `backend/src/paperhub/` | Application code (db, models, tracing, llm, agents, api, cli) |
| `backend/tests/` | pytest suite + fixtures |
| `backend/benchmark/` | Config-driven real-API end-to-end benchmark harness |
| `frontend/` | React + Vite client |
| `docs/superpowers/` | SRS spec + implementation plans |
| `reference/` | Read-only source copied from upstream reference projects |

## Development setup

### Prerequisites

- **Python 3.11** with [`uv`](https://docs.astral.sh/uv/) — we use `uv`
  exclusively; never invoke `pip`, `python -m venv`, or system `python`.
- **Node.js** (LTS) with `npm` for the frontend.
- **Optional system binaries:**
  - `pdflatex` (TeX Live / MiKTeX) — **required** for the slide pipeline.
  - `pandoc` — optional; improves LaTeX→HTML rendering for the Citation Canvas.
  - Docker — only needed for real PDF ingestion via the `marker` service.

### Backend

```bash
cd backend
uv sync                      # CPU-only torch by default
# GPU operators: uv sync --extra cu124  (or cu126 / cu130)
```

### Frontend

```bash
cd frontend
npm install
```

See the [README](README.md) for how to run the full stack
(`backend/scripts/start.ps1` orchestrates the model server, MCP daemons, and the
backend).

## Quality gates

Every change must pass the gates for the part of the tree it touches. CI runs
these on every pull request, but please run them locally first.

**Backend** (from `backend/`):

```bash
uv run pytest -v             # unit + integration tests
uv run ruff check src tests  # lint
uv run mypy src              # strict type-checking
```

**Frontend** (from `frontend/`):

```bash
npm test          # Vitest + RTL + MSW
npm run typecheck # tsc --strict
npm run lint      # ESLint flat config
npm run build     # Vite production build
```

> **Note:** `pytest` measures syntax + mechanism, not end-to-end correctness.
> Whole-plan-phase changes are additionally verified against a live backend via
> the `backend/benchmark/` harness. You don't need to run the real-API
> benchmark for an ordinary PR — green unit gates plus a clear description are
> enough.

## Commit conventions

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <imperative subject>
```

- **Types:** `feat`, `fix`, `docs`, `chore`, `test`, `refactor`.
- The subject is imperative and lowercase ("add", not "added"/"Adds").
- The body (optional) wraps at 72 columns and explains *why*, not *what*.

Examples:

```
feat(slides): let users edit speaker notes in the Slides panel
fix(router): resolve anaphora in bare follow-up turns
docs: add contributing guide and issue templates
```

## Branching & pull requests

1. Branch off `main` using a descriptive name: `feat/...`, `fix/...`,
   `docs/...`.
2. Keep PRs focused — one logical change per PR.
3. Make sure all [quality gates](#quality-gates) pass locally.
4. Fill out the pull request template completely.
5. Link the issue your PR closes (`Closes #123`).

We follow a **fix-now policy**: logical issues surfaced in review are fixed
before merge, not deferred. Only pure stylistic preferences may be left for
follow-up.

## Testing discipline

PaperHub is developed test-first (TDD): write the failing test, make it pass
with the minimal change, then commit. New behavior needs a test; bug fixes need
a regression test that fails before the fix and passes after.

## Reporting bugs & requesting features

Please use the issue templates — they prompt for the details we need (repro
steps, environment, expected vs. actual). For agent-flow bugs (paper search,
paper Q&A, slides, SQL), the run is fully reconstructible from SQLite, so
including the `run_id` (or the output of `uv run paperhub-replay --run-id <N>`)
dramatically speeds up diagnosis.

## Security issues

**Do not open a public issue for security vulnerabilities.** Instead, report
them privately through
[GitHub Security Advisories](https://github.com/whats2000/PaperHub/security/advisories/new)
and we will respond as quickly as we can.

---

Thank you for helping make PaperHub better! 💙
