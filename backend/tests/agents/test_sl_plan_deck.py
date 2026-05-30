"""Tests for the F4.4 T2 cross-paper deck planner.

Covers:

- ``DeckOutline`` / ``PlannedSlide`` Pydantic schema (happy path +
  invalid ``pattern_kind`` + empty-title rejection).
- Happy paths: multi-paper emits ``bottlenecks_table`` + ``concept_2col``
  per paper + ``takeaway_closer``; single-paper skips multi-only
  patterns.
- Hallucination-rejection pass: bad ``figure_key`` / ``paper_id`` /
  ``equation_index`` each raise ``ValueError`` AND flip the tracer step
  to ``status='error'``.
- Parse failure: non-JSON LLM response raises ``ValidationError`` AND
  flips status to ``'error'`` with the canonical
  ``"plan_parse_failed"`` marker (per the agent-flow observability iron
  rule — no silent fallback).
- Trace contract: ``args_redacted_json`` / ``result_summary_json``
  carry the expected reconstruct-able keys.

Mirrors the fixture style of ``tests/agents/test_sl_paper_brief.py``.
"""
from __future__ import annotations

import json
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
)

# ─────────────────────────── helpers ────────────────────────────────


def _msg(content: str) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _brief(
    *,
    paper_id: int,
    figure_keys: tuple[str, ...] = ("p0-fig-001", "p0-fig-002"),
    n_equations: int = 1,
    shape: str = "concept+math+results",
) -> PaperTalkBrief:
    """Build a minimal PaperTalkBrief for planner-input fixtures."""
    return PaperTalkBrief(
        paper_id=paper_id,
        contribution=f"Paper {paper_id} contributes X.",
        method_core=f"Paper {paper_id} introduces Y via Z.",
        key_results=[
            KeyResult(
                description=f"Result for paper {paper_id}",
                number="14%",
                benchmark="LIBERO",
            )
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
                latex=rf"\mathcal{{L}}_{paper_id} = \sum_i x_i^{idx}",
                role="loss",
                notation_explanation=f"L is loss; x_i is sample; index {idx}.",
            )
            for idx in range(n_equations)
        ],
        paper_newcommands="",
        talk_shape_hint=shape,  # type: ignore[arg-type]
    )


def _full_outline_payload(
    *,
    talk_title: str = "Three Bottlenecks Toward a Unified Policy",
    slides: list[dict[str, Any]] | None = None,
    talk_subtitle: str | None = None,
    style_profile_name: str = "default",
) -> dict[str, Any]:
    return {
        "talk_title": talk_title,
        "talk_subtitle": talk_subtitle,
        "style_profile_name": style_profile_name,
        "slides": slides or [],
    }


def _planned_slide(
    *,
    pattern_kind: str,
    title: str = "Slide",
    goal: str = "Land the idea.",
    paper_id: int | None = None,
    figure_key: str | None = None,
    equation_index: int | None = None,
    key_points: list[str] | None = None,
    chunk_ids: list[int] | None = None,
) -> dict[str, Any]:
    return {
        "pattern_kind": pattern_kind,
        "title": title,
        "goal": goal,
        "paper_id": paper_id,
        "figure_key": figure_key,
        "equation_index": equation_index,
        "key_points": key_points or [],
        "chunk_ids": chunk_ids or [],
    }


