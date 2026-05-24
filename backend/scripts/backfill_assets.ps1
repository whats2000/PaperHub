# backend/scripts/backfill_assets.ps1
# Operator-facing wrapper around `paperhub-backfill-assets`. Backfills the F2
# PaperAsset (figures/equations/sections under <cache>/asset/) onto papers
# already in the cache. Marker for PDF sources (incl. arxiv-via-pdf fallbacks),
# LaTeX-source for arxiv/latex_upload.
#
# Also updates paper_content.asset_status in the DB:
#   written (LaTeX)  → 'latex'
#   written (PDF)    → 'marker_ready'
#   error   (PDF)    → 'marker_failed'
#   skipped / dry-run → unchanged
#
# Idempotent (skips papers that already have asset/figures.json unless --force),
# so NO DB/chroma backup is needed — unlike reingest_all_papers.ps1.
#
# Usage:
#   .\scripts\backfill_assets.ps1                          # backfill all papers
#   .\scripts\backfill_assets.ps1 --dry-run                # preview only (no writes)
#   .\scripts\backfill_assets.ps1 --force                  # rebuild even if asset/ exists
#   .\scripts\backfill_assets.ps1 --paper-content-id 22   # single paper
#   .\scripts\backfill_assets.ps1 --enqueue-only           # mark PDF papers
#       # marker_pending (background worker drains them); build LaTeX synchronously
$ErrorActionPreference = "Stop"

$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..\workspace")).Path
$backend   = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# CRITICAL: pin PAPERHUB_WORKSPACE to the absolute backend/workspace path so the
# CLI hits the SAME DB + papers_cache the backend writes to, regardless of the
# invoker's cwd (without this, load_settings() defaults to ./workspace relative
# to cwd — from the repo root that's a different, usually empty workspace).
$env:PAPERHUB_WORKSPACE = $workspace

# Clear any stale VIRTUAL_ENV so uv doesn't emit a "VIRTUAL_ENV does not match
# the project environment" WARNING to stderr — under this script's
# ErrorActionPreference='Stop', that native-stderr line is otherwise promoted to
# a terminating NativeCommandError and aborts the run before the CLI executes.
$env:VIRTUAL_ENV = $null

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
