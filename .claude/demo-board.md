# Demo board

Project config for the `demo-review-skills` plugin (`demo-review-flow`).

- **Board:** https://claude.ai/artifact/2A5BNdWaZEK4LtAJXDviXP (title "PaperHub 筆記示範"; extend it for non-notes areas too)
- **Board source file (local working copy, may be gone in a new session — then `Artifact read` the URL and publish back to it):** the session scratchpad `notes-demo.html`
- **Spec to update when a port ships:** `docs/superpowers/specs/2026-05-17-paperhub-srs.md` (the matching bullet, in its own `docs(srs)` commit)
- **Active branch / worktree for ports:** `feat/plan-h5b1-notes-core` in `D:\GitHub\PaperHub-h5b` (until H5b ships)

## Reference apps per area
| Area | Look at first | Then |
|---|---|---|
| Notes / block editor | Notion | Obsidian (where Notion has nothing) |
| Tabs (notes / reader) | VS Code | Obsidian |
| Find in page | Browsers | VS Code |
| Table borders and styles | Word | Google Docs / Sheets |

## Widths to check
- Notes pane widths the user works at: **580 px** and **715 px** (set via localStorage `paperhub-canvas-v1` = `{"state":{"width":715},"version":0}`)
- Also 400 px, light and dark

## Live app
- Frontend http://localhost:5173, backend :8000 — the branch's code on the user's **REAL data**: read-only browsing only (no Delete / Remove / Add / Save / Accept clicks, no typing into notes, no migrations)
- Workspace copy for checks that change data: frontend :4173, backend :8110, workspace `D:/paperhub-scratch-h5b1-t11`
  - start: `backend/scripts/start.ps1 -BackendPort 8110 -Workspace D:/paperhub-scratch-h5b1-t11 -McpConfig D:/paperhub-scratch-h5b1-t11/mcp_servers.toml -NoWebSearch`; Vite with `VITE_API_BASE_URL=http://localhost:8110` on `--port 4173 --strictPort`
- Migrations: dry run + real run on the copy; the live workspace (`D:\GitHub\PaperHub\backend\workspace`) only with the user's explicit approval and a backup first (`D:/paperhub-backups/`)

## Gates
- Frontend (from `frontend/`): `npx vitest run <touched area>`, `npm run typecheck`, `npm run lint`, `npm run build`
- Backend (from `backend/`, when touched): `uv run pytest <touched tests>`, `uv run ruff check src tests`, `uv run mypy src`
- i18n: every new key in en / zh-TW / zh-CN / ja (`src/locales/parity.test.ts`)
- Never stage the untracked `backend/src/paperhub/notes/markdown_inline.py` / `backend/tests/notes/test_markdown_inline.py`

## Notes
- Feature text on the board is written in zh-TW; `stage` = the sub-plan the item ships in (e.g. H5b.1).
- The user does not review plans; approved items are dispatched at once.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
