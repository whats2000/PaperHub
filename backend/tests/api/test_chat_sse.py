"""Tests for the POST /chat SSE endpoint.

Uses httpx.AsyncClient with ASGITransport for in-process testing without a
real port bind. The LLM adapter is overridden via FastAPI dependency_overrides
so no real API keys or network calls are made.

SSE frames are parsed line-by-line from the response body:
  data: <json>

Empty lines separate events (we skip them).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport

from paperhub.agents.research import AgentResponse, CitationRef
from paperhub.agents.router import BinaryRoutingDecision
from paperhub.data.vectors import ChromaVectorStore, ChunkVector
from paperhub.llm.adapter import FakeAdapter
from paperhub.rag.embedder import FakeEmbedder

# ---------------------------------------------------------------------------
# No-op LaunchedMcpSessions for test fixtures (prevents subprocess spawn)
# ---------------------------------------------------------------------------


class _NoOpMcpSessions:
    """Prevents the app lifespan from launching ``uvx arxiv-mcp-server`` in tests."""

    def __init__(self, settings: Any) -> None:
        pass

    async def __aenter__(self) -> _NoOpMcpSessions:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        pass

    def make_dispatcher(self) -> None:
        return None


def _fake_embed(text: str) -> list[float]:
    h = hash(text) % FakeEmbedder.DIM
    vec = [0.01] * FakeEmbedder.DIM
    vec[h] = 1.0
    return vec


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture()
def seeded_store(workspace: Path) -> ChromaVectorStore:
    """A ChromaVectorStore seeded with one paper chunk."""
    store = ChromaVectorStore(workspace / "chroma")
    paper_id = uuid4()
    chunk_id = uuid4()
    question = "What is X?"
    store.add(
        [
            ChunkVector(
                chunk_id=chunk_id,
                paper_id=paper_id,
                embedding=_fake_embed(question),
                metadata={"text": "X is a novel deep-learning architecture."},
            )
        ]
    )
    return store


@pytest.fixture()
def fake_adapter() -> FakeAdapter:
    return FakeAdapter(
        {
            "router": BinaryRoutingDecision(
                intent="paper_qa",
                confidence=0.95,
                model_tier="small",
                reasoning="looks like a paper QA query",
            ),
            "research_qa": AgentResponse(
                answer="X is a novel architecture.",
                citations=[
                    CitationRef(chunk_id=uuid4(), section="intro", page=1),
                ],
            ),
        }
    )


def _make_app_with_overrides(
    workspace: Path,
    fake_adapter: FakeAdapter,
    seeded_store: ChromaVectorStore,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> Any:
    """Create a FastAPI app with dependency overrides for the LLM adapter and retriever.

    Also applies migrations so tables exist before tests hit the endpoint.
    Env vars (PAPERHUB_WORKSPACE_ROOT, PAPERHUB_DB_PATH) must be set by the
    caller (e.g. via monkeypatch.setenv) BEFORE calling this helper.

    ``monkeypatch`` (required): patches ``LaunchedMcpSessions`` to a no-op so
    the app lifespan does NOT attempt to spawn ``uvx arxiv-mcp-server``
    (which takes ~90s and is not needed for chat-endpoint unit tests).
    The patch persists for the test's duration and is reset automatically.
    """
    import paperhub.api.app as _app_module
    from paperhub.api.app import create_app
    from paperhub.api.routes.chat import get_adapter, get_retriever
    from paperhub.data.db import apply_migrations
    from paperhub.rag.retriever import Retriever

    db_path = workspace / "paperhub.db"

    # Apply migrations so tables exist (normally done by app lifespan)
    apply_migrations(db_path)

    # Patch at module level so the lifespan (which executes during AsyncClient
    # startup, outside any with-block) sees the no-op class.
    if monkeypatch is not None:
        monkeypatch.setattr(_app_module, "LaunchedMcpSessions", _NoOpMcpSessions)

    app = create_app()

    # Inject the retriever backed by seeded_store + FakeEmbedder
    seeded_retriever = Retriever(seeded_store, FakeEmbedder())
    app.dependency_overrides[get_adapter] = lambda: fake_adapter
    app.dependency_overrides[get_retriever] = lambda: seeded_retriever

    return app


def _parse_sse_events(lines: list[str]) -> list[dict[str, Any]]:
    """Parse SSE data lines into a list of JSON dicts."""
    events: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if line.startswith("data:"):
            payload = line[len("data:") :].strip()
            if payload:
                events.append(json.loads(payload))
    return events


@pytest.mark.asyncio
async def test_chat_sse_paper_qa_event_sequence(
    workspace: Path,
    seeded_store: ChromaVectorStore,
    fake_adapter: FakeAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /chat with a paper_qa intent must emit events in order:
    routing_decision → tool_step → token → final.
    """
    monkeypatch.setenv("PAPERHUB_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("PAPERHUB_DB_PATH", str(workspace / "paperhub.db"))

    app = _make_app_with_overrides(workspace, fake_adapter, seeded_store, monkeypatch)

    lines: list[str] = []
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        async with ac.stream(
            "POST",
            "/chat",
            json={"message": "What is X?", "session_id": None},
        ) as r:
            assert r.status_code == 200
            async for line in r.aiter_lines():
                lines.append(line)

    events = _parse_sse_events(lines)
    types = [e["type"] for e in events]

    assert "routing_decision" in types, f"missing routing_decision in {types}"
    assert "tool_step" in types, f"missing tool_step in {types}"
    assert "token" in types, f"missing token in {types}"
    assert "final" in types, f"missing final in {types}"

    # Order: routing_decision must come before tool_step, token, final
    rd_idx = types.index("routing_decision")
    ts_idx = types.index("tool_step")
    tok_idx = types.index("token")
    fin_idx = types.index("final")

    assert rd_idx < ts_idx, "routing_decision must precede tool_step"
    assert ts_idx < tok_idx, "tool_step must precede token"
    assert tok_idx < fin_idx, "token must precede final"


@pytest.mark.asyncio
async def test_chat_sse_routing_decision_has_paper_qa(
    workspace: Path,
    seeded_store: ChromaVectorStore,
    fake_adapter: FakeAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The routing_decision event must carry intent='paper_qa'."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("PAPERHUB_DB_PATH", str(workspace / "paperhub.db"))

    app = _make_app_with_overrides(workspace, fake_adapter, seeded_store, monkeypatch)

    lines: list[str] = []
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        async with ac.stream(
            "POST",
            "/chat",
            json={"message": "What is X?", "session_id": None},
        ) as r:
            async for line in r.aiter_lines():
                lines.append(line)

    events = _parse_sse_events(lines)
    rd_events = [e for e in events if e["type"] == "routing_decision"]
    assert len(rd_events) == 1
    assert rd_events[0]["data"]["intent"] == "paper_qa"


@pytest.mark.asyncio
async def test_chat_sse_final_event_contains_answer(
    workspace: Path,
    seeded_store: ChromaVectorStore,
    fake_adapter: FakeAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final event must contain the agent's answer."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("PAPERHUB_DB_PATH", str(workspace / "paperhub.db"))

    app = _make_app_with_overrides(workspace, fake_adapter, seeded_store, monkeypatch)

    lines: list[str] = []
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        async with ac.stream(
            "POST",
            "/chat",
            json={"message": "What is X?", "session_id": None},
        ) as r:
            async for line in r.aiter_lines():
                lines.append(line)

    events = _parse_sse_events(lines)
    final_events = [e for e in events if e["type"] == "final"]
    assert len(final_events) == 1
    assert "X is a novel architecture." in final_events[0]["answer"]


@pytest.mark.asyncio
async def test_chat_sse_chitchat_returns_final_only(
    workspace: Path,
    seeded_store: ChromaVectorStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A chitchat intent should return routing_decision + final, no tool_step."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("PAPERHUB_DB_PATH", str(workspace / "paperhub.db"))

    chitchat_adapter = FakeAdapter(
        {
            "router": BinaryRoutingDecision(
                intent="chitchat",
                confidence=0.99,
                model_tier="small",
                reasoning="small talk",
            ),
        }
    )
    app = _make_app_with_overrides(workspace, chitchat_adapter, seeded_store, monkeypatch)

    lines: list[str] = []
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        async with ac.stream(
            "POST",
            "/chat",
            json={"message": "Hello!", "session_id": None},
        ) as r:
            async for line in r.aiter_lines():
                lines.append(line)

    events = _parse_sse_events(lines)
    types = [e["type"] for e in events]

    assert "routing_decision" in types
    assert "final" in types
    assert "tool_step" not in types, "chitchat should not emit tool_step"
    assert "token" not in types, "chitchat should not emit token"

    final_events = [e for e in events if e["type"] == "final"]
    assert "PaperHub" in final_events[0]["answer"]


# ---------------------------------------------------------------------------
# D5: User message persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_sse_persists_user_and_assistant_messages(
    workspace: Path,
    seeded_store: ChromaVectorStore,
    fake_adapter: FakeAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D5: After a paper_qa chat, the messages table must contain both a
    role='user' AND a role='assistant' row, both with the same run_id.
    """
    import sqlite3 as _sqlite3

    monkeypatch.setenv("PAPERHUB_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("PAPERHUB_DB_PATH", str(workspace / "paperhub.db"))

    app = _make_app_with_overrides(workspace, fake_adapter, seeded_store, monkeypatch)
    db_path = workspace / "paperhub.db"

    session_id = str(uuid4())
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        async with ac.stream(
            "POST",
            "/chat",
            json={"message": "What is X?", "session_id": session_id},
        ) as r:
            assert r.status_code == 200
            async for _ in r.aiter_lines():
                pass  # consume the stream

    # Check messages table
    conn = _sqlite3.connect(db_path)
    conn.row_factory = _sqlite3.Row
    rows = conn.execute(
        "SELECT role, content, run_id FROM messages WHERE session_id=? ORDER BY created_at",
        (session_id,),
    ).fetchall()
    conn.close()

    roles = [r["role"] for r in rows]
    assert "user" in roles, f"Expected 'user' message in DB, got roles: {roles}"
    assert "assistant" in roles, f"Expected 'assistant' message in DB, got roles: {roles}"

    # Both messages must share the same run_id
    run_ids = {r["run_id"] for r in rows}
    assert len(run_ids) == 1, f"Expected all messages to share a run_id, got: {run_ids}"

    # User message content must match the original request
    user_rows = [r for r in rows if r["role"] == "user"]
    assert user_rows[0]["content"] == "What is X?"


# ---------------------------------------------------------------------------
# D4: Client disconnect / CancelledError → run finalized as 'failed'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_sse_cancelled_error_finalizes_run(
    workspace: Path,
    seeded_store: ChromaVectorStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D4: If the SSE generator is cancelled (simulated client disconnect), the
    runs table must NOT be left in status='running'.
    """
    import asyncio as _asyncio
    import sqlite3 as _sqlite3

    from paperhub.agents.router import BinaryRoutingDecision
    from paperhub.llm.adapter import FakeAdapter as _FakeAdapter

    monkeypatch.setenv("PAPERHUB_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("PAPERHUB_DB_PATH", str(workspace / "paperhub.db"))

    db_path = workspace / "paperhub.db"

    # An adapter that raises CancelledError during the LLM call
    class _CancellingAdapter(_FakeAdapter):
        async def complete(self, *args: object, **kwargs: object) -> object:
            raise _asyncio.CancelledError("simulated client disconnect")

    cancelling_adapter = _CancellingAdapter(
        {
            "router": BinaryRoutingDecision(
                intent="paper_qa",
                confidence=0.95,
                model_tier="small",
                reasoning="qa",
            ),
        }
    )

    app = _make_app_with_overrides(workspace, cancelling_adapter, seeded_store, monkeypatch)

    # The CancelledError will propagate out — we catch it here at the test level
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            async with ac.stream(
                "POST",
                "/chat",
                json={"message": "What is X?", "session_id": None},
            ) as r:
                assert r.status_code == 200
                async for _ in r.aiter_lines():
                    pass
    except Exception:
        pass  # CancelledError or SSE error — expected

    # After the stream ends (for any reason), no run should be stuck in 'running'
    conn = _sqlite3.connect(db_path)
    conn.row_factory = _sqlite3.Row
    rows = conn.execute("SELECT id, status FROM runs").fetchall()
    conn.close()

    running_runs = [r for r in rows if r["status"] == "running"]
    assert not running_runs, (
        f"Expected no runs stuck in 'running', but found: {[dict(r) for r in running_runs]}"
    )
