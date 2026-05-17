# scripts/smoke.ps1
# Phase A end-to-end smoke runner. Boots backend + frontend dev servers,
# runs the e2e pytest, tears everything down.
#
# Prereqs:
#   - ANTHROPIC_API_KEY in the environment (or .env)
#   - GROBID running at $env:PAPERHUB_GROBID_URL (default http://localhost:8070)
#   - uvx + npm on PATH
#
# Usage: pwsh -File scripts/smoke.ps1

param(
    [int]$BackendPort = 8765,
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $repoRoot

# Defaults required by the backend if not pre-set in the caller's env
if (-not $env:PAPERHUB_WORKSPACE_ROOT) {
    $env:PAPERHUB_WORKSPACE_ROOT = Join-Path $env:USERPROFILE "PaperHub/workspace"
}
if (-not $env:PAPERHUB_DB_PATH) {
    $env:PAPERHUB_DB_PATH = Join-Path $env:USERPROFILE "PaperHub/paperhub.db"
}

# Ensure workspace dir exists
New-Item -ItemType Directory -Path $env:PAPERHUB_WORKSPACE_ROOT -Force | Out-Null

Write-Host "[smoke] Booting backend on port $BackendPort..."
$backend = Start-Process pwsh `
    -ArgumentList '-NoProfile', '-Command', "cd backend; uv run uvicorn paperhub.api.app:create_app --factory --port $BackendPort" `
    -PassThru -WindowStyle Hidden

Write-Host "[smoke] Booting frontend dev server on port $FrontendPort..."
$frontend = Start-Process pwsh `
    -ArgumentList '-NoProfile', '-Command', "cd frontend; npm run dev -- --port $FrontendPort" `
    -PassThru -WindowStyle Hidden

# Wait for the backend health endpoint
$backendReady = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:$BackendPort/health" -UseBasicParsing -TimeoutSec 1
        if ($r.StatusCode -eq 200) { $backendReady = $true; break }
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $backendReady) {
    Write-Error "[smoke] Backend did not become ready on port $BackendPort within 30s"
    Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
    Stop-Process -Id $frontend.Id -Force -ErrorAction SilentlyContinue
    exit 1
}
Write-Host "[smoke] Backend up. Frontend at http://localhost:$FrontendPort"

try {
    Write-Host "[smoke] Running e2e pytest..."
    Set-Location backend
    uv run pytest -m e2e -v
    $code = $LASTEXITCODE
    Set-Location $repoRoot
    exit $code
} finally {
    Write-Host "[smoke] Tearing down backend + frontend processes..."
    Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
    Stop-Process -Id $frontend.Id -Force -ErrorAction SilentlyContinue
}
