# Wave 1 end-to-end smoke for the SQL Agent (Plan E, SRS v2.16).
#
# Asserts two behaviours with NO real LLM key (all LLM calls mocked):
#
#   1. SQL ANSWER: a library_stats turn streams to a final SSE event whose
#      content contains a backtick-backtick-backtick-sql fenced block.
#      The planner mock is a valid SELECT so sql.query succeeds; the answer
#      mock contains the fence.
#
#   2. REJECTED ROW: a library_stats turn whose planner mock is a write
#      statement (DELETE FROM papers) is rejected by the sql.query handler
#      (sqlglot gate) and leaves a tool_calls row with status='rejected'
#      (NFR-05 / acceptance I-8 #1).
#
# Mock mechanism: PAPERHUB_SQL_PLANNER_MOCK / PAPERHUB_SQL_ANSWER_MOCK are read
# by chat.py's library_stats branch, mirroring PAPERHUB_ROUTER_MOCK /
# PAPERHUB_CHITCHAT_MOCK used by smoke_chat.ps1.  Because child processes
# inherit environment at spawn time (not dynamically), the two sub-tests each
# start a dedicated backend instance on port 8771 with their specific mocks
# pre-set so the uvicorn worker sees the right values on startup.
#
# Usage: cd backend; .\scripts\smoke_sql_agent.ps1
$ErrorActionPreference = "Stop"

$backendDir = Resolve-Path (Join-Path $PSScriptRoot "..")
$port = 8771

