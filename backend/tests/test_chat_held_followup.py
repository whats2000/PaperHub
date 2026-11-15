"""Plan H5a Task 9: the follow-up turn of an approved held search, and the
single-use approval token the checkpoint honours (ruling R2)."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from paperhub.config import load_settings
from paperhub.db.migrate import apply_schema
from paperhub.llm.adapter import ToolTurn
from paperhub.outbound import checkpoint, held
from paperhub.outbound.context import PrivateItem, add_private, private_material
from paperhub.outbound.models import OutboundRequest
from paperhub.tracing.tracer import Tracer
from tests._research_stubs import StubRegistry, msg
from tests.agent_script import script_agent as _script
from tests.test_chat_agent_flow import _app, _consume, _row

pytestmark = pytest.mark.asyncio

TEXT = "Wafer yield on line 3"
_ENV = ("PAPERHUB_PROFILE", "PAPERHUB_OUTBOUND_REVIEW", "PAPERHUB_OUTBOUND_UNSURE",
        "PAPERHUB_SENSITIVE_TERMS", "PAPERHUB_OUTBOUND_ALLOWLIST",
        "PAPERHUB_TRUSTED_SEARCH_URL", "PAPERHUB_PUBLIC_WEB_SEARCH")


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    import paperhub.api.chat as chat_module

    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")
    monkeypatch.setenv("PAPERHUB_OUTBOUND_REVIEW", "1")
    monkeypatch.setenv("PAPERHUB_OUTBOUND_REVIEW_MODEL", "ollama/review")
    token = private_material.set([])
    chat_module.broker._handles.clear()
    chat_module._live_tasks.clear()
    yield
    private_material.reset(token)
    chat_module.broker._handles.clear()
    chat_module._live_tasks.clear()


class _FailingReview:
    """The review model is down: without the token a query is held (ask)."""

    async def structured(self, **_kw: Any) -> Any:
        raise RuntimeError("review model unreachable")


async def _seed_held(conn: aiosqlite.Connection, *dests: str,
                     text: str = TEXT) -> list[str]:
    await conn.execute("INSERT OR IGNORE INTO chat_sessions (id, title) VALUES (1, 'c')")
    await conn.execute(
        "INSERT OR IGNORE INTO runs (id, session_id, status) VALUES (1, 1, 'ok')")
    await conn.commit()
    return [await held.create(conn, caller="paper_search:resolve",
                              kind="s2_search", destination=d, text=text,
                              reason="unsure", unattended=False, run_id=1,
                              session_id=1)
            for d in dests]


async def _user_sent(conn: aiosqlite.Connection, held_id: str) -> None:
    """The user sends the card (as POST /outbound/held/{id}/send does)."""
    row = await held.get(conn, held_id)
    assert row is not None
    assert held_id in await held.decide(conn, row, "sent", actor="user", sent_text=TEXT)


async def test_approval_token_single_use(tmp_path: Path) -> None:
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_schema(conn)
        h1, h2 = await _seed_held(conn, "api.semanticscholar.org", "public-web-search")
        await _user_sent(conn, h1)  # sends the card: both rows
        (pending,) = await _seed_held(conn, "x.org")
        await conn.execute("INSERT INTO runs (id, session_id) VALUES (2, 1)")
        await conn.commit()
        tracer = Tracer(conn, run_id=2, branch="")
        add_private(PrivateItem("chunk", "c7", "Wafer report", "line 3 yield"))

        async def check(dest: str, *, text: str = TEXT, token: str | None = h1,
                        session_id: int = 1) -> Any:
            return await checkpoint.check(
                OutboundRequest(caller="paper_search:resolve", kind="s2_search",
                                destination=dest, text=text, run_id=2,
                                session_id=session_id),
                conn=conn, adapter=_FailingReview(),  # type: ignore[arg-type]
                tracer=tracer, approved_held_id=token)

        # Another text, another session or no token: the normal stages run.
        assert (await check("api.semanticscholar.org", text="other")).stage != "user"
        assert (await check("api.semanticscholar.org", session_id=9)).stage != "user"
        assert (await check("api.semanticscholar.org", token=None)).stage != "user"
        # A row of the card not yet sent is no approval.
        assert (await check("x.org", token=pending)).stage != "user"

        d = await check("api.semanticscholar.org")
        assert (d.action, d.stage, d.sent_text, d.held_id) == ("send", "user", TEXT, None)
        # Single use: the same approval does not send the text twice.
        again = await check("api.semanticscholar.org")
        assert again.stage != "user" and again.action == "ask"
        # The card's sibling row (other destination) is its own approval.
        web = await check("public-web-search")
        assert (web.action, web.stage) == ("send", "user")
        assert (await check("public-web-search")).stage != "user"

        async with conn.execute(
            "SELECT id, consumed_at IS NOT NULL FROM held_requests ORDER BY id"
        ) as cur:
            consumed = {r[0]: bool(r[1]) for r in await cur.fetchall()}
        assert consumed[h1] and consumed[h2] and not consumed[pending]
        async with conn.execute(
            "SELECT decision, stage, sent_text, actor FROM outbound_log "
            "WHERE stage = 'user' AND decision = 'sent'") as cur:
            assert [tuple(r) for r in await cur.fetchall()] == [
                ("sent", "user", TEXT, "agent")] * 2
        async with conn.execute(
            "SELECT result_summary_json FROM tool_calls WHERE tool = 'outbound:review' "
            "AND run_id = 2") as cur:
            stages = [json.loads(r[0])["stage"] for r in await cur.fetchall()]
        assert stages.count("user") == 2


def _parser_then_quiet() -> AsyncMock:
    """A real-shaped Parser that REWORDS the request (it would miss the
    approval if it ran); every later stage has nothing to add."""
    first = msg(content=json.dumps(
        [{"hint": "line three wafer yields", "kind": "natural_language"}]))
    replies = iter([first])

    async def reply(*_a: Any, **_k: Any) -> dict[str, Any]:
        return next(replies, msg(content="Nothing more."))

    return AsyncMock(side_effect=reply)


async def test_followup_turn_runs_search_with_approved_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with aiosqlite.connect(load_settings().db_path) as conn:
        await apply_schema(conn)
        (hid,) = await _seed_held(conn, "api.semanticscholar.org")
    app = _app()
    reg = StubRegistry(ss_hits=[{"paper_id": "ss:abc", "title": TEXT,
                                 "year": 2024}], has_web_search=False)
    app.state.mcp_registry = reg
    seen = _script(monkeypatch, [ToolTurn(content="Here is what the search found.")])

    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as client:
        # Not yet approved: no follow-up turn.
        early = await client.post("/chat", json={
            "session_id": 1, "user_message": "", "approved_held_id": hid})
        assert early.status_code == 409
        sent = await client.post(f"/outbound/held/{hid}/send", json={})
        assert sent.status_code == 200
        with patch("litellm.acompletion",
                   new=_parser_then_quiet()):
            async with client.stream("POST", "/chat", json={
                "session_id": 1, "user_message": "", "approved_held_id": hid,
            }) as resp:
                assert resp.status_code == 200
                events = await _consume(resp.aiter_bytes())
        # The approval was used: a second follow-up is refused.
        reused = await client.post("/chat", json={
            "session_id": 1, "user_message": "", "approved_held_id": hid})
        assert reused.status_code == 409
        # Another chat cannot spend this chat's approval.
        foreign = await client.post("/chat", json={
            "session_id": 7, "user_message": "", "approved_held_id": hid})
        assert foreign.status_code == 409

    assert dict(events)["final"]["content"] == "Here is what the search found."
    run_id = dict(events)["session"]["run_id"]
    # The search reached Semantic Scholar with exactly the approved text.
    assert [a["query"] for n, a in reg.call_log
            if n == "papers.search_semantic_scholar"] == [TEXT]

    async with (aiosqlite.connect(load_settings().db_path) as conn,
                conn.execute(
                    "SELECT step_index, tool, args_redacted_json, result_summary_json "
                    "FROM tool_calls WHERE run_id = ? ORDER BY step_index",
                    (run_id,)) as cur):
        rows = [(r[0], r[1], json.loads(r[2] or "null"), json.loads(r[3] or "null"))
                for r in await cur.fetchall()]
    tools = [t for _, t, _, _ in rows]
    # The turn's first step is the recorded search, before any model step.
    assert tools.index("tool:search_papers") < tools.index("agent:step")
    (search,) = [(a, r) for _, t, a, r in rows if t == "tool:search_papers"]
    assert search[0]["query"] == TEXT
    assert search[1]["approved_held_id"] == hid
    assert [c["paper_id"] for c in search[1]["candidates"]] == ["ss:abc"]
    reviews = [r for _, t, a, r in rows if t == "outbound:review"
               and a["request"]["text"] == TEXT]
    assert [(r["stage"], r["decision"]) for r in reviews] == [("user", "send")]

    # The model saw the approved search's result before its first step, as
    # user-role context: never as an assistant tool call it did not make
    # (run 914: Gemini rejects those for a missing thought signature).
    first = seen[0]["messages"]
    assert not any(m.get("tool_calls") or m["role"] == "tool" for m in first)
    assert first[-2] == {"role": "user", "content": f"Sent search: {TEXT}"}
    assert first[-1]["role"] == "user"
    assert "search_papers" in first[-1]["content"] and TEXT in first[-1]["content"]
    assert "<approved_search_result" in first[-1]["content"]
    assert "ss:abc" in first[-1]["content"]
    assert "search_results" in [t for t, _ in events]

    (content,) = await _row(
        "SELECT content FROM messages WHERE run_id = ? AND role = 'user'", run_id)
    assert content == f"Sent search: {TEXT}"
    (consumed,) = await _row(
        "SELECT consumed_at IS NOT NULL FROM held_requests WHERE id = ?", hid)
    assert consumed == 1
    (record_raw,) = await _row("SELECT agent_record_json FROM runs WHERE id = ?", run_id)
    record = json.loads(record_raw)
    assert record["tools"][0] == "search_papers"
    assert record["approved_search"] == {"held_id": hid, "text": TEXT}

    # The training trajectory rebuilds the approved search as prompt context.
    from paperhub.feedback.trajectory import agent_trajectory

    async with aiosqlite.connect(load_settings().db_path) as conn:
        traj = await agent_trajectory(conn, run_id)
    prompt = traj.messages[:traj.prompt_len]
    assert prompt[-2]["content"] == f"Sent search: {TEXT}"
    assert prompt[-1] == first[-1]
    assert not any(m.get("tool_calls") for m in prompt)
    assert traj.messages[traj.prompt_len:] == [
        {"role": "assistant", "content": "Here is what the search found."}]


async def test_approval_never_overrides_a_sensitive_term(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A term configured after the user's approval still blocks the text."""
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_schema(conn)
        (h1,) = await _seed_held(conn, "api.semanticscholar.org")
        await _user_sent(conn, h1)
        monkeypatch.setenv("PAPERHUB_SENSITIVE_TERMS", "wafer")
        d = await checkpoint.check(
            OutboundRequest(caller="paper_search:resolve", kind="s2_search",
                            destination="api.semanticscholar.org", text=TEXT,
                            run_id=1, session_id=1),
            conn=conn, adapter=_FailingReview(),  # type: ignore[arg-type]
            tracer=None, approved_held_id=h1)
        assert (d.action, d.stage) == ("block", "terms")
        assert (await held.get(conn, h1)).consumed_at is None  # type: ignore[union-attr]


