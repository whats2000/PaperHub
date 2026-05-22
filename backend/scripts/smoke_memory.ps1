# Wave 2 end-to-end smoke for the Memory System (Plan E, SRS v2.16 FR-10).
#
# Asserts two behaviours with NO real LLM key (all LLM calls mocked):
#
#   1. MEMORY ADD: a `memory` intent turn (router mock -> memory; op mock ->
#      add a GLOBAL memory "answer in Traditional Chinese") reaches a final
#      SSE event, and the workspace DB has a `memories` row with scope='global'.
#
#   2. CROSS-SESSION RECALL: the seeded global memory is visible to a second
#      session (session_id=999 which doesn't own it) via build_memory_context_block.
#
# Usage: cd backend; .\scripts\smoke_memory.ps1
$ErrorActionPreference = "Stop"

$backendDir = Resolve-Path (Join-Path $PSScriptRoot "..")
$port = 8772

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
# Returns a hashtable { FinalContent, GotError }.
# ---------------------------------------------------------------------------
function Parse-Sse {
    param($Raw)
    $result = @{ FinalContent = $null; GotError = $false }
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
            }
        }
        elseif ($line -eq "") { $cur = $null }
    }
    return $result
}

# ---------------------------------------------------------------------------
# Helper: boot uvicorn and wait for /health (up to 30 s).
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
# Write helper Python scripts to TEMP (avoids inline quoting nightmares).
# ---------------------------------------------------------------------------

# Script 1: count global memories rows.
$countScript = Join-Path $env:TEMP "smoke_memory_count.py"
[System.IO.File]::WriteAllText($countScript, @'
import sqlite3, sys
db = sys.argv[1]
conn = sqlite3.connect(db)
row = conn.execute("SELECT count(*) FROM memories WHERE scope='global'").fetchone()
print(row[0] if row else 0)
'@)

# Script 2: cross-session recall via the real helper.
$recallScript = Join-Path $env:TEMP "smoke_memory_recall.py"
[System.IO.File]::WriteAllText($recallScript, @'
import asyncio, os, sys
os.environ["PAPERHUB_INPROCESS_MODELS"] = "1"
os.environ["PAPERHUB_BOOT_BANNER"] = "0"

async def main():
    import aiosqlite
    from paperhub.agents.memory_recall import build_memory_context_block
    db_path = sys.argv[1]
    async with aiosqlite.connect(db_path) as conn:
        block = await build_memory_context_block(
            conn, session_id=999, query="Traditional Chinese", enabled=True,
        )
    print(block)

asyncio.run(main())
'@)

# ---------------------------------------------------------------------------
# Shared env vars.
# ---------------------------------------------------------------------------
$env:PAPERHUB_INPROCESS_MODELS = "1"
$env:PAPERHUB_BOOT_BANNER = "0"
# Router routes to `memory` intent.
$env:PAPERHUB_ROUTER_MOCK = '{"intent":"memory","model_tier":"small","confidence":0.99,"reasoning":"smoke","resolved_query":"remember answer in Traditional Chinese","response_language":"English"}'
Remove-Item Env:PAPERHUB_CHITCHAT_MOCK -ErrorAction SilentlyContinue
Remove-Item Env:PAPERHUB_SQL_PLANNER_MOCK -ErrorAction SilentlyContinue
Remove-Item Env:PAPERHUB_SQL_ANSWER_MOCK -ErrorAction SilentlyContinue

# memory_op mock: add a GLOBAL memory "answer in Traditional Chinese".
$env:PAPERHUB_MEMORY_OP_MOCK = '{"op":"add","scope":"global","content":"answer in Traditional Chinese","target":"","confirmation":"I will remember to answer in Traditional Chinese."}'

# MCP config pointing the memory MCP at the smoke backend port.
$mcpConfig = Join-Path $env:TEMP "smoke_memory_mcp_servers.toml"
[System.IO.File]::WriteAllText($mcpConfig,
    "[[server]]`nname = `"memory`"`ntransport = `"streamable_http`"`nurl = `"http://localhost:$port/mcp-memory`"`nexpose = [`"add`", `"recall`", `"edit`", `"forget`"]`ntimeout_seconds = 8.0`n"
)
$env:PAPERHUB_MCP_CONFIG = $mcpConfig

# Single workspace (global memory must persist between sub-tests).
$ws = Join-Path $backendDir "workspace_smoke_memory"
if (Test-Path $ws) { Remove-Item -Recurse -Force $ws }
$env:PAPERHUB_WORKSPACE = $ws

$srv = $null
try {
    # =========================================================================
    # Sub-test 1: MEMORY ADD
    # =========================================================================
    Write-Host ""
    Write-Host "=== Sub-test 1: memory intent -> global memories row in DB ==="

    $srv = Start-Backend -Dir $backendDir -Port $port

    $sse = Invoke-ChatTurn -BaseUrl "http://127.0.0.1:$port" `
        -Body '{"user_message":"remember to answer in Traditional Chinese globally"}' `
        -TmpFile (Join-Path $env:TEMP "smoke_memory_body1.json")

    $parsed = Parse-Sse -Raw $sse
    if ($parsed.GotError) {
        throw "ASSERTION 1 FAILED: backend returned an error SSE event.`nFull SSE:`n$sse"
    }
    if ($null -eq $parsed.FinalContent) {
        throw "ASSERTION 1 FAILED: no final SSE event.`nFull SSE:`n$sse"
    }
    Write-Host "  final content: $($parsed.FinalContent)"

    $db = Join-Path $ws "paperhub.db"
    $globalCountRaw = & uv run --project $backendDir python $countScript $db
    if ($LASTEXITCODE -ne 0) { throw "Failed to query memories table." }
    $globalCount = [int](($globalCountRaw | Out-String).Trim())
    Write-Host "  memories rows with scope='global': $globalCount"

    if ($globalCount -lt 1) {
        throw "ASSERTION 1 FAILED: no memories row with scope='global' found in DB."
    }
    Write-Host "Sub-test 1 PASS -- global memory row present in DB." -ForegroundColor Green

    # =========================================================================
    # Sub-test 2: CROSS-SESSION RECALL
    # =========================================================================
    Write-Host ""
    Write-Host "=== Sub-test 2: cross-session recall — global memory visible from session 999 ==="

    $recallOut = & uv run --project $backendDir python $recallScript $db
    if ($LASTEXITCODE -ne 0) { throw "Cross-session recall script failed with exit $LASTEXITCODE." }
    $recallBlock = ($recallOut | Out-String).Trim()
    Write-Host "  recall block (session 999): $recallBlock"

    if ($recallBlock -notlike "*Traditional Chinese*") {
        throw "ASSERTION 2 FAILED: global memory not visible from session 999 via recall.`nGot: $recallBlock"
    }
    Write-Host "Sub-test 2 PASS -- global memory visible cross-session via recall." -ForegroundColor Green

} finally {
    if ($null -ne $srv) { Stop-Backend -Proc $srv }
    if (Test-Path $ws) { Remove-Item -Recurse -Force $ws -ErrorAction SilentlyContinue }
    if (Test-Path $mcpConfig) { Remove-Item -Force $mcpConfig -ErrorAction SilentlyContinue }
    foreach ($f in @($countScript, $recallScript, (Join-Path $env:TEMP "smoke_memory_body1.json"))) {
        if (Test-Path $f) { Remove-Item -Force $f -ErrorAction SilentlyContinue }
    }
}

Write-Host ""
Write-Host "smoke_memory: OK" -ForegroundColor Green
