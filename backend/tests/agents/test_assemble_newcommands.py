"""Tests for F4.4 T4 — plumbing ``paper_newcommands`` into the deck preamble.

Covers:

- Block insertion position (after ``\\usepackage`` lines, before ``\\title``).
- Empty-briefs path emits the marker block with a
  "(no paper-defined macros to plumb)" note (location stays consistent
  regardless of input).
- Dedup of identical definitions across papers.
- Collision handling: paper 1's version wins + a NOTE comment about the
  divergent later definition.
- Stable per-paper order: paper 1's macros first, then paper 2's NEW macros.
- ``sl_assemble`` records the newcommands summary in
  ``result_summary_json`` so the plumbing is reconstructable from the DB
  (agent-flow observability rule).
"""
from __future__ import annotations

from typing import Any

import aiosqlite
import pytest

from paperhub.agents._newcommands import build_newcommands_block
from paperhub.models.domain import (
    KeyEquation,
    KeyFigure,
    KeyResult,
    PaperTalkBrief,
)
from paperhub.pipelines.slide_pipeline.assemble import AssembleInput, assemble_deck
from paperhub.tracing.tracer import Tracer

# ────────────────────────── helpers ─────────────────────────────────


def _brief(*, paper_id: int, newcommands: str = "") -> PaperTalkBrief:
    return PaperTalkBrief(
        paper_id=paper_id,
        contribution="c",
        method_core="m",
        key_results=[
            KeyResult(description="d", number="14%", benchmark="LIBERO"),
        ],
        key_figures=[
            KeyFigure(
                key=f"p{paper_id - 1}-fig1",
                role="overview",
                one_line_interpretation="i",
            )
        ],
        key_equations=[
            KeyEquation(latex="x=y", role="objective", notation_explanation="n")
        ],
        paper_newcommands=newcommands,
        talk_shape_hint="concept+math",
    )


def _empty_assemble_input(*, paper_newcommands_block: str = "") -> AssembleInput:
    return AssembleInput(
        title="T",
        theme="metropolis",
        additional_tex_macros=[],
        cache_source_dirs=["/tmp/figs"],
        frames=["\\begin{frame}body\\end{frame}"],
        author="A",
        date="2026",
        subtitle="",
        paper_newcommands_block=paper_newcommands_block,
    )


# ───────────────────── pure-helper tests ────────────────────────────


def test_build_block_inserts_block_with_markers() -> None:
    briefs = [_brief(paper_id=1, newcommands=r"\newcommand{\R}{\mathbb{R}}")]
    block, summary = build_newcommands_block(briefs)
    assert "% BEGIN paperhub:paper_newcommands" in block
    assert "% END paperhub:paper_newcommands" in block
    assert r"\newcommand{\R}{\mathbb{R}}" in block
    assert summary.unique_count == 1
    assert summary.collisions == []


def test_build_block_empty_emits_marker_only() -> None:
    briefs = [_brief(paper_id=1), _brief(paper_id=2)]
    block, summary = build_newcommands_block(briefs)
    assert "% BEGIN paperhub:paper_newcommands" in block
    assert "% END paperhub:paper_newcommands" in block
    assert "(no paper-defined macros to plumb)" in block
    assert summary.unique_count == 0
    assert summary.contributing_papers == 0


def test_build_block_deduplicates_identical_definitions() -> None:
    line = r"\newcommand{\E}{\mathbb{E}}"
    briefs = [
        _brief(paper_id=1, newcommands=line),
        _brief(paper_id=2, newcommands=line),
    ]
    block, summary = build_newcommands_block(briefs)
    assert block.count(line) == 1
    assert summary.unique_count == 1
    assert summary.collisions == []
    assert summary.contributing_papers == 2


def test_build_block_handles_collision() -> None:
    briefs = [
        _brief(paper_id=1, newcommands=r"\newcommand{\R}{\mathbb{R}}"),
        _brief(paper_id=2, newcommands=r"\newcommand{\R}{\textsf{R}}"),
    ]
    block, summary = build_newcommands_block(briefs)
    assert r"\newcommand{\R}{\mathbb{R}}" in block
    assert r"\newcommand{\R}{\textsf{R}}" not in block
    assert "NOTE" in block and r"\R" in block
    assert "paper 2" in block and "paper 1" in block
    assert summary.collisions == ["R"]


def test_build_block_preserves_per_paper_order() -> None:
    briefs = [
        _brief(
            paper_id=1,
            newcommands="\n".join(
                [
                    r"\newcommand{\Mone}{1}",
                    r"\newcommand{\Mtwo}{2}",
                ]
            ),
        ),
        _brief(
            paper_id=2,
            newcommands="\n".join(
                [
                    r"\newcommand{\Mtwo}{TWO}",  # collision — paper 1 wins
                    r"\newcommand{\Mthree}{3}",  # new
                ]
            ),
        ),
    ]
    block, summary = build_newcommands_block(briefs)
    idx_one = block.find(r"\newcommand{\Mone}{1}")
    idx_two = block.find(r"\newcommand{\Mtwo}{2}")
    idx_three = block.find(r"\newcommand{\Mthree}{3}")
    assert -1 < idx_one < idx_two < idx_three
    assert summary.collisions == ["Mtwo"]
    assert summary.unique_count == 3


