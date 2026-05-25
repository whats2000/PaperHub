"""Tests for ``strip_markdown`` (Plan F2.1 Addendum A1).

The function turns assembled organized-markdown back into clean reading-order
plain text that matches a PDF text layer — so the Citation Canvas resolver's
start-anchored prefix search still locates a chunk whose ``text`` is markdown.
"""
from __future__ import annotations

from paperhub.pipelines.markdown_strip import strip_markdown


def test_strips_heading_markers() -> None:
    assert strip_markdown("## 3 Model Architecture") == "3 Model Architecture"
    assert strip_markdown("###### Deep heading") == "Deep heading"


def test_strips_emphasis() -> None:
    assert strip_markdown("**bold** and *italic*") == "bold and italic"
    assert strip_markdown("__bold__ and _italic_") == "bold and italic"


def test_strips_math_delimiters_keeps_content() -> None:
    assert strip_markdown("$$ E = mc^2 $$") == "E = mc^2"
    assert strip_markdown("inline $x + y$ here") == "inline x + y here"


def test_strips_table_pipes_and_separators() -> None:
    md = "| a | b |\n| --- | --- |\n| 1 | 2 |"
    out = strip_markdown(md)
    assert "|" not in out
    assert "a" in out and "b" in out and "1" in out and "2" in out
    assert "---" not in out


def test_drops_image_refs() -> None:
    out = strip_markdown("before ![alt text](path/to/img.png) after")
    assert "![" not in out
    assert "](" not in out
    assert "before" in out and "after" in out


def test_keeps_link_text_drops_url() -> None:
    out = strip_markdown("see [the paper](https://example.com/x) now")
    assert "the paper" in out
    assert "https://example.com" not in out
    assert "[" not in out and "]" not in out


def test_keeps_prose_and_caption_text() -> None:
    out = strip_markdown("*Figure 1: The Transformer - model architecture.*")
    assert out == "Figure 1: The Transformer - model architecture."


def test_matchability_caption_chunk() -> None:
    # A chunk whose text starts with a markdown heading + italic caption.
    text = "## 3 Model Architecture\n\n*Fig. 1 | the encoder.*"
    out = strip_markdown(text)
    for marker in ("#", "*", "|", "$", "!["):
        assert marker not in out
    # The leading text equals the plain heading (prefix search would find it).
    assert out.startswith("3 Model Architecture")


def test_collapses_excess_blank_lines() -> None:
    out = strip_markdown("a\n\n\n\n\nb")
    assert "\n\n\n" not in out
    assert "a" in out and "b" in out
