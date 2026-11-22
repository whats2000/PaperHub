# backend/scripts/reingest_all_papers.ps1
# Operator-facing upgrade for papers ALREADY in the library: brings every
# existing paper up to the current pipeline so features added after it was
# ingested work on it too. Backs up workspace/paperhub.db first so a botched
# run can be rolled back by restoring the .bak copy.
#
# Stop the backend first (it holds the DB), run this, then start it again.
#
# Steps (each idempotent, run in order):
#   0. paperhub-apply-schema                 bring the schema up to date (the
#                                             routine the backend runs at start):
#                                             later steps reach tables added since
#                                             (the H5b notes layer); -DryRun names
#                                             the missing tables only
#   1. paperhub-migrate-mark-styles        Plan H5b.1: reading marks as kind + style
#                                             (highlight -> text/highlight, quote ->
#                                             text/underline no colour), palette hex
#   2. paperhub-reingest                      re-chunk with the current chunker
#                                             (spans unchanged: names updated in
#                                             place, chunk ids kept)
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
# 11. paperhub-reanchor-marks                Plan H5b.1: re-find every reading mark's
#                                             quote in the rendered HTML (after
#                                             step 10), follow renamed sections;
#                                             lost ones stay flagged
# 12. paperhub-backfill-figure-locations     --verify: every figure a [fig:]
#      --verify                               citation can open on (skipped on
#                                             -DryRun; fails on any that cannot)
# 13. paperhub-backfill-page-pdfs --verify   every paper has a page PDF (skipped
#                                             on -DryRun; unplaced figures warn)
# 14. paperhub-backfill-cards                Plan H3: a mechanism card per
#                                             document (digest + card LLM calls),
#                                             read by cross-domain ideation
# 15. paperhub-consolidate-domains           Plan H3: fold near-duplicate domain
#                                             tags, at most two per document
# 16. paperhub-narrow-idea-citations        Plan H3: narrow stored ideas'
#                                             citations to the mechanism's chunks
# 17. paperhub-repair-translations          Plan H1: drop cached translations
#                                             that echo the source or whose PDF
#                                             paragraph changed; merge language-
#                                             label aliases into one cache key
# 18. paperhub-migrate-idea-notes            Plan H5b.1: each idea's ideas.note
#                                             becomes an idea note (column kept)
# 19. paperhub-clear-auto-note-titles        Plan H5b.1: a note title auto-filled
#                                             from its paper / chat / idea is
#                                             cleared (the default shows instead)
# 20. paperhub-clean-empty-chats             Plan H5b.1: chats created just by
#                                             showing them (no messages, papers,
#                                             note text or links, still "New chat")
#                                             are soft-deleted; -DryRun lists them
# 21. paperhub-reconcile-mark-blocks        Plan H5b.1: a mark block whose colour /
#                                             style disagree with its mark row (an
#                                             agent recolour before Task 16) is
#                                             redrawn from the row, a system change
#                                             per note; nothing to redraw, no write
# 22. paperhub-backfill-table-borders   Plan H5b.1: every table without borders gets the Paper preset's lines (what it showed by default)
# Steps 6-7 need the Marker service (`docker compose up -d marker`) for PDF
# papers; they are sequential and skip papers that already have the output.
#
# Usage:
#   .\scripts\reingest_all_papers.ps1                # full upgrade
#   .\scripts\reingest_all_papers.ps1 -DryRun        # preview only (no mutations)
#   .\scripts\reingest_all_papers.ps1 -SkipRechunk   # skip step 2 (names still repaired
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

Invoke-Step "0/22 schema" (@("paperhub-apply-schema") + $dry)
Invoke-Step "1/22 mark styles" (@("paperhub-migrate-mark-styles") + $dry)
if (-not $SkipRechunk) {
    Invoke-Step "2/22 re-chunk" (@("paperhub-reingest") + $dry)
}
Invoke-Step "3/22 figure/table index" (@("paperhub-repair-layout") + $dry)
Invoke-Step "4/22 section names" (@("paperhub-repair-section-names") + $dry)
Invoke-Step "5/22 PDF titles" (@("paperhub-repair-titles") + $dry)
Invoke-Step "6/22 missing assets" (@("paperhub-backfill-assets") + $dry)
Invoke-Step "7/22 PDF paragraph layout" (@("paperhub-backfill-assets", "--blocks-only") + $dry)
Invoke-Step "8/22 figure locations" (@("paperhub-backfill-figure-locations") + $dry)
Invoke-Step "9/22 page PDFs" (@("paperhub-backfill-page-pdfs", "--replace-places") + $dry)
if (-not $DryRun) { Invoke-Step "10/22 Citation Canvas HTML" @("paperhub-rerender-html") }
Invoke-Step "11/22 note mark anchors" (@("paperhub-reanchor-marks") + $dry)
if (-not $DryRun) { Invoke-Step "12/22 figure citation check" @("paperhub-backfill-figure-locations", "--verify") }
if (-not $DryRun) { Invoke-Step "13/22 page PDF check" @("paperhub-backfill-page-pdfs", "--verify") }
Invoke-Step "14/22 ideation mechanism cards" (@("paperhub-backfill-cards") + $dry)
Invoke-Step "15/22 ideation domain consolidation" (@("paperhub-consolidate-domains") + $dry)
Invoke-Step "16/22 ideation citation narrowing" (@("paperhub-narrow-idea-citations") + $dry)
Invoke-Step "17/22 translation cache repair" (@("paperhub-repair-translations") + $dry)
Invoke-Step "18/22 idea notes into the notes store" (@("paperhub-migrate-idea-notes") + $dry)
Invoke-Step "19/22 auto-filled note titles" (@("paperhub-clear-auto-note-titles") + $dry)
Invoke-Step "20/22 empty chats" (@("paperhub-clean-empty-chats") + $dry)
Invoke-Step "21/22 mark blocks drawn from their marks" (@("paperhub-reconcile-mark-blocks") + $dry)
Invoke-Step "22/22 table borders" (@("paperhub-backfill-table-borders") + $dry)

if ($DryRun) { Write-Host "Done (dry run, nothing changed)." }
else { Write-Host "Done. DB backup at $dbBak (remove it when you've verified the result)." }