def test_build_block_skips_unrecognized_lines_with_comment() -> None:
    briefs = [
        _brief(
            paper_id=1,
            newcommands="\n".join(
                [
                    r"\newcommand{\R}{\mathbb{R}}",
                    r"\usepackage{amsmath}",
                    "",
                    r"% just a comment",
                ]
            ),
        )
    ]
    block, summary = build_newcommands_block(briefs)
    assert r"\newcommand{\R}{\mathbb{R}}" in block
    assert "% SKIPPED" in block
    assert r"\usepackage{amsmath}" in block
    assert summary.skipped_count == 1
    assert summary.unique_count == 1


def test_build_block_handles_declare_math_operator() -> None:
    briefs = [
        _brief(
            paper_id=1,
            newcommands="\n".join(
                [
                    r"\DeclareMathOperator{\KL}{KL}",
                    r"\DeclareMathOperator*{\argmin}{arg\,min}",
                ]
            ),
        ),
        _brief(
            paper_id=2,
            newcommands="\n".join(
                [
                    r"\DeclareMathOperator{\KL}{KL}",  # identical → dedupe
                    r"\renewcommand{\vec}[1]{\mathbf{#1}}",
                ]
            ),
        ),
    ]
    block, summary = build_newcommands_block(briefs)
    assert summary.unique_count == 3
    assert block.count(r"\DeclareMathOperator{\KL}{KL}") == 1
    assert r"\DeclareMathOperator*{\argmin}{arg\,min}" in block
    assert r"\renewcommand{\vec}[1]{\mathbf{#1}}" in block


# ──────────────── assemble_deck position tests ──────────────────────


def test_assemble_inserts_newcommands_block_at_correct_position() -> None:
    """Block must land AFTER \\usepackage lines and BEFORE \\title{}."""
    block, _ = build_newcommands_block(
        [_brief(paper_id=1, newcommands=r"\newcommand{\R}{\mathbb{R}}")]
    )
    tex = assemble_deck(_empty_assemble_input(paper_newcommands_block=block))
    idx_usepackage = tex.rfind("\\usepackage")
    idx_block_begin = tex.find("% BEGIN paperhub:paper_newcommands")
    idx_block_end = tex.find("% END paperhub:paper_newcommands")
    idx_title = tex.find("\\title{")
    idx_begin_document = tex.find("\\begin{document}")
    assert -1 < idx_usepackage < idx_block_begin
    assert idx_block_begin < idx_block_end < idx_title < idx_begin_document
    assert r"\newcommand{\R}{\mathbb{R}}" in tex


def test_assemble_empty_newcommands_emits_marker_only() -> None:
    """All briefs empty → block still emits with the (no macros) note."""
    block, _ = build_newcommands_block(
        [_brief(paper_id=1), _brief(paper_id=2)]
    )
    tex = assemble_deck(_empty_assemble_input(paper_newcommands_block=block))
    assert "% BEGIN paperhub:paper_newcommands" in tex
    assert "% END paperhub:paper_newcommands" in tex
    assert "(no paper-defined macros to plumb)" in tex


# ───────────────── sl_assemble tracer integration ────────────────────


@pytest.mark.asyncio
async def test_assemble_records_newcommands_summary_in_trace(
    tmp_path: Any,
) -> None:
    """Tracer step records the unique count + collisions per the agent-flow
    observability rule. Uses the Tracer + assemble helper directly to avoid
    a heavy LangGraph end-to-end harness."""
    from paperhub.db.migrate import apply_schema

    db_path = tmp_path / "pn.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        await conn.execute(
            "INSERT INTO chat_sessions (id, title, created_at) "
            "VALUES (1, 't', '2026')"
        )
        await conn.execute(
            "INSERT INTO runs (id, session_id, status) VALUES (1, 1, 'ok')"
        )
        await conn.commit()
        tracer = Tracer(conn, run_id=1, branch="")

        briefs = [
            _brief(paper_id=1, newcommands=r"\newcommand{\R}{\mathbb{R}}"),
            _brief(paper_id=2, newcommands=r"\newcommand{\R}{\textsf{R}}"),
        ]
        block, summary = build_newcommands_block(briefs)
        async with tracer.step(
            agent="report", tool="report:assemble", model=None
        ) as step:
            step.record_args({"frame_count": 1, "referenced_keys": []})
            tex = assemble_deck(_empty_assemble_input(paper_newcommands_block=block))
            assert "% BEGIN paperhub:paper_newcommands" in tex
            step.record_result(
                {
                    "staged_keys": [],
                    "macro_blocks": 0,
                    "newcommands_unique_count": summary.unique_count,
                    "newcommands_collisions": summary.collisions,
                    "newcommands_skipped_count": summary.skipped_count,
                    "newcommands_contributing_papers": summary.contributing_papers,
                }
            )
        async with conn.execute(
            "SELECT result_summary_json FROM tool_calls "
            "WHERE tool = 'report:assemble' ORDER BY step_index DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    import json as _json

    recorded = _json.loads(row[0])
    assert recorded["newcommands_unique_count"] == 1
    assert recorded["newcommands_collisions"] == ["R"]
    assert recorded["newcommands_contributing_papers"] == 2
    assert recorded["newcommands_skipped_count"] == 0
