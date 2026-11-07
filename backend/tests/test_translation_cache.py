import pytest

from paperhub.db.connection import open_db, write_transaction
from paperhub.db.migrate import apply_schema
from paperhub.translation.cache import get_cached, put_cached


async def _seed(conn) -> int:
    cur = await conn.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, source_path,"
        " source_dir_path, html_path) VALUES ('k','arxiv','1.1','T','s','d','h')"
    )
    await conn.commit()
    return cur.lastrowid


async def _put(conn, *args) -> None:
    async with write_transaction(conn):
        await put_cached(conn, *args)


@pytest.mark.asyncio
async def test_put_cached_leaves_commit_to_the_caller(tmp_path) -> None:
    """put_cached joins the caller's write_transaction; it never commits itself."""
    async with open_db(str(tmp_path / "t.db")) as conn:
        await apply_schema(conn)
        pid = await _seed(conn)
        await put_cached(conn, pid, "h:0", "ja", "s", "m", "x", "small")
        assert conn.in_transaction
        await conn.rollback()
        assert await get_cached(conn, pid, ["h:0"], "ja") == {}


@pytest.mark.asyncio
async def test_put_get_roundtrip_and_replace(tmp_path) -> None:
    async with open_db(str(tmp_path / "t.db")) as conn:
        await apply_schema(conn)
        pid = await _seed(conn)
        await _put(conn, pid, "h:0", "繁體中文", "sh1", "mh1", "你好", "small")
        got = await get_cached(conn, pid, ["h:0", "h:1"], "繁體中文")
        assert set(got) == {"h:0"}
        row = got["h:0"]
        assert (row.source_hash, row.memory_hash, row.text, row.model) == (
            "sh1", "mh1", "你好", "small",
        )
        await _put(conn, pid, "h:0", "繁體中文", "sh2", "mh2", "新", "flagship")
        got = await get_cached(conn, pid, ["h:0"], "繁體中文")
        assert got["h:0"].text == "新" and got["h:0"].model == "flagship"
        cur = await conn.execute("SELECT COUNT(*) FROM translations")
        assert (await cur.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_language_whitespace_shares_key_and_empty_keys(tmp_path) -> None:
    async with open_db(str(tmp_path / "t.db")) as conn:
        await apply_schema(conn)
        pid = await _seed(conn)
        await _put(conn, pid, "p:3", "  繁體中文（香港） ", "s", "m", "x", "small")
        got = await get_cached(conn, pid, ["p:3"], "繁體中文（香港）")
        assert "p:3" in got
        assert await get_cached(conn, pid, [], "繁體中文（香港）") == {}
        assert await get_cached(conn, pid, ["p:3"], "ja") == {}
