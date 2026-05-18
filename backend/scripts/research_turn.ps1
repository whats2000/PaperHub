# Smoke test: three sub-tests for the paper_search read-only shortlist loop (v2.4-5).
#   Sub-test 1: vague prompt → clarifying question, zero external_search calls.
#   Sub-test 2: library hit → search_library before any external search, finalize=true
#               library candidate auto-attached.
#   Sub-test 3: clear RAG prompt → search_semantic_scholar called, search_results SSE event
#               with <= 2 auto_added candidates, suggested-only candidates have no papers row.
# Requires a real LLM key (GEMINI_API_KEY or equivalent) in backend/.env.
# Usage: .\scripts\research_turn.ps1
$ErrorActionPreference = "Stop"

# ── 1. Load backend/.env ──────────────────────────────────────────────────────
$backendDir = Resolve-Path (Join-Path $PSScriptRoot "..")
$envFile = Join-Path $backendDir ".env"
if (-not (Test-Path $envFile)) {
    throw "Missing $envFile. Copy backend/.env.example to backend/.env and fill in your API key."
}
Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { return }
    $idx = $line.IndexOf("=")
    if ($idx -lt 1) { return }
    $key = $line.Substring(0, $idx).Trim()
    $val = $line.Substring($idx + 1).Trim()
    if (($val.StartsWith('"') -and $val.EndsWith('"')) -or
        ($val.StartsWith("'") -and $val.EndsWith("'"))) {
        $val = $val.Substring(1, $val.Length - 2)
    }
    if ($val -ne "") { Set-Item -Path "env:$key" -Value $val }
}

# ── Real-flow guard ───────────────────────────────────────────────────────────
$paperQaModel = if ($env:PAPERHUB_PAPER_QA_MODEL) { $env:PAPERHUB_PAPER_QA_MODEL } else { "gemini/gemini-2.5-pro" }
$needsKey = @{
    "gemini"    = "GEMINI_API_KEY"
    "openai"    = "OPENAI_API_KEY"
    "anthropic" = "ANTHROPIC_API_KEY"
}
$provider = ($paperQaModel -split "/", 2)[0]
$keyName  = $needsKey[$provider]
if ($keyName -and -not (Get-Item -Path "env:$keyName" -ErrorAction SilentlyContinue).Value) {
    throw "Model '$paperQaModel' requires env var '$keyName'. Set it in backend/.env."
}

# ── 2. Isolated workspace ─────────────────────────────────────────────────────
$env:PAPERHUB_WORKSPACE = Join-Path $backendDir "workspace\smoke-research"
if (Test-Path $env:PAPERHUB_WORKSPACE) {
    Remove-Item -Recurse -Force $env:PAPERHUB_WORKSPACE
}

Remove-Item Env:PAPERHUB_ROUTER_MOCK   -ErrorAction SilentlyContinue
Remove-Item Env:PAPERHUB_CHITCHAT_MOCK -ErrorAction SilentlyContinue

# ── 3. Pre-flight: port 8769 must be free ─────────────────────────────────────
$portInUse = $false
try {
    $tcp = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 8769)
    $tcp.Start(); $tcp.Stop()
} catch { $portInUse = $true }
if ($portInUse) {
    throw "Port 8769 already in use. Kill the orphan process and retry."
}

