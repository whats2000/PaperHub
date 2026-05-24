"""Tests for the per-paper paper_qa subagent (Plan C v2.10-3).

Covers:
- Happy path: list_sections → read two sections → cite chunks → return PerPaperPicks
- Budget exhaustion: force-stop returns all seen chunks as best-effort fallback
- Unknown section: returns error dict to LLM, no crash, empty picks
- F2.1 A2': read_section includes page info for Marker chunks; omits for NULL page
- F2.1 A2': PickedChunk carries page; extraction regex unaffected by page in chunk head
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


# ─────────────────────────── F2.1 A2': page in chunk text ───────────────────


async def _seed_paper_with_page(
    conn: aiosqlite.Connection,
    *,
    section_name: str = "Results",
    chunk_text: str = "Accuracy is 94%.",
    page: int | None = None,
) -> tuple[int, int]:
    """Insert a paper + one chunk with optional page. Returns (pcid, chunk_id)."""
    toc = [{"name": section_name, "char_start": 0, "char_end": len(chunk_text),
             "token_count": 20, "chunk_count": 1}]
    await conn.execute(
        "INSERT INTO paper_content "
        "(content_key, kind, arxiv_id, title, authors_json, year, abstract, "
        "source_path, source_dir_path, html_path, sections_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"arxiv:page-test-{page}", "arxiv", f"page-test-{page}", "Page Test Paper",
            "[]", 2024, "abstract", "/tmp/x.tex", "/tmp", "/tmp/x.html",
            json.dumps(toc),
        ),
    )
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    pcid = int(row[0])

    await conn.execute(
        "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text, page) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (pcid, section_name, 0, len(chunk_text), chunk_text, page),
    )
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        cr = await cur.fetchone()
    assert cr is not None
    cid = int(cr[0])
    await conn.commit()
    return pcid, cid


async def test_read_section_includes_page_when_chunk_has_page(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """read_section body must contain 'p.6' (or similar) for a chunk with page=6.

    The page info appears in the tool-result message the LLM sees, so it can
    refer to PDF page positions in its cited summary.
    """
    pcid, cid = await _seed_paper_with_page(
        migrated_db, section_name="Results", chunk_text="Accuracy is 94%.", page=6
    )

    # LLM sequence: read_section directly → final summary
    responses = [
        _msg(tool_calls=[_tool_call("c1", "read_section", {"name": "Results"})]),
        _msg(content=f"Good results [chunk:{cid}]."),
    ]
    mock_completion = AsyncMock(side_effect=responses)

    from paperhub.agents.paper_qa_subagent import run_paper_qa_subagent

    with patch("paperhub.agents.paper_qa_subagent.litellm.acompletion", new=mock_completion):
        await run_paper_qa_subagent(
            paper_content_id=pcid,
            title="Page Test Paper",
            user_message="What are the results?",
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            conn=migrated_db,
            max_section_reads=5,
        )

    # The second LLM call must include a tool result with "p.6" in the chunk body.
    second_call_msgs = mock_completion.call_args_list[1].kwargs["messages"]
    tool_results = [m for m in second_call_msgs if m.get("role") == "tool"]
    assert tool_results, "no tool result for read_section"
    body = tool_results[-1]["content"]
    assert "p.6" in body, (
        f"Expected 'p.6' in read_section tool result for page=6 chunk; got:\n{body!r}"
    )


async def test_read_section_omits_page_when_chunk_has_null_page(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """read_section body must NOT contain any 'p.' marker when chunk.page is NULL.

    LaTeX/PyMuPDF chunks have no page — the format must be unchanged.
    """
    pcid, cid = await _seed_paper_with_page(
        migrated_db, section_name="Methods", chunk_text="We use transformers.", page=None
    )

    responses = [
        _msg(tool_calls=[_tool_call("c1", "read_section", {"name": "Methods"})]),
        _msg(content=f"Uses transformers [chunk:{cid}]."),
    ]
    mock_completion = AsyncMock(side_effect=responses)

    from paperhub.agents.paper_qa_subagent import run_paper_qa_subagent

    with patch("paperhub.agents.paper_qa_subagent.litellm.acompletion", new=mock_completion):
        await run_paper_qa_subagent(
            paper_content_id=pcid,
            title="Page Test Paper",
            user_message="What method is used?",
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            conn=migrated_db,
            max_section_reads=5,
        )

    second_call_msgs = mock_completion.call_args_list[1].kwargs["messages"]
    tool_results = [m for m in second_call_msgs if m.get("role") == "tool"]
    assert tool_results
    body = tool_results[-1]["content"]
    # No 'p.' page annotation should appear for a NULL-page chunk.
    assert " p." not in body and "page=" not in body, (
        f"Unexpected page annotation for NULL-page chunk; got:\n{body!r}"
    )


async def test_picked_chunk_carries_page(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """PickedChunk returned from run_paper_qa_subagent must carry .page=6."""
    pcid, cid = await _seed_paper_with_page(
        migrated_db, section_name="Results", chunk_text="Accuracy is 94%.", page=6
    )

    responses = [
        _msg(tool_calls=[_tool_call("c1", "read_section", {"name": "Results"})]),
        _msg(content=f"Good results [chunk:{cid}]."),
    ]
    mock_completion = AsyncMock(side_effect=responses)

    from paperhub.agents.paper_qa_subagent import run_paper_qa_subagent

    with patch("paperhub.agents.paper_qa_subagent.litellm.acompletion", new=mock_completion):
        picks = await run_paper_qa_subagent(
            paper_content_id=pcid,
            title="Page Test Paper",
            user_message="What are the results?",
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            conn=migrated_db,
            max_section_reads=5,
        )

    assert len(picks.picked_chunks) == 1
    assert picks.picked_chunks[0].chunk_id == cid
    assert picks.picked_chunks[0].page == 6, (
        f"PickedChunk.page should be 6, got {picks.picked_chunks[0].page!r}"
    )


async def test_picked_chunk_page_is_none_for_non_marker_chunk(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """PickedChunk.page must be None for a chunk with NULL page (non-Marker)."""
    pcid, cid = await _seed_paper_with_page(
        migrated_db, section_name="Results", chunk_text="Accuracy is 94%.", page=None
    )

    responses = [
        _msg(tool_calls=[_tool_call("c1", "read_section", {"name": "Results"})]),
        _msg(content=f"Good results [chunk:{cid}]."),
    ]
    mock_completion = AsyncMock(side_effect=responses)

    from paperhub.agents.paper_qa_subagent import run_paper_qa_subagent

    with patch("paperhub.agents.paper_qa_subagent.litellm.acompletion", new=mock_completion):
        picks = await run_paper_qa_subagent(
            paper_content_id=pcid,
            title="Page Test Paper",
            user_message="What are the results?",
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            conn=migrated_db,
            max_section_reads=5,
        )

    assert len(picks.picked_chunks) == 1
    assert picks.picked_chunks[0].page is None, (
        f"PickedChunk.page should be None for NULL-page chunk, got {picks.picked_chunks[0].page!r}"
    )


def test_chunk_id_extraction_unaffected_by_page_in_container() -> None:
    """_extract_cited_chunk_ids must still extract ids correctly when the LLM
    cites [chunk:123] even if the read_section body had page annotations.

    The extraction regex (chunk:\\d+) scans the LLM *summary*, not the tool
    result, so this test verifies that the LLM output citation format is
    unambiguous — the id is still extracted correctly regardless of what
    was shown in the chunk containers.

    Note: the regex matches ``chunk:<digits>`` tokens; comma-separated ids
    like ``[chunk:103,104]`` only match 103 (the 104 has no ``chunk:`` prefix
    — that format extracts a single id per bracket group). The multi-chunk
    form that works for BOTH ids is ``[chunk:103, chunk:104]``.
    """
    from paperhub.agents.paper_qa_subagent import _extract_cited_chunk_ids

    # The LLM prose includes "p.7" (page mention) — must not confuse the extractor.
    summary = (
        "The method achieves 94% accuracy [chunk:101] and the ablation on p.7 "
        "confirms this [chunk:102]. See also [chunk:103] and [chunk:104] for details."
    )
    ids = _extract_cited_chunk_ids(summary)
    assert ids == [101, 102, 103, 104], f"Got: {ids}"


# ─────────────────────── F2.1 A3: layout tools ──────────────────────


async def _seed_paper_with_layout(
    conn: aiosqlite.Connection,
    *,
    title: str = "Layout Paper",
    layout: list[dict[str, Any]] | None,
    chunk_text: str = "Table 1 reports accuracy of 94% across all baselines.",
    page: int | None = 5,
    section_name: str = "Results",
) -> tuple[int, int]:
    """Insert a paper + one chunk + a layout_json index pointing at it.

    ``layout`` is the JSON list to store in ``paper_content.layout_json``
    (NULL when None). The single chunk's id is substituted into any layout
    entry whose ``chunk_id`` sentinel is the string ``"$CID"``.
    Returns (pcid, chunk_id).
    """
    toc = [{"name": section_name, "char_start": 0, "char_end": len(chunk_text),
            "token_count": 20, "chunk_count": 1}]
    await conn.execute(
        "INSERT INTO paper_content "
        "(content_key, kind, arxiv_id, title, authors_json, year, abstract, "
        "source_path, source_dir_path, html_path, sections_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"arxiv:layout-{title}", "arxiv", f"layout-{title}", title,
            "[]", 2024, "abstract", "/tmp/x.pdf", "/tmp", "/tmp/x.html",
            json.dumps(toc),
        ),
    )
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    pcid = int(row[0])

    await conn.execute(
        "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text, page) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (pcid, section_name, 0, len(chunk_text), chunk_text, page),
    )
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        cr = await cur.fetchone()
    assert cr is not None
    cid = int(cr[0])

    if layout is not None:
        resolved = []
        for entry in layout:
            e = dict(entry)
            if e.get("chunk_id") == "$CID":
                e["chunk_id"] = cid
            resolved.append(e)
        await conn.execute(
            "UPDATE paper_content SET layout_json = ? WHERE id = ?",
            (json.dumps(resolved), pcid),
        )
    await conn.commit()
    return pcid, cid


async def test_list_figures_tables_then_read_layout_object_picks_and_cites(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """The subagent can list layout objects, read Table 1 by label, the
    table's chunk lands in picked chunks (so the finalizer cites it), and the
    layout read is recorded in the trace."""
    pcid, cid = await _seed_paper_with_layout(
        migrated_db,
        title="Tables Paper",
        layout=[
            {"kind": "Table", "label": "Table 1", "caption": "Main results",
             "page": 5, "chunk_id": "$CID"},
            {"kind": "Figure", "label": "Figure 2", "caption": "Architecture",
             "page": 3, "chunk_id": 99999},
        ],
        chunk_text="Table 1 reports accuracy of 94% across all baselines.",
        page=5,
    )

    responses = [
        _msg(tool_calls=[_tool_call("c1", "list_figures_tables", {})]),
        _msg(tool_calls=[_tool_call("c2", "read_layout_object", {"label": "Table 1"})]),
        _msg(content=f"Table 1 reports 94% accuracy [chunk:{cid}]."),
    ]
    mock_completion = AsyncMock(side_effect=responses)

    from paperhub.agents.paper_qa_subagent import run_paper_qa_subagent

    with patch("paperhub.agents.paper_qa_subagent.litellm.acompletion", new=mock_completion):
        picks = await run_paper_qa_subagent(
            paper_content_id=pcid,
            title="Tables Paper",
            user_message="What does Table 1 show?",
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            conn=migrated_db,
            max_section_reads=5,
        )

    # The table's chunk is picked + cited.
    assert [c.chunk_id for c in picks.picked_chunks] == [cid]
    assert picks.picked_chunks[0].page == 5

    # The messages list is mutated in place across calls, so the final
    # captured messages contain BOTH tool results: index 0 = list result,
    # index 1 = read_layout_object result.
    final_msgs = mock_completion.call_args_list[-1].kwargs["messages"]
    tool_results = [m for m in final_msgs if m.get("role") == "tool"]
    assert len(tool_results) == 2

    # list_figures_tables result must list the labels (no chunk_id leaked).
    list_body = tool_results[0]["content"]
    assert "Table 1" in list_body and "Figure 2" in list_body
    assert "99999" not in list_body and "chunk_id" not in list_body

    # The read_layout_object result must carry the chunk text in the same
    # <chunk id="N">…</chunk> (p.N) container so the LLM can cite it.
    layout_result = tool_results[1]["content"]
    assert "Table 1 reports accuracy of 94%" in layout_result
    assert f'<chunk id="{cid}">' in layout_result
    assert "p.5" in layout_result

    # Trace records the layout read.
    async with migrated_db.execute(
        "SELECT result_summary_json FROM tool_calls WHERE tool = 'paper_qa:subagent'"
    ) as cur:
        trow = await cur.fetchone()
    assert trow is not None
    summary = json.loads(trow[0])
    assert summary["listed_layout"] == ["Table 1", "Figure 2"]
    assert cid in summary["layout_read_ids"]
    assert cid in summary["chunks_read_ids"]


async def test_read_layout_object_unknown_label_returns_not_found(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """Unknown label → graceful 'not found' tool result, no exception."""
    pcid, cid = await _seed_paper_with_layout(
        migrated_db,
        title="Tables Paper",
        layout=[
            {"kind": "Table", "label": "Table 1", "caption": "Main results",
             "page": 5, "chunk_id": "$CID"},
        ],
    )

    responses = [
        _msg(tool_calls=[_tool_call("c1", "read_layout_object", {"label": "Table 9"})]),
        _msg(content="No such table found in this paper."),
    ]
    mock_completion = AsyncMock(side_effect=responses)

    from paperhub.agents.paper_qa_subagent import run_paper_qa_subagent

    with patch("paperhub.agents.paper_qa_subagent.litellm.acompletion", new=mock_completion):
        picks = await run_paper_qa_subagent(
            paper_content_id=pcid,
            title="Tables Paper",
            user_message="What does Table 9 show?",
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            conn=migrated_db,
            max_section_reads=5,
        )

    assert picks.picked_chunks == []
    second_call_msgs = mock_completion.call_args_list[1].kwargs["messages"]
    tool_results = [m for m in second_call_msgs if m.get("role") == "tool"]
    assert tool_results
    body = tool_results[-1]["content"].lower()
    assert "no such" in body or "not found" in body


async def test_read_layout_object_case_insensitive_label_match(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """'table 1' matches stored 'Table 1' (case-insensitive)."""
    pcid, cid = await _seed_paper_with_layout(
        migrated_db,
        title="Tables Paper",
        layout=[
            {"kind": "Table", "label": "Table 1", "caption": "Main results",
             "page": 5, "chunk_id": "$CID"},
        ],
    )

    responses = [
        _msg(tool_calls=[_tool_call("c1", "read_layout_object", {"label": "table 1"})]),
        _msg(content=f"Table 1 [chunk:{cid}]."),
    ]
    mock_completion = AsyncMock(side_effect=responses)

    from paperhub.agents.paper_qa_subagent import run_paper_qa_subagent

    with patch("paperhub.agents.paper_qa_subagent.litellm.acompletion", new=mock_completion):
        picks = await run_paper_qa_subagent(
            paper_content_id=pcid,
            title="Tables Paper",
            user_message="What does table 1 show?",
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            conn=migrated_db,
            max_section_reads=5,
        )

    assert [c.chunk_id for c in picks.picked_chunks] == [cid]


async def test_list_figures_tables_null_layout_returns_none_marker(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """NULL layout_json → list_figures_tables returns an empty/'(none)'
    result, no crash."""
    pcid, _cid = await _seed_paper_with_layout(
        migrated_db,
        title="No Layout Paper",
        layout=None,
    )

    responses = [
        _msg(tool_calls=[_tool_call("c1", "list_figures_tables", {})]),
        _msg(content="This paper has no figures or tables indexed."),
    ]
    mock_completion = AsyncMock(side_effect=responses)

    from paperhub.agents.paper_qa_subagent import run_paper_qa_subagent

    with patch("paperhub.agents.paper_qa_subagent.litellm.acompletion", new=mock_completion):
        picks = await run_paper_qa_subagent(
            paper_content_id=pcid,
            title="No Layout Paper",
            user_message="List the tables.",
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            conn=migrated_db,
            max_section_reads=5,
        )

    assert picks.picked_chunks == []
    second_call_msgs = mock_completion.call_args_list[1].kwargs["messages"]
    tool_results = [m for m in second_call_msgs if m.get("role") == "tool"]
    body = tool_results[-1]["content"].lower()
    assert "none" in body or body.strip() in {"[]", "()", ""}


async def test_finalizer_per_paper_block_includes_page_for_marker_chunks(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """paper_qa_finalize must include page info in the per_paper_block it builds,
    so the finalizer LLM can mention PDF page positions in its answer prose.
    """
    from paperhub.agents.paper_qa_subagent import PerPaperPicks, PickedChunk
    from paperhub.agents.research import paper_qa_finalize

    # Chunks: one with page, one without.
    picks = [
        PerPaperPicks(
            paper_content_id=1,
            title="Results Paper",
            picked_chunks=[
                PickedChunk(chunk_id=101, text="Table 1 shows 94% accuracy.", section="Results", page=6),
                PickedChunk(chunk_id=102, text="Training details.", section="Methods", page=None),
            ],
            rationale="Has accuracy results and training details.",
        ),
    ]

    stub_tokens = "The accuracy is 94% [chunk:101]."

    class _CaptureAdapter:
        calls: list[dict[str, Any]] = []

        async def structured(self, **_: Any) -> Any:  # pragma: no cover
            raise NotImplementedError

        def stream(self, *, slot: str, variables: dict[str, Any], model: str,
                   history: Any = None, **_: Any) -> Any:
            self.calls.append({"slot": slot, "variables": dict(variables)})

            async def _gen() -> Any:
                yield stub_tokens

            return _gen()

    adapter = _CaptureAdapter()
    tokens: list[str] = []
    async for tok in paper_qa_finalize(
        per_paper_picks=picks,
        user_message="What accuracy?",
        adapter=adapter,  # type: ignore[arg-type]
        tracer=fake_tracer,
        model="stub",
        state={"run_id": fake_tracer._run_id, "history": None},  # type: ignore[arg-type]  # noqa: SLF001
    ):
        tokens.append(tok)

    synth_calls = [c for c in adapter.calls if c["slot"] == "paper_qa_synthesize/v2"]
    assert synth_calls, "Expected paper_qa_synthesize/v2 call"
    per_paper_block = synth_calls[0]["variables"]["per_paper_block"]
    # chunk 101 has page=6 → should appear in per_paper_block
    assert "p.6" in per_paper_block, (
        f"Expected 'p.6' in per_paper_block for chunk 101 (page=6); got:\n{per_paper_block!r}"
    )
    # chunk 102 has page=None → no 'p.' annotation for it
    # (We can't easily check the absence per chunk, but we verify p.6 is present for chunk 101)
    _ = tokens  # tokens consumed — no assertion on LLM output text
