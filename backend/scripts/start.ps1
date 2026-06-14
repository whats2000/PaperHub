# Coordinated start: backend + external MCP daemons.
#
# This script:
#   1. Reaps stale port listeners from a prior run (optional, -NoReap to skip).
#   2. Ensures external MCP daemons are running (open-websearch via paperhub-mcp-up).
#   3. Starts uvicorn in the foreground.
#   4. On Ctrl+C (or any script exit), tears down the backend port listener and
#      any MCP daemons this run started.
#
# Usage:
#     .\scripts\start.ps1                    # backend :8000, MCP daemons up
#     .\scripts\start.ps1 -BackendPort 8765  # custom backend port
#     .\scripts\start.ps1 -NoReload          # production-style: no uvicorn --reload
#     .\scripts\start.ps1 -NoWebSearch       # skip launching external MCP daemons (open-websearch)
#
[CmdletBinding()]
param(
    [int]$BackendPort = 8000,
    [string]$BindHost = "127.0.0.1",     # $Host is a PowerShell reserved automatic
    [switch]$NoReload,                   # default: uvicorn --reload on
    [switch]$NoWebSearch,                # default: ensure external MCP daemons (open-websearch)
    [switch]$NoReap                      # default: reap stale listeners from a prior run before starting
)

$ErrorActionPreference = "Stop"
Set-Location -Path (Join-Path $PSScriptRoot "..")

# --- process-tree teardown helpers -------------------------------------------
# We spawn children through `uv` / `npx` wrappers, so the handle we hold is the
# WRAPPER, not the server. taskkill /T walks + kills the whole tree; reaping by
# port is the wrapper-handle-independent backstop (and the only handle we have
# for detached daemons / leaked workers).
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

# Sidecar file written by paperhub-mcp-up: ports of MCP daemons it started this
# run. We tree-kill their listeners on exit (the daemons are spawned detached,
# so the CLI can't hand us a process handle — port → PID → taskkill /T is the
# reliable cleanup).
$mcpPortsFile = Join-Path $PSScriptRoot "..\.mcp_daemon_ports"
$cleanup = {
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
    # leaked ~580 MB --reload workers accumulate across runs and eventually OOM
    # the box. Tree-kill anything still holding OUR port before we spawn fresh.
    # (MCP :3000 is intentionally left alone — detach-and-leak by design.)
    # Skip with -NoReap.
    if (-not $NoReap) {
        Write-Host "Reaping stale listeners on :$BackendPort ..." -ForegroundColor DarkCyan
        Stop-PortListeners $BackendPort "stale backend"
    }

    # Step 1: ensure external MCP daemons are up (open-websearch today; sql/fs
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

    # Step 2: start the backend in the foreground. --reload watches
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
