# Smoke test: hit the externally-running `open-websearch serve` daemon via
# MCPClient. Verifies the daemon is reachable, advertises `search`, and a
# single `search` call returns at least one hit.
#
# Gated on the daemon being up at http://localhost:3000. Operator runs:
#   npm install -g open-websearch
#   open-websearch serve     # long-lived local daemon on :3000
# in a separate shell, then runs this script. If the daemon is down the
# script exits non-zero with a clear "start the daemon" message.
#
# Unlike smoke_mcp_papers.ps1 this script CAN use MCPClient directly —
# open-websearch's MCP surface does not require any custom request
# headers, so the SDK's `streamablehttp_client(url)` works as-is.
#
# Usage: .\scripts\smoke_mcp_web.ps1
$ErrorActionPreference = "Stop"

$backendDir = Resolve-Path (Join-Path $PSScriptRoot "..")

# ── 1. Pre-flight: is the open-websearch daemon up? ───────────────────────────
$daemonUrl = "http://localhost:3000"
$daemonUp = $false
try {
    $resp = Invoke-WebRequest -Uri "$daemonUrl/" -Method Get -TimeoutSec 3 -ErrorAction Stop
    if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500) {
        $daemonUp = $true
    }
} catch {
    # Some open-websearch builds don't expose `/` — fall back to a TCP probe.
    try {
        $tcp = [System.Net.Sockets.TcpClient]::new()
        $tcp.ConnectAsync("localhost", 3000).Wait(2000) | Out-Null
        if ($tcp.Connected) {
            $daemonUp = $true
            $tcp.Close()
        }
    } catch { }
}

if (-not $daemonUp) {
    Write-Host "SKIP: open-websearch daemon not reachable at $daemonUrl." -ForegroundColor Yellow
    Write-Host "Start it in a separate shell with:" -ForegroundColor Yellow
    Write-Host "    npm install -g open-websearch" -ForegroundColor Yellow
    Write-Host "    open-websearch serve" -ForegroundColor Yellow
    Write-Host "and re-run this script. CI environments without the daemon skip this test." -ForegroundColor Yellow
    exit 1
}

Write-Host "open-websearch daemon reachable at $daemonUrl."

# ── 2. Run the MCP smoke via MCPClient (inline Python) ────────────────────────
Write-Host "`nConnecting to web MCP at $daemonUrl/mcp via MCPClient ..."
& uv run --project $backendDir python -c @'
"""Connect to open-websearch's MCP surface via the project's MCPClient.

Steps:
  1. Build an MCPServerConfig pointing at http://localhost:3000/mcp.
  2. MCPClient.connect() — exercises the same streamable_http transport
     the registry uses in production.
  3. list_tools() — must include the namespaced `web.search` schema
     (upstream `search` after the registry namespacing rule).
  4. call_tool("search", {...}) — print the first 3 hits.
"""
from __future__ import annotations

import asyncio
import sys

from paperhub.mcp.client import MCPClient
from paperhub.mcp.config import MCPServerConfig


async def main() -> int:
    config = MCPServerConfig(
        name="web",
        transport="streamable_http",
        url="http://localhost:3000/mcp",
        expose=["search", "fetchWebContent"],
        aliases={"fetchWebContent": "fetch"},
        timeout_seconds=15.0,
    )
    client = MCPClient(config)
    try:
        await client.connect()
    except Exception as exc:
        print(
            f"FAIL: could not connect to open-websearch at {config.url}: {exc}",
            file=sys.stderr,
        )
        print(
            "Hint: is `open-websearch serve` running and accepting MCP "
            "streamable-HTTP requests on /mcp?",
            file=sys.stderr,
        )
        return 1

    try:
        tools = await client.list_tools()
        names = sorted(t["function"]["name"] for t in tools)
        print(f"  list_tools advertised: {names}")
        if "web.search" not in names:
            print(
                "FAIL: open-websearch did not advertise `web.search`. "
                "Upstream tool name may have changed — check the daemon version.",
                file=sys.stderr,
            )
            return 1

        # Call search. Upstream open-websearch uses `query` + `limit`; some
        # versions accept `max_results`. Send both — the upstream ignores
        # whichever it doesn't know.
        print("  calling web.search (query='denoising diffusion probabilistic models') ...")
        try:
            result = await client.call_tool(
                "search",
                {
                    "query": "denoising diffusion probabilistic models",
                    "limit": 3,
                },
            )
        except Exception as exc:
            # Engines occasionally rate-limit; surface clearly so the operator
            # can re-run rather than hunt down a generic stack trace.
            print(
                f"FAIL: web.search raised: {exc}. Engines may be rate-limited; "
                "wait a minute and retry.",
                file=sys.stderr,
            )
            return 1

        # MCPClient returns structuredContent when available, else joined text.
        # open-websearch returns structuredContent with a `result` list.
        if isinstance(result, dict) and "result" in result:
            hits = result["result"]
        elif isinstance(result, list):
            hits = result
        else:
            hits = [result] if result else []

        print(f"  web.search returned {len(hits)} hit(s).")
        for i, hit in enumerate(hits[:3], start=1):
            if isinstance(hit, dict):
                title = hit.get("title") or hit.get("name") or "<no title>"
                url = hit.get("url") or hit.get("link") or "<no url>"
                print(f"    [{i}] {title}\n        {url}")
            else:
                print(f"    [{i}] {str(hit)[:120]}")

        if len(hits) < 1:
            print(
                "FAIL: web.search returned 0 hits. Engines may be misconfigured.",
                file=sys.stderr,
            )
            return 1
    finally:
        await client.disconnect()

    print("MCP web smoke OK — connect ✓ list_tools ✓ web.search ✓")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
'@
if ($LASTEXITCODE -ne 0) { throw "MCP web smoke FAILED (see Python traceback above)." }

Write-Host "`nPASS: smoke_mcp_web complete." -ForegroundColor Green
