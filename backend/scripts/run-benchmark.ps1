<#
.SYNOPSIS
    Run the PaperHub real-API benchmark against the live backend.

.DESCRIPTION
    Drives the running backend (:8000) as a simulated user: attaches the
    cached reference papers named in the config, routes each prompt through
    /chat, then collects grounding evidence (cited chunk text + agent trace)
    into a JSON + Markdown report for 0/1 review.

    The backend must already be running (scripts/start.ps1). This script does
    NOT boot its own backend — a separate instance would race the user's DB.

.PARAMETER Config
    Path to a benchmark TOML config (relative to backend/, or absolute).
    Default: benchmark/cases.example.toml

.PARAMETER Out
    Output directory for the JSON + MD report (relative to backend/).
    Default: benchmark/results

.PARAMETER Only
    Comma-separated case ids to run a subset (e.g. "qa-01-mha,rpt-01-transformer").

.PARAMETER Resume
    Path to a prior <name>.json result. Cases that already completed cleanly are
    carried over; only failed/missing cases are re-run, merged into one report.
    Useful after a transient network drop mid-sweep.

.EXAMPLE
    scripts/run-benchmark.ps1
    scripts/run-benchmark.ps1 -Config benchmark/my-cases.toml -Only qa-01-mha
    scripts/run-benchmark.ps1 -Resume benchmark/results/paperhub-rag-qa-20260526-165147.json
#>
param(
    [string]$Config = "benchmark/cases.example.toml",
    [string]$Out = "benchmark/results",
    [string]$Only = "",
    [string]$Resume = ""
)

$ErrorActionPreference = "Stop"
# This script lives in backend/scripts/ ; the runner runs from backend/.
$backend = Split-Path -Parent $PSScriptRoot

$configPath = $Config
if (-not [System.IO.Path]::IsPathRooted($configPath)) {
    $configPath = Join-Path $backend $Config
}
if (-not (Test-Path $configPath)) {
    Write-Error "Config not found: $configPath"
    exit 1
}

# Fail fast if the backend isn't reachable.
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 3
    Write-Host "Backend health: $($health.status)" -ForegroundColor Green
} catch {
    Write-Error "Backend not reachable at http://127.0.0.1:8000/health. Start it with scripts/start.ps1 first."
    exit 1
}

Push-Location $backend
try {
    $runnerArgs = @("run", "python", "-m", "benchmark.runner", "--config", $configPath, "--out", $Out)
    if ($Only) { $runnerArgs += @("--only", $Only) }
    if ($Resume) {
        $resumePath = $Resume
        if (-not [System.IO.Path]::IsPathRooted($resumePath)) {
            $resumePath = Join-Path $backend $Resume
        }
        $runnerArgs += @("--resume", $resumePath)
    }
    & uv @runnerArgs
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $code
