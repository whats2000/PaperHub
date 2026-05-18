# Smoke test: hit the in-process `paperhub-papers` FastMCP server mounted on
# the backend at /mcp. Verifies all three tools are advertised + that
# `papers.search_library` round-trips against the live workspace DB.
#
# Always runnable (no external dep). Boots a private uvicorn on :8770 with
# an isolated workspace, seeds one paper, calls the MCP tool, asserts the
# round-trip, kills the server.
#
# Why raw httpx (and not MCPClient)? The FastMCP middleware on /mcp requires
# the `X-Paperhub-Session-Id` request header to thread per-call context into
# the tool handlers (see paperhub.mcp.server.PaperhubPapersRequestContextMiddleware).
# `MCPClient` doesn't currently expose a way to set custom HTTP headers on
# the streamable_http transport, so we call the streamable-HTTP MCP wire
# protocol directly via httpx (initialize → tools/list → tools/call). This
# is the same pattern tests/mcp/test_server.py uses.
#
# Usage: .\scripts\smoke_mcp_papers.ps1
$ErrorActionPreference = "Stop"

# ── 1. Load backend/.env (no API key required for this script) ───────────────
$backendDir = Resolve-Path (Join-Path $PSScriptRoot "..")
$envFile = Join-Path $backendDir ".env"
if (Test-Path $envFile) {
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
        if ($val -ne "") { Set-Item -Path "env:$key" -Value $val }
    }
}

# ── 2. Isolated workspace ─────────────────────────────────────────────────────
$env:PAPERHUB_WORKSPACE = Join-Path $backendDir "workspace\smoke-mcp-papers"
if (Test-Path $env:PAPERHUB_WORKSPACE) {
    Remove-Item -Recurse -Force $env:PAPERHUB_WORKSPACE
}

# Clear any lingering mock vars.
Remove-Item Env:PAPERHUB_ROUTER_MOCK   -ErrorAction SilentlyContinue
Remove-Item Env:PAPERHUB_CHITCHAT_MOCK -ErrorAction SilentlyContinue

# ── 3. Pre-flight: port 8770 must be free ─────────────────────────────────────
$portInUse = $false
try {
    $tcp = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 8770)
    $tcp.Start(); $tcp.Stop()
} catch { $portInUse = $true }
if ($portInUse) {
    throw "Port 8770 already in use. Kill the orphan process and retry."
}

# ── 4. Boot uvicorn ───────────────────────────────────────────────────────────
$server = Start-Process -PassThru -NoNewWindow -WorkingDirectory $backendDir `
    uv -ArgumentList @("run", "uvicorn", "paperhub.app:app", "--host", "127.0.0.1", "--port", "8770")

try {
    # ── 5. Wait for /health (30 s) ────────────────────────────────────────────
    $healthy = $false
    for ($i = 0; $i -lt 150; $i++) {
        try {
            Invoke-RestMethod http://127.0.0.1:8770/health -ErrorAction Stop | Out-Null
            $healthy = $true
            break
        } catch {
            Start-Sleep -Milliseconds 200
        }
    }
    if (-not $healthy) {
        Write-Host "FAIL: backend did not become healthy on :8770 within 30 s." -ForegroundColor Red
        Write-Host "Start it manually with 'uv run uvicorn paperhub.app:app' and re-run." -ForegroundColor Yellow
        throw "backend unhealthy"
    }
    Write-Host "Server up on :8770."

    # ── 6. Seed: chat_sessions row 99 + one paper_content row ────────────────
    $dbPath = Join-Path $env:PAPERHUB_WORKSPACE "paperhub.db"
    for ($i = 0; $i -lt 20; $i++) {
        if (Test-Path $dbPath) { break }
        Start-Sleep -Milliseconds 200
    }
    & uv run --project $backendDir python -c @'
import sqlite3, sys
db = sys.argv[1]
conn = sqlite3.connect(db)
conn.execute("PRAGMA foreign_keys = ON")
conn.execute(
    "INSERT OR IGNORE INTO chat_sessions (id, title) VALUES (99, 'smoke-mcp-papers')"
)
# Seed one paper_content row + FTS row so search_library has something to find.
# The FTS5 trigger fires on INSERT into paper_content; explicit FTS insert is
# defensive in case the trigger schema changes.
conn.execute(
    "INSERT OR IGNORE INTO paper_content "
    "(id, content_key, arxiv_id, title, abstract, year, source_pdf_path) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)",
    (1001, "smoke-key-1", "smoke.0001",
     "Attention Is All You Need (smoke-test seed)",
     "We propose a new simple network architecture, the Transformer, "
     "based solely on attention mechanisms, dispensing with recurrence "
     "and convolutions entirely.",
     2017, "/tmp/seed.pdf"),
)
conn.commit()
conn.close()
'@ $dbPath
    if ($LASTEXITCODE -ne 0) { throw "Failed to seed DB rows." }

    # ── 7. Run the MCP wire-protocol smoke via inline Python + httpx ──────────
    Write-Host "`nRunning MCP wire-protocol smoke against http://127.0.0.1:8770/mcp ..."
    & uv run --project $backendDir python -c @'
