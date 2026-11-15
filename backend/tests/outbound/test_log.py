import asyncio
import csv
import io
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from paperhub.db.migrate import apply_schema, purge_deleted_sessions
from paperhub.outbound import log

pytestmark = pytest.mark.asyncio

KW = {"actor": "agent", "caller": "web_search", "kind": "web_search",
      "stage": "allowlist"}


@pytest_asyncio.fixture
async def conn(tmp_path: Path):
    async with aiosqlite.connect(tmp_path / "t.db") as c:
        await c.execute("PRAGMA foreign_keys = ON")
        await apply_schema(c)
        yield c


async def test_append_and_query_filters(conn: aiosqlite.Connection) -> None:
    a = await log.append(conn, destination="arxiv.org", decision="sent", **KW)
    await log.append(conn, destination="evil.com", decision="blocked",
                     reason="not allowlisted", **KW)
    assert a >= 1
    assert len(await log.query(conn)) == 2
    blocked = await log.query(conn, decision="blocked")
    assert [r.destination for r in blocked] == ["evil.com"]
    assert blocked[0].reason == "not allowlisted"
    assert blocked[0].owner_id == 1
    assert len(await log.query(conn, destination="arxiv.org")) == 1
    assert len(await log.query(conn, since="2999-01-01")) == 0
    assert len(await log.query(conn, limit=1)) == 1


async def test_append_rejects_bad_decision(conn: aiosqlite.Connection) -> None:
    with pytest.raises(aiosqlite.IntegrityError):
        await log.append(conn, destination="x", decision="nope", **KW)


async def test_append_without_conn_opens_workspace_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    async with aiosqlite.connect(tmp_path / "paperhub.db") as c:
        await apply_schema(c)
    rid = await log.append(destination="arxiv.org", decision="sent", **KW)
    async with aiosqlite.connect(tmp_path / "paperhub.db") as c:
        rows = await log.query(c)
    assert [r.id for r in rows] == [rid]


async def test_csv_has_header_and_escapes_quotes(conn: aiosqlite.Connection) -> None:
    await log.append(conn, destination="d", decision="rewritten",
                     sent_text='say "hi", ok\nline2', original_text=None, **KW)
    text = log.to_csv(await log.query(conn))
    parsed = list(csv.reader(io.StringIO(text)))
    assert parsed[0][:3] == ["id", "created_at", "owner_id"]
    assert "sent_text" in parsed[0]
    row = dict(zip(parsed[0], parsed[1], strict=True))
    assert row["sent_text"] == 'say "hi", ok\nline2'
    assert row["original_text"] == ""


async def test_prune_removes_only_older_rows(conn: aiosqlite.Connection) -> None:
    old = await log.append(conn, destination="old", decision="sent", **KW)
    await log.append(conn, destination="new", decision="sent", **KW)
    await conn.execute(
        "UPDATE outbound_log SET created_at = '2000-01-01T00:00:00+00:00' "
        "WHERE id = ?", (old,))
    await conn.commit()
    assert await log.prune(conn, 30) == 1
    assert [r.destination for r in await log.query(conn)] == ["new"]


async def test_rows_survive_session_purge(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        "INSERT INTO chat_sessions (id, title, deleted_at) "
        "VALUES (7, 't', '2000-01-01 00:00:00')")
    await conn.commit()
    await log.append(conn, destination="d", decision="sent", session_id=7, **KW)
    assert await purge_deleted_sessions(conn, 0) == 1
    rows = await log.query(conn)
    assert len(rows) == 1 and rows[0].session_id == 7


async def test_since_accepts_date_space_and_iso(conn: aiosqlite.Connection) -> None:
    await log.append(conn, destination="d", decision="sent", **KW)
    for old in ("2000-01-01", "2000-01-01 00:00:00", "2000-01-01T00:00:00Z"):
        assert len(await log.query(conn, since=old)) == 1
    for future in ("2999-01-01", "2999-01-01 00:00:00", "2999-01-01T00:00:00+00:00"):
        assert len(await log.query(conn, since=future)) == 0
    # same-day boundary: a bare date of today includes today's row
    today = log.utc_now()[:10]
    assert len(await log.query(conn, since=today)) == 1


async def test_prune_loop_prunes_and_stops_promptly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    async with aiosqlite.connect(tmp_path / "paperhub.db") as c:
        await apply_schema(c)
        await log.append(c, destination="old", decision="sent", **KW)
        await c.execute(
            "UPDATE outbound_log SET created_at = '2000-01-01T00:00:00+00:00'")
        await c.commit()
    stop = asyncio.Event()
    task = asyncio.create_task(
        log.run_prune_loop(stop, lambda: 30, interval_s=0.05))
    await asyncio.sleep(0.3)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    async with aiosqlite.connect(tmp_path / "paperhub.db") as c:
        assert await log.query(c) == []


@pytest.mark.parametrize("bad", ["yesterday", "1999", "", "2024-13-45"])
async def test_normalize_since_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError):
        log.normalize_since(bad)


async def test_append_rejects_unknown_actor(conn: aiosqlite.Connection) -> None:
    kw = {**KW, "actor": "root"}
    with pytest.raises(ValueError):
        await log.append(conn, destination="d", decision="sent", **kw)
    for actor in ("user", "agent", "system", "deployer"):
        kw = {**KW, "actor": actor}
        await log.append(conn, destination="d", decision="sent", **kw)
