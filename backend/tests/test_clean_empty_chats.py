"""H5b.1 Task 14: ``paperhub-clean-empty-chats`` soft-deletes the ghost
chats (no messages, no papers, an empty or missing note, title still
"New chat", no link to their note), once."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import aiosqlite
import pytest

from paperhub.cli.clean_empty_chats import clean_empty_chats, main
from paperhub.db.migrate import apply_schema

pytestmark = pytest.mark.asyncio


def _para(text: str) -> str:
    return json.dumps({"props": {}, "content": [{"type": "text", "text": text}]})


def _link(note_id: int) -> str:
    return json.dumps({"props": {}, "content": [{"type": "noteLink", "props": {
        "targetKind": "note", "targetId": note_id, "label": "", "name": "x"}}]})


async def _seed(db: Path) -> None:
    async with aiosqlite.connect(db) as conn:
        await apply_schema(conn)
        await conn.executescript(
            "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
            "source_path, source_dir_path, html_path) "
            "VALUES (1, 'arxiv:1', 'arxiv', '1', 'A Paper', 's', 'd', 'h');"
            # 1 qualifies (no note); 2 qualifies (an empty note).
            "INSERT INTO chat_sessions (id) VALUES (1), (2);"
            "INSERT INTO chat_sessions (id) VALUES (3);"   # has a message
            "INSERT INTO messages (session_id, role, content) VALUES (3, 'user', 'hi');"
            "INSERT INTO chat_sessions (id) VALUES (4);"   # has a paper
            "INSERT INTO papers (session_id, paper_content_id) VALUES (4, 1);"
            "INSERT INTO chat_sessions (id) VALUES (5);"   # note has text
            "INSERT INTO chat_sessions (id) VALUES (6);"   # its note is linked
            "INSERT INTO chat_sessions (id, title) VALUES (7, 'Renamed');"
            "INSERT INTO chat_sessions (id, internal) VALUES (8, 1);"  # internal
            "INSERT INTO chat_sessions (id, deleted_at) VALUES (9, '2026-11-01');"
            # 10 qualifies: its note's title is the chat's own (auto-filled).
            "INSERT INTO chat_sessions (id) VALUES (10);"
            "INSERT INTO notes (id, target_kind, target_id, session_id, title, "
            "created_at, updated_at) VALUES (20, 'session', '10', 10, 'New chat', 'x', 'x');")
        for nid, sid in [(12, 2), (15, 5), (16, 6), (13, 3)]:
            await conn.execute(
                "INSERT INTO notes (id, target_kind, target_id, session_id, "
                "created_at, updated_at) VALUES (?, 'session', ?, ?, 'x', 'x')",
                (nid, str(sid), sid))
        for bid, nid, payload, text in [
            ("b_blank", 12, _para("  "), "  "),
            ("b_text", 15, _para("my words"), "my words"),
            ("b_link", 13, _link(16), "x"),
        ]:
            await conn.execute(
                "INSERT INTO note_blocks (id, note_id, order_key, type, "
                "payload_json, text, author, changed_rev, changed_by, created_at, "
                "updated_at) "
                "VALUES (?, ?, 'a', 'paragraph', ?, ?, 'user', 1, 'user', 'x', 'x')",
                (bid, nid, payload, text))
        await conn.commit()


async def _deleted(db: Path) -> tuple[set[int], set[int]]:
    async with aiosqlite.connect(db) as conn:
        async with conn.execute(
                "SELECT id FROM chat_sessions WHERE deleted_at IS NOT NULL") as cur:
            chats = {int(r[0]) for r in await cur.fetchall()}
        async with conn.execute(
                "SELECT id FROM notes WHERE deleted_at IS NOT NULL") as cur:
            notes = {int(r[0]) for r in await cur.fetchall()}
    return chats, notes


async def test_dry_run_lists_exactly_the_ghost_chats(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    await _seed(db)
    rows = await clean_empty_chats(db, dry_run=True)
    assert [(r["session_id"], r["note_id"], r["status"]) for r in rows] == [
        (1, None, "would_delete"), (2, 12, "would_delete"), (10, 20, "would_delete")]
    assert await _deleted(db) == ({9}, set())


async def test_run_soft_deletes_and_logs_them_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    db = tmp_path / "p.db"
    await _seed(db)
    with caplog.at_level(logging.INFO, logger="paperhub.cli.clean_empty_chats"):
        rows = await clean_empty_chats(db, dry_run=False)
    assert [(r["session_id"], r["status"]) for r in rows] == [
        (1, "deleted"), (2, "deleted"), (10, "deleted")]
    assert await _deleted(db) == ({1, 2, 9, 10}, {12, 20})
    assert "session=1" in caplog.text and "session=2 note=12" in caplog.text
    # The note goes down with its chat: Undo (restore) brings both back.
    async with aiosqlite.connect(db) as conn, conn.execute(
            "SELECT s.deleted_at = n.deleted_at FROM chat_sessions s "
            "JOIN notes n ON n.target_id = CAST(s.id AS TEXT) WHERE s.id = 2") as cur:
        assert (await cur.fetchone()) == (1,)
    assert await clean_empty_chats(db, dry_run=False) == []
    assert await _deleted(db) == ({1, 2, 9, 10}, {12, 20})


async def test_main_prints_each_chat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    await _seed(tmp_path / "paperhub.db")
    # main() runs its own event loop: call it off this one.
    import asyncio
    assert await asyncio.to_thread(main, ["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would_delete" in out and "summary: would_delete=3" in out
    assert await _deleted(tmp_path / "paperhub.db") == ({9}, set())


async def test_main_logs_each_removal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    import asyncio
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    await _seed(tmp_path / "paperhub.db")
    assert await asyncio.to_thread(main, []) == 0
    captured = capsys.readouterr()
    assert "summary: deleted=3" in captured.out
    assert "soft-deleted session=2 note=12" in captured.err
