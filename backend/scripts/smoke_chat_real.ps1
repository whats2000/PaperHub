# Run the backend end-to-end against a REAL LLM (no router/chitchat mocks).
# Loads backend/.env, starts uvicorn, posts to /chat, then replays from SQLite.
#
# Two sub-tests:
#   1. chitchat turn — original v2.4 smoke, verifies the chitchat path is
#      alive and SQLite replay works.
#   2. paper_search turn — exercises the v2.5/v2.6 MCP dispatch. Gated on
#      whether `open-websearch serve` is running:
#        * daemon UP → expect web.search → papers.search_semantic_scholar
#          → search_results SSE event with >= 1 candidate.
#        * daemon DOWN → expect the turn to still complete via papers-only
#          dispatch with NO web.* tool_calls rows.
$ErrorActionPreference = "Stop"

$backendDir = Resolve-Path (Join-Path $PSScriptRoot "..")
$envFile = Join-Path $backendDir ".env"
if (-not (Test-Path $envFile)) {
    throw "Missing $envFile. Copy backend/.env.example to backend/.env and fill in your API key."
}

# Parse KEY=VALUE lines from .env into the current process environment.
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
    if ($val -ne "") {
        Set-Item -Path "env:$key" -Value $val
    }
}

# Real-flow guard: make sure we have *some* provider key for the chosen models.
$routerModel = if ($env:PAPERHUB_ROUTER_MODEL) { $env:PAPERHUB_ROUTER_MODEL } else { "gemini/gemini-2.5-flash" }
$chitchatModel = if ($env:PAPERHUB_CHITCHAT_MODEL) { $env:PAPERHUB_CHITCHAT_MODEL } else { "gemini/gemini-2.5-flash" }
$paperSearchModel = if ($env:PAPERHUB_PAPER_SEARCH_MODEL) { $env:PAPERHUB_PAPER_SEARCH_MODEL } else { "gemini/gemini-2.5-pro" }
$needsKey = @{
    "gemini" = "GEMINI_API_KEY"
    "openai" = "OPENAI_API_KEY"
    "anthropic" = "ANTHROPIC_API_KEY"
}
foreach ($model in @($routerModel, $chitchatModel, $paperSearchModel)) {
    $provider = ($model -split "/", 2)[0]
    $keyName = $needsKey[$provider]
    if ($keyName -and -not (Get-Item -Path "env:$keyName" -ErrorAction SilentlyContinue).Value) {
        throw "Model '$model' requires env var '$keyName'. Set it in backend/.env."
    }
}

# Isolated workspace so this run doesn't share state with the mocked smoke script.
$env:PAPERHUB_WORKSPACE = Join-Path $backendDir "workspace_smoke_real"
if (Test-Path $env:PAPERHUB_WORKSPACE) {
    Remove-Item -Recurse -Force $env:PAPERHUB_WORKSPACE
}

# Explicitly clear the mock vars from the mocked smoke script, in case they leaked into the shell.
Remove-Item Env:PAPERHUB_ROUTER_MOCK -ErrorAction SilentlyContinue
Remove-Item Env:PAPERHUB_CHITCHAT_MOCK -ErrorAction SilentlyContinue

# Pre-flight: port must be free so the /health probe can't succeed against an orphan from a prior run.
$portInUse = $false
try {
    $tcp = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 8766)
    $tcp.Start(); $tcp.Stop()
} catch { $portInUse = $true }
if ($portInUse) {
    throw "Port 8766 already in use. Kill the orphan uvicorn process (taskkill /F /IM python.exe or similar) and retry."
}

# Probe whether the open-websearch daemon is running BEFORE starting uvicorn,
# because the MCP registry lazy-connects on first use; the registry's
# connect-failed cache is per-process. Operators who run this script AFTER
# starting the daemon get the v2 path; operators who haven't installed
# open-websearch get the v1 (papers-only) path. Both are valid.
$webDaemonUp = $false
try {
    $tcp = [System.Net.Sockets.TcpClient]::new()
    $tcp.ConnectAsync("localhost", 3000).Wait(2000) | Out-Null
    if ($tcp.Connected) {
        $webDaemonUp = $true
        $tcp.Close()
    }
} catch { }
if ($webDaemonUp) {
    Write-Host "open-websearch daemon UP at :3000 — will assert v2 paper_search path." -ForegroundColor Green
} else {
    Write-Host "open-websearch daemon DOWN — will assert v1 (papers-only) paper_search path." -ForegroundColor Yellow
    Write-Host "  (Install with 'npm install -g open-websearch' and run 'open-websearch serve' to exercise v2.)" -ForegroundColor Yellow
}

