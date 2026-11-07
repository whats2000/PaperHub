"""QA I4: a chat opened from an idea carries the idea on every turn."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import aiosqlite
from httpx import ASGITransport, AsyncClient

from paperhub.agents.chitchat import chitchat_stream
from paperhub.agents.idea_context import build_idea_context, with_idea
from paperhub.agents.slide_context import slide_aware_query
from paperhub.config import load_settings
from paperhub.tracing.tracer import Tracer
from tests.test_chat_sse import _bootstrap_schema, _consume_sse, _wire_test_app


async def _linked(conn: aiosqlite.Connection, *, explore: bool = False) -> tuple[int, int]:
    """(session_id, idea_id) of an idea chat."""
    for n, title in ((1, "AlphaGenome"), (2, "Mamba")):
        await conn.execute(
            "INSERT INTO paper_content (content_key, kind, sha256, title, source_path, "
            "source_dir_path, html_path) VALUES (?, 'pdf_upload', ?, ?, '', '', '')",
            (f"pdf:{n}", f"{n:064d}", title))
    pid = None
    if not explore:
        cur = await conn.execute(
            "INSERT INTO problems (text, status) VALUES ('Summaries of 50k-token documents "
            "invent facts', 'ready')")
        pid = cur.lastrowid
    cur = await conn.execute(
        "INSERT INTO ideas (problem_id, source_doc, source_section, source_chunk_ids_json, "
        "target_doc, target_section, target_chunk_ids_json, title, rationale) "
        "VALUES (?, 1, 'Methods', '[7, 8]', ?, ?, ?, 'Downsample with skip connections', "
        "'Borrow the U-Net of AlphaGenome <Methods>.')",
        (pid, 2 if explore else None, "Discussion" if explore else None,
         "[9]" if explore else "[]"))
    iid = int(cur.lastrowid or 0)
    cur = await conn.execute("INSERT INTO chat_sessions (title) VALUES ('Idea chat')")
    sid = int(cur.lastrowid or 0)
    await conn.execute("INSERT INTO idea_sessions (idea_id, session_id) VALUES (?, ?)",
                       (iid, sid))
    await conn.commit()
    return sid, iid


async def test_unlinked_session_has_no_idea_context(migrated_db: aiosqlite.Connection) -> None:
    cur = await migrated_db.execute("INSERT INTO chat_sessions (title) VALUES ('x')")
    await migrated_db.commit()
    assert await build_idea_context(migrated_db, session_id=int(cur.lastrowid or 0)) is None


async def test_problem_idea_block_has_problem_idea_and_source(
    migrated_db: aiosqlite.Connection,
) -> None:
    sid, iid = await _linked(migrated_db)
    ctx = await build_idea_context(migrated_db, session_id=sid)
    assert ctx is not None and ctx.idea_id == iid
    assert ctx.block.startswith("<idea_context>") and ctx.block.endswith("</idea_context>")
    for part in ("<problem>Summaries of 50k-token documents invent facts</problem>",
                 "<idea_title>Downsample with skip connections</idea_title>",
                 "Borrow the U-Net of AlphaGenome &lt;Methods&gt;.",
                 '<source_paper title="AlphaGenome" section="Methods" />'):
        assert part in ctx.block
    assert "<target_paper" not in ctx.block
    assert ctx.record["source_chunk_ids"] == [7, 8] and ctx.record["problem_id"] is not None


async def test_explore_idea_block_names_the_target(migrated_db: aiosqlite.Connection) -> None:
    sid, _iid = await _linked(migrated_db, explore=True)
    ctx = await build_idea_context(migrated_db, session_id=sid)
    assert ctx is not None
    assert '<target_paper title="Mamba" section="Discussion" />' in ctx.block
    assert "<problem>" not in ctx.block
    assert ctx.record["target_chunk_ids"] == [9]


def test_paper_qa_query_carries_the_idea() -> None:
    q = slide_aware_query({"user_message": "How do I start?",  # type: ignore[typeddict-item]
                           "idea_context": "<idea_context>X</idea_context>"})
    assert q.startswith("<idea_context>X</idea_context>") and q.endswith("How do I start?")
    assert slide_aware_query({"user_message": "plain"}) == "plain"  # type: ignore[typeddict-item]


def test_with_idea_joins_the_blocks() -> None:
    assert with_idea("", None) == ""
    assert with_idea("MEM", None) == "MEM"
    assert with_idea("MEM", "IDEA") == "IDEA\n\nMEM"


class _Capture:
    def __init__(self) -> None:
        self.variables: dict[str, Any] = {}

    def stream(self, **kw: Any) -> AsyncIterator[str]:
        self.variables = kw["variables"]

        async def gen() -> AsyncIterator[str]:
            yield "ok"
        return gen()


async def test_chitchat_sees_the_idea(migrated_db: aiosqlite.Connection) -> None:
    sid, _iid = await _linked(migrated_db)
    run = await migrated_db.execute("INSERT INTO runs (session_id) VALUES (?)", (sid,))
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=int(run.lastrowid or 0), branch="")
    cap = _Capture()
    state: Any = {"session_id": sid, "user_message": "thoughts?",
                  "idea_context": "<idea_context>IDEA</idea_context>"}
    _ = [t async for t in chitchat_stream(state, adapter=cap,  # type: ignore[arg-type]
                                          tracer=tracer, model="m")]
    assert "<idea_context>IDEA</idea_context>" in cap.variables["memory_context"]


async def test_chat_turn_records_the_idea_context(tmp_path: Any, monkeypatch: Any) -> None:
    """Every turn of a linked session — not just the prefilled first one."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_ROUTER_MOCK",
                       '{"intent":"chitchat","confidence":0.9,"reasoning":"x"}')
    monkeypatch.setenv("PAPERHUB_CHITCHAT_MOCK", "Sure.")
    await _bootstrap_schema(tmp_path)
    async with aiosqlite.connect(load_settings().db_path) as conn:
        sid, iid = await _linked(conn)
    app = _wire_test_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        for msg in ("my own question", "a follow-up"):
            async with client.stream("POST", "/chat",
                                     json={"session_id": sid, "user_message": msg}) as r:
                assert r.status_code == 200
                await _consume_sse(r.aiter_bytes())
    async with aiosqlite.connect(load_settings().db_path) as conn, conn.execute(
        "SELECT tc.result_summary_json FROM tool_calls tc JOIN runs r ON r.id = tc.run_id "
        "WHERE r.session_id = ? AND tc.tool = 'ideation:idea_context' ORDER BY r.id",
        (sid,),
    ) as cur:
        rows = [json.loads(r[0]) for r in await cur.fetchall()]
    assert [r["idea_id"] for r in rows] == [iid, iid]
    assert rows[0]["source_section"] == "Methods"
