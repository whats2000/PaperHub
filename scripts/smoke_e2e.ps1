# Boot backend (mocked LLM) + Vite dev server. Operator manually drives the browser.
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot "backend"
$frontendDir = Join-Path $repoRoot "frontend"

$env:PAPERHUB_WORKSPACE = Join-Path $backendDir "workspace_smoke_e2e"
$env:PAPERHUB_ROUTER_MOCK = '{"intent":"chitchat","model_tier":"small","confidence":0.9,"reasoning":"e2e smoke"}'
$env:PAPERHUB_CHITCHAT_MOCK = "Hi from PaperHub! (e2e smoke)"

if (Test-Path $env:PAPERHUB_WORKSPACE) {
    Remove-Item -Recurse -Force $env:PAPERHUB_WORKSPACE
}

Push-Location $backendDir
$backend = Start-Process -PassThru -NoNewWindow uv -ArgumentList @(
    "run", "uvicorn", "paperhub.app:app", "--host", "127.0.0.1", "--port", "8000"
)
Pop-Location

Push-Location $frontendDir
$frontend = Start-Process -PassThru -NoNewWindow npm -ArgumentList @("run", "dev")
Pop-Location

try {
    Write-Host "`nBackend: http://127.0.0.1:8000 (PID $($backend.Id))"
    Write-Host "Frontend: http://127.0.0.1:5173 (PID $($frontend.Id))"
    Write-Host "`nDrive the browser: type 'hello' and Ctrl+Enter."
    Write-Host "Expected: routing badge says 'Chitchat 90% · small', trace shows 2 steps,"
    Write-Host "assistant message is 'Hi from PaperHub! (e2e smoke)'."
    Write-Host "`nCtrl+C to stop both processes." -ForegroundColor Yellow
    Wait-Event
} finally {
    # taskkill /F /T so uv + npm child processes (uvicorn + vite) get killed
    & taskkill.exe /F /T /PID $backend.Id 2>&1 | Out-Null
    & taskkill.exe /F /T /PID $frontend.Id 2>&1 | Out-Null
}
