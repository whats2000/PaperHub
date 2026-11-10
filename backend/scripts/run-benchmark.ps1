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

.PARAMETER KeepSessions
    Keep the chat sessions the run created. By default every session the run
    created is deleted at the end, also when a case fails or the run is stopped.

.PARAMETER BaseUrl
    Backend to drive and health-check. Default: http://127.0.0.1:8000

.PARAMETER DbPath
    Workspace SQLite DB used to resolve cached papers. Default: the config's
    db_path (workspace/paperhub.db).

.PARAMETER Compare
    Baseline backend URL (e.g. v2.41 on http://127.0.0.1:8200). Every case also
    runs there; -BaseUrl is the candidate. With -Judge it writes pairwise
    verdicts (both orders) and the H4 merge gate (<name>.gate.json).

.PARAMETER CompareDbPath
    The baseline instance's workspace DB. Required with -Compare.

.EXAMPLE
    scripts/run-benchmark.ps1
    scripts/run-benchmark.ps1 -BaseUrl http://127.0.0.1:8100 -DbPath D:/paperhub-h4-workspace/paperhub.db
    scripts/run-benchmark.ps1 -Config benchmark/my-cases.toml -Only qa-01-mha
    scripts/run-benchmark.ps1 -Resume benchmark/results/paperhub-rag-qa-20260526-165147.json
    scripts/run-benchmark.ps1 -Config benchmark/cases.h4-scenarios.toml -Judge `
        -BaseUrl http://127.0.0.1:8100 -DbPath D:/paperhub-h4-workspace/paperhub.db `
        -Compare http://127.0.0.1:8200 -CompareDbPath D:/paperhub-v241-eval-workspace/paperhub.db
#>
param(
    [string]$Config = "benchmark/cases.example.toml",
    [string]$Out = "benchmark/results",
    [string]$Only = "",
    [string]$Resume = "",
    [switch]$Judge,
    [string]$JudgeModel = "",
    [switch]$KeepSessions,
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$DbPath = "",
    [string]$Compare = "",
    [string]$CompareDbPath = ""
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

if ($Compare -and -not $CompareDbPath) {
    Write-Error "-Compare needs -CompareDbPath (the baseline instance's workspace DB)."
    exit 1
}

# Fail fast if a backend isn't reachable.
$urls = @($BaseUrl)
if ($Compare) { $urls += $Compare }
foreach ($url in $urls) {
    try {
        $health = Invoke-RestMethod -Uri "$url/health" -TimeoutSec 3
        Write-Host "Backend health ($url): $($health.status)" -ForegroundColor Green
    } catch {
        Write-Error "Backend not reachable at $url/health. Start it with scripts/start.ps1 first."
        exit 1
    }
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
    if ($Judge) { $runnerArgs += "--judge" }
    if ($JudgeModel) { $runnerArgs += @("--judge-model", $JudgeModel) }
    if ($KeepSessions) { $runnerArgs += "--keep-sessions" }
    if ($BaseUrl -ne "http://127.0.0.1:8000") { $runnerArgs += @("--base-url", $BaseUrl) }
    if ($DbPath) { $runnerArgs += @("--db-path", $DbPath) }
    if ($Compare) { $runnerArgs += @("--compare", $Compare, "--compare-db-path", $CompareDbPath) }
    & uv @runnerArgs
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $code
