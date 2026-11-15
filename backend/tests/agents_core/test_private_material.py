"""Private material in the agent turn (H5a Task 7)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import aiosqlite
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from paperhub.agents.core.private_material import collect_turn_start
from paperhub.agents.core.read_tools import register_read_tools
from paperhub.agents.core.tools import (
    Tier,
    ToolContext,
    ToolOutcome,
    ToolRegistry,
    ToolSpec,
)
from paperhub.app import create_app
from paperhub.config import load_settings
from paperhub.db.migrate import apply_schema
from paperhub.llm.adapter import ToolCall
from paperhub.outbound.context import begin_turn, current_private
from paperhub.tracing.tracer import Tracer

pytestmark = pytest.mark.asyncio


async def _paper(
    conn: aiosqlite.Connection, key: str, texts: list[str], *,
    private: bool, source_dir: str = "/x",
) -> tuple[int, list[int]]:
    await conn.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, source_path, "
        "source_dir_path, html_path, sections_json, is_private) "
        "VALUES (?, 'arxiv', ?, ?, '/x', ?, '/x', ?, ?)",
        (f"arxiv:{key}", key, f"Title {key}", source_dir,
         json.dumps([{"name": "S", "char_start": 0, "char_end": 0,
                      "token_count": 1, "chunk_count": len(texts)}]),
         int(private)),
    )
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    pid = int(row[0])
    ids: list[int] = []
    pos = 0
    for t in texts:
        await conn.execute(
            "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text) "
            "VALUES (?, 'S', ?, ?, ?)", (pid, pos, pos + len(t), t))
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            r = await cur.fetchone()
        assert r is not None
        ids.append(int(r[0]))
        pos += len(t)
    await conn.commit()
    return pid, ids


def _ctx(conn: aiosqlite.Connection, tracer: Tracer,
         enabled: list[tuple[int, str]]) -> ToolContext:
    async def emit(_n: str, _p: dict[str, Any]) -> None:
        return None
    return ToolContext(
        conn=conn, tracer=tracer, session_id=1, run_id=1, settings=MagicMock(),
        adapter=MagicMock(), emit=emit, enabled_papers=enabled, history=[], state={},
    )


def _reg() -> ToolRegistry:
    r = ToolRegistry()
    register_read_tools(r)
    return r


async def _run(r: ToolRegistry, ctx: ToolContext, name: str, **args: Any) -> ToolOutcome:
    return await r.run(ctx, ToolCall(id="c1", name=name, arguments=args))


async def _attach(conn: aiosqlite.Connection, pid: int) -> int:
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    async with conn.execute("SELECT max(id) FROM chat_sessions") as cur:
        row = await cur.fetchone()
    assert row is not None
    sid = int(row[0])
    await conn.execute(
        "INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (?, ?, 1)",
        (sid, pid))
    await conn.commit()
    return sid


async def test_turn_starts_with_attached_private_docs(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    (tmp_path / "digest.json").write_text(
        json.dumps({"abstract": "SECRET ABSTRACT"}), encoding="utf-8")
    priv, _ = await _paper(migrated_db, "1", ["a"], private=True,
                           source_dir=str(tmp_path))
    pub, _ = await _paper(migrated_db, "2", ["b"], private=False)
    sid = await _attach(migrated_db, priv)
    await migrated_db.execute(
        "INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (?, ?, 1)",
        (sid, pub))
    await migrated_db.execute("INSERT INTO problems (text) VALUES ('the idea problem')")
    await migrated_db.execute(
        "INSERT INTO ideas (source_doc, source_section, title, rationale, problem_id) "
        "VALUES (?, 'S', 't', 'r', 1)", (priv,))
    await migrated_db.execute(
        "INSERT INTO idea_sessions (idea_id, session_id) VALUES (1, ?)", (sid,))
    await migrated_db.commit()
    begin_turn()
    await collect_turn_start(migrated_db, sid)
    got = {(i.kind, i.ref): i for i in current_private()}
    assert set(got) == {("document", f"doc:{priv}"), ("problem", "problem:1")}
    assert got[("document", f"doc:{priv}")].title == "Title 1"
    assert got[("document", f"doc:{priv}")].text == "SECRET ABSTRACT"
    assert got[("problem", "problem:1")].text == "the idea problem"


async def test_reading_private_chunk_adds_it(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    pid, ids = await _paper(migrated_db, "1", ["private words here", "other"],
                            private=True)
    ctx = _ctx(migrated_db, fake_tracer, [(pid, "T")])
    begin_turn()
    await _run(_reg(), ctx, "read_chunks", ids=[ids[0]])
    await _run(_reg(), ctx, "search_chunks", query="other")
    got = current_private()
    assert {i.ref for i in got} == {f"chunk:{ids[0]}", f"chunk:{ids[1]}"}
    assert all(i.kind == "chunk" for i in got)
    assert any(i.text == "private words here" for i in got)


async def test_read_section_adds_private_chunks(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    pid, ids = await _paper(migrated_db, "1", ["sec text"], private=True)
    ctx = _ctx(migrated_db, fake_tracer, [(pid, "T")])
    begin_turn()
    await _run(_reg(), ctx, "read_section", paper=pid, section="S")
    assert [i.ref for i in current_private()] == [f"chunk:{ids[0]}"]


async def test_public_paper_reads_add_nothing(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    pid, ids = await _paper(migrated_db, "1", ["public text"], private=False)
    ctx = _ctx(migrated_db, fake_tracer, [(pid, "T")])
    begin_turn()
    await _run(_reg(), ctx, "read_chunks", ids=ids)
    await _run(_reg(), ctx, "search_chunks", query="public")
    assert current_private() == []


async def test_parallel_tool_calls_are_visible_to_the_parent(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    pid, ids = await _paper(migrated_db, "1", ["one", "two"], private=True)
    ctx = _ctx(migrated_db, fake_tracer, [(pid, "T")])
    begin_turn()
    reg = _reg()
    await asyncio.gather(
        asyncio.create_task(_run(reg, ctx, "read_chunks", ids=[ids[0]])),
        asyncio.create_task(_run(reg, ctx, "read_chunks", ids=[ids[1]])),
    )
    assert {i.ref for i in current_private()} == {f"chunk:{i}" for i in ids}


async def test_gather_outcome_chunks_count(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    """Any tool (gather included) reporting chunk_ids_read is collected."""
    pid, ids = await _paper(migrated_db, "1", ["gathered"], private=True)

    async def handler(_c: ToolContext, _a: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(content="x", record={}, chunk_ids_read=ids)

    reg = ToolRegistry()
    reg.register(ToolSpec("gather", "d", {"type": "object", "properties": {}},
                          Tier.FREE, handler))
    begin_turn()
    await _run(reg, _ctx(migrated_db, fake_tracer, [(pid, "T")]), "gather")
    assert [i.ref for i in current_private()] == [f"chunk:{ids[0]}"]


async def _chat_capture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, private_doc: bool,
    seen: list[list[Any]], messages: list[str],
) -> None:
    import paperhub.api.chat as chat_module

    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    async with aiosqlite.connect(load_settings().db_path) as conn:
        await apply_schema(conn)
        if private_doc:
            pid, _ = await _paper(conn, "1", ["a"], private=True)
            await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
            await conn.execute(
                "INSERT INTO papers (session_id, paper_content_id, enabled) "
                "VALUES (1, ?, 1)", (pid,))
            await conn.commit()

    async def fake_drive(
        handle: Any, state: Any, **_kw: Any,
    ) -> tuple[str, dict[str, Any]]:
        seen.append(current_private())
        return "ok", {"tools": [], "read": [], "cited": [], "papers": [],
                      "steps": 0, "ui_events": []}

    monkeypatch.setattr(chat_module, "_drive_core_agent", fake_drive)
    app: FastAPI = create_app()
    app.state.mcp_registry = MagicMock()
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as client:
        for msg in messages:
            body = {"session_id": 1 if private_doc else None, "user_message": msg}
            async with client.stream("POST", "/chat", json=body) as resp:
                async for _ in resp.aiter_bytes():
                    pass


async def test_user_message_is_not_private_material(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    seen: list[list[Any]] = []
    await _chat_capture(monkeypatch, tmp_path, False, seen, ["MY TYPED SECRET"])
    assert seen == [[]]


async def test_material_resets_between_turns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    seen: list[list[Any]] = []
    await _chat_capture(monkeypatch, tmp_path, True, seen, ["one", "two"])
    assert len(seen) == 2
    # Each turn starts from its own fresh list: the one private doc, once.
    assert [[i.ref for i in s] for s in seen] == [["doc:1"], ["doc:1"]]


async def test_chunk_hook_failure_keeps_read_flags_and_traces(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paperhub.agents.core.tools as tools_module
    from paperhub.outbound.context import material_incomplete

    pid, ids = await _paper(migrated_db, "1", ["readable"], private=True)

    async def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("db gone")

    monkeypatch.setattr(tools_module, "collect_chunks", boom)
    ctx = _ctx(migrated_db, fake_tracer, [(pid, "T")])
    begin_turn()
    out = await _run(_reg(), ctx, "read_chunks", ids=ids)
    assert "readable" in out.content  # the read survives
    assert out.record["private_collect_error"] == "RuntimeError"
    assert material_incomplete() is not None
    begin_turn()
    assert material_incomplete() is None  # reset per turn


async def test_turn_survives_turn_start_failure_and_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import paperhub.api.chat as chat_module
    from paperhub.outbound.context import material_incomplete

    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    async with aiosqlite.connect(load_settings().db_path) as conn:
        await apply_schema(conn)

    async def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("db gone")

    flags: list[str | None] = []

    async def fake_drive(
        handle: Any, state: Any, **_kw: Any,
    ) -> tuple[str, dict[str, Any]]:
        flags.append(material_incomplete())
        return "ok", {"tools": [], "read": [], "cited": [], "papers": [],
                      "steps": 0, "ui_events": []}

    monkeypatch.setattr(chat_module, "collect_turn_start", boom)
    monkeypatch.setattr(chat_module, "_drive_core_agent", fake_drive)
    app: FastAPI = create_app()
    app.state.mcp_registry = MagicMock()
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as client, client.stream(
        "POST", "/chat", json={"session_id": None, "user_message": "hi"},
    ) as resp:
        body = b"".join([c async for c in resp.aiter_bytes()])
    assert b"event: final" in body  # the turn completed
    assert flags and "turn-start" in str(flags[0])
    async with aiosqlite.connect(load_settings().db_path) as conn, conn.execute(
        "SELECT result_summary_json FROM tool_calls WHERE tool = 'outbound:collect'",
    ) as cur:
        row = await cur.fetchone()
    assert row is not None and "RuntimeError" in row[0]


# ------------------------------------------------- query_library (final review)


async def _set_abstract(conn: aiosqlite.Connection, pid: int, abstract: str) -> None:
    await conn.execute("UPDATE paper_content SET abstract = ? WHERE id = ?", (abstract, pid))
    await conn.commit()


_PRIV_ABS = "Our internal wafer study found line three yield fell after the reflow change."


async def _library(conn: aiosqlite.Connection) -> tuple[int, list[int], int]:
    priv, chunks = await _paper(conn, "1", ["private chunk text about scribe edge cracks"],
                                private=True)
    await conn.execute("UPDATE paper_content SET title = ? WHERE id = ?",
                       ("Internal Wafer Yield Review", priv))
    await _set_abstract(conn, priv, _PRIV_ABS)
    pub, _ = await _paper(conn, "2", ["public chunk"], private=False)
    await _set_abstract(conn, pub, "A public abstract about sparse attention models.")
    return priv, chunks, pub


async def test_query_library_rows_of_a_private_paper_are_tracked(
    migrated_db: aiosqlite.Connection,
) -> None:
    from paperhub.agents.core.private_material import collect_library_rows
    from paperhub.outbound.context import material_incomplete

    priv, chunks, pub = await _library(migrated_db)
    begin_turn()
    await collect_library_rows(migrated_db, [], [
        (["title", "abstract"], [["Internal Wafer Yield Review", _PRIV_ABS],
                                 ["Title 2", "A public abstract about sparse attention models."]]),
        (["pc.paper_content_id", "year"], [[priv, 2024]]),
        (["chunk_id"], [[chunks[0]]]),
        (["abstract"], [[_PRIV_ABS[:40]]]),  # a substring: attributed by lookup
    ])
    items = current_private()
    texts = " ".join(i.text for i in items)
    assert _PRIV_ABS in texts
    assert "scribe edge cracks" in texts  # chunk_id column → the chunk
    assert all(i.title in ("Internal Wafer Yield Review",) for i in items)
    assert "public abstract" not in texts
    assert material_incomplete() is None


async def test_query_library_candidates_add_the_private_document(
    migrated_db: aiosqlite.Connection,
) -> None:
    from paperhub.agents.core.private_material import collect_library_rows

    priv, _, pub = await _library(migrated_db)
    begin_turn()
    await collect_library_rows(migrated_db, [pub, priv], [])
    assert [(i.kind, i.ref, i.title) for i in current_private()] == [
        ("document", f"doc:{priv}", "Internal Wafer Yield Review")]


async def test_query_library_unattributable_long_text_marks_incomplete(
    migrated_db: aiosqlite.Connection,
) -> None:
    from paperhub.agents.core.private_material import collect_library_rows
    from paperhub.outbound.context import material_incomplete

    await _library(migrated_db)
    begin_turn()
    # A concatenation (title || abstract): no single field contains it.
    await collect_library_rows(migrated_db, [], [
        (["blurb"], [["Internal Wafer Yield Review: " + _PRIV_ABS]])])
    assert current_private() != []  # the title inside still attributes it
    begin_turn()
    await collect_library_rows(migrated_db, [], [
        (["blurb"], [["x" * 20 + " " + _PRIV_ABS[::-1]]])])
    assert material_incomplete() is not None


async def test_query_library_without_private_papers_does_nothing(
    migrated_db: aiosqlite.Connection,
) -> None:
    from paperhub.agents.core.private_material import collect_library_rows
    from paperhub.outbound.context import material_incomplete

    await _paper(migrated_db, "2", ["public chunk"], private=False)
    begin_turn()
    await collect_library_rows(migrated_db, [], [(["blurb"], [["y" * 200]])])
    assert current_private() == [] and material_incomplete() is None
