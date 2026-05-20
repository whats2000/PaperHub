# Coordinated start: model server + backend.
#
# Why this script exists: the embedder (SentenceTransformer ~110 MB)
# and reranker (CrossEncoder ~80 MB) live in a SEPARATE process so
# uvicorn --reload on backend code can't reset their weights. The
# previous design auto-spawned that process from inside the lifespan,
# but that tied modelserver lifecycle to worker lifecycle — every
# reload orphaned-then-respawned the model process, defeating the
# isolation, AND blocked lifespan for the duration of HF model
# downloads.
#
# This script:
#   1. Starts `paperhub-modelserver` as a background process.
#   2. Polls /health until it's reachable.
#   3. Starts uvicorn in the foreground.
#   4. On Ctrl+C (or any script exit), terminates the modelserver
#      so we don't orphan it.
#
# Usage:
#     .\scripts\start.ps1                    # backend :8000, modelserver :8001, MCP daemons up
#     .\scripts\start.ps1 -BackendPort 8765  # custom backend port
#     .\scripts\start.ps1 -NoReload          # production-style: no uvicorn --reload
#     .\scripts\start.ps1 -NoWebSearch       # skip launching external MCP daemons (open-websearch)
#
# The model server is **deliberately not** subject to --reload — its
# whole reason to exist is to survive backend reloads. If you change
# code under `paperhub/modelserver/`, restart this script.
[CmdletBinding()]
param(
    [int]$BackendPort = 8000,
    [int]$ModelServerPort = 8001,
    [string]$BindHost = "127.0.0.1",     # $Host is a PowerShell reserved automatic
    [switch]$NoReload,                   # default: uvicorn --reload on
    [switch]$NoWebSearch,                # default: ensure external MCP daemons (open-websearch)
    [switch]$NoReap                      # default: reap stale listeners from a prior run before starting
)

$ErrorActionPreference = "Stop"
Set-Location -Path (Join-Path $PSScriptRoot "..")

# --- process-tree teardown helpers -------------------------------------------
# We spawn children through `uv` / `npx` wrappers, so the handle we hold is the
# WRAPPER, not the server. Killing the wrapper orphans the real grandchild (the
# modelserver leak: `Stop-Process` on the `uv` wrapper left `-m paperhub.modelserver`
# running, holding :8001 and ~400 MB — repeated across runs => OOM). taskkill /T
# walks + kills the whole tree; reaping by port is the wrapper-handle-independent
# backstop (and the only handle we have for detached daemons / leaked workers).
function Stop-ProcessTree([int]$procId, [string]$why) {
    if ($procId -le 0) { return }
    & taskkill /F /T /PID $procId 2>$null | Out-Null
    if ($why) { Write-Host "  killed pid=$procId ($why)" -ForegroundColor DarkYellow }
}

function Stop-PortListeners([int]$port, [string]$why) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($conn in $conns) {
        Write-Host "Stopping listener on :$port (pid=$($conn.OwningProcess)) — $why" -ForegroundColor Yellow
        Stop-ProcessTree $conn.OwningProcess $why
    }
}

# Propagate ports to the modelserver subprocess via env, so its
# Settings.load picks the right bind address.
$env:PAPERHUB_MODEL_SERVER_HOST = $BindHost
$env:PAPERHUB_MODEL_SERVER_PORT = $ModelServerPort

$modelProc = $null
# Sidecar file written by paperhub-mcp-up: ports of MCP daemons it started this
# run. We tree-kill their listeners on exit (the daemons are spawned detached,
# so the CLI can't hand us a process handle — port → PID → taskkill /T is the
# reliable cleanup).
$mcpPortsFile = Join-Path $PSScriptRoot "..\.mcp_daemon_ports"
$cleanup = {
    # Modelserver: we hold the `uv` wrapper handle, but the real server is its
    # python grandchild (`-m paperhub.modelserver`). Tree-kill the wrapper so the
    # child dies too, then reap the port as a backstop (covers the case where the
    # wrapper already exited but the grandchild kept the socket).
    if ($script:modelProc -and -not $script:modelProc.HasExited) {
        Write-Host "Stopping modelserver tree (wrapper pid=$($script:modelProc.Id))" -ForegroundColor Yellow
        Stop-ProcessTree $script:modelProc.Id "modelserver wrapper"
    }
    Stop-PortListeners $script:ModelServerPort "modelserver"

    # Backend: foreground `& uv` should exit with the script, but uvicorn
    # --reload workers (multiprocessing spawn children) can outlive a Ctrl+C —
    # reap the port to catch orphaned ~580 MB workers.
    Stop-PortListeners $script:BackendPort "backend"

    # MCP daemons paperhub-mcp-up started (open-websearch, etc.). NOTE: we do NOT
    # reap the MCP port at startup — those are detach-and-leak BY DESIGN (reused
    # across runs to skip the ~25s npx cold start); only this run's owned daemons
    # (recorded in the sidecar) are torn down here.
    if (Test-Path $script:mcpPortsFile) {
        foreach ($line in (Get-Content $script:mcpPortsFile -ErrorAction SilentlyContinue)) {
            $port = 0
            if (-not [int]::TryParse($line.Trim(), [ref]$port) -or $port -eq 0) { continue }
            Stop-PortListeners $port "mcp daemon"
        }
        Remove-Item $script:mcpPortsFile -Force -ErrorAction SilentlyContinue
    }
}