async def _rows(sql: str, *args: Any) -> list[tuple[Any, ...]]:
    async with (aiosqlite.connect(load_settings().db_path) as conn,
                conn.execute(sql, args) as cur):
        return [tuple(r) for r in await cur.fetchall()]


@pytest.mark.parametrize("card", [
    [("s2_search", "api.semanticscholar.org")],
    [("s2_search", "api.semanticscholar.org"), ("web_search", "public-web-search")],
])
async def test_approved_text_pinned_past_a_rewording_parser(
    monkeypatch: pytest.MonkeyPatch, card: list[tuple[str, str]],
) -> None:
    """R37: the approved text leaves exactly as written to each approved
    destination, once, with stage user, although the Parser rewords and the
    review (down: unsure -> ask) would hold anything else. No new card."""
    from paperhub.llm.litellm_adapter import LiteLlmAdapter

    async def review_down(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("review model unreachable")

    monkeypatch.setattr(LiteLlmAdapter, "structured", review_down)
    monkeypatch.setattr(checkpoint, "current_private", lambda: [
        PrivateItem("chunk", "c7", "Wafer report", "line 3 yield")])
    async with aiosqlite.connect(load_settings().db_path) as conn:
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (id, title) VALUES (1, 'c')")
        await conn.execute("INSERT INTO runs (id, session_id, status) VALUES (1, 1, 'ok')")
        await conn.commit()
        ids = [await held.create(conn, caller="paper_search", kind=k, destination=d,
                                 text=TEXT, reason="unsure", unattended=False,
                                 run_id=1, session_id=1) for k, d in card]
    app = _app()
    reg = StubRegistry(ss_hits=[{"paper_id": "ss:abc", "title": TEXT, "year": 2024}],
                       web_hits=[{"title": "Wafer yield", "url": "https://x.org/a"}],
                       has_web_search=len(card) > 1)
    app.state.mcp_registry = reg
    _script(monkeypatch, [ToolTurn(content="Found it.")])
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as client:
        assert (await client.post(f"/outbound/held/{ids[0]}/send", json={})
                ).status_code == 200
        with patch("litellm.acompletion",
                   new=_parser_then_quiet()):
            async with client.stream("POST", "/chat", json={
                "session_id": 1, "approved_held_id": ids[0]}) as resp:
                assert resp.status_code == 200
                events = await _consume(resp.aiter_bytes())
    assert dict(events)["final"]["content"] == "Found it."
    assert "outbound_held" not in [t for t, _ in events]
    # Each approved destination got exactly the approved text, once.
    assert [a["query"] for n, a in reg.call_log
            if n == "papers.search_semantic_scholar"] == [TEXT]
    assert [a["query"] for n, a in reg.call_log if n == "web.search"] == (
        [TEXT] if len(card) > 1 else [])
    run_id = dict(events)["session"]["run_id"]
    reviews = [json.loads(r[0]) for r in await _rows(
        "SELECT result_summary_json FROM tool_calls WHERE run_id = ? "
        "AND tool = 'outbound:review'", run_id)]
    assert [(r["stage"], r["sent_text"]) for r in reviews] == [("user", TEXT)] * len(card)
    tools = [r[0] for r in await _rows(
        "SELECT tool FROM tool_calls WHERE run_id = ? ORDER BY step_index", run_id)]
    assert "paper_search:parse" not in tools and "paper_search:parse_pinned" in tools
    assert await _rows("SELECT COUNT(*) FROM held_requests") == [(len(card),)]
    assert await _rows(
        "SELECT COUNT(*) FROM held_requests WHERE consumed_at IS NOT NULL") == [(len(card),)]


async def test_racing_followup_turns_reserve_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two follow-up turns of one approval: one reserves it, the other is
    refused before it stores a message or a run."""
    import asyncio

    async with aiosqlite.connect(load_settings().db_path) as conn:
        await apply_schema(conn)
        h1, h2 = await _seed_held(conn, "api.semanticscholar.org", "public-web-search")
        await _user_sent(conn, h1)
        got = await asyncio.gather(
            held.reserve_followup(conn, h1, session_id=1),
            held.reserve_followup(conn, h2, session_id=1))
    # One turn per card, whichever row id it names.
    assert sorted(r is not None for r in got) == [False, True]
    before = await _rows("SELECT COUNT(*) FROM messages")
    runs_before = await _rows("SELECT COUNT(*) FROM runs")
    async with AsyncClient(transport=ASGITransport(app=_app()),
                           base_url="http://test") as client:
        r = await client.post("/chat", json={"session_id": 1, "approved_held_id": h1})
    assert r.status_code == 409
    assert await _rows("SELECT COUNT(*) FROM messages") == before
    assert await _rows("SELECT COUNT(*) FROM runs") == runs_before


async def test_trajectory_of_run_failed_before_first_step() -> None:
    """A run that failed after the approved search, before any agent:step,
    is refused cleanly (TrajectoryError), never an IndexError."""
    from paperhub.feedback.trajectory import TrajectoryError, agent_trajectory

    async with aiosqlite.connect(load_settings().db_path) as conn:
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (id, title) VALUES (1, 'c')")
        await conn.execute(
            "INSERT INTO runs (id, session_id, status, mode) VALUES (1, 1, 'error', 'agent')")
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) "
            "VALUES (1, 'user', 'Sent search: x', 1)")
        # What a failed run stores as its answer (the error text).
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) "
            "VALUES (1, 'assistant', 'The model call failed.', 1)")
        await conn.commit()
        tracer = Tracer(conn, run_id=1, branch="")
        async with tracer.step(agent="agent", tool="tool:search_papers", model=None) as st:
            st.record_args({"query": "x", "call_id": "approved_h"})
            st.record_result({"query": "x", "approved_held_id": "h", "candidates": []})
        with pytest.raises(TrajectoryError, match="recorded no agent step"):
            await agent_trajectory(conn, 1)


async def test_reserve_refused_after_stop_or_delete() -> None:
    async with aiosqlite.connect(load_settings().db_path) as conn:
        await apply_schema(conn)
        (h1,) = await _seed_held(conn, "api.semanticscholar.org")
        await _user_sent(conn, h1)
        await conn.execute(
            "UPDATE chat_sessions SET deleted_at = datetime('now') WHERE id = 1")
        await conn.commit()
        assert await held.reserve_followup(conn, h1, session_id=1) is None
        await conn.execute("UPDATE chat_sessions SET deleted_at = NULL WHERE id = 1")
        await conn.execute("UPDATE runs SET status = 'cancelled' WHERE id = 1")
        await conn.commit()
        assert await held.reserve_followup(conn, h1, session_id=1) is None
        await conn.execute("UPDATE runs SET status = 'ok' WHERE id = 1")
        await conn.commit()
        assert await held.reserve_followup(conn, h1, session_id=1) is not None


async def test_unspent_reservation_released_when_followup_fails_or_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A follow-up turn that fails or is stopped before the approved text
    leaves keeps the approval usable; one that spent it does not."""
    import asyncio

    import paperhub.api.chat as chat_module
    from paperhub.llm.litellm_adapter import LiteLlmAdapter

    async with aiosqlite.connect(load_settings().db_path) as conn:
        await apply_schema(conn)
        (hid,) = await _seed_held(conn, "api.semanticscholar.org")
        await _user_sent(conn, hid)
    settings = load_settings()

    async def run_turn(mcp: Any) -> asyncio.Task[None]:
        async with aiosqlite.connect(settings.db_path) as conn:
            assert await held.reserve_followup(conn, hid, session_id=1) is not None
            run_id = await chat_module._new_run(conn, 1)
        handle = chat_module.broker.register(run_id)
        req = chat_module.ChatRequest(session_id=1, approved_held_id=hid)
        handle.task = asyncio.create_task(chat_module.run_agent(
            handle, 1, run_id, req, settings, LiteLlmAdapter(),
            None, None, None, mcp))
        return handle.task

    async def reservable() -> bool:
        async with aiosqlite.connect(settings.db_path) as conn:
            row = await held.reserve_followup(conn, hid, session_id=1)
            if row is not None:
                await held.release_followup(conn, hid)
            return row is not None

    # 1. Fails: no search registry (nothing sent), then the model errors.
    _script(monkeypatch, [RuntimeError("model down")])
    await (await run_turn(None))
    assert await reservable()

    # 2. Stopped while the approved search is still running.
    started = asyncio.Event()

    async def hang(state: Any, **_kw: Any) -> Any:
        started.set()
        await asyncio.sleep(30)
        yield None  # pragma: no cover

    monkeypatch.setattr(chat_module, "paper_search", hang)
    task = await run_turn(StubRegistry())
    await asyncio.wait_for(started.wait(), timeout=10)
    run_id = max(chat_module.broker._handles)
    await chat_module.cancel_run(chat_module.CancelRequest(run_id=run_id))
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=10)
    assert await reservable()

    # 3. Spent: the approved text left, so the approval is gone.
    async with aiosqlite.connect(settings.db_path) as conn:
        assert await held.reserve_followup(conn, hid, session_id=1) is not None
        await conn.execute(
            "UPDATE held_requests SET consumed_at = datetime('now') WHERE id = ?", (hid,))
        await conn.commit()
        assert await held.release_followup(conn, hid) is False
        assert await held.reserve_followup(conn, hid, session_id=1) is None


async def test_reservation_released_when_the_run_cannot_be_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The endpoint reserved the follow-up turn, then failed to create the
    run: the approval stays usable for the next try."""
    import paperhub.api.chat as chat_module

    async with aiosqlite.connect(load_settings().db_path) as conn:
        await apply_schema(conn)
        (h1,) = await _seed_held(conn, "api.semanticscholar.org")
        await _user_sent(conn, h1)

    async def boom(*_a: Any, **_k: Any) -> int:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(chat_module, "_new_run", boom)
    async with AsyncClient(transport=ASGITransport(app=_app()),
                           base_url="http://test") as client:
        with pytest.raises(RuntimeError):
            await client.post("/chat", json={"session_id": 1, "approved_held_id": h1})
    async with aiosqlite.connect(load_settings().db_path) as conn:
        assert await held.reserve_followup(conn, h1, session_id=1) is not None
