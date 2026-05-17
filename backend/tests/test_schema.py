import aiosqlite
import pytest

EXPECTED_TABLES = {
    "chat_sessions", "paper_content", "papers", "chunks",
    "messages", "runs", "tool_calls",
}


async def test_all_seven_tables_exist(migrated_db: aiosqlite.Connection) -> None:
    async with migrated_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ) as cursor:
        rows = await cursor.fetchall()
    assert {r[0] for r in rows} >= EXPECTED_TABLES


async def test_tool_calls_pk_includes_branch(migrated_db: aiosqlite.Connection) -> None:
    async with migrated_db.execute("PRAGMA index_list('tool_calls')") as cursor:
        indexes = [row[1] async for row in cursor]
    pk_indexes = [name for name in indexes if name.startswith("sqlite_autoindex")]
    # Read PK columns
    async with migrated_db.execute(
        f"PRAGMA index_info('{pk_indexes[0]}')"
    ) as cursor:
        cols = [row[2] async for row in cursor]
    assert cols == ["run_id", "branch", "step_index"]


async def test_paper_content_xor_constraint(migrated_db: aiosqlite.Connection) -> None:
    # arxiv_id XOR sha256 must hold — inserting both should fail.
    # SQLite fires the CHECK constraint at execute() time (not deferred to commit).
    with pytest.raises(aiosqlite.IntegrityError):
        await migrated_db.execute(
            "INSERT INTO paper_content (content_key, kind, arxiv_id, sha256, "
            "title, source_path, source_dir_path, html_path) "
            "VALUES ('arxiv:1', 'arxiv', '1', 'abc', 't', 's', 'd', 'h')"
        )


async def test_papers_unique_session_content(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, "
        "source_path, source_dir_path, html_path) "
        "VALUES ('arxiv:2403.01234', 'arxiv', '2403.01234', 't', 's', 'd', 'h')"
    )
    await migrated_db.commit()
    await migrated_db.execute(
        "INSERT INTO papers (session_id, paper_content_id) VALUES (1, 1)"
    )
    await migrated_db.commit()
    with pytest.raises(aiosqlite.IntegrityError):
        await migrated_db.execute(
            "INSERT INTO papers (session_id, paper_content_id) VALUES (1, 1)"
        )
        await migrated_db.commit()
