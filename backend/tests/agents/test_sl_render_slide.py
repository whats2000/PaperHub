"""Tests for the F4.4 T3 per-slide renderer.

Covers:

- ``RenderedSlide`` Pydantic schema (happy-path round trip).
- Happy paths: title / concept_2col / takeaway_closer patterns emit the
  expected LaTeX skeleton.
- Deterministic sanity validation: math_stack must contain a display-math
  block; multi-frame output is rejected; non-JSON LLM response is rejected
  with the canonical ``render_parse_failed`` marker; layout-validation
  failures use ``render_validation_failed``.
- Bounded callback budget: a 3rd read_section attempt is rejected with a
  budget-exhausted tool error and never reaches the callback_reads log.
- Cross-paper patterns have NO callback tools wired in the LLM schema.
- Trace contract: ``args_redacted_json`` / ``result_summary_json`` carry
  the expected reconstruct-able keys.

Mirrors the fixture style of ``tests/agents/test_sl_plan_deck.py`` and
``tests/agents/test_sl_paper_brief.py``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
from pydantic import ValidationError

from paperhub.models.domain import (
    DeckOutline,
    KeyEquation,
    KeyFigure,
    KeyResult,
    PaperTalkBrief,
    PlannedSlide,
    RenderedSlide,
)
from paperhub.pipelines.paper_asset import (
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


def _brief(
    *,
    paper_id: int,
    figure_keys: tuple[str, ...] = ("p0-fig-001", "p0-fig-002"),
) -> PaperTalkBrief:
    return PaperTalkBrief(
        paper_id=paper_id,
        contribution=f"Paper {paper_id} contributes X.",
        method_core=f"Paper {paper_id} introduces Y via Z.",
        key_results=[
            KeyResult(
                description=f"Better thing for paper {paper_id}",
                number="14%",
                benchmark="LIBERO",
            ),
        ],
        key_figures=[
            KeyFigure(
                key=k,
                role="overview" if i == 0 else "method_diagram",
                one_line_interpretation=f"Figure {k} shows Q.",
            )
            for i, k in enumerate(figure_keys)
        ],
        key_equations=[
            KeyEquation(
                latex=r"\mathcal{L} = \sum_i x_i^2",
                role="loss",
                notation_explanation="L is loss; x_i is sample i.",
            ),
        ],
        paper_newcommands="",
        talk_shape_hint="concept+math+results",
    )


def _outline_with(slides: list[PlannedSlide]) -> DeckOutline:
    return DeckOutline(
        talk_title="A Talk",
        talk_subtitle=None,
        slides=slides,
        style_profile_name="default",
    )


def _planned(
    *,
    pattern_kind: str,
    title: str = "Slide title",
    goal: str = "Land the idea.",
    paper_id: int | None = None,
    figure_key: str | None = None,
    equation_index: int | None = None,
    key_points: list[str] | None = None,
) -> PlannedSlide:
    return PlannedSlide(
        pattern_kind=pattern_kind,  # type: ignore[arg-type]
        title=title,
        goal=goal,
        paper_id=paper_id,
        figure_key=figure_key,
        equation_index=equation_index,
        key_points=key_points or [],
        chunk_ids=[],
    )


def _rendered_payload(
    *,
    slide_index: int,
    pattern_kind: str,
    frame_tex: str,
    paper_id: int | None = None,
    figure_keys_used: list[str] | None = None,
    callback_reads: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "slide_index": slide_index,
        "pattern_kind": pattern_kind,
        "paper_id": paper_id,
        "frame_tex": frame_tex,
        "figure_keys_used": figure_keys_used or [],
        "callback_reads": callback_reads or [],
    }


async def _read_step_row(
    conn: aiosqlite.Connection,
) -> tuple[str, str | None, dict[str, Any], dict[str, Any]]:
    async with conn.execute(
        "SELECT status, error, args_redacted_json, result_summary_json "
        "FROM tool_calls WHERE tool = 'report:render_slide'"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None, "expected one report:render_slide step row"
    status, error, args_json, result_json = row
    return (
        str(status),
        None if error is None else str(error),
        json.loads(args_json),
        json.loads(result_json),
    )


async def _seed_paper(
    conn: aiosqlite.Connection,
    *,
    title: str,
    sections: list[dict[str, Any]],
    source_dir: Path,
) -> int:
    toc: list[dict[str, Any]] = []
    all_chunks: list[tuple[str, str]] = []
    char_cursor = 0
    for sec in sections:
        sec_start = char_cursor
        for txt in sec["chunks"]:
            all_chunks.append((sec["name"], txt))
            char_cursor += len(txt)
        toc.append(
            {
                "name": sec["name"],
                "char_start": sec_start,
                "char_end": char_cursor,
                "token_count": len(sec["chunks"]) * 20,
                "chunk_count": len(sec["chunks"]),
            }
        )

    src_dir = str(source_dir)
    await conn.execute(
        "INSERT INTO paper_content "
        "(content_key, kind, arxiv_id, title, authors_json, year, abstract, "
        "source_path, source_dir_path, html_path, sections_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"arxiv:t3-{title}",
            "arxiv",
            f"t3-{title.replace(' ', '-')}",
            title,
            "[]",
            2024,
            "abstract",
            f"{src_dir}/source.tex",
            src_dir,
            f"{src_dir}/source.html",
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


def _write_minimal_asset(source_dir: Path, *, fig_id: str = "fig-001") -> None:
    asset = PaperAsset(
        figures=[
            FigureAsset(
                id=fig_id,
                caption="An interpretation of the data.",
                page=1,
                section="Methods",
                image_path=f"figures/{fig_id}.png",
            ),
        ],
        equations=[],
        sections=[SectionAsset(name="Methods", order=1)],
    )
    write_paper_asset(asset, source_dir)


# ─────────────────────────── schema tests ───────────────────────────


def test_rendered_slide_schema_validates_full_payload() -> None:
    """Happy-path Pydantic round-trip of a complete RenderedSlide."""
    payload = _rendered_payload(
        slide_index=3,
        pattern_kind="concept_2col",
        paper_id=42,
        frame_tex=(
            r"\begin{frame}{Method overview}"
            r"\begin{columns}\end{columns}"
            r"\end{frame}"
        ),
        figure_keys_used=["p0-fig-001"],
        callback_reads=[
            {"tool": "read_figure_block", "args": "{}", "result_excerpt": "..."},
        ],
    )
    rendered = RenderedSlide.model_validate(payload)
    assert rendered.slide_index == 3
    assert rendered.pattern_kind == "concept_2col"
    assert rendered.paper_id == 42
    assert rendered.figure_keys_used == ["p0-fig-001"]
    assert rendered.callback_reads[0]["tool"] == "read_figure_block"

    raw = rendered.model_dump_json()
    assert RenderedSlide.model_validate_json(raw) == rendered


# ─────────────────────────── happy-path renderer tests ──────────────


@pytest.mark.asyncio
async def test_sl_render_slide_title_pattern_emits_titlepage_frame(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """``title`` pattern: stubbed LLM emits the [plain] + \\titlepage skeleton."""
    title_slide = _planned(
        pattern_kind="title",
        title="",
        goal="Open the talk.",
    )
    other_slide = _planned(
        pattern_kind="motivation_figure",
        title="The problem",
        goal="Show the shared pain point.",
        paper_id=100,
        figure_key="p0-fig-001",
    )
    outline = _outline_with([title_slide, other_slide])
    briefs = [_brief(paper_id=100)]

    frame_tex = "\\begin{frame}[plain]\n  \\titlepage\n\\end{frame}"
    payload = _rendered_payload(
        slide_index=0,
        pattern_kind="title",
        frame_tex=frame_tex,
    )
    mock_completion = AsyncMock(side_effect=[_msg(content=json.dumps(payload))])

    from paperhub.agents.sl_render_slide import run_sl_render_slide

    with patch("paperhub.agents.sl_render_slide.litellm.acompletion", new=mock_completion):
        rendered = await run_sl_render_slide(
            planned_slide=title_slide,
            deck_outline=outline,
            paper_brief=None,
            all_briefs=briefs,
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            response_language="English",
        )

    assert rendered.frame_tex.startswith("\\begin{frame}[plain]")
    assert "\\titlepage" in rendered.frame_tex
    assert rendered.slide_index == 0
    assert rendered.pattern_kind == "title"
    assert rendered.figure_keys_used == []


@pytest.mark.asyncio
async def test_sl_render_slide_concept_2col_uses_figure_from_brief(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """``concept_2col`` pattern: figure_keys_used carries the assigned key."""
    slide = _planned(
        pattern_kind="concept_2col",
        title="Method overview",
        goal="Pitch the method.",
        paper_id=100,
        figure_key="p0-fig-001",
        key_points=["Idea 1", "Idea 2"],
    )
    outline = _outline_with([slide])
    briefs = [_brief(paper_id=100)]

    frame_tex = (
        "\\begin{frame}{Method overview}\n"
        "  \\begin{columns}[T]\n"
        "    \\begin{column}{0.55\\textwidth}\n"
        "      \\includegraphics[width=\\linewidth]{p0-fig-001}\n"
        "    \\end{column}\n"
        "    \\begin{column}{0.45\\textwidth}\n"
        "      \\begin{itemize}\\item Idea 1\\item Idea 2\\end{itemize}\n"
        "      \\begin{block}{Result}14% on LIBERO\\end{block}\n"
        "    \\end{column}\n"
        "  \\end{columns}\n"
        "\\end{frame}"
    )
    payload = _rendered_payload(
        slide_index=0,
        pattern_kind="concept_2col",
        paper_id=100,
        frame_tex=frame_tex,
        figure_keys_used=["p0-fig-001"],
    )
    mock_completion = AsyncMock(side_effect=[_msg(content=json.dumps(payload))])

    from paperhub.agents.sl_render_slide import run_sl_render_slide

    with patch("paperhub.agents.sl_render_slide.litellm.acompletion", new=mock_completion):
        rendered = await run_sl_render_slide(
            planned_slide=slide,
            deck_outline=outline,
            paper_brief=briefs[0],
            all_briefs=briefs,
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            response_language="English",
        )

    assert rendered.figure_keys_used == ["p0-fig-001"]
    assert "\\begin{columns}" in rendered.frame_tex
    assert "\\includegraphics" in rendered.frame_tex


@pytest.mark.asyncio
async def test_sl_render_slide_takeaway_closer_pattern_has_no_frametitle(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """``takeaway_closer`` pattern: must NOT contain ``\\frametitle``."""
    slide = _planned(
        pattern_kind="takeaway_closer",
        title="",
        goal="Land the take-away.",
        key_points=["Take-away.", "Open Q?"],
    )
    outline = _outline_with([slide])

    frame_tex = (
        "\\begin{frame}[plain]\n"
        "  \\centering\n"
        "  \\rule{0.6\\linewidth}{0.4pt}\\\\[1em]\n"
        "  {\\Large\\bfseries Take-away}\\\\[0.75em]\n"
        "  The three together suggest unification.\\\\[1em]\n"
        "  \\rule{0.6\\linewidth}{0.4pt}\\\\[1em]\n"
        "  \\begin{block}{Open Question}\n"
        "    \\itshape How do we combine them?\n"
        "  \\end{block}\n"
        "  \\vspace{1em}\n"
        "  \\textbf{\\Large Thank you. Questions?}\n"
        "\\end{frame}"
    )
    payload = _rendered_payload(
        slide_index=0,
        pattern_kind="takeaway_closer",
        frame_tex=frame_tex,
    )
    mock_completion = AsyncMock(side_effect=[_msg(content=json.dumps(payload))])

    from paperhub.agents.sl_render_slide import run_sl_render_slide

    with patch("paperhub.agents.sl_render_slide.litellm.acompletion", new=mock_completion):
        rendered = await run_sl_render_slide(
            planned_slide=slide,
            deck_outline=outline,
            paper_brief=None,
            all_briefs=[],
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            response_language="English",
        )

    assert "\\frametitle" not in rendered.frame_tex
    assert "[plain]" in rendered.frame_tex
    assert "\\rule" in rendered.frame_tex
    assert "Thank you" in rendered.frame_tex


# ─────────────────────────── validation failures ────────────────────


@pytest.mark.asyncio
async def test_sl_render_slide_math_stack_requires_equation_block(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """``math_stack`` with NO ``\\[...\\]`` block is rejected."""
    slide = _planned(
        pattern_kind="math_stack",
        title="Central equation",
        goal="Show the loss.",
        paper_id=100,
        equation_index=0,
    )
    outline = _outline_with([slide])
    briefs = [_brief(paper_id=100)]

    # Frame is a SINGLE \begin{frame}...\end{frame} (so the multi-frame
    # check passes) but contains no display-math block — math_stack must
    # carry at least one.
    frame_tex = (
        "\\begin{frame}{Central equation}\n"
        "  \\textbf{Loss:}\n"
        "  This is just prose, no equation.\n"
        "\\end{frame}"
    )
    payload = _rendered_payload(
        slide_index=0,
        pattern_kind="math_stack",
        paper_id=100,
        frame_tex=frame_tex,
    )
    mock_completion = AsyncMock(side_effect=[_msg(content=json.dumps(payload))])

    from paperhub.agents.sl_render_slide import run_sl_render_slide

    with (
        patch(
            "paperhub.agents.sl_render_slide.litellm.acompletion", new=mock_completion
        ),
        pytest.raises(ValueError, match="math_stack"),
    ):
        await run_sl_render_slide(
            planned_slide=slide,
            deck_outline=outline,
            paper_brief=briefs[0],
            all_briefs=briefs,
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            response_language="English",
        )

    status, error, _args, result = await _read_step_row(migrated_db)
    assert status == "error"
    assert error == "render_validation_failed"
    assert result.get("validation_failed") is True
    assert "math_stack" in result.get("validation_error", "")


@pytest.mark.asyncio
async def test_sl_render_slide_rejects_multi_frame_output(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """Two frame envs in one response are rejected."""
    slide = _planned(
        pattern_kind="motivation_figure",
        title="The problem",
        goal="Frame it.",
        paper_id=100,
        figure_key="p0-fig-001",
    )
    outline = _outline_with([slide])
    briefs = [_brief(paper_id=100)]

    frame_tex = (
        "\\begin{frame}{The problem}\\centering "
        "\\includegraphics[width=0.65\\linewidth]{p0-fig-001}\\end{frame}\n"
        "\\begin{frame}{Bonus}rogue extra frame\\end{frame}"
    )
    payload = _rendered_payload(
        slide_index=0,
        pattern_kind="motivation_figure",
        paper_id=100,
        frame_tex=frame_tex,
        figure_keys_used=["p0-fig-001"],
    )
    mock_completion = AsyncMock(side_effect=[_msg(content=json.dumps(payload))])

    from paperhub.agents.sl_render_slide import run_sl_render_slide

    with (
        patch(
            "paperhub.agents.sl_render_slide.litellm.acompletion", new=mock_completion
        ),
        pytest.raises(ValueError, match="expected exactly 1"),
    ):
        await run_sl_render_slide(
            planned_slide=slide,
            deck_outline=outline,
            paper_brief=briefs[0],
            all_briefs=briefs,
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            response_language="English",
        )

    status, error, _args, _result = await _read_step_row(migrated_db)
    assert status == "error"
    assert error == "render_validation_failed"


@pytest.mark.asyncio
async def test_sl_render_slide_rejects_parse_failure(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """Non-JSON LLM response is rejected with canonical ``render_parse_failed``."""
    slide = _planned(
        pattern_kind="title",
        title="",
        goal="Open.",
    )
    outline = _outline_with([slide])

    mock_completion = AsyncMock(side_effect=[_msg(content="this is not valid JSON")])

    from paperhub.agents.sl_render_slide import run_sl_render_slide

    with (
        patch(
            "paperhub.agents.sl_render_slide.litellm.acompletion", new=mock_completion
        ),
        pytest.raises(ValidationError),
    ):
        await run_sl_render_slide(
            planned_slide=slide,
            deck_outline=outline,
            paper_brief=None,
            all_briefs=[],
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            response_language="English",
        )

    status, error, _args, result = await _read_step_row(migrated_db)
    assert status == "error"
    assert error == "render_parse_failed"
    assert result["parse_error"]
    assert "ValidationError" in result["parse_error"]


# ─────────────────────────── bounded callback budget ────────────────


@pytest.mark.asyncio
async def test_sl_render_slide_bounded_callback_budget_enforced(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
    tmp_path: Path,
) -> None:
    """3rd ``read_section`` call is rejected; callback_reads has exactly 2 entries."""
    src_dir = tmp_path / "paper-cb"
    src_dir.mkdir()
    _write_minimal_asset(src_dir, fig_id="fig-001")

    pcid = await _seed_paper(
        migrated_db,
        title="Callback paper",
        sections=[
            {"name": "Methods", "chunks": ["Methods body text."]},
            {"name": "Results", "chunks": ["Results body text."]},
            {"name": "Discussion", "chunks": ["Discussion body text."]},
        ],
        source_dir=src_dir,
    )

    slide = _planned(
        pattern_kind="concept_2col",
        title="Method overview",
        goal="Pitch the method.",
        paper_id=pcid,
        figure_key="p0-fig-001",
        key_points=["Idea 1", "Idea 2"],
    )
    outline = _outline_with([slide])
    briefs = [_brief(paper_id=pcid)]

    frame_tex = (
        "\\begin{frame}{Method overview}\n"
        "  \\begin{columns}[T]\n"
        "    \\begin{column}{0.55\\textwidth}\n"
        "      \\includegraphics[width=\\linewidth]{p0-fig-001}\n"
        "    \\end{column}\n"
        "    \\begin{column}{0.45\\textwidth}\n"
        "      \\begin{itemize}\\item Idea 1\\item Idea 2\\end{itemize}\n"
        "    \\end{column}\n"
        "  \\end{columns}\n"
        "\\end{frame}"
    )
    final_payload = _rendered_payload(
        slide_index=0,
        pattern_kind="concept_2col",
        paper_id=pcid,
        frame_tex=frame_tex,
        figure_keys_used=["p0-fig-001"],
    )

    responses = [
        _msg(tool_calls=[_tool_call("c1", "read_section", {"name": "Methods"})]),
        _msg(tool_calls=[_tool_call("c2", "read_section", {"name": "Results"})]),
        _msg(tool_calls=[_tool_call("c3", "read_section", {"name": "Discussion"})]),
        _msg(content=json.dumps(final_payload)),
    ]
    mock_completion = AsyncMock(side_effect=responses)

    from paperhub.agents.sl_render_slide import run_sl_render_slide

    with patch(
        "paperhub.agents.sl_render_slide.litellm.acompletion", new=mock_completion
    ):
        rendered = await run_sl_render_slide(
            planned_slide=slide,
            deck_outline=outline,
            paper_brief=briefs[0],
            all_briefs=briefs,
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            response_language="English",
            conn=migrated_db,
        )

    # Bounded callback budget: only 2 read_section calls landed in the log.
    assert len(rendered.callback_reads) == 2
    assert all(c["tool"] == "read_section" for c in rendered.callback_reads)

    # The 3rd tool message the LLM saw was the budget-exhausted error marker.
    last_call_msgs = mock_completion.call_args_list[-1].kwargs["messages"]
    tool_results = [m for m in last_call_msgs if m.get("role") == "tool"]
    assert len(tool_results) == 3
    last_body = tool_results[-1]["content"].lower()
    assert "budget" in last_body or "exhausted" in last_body

    # Tracer records the same.
    status, error, _args, result = await _read_step_row(migrated_db)
    assert status == "ok"
    assert error is None
    assert result["callback_reads_count"] == 2
    assert len(result["callback_reads_summary"]) == 2


# ─────────────────────────── cross-paper schema pruning ─────────────


@pytest.mark.asyncio
async def test_sl_render_slide_cross_paper_pattern_no_callback_tools(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """Cross-paper pattern: callback tools are absent from the LLM schema."""
    slide = _planned(
        pattern_kind="bottlenecks_table",
        title="Three bottlenecks",
        goal="Map each paper to one axis.",
        key_points=[
            "Paper 100: latency",
            "Paper 101: data",
            "Paper 102: stability",
        ],
    )
    outline = _outline_with([slide])
    briefs = [_brief(paper_id=100 + i) for i in range(3)]

    frame_tex = (
        "\\begin{frame}{Three bottlenecks}\n"
        "  \\begin{tabular}{l l l}\n"
        "    \\toprule\n"
        "    Bottleneck & Paper & Speedup \\\\\n"
        "    \\midrule\n"
        "    Latency & [1] A & 14% \\\\\n"
        "    Data & [2] B & 7% \\\\\n"
        "    Stability & [3] C & 3% \\\\\n"
        "    \\bottomrule\n"
        "  \\end{tabular}\n"
        "\\end{frame}"
    )
    payload = _rendered_payload(
        slide_index=0,
        pattern_kind="bottlenecks_table",
        frame_tex=frame_tex,
    )
    mock_completion = AsyncMock(side_effect=[_msg(content=json.dumps(payload))])

    from paperhub.agents.sl_render_slide import run_sl_render_slide

    with patch(
        "paperhub.agents.sl_render_slide.litellm.acompletion", new=mock_completion
    ):
        await run_sl_render_slide(
            planned_slide=slide,
            deck_outline=outline,
            paper_brief=None,
            all_briefs=briefs,
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            response_language="English",
        )

    # Inspect the kwargs passed to litellm.acompletion — cross-paper pattern
    # must NOT pass any tools schema.
    call_kwargs = mock_completion.call_args_list[0].kwargs
    assert "tools" not in call_kwargs, (
        "cross-paper patterns must not wire callback tools into the schema; "
        "the schema must be pruned so the LLM cannot call back-into-paper "
        "tools when no paper is in scope."
    )
    assert "tool_choice" not in call_kwargs


# ─────────────────────────── trace contract ─────────────────────────


@pytest.mark.asyncio
async def test_sl_render_slide_trace_captures_callback_state(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
    tmp_path: Path,
) -> None:
    """Happy path with 1 callback: tracer records callback_reads_count + summary."""
    src_dir = tmp_path / "paper-trace"
    src_dir.mkdir()
    _write_minimal_asset(src_dir, fig_id="fig-001")

    pcid = await _seed_paper(
        migrated_db,
        title="Trace paper",
        sections=[
            {"name": "Methods", "chunks": ["Methods body text."]},
        ],
        source_dir=src_dir,
    )

    slide = _planned(
        pattern_kind="motivation_figure",
        title="The problem",
        goal="Show it.",
        paper_id=pcid,
        figure_key="p0-fig-001",
    )
    outline = _outline_with([slide])
    briefs = [_brief(paper_id=pcid)]

    frame_tex = (
        "\\begin{frame}{The problem}\n"
        "  \\centering\n"
        "  \\includegraphics[width=0.65\\linewidth]{p0-fig-001}\\\\\n"
        "  \\textbf{One-sentence motivation.}\n"
        "\\end{frame}"
    )
    final_payload = _rendered_payload(
        slide_index=0,
        pattern_kind="motivation_figure",
        paper_id=pcid,
        frame_tex=frame_tex,
        figure_keys_used=["p0-fig-001"],
    )

    responses = [
        _msg(tool_calls=[_tool_call("c1", "read_section", {"name": "Methods"})]),
        _msg(content=json.dumps(final_payload)),
    ]
    mock_completion = AsyncMock(side_effect=responses)

    from paperhub.agents.sl_render_slide import run_sl_render_slide

    with patch(
        "paperhub.agents.sl_render_slide.litellm.acompletion", new=mock_completion
    ):
        rendered = await run_sl_render_slide(
            planned_slide=slide,
            deck_outline=outline,
            paper_brief=briefs[0],
            all_briefs=briefs,
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            response_language="English",
            conn=migrated_db,
        )

    assert len(rendered.callback_reads) == 1
    assert rendered.callback_reads[0]["tool"] == "read_section"

    status, error, args, result = await _read_step_row(migrated_db)
    assert status == "ok"
    assert error is None

    # args_redacted_json contract
    assert args["slide_index"] == 0
    assert args["pattern_kind"] == "motivation_figure"
    assert args["paper_id"] == pcid
    assert args["figure_key"] == "p0-fig-001"
    assert args["callback_budget"] == 2
    assert args["has_callback_tools"] is True

    # result_summary_json contract
    assert result["callback_reads_count"] == 1
    assert len(result["callback_reads_summary"]) == 1
    assert result["figure_keys_used"] == ["p0-fig-001"]
    assert result["frame_tex_first_200_chars"].startswith("\\begin{frame}")
    assert result["parse_status"] == "ok"


# ─────────────────────────── programmer-bug boundary ────────────────


@pytest.mark.asyncio
async def test_sl_render_slide_raises_when_planned_slide_not_in_outline(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """Unresolvable ``planned_slide`` raises ``ValueError`` (not silent 0)."""
    slide_a = _planned(
        pattern_kind="title",
        title="In outline",
        goal="Goal A — open the talk.",
    )
    outline = _outline_with([slide_a])

    # A NEW PlannedSlide B (not in the outline; different goal so the
    # (pattern_kind, title, goal) soft-match also fails).
    slide_b = _planned(
        pattern_kind="title",
        title="Not in outline",
        goal="Goal B — totally different.",
    )

    mock_completion = AsyncMock()

    from paperhub.agents.sl_render_slide import run_sl_render_slide

    with (
        patch(
            "paperhub.agents.sl_render_slide.litellm.acompletion", new=mock_completion
        ),
        pytest.raises(ValueError, match="could not resolve slide_index"),
    ):
        await run_sl_render_slide(
            planned_slide=slide_b,
            deck_outline=outline,
            paper_brief=None,
            all_briefs=[],
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            response_language="English",
        )

    # The LLM was never even called — the boundary check raised first.
    mock_completion.assert_not_called()
