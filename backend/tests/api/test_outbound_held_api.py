"""Plan H5a Task 9: send, edit or discard a held search."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from paperhub import settings_overlay as ov
from paperhub.app import create_app
from paperhub.config import load_settings
from paperhub.db.migrate import apply_schema
from paperhub.outbound import held

pytestmark = pytest.mark.asyncio

TEXT = "wafer yield on line 3"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    import paperhub.api.chat as chat_module

    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")
    monkeypatch.setenv("PAPERHUB_SENSITIVE_TERMS", "Kestrel")
    chat_module.broker._handles.clear()
    held._RESENDERS.clear()
    ov.reset_for_tests()
    yield
    chat_module.broker._handles.clear()
    held._RESENDERS.clear()
    ov.reset_for_tests()


async def _seed() -> dict[str, str]:
    """Session 1 with a message, run 1 still running: two held rows of one
    card (same text, two destinations) and one row of another card."""
    async with aiosqlite.connect(load_settings().db_path) as conn:
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (id, title) VALUES (1, 'chat')")
        await conn.execute(
            "INSERT INTO runs (id, session_id, status) VALUES (1, 1, 'running')")
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) "
            "VALUES (1, 'user', 'find it', 1)")
        await conn.commit()
        ids = {}
        for key, text, dest in (("h1", TEXT, "api.semanticscholar.org"),
                                ("h2", TEXT, "public-web-search"),
                                ("other", "another query", "api.semanticscholar.org")):
            ids[key] = await held.create(
                conn, caller="paper_search:resolve", kind="s2_search",
                destination=dest, text=text, reason="unsure", unattended=False,
                run_id=1, session_id=1)
    return ids


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as c:
        yield c


async def _rows(sql: str, *args: Any) -> list[tuple[Any, ...]]:
    async with (aiosqlite.connect(load_settings().db_path) as conn,
                conn.execute(sql, args) as cur):
        return [tuple(r) for r in await cur.fetchall()]


async def _held(hid: str) -> tuple[Any, ...]:
    (row,) = await _rows(
        "SELECT status, sent_text, log_id IS NOT NULL FROM held_requests WHERE id = ?",
        hid)
    return row


async def _log() -> list[tuple[Any, ...]]:
    return await _rows(
        "SELECT actor, decision, stage, destination, sent_text, original_text, "
        "run_id, session_id FROM outbound_log ORDER BY id")


async def test_send_as_shown(client: AsyncClient) -> None:
    ids = await _seed()
    r = await client.post(f"/outbound/held/{ids['h1']}/send", json={})
    assert r.status_code == 200
    # One card: sending one id resolves its siblings (same text, same run).
    assert r.json() == {"status": "sent", "held_id": ids["h1"],
                        "held_ids": [ids["h1"], ids["h2"]], "text": TEXT,
                        "resumed": False}
    assert await _held(ids["h1"]) == ("sent", TEXT, 1)
    assert await _held(ids["h2"]) == ("sent", TEXT, 1)
    assert (await _held(ids["other"]))[0] == "pending"
    assert await _log() == [
        ("user", "asked_sent", "user", "api.semanticscholar.org", TEXT, None, 1, 1),
        ("user", "asked_sent", "user", "public-web-search", TEXT, None, 1, 1),
    ]
    # Already sent: nothing left to decide.
    again = await client.post(f"/outbound/held/{ids['h2']}/send", json={})
    assert again.status_code == 409


async def test_send_edited_rechecks_terms(client: AsyncClient) -> None:
    ids = await _seed()
    r = await client.post(f"/outbound/held/{ids['h1']}/send",
                          json={"text": "kestrel yield"})
    assert r.status_code == 422
    assert r.json() == {"detail": "sensitive_term", "term": "Kestrel"}
    assert (await _held(ids["h1"]))[0] == "pending"
    assert await _log() == []

    r = await client.post(f"/outbound/held/{ids['h1']}/send",
                          json={"text": "  yield modelling  "})
    assert r.status_code == 200
    assert r.json()["text"] == "yield modelling"
    assert await _held(ids["h1"]) == ("sent", "yield modelling", 1)
    assert await _held(ids["h2"]) == ("sent", "yield modelling", 1)
    assert [row[4:6] for row in await _log()] == [
        ("yield modelling", TEXT), ("yield modelling", TEXT)]

    other = await client.post(f"/outbound/held/{ids['other']}/send",
                              json={"text": "   "})
    assert other.status_code == 422


async def test_discard(client: AsyncClient) -> None:
    ids = await _seed()
    r = await client.post(f"/outbound/held/{ids['h2']}/discard")
    assert r.status_code == 200
    assert r.json() == {"status": "discarded", "held_id": ids["h2"],
                        "held_ids": [ids["h2"], ids["h1"]], "resumed": False}
    assert (await _held(ids["h1"]))[0] == "discarded"
    assert (await _held(ids["h2"]))[0] == "discarded"
    assert (await _held(ids["other"]))[0] == "pending"
    log = await _log()
    assert [(a, d, s) for a, d, s, *_ in log] == [
        ("user", "asked_discarded", "user")] * 2
    assert all(row[4] is None and row[5] == TEXT for row in log)
    assert (await client.post(f"/outbound/held/{ids['h1']}/send", json={})
            ).status_code == 409
    assert (await client.post(f"/outbound/held/{ids['h1']}/discard")
            ).status_code == 409
    assert (await client.post("/outbound/held/nope/discard")).status_code == 404
    assert (await client.post("/outbound/held/nope/send", json={})).status_code == 404


async def test_send_refused_after_stop_or_delete(client: AsyncClient) -> None:
    ids = await _seed()
    # Stop: the run's pending held rows are discarded by the system.
    r = await client.post("/chat/cancel", json={"run_id": 1})
    assert r.status_code == 200
    for key in ("h1", "h2", "other"):
        assert (await _held(ids[key]))[0] == "discarded"
        assert (await client.post(f"/outbound/held/{ids[key]}/send", json={})
                ).status_code == 409
    assert {(a, d) for a, d, *_ in await _log()} == {("system", "asked_discarded")}

    # A row still pending on a cancelled run (e.g. left by an older build).
    async with aiosqlite.connect(load_settings().db_path) as conn:
        late = await held.create(
            conn, caller="paper_search:resolve", kind="s2_search",
            destination="api.semanticscholar.org", text="late", reason="r",
            unattended=False, run_id=1, session_id=1)
    assert (await client.post(f"/outbound/held/{late}/send", json={})
            ).status_code == 409
    assert (await client.post(f"/outbound/held/{late}/discard")).status_code == 409

    # Session delete: its pending held rows are discarded too.
    async with aiosqlite.connect(load_settings().db_path) as conn:
        await conn.execute("INSERT INTO chat_sessions (id, title) VALUES (2, 'two')")
        await conn.execute(
            "INSERT INTO runs (id, session_id, status) VALUES (2, 2, 'ok')")
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) "
            "VALUES (2, 'user', 'hi', 2)")
        await conn.commit()
        in_two = await held.create(
            conn, caller="paper_search:resolve", kind="s2_search",
            destination="api.semanticscholar.org", text="q2", reason="r",
            unattended=False, run_id=2, session_id=2)
        orphan = await held.create(
            conn, caller="paper_search:resolve", kind="s2_search",
            destination="api.semanticscholar.org", text="q3", reason="r",
            unattended=False, run_id=2, session_id=2)
    assert (await client.delete("/sessions/2")).status_code == 204
    assert (await _held(in_two))[0] == "discarded"
    assert (await client.post(f"/outbound/held/{in_two}/send", json={})
            ).status_code == 409
    # Pending on a soft-deleted session (written after the delete): refused.
    async with aiosqlite.connect(load_settings().db_path) as conn:
        await conn.execute(
            "UPDATE held_requests SET status = 'pending' WHERE id = ?", (orphan,))
        await conn.commit()
    assert (await client.post(f"/outbound/held/{orphan}/send", json={})
            ).status_code == 409


async def test_unattended_send_uses_registered_resender(client: AsyncClient) -> None:
    await _seed()
    async with aiosqlite.connect(load_settings().db_path) as conn:
        a = await held.create(conn, caller="trend_watch", kind="s2_search",
                              destination="api.semanticscholar.org", text="t1",
                              reason="r", unattended=True, run_id=None,
                              session_id=None)
        b = await held.create(conn, caller="nobody", kind="s2_search",
                              destination="api.semanticscholar.org", text="t2",
                              reason="r", unattended=True, run_id=None,
                              session_id=None)
    sent: list[tuple[str, str]] = []

    async def resend(row: held.HeldRow, text: str) -> None:
        sent.append((row.id, text))

    held.register_resender("trend_watch", resend)
    r = await client.post(f"/outbound/held/{a}/send", json={"text": "t1 edited"})
    assert r.status_code == 200 and r.json()["held_ids"] == [a]
    assert sent == [(a, "t1 edited")]
    # No resender registered for this caller: nothing could send it, so it
    # is refused and stays pending (never logged as sent).
    r = await client.post(f"/outbound/held/{b}/send", json={})
    assert r.status_code == 409 and r.json()["detail"] == "no_resender"
    assert (await _held(b))[0] == "pending"
    async with aiosqlite.connect(load_settings().db_path) as conn, conn.execute(
        "SELECT COUNT(*) FROM outbound_log WHERE original_text = 't2' "
        "OR sent_text = 't2'") as cur:
        assert (await cur.fetchone()) == (0,)


async def test_failed_resend_keeps_row_pending(client: AsyncClient) -> None:
    await _seed()
    async with aiosqlite.connect(load_settings().db_path) as conn:
        a = await held.create(conn, caller="trend_watch", kind="s2_search",
                              destination="api.semanticscholar.org", text="t1",
                              reason="r", unattended=True, run_id=None,
                              session_id=None)

    async def boom(_row: held.HeldRow, _text: str) -> None:
        raise RuntimeError("down")

    held.register_resender("trend_watch", boom)
    r = await client.post(f"/outbound/held/{a}/send", json={})
    assert r.status_code == 502
    assert (await _held(a))[0] == "pending"


async def test_replayed_card_shows_decision(client: AsyncClient) -> None:
    ids = await _seed()
    card = {"held_id": ids["h1"], "held_ids": [ids["h1"], ids["h2"]], "text": TEXT,
            "reason": "unsure", "destination": "api.semanticscholar.org",
            "status": "pending"}
    other = {**card, "held_id": ids["other"], "held_ids": [ids["other"]],
             "text": "another query"}
    record = {"tools": ["search_papers"], "read": [], "cited": [], "papers": [],
              "steps": 2, "ui_events": [{"event": "outbound_held", "data": card},
                                        {"event": "outbound_held", "data": other}]}
    async with aiosqlite.connect(load_settings().db_path) as conn:
        await conn.execute(
            "UPDATE runs SET status = 'ok', mode = 'agent', agent_record_json = ? "
            "WHERE id = 1", (json.dumps(record),))
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) "
            "VALUES (1, 'assistant', 'Waiting on your approval.', 1)")
        await conn.commit()
    assert (await client.post(f"/outbound/held/{ids['h1']}/send",
                              json={"text": "yield modelling"})).status_code == 200
    msgs = (await client.get("/sessions/1/messages")).json()
    (assistant,) = [m for m in msgs if m["role"] == "assistant"]
    cards = [e["data"] for e in assistant["agent_events"]]
    assert [(c["held_id"], c["status"]) for c in cards] == [
        (ids["h1"], "sent"), (ids["other"], "pending")]
    assert cards[0]["sent_text"] == "yield modelling"
    assert cards[0]["ran"] is False  # approved, follow-up not started
    async with aiosqlite.connect(load_settings().db_path) as conn:
        await conn.execute(
            "UPDATE held_requests SET reserved_at = datetime('now') WHERE id = ?",
            (ids["h1"],))
        await conn.commit()
    msgs = (await client.get("/sessions/1/messages")).json()
    (assistant,) = [m for m in msgs if m["role"] == "assistant"]
    assert assistant["agent_events"][0]["data"]["ran"] is True


async def test_concurrent_unattended_send_resends_once(client: AsyncClient) -> None:
    """A double click / two tabs: the resender runs once, after the claim."""
    import asyncio

    await _seed()
    async with aiosqlite.connect(load_settings().db_path) as conn:
        a = await held.create(conn, caller="trend_watch", kind="s2_search",
                              destination="api.semanticscholar.org", text="t1",
                              reason="r", unattended=True, run_id=None,
                              session_id=None)
    sent: list[str] = []

    async def slow(row: held.HeldRow, text: str) -> None:
        await asyncio.sleep(0.05)
        sent.append(text)

    held.register_resender("trend_watch", slow)
    r1, r2 = await asyncio.gather(
        client.post(f"/outbound/held/{a}/send", json={}),
        client.post(f"/outbound/held/{a}/send", json={}))
    assert sorted([r1.status_code, r2.status_code]) == [200, 409]
    assert sent == ["t1"]
    assert [row[1] for row in await _log()] == ["asked_sent"]


async def test_lost_anchor_claim_decides_nothing() -> None:
    """Send vs discard racing on one card: when the anchor was already
    decided, the loser decides no sibling (the card never splits)."""
    ids = await _seed()
    async with aiosqlite.connect(load_settings().db_path) as conn:
        stale = await held.get(conn, ids["h1"])  # read before the race
        assert stale is not None
        assert await held.decide(conn, stale, "discarded", actor="user") == [
            ids["h1"], ids["h2"]]
        # The racing send still holds its stale (pending) view of h1.
        assert await held.decide(conn, stale, "sent", actor="user",
                                 sent_text=TEXT) == []
    assert (await _held(ids["h1"]))[0] == "discarded"
    assert (await _held(ids["h2"]))[0] == "discarded"


async def test_send_discard_race_on_one_card(client: AsyncClient) -> None:
    import asyncio

    ids = await _seed()
    r1, r2 = await asyncio.gather(
        client.post(f"/outbound/held/{ids['h1']}/send", json={}),
        client.post(f"/outbound/held/{ids['h2']}/discard"))
    assert sorted([r1.status_code, r2.status_code]) == [200, 409]
    assert (await _held(ids["h1"]))[0] == (await _held(ids["h2"]))[0]


async def test_rows_of_stopped_runs_leave_the_queue(client: AsyncClient) -> None:
    """A stopped run that writes a held row after /chat/cancel committed, or
    a row of a deleted chat: never counted, discarded on the next read."""
    ids = await _seed()
    assert (await client.get("/outbound/held/count")).json() == {"count": 3}
    assert (await client.post("/chat/cancel", json={"run_id": 1})).status_code == 200
    async with aiosqlite.connect(load_settings().db_path) as conn:
        late = await held.create(
            conn, caller="paper_search:resolve", kind="s2_search",
            destination="api.semanticscholar.org", text="late", reason="r",
            unattended=False, run_id=1, session_id=1)
        assert await held.count_pending(conn) == 0  # excluded even unswept
        await conn.execute("INSERT INTO chat_sessions (id, title, deleted_at) "
                           "VALUES (5, 'gone', datetime('now'))")
        await conn.commit()
        gone = await held.create(
            conn, caller="paper_search:resolve", kind="s2_search",
            destination="api.semanticscholar.org", text="g", reason="r",
            unattended=False, run_id=None, session_id=5)
        assert await held.list_pending(conn) == []
    assert (await client.get("/outbound/held/count")).json() == {"count": 0}
    assert (await client.get("/outbound/held")).json() == []
    assert (await _held(late))[0] == "discarded"
    assert (await _held(gone))[0] == "discarded"
    assert ids  # the seeded rows were discarded by the cancel itself


async def test_stop_between_check_and_claim_lets_no_send_land() -> None:
    """A Stop (or chat delete) committing after the endpoint's checks but
    before the claim: the claim itself refuses, nothing is decided."""
    ids = await _seed()
    async with aiosqlite.connect(load_settings().db_path) as conn:
        view = await held.get(conn, ids["h1"])  # what _decidable saw
        assert view is not None
        await conn.execute("UPDATE runs SET status = 'cancelled' WHERE id = 1")
        await conn.commit()
        assert await held.decide(conn, view, "sent", actor="user", sent_text=TEXT) == []
        assert (await _held(ids["h1"]))[0] == "pending"
        assert (await _held(ids["h2"]))[0] == "pending"
        # The system may still discard what the stop left behind.
        assert sorted(await held.sweep_stale(conn)) == sorted(ids.values())

        # Same for a chat deleted between the checks and the claim.
        await conn.execute("INSERT INTO chat_sessions (id, title) VALUES (3, 't')")
        await conn.commit()
        row_id = await held.create(
            conn, caller="c", kind="s2_search", destination="api.semanticscholar.org",
            text="q", reason="r", unattended=False, run_id=None, session_id=3)
        view = await held.get(conn, row_id)
        assert view is not None
        await conn.execute(
            "UPDATE chat_sessions SET deleted_at = datetime('now') WHERE id = 3")
        await conn.commit()
        assert await held.decide(conn, view, "discarded", actor="user") == []


async def test_resend_in_flight_is_sending_then_sent(client: AsyncClient) -> None:
    """While the resender runs, the rows are 'sending' (not pending, no log
    row yet); success makes them 'sent' with one log row each."""
    await _seed()
    async with aiosqlite.connect(load_settings().db_path) as conn:
        a = await held.create(conn, caller="trend_watch", kind="s2_search",
                              destination="api.semanticscholar.org", text="t1",
                              reason="r", unattended=True, run_id=None,
                              session_id=None)
    seen: list[tuple[Any, ...]] = []

    async def look(row: held.HeldRow, text: str) -> None:
        seen.append(await _held(row.id))
        seen.append(((await _log()) == [],))
        async with aiosqlite.connect(load_settings().db_path) as conn:
            seen.append((await held.count_pending(conn),))

    held.register_resender("trend_watch", look)
    r = await client.post(f"/outbound/held/{a}/send", json={})
    assert r.status_code == 200
    # 'sending' with no log row, and not counted as pending (3 seeded rows).
    assert seen == [("sending", "t1", 0), (True,), (3,)]
    assert await _held(a) == ("sent", "t1", 1)
    assert (await client.post(f"/outbound/held/{a}/send", json={})).status_code == 409


async def test_crashed_resend_returns_to_pending_after_timeout() -> None:
    await _seed()
    async with aiosqlite.connect(load_settings().db_path) as conn:
        old = await held.create(conn, caller="trend_watch", kind="s2_search",
                                destination="api.semanticscholar.org", text="old",
                                reason="r", unattended=True, run_id=None,
                                session_id=None)
        new = await held.create(conn, caller="trend_watch", kind="s2_search",
                                destination="api.semanticscholar.org", text="new",
                                reason="r", unattended=True, run_id=None,
                                session_id=None)
        await conn.execute(
            "UPDATE held_requests SET status = 'sending', sent_text = text, "
            "sending_at = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", old))
        await conn.execute(
            "UPDATE held_requests SET status = 'sending', sent_text = text, "
            "sending_at = ? WHERE id = ?", (held.utc_now(), new))
        await conn.commit()
        assert await held.count_pending(conn) == 3
        await held.sweep_stale(conn)
        assert await held.count_pending(conn) == 4
    assert await _held(old) == ("pending", None, 0)
    assert (await _held(new))[0] == "sending"


async def test_old_held_table_gains_the_sending_status(tmp_path: Path) -> None:
    """A DB created before 'sending' is rebuilt (rows kept), idempotently."""
    old_ddl = (
        "CREATE TABLE held_requests (id TEXT PRIMARY KEY, created_at TEXT, "
        "owner_id INTEGER NOT NULL DEFAULT 1, caller TEXT, kind TEXT, "
        "destination TEXT, text TEXT NOT NULL, reason TEXT, unattended INTEGER "
        "NOT NULL, run_id INTEGER, session_id INTEGER, status TEXT NOT NULL "
        "CHECK (status IN ('pending','sent','discarded')), sent_text TEXT, "
        "log_id INTEGER)")
    async with aiosqlite.connect(tmp_path / "old.db") as conn:
        await conn.execute(old_ddl)
        await conn.execute(
            "INSERT INTO held_requests (id, text, unattended, status) "
            "VALUES ('x', 'q', 0, 'pending')")
        await conn.commit()
        await apply_schema(conn)
        await apply_schema(conn)
        await conn.execute("UPDATE held_requests SET status = 'sending' WHERE id = 'x'")
        await conn.commit()
        row = await held.get(conn, "x")
        assert row is not None and row.status == "sending" and row.text == "q"


async def test_followup_turn_replays_its_approved_held_id(client: AsyncClient) -> None:
    """The user message of a held follow-up carries the id it ran, so Retry
    never has to guess from text."""
    await _seed()
    async with aiosqlite.connect(load_settings().db_path) as conn:
        await conn.execute(
            "INSERT INTO runs (id, session_id, status, mode, approved_held_id) "
            "VALUES (2, 1, 'ok', 'agent', 'held-abc')")
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) "
            "VALUES (1, 'user', 'Sent search: q', 2)")
        await conn.commit()
    msgs = (await client.get("/sessions/1/messages")).json()
    by_run = {m["run_id"]: m for m in msgs if m["role"] == "user"}
    assert by_run[2]["approved_held_id"] == "held-abc"
    assert by_run[1]["approved_held_id"] is None


async def test_a_decision_goes_to_the_paused_run(client: AsyncClient) -> None:
    """H5a: a live run waits on the card: the endpoint hands the decision
    to it (every row of the card) and answers ``resumed: true``."""
    import asyncio

    from paperhub import approvals

    ids = await _seed()
    approvals.open_run(1)
    try:
        waits = [asyncio.create_task(approvals.wait(
            run_id=1, kind="held", row_id=ids[k], card_id=ids["h1"], tracer=None))
            for k in ("h1", "h2", "other")]
        await asyncio.sleep(0.01)
        r = await client.post(f"/outbound/held/{ids['h1']}/send",
                              json={"text": "yield modelling"})
        assert r.status_code == 200 and r.json()["resumed"] is True
        assert [await w for w in waits[:2]] == [
            approvals.ApprovalDecision(True, "yield modelling", edited=True)] * 2
        r = await client.post(f"/outbound/held/{ids['other']}/discard")
        assert r.json()["resumed"] is True
        assert await waits[2] == approvals.ApprovalDecision(False)
    finally:
        approvals.close_run(1)
