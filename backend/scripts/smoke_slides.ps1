# End-to-end smoke for the Slide Pipeline (Plan F, SRS FR-12).
#
# Asserts ONE of two acceptable outcomes depending on whether pdflatex
# is present on PATH:
#
#   A. pdflatex ABSENT (graceful guard path — no API key required):
#      The report graph routes to sl_no_latex, which returns the graceful
#      guard message containing "LaTeX"/"pdflatex". The final SSE event
#      must contain that guard text.
#      PRINT: PASS (no pdflatex — graceful guard)
#
#   B. pdflatex PRESENT (compiled deck path — requires a real LLM API key):
#      The report graph runs plan_deck → generate_section → generate_notes
#      → compile_with_revise. A `deck` SSE event appears with a numeric
#      page_count, and GET /sessions/{id}/deck/pdf returns 200 with a body
#      starting `%PDF`.
#      PRINT: PASS (compiled deck)
#
# Mock mechanism: PAPERHUB_ROUTER_MOCK forces intent=slides so the router
# LLM is never called. The Report pipeline's plan/section/notes calls are
# real LLM calls — there are no slides-specific mock env vars in chat.py.
# Consequence:
#   - Path A (no pdflatex) never reaches the LLM calls — always mockable.
#   - Path B (pdflatex present) requires a real API key in backend/.env.
# The smoke always exercises Path A on hosts without pdflatex (the normal
# CI / dev situation).  Path B is an operator-facing gate for TeX-equipped
# hosts.
#
# Data seeding: a single paper_content + papers row (enabled=1, kind='arxiv')
# is inserted directly via SQLite so the sl_resolve node finds at least one
# paper, ensuring the _empty branch does NOT fire.
#
# Usage: cd backend; .\scripts\smoke_slides.ps1
$ErrorActionPreference = "Stop"

$backendDir = Resolve-Path (Join-Path $PSScriptRoot "..")
$port = 8771

# ---------------------------------------------------------------------------
# Detect pdflatex upfront — determines which assertion branch we take.
# ---------------------------------------------------------------------------
$hasPdflatex = $null -ne (Get-Command pdflatex -ErrorAction SilentlyContinue)
if ($hasPdflatex) {
    Write-Host "pdflatex detected on PATH — will assert compiled deck path (requires LLM API key)."
} else {
    Write-Host "pdflatex NOT on PATH — will assert graceful guard path (no API key needed)."
}

# ---------------------------------------------------------------------------
# Helper: issue one /chat POST, return raw SSE lines.
# ---------------------------------------------------------------------------
function Invoke-ChatTurn {
    param(
        [string]$BaseUrl,
        [string]$Body,
        [string]$TmpFile
    )
    [System.IO.File]::WriteAllText($TmpFile, $Body)
    $sse = & curl.exe -N -s -X POST "$BaseUrl/chat" `
        -H "Content-Type: application/json" `
        --data-binary "@$TmpFile"
    if ($LASTEXITCODE -ne 0) { throw "curl.exe failed posting to $BaseUrl/chat" }
    return $sse
}

# ---------------------------------------------------------------------------
# Helper: parse SSE output.
# Returns a hashtable { FinalContent, GotError, GotDeck, DeckPageCount }.
# Accepts either a string or an Object[] (what curl.exe returns in PS).
# ---------------------------------------------------------------------------
function Parse-Sse {
    param($Raw)
    $result = @{ FinalContent = $null; GotError = $false; GotDeck = $false; DeckPageCount = 0 }
    $cur = $null
    if ($Raw -is [array]) {
        $lines = $Raw | ForEach-Object { "$_".TrimEnd("`r") }
    } else {
        $lines = "$Raw" -split "`n" | ForEach-Object { $_.TrimEnd("`r") }
    }
    foreach ($line in $lines) {
        if ($line.StartsWith("event:")) { $cur = $line.Substring(6).Trim() }
        elseif ($line.StartsWith("data:")) {
            $j = $line.Substring(5).Trim()
            if ($cur -eq "final") {
                try { $result.FinalContent = ($j | ConvertFrom-Json).content } catch { }
            } elseif ($cur -eq "error") {
                $result.GotError = $true
                Write-Host "  ERROR event data: $j" -ForegroundColor Red
            } elseif ($cur -eq "deck") {
                try {
                    $d = $j | ConvertFrom-Json
                    $result.GotDeck = $true
                    $result.DeckPageCount = [int]$d.page_count
                } catch { }
            }
        }
        elseif ($line -eq "") { $cur = $null }
    }
    return $result
}

