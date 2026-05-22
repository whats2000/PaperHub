import aiosqlite
import pytest

from paperhub.mcp.memory_server import _add_handler, _edit_handler, _forget_handler, _recall_handler
from paperhub.mcp.server_context import (
    PaperhubPapersRequestContext,
    reset_request_context,
    set_request_context,
)
from paperhub.tracing.tracer import Tracer


@pytest.fixture
async def mem_ctx(migrated_db: aiosqlite.Connection):
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")  # 1
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")  # 2
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    ctx = PaperhubPapersRequestContext(
        conn=migrated_db, session_id=2, run_id=1, tracer=tracer, caller_supplied_run=True,
    )
    token = set_request_context(ctx)
    try:
        yield ctx
    finally:
        reset_request_context(token)


@pytest.mark.asyncio
async def test_add_then_recall(mem_ctx) -> None:
    out = await _add_handler(content="prefers concise answers", scope="session")
    assert out["id"]
    hits = await _recall_handler(query="concise", scope="both")
    assert any("concise" in h["content"] for h in hits)


@pytest.mark.asyncio
async def test_edit_other_session_returns_rejected(mem_ctx) -> None:
    await mem_ctx.conn.execute(
        "INSERT INTO memories (scope, session_id, content) VALUES ('session', 1, 'owned by 1')"
    )
    await mem_ctx.conn.commit()
    async with mem_ctx.conn.execute("SELECT last_insert_rowid()") as cur:
        mid = (await cur.fetchone())[0]
    out = await _edit_handler(memory_id=int(mid), content="hijack")
    assert out["error"] == "rejected"


@pytest.mark.asyncio
async def test_forget_other_session_returns_rejected(mem_ctx) -> None:
    await mem_ctx.conn.execute(
        "INSERT INTO memories (scope, session_id, content) VALUES ('session', 1, 'owned by 1')"
    )
    await mem_ctx.conn.commit()
    async with mem_ctx.conn.execute("SELECT last_insert_rowid()") as cur:
        mid = (await cur.fetchone())[0]
    out = await _forget_handler(memory_id=int(mid))
    assert out["error"] == "rejected"


@pytest.mark.asyncio
async def test_forget_global_ok_via_handler(mem_ctx) -> None:
    out_add = await _add_handler(content="global note via handler", scope="global")
    out_forget = await _forget_handler(memory_id=int(out_add["id"]))
    assert out_forget.get("ok") is True
