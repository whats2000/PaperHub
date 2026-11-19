"""H5b.1: note titles auto-filled from the target are cleared, once."""
from __future__ import annotations

from pathlib import Path

import aiosqlite

from paperhub.cli.clear_auto_note_titles import clear_titles
from paperhub.db.migrate import apply_schema


async def _seed(db: Path) -> None:
    async with aiosqlite.connect(db) as conn:
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (id, title) VALUES (1, 'Chat  One')")
        await conn.execute(
            "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
            "source_path, source_dir_path, html_path) "
            "VALUES (1, 'arxiv:1', 'arxiv', '1', 'A Paper', 's', 'd', 'h')")
        for nid, kind, target, title in [
            (1, "session", "1", "Chat One"),      # auto (whitespace differs)
            (2, "paper", "1", "My reading"),      # the user's
            (3, "paper", "99", "Gone"),           # target missing
            (4, "session", "2", ""),              # already empty: not listed
        ]:
            await conn.execute(
                "INSERT INTO notes (id, target_kind, target_id, title, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, 'x', 'x')", (nid, kind, target, title))
        await conn.commit()


async def _titles(db: Path) -> dict[int, str]:
    async with aiosqlite.connect(db) as conn, conn.execute(
            "SELECT id, title FROM notes ORDER BY id") as cur:
        return {int(r[0]): str(r[1]) for r in await cur.fetchall()}


async def test_dry_run_reports_and_changes_nothing(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    await _seed(db)
    rows = await clear_titles(db, dry_run=True)
    assert {r["note_id"]: r["status"] for r in rows} == {
        1: "would_clear", 2: "kept_user_title", 3: "target_missing"}
    assert (await _titles(db))[1] == "Chat One"


async def test_clears_auto_titles_once(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    await _seed(db)
    rows = await clear_titles(db, dry_run=False)
    assert [r["status"] for r in rows if r["note_id"] == 1] == ["cleared"]
    assert await _titles(db) == {1: "", 2: "My reading", 3: "Gone", 4: ""}
    again = await clear_titles(db, dry_run=False)
    assert {r["status"] for r in again} == {"kept_user_title", "target_missing"}