# ── 4. Boot uvicorn ───────────────────────────────────────────────────────────
$server = Start-Process -PassThru -NoNewWindow -WorkingDirectory $backendDir `
    uv -ArgumentList @("run", "uvicorn", "paperhub.app:app", "--host", "127.0.0.1", "--port", "8769")

# ── Helper: send a /chat request and return the final content string ──────────
function Invoke-Chat {
    param([int]$SessionId, [string]$UserMessage)
    $body = @{ session_id = $SessionId; user_message = $UserMessage } | ConvertTo-Json -Compress
    $tmpBody = Join-Path $env:TEMP "smoke_research_body_$SessionId.json"
    [System.IO.File]::WriteAllText($tmpBody, $body)
    $sseRaw = & curl.exe -N -s -X POST http://127.0.0.1:8769/chat `
        -H "Content-Type: application/json" `
        --data-binary "@$tmpBody"
    if ($LASTEXITCODE -ne 0) { throw "curl.exe failed (session $SessionId)." }

    # Parse SSE — find the 'final' event data and search_results candidates.
    $finalContent = $null
    $searchCandidates = @()
    $currentEvent = $null
    foreach ($line in ($sseRaw -split "`n")) {
        $line = $line.TrimEnd("`r")
        if ($line.StartsWith("event:")) {
            $currentEvent = $line.Substring(6).Trim()
        } elseif ($line.StartsWith("data:")) {
            $dataJson = $line.Substring(5).Trim()
            if ($currentEvent -eq "final") {
                try {
                    $obj = $dataJson | ConvertFrom-Json
                    $finalContent = $obj.content
                } catch { }
            } elseif ($currentEvent -eq "search_results") {
                try {
                    $obj = $dataJson | ConvertFrom-Json
                    $searchCandidates = $obj.candidates
                } catch { }
            }
        } elseif ($line -eq "") {
            $currentEvent = $null
        }
    }
    if ($null -eq $finalContent) {
        throw "No 'final' SSE event found for session $SessionId. Raw:`n$sseRaw"
    }
    return @{ Final = $finalContent; Candidates = $searchCandidates }
}

# ── Helper: run a Python SQLite query, return trimmed lines ──────────────────
function Invoke-SqliteQuery {
    param([string]$DbPath, [string]$Query)
    $result = & uv run --project $backendDir python -c @'
import sqlite3, sys
db, q = sys.argv[1], sys.argv[2]
conn = sqlite3.connect(db)
for row in conn.execute(q).fetchall():
    print("|".join(str(c) for c in row))
conn.close()
'@ $DbPath $Query
    if ($LASTEXITCODE -ne 0) { throw "SQLite query failed: $Query" }
    return @($result | Where-Object { $_ -ne "" })
}

