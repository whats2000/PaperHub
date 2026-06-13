"""E1 Task 2: the library_stats chat branch forwards the SQL Agent's
``SearchResultsYield`` as a ``search_results`` SSE event (mirroring
paper_search) and persists ``runs.search_results_json`` for the run.
"""
import json
from collections.abc import AsyncIterator
from typing import Any

import aiosqlite
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from paperhub.agents.research import SearchCandidate, SearchResultsYield
from paperhub.app import create_app
from paperhub.config import load_settings
from paperhub.db.migrate import apply_schema


class _FakeMcpRegistry:
    async def aggregate_tool_schemas(self) -> list[Any]:
        return []

    async def has_tool(self, name: str) -> bool:
        return False

    async def call(self, name: str, args: dict[str, Any]) -> Any:  # pragma: no cover
        raise RuntimeError("unexpected registry call in library_stats card test")


def _wire_test_app() -> FastAPI:
    app = create_app()
    app.state.mcp_registry = _FakeMcpRegistry()
    return app


async def _bootstrap_schema() -> None:
    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn:
        await apply_schema(conn)


async def _consume_sse(stream: AsyncIterator[bytes]) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    buf = ""
    async for chunk in stream:
        buf += chunk.decode("utf-8").replace("\r\n", "\n")
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            event_type = ""
            data = ""
            for line in block.splitlines():
                if line.startswith("event: "):
                    event_type = line[len("event: "):]
                elif line.startswith("data: "):
                    data = line[len("data: "):]
            if event_type:
                events.append((event_type, json.loads(data) if data else {}))
    return events


def _candidate(paper_id: str, *, title: str = "T") -> SearchCandidate:
    return SearchCandidate(
        paper_id=paper_id,
        title=title,
        authors=[],
        year=2024,
        abstract="abs",
        arxiv_id=None,
        has_open_pdf=False,
        reason="from library",
        finalize=False,
    )


async def test_library_stats_forwards_search_results_event_and_persists(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    """A library_stats turn whose SQL agent yields a SearchResultsYield must:
    - emit a search_results SSE event carrying the candidates, and
    - persist runs.search_results_json for that run,
    while still streaming the answer tokens + a final event."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"library_stats","model_tier":"small","confidence":0.95,'
        '"reasoning":"list papers"}',
    )
    await _bootstrap_schema()

    # Seed a paper_content row so the library:<id> candidate is resolvable
    # (and not flagged no_ingestible_source on any incidental attach path).
    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn:
        await conn.execute(
            "INSERT INTO paper_content "
            "(content_key, kind, arxiv_id, title, authors_json, year, abstract, "
            "source_path, source_dir_path, html_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "arxiv:1706.03762", "arxiv", "1706.03762",
                "Attention Is All You Need", "[]", 2017,
                "abs", "/tmp/s.tex", "/tmp", "/tmp/s.html",
            ),
        )
        await conn.commit()
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            row = await cur.fetchone()
        assert row is not None
        pcid = int(row[0])

    _tokens = ["You have ", "1 paper."]

    async def _fake_sql_agent_stream(
        state: Any,
        *,
        adapter: Any,
        tracer: Any,
        registry: Any,
        planner_model: Any,
        answer_model: Any,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        yield SearchResultsYield(
            candidates=[_candidate(f"library:{pcid}", title="Attention")],
        )
        for tok in _tokens:
            yield tok

    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "sql_agent_stream", _fake_sql_agent_stream)

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
    ) as client, client.stream(
        "POST", "/chat",
        json={"session_id": None, "user_message": "list my papers"},
    ) as response:
        assert response.status_code == 200
        events = await _consume_sse(response.aiter_bytes())

    types = [t for t, _ in events]
    assert "search_results" in types, (
        f"Expected a search_results event from library_stats, got: {types}"
    )

    sr_idx = types.index("search_results")
    sr_payload = events[sr_idx][1]
    assert sr_payload["run_id"] > 0
    assert len(sr_payload["candidates"]) == 1
    assert sr_payload["candidates"][0]["paper_id"] == f"library:{pcid}"

    # Tokens + final must still flow (the card must NOT become answer text).
    assert "token" in types
    final_payload = next(d for t, d in events if t == "final")
    assert final_payload["content"] == "You have 1 paper."
    assert "library:" not in final_payload["content"]

    run_id = sr_payload["run_id"]

    # runs.search_results_json must be persisted for the run.
    async with aiosqlite.connect(settings.db_path) as conn, conn.execute(
        "SELECT search_results_json FROM runs WHERE id = ?", (run_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] is not None, "Expected runs.search_results_json to be persisted"
    persisted = json.loads(row[0])
    assert len(persisted) == 1
    assert persisted[0]["paper_id"] == f"library:{pcid}"