"""Hit POST /mcp with the streamable-HTTP MCP wire protocol:
  1. `initialize`            (handshake)
  2. `notifications/initialized` (handshake complete)
  3. `tools/list`            (assert all three papers.* tools advertised)
  4. `tools/call search_library` (assert seeded row comes back)

Requires `X-Paperhub-Session-Id` on every POST so the FastMCP middleware
threads the per-call context into the tool handlers.
"""
from __future__ import annotations

import json
import sys

import httpx

URL = "http://127.0.0.1:8770/mcp/"  # trailing slash required by Starlette mount
SESSION_ID = "99"

EXPECTED_TOOLS = {"search_library", "search_semantic_scholar", "find_related_papers"}

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "X-Paperhub-Session-Id": SESSION_ID,
}


def _parse_streamable_response(resp: httpx.Response) -> dict:
    """The streamable-HTTP transport may reply as either application/json
    or text/event-stream. Decode whichever arrived."""
    ctype = resp.headers.get("content-type", "")
    if ctype.startswith("application/json"):
        return resp.json()
    if ctype.startswith("text/event-stream"):
        # SSE: find the first `data:` line, parse as JSON.
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
    raise RuntimeError(
        f"unexpected response content-type {ctype!r}; body={resp.text[:200]!r}"
    )


def _post(client: httpx.Client, payload: dict, *, extra_headers: dict | None = None) -> httpx.Response:
    hdr = dict(HEADERS)
    if extra_headers:
        hdr.update(extra_headers)
    resp = client.post(URL, headers=hdr, content=json.dumps(payload))
    if resp.status_code != 200 and resp.status_code != 202:
        raise RuntimeError(
            f"POST {URL} returned HTTP {resp.status_code}: {resp.text[:300]!r}"
        )
    return resp


def main() -> None:
    with httpx.Client(timeout=10.0) as client:
        # 1. initialize handshake.
        init_resp = _post(client, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "smoke_mcp_papers", "version": "0"},
            },
        })
        # The server returns its own session id in the response header — we
        # do NOT use it (FastMCP runs in stateless mode here; PaperHub's
        # X-Paperhub-Session-Id is the only session header the middleware
        # cares about). Capture the protocol version for the next call.
        mcp_session_hdr = init_resp.headers.get("mcp-session-id")
        proto_extra = {"MCP-Protocol-Version": "2025-06-18"}
        if mcp_session_hdr:
            proto_extra["Mcp-Session-Id"] = mcp_session_hdr

        # 2. initialized notification — fire-and-forget; spec requires it
        #    before any other request.
        try:
            _post(client, {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }, extra_headers=proto_extra)
        except RuntimeError as exc:
            # Some FastMCP versions return 202 with empty body; tolerate.
            print(f"  (initialized notification non-200 — tolerated: {exc})")

        # 3. tools/list.
        list_resp = _post(client, {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
        }, extra_headers=proto_extra)
        list_payload = _parse_streamable_response(list_resp)
        if "result" not in list_payload:
            raise RuntimeError(f"tools/list returned no result: {list_payload!r}")
        advertised = {t["name"] for t in list_payload["result"]["tools"]}
        print(f"  tools/list advertised: {sorted(advertised)}")
        missing = EXPECTED_TOOLS - advertised
        if missing:
            raise RuntimeError(
                f"tools/list missing expected tools: {sorted(missing)} "
                f"(got {sorted(advertised)})"
            )

        # 4. tools/call search_library.
        call_resp = _post(client, {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "search_library",
                "arguments": {"query": "transformer attention", "max_results": 5},
            },
        }, extra_headers=proto_extra)
        call_payload = _parse_streamable_response(call_resp)
        if "result" not in call_payload:
            raise RuntimeError(f"tools/call returned no result: {call_payload!r}")
        structured = call_payload["result"].get("structuredContent")
        if structured is None:
            raise RuntimeError(
                f"tools/call missing structuredContent: {call_payload['result']!r}"
            )
        hits = structured.get("result", [])
        if not isinstance(hits, list):
            raise RuntimeError(
                f"tools/call structured.result not a list: {structured!r}"
            )
        # The seed row is session 99-exclusive on a paper *not* in session 99's
        # `papers` table, so search_library should find it (FTS5 ranks by
        # match relevance; "transformer attention" is in the seeded abstract).
        seeded_ids = [int(h.get("paper_content_id", 0)) for h in hits]
        print(f"  tools/call search_library hits: {seeded_ids}")
        if 1001 not in seeded_ids:
            raise RuntimeError(
                f"seeded paper_content row (id=1001) not in hits: {hits!r}"
            )

    print("MCP wire-protocol smoke OK — initialize ✓ tools/list ✓ tools/call ✓")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
'@
    if ($LASTEXITCODE -ne 0) { throw "MCP papers smoke FAILED (see Python traceback above)." }

    Write-Host "`nPASS: smoke_mcp_papers complete." -ForegroundColor Green

} finally {
    & taskkill.exe /F /T /PID $server.Id 2>&1 | Out-Null
    if (Test-Path $env:PAPERHUB_WORKSPACE) {
        Remove-Item -Recurse -Force $env:PAPERHUB_WORKSPACE -ErrorAction SilentlyContinue
    }
}
