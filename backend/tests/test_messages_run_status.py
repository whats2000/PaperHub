"""Tests for run_status in GET /sessions/{id}/messages (FR-15, Task A6).

Each message in the response includes the status of the run that produced it:
- 'running' for an in-progress run
- 'ok' for a successfully completed run
- None for messages with no linked run row (legacy / orphan)
"""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def msg_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    """ASGI test client with a bootstrapped tmp_path DB."""
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


async def test_run_status_running(msg_client: AsyncClient, tmp_path: Path) -> None:
    """A message whose run is still 'running' → run_status == 'running'."""
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("INSERT INTO chat_sessions (id) VALUES (1)")
        await conn.execute(
            "INSERT INTO runs (id, session_id, status) VALUES (1, 1, 'running')",
        )
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) "
            "VALUES (1, 'user', 'hello', 1)",
        )
        await conn.commit()

    resp = await msg_client.get("/sessions/1/messages")
    assert resp.status_code == 200
    msgs = resp.json()
    assert len(msgs) == 1
    assert msgs[0]["run_status"] == "running"


async def test_run_status_ok(msg_client: AsyncClient, tmp_path: Path) -> None:
    """A message whose run completed as 'ok' → run_status == 'ok'."""
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("INSERT INTO chat_sessions (id) VALUES (1)")
        await conn.execute(
            "INSERT INTO runs (id, session_id, status) VALUES (1, 1, 'ok')",
        )
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) "
            "VALUES (1, 'assistant', 'hi there', 1)",
        )
        await conn.commit()

    resp = await msg_client.get("/sessions/1/messages")
    assert resp.status_code == 200
    msgs = resp.json()
    assert len(msgs) == 1
    assert msgs[0]["run_status"] == "ok"


async def test_run_status_none_when_no_run(
    msg_client: AsyncClient, tmp_path: Path
) -> None:
    """A message with no linked run row (run_id NULL) → run_status is None."""
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("INSERT INTO chat_sessions (id) VALUES (1)")
        await conn.execute(
            "INSERT INTO messages (session_id, role, content) "
            "VALUES (1, 'user', 'orphan message')",
        )
        await conn.commit()

    resp = await msg_client.get("/sessions/1/messages")
    assert resp.status_code == 200
    msgs = resp.json()
    assert len(msgs) == 1
    assert msgs[0]["run_status"] is None
