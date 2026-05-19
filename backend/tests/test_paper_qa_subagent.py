"""Tests for the per-paper paper_qa subagent (Plan C v2.10-3).

Covers:
- Happy path: list_sections → read two sections → cite chunks → return PerPaperPicks
- Budget exhaustion: force-stop returns all seen chunks as best-effort fallback
- Unknown section: returns error dict to LLM, no crash, empty picks
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

pytestmark = pytest.mark.asyncio


# ─────────────────────────── helpers ────────────────────────────────


def _msg(
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    m: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    return {"choices": [{"message": m}]}


def _tool_call(call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


async def _seed_paper_with_sections(
    conn: aiosqlite.Connection,
    *,
    arxiv_id: str = "2401.0001",
    title: str = "Test Paper",
    sections: list[dict[str, Any]],
    sections_json: list[dict[str, Any]] | None = None,
) -> tuple[int, dict[str, int]]:
    """Insert a paper_content row with chunks grouped by section.

    ``sections`` is a list of ``{"name": str, "chunks": [str, ...]}``.
    Returns (paper_content_id, {chunk_text: chunk_id}).
    """
    # Build sections_json TOC if not provided explicitly.
    toc: list[dict[str, Any]] = []
    all_chunks: list[tuple[str, str]] = []  # (section_name, text)
    char_cursor = 0
    for sec in sections:
        sec_start = char_cursor
        for txt in sec["chunks"]:
            all_chunks.append((sec["name"], txt))
            char_cursor += len(txt)
        toc.append({
            "name": sec["name"],
            "char_start": sec_start,
            "char_end": char_cursor,
            "token_count": len(sec["chunks"]) * 20,
            "chunk_count": len(sec["chunks"]),
        })
    if sections_json is not None:
        toc = sections_json

    await conn.execute(
        "INSERT INTO paper_content "
        "(content_key, kind, arxiv_id, title, authors_json, year, abstract, "
        "source_path, source_dir_path, html_path, sections_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"arxiv:{arxiv_id}", "arxiv", arxiv_id, title,
            "[]", 2024, "abstract",
            "/tmp/x.tex", "/tmp", "/tmp/x.html",
            json.dumps(toc),
        ),
    )
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    pcid = int(row[0])

    chunk_id_map: dict[str, int] = {}
    char_start = 0
    for sec_name, txt in all_chunks:
        char_end = char_start + len(txt)
        await conn.execute(
            "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text) "
            "VALUES (?, ?, ?, ?, ?)",
            (pcid, sec_name, char_start, char_end, txt),
        )
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            cr = await cur.fetchone()
        assert cr is not None
        chunk_id_map[txt] = int(cr[0])
        char_start = char_end
    await conn.commit()
    return pcid, chunk_id_map


# ─────────────────────────── tests ──────────────────────────────────


async def test_subagent_loop_lists_sections_reads_picks_chunks_and_stops(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """Happy path: subagent lists sections, reads two relevant ones, cites
    chunks via [chunk:N] markers in its final no-tool-calls message."""
    pcid, cid_map = await _seed_paper_with_sections(
        migrated_db,
        title="Attention Is All You Need",
        sections=[
            {"name": "Method", "chunks": ["Transformer architecture details."]},
            {"name": "Experiments", "chunks": ["BLEU score results on WMT."]},
        ],
    )
    method_cid = cid_map["Transformer architecture details."]
    exp_cid = cid_map["BLEU score results on WMT."]

    # LLM sequence:
    # Turn 1: tool_calls=[list_sections()]
    # Turn 2: tool_calls=[read_section("Method")]
    # Turn 3: tool_calls=[read_section("Experiments")]
    # Turn 4: no tool_calls, final summary with cites
    responses = [
        _msg(tool_calls=[_tool_call("c1", "list_sections", {})]),
        _msg(tool_calls=[_tool_call("c2", "read_section", {"name": "Method"})]),
        _msg(tool_calls=[_tool_call("c3", "read_section", {"name": "Experiments"})]),
        _msg(content=f"Paper covers architecture [chunk:{method_cid}] and evaluation [chunk:{exp_cid}]."),
    ]
    mock_completion = AsyncMock(side_effect=responses)

    from paperhub.agents.paper_qa_subagent import run_paper_qa_subagent

    with patch("paperhub.agents.paper_qa_subagent.litellm.acompletion", new=mock_completion):
        picks = await run_paper_qa_subagent(
            paper_content_id=pcid,
            title="Attention Is All You Need",
            user_message="How does this paper evaluate its method?",
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            conn=migrated_db,
            max_section_reads=5,
        )

    assert picks.paper_content_id == pcid
    assert picks.title == "Attention Is All You Need"
    cited_ids = {c.chunk_id for c in picks.picked_chunks}
    assert cited_ids == {method_cid, exp_cid}
    assert picks.rationale  # non-empty

    # ONE tracer step per run_paper_qa_subagent call, not one per iteration.
    async with migrated_db.execute(
        "SELECT COUNT(*) FROM tool_calls WHERE tool = 'paper_qa:subagent'"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == 1, (
        f"expected exactly 1 tracer step for paper_qa:subagent, got {row[0]}"
    )


async def test_subagent_loop_stops_at_max_section_reads_and_returns_what_it_read(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """If the LLM keeps calling read_section past max_section_reads, the
    loop force-stops and returns every chunk it ever read."""
    # Seed paper with 10 sections, each 1 chunk.
    sections = [
        {"name": f"Section{i}", "chunks": [f"Content of section {i}."]}
        for i in range(1, 11)
    ]
    pcid, cid_map = await _seed_paper_with_sections(
        migrated_db, title="Big Paper", sections=sections,
    )

    # LLM always returns read_section("Section1") — never stops.
    def _always_read_section(_: Any, **__: Any) -> Any:
        return _msg(tool_calls=[_tool_call("cx", "read_section", {"name": "Section1"})])

    mock_completion = AsyncMock(side_effect=lambda *a, **kw: _msg(
        tool_calls=[_tool_call("cx", "read_section", {"name": "Section1"})],
    ))

    from paperhub.agents.paper_qa_subagent import run_paper_qa_subagent

    with patch("paperhub.agents.paper_qa_subagent.litellm.acompletion", new=mock_completion):
        picks = await run_paper_qa_subagent(
            paper_content_id=pcid,
            title="Big Paper",
            user_message="Tell me everything.",
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            conn=migrated_db,
            max_section_reads=5,
        )

    # Budget exhausted — best-effort fallback: return all chunks that were read.
    assert picks.paper_content_id == pcid
    assert len(picks.picked_chunks) > 0, "Expected non-empty picks from best-effort fallback"


async def test_subagent_read_section_unknown_returns_error_to_llm_not_crash(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """If LLM asks for a section that doesn't exist, return a clean error
    in the tool result — no crash."""
    pcid, _ = await _seed_paper_with_sections(
        migrated_db,
        title="Methods Paper",
        sections=[{"name": "Method", "chunks": ["We use transformers."]}],
    )

    # LLM:
    # Turn 1: read_section("Nonexistent") — unknown section
    # Turn 2: no tool_calls — gives up
    responses = [
        _msg(tool_calls=[_tool_call("c1", "read_section", {"name": "Nonexistent"})]),
        _msg(content="No relevant chunks found for this question."),
    ]
    mock_completion = AsyncMock(side_effect=responses)

    from paperhub.agents.paper_qa_subagent import run_paper_qa_subagent

    with patch("paperhub.agents.paper_qa_subagent.litellm.acompletion", new=mock_completion):
        picks = await run_paper_qa_subagent(
            paper_content_id=pcid,
            title="Methods Paper",
            user_message="What experiments were run?",
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            conn=migrated_db,
            max_section_reads=5,
        )

    # No crash; LLM didn't cite anything → empty picks.
    assert picks.paper_content_id == pcid
    assert picks.picked_chunks == []

    # The unknown-section error must have been relayed back to the LLM
    # as a tool-role message so the LLM can react to it.
    # The second LLM call receives the messages including the tool result.
    second_call_messages = mock_completion.call_args_list[1].kwargs["messages"]
    tool_results = [m for m in second_call_messages if m.get("role") == "tool"]
    assert tool_results, "no tool result message was sent back to the LLM"
    error_content = tool_results[-1]["content"].lower()
    assert "unknown section" in error_content or "not found" in error_content, (
        f"unknown-section error was not relayed to LLM in tool result; "
        f"got: {tool_results[-1]['content']!r}"
    )


async def test_subagent_handles_malformed_tool_call_arguments(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """Regression: LiteLLM/model can return malformed JSON in tool_call
    arguments. Subagent must not crash — it should treat the call as
    if no args were provided and continue."""
    pcid, _ = await _seed_paper_with_sections(
        migrated_db,
        title="Methods Paper",
        sections=[{"name": "Method", "chunks": ["Method content."]}],
    )

    # Turn 1: read_section tool_call with broken JSON args ("{bad").
    # Turn 2: final summary — no tool_calls.
    broken_call: dict[str, Any] = {
        "id": "1",
        "type": "function",
        "function": {"name": "read_section", "arguments": "{bad"},
    }
    responses = [
        _msg(tool_calls=[broken_call]),
        _msg(content="Done. No chunks found."),
    ]
    mock_completion = AsyncMock(side_effect=responses)

    from paperhub.agents.paper_qa_subagent import run_paper_qa_subagent

    with patch("paperhub.agents.paper_qa_subagent.litellm.acompletion", new=mock_completion):
        picks = await run_paper_qa_subagent(
            paper_content_id=pcid,
            title="Methods Paper",
            user_message="What is the method?",
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            conn=migrated_db,
            max_section_reads=5,
        )

    # No crash; subagent reached the final summary path.
    assert picks.paper_content_id == pcid

    # The second LLM call must include a tool result message
    # (the error for the malformed/empty-args read_section call).
    second_call_msgs = mock_completion.call_args_list[1].kwargs["messages"]
    tool_results = [m for m in second_call_msgs if m.get("role") == "tool"]
    assert tool_results, "no tool result returned to the LLM after malformed args"
