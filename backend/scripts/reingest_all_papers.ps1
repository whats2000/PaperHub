# backend/scripts/reingest_all_papers.ps1
# Operator-facing upgrade for papers ALREADY in the library: brings every
# existing paper up to the current pipeline so features added after it was
# ingested work on it too. Backs up workspace/paperhub.db first so a botched
# run can be rolled back by restoring the .bak copy.
#
# Steps (each idempotent, run in order):
#   1. paperhub-reingest                      re-chunk with the current chunker
#                                             (spans unchanged: names updated in
#                                             place, chunk ids kept)
#   2. paperhub-reanchor-marks                Plan H5b.1: re-find every reading mark's
#                                             quote in the new chunks, follow
#                                             renamed sections; lost ones stay flagged
#   3. paperhub-repair-layout                 re-point figure/table index entries
#                                             at the paper's current chunks
#   4. paperhub-repair-section-names          rename non-heading section names
#                                             (raw LaTeX macros, PDF fragments) in
#                                             place + in digests / cards / ideas /
#                                             slide grounding
#   5. paperhub-repair-titles                 re-derive auto-picked PDF titles in
#                                             place (journal wordmarks, drop caps),
#                                             carried to FTS / digest / card hash
#   6. paperhub-backfill-assets               build a missing PaperAsset
#                                             (figures / equations / sections)
#   7. paperhub-backfill-assets --blocks-only Plan H1: Marker paragraph layout
#                                             (asset/blocks.json) for PDF papers,
#                                             needed by PDF bilingual reading
#   8. paperhub-backfill-figure-locations     where each figure sits in the reader
#                                             (LaTeX figure environment, PDF bbox),
#                                             commented-out figures dropped, multi-
#                                             panel figures composed, figures Marker
#                                             split joined; needs step 7's blocks
#   9. paperhub-backfill-page-pdfs            a page PDF for every paper (LaTeX:
#                                             local pdflatex into asset/paper.pdf,
#                                             arXiv once on failure) and LaTeX
#                                             figures placed on it afresh
#                                             (--replace-places; writes only on change)
# 10. paperhub-rerender-html                 re-render Citation Canvas HTML for
#                                             LaTeX papers with figure anchors and
#                                             the paper's figure numbers (chunks
#                                             untouched; skipped on -DryRun)
# 11. paperhub-backfill-figure-locations     --verify: every figure a [fig:]
#      --verify                               citation can open on (skipped on
#                                             -DryRun; fails on any that cannot)
# 12. paperhub-backfill-page-pdfs --verify   every paper has a page PDF (skipped
#                                             on -DryRun; unplaced figures warn)
# 13. paperhub-backfill-cards                Plan H3: a mechanism card per
#                                             document (digest + card LLM calls),
#                                             read by cross-domain ideation
# 14. paperhub-consolidate-domains           Plan H3: fold near-duplicate domain
#                                             tags, at most two per document
# 15. paperhub-narrow-idea-citations        Plan H3: narrow stored ideas'
#                                             citations to the mechanism's chunks
# 16. paperhub-repair-translations          Plan H1: drop cached translations
#                                             that echo the source or whose PDF
#                                             paragraph changed; merge language-
#                                             label aliases into one cache key
# 17. paperhub-migrate-idea-notes            Plan H5b.1: each idea's ideas.note
#                                             becomes an idea note (column kept)
# Steps 6-7 need the Marker service (`docker compose up -d marker`) for PDF
# papers; they are sequential and skip papers that already have the output.
#
# Usage:
#   .\scripts\reingest_all_papers.ps1                # full upgrade
#   .\scripts\reingest_all_papers.ps1 -DryRun        # preview only (no mutations)
#   .\scripts\reingest_all_papers.ps1 -SkipRechunk   # skip step 1 (names still repaired
#                                                    # in place; chunk rows and citations kept)
param(
    [switch]$DryRun,
    [switch]$SkipRechunk
)
$ErrorActionPreference = "Stop"