def _multi_paper_outline_dict(
    briefs: list[PaperTalkBrief],
    *,
    figure_overrides: dict[int, str] | None = None,
    paper_id_overrides: dict[int, int] | None = None,
    equation_idx_overrides: dict[int, int] | None = None,
) -> dict[str, Any]:
    """Build a valid multi-paper outline dict for the 3-brief fixture.

    Overrides let individual hallucination tests poke a single slide.
    """
    figure_overrides = figure_overrides or {}
    paper_id_overrides = paper_id_overrides or {}
    equation_idx_overrides = equation_idx_overrides or {}

    slides: list[dict[str, Any]] = []
    slides.append(_planned_slide(pattern_kind="title", title="", goal="Open the talk."))
    slides.append(
        _planned_slide(
            pattern_kind="references",
            title="Papers covered",
            goal="Anchor the audience in the three sources.",
        )
    )
    slides.append(
        _planned_slide(
            pattern_kind="motivation_figure",
            title="The problem",
            goal="Show the shared pain point.",
            paper_id=briefs[0].paper_id,
            figure_key=briefs[0].key_figures[0].key,
        )
    )
    slides.append(
        _planned_slide(
            pattern_kind="bottlenecks_table",
            title="Three bottlenecks",
            goal="Map each paper to one axis.",
            key_points=[
                f"Paper {briefs[0].paper_id}: latency",
                f"Paper {briefs[1].paper_id}: data",
                f"Paper {briefs[2].paper_id}: stability",
            ],
        )
    )
    for i, brief in enumerate(briefs):
        slides.append(
            _planned_slide(
                pattern_kind="concept_2col",
                title=f"{brief.paper_id} concept",
                goal=f"Pitch paper {brief.paper_id} in one slide.",
                paper_id=paper_id_overrides.get(len(slides), brief.paper_id),
                figure_key=figure_overrides.get(
                    len(slides), brief.key_figures[0].key
                ),
                key_points=[f"Idea {i}.1", f"Idea {i}.2"],
            )
        )
        slides.append(
            _planned_slide(
                pattern_kind="math_stack",
                title=f"{brief.paper_id} math",
                goal=f"Show the central equation for paper {brief.paper_id}.",
                paper_id=brief.paper_id,
                equation_index=equation_idx_overrides.get(len(slides), 0),
            )
        )
    slides.append(
        _planned_slide(
            pattern_kind="proposed_direction_placeholder",
            title="Proposed direction",
            goal="Cue the speaker to add their synthesis.",
        )
    )
    slides.append(
        _planned_slide(
            pattern_kind="plan_numbered",
            title="Plan",
            goal="Lay out the next 4 steps.",
            key_points=["1. step", "2. step", "3. step", "4. step"],
        )
    )
    slides.append(
        _planned_slide(
            pattern_kind="takeaway_closer",
            title="",
            goal="Leave the audience with one take-away.",
            key_points=["The three together suggest unification.", "Open Q: how?"],
        )
    )
    return _full_outline_payload(slides=slides)


def _single_paper_outline_dict(brief: PaperTalkBrief) -> dict[str, Any]:
    slides = [
        _planned_slide(pattern_kind="title", title="", goal="Open."),
        _planned_slide(
            pattern_kind="motivation_figure",
            title="Motivation",
            goal="Frame the problem.",
            paper_id=brief.paper_id,
            figure_key=brief.key_figures[0].key,
        ),
        _planned_slide(
            pattern_kind="concept_2col",
            title="Method overview",
            goal="Pitch the method.",
            paper_id=brief.paper_id,
            figure_key=brief.key_figures[1].key,
            key_points=["Idea 1", "Idea 2"],
        ),
        _planned_slide(
            pattern_kind="math_stack",
            title="Central equation",
            goal="Show the loss.",
            paper_id=brief.paper_id,
            equation_index=0,
        ),
        _planned_slide(
            pattern_kind="takeaway_closer",
            title="",
            goal="Land the take-away.",
            key_points=["Take-away.", "Open Q?"],
        ),
    ]
    return _full_outline_payload(
        talk_title="Single-paper focused talk",
        slides=slides,
    )


# ─────────────────────────── schema tests ───────────────────────────


def test_deck_outline_schema_validates_full_payload() -> None:
    """Happy-path Pydantic round-trip of a complete DeckOutline."""
    briefs = [_brief(paper_id=10 + i) for i in range(3)]
    payload = _multi_paper_outline_dict(briefs)
    outline = DeckOutline.model_validate(payload)
    assert outline.talk_title.startswith("Three Bottlenecks")
    assert outline.style_profile_name == "default"
    assert len(outline.slides) >= 8
    assert outline.slides[0].pattern_kind == "title"
    # Last slide should be the closer.
    assert outline.slides[-1].pattern_kind == "takeaway_closer"
    # JSON round-trip.
    raw = outline.model_dump_json()
    assert DeckOutline.model_validate_json(raw) == outline


def test_deck_outline_rejects_unknown_pattern_kind() -> None:
    """An invalid pattern_kind enum value is rejected."""
    payload = _full_outline_payload(
        slides=[_planned_slide(pattern_kind="not_a_pattern", title="x", goal="g")]
    )
    with pytest.raises(ValidationError):
        DeckOutline.model_validate(payload)


def test_deck_outline_rejects_empty_talk_title() -> None:
    """talk_title=='' is rejected by the min_length=1 constraint."""
    payload = _full_outline_payload(talk_title="")
    with pytest.raises(ValidationError):
        DeckOutline.model_validate(payload)


