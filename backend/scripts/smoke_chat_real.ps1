# Run the backend end-to-end against a REAL LLM (no router/chitchat mocks).
# Loads backend/.env, starts uvicorn, posts to /chat, then replays from SQLite.
$ErrorActionPreference = "Stop"

$backendDir = Resolve-Path (Join-Path $PSScriptRoot "..")
$envFile = Join-Path $backendDir ".env"
if (-not (Test-Path $envFile)) {
    throw "Missing $envFile. Copy backend/.env.example to backend/.env and fill in your API key."
}

# Parse KEY=VALUE lines from .env into the current process environment.
Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { return }
    $idx = $line.IndexOf("=")
    if ($idx -lt 1) { return }
    $key = $line.Substring(0, $idx).Trim()
    $val = $line.Substring($idx + 1).Trim()
    if (($val.StartsWith('"') -and $val.EndsWith('"')) -or
        ($val.StartsWith("'") -and $val.EndsWith("'"))) {
        $val = $val.Substring(1, $val.Length - 2)
    }
    if ($val -ne "") {
        Set-Item -Path "env:$key" -Value $val
    }
}

# Real-flow guard: make sure we have *some* provider key for the chosen models.
$routerModel = if ($env:PAPERHUB_ROUTER_MODEL) { $env:PAPERHUB_ROUTER_MODEL } else { "gemini/gemini-2.5-flash" }
$chitchatModel = if ($env:PAPERHUB_CHITCHAT_MODEL) { $env:PAPERHUB_CHITCHAT_MODEL } else { "gemini/gemini-2.5-flash" }
$needsKey = @{
    "gemini" = "GEMINI_API_KEY"
    "openai" = "OPENAI_API_KEY"
    "anthropic" = "ANTHROPIC_API_KEY"
}
foreach ($model in @($routerModel, $chitchatModel)) {
    $provider = ($model -split "/", 2)[0]
    $keyName = $needsKey[$provider]
    if ($keyName -and -not (Get-Item -Path "env:$keyName" -ErrorAction SilentlyContinue).Value) {
        throw "Model '$model' requires env var '$keyName'. Set it in backend/.env."
    }
}

# Isolated workspace so this run doesn't share state with the mocked smoke script.
$env:PAPERHUB_WORKSPACE = Join-Path $backendDir "workspace_smoke_real"
if (Test-Path $env:PAPERHUB_WORKSPACE) {
    Remove-Item -Recurse -Force $env:PAPERHUB_WORKSPACE
}

# Explicitly clear the mock vars from the mocked smoke script, in case they leaked into the shell.
Remove-Item Env:PAPERHUB_ROUTER_MOCK -ErrorAction SilentlyContinue
Remove-Item Env:PAPERHUB_CHITCHAT_MOCK -ErrorAction SilentlyContinue

# Pre-flight: port must be free so the /health probe can't succeed against an orphan from a prior run.
$portInUse = $false
try {
    $tcp = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 8766)
    $tcp.Start(); $tcp.Stop()
} catch { $portInUse = $true }
if ($portInUse) {
    throw "Port 8766 already in use. Kill the orphan uvicorn process (taskkill /F /IM python.exe or similar) and retry."
}

$server = Start-Process -PassThru -NoNewWindow uv -ArgumentList @(
    "run", "uvicorn", "paperhub.app:app", "--host", "127.0.0.1", "--port", "8766"
)
try {
    for ($i = 0; $i -lt 50; $i++) {
        try {
            Invoke-RestMethod http://127.0.0.1:8766/health -ErrorAction Stop | Out-Null
            break
        } catch {
            Start-Sleep -Milliseconds 200
        }
    }
    Write-Host "Server up. Issuing REAL chat request (router=$routerModel, chitchat=$chitchatModel)..."
    $userMessage = if ($args.Count -gt 0) { $args -join " " } else { "hello, what can you help me with?" }
    $body = @{ user_message = $userMessage } | ConvertTo-Json -Compress
    $tmpBody = Join-Path $env:TEMP "smoke_body_real.json"
    [System.IO.File]::WriteAllText($tmpBody, $body)
    curl.exe -N -s -X POST http://127.0.0.1:8766/chat `
        -H "Content-Type: application/json" `
        --data-binary "@$tmpBody"
    Write-Host "`n--- Replay ---"
    uv run paperhub-replay --run-id 1
} finally {
    # uv spawns a python child holding the listening socket; kill the whole tree, not just the launcher.
    & taskkill.exe /F /T /PID $server.Id 2>&1 | Out-Null
}
