from importlib.resources import files

import aiosqlite


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


async def apply_schema(conn: aiosqlite.Connection) -> None:
    sql = (files("paperhub.db") / "schema.sql").read_text(encoding="utf-8")
    await conn.executescript(sql)
    # executescript auto-commits; no explicit commit needed here.

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
