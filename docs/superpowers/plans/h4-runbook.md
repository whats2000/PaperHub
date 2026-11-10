# H4 runbook: an isolated instance beside v2.41

H4 runs on its own backend (`:8100`), frontend (`:4173`) and a COPY of the
workspace (`D:/paperhub-h4-workspace`). v2.41 (`D:/GitHub/PaperHub`, `:8000` /
`:5173`) is never touched. All commands are PowerShell.

## 1. One-time setup (from the H4 worktree)

```powershell
cd D:/GitHub/PaperHub-h4/backend
uv sync
cd ../frontend; npm ci
Copy-Item D:/GitHub/PaperHub/backend/.env D:/GitHub/PaperHub-h4/backend/.env   # gitignored, never commit
```

## 2. Back up v2.41's workspace

Uses the SQLite backup API (the DB has WAL files; never a plain file copy).
Read-only on the source, safe while v2.41 is running.

```powershell
cd D:/GitHub/PaperHub-h4/backend
uv run paperhub-copy-workspace --src D:/GitHub/PaperHub/backend/workspace --dest D:/paperhub-backups/v2.41-<date>
```

## 3. Make the H4 workspace

```powershell
uv run paperhub-copy-workspace --src D:/GitHub/PaperHub/backend/workspace --dest D:/paperhub-h4-workspace
```

Stored absolute paths (`paper_content.source_path/source_dir_path/html_path`,
`decks.tex_path/pdf_path`) are rewritten to the new location.

## 4. H4 MCP config

Copy `D:/GitHub/PaperHub/backend/mcp_servers.toml` to
`D:/paperhub-h4-workspace/mcp_servers.toml` and change the `papers`, `sql` and
`memory` URLs from `localhost:8000` to `localhost:8100` (`/mcp`, `/mcp-sql`,
`/mcp-memory`). Without this, the H4 SQL / memory calls would hit v2.41's DB.
The `web` server (`:3000`) may stay shared.

## 5. Start H4

```powershell
cd D:/GitHub/PaperHub-h4/backend
$env:PAPERHUB_MARKER_WORKER='0'; $env:PAPERHUB_ARXIV_BACKFILL='0'; $env:PAPERHUB_CITATION_EDGES='0'
.\scripts\start.ps1 -BackendPort 8100 -Workspace D:/paperhub-h4-workspace -McpConfig D:/paperhub-h4-workspace/mcp_servers.toml -NoWebSearch
```

Frontend (second shell):

```powershell
cd D:/GitHub/PaperHub-h4/frontend
$env:VITE_API_BASE_URL='http://localhost:8100'; npx vite --port 4173 --strictPort
```

Check: `curl http://127.0.0.1:8100/health` and `curl http://127.0.0.1:8000/health`.

Benchmarks against H4: `scripts/run-benchmark.ps1 -BaseUrl http://127.0.0.1:8100 -DbPath D:/paperhub-h4-workspace/paperhub.db`.

## 6. Start / demo v2.41

Unchanged: `D:/GitHub/PaperHub`, `:8000` / `:5173`.

## 7. Restore v2.41 data from a backup

Stop v2.41 first, then:

```powershell
cd D:/GitHub/PaperHub-h4/backend
uv run paperhub-copy-workspace --src D:/paperhub-backups/v2.41-<date> --dest D:/GitHub/PaperHub/backend/workspace --force
```

## 8. Before every H4 migration run

Back up the H4 workspace the same way:

```powershell
uv run paperhub-copy-workspace --src D:/paperhub-h4-workspace --dest D:/paperhub-backups/h4-<date>-<n>
```
