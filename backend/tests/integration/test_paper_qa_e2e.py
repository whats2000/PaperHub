"""End-to-end smoke test for the Phase A paper_qa vertical slice.

This test exercises the real stack against the live arXiv API, a local
GROBID instance, and a real LiteLLM-backed Anthropic call. It is SKIPPED
by default; run via:

    cd backend; uv run pytest -m e2e -v

Required environment:
- ANTHROPIC_API_KEY     — set in your shell or .env
- PAPERHUB_GROBID_URL   — default http://localhost:8070 (override if needed)
- network access to arxiv.org and api.anthropic.com
- uvx and `arxiv-mcp-server` installed (auto-fetched via uvx on first call)
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_paper_qa_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Import an arXiv paper, ask a question, verify a cited answer."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — skipping e2e")

    monkeypatch.setenv("PAPERHUB_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_DB_PATH", str(tmp_path / "paperhub.db"))

    from paperhub.api.app import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://e2e") as ac:
        # 1. Import a real arXiv paper
        import_response = await ac.post(
            "/papers/import",
            json={"arxiv_id": "2401.00001"},
            timeout=120.0,
        )
        assert import_response.status_code == 200, (
            f"import failed: {import_response.status_code} {import_response.text}"
        )
        paper = import_response.json()
        assert paper["arxiv_id"] == "2401.00001"
        assert paper["title"]

        # 2. Ask a question about the imported paper
        sse_events: list[dict[str, object]] = []
        async with ac.stream(
            "POST",
            "/chat",
            json={"message": "What problem does this paper address?", "session_id": None},
            timeout=120.0,
        ) as response:
            assert response.status_code == 200
            buffer = ""
            async for chunk in response.aiter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    frame, buffer = buffer.split("\n\n", 1)
                    for line in frame.splitlines():
                        if line.startswith("data: "):
                            sse_events.append(json.loads(line[len("data: ") :]))

        # 3. Verify the SSE event sequence
        event_types = [e.get("type") for e in sse_events]
        assert "routing_decision" in event_types, f"missing routing_decision in {event_types}"
        assert "final" in event_types, f"missing final in {event_types}"

        # 4. Verify the final answer contains an inline citation marker
        final_events = [e for e in sse_events if e.get("type") == "final"]
        assert final_events, "no final event"
        final_event = final_events[-1]
        answer_text = final_event.get("answer")
        assert isinstance(answer_text, str) and answer_text, (
            f"missing or empty answer in final event: {final_event}"
        )
        citation_pattern = re.compile(r"\(§[^)]+,\s*p\.\d+\)")
        assert citation_pattern.search(answer_text), (
            f"answer missing (§sec, p.N) citation marker: {answer_text[:200]}"
        )
