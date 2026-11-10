"""Tests for the core agent's tool framework and read tools (H4 Task 4)."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import aiosqlite
import pytest

from paperhub.agents.core.read_tools import register_read_tools
from paperhub.agents.core.tools import ToolContext, ToolOutcome, ToolRegistry
from paperhub.llm.adapter import ToolCall
from paperhub.tracing.tracer import Tracer

pytestmark = pytest.mark.asyncio


async def _paper(
    conn: aiosqlite.Connection, key: str, sections: dict[str, list[str]]
) -> tuple[int, dict[str, int]]:
    toc = [
        {"name": n, "char_start": 0, "char_end": 0, "token_count": 1,
         "chunk_count": len(c)}
        for n, c in sections.items()
    ]
    await conn.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, source_path, "
        "source_dir_path, html_path, sections_json) "
        "VALUES (?, 'arxiv', ?, ?, '/x', '/x', '/x', ?)",
        (f"arxiv:{key}", key, f"Title {key}", json.dumps(toc)),
    )
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    pid = int(row[0])
    ids: dict[str, int] = {}
    pos = 0
    for name, texts in sections.items():
        for t in texts:
            await conn.execute(
                "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text) "
                "VALUES (?, ?, ?, ?, ?)",
                (pid, name, pos, pos + len(t), t),
            )
            async with conn.execute("SELECT last_insert_rowid()") as cur:
                r = await cur.fetchone()
            assert r is not None
            ids[t] = int(r[0])
            pos += len(t)
    await conn.commit()
    return pid, ids


def _ctx(
    conn: aiosqlite.Connection, tracer: Tracer, enabled: list[tuple[int, str]]
) -> ToolContext:
    async def emit(_n: str, _p: dict[str, Any]) -> None:
        return None

    return ToolContext(
        conn=conn, tracer=tracer, session_id=1, run_id=1,
        settings=MagicMock(), adapter=MagicMock(), emit=emit,
        enabled_papers=enabled, history=[], state={},
    )


def _reg() -> ToolRegistry:
    r = ToolRegistry()
    register_read_tools(r)
    return r


async def _run(
    r: ToolRegistry, ctx: ToolContext, name: str, **args: Any
) -> ToolOutcome:
    return await r.run(ctx, ToolCall(id="c1", name=name, arguments=args))


async def test_read_section_pages_in_char_start_order(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer
) -> None:
    texts = [f"chunk number {i:02d}" for i in range(30)]
    pid, ids = await _paper(migrated_db, "2401.0001", {"Method": texts})
    ctx = _ctx(migrated_db, fake_tracer, [(pid, "T")])
    out = await _run(_reg(), ctx, "read_section", paper=pid, section="Method", page=2)
    assert out.record["pages"] == 3 and out.record["page"] == 2
    assert out.record["chunk_ids"] == [ids[t] for t in texts[12:24]]
    assert "page 2 of 3" in out.content
    assert out.chunk_ids_read == out.record["chunk_ids"]


async def test_search_chunks_scoping(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer
) -> None:
    a, ia = await _paper(migrated_db, "2401.0001", {"S": ["quantum widgets are great"]})
    b, ib = await _paper(migrated_db, "2401.0002", {"S": ["quantum gadgets are fine"]})
    ctx = _ctx(migrated_db, fake_tracer, [(a, "A")])
    out = await _run(_reg(), ctx, "search_chunks", query="quantum")
    assert out.record["hit_ids"] == [ia["quantum widgets are great"]]
    out2 = await _run(_reg(), ctx, "search_chunks", query="quantum", papers=[b])
    assert out2.record["hit_ids"] == [ib["quantum gadgets are fine"]]


async def test_read_chunks_unknown_id(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer
) -> None:
    pid, ids = await _paper(migrated_db, "2401.0001", {"S": ["hello"]})
    ctx = _ctx(migrated_db, fake_tracer, [(pid, "T")])
    out = await _run(_reg(), ctx, "read_chunks", ids=[ids["hello"], 99999])
    assert "Unknown chunk id(s): 99999" in out.content
    assert "hello" in out.content
    assert out.record["chunk_ids"] == [ids["hello"]]


async def test_paper_not_enabled_names_enabled(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer
) -> None:
    a, _ = await _paper(migrated_db, "2401.0001", {"S": ["x"]})
    b, _ = await _paper(migrated_db, "2401.0002", {"S": ["y"]})
    ctx = _ctx(migrated_db, fake_tracer, [(a, "Paper A")])
    out = await _run(_reg(), ctx, "list_sections", paper=b)
    assert "not enabled" in out.content and "Paper A" in out.content


async def test_no_enabled_papers(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer
) -> None:
    ctx = _ctx(migrated_db, fake_tracer, [])
    msg = "No papers are enabled in this chat; use search_papers or ask the user to add one."
    cases: list[tuple[str, dict[str, Any]]] = [
        ("list_sections", {"paper": 1}),
        ("read_section", {"paper": 1, "section": "S"}),
        ("search_chunks", {"query": "x"}),
        ("list_figures_tables", {"paper": 1}),
        ("read_layout_object", {"paper": 1, "label": "T1"}),
    ]
    for name, args in cases:
        out = await _run(_reg(), ctx, name, **args)
        assert msg in out.content, name


async def test_run_traces_one_row_and_never_raises(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer
) -> None:
    pid, ids = await _paper(migrated_db, "2401.0001", {"Method": ["aaa", "bbb"]})
    ctx = _ctx(migrated_db, fake_tracer, [(pid, "T")])
    r = _reg()
    await _run(r, ctx, "read_section", paper=pid, section="Method")
    bad = await _run(r, ctx, "nope")
    raw = await r.run(ctx, ToolCall(id="c2", name="read_section", arguments={"_raw": "{"}))
    assert "Unknown tool" in bad.content and "not valid JSON" in raw.content
    async with migrated_db.execute(
        "SELECT tool, status, result_summary_json FROM tool_calls ORDER BY step_index"
    ) as cur:
        rows = await cur.fetchall()
    assert rows[0][0] == "tool:read_section" and rows[0][1] == "ok"
    res = json.loads(rows[0][2])
    assert res["chunk_ids"] == [ids["aaa"], ids["bbb"]] and res["status"] == "ok"
    assert rows[1][1] == "error" and rows[2][1] == "error"
    assert len(r.schemas()) == 10 and r.schemas()[0]["type"] == "function"


async def test_search_chunks_bad_papers_limit_clamp_and_bool_paper(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer
) -> None:
    texts = [f"zebra item {i}" for i in range(60)]
    pid, _ = await _paper(migrated_db, "2401.0001", {"S": texts})
    ctx = _ctx(migrated_db, fake_tracer, [(pid, "T")])
    r = _reg()
    bad = await _run(r, ctx, "search_chunks", query="zebra", papers=["abc"])
    assert "'abc'" in bad.content and "failed" not in bad.content
    big = await _run(r, ctx, "search_chunks", query="zebra", limit=1000)
    assert len(big.record["hit_ids"]) == 50
    flag = await _run(r, ctx, "list_sections", paper=True)
    assert "not enabled" in flag.content


async def test_search_chunks_falls_back_to_any_term_then_cjk_substring(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer
) -> None:
    """Review #9: an AND over every word finds nothing for a natural query;
    the tool then ORs the words (bm25 order), and a CJK term (one unicode61
    token for a whole run) falls back to a substring match."""
    a, ids = await _paper(migrated_db, "2401.0901", {
        "Loss": ["sparsity regularization weight", "an ablation of the loss"],
        "中文": ["本文提出稀疏正則化方法來改善分割"],
    })
    ctx = _ctx(migrated_db, fake_tracer, [(a, "A")])
    r = _reg()
    exact = await _run(r, ctx, "search_chunks", query="sparsity regularization")
    assert exact.record["match"] == "all_terms"
    assert exact.record["hit_ids"] == [ids["sparsity regularization weight"]]
    loose = await _run(r, ctx, "search_chunks",
                       query="sparsity regularization loss weight ablation")
    assert loose.record["match"] == "any_term"
    assert set(loose.record["hit_ids"]) == {
        ids["sparsity regularization weight"], ids["an ablation of the loss"]}
    cjk = await _run(r, ctx, "search_chunks", query="稀疏正則化")
    assert cjk.record["match"] == "substring"
    assert cjk.record["hit_ids"] == [ids["本文提出稀疏正則化方法來改善分割"]]
    none = await _run(r, ctx, "search_chunks", query="zebra")
    assert none.record["match"] == "none" and none.record["hit_ids"] == []
