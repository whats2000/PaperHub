"""Tests for the F4.4 T1 per-paper agentic brief subagent.

Covers:
- PaperTalkBrief Pydantic round-trip (full payload + invalid enum rejection)
- Happy path: list_sections → read_section → read_figure_block → final JSON
- paper_newcommands extraction from the PaperAsset preamble
- Read-budget cap: the 6th read_section attempt is rejected with a clear
  error, but the brief is still produced from what was already read.

Mirrors the fixture style of ``tests/test_paper_qa_subagent.py``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
from pydantic import ValidationError

from paperhub.models.domain import PaperTalkBrief
from paperhub.pipelines.paper_asset import (
    EquationAsset,
    FigureAsset,
    PaperAsset,
    SectionAsset,
    write_paper_asset,
)

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


def _full_brief_payload(
    *,
    paper_id: int,
    figure_key: str = "p0-fig-001",
    extra_newcommands: str = "",
) -> dict[str, Any]:
    """Build a complete PaperTalkBrief JSON payload the test LLM emits."""
    return {
        "paper_id": paper_id,
        "contribution": "Introduces a flow-matching variant for discrete data.",
        "method_core": (
            "Reformulates the loss as an interpolant-conditional regression. "
            "Trains a single network end-to-end."
        ),
        "key_results": [
            {
                "description": "Higher exact-match accuracy than diffusion baseline",
                "number": "14%",
                "benchmark": "LIBERO",
            },
        ],
        "key_figures": [
            {
                "key": figure_key,
                "role": "method_diagram",
                "one_line_interpretation": (
                    "Shows the interpolant trajectory between source and target."
                ),
            },
        ],
        "key_equations": [
            {
                "latex": r"\mathcal{L} = \mathbb{E}_{t, x_0, x_1}[\| v_\theta(x_t, t) - (x_1 - x_0) \|^2]",
                "role": "loss",
                "notation_explanation": (
                    "L is the training loss; v_theta is the network; "
                    "x_0/x_1 are source/target samples; t is the interpolation time."
                ),
            },
        ],
        "paper_newcommands": extra_newcommands,
        "talk_shape_hint": "concept+math+results",
    }


async def _seed_paper(
    conn: aiosqlite.Connection,
    *,
    title: str = "Flow Matching for Discrete Data",
    sections: list[dict[str, Any]],
    source_dir: Path | None = None,
) -> int:
    """Insert a paper_content row with chunks grouped by section.

    ``sections`` is a list of ``{"name": str, "chunks": [str, ...]}``.
    Returns ``paper_content_id``.
    """
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

    src_dir = str(source_dir) if source_dir is not None else "/tmp/test-source"
    await conn.execute(
        "INSERT INTO paper_content "
        "(content_key, kind, arxiv_id, title, authors_json, year, abstract, "
        "source_path, source_dir_path, html_path, sections_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"arxiv:t1-{title}", "arxiv", f"t1-{title.replace(' ', '-')}", title,
            "[]", 2024, "abstract",
            f"{src_dir}/source.tex", src_dir, f"{src_dir}/source.html",
            json.dumps(toc),
        ),
    )
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    pcid = int(row[0])

    char_start = 0
    for sec_name, txt in all_chunks:
        char_end = char_start + len(txt)
        await conn.execute(
            "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text) "
            "VALUES (?, ?, ?, ?, ?)",
            (pcid, sec_name, char_start, char_end, txt),
        )
        char_start = char_end
    await conn.commit()
    return pcid


def _write_asset_with_figure(source_dir: Path, fig_id: str = "fig-001") -> None:
    """Write a minimal PaperAsset under ``source_dir/asset/`` with one figure."""
    asset = PaperAsset(
        figures=[
            FigureAsset(
                id=fig_id,
                caption="Interpolant trajectory between source and target distributions.",
                page=2,
                section="Methods",
                image_path=f"figures/{fig_id}.png",
            ),
        ],
        equations=[],
        sections=[SectionAsset(name="Methods", order=1)],
    )
    write_paper_asset(asset, source_dir)


# ─────────────────────────── schema tests ───────────────────────────


def test_paper_talk_brief_schema_validates_full_payload() -> None:
    """Happy-path Pydantic round-trip of a complete PaperTalkBrief."""
    payload = _full_brief_payload(paper_id=42)
    brief = PaperTalkBrief.model_validate(payload)
    assert brief.paper_id == 42
    assert brief.contribution.startswith("Introduces")
    assert len(brief.key_results) == 1
    assert brief.key_results[0].benchmark == "LIBERO"
    assert brief.key_figures[0].role == "method_diagram"
    assert brief.key_equations[0].notation_explanation
    assert brief.talk_shape_hint == "concept+math+results"

    # JSON round-trip (the node validates via model_validate_json).
    raw = brief.model_dump_json()
    assert PaperTalkBrief.model_validate_json(raw) == brief


def test_paper_talk_brief_rejects_unknown_role() -> None:
    """An invalid key_figures[*].role enum value is rejected by the schema."""
    payload = _full_brief_payload(paper_id=1)
    payload["key_figures"][0]["role"] = "not_a_role"
    with pytest.raises(ValidationError):
        PaperTalkBrief.model_validate(payload)


@pytest.mark.parametrize(
    "field_path",
    [
        ("key_results", 0, "number"),
        ("key_results", 0, "benchmark"),
        ("key_equations", 0, "notation_explanation"),
    ],
)
def test_paper_talk_brief_rejects_empty_load_bearing_fields(
    field_path: tuple[str, int, str],
) -> None:
    """Empty values for KeyResult.number / .benchmark and
    KeyEquation.notation_explanation are rejected by the schema.

    The prompt mandates these fields be present and informative; without a
    schema-level non-empty constraint a lazy LLM emit of ``""`` would still
    validate, silently dropping the quantification (results) or the symbol
    definitions (equations) the slide stage depends on.
    """
    payload = _full_brief_payload(paper_id=1)
    parent_key, idx, field_name = field_path
    payload[parent_key][idx][field_name] = ""
    with pytest.raises(ValidationError):
        PaperTalkBrief.model_validate(payload)


# ─────────────────────────── agentic loop tests ─────────────────────


@pytest.mark.asyncio
async def test_sl_paper_brief_dispatches_tools_then_emits_brief(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
    tmp_path: Path,
) -> None:
    """Stubbed LLM that walks the tool palette then emits final JSON.

    Asserts: (1) tools dispatch in order; (2) brief schema validates;
    (3) the tracer step's args/result records carry the IDs + tool log.
    """
    src_dir = tmp_path / "paper-1"
    src_dir.mkdir()
    _write_asset_with_figure(src_dir, fig_id="fig-001")

    pcid = await _seed_paper(
        migrated_db,
        title="Flow Matching for Discrete Data",
        sections=[
            {"name": "Methods", "chunks": ["We define the interpolant as x_t = (1-t) x_0 + t x_1."]},
            {"name": "Results", "chunks": ["On LIBERO we beat the diffusion baseline by 14%."]},
        ],
        source_dir=src_dir,
    )

    final_json = json.dumps(_full_brief_payload(paper_id=pcid, figure_key="p0-fig-001"))
    responses = [
        _msg(tool_calls=[_tool_call("c1", "list_sections", {})]),
        _msg(tool_calls=[_tool_call("c2", "read_section", {"name": "Methods"})]),
        _msg(tool_calls=[_tool_call("c3", "read_figure_block", {"figure_key": "p0-fig-001"})]),
        _msg(content=final_json),
    ]
    mock_completion = AsyncMock(side_effect=responses)

    from paperhub.agents.sl_paper_brief import run_sl_paper_brief

    with patch("paperhub.agents.sl_paper_brief.litellm.acompletion", new=mock_completion):
        brief = await run_sl_paper_brief(
            paper_content_id=pcid,
            paper_idx=0,
            title="Flow Matching for Discrete Data",
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            conn=migrated_db,
            response_language="English",
        )

    # (1) brief schema is valid + canonical paper_id is the seeded one
    assert isinstance(brief, PaperTalkBrief)
    assert brief.paper_id == pcid
    assert brief.key_figures[0].key == "p0-fig-001"
    assert brief.talk_shape_hint == "concept+math+results"

    # (2) tools dispatched in order — inspect what the LLM was asked to call
    assert mock_completion.call_count == 4

    # (3) tool results were appended to the messages — final call sees them
    final_call_msgs = mock_completion.call_args_list[-1].kwargs["messages"]
    tool_results = [m for m in final_call_msgs if m.get("role") == "tool"]
    assert [t["name"] for t in tool_results] == [
        "list_sections", "read_section", "read_figure_block",
    ]

    # read_figure_block result contains the asset caption
    fig_body = tool_results[-1]["content"]
    assert "Interpolant trajectory" in fig_body

    # (4) tracer recorded the per-stage state
    async with migrated_db.execute(
        "SELECT args_redacted_json, result_summary_json FROM tool_calls "
        "WHERE tool = 'report:paper_brief'"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    args = json.loads(row[0])
    result = json.loads(row[1])
    assert args["paper_content_id"] == pcid
    assert args["paper_idx"] == 0
    assert result["paper_id"] == pcid
    assert result["sections_read"] == ["Methods"]
    assert result["figures_read"] == ["p0-fig-001"]
    assert "Methods" in (result["listed_sections"] or [])
    assert len(result["tool_call_log"]) == 3
    assert result["parse_error"] is None
    # The full final text is recorded for debugging.
    assert result["final_text"]


@pytest.mark.asyncio
async def test_sl_paper_brief_records_paper_newcommands(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
    tmp_path: Path,
) -> None:
    """When the brief JSON carries a paper_newcommands block, it is preserved
    verbatim in the resulting PaperTalkBrief (T4 will plumb it into the deck
    preamble)."""
    src_dir = tmp_path / "paper-2"
    src_dir.mkdir()
    _write_asset_with_figure(src_dir, fig_id="fig-001")

    pcid = await _seed_paper(
        migrated_db,
        title="Macro-heavy paper",
        sections=[
            {"name": "Methods", "chunks": ["We define $\\E$ as expectation."]},
        ],
        source_dir=src_dir,
    )

    macros = (
        r"\newcommand{\E}{\mathbb{E}}" "\n"
        r"\DeclareMathOperator{\softmax}{softmax}"
    )
    final_json = json.dumps(
        _full_brief_payload(paper_id=pcid, extra_newcommands=macros)
    )
    responses = [
        _msg(tool_calls=[_tool_call("c1", "list_sections", {})]),
        _msg(content=final_json),
    ]
    mock_completion = AsyncMock(side_effect=responses)

    from paperhub.agents.sl_paper_brief import run_sl_paper_brief

    with patch("paperhub.agents.sl_paper_brief.litellm.acompletion", new=mock_completion):
        brief = await run_sl_paper_brief(
            paper_content_id=pcid,
            paper_idx=0,
            title="Macro-heavy paper",
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            conn=migrated_db,
            response_language="English",
        )

    assert r"\newcommand{\E}{\mathbb{E}}" in brief.paper_newcommands
    assert r"\DeclareMathOperator{\softmax}{softmax}" in brief.paper_newcommands

    # Trace also records the macros so a post-mortem doesn't need to re-run.
    async with migrated_db.execute(
        "SELECT result_summary_json FROM tool_calls WHERE tool = 'report:paper_brief'"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    result = json.loads(row[0])
    assert r"\newcommand{\E}{\mathbb{E}}" in result["paper_newcommands"]


@pytest.mark.asyncio
async def test_sl_paper_brief_caps_read_budget(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
    tmp_path: Path,
) -> None:
    """A 6th read_section call (max=5) is rejected with a budget-exhausted
    tool error. The LLM then emits the final brief from what it has read,
    and the safety-net behavior still yields a valid PaperTalkBrief."""
    src_dir = tmp_path / "paper-3"
    src_dir.mkdir()
    _write_asset_with_figure(src_dir, fig_id="fig-001")

    sections = [
        {"name": f"Sec{i}", "chunks": [f"Content of section {i}."]}
        for i in range(1, 7)
    ]
    pcid = await _seed_paper(
        migrated_db,
        title="Bloated paper",
        sections=sections,
        source_dir=src_dir,
    )

    final_json = json.dumps(_full_brief_payload(paper_id=pcid))
    responses = [
        _msg(tool_calls=[_tool_call("c0", "list_sections", {})]),
        _msg(tool_calls=[_tool_call("c1", "read_section", {"name": "Sec1"})]),
        _msg(tool_calls=[_tool_call("c2", "read_section", {"name": "Sec2"})]),
        _msg(tool_calls=[_tool_call("c3", "read_section", {"name": "Sec3"})]),
        _msg(tool_calls=[_tool_call("c4", "read_section", {"name": "Sec4"})]),
        _msg(tool_calls=[_tool_call("c5", "read_section", {"name": "Sec5"})]),
        # 6th call — must be rejected with a budget error.
        _msg(tool_calls=[_tool_call("c6", "read_section", {"name": "Sec6"})]),
        # LLM gives up calling tools and emits the final brief.
        _msg(content=final_json),
    ]
    mock_completion = AsyncMock(side_effect=responses)

    from paperhub.agents.sl_paper_brief import run_sl_paper_brief

    with patch("paperhub.agents.sl_paper_brief.litellm.acompletion", new=mock_completion):
        brief = await run_sl_paper_brief(
            paper_content_id=pcid,
            paper_idx=0,
            title="Bloated paper",
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            conn=migrated_db,
            response_language="English",
            max_section_reads=5,
        )

    # The brief is still produced from the in-budget reads.
    assert brief.paper_id == pcid
    assert brief.contribution

    # The LLM saw a budget-exhausted error for the 6th call.
    last_call_msgs = mock_completion.call_args_list[-1].kwargs["messages"]
    tool_results = [m for m in last_call_msgs if m.get("role") == "tool"]
    # 1 list_sections + 6 read_section tool results = 7 tool messages total.
    assert len(tool_results) == 7
    last_tool_body = tool_results[-1]["content"].lower()
    assert "budget" in last_tool_body or "exhausted" in last_tool_body

    # Tracer records exactly 5 section reads (the 6th was rejected).
    async with migrated_db.execute(
        "SELECT result_summary_json FROM tool_calls WHERE tool = 'report:paper_brief'"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    result = json.loads(row[0])
    assert result["section_reads_used"] == 5
    # The 6th call appears in the tool_call_log with a budget_exhausted error.
    budget_hits = [
        e for e in result["tool_call_log"]
        if e.get("error") == "budget_exhausted"
    ]
    assert len(budget_hits) == 1


def _write_asset_with_equations(source_dir: Path) -> None:
    """Write a PaperAsset with mixed-section equations.

    Two equations in Methods (an equation env + an align* env, both LaTeX
    bodies — the asset stores rendered LaTeX, not the wrapping env), one in
    Results. _read_equations should filter to the Methods pair.
    """
    asset = PaperAsset(
        figures=[
            FigureAsset(
                id="fig-001",
                caption="dummy",
                page=1,
                section="Methods",
                image_path="figures/fig-001.png",
            ),
        ],
        equations=[
            EquationAsset(
                id="eq-001",
                latex=r"\mathcal{L} = \mathbb{E}[\| v_\theta - (x_1 - x_0) \|^2]",
                section="Methods",
            ),
            EquationAsset(
                id="eq-002",
                latex=r"x_t &= (1-t) x_0 + t x_1 \\ \dot x_t &= x_1 - x_0",
                section="Methods",
            ),
            EquationAsset(
                id="eq-003",
                latex=r"\text{Acc}(M) = \frac{1}{N}\sum_i \mathbf{1}[\hat y_i = y_i]",
                section="Results",
            ),
        ],
        sections=[
            SectionAsset(name="Methods", order=1),
            SectionAsset(name="Results", order=2),
        ],
    )
    write_paper_asset(asset, source_dir)


@pytest.mark.asyncio
async def test_sl_paper_brief_read_equations_filters_by_section(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
    tmp_path: Path,
) -> None:
    """read_equations(Methods) returns both Methods equations and excludes
    the Results equation; the tracer step records the section name + the
    budget counter increments by one.
    """
    src_dir = tmp_path / "paper-eq"
    src_dir.mkdir()
    _write_asset_with_equations(src_dir)

    pcid = await _seed_paper(
        migrated_db,
        title="Equation-heavy paper",
        sections=[
            {"name": "Methods", "chunks": ["Methods text."]},
            {"name": "Results", "chunks": ["Results text."]},
        ],
        source_dir=src_dir,
    )

    final_json = json.dumps(_full_brief_payload(paper_id=pcid))
    responses = [
        _msg(tool_calls=[_tool_call("c1", "list_sections", {})]),
        _msg(tool_calls=[_tool_call("c2", "read_equations", {"section": "Methods"})]),
        _msg(content=final_json),
    ]
    mock_completion = AsyncMock(side_effect=responses)

    from paperhub.agents.sl_paper_brief import run_sl_paper_brief

    with patch("paperhub.agents.sl_paper_brief.litellm.acompletion", new=mock_completion):
        await run_sl_paper_brief(
            paper_content_id=pcid,
            paper_idx=0,
            title="Equation-heavy paper",
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            conn=migrated_db,
            response_language="English",
        )

    # Inspect the tool result the LLM saw on its final call.
    final_call_msgs = mock_completion.call_args_list[-1].kwargs["messages"]
    tool_results = [m for m in final_call_msgs if m.get("role") == "tool"]
    eq_results = [t for t in tool_results if t["name"] == "read_equations"]
    assert len(eq_results) == 1
    body = eq_results[0]["content"]
    assert r"\mathcal{L}" in body
    assert r"x_t &= (1-t)" in body
    # The Results-section equation must NOT leak into the Methods read.
    assert r"\text{Acc}(M)" not in body

    # Tracer records the section + budget counter.
    async with migrated_db.execute(
        "SELECT result_summary_json FROM tool_calls WHERE tool = 'report:paper_brief'"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    result = json.loads(row[0])
    assert result["equations_read_sections"] == ["Methods"]
    assert result["equation_reads_used"] == 1


@pytest.mark.asyncio
async def test_sl_paper_brief_fallback_marks_step_error(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
    tmp_path: Path,
) -> None:
    """When the LLM emits non-JSON, the node returns an empty-but-valid brief
    AND flips the tracer step to status='error' with the canonical
    'brief_parse_failed' marker. Per the agent-flow observability iron rule:
    a silent fallback emitting structurally-valid garbage downstream is
    precisely the failure mode the rule was written to prevent.
    """
    src_dir = tmp_path / "paper-bad-json"
    src_dir.mkdir()
    _write_asset_with_figure(src_dir, fig_id="fig-001")

    pcid = await _seed_paper(
        migrated_db,
        title="Recalcitrant paper",
        sections=[
            {"name": "Methods", "chunks": ["Methods text."]},
        ],
        source_dir=src_dir,
    )

    # Only one LLM turn: invalid JSON, no tool calls — node exits the loop
    # and falls through to the parse-error branch.
    responses = [
        _msg(content="this is not valid JSON, sorry"),
    ]
    mock_completion = AsyncMock(side_effect=responses)

    from paperhub.agents.sl_paper_brief import run_sl_paper_brief

    with patch("paperhub.agents.sl_paper_brief.litellm.acompletion", new=mock_completion):
        brief = await run_sl_paper_brief(
            paper_content_id=pcid,
            paper_idx=0,
            title="Recalcitrant paper",
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            conn=migrated_db,
            response_language="English",
        )

    # (a) graceful empty-but-valid brief
    assert isinstance(brief, PaperTalkBrief)
    assert brief.paper_id == pcid
    assert brief.contribution == ""
    assert brief.key_results == []
    assert brief.talk_shape_hint == "concept_only"

    # (b) recorded tool_calls row is status='error' with the canonical marker
    async with migrated_db.execute(
        "SELECT status, error, result_summary_json FROM tool_calls "
        "WHERE tool = 'report:paper_brief'"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    status, error, result_json = row
    assert status == "error"
    assert error == "brief_parse_failed"
    # parse_error in the recorded payload pinpoints the failure for debug.
    result = json.loads(result_json)
    assert result["parse_error"]