def test_planned_slide_rejects_empty_goal() -> None:
    """goal=='' is rejected by the min_length=1 constraint — the renderer
    relies on goal to keep the slide on-message."""
    with pytest.raises(ValidationError):
        PlannedSlide.model_validate(
            _planned_slide(pattern_kind="title", title="x", goal="")
        )


# ─────────────────────────── planner tests ──────────────────────────


@pytest.mark.asyncio
async def test_sl_plan_deck_multi_paper_uses_bottlenecks_pattern(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """Stubbed LLM emits a valid outline for 3 briefs; assert the outline
    contains at least one bottlenecks_table AND a concept_2col per paper
    AND a takeaway_closer."""
    briefs = [_brief(paper_id=10 + i) for i in range(3)]
    outline_dict = _multi_paper_outline_dict(briefs)
    mock_completion = AsyncMock(side_effect=[_msg(json.dumps(outline_dict))])

    from paperhub.agents.sl_plan_deck import run_sl_plan_deck

    with patch("paperhub.agents.sl_plan_deck.litellm.acompletion", new=mock_completion):
        outline = await run_sl_plan_deck(
            briefs=briefs,
            target_slide_count=12,
            talk_title_hint="Three bottlenecks",
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            deps=None,
            response_language="English",
        )

    kinds = [s.pattern_kind for s in outline.slides]
    assert kinds.count("bottlenecks_table") >= 1
    # One concept_2col per paper.
    per_paper_concept = [
        s for s in outline.slides
        if s.pattern_kind == "concept_2col"
    ]
    paper_ids_covered = {s.paper_id for s in per_paper_concept}
    assert paper_ids_covered == {b.paper_id for b in briefs}
    assert "takeaway_closer" in kinds


@pytest.mark.asyncio
async def test_sl_plan_deck_single_paper_skips_multi_only_patterns(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """For N==1 the outline must have no bottlenecks_table, no references,
    and no proposed_direction_placeholder."""
    briefs = [_brief(paper_id=42)]
    outline_dict = _single_paper_outline_dict(briefs[0])
    mock_completion = AsyncMock(side_effect=[_msg(json.dumps(outline_dict))])

    from paperhub.agents.sl_plan_deck import run_sl_plan_deck

    with patch("paperhub.agents.sl_plan_deck.litellm.acompletion", new=mock_completion):
        outline = await run_sl_plan_deck(
            briefs=briefs,
            target_slide_count=15,
            talk_title_hint=None,
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            deps=None,
            response_language="English",
        )

    kinds = {s.pattern_kind for s in outline.slides}
    assert "bottlenecks_table" not in kinds
    assert "references" not in kinds
    assert "proposed_direction_placeholder" not in kinds
    assert "takeaway_closer" in kinds


async def _read_step_row(
    conn: aiosqlite.Connection,
) -> tuple[str, str | None, dict[str, Any], dict[str, Any]]:
    """Read the single ``report:plan_deck`` step row from tool_calls."""
    async with conn.execute(
        "SELECT status, error, args_redacted_json, result_summary_json "
        "FROM tool_calls WHERE tool = 'report:plan_deck'"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None, "expected one report:plan_deck step row"
    status, error, args_json, result_json = row
    return (
        str(status),
        None if error is None else str(error),
        json.loads(args_json),
        json.loads(result_json),
    )


@pytest.mark.asyncio
async def test_sl_plan_deck_rejects_hallucinated_figure_key(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """An outline with figure_key='p99-fake' must raise ValueError +
    tracer step status='error'."""
    briefs = [_brief(paper_id=10 + i) for i in range(3)]
    # Patch the first concept_2col slide (index 4 in the multi-paper
    # skeleton: title/refs/motivation/bottlenecks/concept_2col) to point at
    # a key no brief contains.
    outline_dict = _multi_paper_outline_dict(
        briefs, figure_overrides={4: "p99-fake"}
    )
    mock_completion = AsyncMock(side_effect=[_msg(json.dumps(outline_dict))])

    from paperhub.agents.sl_plan_deck import run_sl_plan_deck

    with (
        patch("paperhub.agents.sl_plan_deck.litellm.acompletion", new=mock_completion),
        pytest.raises(ValueError, match="hallucinated"),
    ):
        await run_sl_plan_deck(
            briefs=briefs,
            target_slide_count=12,
            talk_title_hint=None,
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            deps=None,
            response_language="English",
        )

    status, error, _args, result = await _read_step_row(migrated_db)
    assert status == "error"
    assert error == "plan_validation_failed"
    assert result.get("validation_failed") is True
    assert "hallucinated" in result.get("validation_error", "")


@pytest.mark.asyncio
async def test_sl_plan_deck_rejects_hallucinated_paper_id(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """An outline with paper_id=999 must raise ValueError + status='error'."""
    briefs = [_brief(paper_id=10 + i) for i in range(3)]
    outline_dict = _multi_paper_outline_dict(
        briefs, paper_id_overrides={4: 999}
    )
    mock_completion = AsyncMock(side_effect=[_msg(json.dumps(outline_dict))])

    from paperhub.agents.sl_plan_deck import run_sl_plan_deck

    with (
        patch("paperhub.agents.sl_plan_deck.litellm.acompletion", new=mock_completion),
        pytest.raises(ValueError, match="hallucinated"),
    ):
        await run_sl_plan_deck(
            briefs=briefs,
            target_slide_count=12,
            talk_title_hint=None,
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            deps=None,
            response_language="English",
        )

    status, error, _args, _result = await _read_step_row(migrated_db)
    assert status == "error"
    assert error == "plan_validation_failed"


@pytest.mark.asyncio
async def test_sl_plan_deck_rejects_invalid_equation_index(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """An outline with equation_index=5 for a paper with 1 equation must
    raise ValueError + status='error'."""
    briefs = [_brief(paper_id=10 + i, n_equations=1) for i in range(3)]
    # The math_stack slide for paper 10 sits at index 5 in the skeleton:
    # title/refs/motivation/bottlenecks/concept_2col[paper10]/math_stack[paper10]
    outline_dict = _multi_paper_outline_dict(
        briefs, equation_idx_overrides={5: 5}
    )
    mock_completion = AsyncMock(side_effect=[_msg(json.dumps(outline_dict))])

    from paperhub.agents.sl_plan_deck import run_sl_plan_deck

    with (
        patch("paperhub.agents.sl_plan_deck.litellm.acompletion", new=mock_completion),
        pytest.raises(ValueError, match="equation_index"),
    ):
        await run_sl_plan_deck(
            briefs=briefs,
            target_slide_count=12,
            talk_title_hint=None,
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            deps=None,
            response_language="English",
        )

    status, error, _args, _result = await _read_step_row(migrated_db)
    assert status == "error"
    assert error == "plan_validation_failed"


@pytest.mark.asyncio
async def test_sl_plan_deck_rejects_empty_title_on_content_pattern(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """An outline with title='' on a content pattern (concept_2col) must
    raise ValueError + tracer step status='error' with
    'plan_validation_failed'. Mirrors the existing hallucination-rejection
    pattern: T3 would otherwise silently emit ``\\frametitle{}`` on the
    rendered frame.
    """
    briefs = [_brief(paper_id=10 + i) for i in range(3)]
    outline_dict = _multi_paper_outline_dict(briefs)
    # Patch the first concept_2col slide (index 4 in the multi-paper
    # skeleton) to clear its title — schema-valid but semantically broken
    # for a content pattern.
    outline_dict["slides"][4]["title"] = ""
    mock_completion = AsyncMock(side_effect=[_msg(json.dumps(outline_dict))])

    from paperhub.agents.sl_plan_deck import run_sl_plan_deck

    with (
        patch("paperhub.agents.sl_plan_deck.litellm.acompletion", new=mock_completion),
        pytest.raises(ValueError, match="empty title|content pattern"),
    ):
        await run_sl_plan_deck(
            briefs=briefs,
            target_slide_count=12,
            talk_title_hint=None,
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            deps=None,
            response_language="English",
        )

    status, error, _args, result = await _read_step_row(migrated_db)
    assert status == "error"
    assert error == "plan_validation_failed"
    assert result.get("validation_failed") is True
    assert "empty title" in result.get("validation_error", "")


@pytest.mark.asyncio
async def test_sl_plan_deck_allows_empty_title_on_title_and_closer_patterns(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """Positive control: title='' on the ``title`` and ``takeaway_closer``
    patterns is intentional (those layouts use ``\\titlepage`` / ``\\rule``
    instead of ``\\frametitle``) and MUST NOT raise. The multi-paper
    fixture already sets title='' on both patterns; assert the planner
    accepts it.
    """
    briefs = [_brief(paper_id=10 + i) for i in range(3)]
    outline_dict = _multi_paper_outline_dict(briefs)
    # Sanity-check: the fixture's slide 0 (title) and last slide
    # (takeaway_closer) have empty titles — these are the legitimate cases.
    assert outline_dict["slides"][0]["pattern_kind"] == "title"
    assert outline_dict["slides"][0]["title"] == ""
    assert outline_dict["slides"][-1]["pattern_kind"] == "takeaway_closer"
    assert outline_dict["slides"][-1]["title"] == ""
    mock_completion = AsyncMock(side_effect=[_msg(json.dumps(outline_dict))])

    from paperhub.agents.sl_plan_deck import run_sl_plan_deck

    with patch("paperhub.agents.sl_plan_deck.litellm.acompletion", new=mock_completion):
        outline = await run_sl_plan_deck(
            briefs=briefs,
            target_slide_count=12,
            talk_title_hint=None,
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            deps=None,
            response_language="English",
        )

    assert outline.slides[0].title == ""
    assert outline.slides[-1].title == ""
    status, error, _args, _result = await _read_step_row(migrated_db)
    assert status == "ok"
    assert error is None


@pytest.mark.asyncio
async def test_sl_plan_deck_marks_error_on_parse_failure(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """When the LLM emits non-JSON, ValidationError is raised AND the
    tracer step status='error' with the canonical 'plan_parse_failed'
    marker. Per the agent-flow observability iron rule — no silent
    fallback to structurally-valid garbage downstream."""
    briefs = [_brief(paper_id=10 + i) for i in range(3)]
    mock_completion = AsyncMock(side_effect=[_msg("this is not valid JSON")])

    from paperhub.agents.sl_plan_deck import run_sl_plan_deck

    with (
        patch("paperhub.agents.sl_plan_deck.litellm.acompletion", new=mock_completion),
        pytest.raises(ValidationError),
    ):
        await run_sl_plan_deck(
            briefs=briefs,
            target_slide_count=12,
            talk_title_hint=None,
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            deps=None,
            response_language="English",
        )

    status, error, _args, result = await _read_step_row(migrated_db)
    assert status == "error"
    assert error == "plan_parse_failed"
    assert result["parse_error"]
    assert "ValidationError" in result["parse_error"]


@pytest.mark.asyncio
async def test_sl_plan_deck_trace_captures_planning_state(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """Happy path: assert the tracer row records the expected
    args_redacted_json keys and result_summary_json keys."""
    briefs = [_brief(paper_id=100 + i) for i in range(3)]
    outline_dict = _multi_paper_outline_dict(briefs)
    mock_completion = AsyncMock(side_effect=[_msg(json.dumps(outline_dict))])

    from paperhub.agents.sl_plan_deck import run_sl_plan_deck

    with patch("paperhub.agents.sl_plan_deck.litellm.acompletion", new=mock_completion):
        outline = await run_sl_plan_deck(
            briefs=briefs,
            target_slide_count=12,
            talk_title_hint="My Talk Hint",
            tracer=fake_tracer,
            model="gemini/gemini-2.5-flash-lite",
            deps=None,
            response_language="English",
        )

    status, error, args, result = await _read_step_row(migrated_db)
    assert status == "ok"
    assert error is None

    # args_redacted_json contract
    assert args["paper_ids_in_brief"] == [100, 101, 102]
    assert args["target_slide_count"] == 12
    assert args["talk_title_hint"] == "My Talk Hint"
    summaries = args["brief_summary_per_paper"]
    assert len(summaries) == 3
    expected_summary_keys = {
        "paper_id",
        "contribution_len",
        "method_core_len",
        "key_results_count",
        "key_figures_count",
        "key_equations_count",
        "key_figure_keys",
        "talk_shape_hint",
    }
    assert expected_summary_keys <= set(summaries[0].keys())

    # result_summary_json contract
    assert result["talk_title"] == outline.talk_title
    assert result["planned_slides_count"] == len(outline.slides)
    distribution = result["pattern_kind_distribution"]
    assert distribution["bottlenecks_table"] >= 1
    assert distribution["concept_2col"] >= 3
    assert distribution["takeaway_closer"] == 1
    attribution = result["paper_id_attribution_counts"]
    # Cross-paper slides go under the "cross" bucket; per-paper slides
    # under their paper_id key.
    assert "cross" in attribution
    for pid in (100, 101, 102):
        assert str(pid) in attribution