# ---------------------------------------------------------------------------
# Helper: boot uvicorn and wait for /health (up to 30 s).
# Requires all relevant env vars to be set BEFORE calling this.
# Returns the Process object.
# ---------------------------------------------------------------------------
function Start-Backend {
    param([string]$Dir, [int]$Port)

    $inUse = $false
    try {
        $tcp = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        $tcp.Start(); $tcp.Stop()
    } catch { $inUse = $true }
    if ($inUse) { throw "Port $Port already in use. Kill the orphan uvicorn process and retry." }

    $proc = Start-Process -PassThru -NoNewWindow -WorkingDirectory $Dir `
        uv -ArgumentList @("run", "uvicorn", "paperhub.app:app", "--host", "127.0.0.1", "--port", "$Port")

    $healthy = $false
    for ($i = 0; $i -lt 150; $i++) {
        try { Invoke-RestMethod "http://127.0.0.1:$Port/health" -ErrorAction Stop | Out-Null; $healthy = $true; break }
        catch { Start-Sleep -Milliseconds 200 }
    }
    if (-not $healthy) { throw "Backend on :$Port did not become healthy within 30 s." }
    Write-Host "  Server up on :$Port."
    return $proc
}

# ---------------------------------------------------------------------------
# Helper: kill a uvicorn process tree.
# ---------------------------------------------------------------------------
function Stop-Backend {
    param($Proc)
    & taskkill.exe /F /T /PID $Proc.Id 2>&1 | Out-Null
}

# ---------------------------------------------------------------------------
# Shared env vars.
# ---------------------------------------------------------------------------
$env:PAPERHUB_INPROCESS_MODELS = "1"
$env:PAPERHUB_BOOT_BANNER = "0"
# Router mock: force intent=slides so no router LLM call is made.
$env:PAPERHUB_ROUTER_MOCK = '{"intent":"slides","model_tier":"small","confidence":0.99,"reasoning":"smoke","resolved_query":"make slides from my papers","response_language":"English"}'
# Clear any mock vars from other smokes that would interfere.
Remove-Item Env:PAPERHUB_CHITCHAT_MOCK    -ErrorAction SilentlyContinue
Remove-Item Env:PAPERHUB_SQL_PLANNER_MOCK -ErrorAction SilentlyContinue
Remove-Item Env:PAPERHUB_SQL_ANSWER_MOCK  -ErrorAction SilentlyContinue
Remove-Item Env:PAPERHUB_MEMORY_OP_MOCK   -ErrorAction SilentlyContinue

# =========================================================================
# Main smoke
# =========================================================================
Write-Host ""
Write-Host "=== smoke_slides: slide pipeline end-to-end ==="

$ws = Join-Path $backendDir "workspace_smoke_slides"
if (Test-Path $ws) { Remove-Item -Recurse -Force $ws }
$env:PAPERHUB_WORKSPACE = $ws

$srv = $null
$sessionId = $null
try {
    $srv = Start-Backend -Dir $backendDir -Port $port

    # ── Seed: create a chat session + one enabled arXiv paper ────────────────
    # We POST /chat first to let the backend auto-create a session (session_id
    # is returned in the `session` SSE event), then insert the paper rows
    # directly into the workspace DB (same approach used by smoke_mcp_papers.ps1).
    # The `papers` row must be enabled=1 so _enabled_papers() returns it and the
    # sl_resolve node doesn't fire the _empty branch.
    $dbPath = Join-Path $ws "paperhub.db"

    # Wait for the DB file to be created by the backend's first migration.
    for ($i = 0; $i -lt 50; $i++) {
        if (Test-Path $dbPath) { break }
        Start-Sleep -Milliseconds 200
    }
    if (-not (Test-Path $dbPath)) {
        throw "DB file not found at $dbPath after 10 s — backend may not have started correctly."
    }

    # Write the seeding script to a temp file (avoids PowerShell here-string
    # escaping issues with PRAGMA and multi-line Python strings — same approach
    # used by smoke_memory.ps1).
    $seedScript = Join-Path $env:TEMP "smoke_slides_seed.py"
    [System.IO.File]::WriteAllText($seedScript, @'
import sqlite3, sys
db = sys.argv[1]
conn = sqlite3.connect(db)
# paper_content has NOT NULL cols: source_path, source_dir_path, html_path.
# It also has CHECK (arxiv_id IS NOT NULL) <> (sha256 IS NOT NULL) so we must
# supply arxiv_id and no sha256, or sha256 and no arxiv_id.
# FK enforcement must be ON for the papers -> paper_content FK insert.
conn.execute("PRAGMA foreign_keys = ON")
conn.execute("INSERT OR IGNORE INTO chat_sessions (id, title) VALUES (1, 'smoke-slides')")
conn.execute(
    "INSERT OR IGNORE INTO paper_content "
    "(id, content_key, kind, arxiv_id, title, abstract, year, "
    " source_path, source_dir_path, html_path) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    (
        2001,
        "smoke-slides-key-1",
        "arxiv",
        "2301.00001",
        "Attention Is All You Need (smoke-slides seed)",
        "We propose a new network architecture, the Transformer, based solely "
        "on attention mechanisms, dispensing with recurrence and convolutions.",
        2017,
        "/tmp/seed.pdf",
        "/tmp",
        "/tmp/seed.html",
    ),
)
conn.execute(
    "INSERT OR IGNORE INTO papers "
    "(id, session_id, paper_content_id, enabled, added_at) "
    "VALUES (?, ?, ?, 1, datetime('now'))",
    (3001, 1, 2001),
)
conn.commit()
conn.close()
print("DB seeded OK")
'@)

    # Create session 1 + seed paper rows so the slides intent finds papers.
    & uv run --project $backendDir python $seedScript $dbPath
    if ($LASTEXITCODE -ne 0) { throw "Failed to seed DB rows." }

    # ── Chat turn: slides intent ─────────────────────────────────────────────
    # We supply session_id=1 explicitly so the backend uses the seeded session
    # (and therefore finds the seeded paper in _enabled_papers).
    Write-Host ""
    Write-Host "  Posting slides chat turn (session_id=1)..."
    $body = '{"session_id":1,"user_message":"make slides from my papers"}'
    $tmpBody = Join-Path $env:TEMP "smoke_slides_body.json"
    $sessionId = 1

    $sse = Invoke-ChatTurn -BaseUrl "http://127.0.0.1:$port" -Body $body -TmpFile $tmpBody
    Write-Host ""
    Write-Host "  --- Raw SSE (first 20 lines) ---"
    if ($sse -is [array]) { $sse | Select-Object -First 20 | ForEach-Object { Write-Host "  $_" } }
    else { ($sse -split "`n" | Select-Object -First 20) | ForEach-Object { Write-Host "  $_" } }

    $parsed = Parse-Sse -Raw $sse

    if ($parsed.GotError) {
        throw "FAIL: backend returned an error SSE event. See ERROR event data above.`nFull SSE:`n$sse"
    }
    if ($null -eq $parsed.FinalContent) {
        throw "FAIL: no final SSE event received.`nFull SSE:`n$sse"
    }

    Write-Host ""
    Write-Host "  final content: $($parsed.FinalContent)"
    Write-Host "  got deck event: $($parsed.GotDeck)  page_count: $($parsed.DeckPageCount)"

    # ── Assert correct outcome branch ────────────────────────────────────────
    if ($hasPdflatex) {
        # Path B: pdflatex present — expect a deck SSE event + PDF endpoint.
        if (-not $parsed.GotDeck) {
            throw "FAIL (compiled deck): expected a 'deck' SSE event but none arrived.`nFull SSE:`n$sse"
        }
        if ($parsed.DeckPageCount -lt 1) {
            throw "FAIL (compiled deck): deck event page_count=$($parsed.DeckPageCount) — expected >= 1."
        }

        # Verify GET /sessions/{id}/deck/pdf returns 200 + a PDF body.
        Write-Host "  Fetching /sessions/$sessionId/deck/pdf ..."
        $pdfPath = Join-Path $env:TEMP "smoke_slides_deck.pdf"
        $pdfResp = & curl.exe -s -o $pdfPath -w "%{http_code}" `
            "http://127.0.0.1:$port/sessions/$sessionId/deck/pdf"
        $pdfStatus = ($pdfResp | Out-String).Trim()
        if ($pdfStatus -ne "200") {
            throw "FAIL (compiled deck): GET /sessions/$sessionId/deck/pdf returned HTTP $pdfStatus (expected 200)."
        }
        $pdfHeader = [System.IO.File]::ReadAllBytes($pdfPath) | Select-Object -First 4
        $pdfMagic = [System.Text.Encoding]::ASCII.GetString($pdfHeader)
        if ($pdfMagic -ne "%PDF") {
            throw "FAIL (compiled deck): PDF body does not start with %PDF (got '$pdfMagic')."
        }
        if (Test-Path $pdfPath) { Remove-Item $pdfPath -Force -ErrorAction SilentlyContinue }

        Write-Host ""
        Write-Host "PASS (compiled deck) — deck SSE page_count=$($parsed.DeckPageCount), PDF 200 + %PDF magic." -ForegroundColor Green

    } else {
        # Path A: no pdflatex — expect the graceful guard message.
        # The guard text is: "Slide generation needs a LaTeX distribution (TeX Live
        # or MikTeX) with pdflatex on PATH. Install one and try again."
        $guardMatch = ($parsed.FinalContent -match "(?i)latex") -or
                      ($parsed.FinalContent -match "(?i)pdflatex")
        if (-not $guardMatch) {
            throw "FAIL (no pdflatex — graceful guard): final content does not mention 'LaTeX' or 'pdflatex'.`nActual: $($parsed.FinalContent)"
        }
        # Deck event must NOT appear (the no_latex branch never reaches _generate).
        if ($parsed.GotDeck) {
            throw "FAIL (no pdflatex — graceful guard): unexpected 'deck' SSE event appeared (pdflatex not on PATH)."
        }

        Write-Host ""
        Write-Host "PASS (no pdflatex — graceful guard)" -ForegroundColor Green
    }

    # Informational replay.
    Write-Host ""
    Write-Host "--- Replay (run 1) ---"
    & uv run --project $backendDir paperhub-replay --run-id 1
    if ($LASTEXITCODE -ne 0) { Write-Host "  (paperhub-replay non-zero -- informational)" }

} finally {
    if ($null -ne $srv) { Stop-Backend -Proc $srv }
    if (Test-Path $ws) { Remove-Item -Recurse -Force $ws -ErrorAction SilentlyContinue }
    foreach ($f in @(
        (Join-Path $env:TEMP "smoke_slides_body.json"),
        (Join-Path $env:TEMP "smoke_slides_seed.py")
    )) {
        if (Test-Path $f) { Remove-Item $f -Force -ErrorAction SilentlyContinue }
    }
}

Write-Host ""
Write-Host "smoke_slides: OK" -ForegroundColor Green
