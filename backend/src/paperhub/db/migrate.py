import asyncio
import logging
import shutil
from importlib.resources import files
from pathlib import Path

import aiosqlite

_LOG = logging.getLogger(__name__)


async def _rebuild_papers_table(conn: aiosqlite.Connection) -> None:
    """Rebuild the papers table to add ON DELETE RESTRICT to paper_content_id FK.

    Uses the standard SQLite 12-step table rebuild wrapped in a transaction.
    """
    await conn.execute("BEGIN")
    try:
        await conn.execute("""
            CREATE TABLE papers_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
                paper_content_id INTEGER NOT NULL
                    REFERENCES paper_content(id) ON DELETE RESTRICT,
                enabled INTEGER NOT NULL DEFAULT 1,
                added_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (session_id, paper_content_id)
            )
        """)
        await conn.execute(
            "INSERT INTO papers_new "
            "SELECT id, session_id, paper_content_id, enabled, added_at FROM papers"
        )
        await conn.execute("DROP TABLE papers")
        await conn.execute("ALTER TABLE papers_new RENAME TO papers")
        await conn.execute("COMMIT")
    except Exception:
        await conn.execute("ROLLBACK")
        raise


async def _rebuild_messages_table(conn: aiosqlite.Connection) -> None:
    """Rebuild the messages table to add REFERENCES runs(id) ON DELETE SET NULL
    to run_id, replacing the bare integer column.
    """
    await conn.execute("BEGIN")
    try:
        await conn.execute("""
            CREATE TABLE messages_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
                content TEXT NOT NULL,
                run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await conn.execute(
            "INSERT INTO messages_new "
            "SELECT id, session_id, role, content, run_id, created_at FROM messages"
        )
        await conn.execute("DROP TABLE messages")
        await conn.execute("ALTER TABLE messages_new RENAME TO messages")
        await conn.execute("COMMIT")
    except Exception:
        await conn.execute("ROLLBACK")
        raise


async def purge_deleted_sessions(
    conn: aiosqlite.Connection,
    retention_days: int,
    *,
    workspace_dir: Path | None = None,
) -> int:
    """Hard-delete soft-deleted sessions whose tombstone is older than the
    retention window. Cascades the DB rows (papers/messages/runs/tool_calls/
    decks/deck_slides/slide_style_overrides) AND removes each purged session's
    on-disk folder under ``workspace_dir/chat_session/<session_id>/`` when
    ``workspace_dir`` is provided.

    Returns the number of sessions purged. A retention of 0 purges every
    tombstoned session immediately. Requires ``PRAGMA foreign_keys = ON`` for
    the cascade (open_db sets this). Per-folder cleanup is best-effort: a
    missing or locked folder logs a warning and continues with the rest.
    """
    # Capture IDs first so we can remove their folders after the cascade.
    async with conn.execute(
        "SELECT id FROM chat_sessions "
        "WHERE deleted_at IS NOT NULL "
        "AND deleted_at < datetime('now', ?)",
        (f"-{int(retention_days)} days",),
    ) as cur:
        purged_ids = [row[0] for row in await cur.fetchall()]

    if not purged_ids:
        return 0

    del_cur = await conn.execute(
        "DELETE FROM chat_sessions "
        "WHERE deleted_at IS NOT NULL "
        "AND deleted_at < datetime('now', ?)",
        (f"-{int(retention_days)} days",),
    )
    await conn.commit()
    n_purged = (
        del_cur.rowcount if del_cur.rowcount is not None else len(purged_ids)
    )

    # Cascade to disk. Soft-fails per-folder so a missing/locked folder doesn't
    # block the rest. Each folder is workspace_dir/chat_session/<id>/.
    if workspace_dir is not None:
        sessions_root = Path(workspace_dir) / "chat_session"
        for sid in purged_ids:
            folder = sessions_root / str(sid)
            if folder.exists():
                try:
                    await asyncio.to_thread(shutil.rmtree, folder)
                except Exception as exc:  # noqa: BLE001
                    _LOG.warning(
                        "purge_deleted_sessions: failed to remove %s: %r",
                        folder, exc,
                    )
    return n_purged


async def sweep_orphan_session_folders(
    conn: aiosqlite.Connection, workspace_dir: Path
) -> int:
    """Remove ``workspace_dir/chat_session/<id>/`` folders whose id has NO row
    in ``chat_sessions`` (active or tombstoned). Defends against partial-write
    crashes during session creation and pre-fix leaks from before
    ``purge_deleted_sessions`` cascaded to disk.

    Only numeric subdirectories are considered — non-digit names (operator
    scratch like ``scratch/`` or ``tmp_data/``) are left alone. Best-effort
    per-folder: a failure logs and continues.

    Returns the number of folders removed.
    """
    sessions_root = Path(workspace_dir) / "chat_session"
    if not sessions_root.exists():
        return 0
    async with conn.execute("SELECT id FROM chat_sessions") as cur:
        db_ids = {row[0] for row in await cur.fetchall()}
    removed = 0
    for folder in sessions_root.iterdir():
        if not folder.is_dir():
            continue
        if not folder.name.isdigit():
            continue
        if int(folder.name) not in db_ids:
            try:
                await asyncio.to_thread(shutil.rmtree, folder)
                removed += 1
            except Exception as exc:  # noqa: BLE001
                _LOG.warning(
                    "sweep_orphan_session_folders: failed to remove %s: %r",
                    folder, exc,
                )
    return removed


async def apply_schema(conn: aiosqlite.Connection) -> None:
    sql = (files("paperhub.db") / "schema.sql").read_text(encoding="utf-8")
    await conn.executescript(sql)
    # executescript auto-commits; no explicit commit needed here.

    # -----------------------------------------------------------------------
    # Idempotent column-add for chat_sessions.deleted_at (soft-delete
    # tombstone). Pre-existing DBs created before this migration won't have it.
    # -----------------------------------------------------------------------
    async with conn.execute("PRAGMA table_info(chat_sessions)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "deleted_at" not in cols:
        await conn.execute("ALTER TABLE chat_sessions ADD COLUMN deleted_at TEXT")
        await conn.commit()

    # -----------------------------------------------------------------------
    # v2.30: Idempotent column-add for chat_sessions.forked_from_session_id
    # (fork lineage — the session a fork was branched FROM). A nullable
    # self-referential FK with ON DELETE SET NULL; SQLite permits adding it via
    # ALTER because the default is NULL. Pre-existing DBs created before the
    # fork feature won't have it.
    # -----------------------------------------------------------------------
    async with conn.execute("PRAGMA table_info(chat_sessions)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "forked_from_session_id" not in cols:
        await conn.execute(
            "ALTER TABLE chat_sessions ADD COLUMN forked_from_session_id "
            "INTEGER REFERENCES chat_sessions(id) ON DELETE SET NULL"
        )
        await conn.commit()

    # -----------------------------------------------------------------------
    # Idempotent column-add for runs.search_results_json (paper-search cards
    # persisted per turn so they replay cross-device). Pre-existing DBs won't
    # have it.
    # -----------------------------------------------------------------------
    async with conn.execute("PRAGMA table_info(runs)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "search_results_json" not in cols:
        await conn.execute("ALTER TABLE runs ADD COLUMN search_results_json TEXT")
        await conn.commit()

    # -----------------------------------------------------------------------
    # F4.5: Idempotent column-add for runs.deck_version_id — records which
    # deck-version snapshot a turn stamped, so per-turn DeckChip cards in
    # the chat replay refer to the version produced by THAT turn (not just
    # the most recent one). NULL for non-slide runs.
    # -----------------------------------------------------------------------
    async with conn.execute("PRAGMA table_info(runs)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "deck_version_id" not in cols:
        await conn.execute("ALTER TABLE runs ADD COLUMN deck_version_id TEXT")
        await conn.commit()

    # -----------------------------------------------------------------------
    # C4: Idempotent column-add migration for paper_content.abstract
    # (pre-existing DBs created before Plan C won't have this column).
    # -----------------------------------------------------------------------
    async with conn.execute("PRAGMA table_info(paper_content)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "abstract" not in cols:
        await conn.execute(
            "ALTER TABLE paper_content ADD COLUMN abstract TEXT NOT NULL DEFAULT ''"
        )
        await conn.commit()
        # Rebuild FTS so the new column is indexed.
        await conn.execute(
            "INSERT INTO paper_content_fts(paper_content_fts) VALUES ('rebuild')"
        )
        await conn.commit()

    # -----------------------------------------------------------------------
    # v2.10-2: Idempotent column-add for paper_content.sections_json
    # (pre-existing DBs created before this migration won't have the column).
    # Populated at re-ingest time by Plan C v2.10-5's paperhub-reingest CLI;
    # rows that haven't been re-ingested keep NULL.
    # -----------------------------------------------------------------------
    async with conn.execute("PRAGMA table_info(paper_content)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "sections_json" not in cols:
        await conn.execute(
            "ALTER TABLE paper_content ADD COLUMN sections_json TEXT"
        )
        await conn.commit()

    # -----------------------------------------------------------------------
    # F2.1: Idempotent column-add for paper_content.asset_status
    # Tracks the PaperAsset build state for each paper.  Values:
    #   'latex' | 'pymupdf_only' | 'marker_pending' | 'marker_ready' | 'marker_failed'
    # NULL for rows ingested before Plan F2 (treated as "asset not yet built").
    # -----------------------------------------------------------------------
    async with conn.execute("PRAGMA table_info(paper_content)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "asset_status" not in cols:
        await conn.execute(
            "ALTER TABLE paper_content ADD COLUMN asset_status TEXT"
        )
        await conn.commit()

    # -----------------------------------------------------------------------
    # W6-1: Idempotent column-add for chunks.dom_id
    # (pre-existing DBs created before Plan D Wave 6 won't have this column).
    # Populated at re-ingest / sentinel-injection time; rows that haven't been
    # processed keep NULL.
    # -----------------------------------------------------------------------
    async with conn.execute("PRAGMA table_info(chunks)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "dom_id" not in cols:
        await conn.execute(
            "ALTER TABLE chunks ADD COLUMN dom_id TEXT"
        )
        await conn.commit()

    # -----------------------------------------------------------------------
    # A5: Ensure papers.paper_content_id FK has ON DELETE RESTRICT.
    # PRAGMA foreign_key_list returns rows where column 6 is `on_delete`.
    # -----------------------------------------------------------------------
    papers_fk_ok = False
    async with conn.execute("PRAGMA foreign_key_list('papers')") as cur:
        for row in await cur.fetchall():
            # row: (id, seq, table, from, to, on_update, on_delete, match)
            if row[3] == "paper_content_id" and row[6].upper() == "RESTRICT":
                papers_fk_ok = True
    if not papers_fk_ok:
        await _rebuild_papers_table(conn)

    # -----------------------------------------------------------------------
    # A6: Ensure messages.run_id FK references runs(id) ON DELETE SET NULL.
    # -----------------------------------------------------------------------
    messages_fk_ok = False
    async with conn.execute("PRAGMA foreign_key_list('messages')") as cur:
        for row in await cur.fetchall():
            # row: (id, seq, table, from, to, on_update, on_delete, match)
            if row[3] == "run_id" and row[6].upper() == "SET NULL":
                messages_fk_ok = True
    if not messages_fk_ok:
        await _rebuild_messages_table(conn)

    # -----------------------------------------------------------------------
    # v2.17 — memories governance columns (idempotent column-add).
    # status / supersedes / superseded_by support memory lifecycle management.
    # Pre-existing DBs created before Plan E won't have these columns.
    # -----------------------------------------------------------------------
    async with conn.execute("PRAGMA table_info(memories)") as cur:
        mem_cols = {r[1] for r in await cur.fetchall()}
    if "status" not in mem_cols:
        await conn.execute(
            "ALTER TABLE memories ADD COLUMN status TEXT NOT NULL DEFAULT 'active' "
            "CHECK (status IN ('active','superseded'))"
        )
    if "supersedes" not in mem_cols:
        await conn.execute(
            "ALTER TABLE memories ADD COLUMN supersedes INTEGER NULL "
            "REFERENCES memories(id) ON DELETE SET NULL"
        )
    if "superseded_by" not in mem_cols:
        await conn.execute(
            "ALTER TABLE memories ADD COLUMN superseded_by INTEGER NULL "
            "REFERENCES memories(id) ON DELETE SET NULL"
        )
    await conn.commit()

    # -----------------------------------------------------------------------
    # F2.1 A1: Idempotent column-add for chunks.match_text
    # Stores a markdown-stripped copy of chunk.text for the Citation Canvas
    # resolver (which matches a plain-text prefix against the PDF text layer).
    # NULL until a later task populates it; existing rows are unaffected.
    # -----------------------------------------------------------------------
    async with conn.execute("PRAGMA table_info(chunks)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "match_text" not in cols:
        await conn.execute(
            "ALTER TABLE chunks ADD COLUMN match_text TEXT"
        )
        await conn.commit()

    # -----------------------------------------------------------------------
    # F2.1 A2': Idempotent column-add for chunks.page (INTEGER) + chunks.bbox
    # (TEXT, JSON [x0,y0,x1,y1]). Marker block-anchored chunks carry their
    # page index + union bbox so the Citation Canvas can draw a GEOMETRIC
    # highlight. NULL for non-Marker (LaTeX / PyMuPDF) chunks — unchanged.
    # -----------------------------------------------------------------------
    async with conn.execute("PRAGMA table_info(chunks)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "page" not in cols:
        await conn.execute("ALTER TABLE chunks ADD COLUMN page INTEGER")
        await conn.commit()
    if "bbox" not in cols:
        await conn.execute("ALTER TABLE chunks ADD COLUMN bbox TEXT")
        await conn.commit()

    # -----------------------------------------------------------------------
    # F2.1 A3: Idempotent column-add for paper_content.layout_json (TEXT, JSON
    # list of {kind,label,caption,page,chunk_id}). A per-paper index of figures
    # + tables so the paper_qa subagent can later fetch a floated/mis-filed
    # layout object by its label. Populated by the Marker upgrade path; NULL for
    # non-Marker (LaTeX / PyMuPDF) rows — unchanged.
    # -----------------------------------------------------------------------
    async with conn.execute("PRAGMA table_info(paper_content)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "layout_json" not in cols:
        await conn.execute(
            "ALTER TABLE paper_content ADD COLUMN layout_json TEXT"
        )
        await conn.commit()

    # -----------------------------------------------------------------------
    # decks + deck_slides (v2.18 / v2.21, Plan F): created by schema.sql's
    # CREATE TABLE IF NOT EXISTS. deck_slides holds one row per final frame
    # (frame_tex + opt-in note_text/note_language + PDF page span). Future
    # column-adds go here, mirroring the chat_sessions.deleted_at pattern.
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # F6.2: Idempotent column-add for deck_slides.source_sections_json — the
    # per-slide source-section grounding (JSON list of {paper_id, section_name,
    # chunk_ids}) that satisfies the traceability north star: each content
    # slide records the paper section(s) it was written from. NULL for rows
    # created before this migration; sl_emit writes '[]' when no outline.
    # -----------------------------------------------------------------------
    async with conn.execute("PRAGMA table_info(deck_slides)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "source_sections_json" not in cols:
        await conn.execute(
            "ALTER TABLE deck_slides ADD COLUMN source_sections_json TEXT"
        )
        await conn.commit()

    # -----------------------------------------------------------------------
    # F4.5 (v2.25): Drop decks.theme + add decks.current_version_id + create
    # slide_style_overrides (§III-7). Idempotent.
    #
    # SQLite < 3.35 cannot DROP COLUMN directly, so we use the table-rebuild
    # pattern — `theme` had a NOT NULL default of 'metropolis', so dropping
    # it silently is safe (preamble is the source of truth in F4.5, resolved
    # via slide_style_overrides -> slide_style_global memory -> default file).
    # -----------------------------------------------------------------------
    async with conn.execute("PRAGMA table_info(decks)") as cur:
        decks_cols = {row[1] for row in await cur.fetchall()}

    if "theme" in decks_cols or "current_version_id" not in decks_cols:
        # Atomic rebuild — mirrors _rebuild_papers_table / _rebuild_messages_table.
        # executescript auto-commits each statement, so an interruption between
        # INSERT and DROP could leave decks_new orphaned and trip the next run.
        await conn.execute("BEGIN")
        try:
            await conn.execute("""
                CREATE TABLE decks_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
                    run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
                    tex_path TEXT NOT NULL,
                    pdf_path TEXT,
                    speaker_notes_json TEXT,
                    plan_json TEXT,
                    page_count INTEGER NOT NULL DEFAULT 0,
                    current_version_id TEXT,
                    contributing_paper_ids_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','error')),
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE (session_id)
                )
            """)
            await conn.execute(
                "INSERT INTO decks_new ("
                "id, session_id, run_id, tex_path, pdf_path, speaker_notes_json, "
                "plan_json, page_count, current_version_id, "
                "contributing_paper_ids_json, status, created_at, updated_at) "
                "SELECT id, session_id, run_id, tex_path, pdf_path, speaker_notes_json, "
                "plan_json, page_count, NULL, "
                "contributing_paper_ids_json, status, created_at, updated_at "
                "FROM decks"
            )
            await conn.execute("DROP TABLE decks")
            await conn.execute("ALTER TABLE decks_new RENAME TO decks")
            await conn.execute("COMMIT")
        except Exception:
            await conn.execute("ROLLBACK")
            raise

    # -----------------------------------------------------------------------
    # F4.5: memories.metadata column — a JSON blob the style_resolver uses to
    # tag the "remembered global slide style" memory row
    # (``json_extract(metadata, '$.kind') = 'slide_style_global'``). Idempotent
    # column-add; pre-existing DBs created before F4.5 won't have it.
    # -----------------------------------------------------------------------
    async with conn.execute("PRAGMA table_info(memories)") as cur:
        mem_cols = {row[1] for row in await cur.fetchall()}
    if "metadata" not in mem_cols:
        await conn.execute("ALTER TABLE memories ADD COLUMN metadata TEXT")
        await conn.commit()

    # Create slide_style_overrides if missing (pre-existing DBs created before
    # F4.5 won't have it; schema.sql will create it on fresh DBs but the
    # explicit guard keeps the migration safe for both paths).
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='slide_style_overrides'"
    ) as cur:
        exists = await cur.fetchone()
    if not exists:
        await conn.execute(
            """
            CREATE TABLE slide_style_overrides (
                session_id INTEGER PRIMARY KEY REFERENCES chat_sessions(id) ON DELETE CASCADE,
                preamble_tex TEXT NOT NULL,
                source TEXT NOT NULL CHECK (source IN ('user_request','agent_inferred','global_memory_projection')),
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        await conn.commit()

    # -----------------------------------------------------------------------
    # Rebuild the FTS index from paper_content if the index is empty
    # but the source table has rows (handles upgrades from pre-FTS schemas).
    # -----------------------------------------------------------------------
    async with conn.execute("SELECT COUNT(*) FROM paper_content") as cur:
        pc_row = await cur.fetchone()
    pc_count: int = int(pc_row[0]) if pc_row is not None else 0
    async with conn.execute("SELECT COUNT(*) FROM paper_content_fts") as cur:
        fts_row = await cur.fetchone()
    fts_count: int = int(fts_row[0]) if fts_row is not None else 0
    if pc_count > 0 and fts_count == 0:
        await conn.execute(
            "INSERT INTO paper_content_fts(paper_content_fts) VALUES ('rebuild')"
        )
        await conn.commit()
