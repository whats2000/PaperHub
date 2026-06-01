"""Tests for the F4.5 Phase 6 ``gather_context`` per-paper subagent.

Covers:
- Happy path: LLM emits a final no-tool-calls JSON response; we get back a
  :class:`PaperContextBundle` with probed figure dimensions.
- Hard contract: a ``key_figures[*].key`` that is not in the deck-prefixed
  figure inventory is rejected at parse time (no hallucinated figures).

NOTE: The plan stub assumed a PaperAsset shape with ``source_dir`` /
``metadata`` / ``additional_tex`` keys. The real ``PaperAsset`` is the F2
ingestion dataclass (figures + equations + sections only). This test therefore
constructs the real dataclass and threads paper-row metadata + ADDITIONAL.tex
contents in as separate kwargs (matching how ``report_graph.py`` will call it).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from paperhub.agents.gather_context import run_gather_context
from paperhub.models.slide_domain import FigureDimensions
from paperhub.pipelines.paper_asset import (
    EquationAsset,
    FigureAsset,
    PaperAsset,
    SectionAsset,
    write_paper_asset,
)
from paperhub.tracing.tracer import Tracer


@pytest.fixture
def fake_asset(tmp_path: Path) -> tuple[PaperAsset, Path]:
    """Construct a minimal PaperAsset on disk + return (asset, source_dir).

    Writes one figure image so ``probe_figure_dimensions`` returns the real
    pixel size (1640x920) rather than the 1000x1000 fallback.
    """
    source_dir = tmp_path
    fig_dir = source_dir / "asset" / "figures"
    fig_dir.mkdir(parents=True)
    Image.new("RGB", (1640, 920)).save(fig_dir / "fig-001.png")
    asset = PaperAsset(
        figures=[
            FigureAsset(
                id="fig-001",
                caption="An overview of the method.",
                page=1,
                section="Method",
                image_path="figures/fig-001.png",
            ),
        ],
        equations=[
            EquationAsset(id="eq-001", latex=r"\Phi = \sum a", section="Method"),
        ],
        sections=[SectionAsset(name="Method", order=1)],
    )
    write_paper_asset(asset, source_dir)
    return asset, source_dir


def _bundle_payload(
    *,
    paper_id: int,
    paper_idx: int,
    figure_key: str = "p0-fig-001",
) -> dict[str, Any]:
    """Build a PaperContextBundle JSON payload the test LLM emits."""
    return {
        "paper_id": paper_id,
        "paper_idx": paper_idx,
        "title": "T",
        "authors": ["A"],
        "year": 2025,
        "narrative_summary": "Contribution: X. Method: Y. Results: 14% better.",
        "key_figures": [
            {
                "key": figure_key,
                "role": "overview",
                "one_line_interpretation": "An overview",
                "dimensions": {"width_px": 1640, "height_px": 920},
            }
        ],
        "key_equations": [
            {
                "latex": r"\Phi = \sum a",
                "role": "importance_score",
                "notation_legend": "Phi: score",
            }
        ],
        "section_excerpts": [],
        "paper_newcommands": ["\\newcommand{\\bm}{...}"],
    }


def _msg_no_tool_calls(content: str) -> dict[str, Any]:
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content, "tool_calls": []}}
        ]
    }


@pytest.mark.asyncio
async def test_gather_context_returns_bundle_with_probed_dimensions(
    fake_asset: tuple[PaperAsset, Path],
    fake_tracer: Tracer,
) -> None:
    asset, source_dir = fake_asset
    payload = _bundle_payload(paper_id=42, paper_idx=0)
    llm = AsyncMock()
    llm.return_value = _msg_no_tool_calls(json.dumps(payload))

    bundle = await run_gather_context(
        paper_id=42,
        paper_idx=0,
        asset=asset,
        source_dir=source_dir,
        paper_title="T",
        paper_authors=["A"],
        paper_year=2025,
        paper_abstract="abs",
        paper_newcommands=["\\newcommand{\\bm}{...}"],
        conn=None,
        tracer=fake_tracer,
        model="stub",
        llm_acompletion=llm,
    )

    assert bundle.paper_id == 42
    assert bundle.paper_idx == 0
    assert len(bundle.key_figures) == 1
    assert bundle.key_figures[0].dimensions == FigureDimensions(
        width_px=1640, height_px=920
    )


@pytest.mark.asyncio
async def test_gather_context_rejects_unknown_figure_key(
    fake_asset: tuple[PaperAsset, Path],
    fake_tracer: Tracer,
) -> None:
    asset, source_dir = fake_asset
    payload = _bundle_payload(
        paper_id=42, paper_idx=0, figure_key="p0-fig-NOT-IN-INVENTORY"
    )
    llm = AsyncMock()
    llm.return_value = _msg_no_tool_calls(json.dumps(payload))

    with pytest.raises(ValueError, match="unknown figure key"):
        await run_gather_context(
            paper_id=42,
            paper_idx=0,
            asset=asset,
            source_dir=source_dir,
            paper_title="T",
            paper_authors=[],
            paper_year=2025,
            paper_abstract="abs",
            paper_newcommands=[],
            conn=None,
            tracer=fake_tracer,
            model="stub",
            llm_acompletion=llm,
        )
