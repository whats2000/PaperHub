# backend/scripts/reingest_all_papers.ps1
# Operator-facing wrapper around `paperhub-reingest`. Backs up
# workspace/paperhub.db and workspace/chroma BEFORE running so a
# botched run can be rolled back by restoring the .bak.v2.10 copies.
#
# Usage:
#   .\scripts\reingest_all_papers.ps1             # re-ingest all papers
#   .\scripts\reingest_all_papers.ps1 --dry-run   # preview only (no mutations)
$ErrorActionPreference = "Stop"

$workspace = Join-Path $PSScriptRoot "..\workspace"
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

Write-Host "Running paperhub-reingest $args..."
uv run paperhub-reingest @args

if ($LASTEXITCODE -ne 0) {
    Write-Error "paperhub-reingest exited $LASTEXITCODE; old data preserved at $dbBak and $chromaBak"
    exit $LASTEXITCODE
}
Write-Host "Done. Backups at $dbBak and $chromaBak (remove when you've verified the result)."