try {
    # ── 5. Wait for /health (30 s) ────────────────────────────────────────────
    $healthy = $false
    for ($i = 0; $i -lt 150; $i++) {
        try {
            Invoke-RestMethod http://127.0.0.1:8769/health -ErrorAction Stop | Out-Null
            $healthy = $true
            break
        } catch {
            Start-Sleep -Milliseconds 200
        }
    }
    if (-not $healthy) { throw "Server did not become healthy within 30 s." }
    Write-Host "Server up on :8769."

    # ── 6. Pre-create sessions 4, 5, 6, 10 ───────────────────────────────────
    $dbPath = Join-Path $env:PAPERHUB_WORKSPACE "paperhub.db"
    for ($i = 0; $i -lt 20; $i++) {
        if (Test-Path $dbPath) { break }
        Start-Sleep -Milliseconds 200
    }
    & uv run --project $backendDir python -c @'
import sqlite3, sys
db = sys.argv[1]
conn = sqlite3.connect(db)
conn.execute("PRAGMA foreign_keys = ON")
for sid, title in [(4,"smoke-r1"),(5,"smoke-r2"),(6,"smoke-r3"),(10,"smoke-lib")]:
    conn.execute("INSERT OR IGNORE INTO chat_sessions (id, title) VALUES (?, ?)", (sid, title))
conn.commit()
conn.close()
'@ $dbPath
    if ($LASTEXITCODE -ne 0) { throw "Failed to pre-create chat_sessions rows." }

    # ═══════════════════════════════════════════════════════════════════════════
    # Sub-test 1: vague prompt → clarifying question, zero external_search calls.
    # ═══════════════════════════════════════════════════════════════════════════
    Write-Host "`n=== Sub-test 1: vague prompt → clarifying question ==="
    $resp1 = Invoke-Chat -SessionId 4 -UserMessage "find me good ML papers"
    $final1 = $resp1.Final
    Write-Host "Response: $($final1.Substring(0, [Math]::Min(300, $final1.Length)))"

    # Must contain a '?' — heuristic for clarifying question.
    if ($final1 -notmatch '\?') {
        throw "ASSERTION: clarifying question expected (response should contain '?'). Got:`n$final1"
    }

    # Get run_id for session 4 (most recent run).
    $runRows1 = Invoke-SqliteQuery -DbPath $dbPath -Query "SELECT id FROM runs WHERE session_id = 4 ORDER BY id DESC LIMIT 1"
    if ($runRows1.Count -eq 0) { throw "No run found for session 4." }
    $runId1 = $runRows1[0].Trim()

    # Must have at least one paper_search:plan tool_call row.
    $planRows1 = Invoke-SqliteQuery -DbPath $dbPath -Query "SELECT COUNT(*) FROM tool_calls WHERE run_id = $runId1 AND tool = 'paper_search:plan'"
    $planCount1 = [int]($planRows1[0].Trim())
    if ($planCount1 -lt 1) {
        throw "ASSERTION: expected >= 1 tool_calls row with tool='paper_search:plan', got $planCount1"
    }

    # Must have ZERO external_search calls (semantic_scholar or arxiv).
    $extRows1 = Invoke-SqliteQuery -DbPath $dbPath -Query "SELECT COUNT(*) FROM tool_calls WHERE run_id = $runId1 AND (tool LIKE 'paper_search:search_semantic_scholar%' OR tool LIKE 'paper_search:search_arxiv%')"
    $extCount1 = [int]($extRows1[0].Trim())
    if ($extCount1 -ne 0) {
        throw "ASSERTION: expected 0 external search calls for vague prompt, got $extCount1"
    }
    Write-Host "Sub-test 1 PASS — clarifying question returned, plan_calls=$planCount1, external_search_calls=0"

    # ═══════════════════════════════════════════════════════════════════════════
    # Sub-test 2: library hit → library-first preference, finalize auto-attach.
    # ═══════════════════════════════════════════════════════════════════════════
    Write-Host "`n=== Sub-test 2: library hit → library-first preference ==="
    # Pre-ingest 1706.03762 into session 10 (the deduplicated library).
    Write-Host "Pre-ingesting 1706.03762 into session 10 (library pre-seed)..."
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8769/papers" `
        -ContentType "application/json" `
        -Body (@{ session_id = 10; arxiv_id = "1706.03762" } | ConvertTo-Json -Compress) | Out-Null
    Write-Host "Library pre-seed done."

    $resp2 = Invoke-Chat -SessionId 5 -UserMessage "I want the original transformer paper"
    $final2 = $resp2.Final
    Write-Host "Response: $($final2.Substring(0, [Math]::Min(300, $final2.Length)))"

    # Response must mention the transformer paper.
    if ($final2 -notmatch '(?i)(transformer|attention is all you need|vaswani)') {
        throw "ASSERTION: response should name the transformer paper. Got:`n$final2"
    }

    # Get run_id for session 5.
    $runRows2 = Invoke-SqliteQuery -DbPath $dbPath -Query "SELECT id FROM runs WHERE session_id = 5 ORDER BY id DESC LIMIT 1"
    if ($runRows2.Count -eq 0) { throw "No run found for session 5." }
    $runId2 = $runRows2[0].Trim()

    # search_library must appear before any external search (by step_index).
    $libStep = Invoke-SqliteQuery -DbPath $dbPath -Query "SELECT MIN(step_index) FROM tool_calls WHERE run_id = $runId2 AND tool = 'paper_search:search_library'"
    $extStep = Invoke-SqliteQuery -DbPath $dbPath -Query "SELECT MIN(step_index) FROM tool_calls WHERE run_id = $runId2 AND tool LIKE 'paper_search:search_semantic_scholar%'"
    $libStepVal = if ($libStep[0].Trim() -eq "" -or $libStep[0].Trim() -eq "None") { $null } else { [int]$libStep[0].Trim() }
    $extStepVal = if ($extStep[0].Trim() -eq "" -or $extStep[0].Trim() -eq "None") { $null } else { [int]$extStep[0].Trim() }

    if ($null -eq $libStepVal) {
        throw "ASSERTION: expected a paper_search:search_library tool_calls row for session 5."
    }
    if ($null -ne $extStepVal -and $libStepVal -ge $extStepVal) {
        throw "ASSERTION: search_library (step $libStepVal) must appear BEFORE search_semantic_scholar (step $extStepVal)."
    }

    # search_results event must surface a library: candidate.
    $candCount2 = ($resp2.Candidates | Measure-Object).Count
    if ($candCount2 -lt 1) {
        throw "ASSERTION: expected >= 1 search_results candidate for session 5, got $candCount2"
    }
    $autoAdded2 = @($resp2.Candidates | Where-Object { $_.auto_added -eq $true })
    if (($autoAdded2 | Measure-Object).Count -lt 1) {
        throw "ASSERTION: expected >= 1 auto_added candidate (finalize=true) for session 5."
    }
    Write-Host "Sub-test 2 PASS — search_library first (step $libStepVal), search_results candidates=$candCount2, auto_added=$(($autoAdded2 | Measure-Object).Count)"

    # ═══════════════════════════════════════════════════════════════════════════
    # Sub-test 3: clear prompt — search_semantic_scholar + finalize-cap + papers.
    # ═══════════════════════════════════════════════════════════════════════════
    Write-Host "`n=== Sub-test 3: clear prompt → external search + finalize auto-attach ==="
    $resp3 = Invoke-Chat -SessionId 6 -UserMessage "find recent papers about retrieval augmented generation"
    $final3 = $resp3.Final
    Write-Host "Response: $($final3.Substring(0, [Math]::Min(300, $final3.Length)))"

    # Get run_id for session 6.
    $runRows3 = Invoke-SqliteQuery -DbPath $dbPath -Query "SELECT id FROM runs WHERE session_id = 6 ORDER BY id DESC LIMIT 1"
    if ($runRows3.Count -eq 0) { throw "No run found for session 6." }
    $runId3 = $runRows3[0].Trim()

    # At least one search_semantic_scholar call.
    $ssRows3 = Invoke-SqliteQuery -DbPath $dbPath -Query "SELECT COUNT(*) FROM tool_calls WHERE run_id = $runId3 AND tool = 'paper_search:search_semantic_scholar'"
    $ssCount3 = [int]($ssRows3[0].Trim())
    if ($ssCount3 -lt 1) {
        throw "ASSERTION: expected >= 1 search_semantic_scholar tool_calls row for session 6, got $ssCount3"
    }

    # search_results event must arrive.
    $candCount3 = ($resp3.Candidates | Measure-Object).Count
    if ($candCount3 -lt 1) {
        throw "ASSERTION: expected >= 1 search_results candidate for session 6, got $candCount3"
    }

    # At most 2 auto_added (finalize cap).
    $autoAdded3 = @($resp3.Candidates | Where-Object { $_.auto_added -eq $true })
    $autoCount3 = ($autoAdded3 | Measure-Object).Count
    if ($autoCount3 -gt 2) {
        throw "ASSERTION: finalize cap=2 violated for session 6, got $autoCount3 auto_added"
    }

    # papers row count matches the number of auto_added candidates.
    $papersRows3 = Invoke-SqliteQuery -DbPath $dbPath -Query "SELECT COUNT(*) FROM papers WHERE session_id = 6 AND enabled = 1"
    $papersCount3 = [int]($papersRows3[0].Trim())
    if ($papersCount3 -ne $autoCount3) {
        throw "ASSERTION: enabled papers count ($papersCount3) for session 6 should match auto_added ($autoCount3)."
    }

    # Suggested-only candidates have NO corresponding papers row.
    $suggested3 = @($resp3.Candidates | Where-Object { $_.auto_added -eq $false -and ($null -eq $_.error -or $_.error -eq "") })
    Write-Host "Sub-test 3 PASS — ss_calls=$ssCount3, candidates=$candCount3, auto_added=$autoCount3, suggested-only=$(($suggested3 | Measure-Object).Count), papers=$papersCount3"

    Write-Host "`nPASS: research_turn smoke test complete (all 3 sub-tests)."

} finally {
    & taskkill.exe /F /T /PID $server.Id 2>&1 | Out-Null
    if (Test-Path $env:PAPERHUB_WORKSPACE) {
        Remove-Item -Recurse -Force $env:PAPERHUB_WORKSPACE -ErrorAction SilentlyContinue
    }
}
