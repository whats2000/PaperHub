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
#     .\scripts\start.ps1                    # backend on :8000, modelserver on :8001
#     .\scripts\start.ps1 -BackendPort 8765  # custom backend port
#     .\scripts\start.ps1 -NoReload          # production-style: no uvicorn --reload
#
# The model server is **deliberately not** subject to --reload — its
# whole reason to exist is to survive backend reloads. If you change
# code under `paperhub/modelserver/`, restart this script.
[CmdletBinding()]
param(
    [int]$BackendPort = 8000,
    [int]$ModelServerPort = 8001,
    [string]$BindHost = "127.0.0.1",     # $Host is a PowerShell reserved automatic
    [switch]$NoReload                    # default: uvicorn --reload on
)

$ErrorActionPreference = "Stop"
Set-Location -Path (Join-Path $PSScriptRoot "..")

# Propagate ports to the modelserver subprocess via env, so its
# Settings.load picks the right bind address.
$env:PAPERHUB_MODEL_SERVER_HOST = $BindHost
$env:PAPERHUB_MODEL_SERVER_PORT = $ModelServerPort

$modelProc = $null
$cleanup = {
    if ($script:modelProc -and -not $script:modelProc.HasExited) {
        Write-Host "Stopping modelserver (pid=$($script:modelProc.Id))" -ForegroundColor Yellow
        try {
            $script:modelProc.CloseMainWindow() | Out-Null
            if (-not $script:modelProc.WaitForExit(5000)) {
                Stop-Process -Id $script:modelProc.Id -Force -ErrorAction SilentlyContinue
            }
        } catch {
            Stop-Process -Id $script:modelProc.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

try {
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
