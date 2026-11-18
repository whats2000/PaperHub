"""paperhub-migrate-mark-styles: highlight / quote kinds become text marks
with a style; the palette gets its hex and order."""
import json

from paperhub.cli.migrate_mark_styles import migrate
from paperhub.db.migrate import apply_schema, migrate_mark_styles, note_marks_legacy
from paperhub.notes.marks import MarkIn, colors, create_mark, list_marks
from paperhub.notes.store import list_blocks

LEGACY_MARKS = """CREATE TABLE note_marks (
    id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL DEFAULT 1,
    paper_content_id INTEGER NOT NULL REFERENCES paper_content(id) ON DELETE CASCADE,
    block_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('highlight', 'quote', 'figure', 'equation', 'region')),
    color TEXT, view TEXT NOT NULL CHECK (view IN ('html', 'pdf')),
    pieces_json TEXT NOT NULL, lang TEXT, source_ref TEXT, section_key TEXT,
    status TEXT NOT NULL DEFAULT 'ok', created_at TEXT NOT NULL, deleted_at TEXT)"""
COLS = ("id, owner_id, paper_content_id, block_id, color, view, pieces_json, "
        "lang, source_ref, section_key, status, created_at, deleted_at")


async def _legacy_db(conn):
    """Marks made now, then put back into the pre-style shape."""
    await conn.execute(
        "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
        "sections_json, source_path, source_dir_path, html_path) "
        "VALUES (1, 'arxiv:1', 'arxiv', '1', 'A Paper', ?, 's', 'd', 'h')",
        (json.dumps([{"name": "Intro"}]),))
    await conn.commit()
    piece = [{"quote": "q", "chunk_start": 0, "chunk_end": 1}]
    base = dict(paper_content_id=1, view="html", section_key="Intro",
                pieces=piece)
    hl, _ = await create_mark(conn, MarkIn(kind="text", style="highlight",
                                           color="blue", **base))
    qt, _ = await create_mark(conn, MarkIn(kind="text", style="underline",
                                           color="pink", **base))
    fg, _ = await create_mark(conn, MarkIn(kind="figure", color="green",
                                           **base))
    await conn.execute("ALTER TABLE note_marks RENAME TO nm_new")
    await conn.execute(LEGACY_MARKS)
    await conn.execute(
        f"INSERT INTO note_marks ({COLS}, kind) SELECT {COLS}, "
        "CASE style WHEN 'highlight' THEN 'highlight' "
        "WHEN 'underline' THEN 'quote' ELSE kind END FROM nm_new")
    await conn.execute("DROP TABLE nm_new")
    await conn.execute(
        "UPDATE note_blocks SET payload_json = json_remove(json_set("
        "payload_json, '$.props.kind', CASE json_extract(payload_json, "
        "'$.props.style') WHEN 'highlight' THEN 'highlight' ELSE 'quote' END),"
        " '$.props.style') WHERE type = 'paperQuote'")
    await conn.execute("DROP TABLE mark_colors")
    await conn.execute(
        "CREATE TABLE mark_colors (owner_id INTEGER NOT NULL DEFAULT 1, "
        "color TEXT NOT NULL, name TEXT NOT NULL, visible INTEGER NOT NULL "
        "DEFAULT 1, PRIMARY KEY (owner_id, color))")
    await conn.execute(
        "INSERT INTO mark_colors (owner_id, color, name) VALUES "
        "(1, 'green', 'Key result'), (1, 'blue', 'Method'), "
        "(1, 'pink', 'Question'), (1, 'purple', 'Disagree')")
    await conn.commit()
    return hl, qt, fg


async def test_dry_run_counts_and_changes_nothing(conn):
    await _legacy_db(conn)
    out = await migrate_mark_styles(conn, dry_run=True)
    assert out == {"highlight": 1, "quote": 1, "blocks": 2, "colors": 4,
                   "rebuilt": 1}
    assert await note_marks_legacy(conn)


async def test_startup_schema_leaves_a_legacy_table_alone(conn):
    await _legacy_db(conn)
    await apply_schema(conn)
    assert await note_marks_legacy(conn)


async def test_migration_applies_both_rules_and_is_idempotent(conn):
    hl, qt, fg = await _legacy_db(conn)
    out = await migrate_mark_styles(conn)
    assert out["rebuilt"] == 1 and out["blocks"] == 2
    by_id = {m["id"]: m for m in await list_marks(conn, 1)}
    assert (by_id[hl["id"]]["kind"], by_id[hl["id"]]["style"],
            by_id[hl["id"]]["color"]) == ("text", "highlight", "blue")
    assert (by_id[qt["id"]]["kind"], by_id[qt["id"]]["style"],
            by_id[qt["id"]]["color"]) == ("text", "underline", None)
    assert (by_id[fg["id"]]["kind"], by_id[fg["id"]]["style"]) == ("figure", None)
    note_id = by_id[hl["id"]]["note_id"]
    props = {b.props["markId"]: b.props for b in await list_blocks(conn, note_id)
             if b.type == "paperQuote"}
    assert (props[hl["id"]]["kind"], props[hl["id"]]["style"]) == ("text", "highlight")
    assert (props[qt["id"]]["style"], props[qt["id"]]["color"]) == ("underline", None)
    assert [c["hex"] for c in await colors(conn)] == [
        "#2f9e63", "#3b7dd8", "#d6457e", "#8b5cd6"]
    again = await migrate_mark_styles(conn)
    assert again == {"highlight": 0, "quote": 0, "blocks": 0, "colors": 0,
                     "rebuilt": 0}


async def test_cli_dry_run_then_real(tmp_path):
    import aiosqlite

    db = tmp_path / "w.db"
    async with aiosqlite.connect(str(db)) as c:
        await c.execute("PRAGMA foreign_keys = ON")
        await apply_schema(c)
        await _legacy_db(c)
    dry = await migrate(db, dry_run=True)
    assert dry["rebuilt"] == 1
    real = await migrate(db, dry_run=False)
    assert real["highlight"] == 1
    assert (await migrate(db, dry_run=False))["rebuilt"] == 0
