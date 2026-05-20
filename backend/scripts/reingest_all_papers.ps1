# backend/scripts/reingest_all_papers.ps1
# Operator-facing wrapper around `paperhub-reingest`. Backs up
# workspace/paperhub.db and workspace/chroma BEFORE running so a
# botched run can be rolled back by restoring the .bak.v2.10 copies.
#
# Usage:
#   .\scripts\reingest_all_papers.ps1             # re-ingest all papers
#   .\scripts\reingest_all_papers.ps1 --dry-run   # preview only (no mutations)
$ErrorActionPreference = "Stop"

$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..\workspace")).Path
$backend   = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$db        = Join-Path $workspace "paperhub.db"
$chroma    = Join-Path $workspace "chroma"
$dbBak     = Join-Path $workspace "paperhub.db.bak.v2.10"
$chromaBak = Join-Path $workspace "chroma.bak.v2.10"

if (-not (Test-Path $db))     { Write-Error "No paperhub.db at $db";  exit 1 }
if (-not (Test-Path $chroma)) { Write-Error "No chroma dir at $chroma"; exit 1 }

Write-Host "Backing up workspace/paperhub.db -> paperhub.db.bak.v2.10..."
Copy-Item $db $dbBak -Force

Write-Host "Backing up workspace/chroma -> chroma.bak.v2.10..."
if (Test-Path $chromaBak) { Remove-Item -Recurse -Force $chromaBak }
Copy-Item $chroma $chromaBak -Recurse

# CRITICAL: pin PAPERHUB_WORKSPACE to the absolute backend/workspace path so
# `paperhub-reingest` hits the SAME DB the backend writes to, regardless of
# where the operator invoked this script from. Without this, `load_settings()`
# defaults to ./workspace relative to cwd — if you ran from the repo root,
# that resolves to <repo>/workspace/paperhub.db (a different, usually empty DB)
# and the CLI silently reports "0 paper(s)" while the live DB is untouched.
$env:PAPERHUB_WORKSPACE = $workspace

Write-Host "Running paperhub-reingest $args (PAPERHUB_WORKSPACE=$workspace) ..."
# Use --project to anchor uv on the backend's pyproject.toml regardless of
# the invoker's cwd. Without this, uv may pick a different (or root) project
# context and the `paperhub-reingest` entry-point fails to resolve.
uv run --project $backend paperhub-reingest @args

if ($LASTEXITCODE -ne 0) {
    Write-Error "paperhub-reingest exited $LASTEXITCODE; old data preserved at $dbBak and $chromaBak"
    exit $LASTEXITCODE
}
Write-Host "Done. Backups at $dbBak and $chromaBak (remove when you've verified the result)."
