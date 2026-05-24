"""Tests for ``marker_doc_to_markdown`` (Plan F2.1 Addendum A1).

Walks ALL Marker blocks in document order into an organized-markdown document
(headings, figure captions, equations, tables in reading order) + section
boundaries, so the chunker sees richer context than the figure-skipping
``_marker_text_and_sections`` it replaces.
"""
from __future__ import annotations

import json
from pathlib import Path

from paperhub.pipelines.markdown_strip import strip_markdown
from paperhub.pipelines.marker_client import MarkerBlock, MarkerDoc, _parse
from paperhub.pipelines.marker_to_asset import marker_doc_to_markdown

_FIXTURE = Path(__file__).parent / "fixtures" / "marker_doc.json"


def test_assembles_organized_markdown_with_boundaries() -> None:
    doc = MarkerDoc(blocks=[
        MarkerBlock(block_type="SectionHeader", html="<h1>Method</h1>",
                    block_id="/page/0/SectionHeader/1"),
        MarkerBlock(block_type="Text", html="<p>We propose a new model.</p>"),
        MarkerBlock(block_type="Figure",
                    caption="Figure 1: the architecture diagram."),
        MarkerBlock(block_type="Equation", html="<math/>", latex=r"E=mc^2"),
    ])
    md, boundaries = marker_doc_to_markdown(doc)

    assert "## Method" in md
    assert "We propose a new model." in md
    # Raw caption text (no "Figure:" prefix prepended), italicized.
    assert "Figure 1: the architecture diagram." in md
    assert "$$" in md
    assert "E=mc^2" in md

    # One boundary for the section, at the offset where "## Method" begins.
    assert boundaries == [("Method", md.index("## Method"))]


def test_skips_empty_figure_caption_and_empty_equation() -> None:
    doc = MarkerDoc(blocks=[
        MarkerBlock(block_type="Figure", caption="", html=""),
        MarkerBlock(block_type="Equation", html="<math/>", latex=None),
        MarkerBlock(block_type="Text", html="<p>kept</p>"),
    ])
    md, boundaries = marker_doc_to_markdown(doc)
    assert md.strip() == "kept"
    assert boundaries == []


def test_figure_caption_falls_back_to_html_when_no_caption() -> None:
    doc = MarkerDoc(blocks=[
        MarkerBlock(block_type="Figure", caption=None, html="<p>fallback cap</p>"),
    ])
    md, _ = marker_doc_to_markdown(doc)
    assert "fallback cap" in md


def test_records_first_occurrence_of_each_section() -> None:
    doc = MarkerDoc(blocks=[
        MarkerBlock(block_type="SectionHeader", html="<h1>Intro</h1>"),
        MarkerBlock(block_type="Text", html="<p>a</p>"),
        MarkerBlock(block_type="SectionHeader", html="<h1>Intro</h1>"),  # dup
        MarkerBlock(block_type="SectionHeader", html="<h1>Body</h1>"),
    ])
    _, boundaries = marker_doc_to_markdown(doc)
    names = [n for n, _ in boundaries]
    assert names == ["Intro", "Body"]


def test_real_fixture_includes_transformer_caption() -> None:
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    doc = _parse(payload)
    md, boundaries = marker_doc_to_markdown(doc)

    assert "Figure 1: The Transformer - model architecture." in md
    # The stripped markdown still contains the plain caption (matchable).
    assert "Figure 1: The Transformer - model architecture." in strip_markdown(md)
    names = [n for n, _ in boundaries]
    assert "3 Model Architecture" in names
    # Every boundary offset points at where its "## <name>" begins.
    for name, off in boundaries:
        assert md[off:].startswith(f"## {name}")