# ---------------------------------------------------------------------------
# Helper: wait for /health, issue one /chat POST, return raw SSE bytes.
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
# Helper: parse the final / error events out of raw SSE output.
# Accepts either a string or an Object[] (what curl.exe returns in PS).
# Returns a hashtable { FinalContent, GotError }.
# ---------------------------------------------------------------------------
function Parse-Sse {
    param($Raw)
    $result = @{ FinalContent = $null; GotError = $false }
    $cur = $null
    # curl.exe output in PowerShell is an Object[] (one element per line).
    # Normalise to a flat array of trimmed strings regardless of input type.
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

    # Pre-flight: port must be free.
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
# Shared env vars (constant across both sub-tests).
# ---------------------------------------------------------------------------
$env:PAPERHUB_INPROCESS_MODELS = "1"
$env:PAPERHUB_BOOT_BANNER = "0"
# Router always routes to library_stats -- no real router LLM needed.
$env:PAPERHUB_ROUTER_MOCK = '{"intent":"library_stats","model_tier":"small","confidence":0.95,"reasoning":"smoke","resolved_query":"how many papers do I have?","response_language":"English"}'
Remove-Item Env:PAPERHUB_CHITCHAT_MOCK -ErrorAction SilentlyContinue

# MCP config pointing sql server at the smoke port (not the default :8000).
$mcpConfig = Join-Path $env:TEMP "smoke_sql_mcp_servers.toml"
[System.IO.File]::WriteAllText($mcpConfig,
    "[[server]]`nname = `"sql`"`ntransport = `"streamable_http`"`nurl = `"http://localhost:$port/mcp-sql`"`nexpose = [`"list_tables`", `"describe`", `"query`"]`ntimeout_seconds = 8.0`n"
)
$env:PAPERHUB_MCP_CONFIG = $mcpConfig

# Three backtick chars -- avoids PowerShell string-escaping issues.
$fence = [string]::new([char]96, 3)

# =========================================================================
# Sub-test 1: SQL ANSWER
# =========================================================================
Write-Host ""
Write-Host "=== Sub-test 1: library_stats -> sql fence in final answer ==="

# Workspace for sub-test 1.
$ws1 = Join-Path $backendDir "workspace_smoke_sql_1"
if (Test-Path $ws1) { Remove-Item -Recurse -Force $ws1 }
$env:PAPERHUB_WORKSPACE = $ws1

$env:PAPERHUB_SQL_PLANNER_MOCK = "SELECT count(*) AS n FROM papers"
$nl = [System.Environment]::NewLine
$env:PAPERHUB_SQL_ANSWER_MOCK = "You have 0 papers saved." + $nl + $nl + $fence + "sql" + $nl + "SELECT count(*) AS n FROM papers" + $nl + $fence

$srv1 = $null
try {
    $srv1 = Start-Backend -Dir $backendDir -Port $port

    $sse1 = Invoke-ChatTurn -BaseUrl "http://127.0.0.1:$port" `
        -Body '{"user_message":"how many papers do I have?"}' `
        -TmpFile (Join-Path $env:TEMP "smoke_sql_body1.json")

    $parsed1 = Parse-Sse -Raw $sse1
    if ($parsed1.GotError) {
        throw "ASSERTION 1 FAILED: backend returned an error SSE event.`nFull SSE:`n$sse1"
    }
    if ($null -eq $parsed1.FinalContent) {
        throw "ASSERTION 1 FAILED: no final SSE event.`nFull SSE:`n$sse1"
    }
    Write-Host "  final content: $($parsed1.FinalContent)"

    if ($parsed1.FinalContent -notlike ("*" + $fence + "sql*")) {
        throw "ASSERTION 1 FAILED: final answer does not contain a sql fence.`nActual: $($parsed1.FinalContent)"
    }
    Write-Host "Sub-test 1 PASS -- final event received, sql fence present." -ForegroundColor Green

    # Informational replay.
    Write-Host ""
    Write-Host "--- Replay (run 1) ---"
    $db1 = Join-Path $ws1 "paperhub.db"
    & uv run --project $backendDir paperhub-replay --run-id 1
    if ($LASTEXITCODE -ne 0) { Write-Host "  (paperhub-replay non-zero -- informational)" }

} finally {
    if ($null -ne $srv1) { Stop-Backend -Proc $srv1 }
    if (Test-Path $ws1) { Remove-Item -Recurse -Force $ws1 -ErrorAction SilentlyContinue }
}

# =========================================================================
# Sub-test 2: REJECTED ROW
# =========================================================================
Write-Host ""
Write-Host "=== Sub-test 2: write SQL -> tool_calls.status='rejected' ==="

# Workspace for sub-test 2 (fresh DB, no run history from sub-test 1).
$ws2 = Join-Path $backendDir "workspace_smoke_sql_2"
if (Test-Path $ws2) { Remove-Item -Recurse -Force $ws2 }
$env:PAPERHUB_WORKSPACE = $ws2

# Planner mock emits DELETE -- the sqlglot gate in sql.query rejects it.
# The self-repair path also emits DELETE (repair_mock defaults to planner_mock
# inside sql_agent_stream when not supplied), so both sql.query calls reject.
# The answer step still fires (agent does not raise on rejection).
$env:PAPERHUB_SQL_PLANNER_MOCK = "DELETE FROM papers"
$env:PAPERHUB_SQL_ANSWER_MOCK  = "I was unable to execute that query."

$srv2 = $null
try {
    $srv2 = Start-Backend -Dir $backendDir -Port $port

    $sse2 = Invoke-ChatTurn -BaseUrl "http://127.0.0.1:$port" `
        -Body '{"user_message":"delete all my papers"}' `
        -TmpFile (Join-Path $env:TEMP "smoke_sql_body2.json")

    $parsed2 = Parse-Sse -Raw $sse2
    if ($parsed2.GotError) {
        throw "ASSERTION 2 FAILED: backend returned an error SSE event.`nFull SSE:`n$sse2"
    }
    if ($null -eq $parsed2.FinalContent) {
        throw "ASSERTION 2 FAILED: no final SSE event.`nFull SSE:`n$sse2"
    }
    Write-Host "  final content: $($parsed2.FinalContent)"

    # Assert at least one tool_calls row has status='rejected'.
    $db2 = Join-Path $ws2 "paperhub.db"
    $runId2Raw = & uv run --project $backendDir python -c "import sqlite3, sys; db=sys.argv[1]; conn=sqlite3.connect(db); row=conn.execute('SELECT id FROM runs ORDER BY id DESC LIMIT 1').fetchone(); print(row[0] if row else '')" $db2
    if ($LASTEXITCODE -ne 0) { throw "Failed to fetch latest run_id from DB." }
    $runId2 = ($runId2Raw | Out-String).Trim()
    if (-not $runId2) { throw "ASSERTION 2 FAILED: no runs row found." }
    Write-Host "  run_id = $runId2"

    $rejCountRaw = & uv run --project $backendDir python -c "import sqlite3, sys; db,rid=sys.argv[1],sys.argv[2]; conn=sqlite3.connect(db); row=conn.execute('SELECT count(*) FROM tool_calls WHERE run_id=? AND status=?',(rid,'rejected')).fetchone(); print(row[0] if row else 0)" $db2 $runId2
    if ($LASTEXITCODE -ne 0) { throw "Failed to query tool_calls for rejected rows." }
    $rejCount = [int](($rejCountRaw | Out-String).Trim())
    Write-Host "  tool_calls rows with status='rejected' for run ${runId2}: $rejCount"

    if ($rejCount -lt 1) {
        Write-Host "  Dumping all tool_calls for run ${runId2}:" -ForegroundColor Yellow
        & uv run --project $backendDir python -c "import sqlite3, sys; db,rid=sys.argv[1],sys.argv[2]; conn=sqlite3.connect(db); [print(r) for r in conn.execute('SELECT step_index,tool,status,error FROM tool_calls WHERE run_id=? ORDER BY step_index',(rid,)).fetchall()]" $db2 $runId2
        throw "ASSERTION 2 FAILED: no tool_calls row with status='rejected' for run $runId2 (NFR-05)."
    }
    Write-Host "Sub-test 2 PASS -- tool_calls.status='rejected' confirmed for write SQL." -ForegroundColor Green

    # Informational replay.
    Write-Host ""
    Write-Host "--- Replay (rejected run $runId2) ---"
    & uv run --project $backendDir paperhub-replay --run-id $runId2
    if ($LASTEXITCODE -ne 0) { Write-Host "  (paperhub-replay non-zero -- informational)" }

} finally {
    if ($null -ne $srv2) { Stop-Backend -Proc $srv2 }
    if (Test-Path $ws2) { Remove-Item -Recurse -Force $ws2 -ErrorAction SilentlyContinue }
    if (Test-Path $mcpConfig) { Remove-Item -Force $mcpConfig -ErrorAction SilentlyContinue }
}

Write-Host ""
Write-Host "smoke_sql_agent: OK" -ForegroundColor Green
