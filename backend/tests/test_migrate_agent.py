"""Plan H4 / Task 2: the additive agent-flow schema."""
import json
from pathlib import Path

import aiosqlite
import pytest

from paperhub.api.sessions import load_search_results
from paperhub.db.migrate import apply_schema


async def _cols(conn: aiosqlite.Connection, table: str) -> dict[str, tuple]:  # type: ignore[type-arg]
    async with conn.execute(f"PRAGMA table_info({table})") as cur:
        return {r[1]: tuple(r) for r in await cur.fetchall()}


async def _legacy_db(db_path: Path) -> None:
    """A pre-A4 / pre-H4 DB: runs CHECK lacks 'interrupted', no agent columns."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "CREATE TABLE chat_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "title TEXT NOT NULL DEFAULT 'New chat')"
        )
        await conn.execute(
            "CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "session_id INTEGER NOT NULL REFERENCES chat_sessions(id) "
            "ON DELETE CASCADE, routing_decision_json TEXT, "
            "search_results_json TEXT, deck_version_id TEXT, "
            "started_at TEXT NOT NULL DEFAULT (datetime('now')), finished_at TEXT, "
            "status TEXT NOT NULL DEFAULT 'running' "
            "CHECK (status IN ('running','ok','error','cancelled')))"
        )
        await conn.execute("INSERT INTO chat_sessions (title) VALUES ('old')")
        await conn.execute("INSERT INTO runs (session_id) VALUES (1)")
        await conn.commit()


@pytest.mark.asyncio
async def test_agent_columns_added_after_runs_rebuild(tmp_path: Path) -> None:
    fresh = tmp_path / "fresh.db"
    legacy = tmp_path / "legacy.db"
    await _legacy_db(legacy)
    for path in (fresh, legacy):
        async with aiosqlite.connect(path) as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            await apply_schema(conn)
            await apply_schema(conn)  # second run is a no-op
            runs = await _cols(conn, "runs")
            assert runs["mode"][2:5] == ("TEXT", 1, "'pipeline'")
            assert "agent_record_json" in runs
            sess = await _cols(conn, "chat_sessions")
            assert sess["auto_approve"][2:5] == ("INTEGER", 1, "0")
            pa = await _cols(conn, "pending_actions")
            assert {"id", "run_id", "session_id", "tool", "args_json", "status",
                    "result_json", "created_at"} <= set(pa)
    async with aiosqlite.connect(legacy) as conn, conn.execute("SELECT mode FROM runs") as cur:
        assert [r[0] for r in await cur.fetchall()] == ["pipeline"]


@pytest.mark.asyncio
async def test_pending_actions_status_check_and_cascade(tmp_path: Path) -> None:
    async with aiosqlite.connect(tmp_path / "p.db") as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (title) VALUES ('s')")
        await conn.execute("INSERT INTO runs (session_id) VALUES (1)")
        await conn.execute(
            "INSERT INTO pending_actions (id, run_id, session_id, tool, args_json) "
            "VALUES ('a', 1, 1, 'attach_paper', '{}')"
        )
        async with conn.execute("SELECT status FROM pending_actions") as cur:
            assert (await cur.fetchone())[0] == "pending"  # type: ignore[index]
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO pending_actions (id, run_id, session_id, tool, "
                "args_json, status) VALUES ('b', 1, 1, 't', '{}', 'bogus')"
            )
        await conn.execute("DELETE FROM runs WHERE id = 1")
        async with conn.execute("SELECT count(*) FROM pending_actions") as cur:
            assert (await cur.fetchone())[0] == 0  # type: ignore[index]


async def _fts_ids(conn: aiosqlite.Connection, q: str) -> set[int]:
    async with conn.execute(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", (q,)
    ) as cur:
        return {r[0] for r in await cur.fetchall()}


@pytest.mark.asyncio
async def test_chunks_fts_backfilled_and_kept_in_sync(tmp_path: Path) -> None:
    db = tmp_path / "fts.db"
    async with aiosqlite.connect(db) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        await conn.execute(
            "INSERT INTO paper_content (content_key, kind, arxiv_id, title, source_path, source_dir_path, html_path) "
            "VALUES ('k', 'arxiv', '1', 't', 'p', 'd', 'h')"
        )
        await conn.commit()
        # Simulate a pre-H4 DB: chunks exist, no FTS yet.
        await conn.execute("DROP TRIGGER IF EXISTS chunks_fts_ai")
        await conn.execute("DROP TRIGGER IF EXISTS chunks_fts_ad")
        await conn.execute("DROP TRIGGER IF EXISTS chunks_fts_au")
        await conn.execute("DROP TABLE chunks_fts")
        await conn.execute(
            "INSERT INTO chunks (paper_content_id, char_start, char_end, text) "
            "VALUES (1, 0, 5, 'alpha retrieval'), (1, 5, 9, 'beta gamma')"
        )
        await conn.commit()
        await apply_schema(conn)
        assert await _fts_ids(conn, "retrieval") == {1}
        await conn.execute(
            "INSERT INTO chunks (paper_content_id, char_start, char_end, text) "
            "VALUES (1, 9, 12, 'delta retrieval')"
        )
        assert await _fts_ids(conn, "retrieval") == {1, 3}
        await conn.execute("UPDATE chunks SET text = 'epsilon' WHERE id = 1")
        assert await _fts_ids(conn, "retrieval") == {3}
        assert await _fts_ids(conn, "epsilon") == {1}
        await conn.execute("DELETE FROM chunks WHERE id = 3")
        assert await _fts_ids(conn, "retrieval") == set()
        await apply_schema(conn)  # idempotent, no double backfill
        assert await _fts_ids(conn, "epsilon") == {1}


@pytest.mark.asyncio
async def test_v241_reads_migrated_db(tmp_path: Path) -> None:
    async with aiosqlite.connect(tmp_path / "v.db") as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (title, deleted_at) VALUES ('x', NULL)")
        await conn.execute(
            "INSERT INTO runs (session_id, routing_decision_json, "
            "search_results_json, deck_version_id, started_at, finished_at, status) "
            "VALUES (1, '{}', NULL, NULL, datetime('now'), NULL, 'running')"
        )
        async with conn.execute("SELECT mode, agent_record_json FROM runs") as cur:
            assert tuple(await cur.fetchone()) == ("pipeline", None)  # type: ignore[arg-type]
        async with conn.execute("SELECT auto_approve FROM chat_sessions") as cur:
            assert (await cur.fetchone())[0] == 0  # type: ignore[index]


def test_search_results_reader_accepts_both_shapes() -> None:
    cards = [{"title": "A"}, {"title": "B"}]
    assert load_search_results(json.dumps(cards)) == [cards]
    assert load_search_results(json.dumps({"batches": [cards, cards[:1]]})) == [
        cards, cards[:1]
    ]
    assert load_search_results(None) == []
    assert load_search_results("") == []
    assert load_search_results("not json") == []
    assert load_search_results("{}") == []