try {
    # Step 0: reap stragglers from a prior run. PowerShell's `finally` does NOT
    # reliably run when a native foreground command (`& uv`) catches Ctrl+C, so
    # leaked modelservers + ~580 MB --reload workers accumulate across runs and
    # eventually OOM the box. Tree-kill anything still holding OUR ports before we
    # spawn fresh — this is the backstop that makes "start all, close all" hold
    # even when last run's teardown was skipped. (MCP :3000 is intentionally left
    # alone — detach-and-leak by design.) Skip with -NoReap.
    if (-not $NoReap) {
        Write-Host "Reaping stale listeners on :$BackendPort / :$ModelServerPort ..." -ForegroundColor DarkCyan
        Stop-PortListeners $ModelServerPort "stale modelserver"
        Stop-PortListeners $BackendPort "stale backend"
    }

    # Step 1: start the model server in the background. Inherits the
    # current uv-managed Python environment via `uv run`.
    Write-Host "Starting modelserver on ${BindHost}:$ModelServerPort..." -ForegroundColor Cyan
    $modelProc = Start-Process -PassThru -NoNewWindow `
        -FilePath "uv" `
        -ArgumentList @("run", "paperhub-modelserver")

    # Step 2: poll /health. Cold HF cache → first load can be minutes;
    # generous timeout. Each line gives the operator a visible
    # heartbeat so they don't think the script is wedged.
    $healthUrl = "http://${BindHost}:$ModelServerPort/health"
    $readyTimeoutSec = 60
    $deadline = (Get-Date).AddSeconds($readyTimeoutSec)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        if ($modelProc.HasExited) {
            throw "modelserver exited prematurely with code $($modelProc.ExitCode); check its stdout above"
        }
        try {
            $r = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 2 -ErrorAction Stop
            if ($r.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch {
            # Not up yet — quiet retry.
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) {
        throw "modelserver did not become ready at $healthUrl within ${readyTimeoutSec}s"
    }
    Write-Host "modelserver ready at $healthUrl (pid=$($modelProc.Id))" -ForegroundColor Green

    # Step 2b: ensure external MCP daemons are up (open-websearch today; sql/fs
    # later). `paperhub-mcp-up` reads mcp_servers.toml and launches every
    # `launch`-declaring server via a detached subprocess.Popen — loop-
    # independent, so it works even though uvicorn --reload forces a
    # SelectorEventLoop on Windows (where the in-worker asyncio spawn raises
    # NotImplementedError). The daemons are detach-and-leak: they survive
    # backend reloads and are reused on the next boot. NON-FATAL: web search
    # is optional, so a failure here just means the agent falls back to
    # papers-only — we warn and keep booting. Skip with -NoWebSearch.
    if (-not $NoWebSearch) {
        Write-Host "Ensuring external MCP daemons (paperhub-mcp-up)..." -ForegroundColor Cyan
        try {
            & uv run paperhub-mcp-up
            if ($LASTEXITCODE -ne 0) {
                Write-Host "paperhub-mcp-up exited $LASTEXITCODE; continuing without it" -ForegroundColor Yellow
            }
        } catch {
            Write-Host "paperhub-mcp-up failed ($_); continuing without external MCP daemons" -ForegroundColor Yellow
        }
    } else {
        Write-Host "Skipping external MCP daemons (-NoWebSearch)" -ForegroundColor DarkGray
    }

    # Step 3: start the backend in the foreground. --reload watches
    # src/ only so workspace/ writes and .venv/ activity don't trigger
    # spurious worker restarts.
    $uvicornArgs = @(
        "run", "uvicorn", "paperhub.app:app",
        "--host", $BindHost, "--port", $BackendPort
    )
    if (-not $NoReload) {
        $uvicornArgs += @("--reload", "--reload-dir", "src")
    }

    Write-Host "Starting backend on ${BindHost}:$BackendPort ..." -ForegroundColor Cyan
    & uv @uvicornArgs
} finally {
    & $cleanup
}
