#!/usr/bin/env pwsh
# Smoke test for the Dockerized Marker extraction service (Plan F2 Task 7).
# GET :8002/health; if up, POST a sample PDF and assert blocks are non-empty.
# If the service is down, print the up command and exit 0 (not a failure).
$ErrorActionPreference = "Stop"
$base = "http://localhost:8002"

function Up { Write-Host "marker service not reachable on :8002 -- start it with:"; Write-Host "  docker compose up -d marker"; exit 0 }

try {
    $h = Invoke-RestMethod -Uri "$base/health" -Method Get -TimeoutSec 5
} catch { Up }

Write-Host "health: status=$($h.status) models_loaded=$($h.models_loaded)"

# Locate a sample PDF: prefer the one bundled with the service.
$pdf = Join-Path $PSScriptRoot "..\..\marker_service\sample.pdf"
if (-not (Test-Path $pdf)) {
    Write-Host "no sample.pdf found at $pdf -- skipping /extract assertion"
    exit 0
}

Write-Host "POST /extract ($pdf) -- this may take a minute on a real PDF..."
$form = @{ file = Get-Item $pdf }
$resp = Invoke-RestMethod -Uri "$base/extract" -Method Post -Form $form -TimeoutSec 600

$n = $resp.blocks.Count
Write-Host "extract returned $n blocks"
if ($n -lt 1) { Write-Error "expected >=1 block from /extract"; exit 1 }

$types = ($resp.blocks | ForEach-Object { $_.block_type } | Select-Object -Unique) -join ", "
Write-Host "block types present: $types"
Write-Host "smoke_marker OK"
