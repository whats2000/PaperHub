# backend/scripts/backfill_assets.ps1
# Operator-facing wrapper around `paperhub-backfill-assets`. Backfills the F2
# PaperAsset (figures/equations/sections under <cache>/asset/) onto papers
# already in the cache. Marker for PDF sources (incl. arxiv-via-pdf fallbacks),
# LaTeX-source for arxiv/latex_upload.
#
# Filesystem-only + idempotent (skips papers that already have asset/figures.json
# unless --force), so NO DB/chroma backup is taken — unlike reingest_all_papers.ps1.
#
# Usage:
#   .\scripts\backfill_assets.ps1                 # backfill all papers
#   .\scripts\backfill_assets.ps1 --dry-run       # preview only (no writes)
#   .\scripts\backfill_assets.ps1 --force         # rebuild even if asset/ exists
#   .\scripts\backfill_assets.ps1 --paper-content-id 22
$ErrorActionPreference = "Stop"

$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..\workspace")).Path
$backend   = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# CRITICAL: pin PAPERHUB_WORKSPACE to the absolute backend/workspace path so the
# CLI hits the SAME DB + papers_cache the backend writes to, regardless of the
# invoker's cwd (without this, load_settings() defaults to ./workspace relative
# to cwd — from the repo root that's a different, usually empty workspace).
$env:PAPERHUB_WORKSPACE = $workspace

# The PDF path calls the Marker service (default http://127.0.0.1:8002) — make
# sure `docker compose up -d marker` is running, or PDF papers will error
# (per-paper recovery keeps the run going; LaTeX papers are unaffected).
Write-Host "Running paperhub-backfill-assets $args (PAPERHUB_WORKSPACE=$workspace) ..."
# --project anchors uv on the backend's pyproject.toml regardless of cwd.
uv run --project $backend paperhub-backfill-assets @args

if ($LASTEXITCODE -ne 0) {
    Write-Error "paperhub-backfill-assets exited $LASTEXITCODE"
    exit $LASTEXITCODE
}
Write-Host "Done."
