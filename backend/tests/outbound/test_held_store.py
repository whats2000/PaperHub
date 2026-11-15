from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from paperhub.db.migrate import apply_schema
from paperhub.outbound import held

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def conn(tmp_path: Path):
    async with aiosqlite.connect(tmp_path / "t.db") as c:
        await apply_schema(c)
        yield c


async def test_held_lifecycle(conn: aiosqlite.Connection) -> None:
    assert await held.count_pending(conn) == 0
    hid = await held.create(
        conn, caller="research", kind="web_search", destination="web",
        text="query", reason="unsure", unattended=True, run_id=3, session_id=4)
    assert await held.count_pending(conn) == 1
    rows = await held.list_pending(conn)
    assert rows[0].id == hid and rows[0].status == "pending"
    assert rows[0].unattended is True and rows[0].text == "query"
    row = await held.get(conn, hid)
    assert row is not None
    assert await held.decide(conn, row, "sent", actor="user", sent_text="query2") == [hid]
    assert await held.count_pending(conn) == 0
    assert await held.list_pending(conn) == []
    async with conn.execute(
        "SELECT status, sent_text, log_id IS NOT NULL FROM held_requests WHERE id = ?", (hid,)
    ) as cur:
        assert await cur.fetchone() == ("sent", "query2", 1)


async def test_unknown_id_is_none(conn: aiosqlite.Connection) -> None:
    assert await held.get(conn, "nope") is None
