import aiosqlite
import pytest

from paperhub.agents.style_resolver import (
    clear_session_override,
    promote_to_global,
    resolve_preamble,
    set_session_override,
)
from paperhub.db.migrate import apply_schema


@pytest.fixture
async def conn(tmp_path):
    db = tmp_path / "test.db"
    async with aiosqlite.connect(str(db)) as c:
        await c.execute(
            "CREATE TABLE chat_sessions (id INTEGER PRIMARY KEY, created_at TEXT, title TEXT)"
        )
        await c.execute(
            "CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "scope TEXT CHECK (scope IN ('session','global')), session_id INTEGER, "
            "content TEXT, created_at TEXT, updated_at TEXT, status TEXT DEFAULT 'active', "
            "supersedes INTEGER, superseded_by INTEGER, metadata TEXT)"
        )
        await c.commit()
        await apply_schema(c)
        await c.execute(
            "INSERT INTO chat_sessions (id, created_at, title) VALUES (1, datetime('now'), 't')"
        )
        await c.commit()
        yield c


@pytest.mark.asyncio
async def test_resolve_returns_default_when_no_override_or_memory(conn):
    tex = await resolve_preamble(session_id=1, conn=conn)
    assert "\\usetheme{Berlin}" in tex
    assert "\\usecolortheme{dolphin}" in tex


@pytest.mark.asyncio
async def test_session_override_wins_over_default(conn):
    await set_session_override(
        session_id=1,
        preamble_tex="\\documentclass{beamer}\\usetheme{Madrid}",
        source="user_request",
        conn=conn,
    )
    tex = await resolve_preamble(session_id=1, conn=conn)
    assert "\\usetheme{Madrid}" in tex
    assert "Berlin" not in tex


@pytest.mark.asyncio
async def test_global_memory_wins_over_default_when_no_session_override(conn):
    await conn.execute(
        "INSERT INTO memories (scope, content, status, metadata) VALUES ('global', "
        "'\\\\documentclass{beamer}\\\\usetheme{Warsaw}', 'active', "
        "'{\"kind\":\"slide_style_global\"}')"
    )
    await conn.commit()
    tex = await resolve_preamble(session_id=1, conn=conn)
    assert "\\usetheme{Warsaw}" in tex


@pytest.mark.asyncio
async def test_session_override_wins_over_global_memory(conn):
    await conn.execute(
        "INSERT INTO memories (scope, content, status, metadata) VALUES ('global', "
        "'\\\\usetheme{Warsaw}', 'active', '{\"kind\":\"slide_style_global\"}')"
    )
    await set_session_override(
        session_id=1,
        preamble_tex="\\usetheme{Madrid}",
        source="user_request",
        conn=conn,
    )
    await conn.commit()
    tex = await resolve_preamble(session_id=1, conn=conn)
    assert "\\usetheme{Madrid}" in tex
    assert "Warsaw" not in tex


@pytest.mark.asyncio
async def test_clear_session_override_falls_back(conn):
    await set_session_override(
        session_id=1,
        preamble_tex="\\usetheme{Madrid}",
        source="user_request",
        conn=conn,
    )
    await clear_session_override(session_id=1, conn=conn)
    tex = await resolve_preamble(session_id=1, conn=conn)
    assert "\\usetheme{Berlin}" in tex  # back to default


@pytest.mark.asyncio
async def test_promote_to_global_copies_session_override_to_memories(conn):
    await set_session_override(
        session_id=1,
        preamble_tex="\\usetheme{Madrid}",
        source="user_request",
        conn=conn,
    )
    await promote_to_global(session_id=1, conn=conn)
    async with conn.execute(
        "SELECT content FROM memories WHERE scope='global' AND status='active' "
        "AND metadata LIKE '%slide_style_global%'"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert "\\usetheme{Madrid}" in row[0]


@pytest.mark.asyncio
async def test_promote_to_global_supersede_chain_is_bidirectional(conn):
    """First promote: one active row. Second promote with a different style:
    old row → status='superseded' AND superseded_by points at the new active row."""
    # First promotion
    await set_session_override(
        session_id=1,
        preamble_tex="\\usetheme{Madrid}",
        source="user_request",
        conn=conn,
    )
    await promote_to_global(session_id=1, conn=conn)

    # Second promotion with a different style
    await set_session_override(
        session_id=1,
        preamble_tex="\\usetheme{Warsaw}",
        source="user_request",
        conn=conn,
    )
    await promote_to_global(session_id=1, conn=conn)

    # Now expect:
    #   - 1 active row (Warsaw)
    #   - 1 superseded row (Madrid) with superseded_by = <id of Warsaw row>
    async with conn.execute(
        "SELECT id, content, status, superseded_by FROM memories "
        "WHERE scope='global' AND metadata IS NOT NULL "
        "AND json_extract(metadata, '$.kind') = 'slide_style_global' "
        "ORDER BY id"
    ) as cur:
        rows = await cur.fetchall()
    assert len(rows) == 2
    older = rows[0]
    newer = rows[1]
    assert older[2] == "superseded"
    assert newer[2] == "active"
    assert "Madrid" in older[1]
    assert "Warsaw" in newer[1]
    assert older[3] == newer[0]  # back-pointer chain
