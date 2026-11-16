import aiosqlite
import pytest

from paperhub.db.migrate import apply_schema

pytestmark = pytest.mark.asyncio


async def test_schema_applies_twice_and_backfills_fts(tmp_path):
    async with aiosqlite.connect(str(tmp_path / "t.db")) as conn:
        await apply_schema(conn)
        await apply_schema(conn)
        await conn.execute(
            "INSERT INTO notes (id, target_kind, target_id, created_at, updated_at) "
            "VALUES (1,'paper','1','t','t')")
        for bid, txt, dele in (("b_1", "needle one", None), ("b_2", "needle two", "t")):
            await conn.execute(
                "INSERT INTO note_blocks (id, note_id, order_key, type, payload_json, "
                "text, author, changed_rev, changed_by, created_at, updated_at, "
                "deleted_at) VALUES (?,1,'a0','paragraph','{}',?,'user',1,'user','t','t',?)",
                (bid, txt, dele))
        await conn.commit()
        # Simulate an index that was never populated.
        await conn.execute("INSERT INTO note_blocks_fts(note_blocks_fts) VALUES ('delete-all')")
        await conn.commit()
        await apply_schema(conn)
        async with conn.execute(
            "SELECT b.id FROM note_blocks_fts f JOIN note_blocks b ON b.rowid=f.rowid "
            "WHERE note_blocks_fts MATCH 'needle'") as cur:
            assert [r[0] for r in await cur.fetchall()] == ["b_1"]


async def test_owner_id_guard_adds_missing_column(tmp_path):
    async with aiosqlite.connect(str(tmp_path / "t.db")) as conn:
        await apply_schema(conn)
        await conn.execute("ALTER TABLE note_revisions DROP COLUMN owner_id")
        await conn.commit()
        await apply_schema(conn)
        for tbl in ("note_blocks", "note_block_history", "note_revisions"):
            async with conn.execute(f"PRAGMA table_info({tbl})") as cur:
                assert "owner_id" in {r[1] for r in await cur.fetchall()}, tbl
