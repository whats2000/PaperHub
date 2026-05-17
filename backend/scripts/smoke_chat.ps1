# Run the backend end-to-end against the chitchat path with mocked LLM responses.
# Verifies: SSE round-trip works, schema is migrated, run is replayable from SQLite.
$ErrorActionPreference = "Stop"

$env:PAPERHUB_WORKSPACE = Join-Path $PSScriptRoot "..\workspace_smoke"
$env:PAPERHUB_ROUTER_MOCK = '{"intent":"chitchat","model_tier":"small","confidence":0.9,"reasoning":"greeting"}'
$env:PAPERHUB_CHITCHAT_MOCK = "Hi from PaperHub!"

if (Test-Path $env:PAPERHUB_WORKSPACE) {
    Remove-Item -Recurse -Force $env:PAPERHUB_WORKSPACE
}

$server = Start-Process -PassThru -NoNewWindow uv -ArgumentList @(
    "run", "uvicorn", "paperhub.app:app", "--host", "127.0.0.1", "--port", "8765"
)
try {
    # Wait for server to come up
    for ($i = 0; $i -lt 30; $i++) {
        try {
            Invoke-RestMethod http://127.0.0.1:8765/health -ErrorAction Stop | Out-Null
            break
        } catch {
            Start-Sleep -Milliseconds 200
        }
    }
    Write-Host "Server up. Issuing chat request..."
    # Write JSON body to a temp file to avoid PowerShell shell-quoting issues with curl
    $body = '{"user_message":"hello"}'
    $tmpBody = Join-Path $env:TEMP "smoke_body.json"
    [System.IO.File]::WriteAllText($tmpBody, $body)
    curl.exe -N -s -X POST http://127.0.0.1:8765/chat `
        -H "Content-Type: application/json" `
        --data-binary "@$tmpBody" | Tee-Object -Variable sseOutput
    Write-Host "`n--- Replay ---"
    uv run paperhub-replay --run-id 1
} finally {
    # uv spawns a python child holding the listening socket; kill the whole tree, not just the launcher.
    & taskkill.exe /F /T /PID $server.Id 2>&1 | Out-Null
}
