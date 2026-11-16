"""H5b.1: each idea's ``ideas.note`` is moved into the notes store, once."""
from __future__ import annotations

from pathlib import Path

import aiosqlite

from paperhub.cli.migrate_idea_notes import migrate
from paperhub.db.migrate import apply_schema
from paperhub.notes.ideas import idea_note_text

_NOTES = {
    1: "First thought.\n\nSecond paragraph\nwith a soft break.\n\n\n  \nThird.",
    2: "Only one line",
    3: "",
}


async def _seed(db: Path) -> None:
    async with aiosqlite.connect(db) as conn:
        await apply_schema(conn)
        await conn.execute(
            "INSERT INTO paper_content (content_key, kind, sha256, title, source_path, "
            "source_dir_path, html_path) VALUES ('pdf:1', 'pdf_upload', ?, 'D', '', '', '')",
            ("1" * 64,))
        for iid, note in _NOTES.items():
            await conn.execute(
                "INSERT INTO ideas (id, source_doc, source_section, title, note) "
                "VALUES (?, 1, 'S', ?, ?)", (iid, f"idea {iid}", note))
        await conn.commit()


def _norm(text: str) -> str:
    return "\n\n".join(p.strip() for p in text.split("\n\n") if p.strip())


async def test_migration_moves_every_note(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    await _seed(db)
    rows = await migrate(db, dry_run=False)
    assert {r["idea_id"]: r["status"] for r in rows} == {
        1: "migrated", 2: "migrated", 3: "skipped_empty"}
    async with aiosqlite.connect(db) as conn:
        for iid in (1, 2):
            got = await idea_note_text(conn, iid)
            assert got == "\n\n".join(
                p.strip() for p in _NOTES[iid].split("\n\n") if p.strip())
            assert "Third." in got or iid == 2
        assert await idea_note_text(conn, 3) == ""
        async with conn.execute(
            "SELECT type, author FROM note_blocks nb JOIN notes n ON n.id = nb.note_id "
            "WHERE n.target_kind = 'idea' AND n.target_id = '1' ORDER BY nb.order_key",
        ) as cur:
            assert [tuple(r) for r in await cur.fetchall()] == [("paragraph", "user")] * 3
        async with conn.execute(
            "SELECT actor, summary FROM note_revisions") as cur:
            revs = [tuple(r) for r in await cur.fetchall()]
        assert ("system", "moved from ideas.note") in revs
        async with conn.execute("SELECT note FROM ideas WHERE id = 1") as cur:
            assert (await cur.fetchone()) == (_NOTES[1],)  # column untouched


async def test_migration_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    await _seed(db)
    await migrate(db, dry_run=False)
    again = await migrate(db, dry_run=False)
    assert {r["idea_id"]: r["status"] for r in again} == {
        1: "skipped_exists", 2: "skipped_exists", 3: "skipped_empty"}
    async with (aiosqlite.connect(db) as conn,
                conn.execute("SELECT COUNT(*) FROM note_blocks") as cur):
        assert (await cur.fetchone()) == (4,)


async def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    await _seed(db)
    rows = await migrate(db, dry_run=True)
    assert {r["idea_id"]: r["status"] for r in rows}[1] == "would_migrate"
    async with aiosqlite.connect(db) as conn:
        for table in ("notes", "note_blocks", "note_revisions"):
            async with conn.execute(f"SELECT COUNT(*) FROM {table}") as cur:
                assert (await cur.fetchone()) == (0,)
