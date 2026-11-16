"""``<notes>`` and ``<notes_changes>`` in the core agent's session state (H5b.1 Task 10)."""
from __future__ import annotations

import re

import aiosqlite
import pytest

from paperhub.agents.core.context import build_session_state
from paperhub.notes.ops import BlockIn, Op, apply_ops
from paperhub.notes.store import get_or_create_note
from paperhub.outbound.context import begin_turn, current_private

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fake_tracer")]  # chat session 1


def _p(text: str) -> Op:
    return Op(op="insert", block=BlockIn(
        type="paragraph", content=[{"type": "text", "text": text}]))


async def _note(conn: aiosqlite.Connection, sid: int, title: str = "My chat") -> int:
    return (await get_or_create_note(
        conn, target_kind="session", target_id=str(sid), title=title,
        session_id=sid)).id


async def _rev(conn: aiosqlite.Connection, note_id: int) -> int:
    async with conn.execute("SELECT revision FROM notes WHERE id = ?", (note_id,)) as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _write(conn: aiosqlite.Connection, note_id: int, text: str, actor: str) -> None:
    await apply_ops(conn, note_id, base_revision=await _rev(conn, note_id),
                    ops=[_p(text)], actor=actor)  # type: ignore[arg-type]


async def _mark_read(conn: aiosqlite.Connection, sid: int, note_id: int) -> None:
    await conn.execute(
        "INSERT INTO note_agent_reads (session_id, note_id, revision, at) "
        "VALUES (?, ?, ?, 'now') ON CONFLICT (session_id, note_id) "
        "DO UPDATE SET revision = excluded.revision",
        (sid, note_id, await _rev(conn, note_id)))
    await conn.commit()


async def test_notes_index_bounded(migrated_db: aiosqlite.Connection) -> None:
    nid = await _note(migrated_db, 1)
    await _write(migrated_db, nid, "hello", "user")
    for i in range(15):
        await migrated_db.execute(
            "INSERT INTO paper_content (content_key, kind, arxiv_id, title, "
            "source_path, source_dir_path, html_path) "
            "VALUES (?, 'arxiv', ?, ?, '/x', '/x', '/x')", (f"k{i}", f"a{i}", f"P{i}"))
        await migrated_db.execute(
            "INSERT INTO papers (session_id, paper_content_id, enabled, added_at) "
            "SELECT 1, id, 1, ? FROM paper_content WHERE content_key = ?",
            (f"2026-01-01T00:00:{i:02d}", f"k{i}"))
    await migrated_db.commit()
    state = await build_session_state(migrated_db, 1, {})
    block = re.search(r"<notes>.*?</notes>", state, re.S)
    assert block is not None
    assert block.group(0).count("<note ") == 12
    assert 'ref="chat"' in block.group(0)
    assert 'revision="1"' in block.group(0) and 'blocks="1"' in block.group(0)
    assert "<note_title>My chat</note_title>" in block.group(0)


async def test_user_edit_appears_in_notes_changes(
    migrated_db: aiosqlite.Connection,
) -> None:
    nid = await _note(migrated_db, 1)
    await _write(migrated_db, nid, "first", "user")
    await _mark_read(migrated_db, 1, nid)
    assert "<notes_changes>" not in await build_session_state(migrated_db, 1, {})
    await _write(migrated_db, nid, "second", "user")
    async with migrated_db.execute(
        "SELECT block_id FROM note_block_history WHERE note_id = ? AND revision = 2",
        (nid,),
    ) as cur:
        bid = (await cur.fetchone())[0]  # type: ignore[index]
    state = await build_session_state(migrated_db, 1, {})
    block = re.search(r"<notes_changes>.*?</notes_changes>", state, re.S)
    assert block is not None
    assert 'note="chat"' in block.group(0) and bid in block.group(0)
    assert "first" not in block.group(0)  # ids only, never the user's words


async def test_agent_read_clears_change(migrated_db: aiosqlite.Connection) -> None:
    nid = await _note(migrated_db, 1)
    await _write(migrated_db, nid, "first", "user")
    await _mark_read(migrated_db, 1, nid)
    await _write(migrated_db, nid, "second", "user")
    assert "<notes_changes>" in await build_session_state(migrated_db, 1, {})
    await _mark_read(migrated_db, 1, nid)
    assert "<notes_changes>" not in await build_session_state(migrated_db, 1, {})


async def test_agent_own_edit_is_not_a_change(
    migrated_db: aiosqlite.Connection,
) -> None:
    nid = await _note(migrated_db, 1)
    await _mark_read(migrated_db, 1, nid)
    await _write(migrated_db, nid, "by agent", "agent")
    assert "<notes_changes>" not in await build_session_state(migrated_db, 1, {})


async def test_note_titles_are_private_material(
    migrated_db: aiosqlite.Connection,
) -> None:
    begin_turn()
    nid = await _note(migrated_db, 1, "Secret plan")
    await _write(migrated_db, nid, "x", "user")
    await build_session_state(migrated_db, 1, {})
    assert any(i.kind == "note" and "Secret plan" in i.text for i in current_private())