$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..\workspace")).Path
$backend   = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$db        = Join-Path $workspace "paperhub.db"
$dbBak     = Join-Path $workspace "paperhub.db.bak"

if (-not (Test-Path $db)) { Write-Error "No paperhub.db at $db"; exit 1 }

if (-not $DryRun) {
    Write-Host "Backing up workspace/paperhub.db -> paperhub.db.bak..."
    Copy-Item $db $dbBak -Force
}

# CRITICAL: pin PAPERHUB_WORKSPACE to the absolute backend/workspace path so the
# CLIs hit the SAME DB the backend writes to, regardless of where the operator
# invoked this script from. Without this, `load_settings()` defaults to
# ./workspace relative to cwd and silently reports "0 paper(s)".
$env:PAPERHUB_WORKSPACE = $workspace

# Clear stale VIRTUAL_ENV so uv's mismatch WARNING (stderr) isn't promoted to a
# terminating NativeCommandError under ErrorActionPreference='Stop'.
$env:VIRTUAL_ENV = $null

$dry = @()
if ($DryRun) { $dry = @("--dry-run") }

function Invoke-Step([string]$label, [string[]]$cmd) {
    Write-Host "== $label : $($cmd -join ' ') (PAPERHUB_WORKSPACE=$workspace)"
    # --project anchors uv on the backend's pyproject.toml regardless of cwd.
    # The CLIs log to stderr; under 'Stop' PowerShell 5.1 would turn the first
    # log line into a terminating NativeCommandError, so judge by exit code.
    $ErrorActionPreference = "Continue"
    uv run --project $backend @cmd 2>&1 | ForEach-Object { "$_" }
    $ErrorActionPreference = "Stop"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "$label exited $LASTEXITCODE; the DB before this run is at $dbBak"
        exit $LASTEXITCODE
    }
}

if (-not $SkipRechunk) {
    Invoke-Step "1/17 re-chunk" (@("paperhub-reingest") + $dry)
}
Invoke-Step "2/17 note mark anchors" (@("paperhub-reanchor-marks") + $dry)
Invoke-Step "3/17 figure/table index" (@("paperhub-repair-layout") + $dry)
Invoke-Step "4/17 section names" (@("paperhub-repair-section-names") + $dry)
Invoke-Step "5/17 PDF titles" (@("paperhub-repair-titles") + $dry)
Invoke-Step "6/17 missing assets" (@("paperhub-backfill-assets") + $dry)
Invoke-Step "7/17 PDF paragraph layout" (@("paperhub-backfill-assets", "--blocks-only") + $dry)
Invoke-Step "8/17 figure locations" (@("paperhub-backfill-figure-locations") + $dry)
Invoke-Step "9/17 page PDFs" (@("paperhub-backfill-page-pdfs", "--replace-places") + $dry)
if (-not $DryRun) { Invoke-Step "10/17 Citation Canvas HTML" @("paperhub-rerender-html") }
if (-not $DryRun) { Invoke-Step "11/17 figure citation check" @("paperhub-backfill-figure-locations", "--verify") }
if (-not $DryRun) { Invoke-Step "12/17 page PDF check" @("paperhub-backfill-page-pdfs", "--verify") }
Invoke-Step "13/17 ideation mechanism cards" (@("paperhub-backfill-cards") + $dry)
Invoke-Step "14/17 ideation domain consolidation" (@("paperhub-consolidate-domains") + $dry)
Invoke-Step "15/17 ideation citation narrowing" (@("paperhub-narrow-idea-citations") + $dry)
Invoke-Step "16/17 translation cache repair" (@("paperhub-repair-translations") + $dry)
Invoke-Step "17/17 idea notes into the notes store" (@("paperhub-migrate-idea-notes") + $dry)

if ($DryRun) { Write-Host "Done (dry run, nothing changed)." }
else { Write-Host "Done. DB backup at $dbBak (remove it when you've verified the result)." }