$server = Start-Process -PassThru -NoNewWindow uv -ArgumentList @(
    "run", "uvicorn", "paperhub.app:app", "--host", "127.0.0.1", "--port", "8766"
)
try {
    for ($i = 0; $i -lt 50; $i++) {
        try {
            Invoke-RestMethod http://127.0.0.1:8766/health -ErrorAction Stop | Out-Null
            break
        } catch {
            Start-Sleep -Milliseconds 200
        }
    }

    # ── Sub-test 1: chitchat turn ─────────────────────────────────────────────
    Write-Host "`n=== Sub-test 1: chitchat turn (router=$routerModel, chitchat=$chitchatModel) ==="
    $userMessage = if ($args.Count -gt 0) { $args -join " " } else { "hello, what can you help me with?" }
    $body = @{ user_message = $userMessage } | ConvertTo-Json -Compress
    $tmpBody = Join-Path $env:TEMP "smoke_body_real.json"
    [System.IO.File]::WriteAllText($tmpBody, $body)
    curl.exe -N -s -X POST http://127.0.0.1:8766/chat `
        -H "Content-Type: application/json" `
        --data-binary "@$tmpBody"
    Write-Host "`n--- Replay (run 1) ---"
    uv run paperhub-replay --run-id 1

    # ── Sub-test 2: paper_search turn (MCP dispatch) ─────────────────────────
    Write-Host "`n=== Sub-test 2: paper_search turn — MCP dispatch (paper_search=$paperSearchModel) ==="
    $dbPath = Join-Path $env:PAPERHUB_WORKSPACE "paperhub.db"
    # Pre-create session 200 — chat auto-creates one per request, but giving the
    # paper_search turn its own stable id makes the trace inspection deterministic.
    & uv run --project $backendDir python -c @'
import sqlite3, sys
db = sys.argv[1]
conn = sqlite3.connect(db)
conn.execute("PRAGMA foreign_keys = ON")
conn.execute("INSERT OR IGNORE INTO chat_sessions (id, title) VALUES (200, 'smoke-real-search')")
conn.commit()
conn.close()
'@ $dbPath
    if ($LASTEXITCODE -ne 0) { throw "Failed to pre-create chat_sessions row 200." }

    # Vague-but-paper-search-worthy query — should route to paper_search.
    $searchBody = @{
        session_id = 200
        user_message = "find recent papers about retrieval augmented generation"
    } | ConvertTo-Json -Compress
    $tmpSearchBody = Join-Path $env:TEMP "smoke_body_real_search.json"
    [System.IO.File]::WriteAllText($tmpSearchBody, $searchBody)
    $sseRaw = & curl.exe -N -s -X POST http://127.0.0.1:8766/chat `
        -H "Content-Type: application/json" `
        --data-binary "@$tmpSearchBody"
    if ($LASTEXITCODE -ne 0) { throw "curl.exe failed (paper_search sub-test)." }

    # Parse SSE: collect `final` content + `search_results` candidates.
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
        throw "ASSERTION: paper_search turn produced no `final` SSE event. Raw:`n$sseRaw"
    }

    # Get the run_id for session 200's most recent run, then inspect tool_calls.
    $runRowRaw = & uv run --project $backendDir python -c @'
import sqlite3, sys
db = sys.argv[1]
conn = sqlite3.connect(db)
row = conn.execute(
    "SELECT id FROM runs WHERE session_id = 200 ORDER BY id DESC LIMIT 1"
).fetchone()
print(row[0] if row else "")
conn.close()
'@ $dbPath
    if ($LASTEXITCODE -ne 0) { throw "Failed to fetch run_id for session 200." }
    $runId = ($runRowRaw | Out-String).Trim()
    if (-not $runId) { throw "ASSERTION: no runs row for session 200." }
    Write-Host "paper_search run_id = $runId"

    $toolsRaw = & uv run --project $backendDir python -c @'
import sqlite3, sys
db, run_id = sys.argv[1], sys.argv[2]
conn = sqlite3.connect(db)
for (tool,) in conn.execute(
    "SELECT tool FROM tool_calls WHERE run_id = ? ORDER BY step_index", (run_id,)
).fetchall():
    print(tool)
conn.close()
'@ $dbPath $runId
    if ($LASTEXITCODE -ne 0) { throw "Failed to list tool_calls for run $runId." }
    $tools = @($toolsRaw | Where-Object { $_ -ne "" } | ForEach-Object { $_.Trim() })
    Write-Host "tool_calls rows for run ${runId}: $($tools -join ', ')"

    $hasWebSearch = $tools -contains "paper_search:web.search"
    $hasSsSearch  = $tools -contains "paper_search:papers.search_semantic_scholar"
    $hasLibSearch = $tools -contains "paper_search:papers.search_library"
    $anyWebTool   = @($tools | Where-Object { $_ -like "paper_search:web.*" }).Count -gt 0
    $candCount    = ($searchCandidates | Measure-Object).Count

    if ($webDaemonUp) {
        # v2 path: discover via web.search, then refine via papers.search_semantic_scholar,
        # surface in search_results SSE event.
        if (-not $hasWebSearch) {
            throw "ASSERTION (daemon UP): expected at least one paper_search:web.search tool_call. Got: $($tools -join ', ')"
        }
        if (-not $hasSsSearch) {
            throw "ASSERTION (daemon UP): expected paper_search:papers.search_semantic_scholar to follow web.search. Got: $($tools -join ', ')"
        }
        if ($candCount -lt 1) {
            throw "ASSERTION (daemon UP): expected >= 1 search_results candidate, got $candCount."
        }
        Write-Host "Sub-test 2 PASS (v2 path) — web.search ✓, papers.search_semantic_scholar ✓, candidates=$candCount" -ForegroundColor Green
    } else {
        # v1 path: papers-only — must have at least search_library, and zero web.* tools.
        if ($anyWebTool) {
            throw "ASSERTION (daemon DOWN): unexpected paper_search:web.* tool_call when daemon is unreachable. Got: $($tools -join ', ')"
        }
        if (-not ($hasLibSearch -or $hasSsSearch)) {
            throw "ASSERTION (daemon DOWN): expected at least one papers.search_library or papers.search_semantic_scholar tool_call. Got: $($tools -join ', ')"
        }
        Write-Host "Sub-test 2 PASS (v1 path) — papers-only dispatch, no web.* rows, candidates=$candCount" -ForegroundColor Green
    }

    Write-Host "`nPASS: smoke_chat_real complete (chitchat + paper_search MCP dispatch)." -ForegroundColor Green
} finally {
    # uv spawns a python child holding the listening socket; kill the whole tree, not just the launcher.
    & taskkill.exe /F /T /PID $server.Id 2>&1 | Out-Null
}
