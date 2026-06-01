import aiosqlite
import pytest

from paperhub.agents.style_commands import (
    classify_style_command,
    handle_style_command,
)
from paperhub.agents.style_resolver import set_session_override
from paperhub.db.migrate import apply_schema


@pytest.fixture
async def conn(tmp_path):
    db = tmp_path / "test.db"
    async with aiosqlite.connect(str(db)) as c:
        await c.executescript(
            """
            CREATE TABLE chat_sessions (id INTEGER PRIMARY KEY, created_at TEXT, title TEXT);
            CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT CHECK (scope IN ('session','global')), session_id INTEGER,
                content TEXT, created_at TEXT, updated_at TEXT, status TEXT DEFAULT 'active',
                supersedes INTEGER, superseded_by INTEGER, metadata TEXT);
            """
        )
        await c.commit()
        await apply_schema(c)
        await c.execute("INSERT INTO chat_sessions (id, created_at, title) VALUES (1, datetime('now'), 't')")
        await c.commit()
        yield c


@pytest.mark.parametrize("text,expected", [
    ("reset slide style", "reset_style"),
    ("Reset slide style please", "reset_style"),
    ("重置投影片樣式", "reset_style"),
    ("remember this style for all future chats", "promote_to_global"),
    ("remember this slide style globally", "promote_to_global"),
    ("Generate slides for the papers", None),
    ("make the slides dark", None),                 # creative — let the agent handle it
    ("edit slide 3", None),
])
def test_classify_style_command(text, expected):
    assert classify_style_command(text) == expected


@pytest.mark.asyncio
async def test_handle_reset_drops_session_override(conn):
    await set_session_override(
        session_id=1, preamble_tex=r"\usetheme{Madrid}",
        source="user_request", conn=conn,
    )
    reply = await handle_style_command(
        action="reset_style", session_id=1, conn=conn,
    )
    assert "reset" in reply.lower()
    async with conn.execute(
        "SELECT COUNT(*) FROM slide_style_overrides WHERE session_id=1"
    ) as cur:
        assert (await cur.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_handle_reset_no_op_when_no_override(conn):
    reply = await handle_style_command(
        action="reset_style", session_id=1, conn=conn,
    )
    assert "no custom" in reply.lower() or "already" in reply.lower() or "default" in reply.lower()


@pytest.mark.asyncio
async def test_handle_promote_copies_override_to_global_memory(conn):
    await set_session_override(
        session_id=1, preamble_tex=r"\usetheme{Madrid}",
        source="user_request", conn=conn,
    )
    reply = await handle_style_command(
        action="promote_to_global", session_id=1, conn=conn,
    )
    assert "remember" in reply.lower() or "saved" in reply.lower() or "global" in reply.lower()
    async with conn.execute(
        "SELECT content FROM memories WHERE scope='global' AND status='active' "
        "AND json_extract(metadata, '$.kind') = 'slide_style_global'"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert "Madrid" in row[0]


@pytest.mark.asyncio
async def test_handle_promote_no_op_when_no_override(conn):
    reply = await handle_style_command(
        action="promote_to_global", session_id=1, conn=conn,
    )
    assert "nothing" in reply.lower() or "no custom" in reply.lower()
