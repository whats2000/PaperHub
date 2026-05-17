"""Tests for the greedy text chunker, including LaTeX preamble stripping."""

from __future__ import annotations

from uuid import uuid4

import tiktoken

from paperhub.rag.chunker import chunk_text, is_latex, strip_latex_preamble

_ENC = tiktoken.get_encoding("cl100k_base")


def _token_count(text: str) -> int:
    return len(_ENC.encode(text))


def test_short_text_yields_one_chunk() -> None:
    """Text shorter than target_tokens produces exactly one chunk."""
    paper_id = uuid4()
    text = "This is a short abstract."
    chunks = list(chunk_text(paper_id, text, target_tokens=800))
    assert len(chunks) == 1
    assert chunks[0].paper_id == paper_id
    assert chunks[0].text.strip() == text.strip()


def test_long_text_yields_multiple_chunks() -> None:
    """Text significantly longer than target_tokens splits into multiple chunks."""
    paper_id = uuid4()
    # Build ~2500 tokens of text (well above target_tokens=800)
    word = "knowledge "
    text = word * 250  # ~250 tokens
    chunks = list(chunk_text(paper_id, text, target_tokens=100, hard_max=120, overlap=10))
    assert len(chunks) > 1


def test_all_chunks_under_hard_max() -> None:
    """No chunk should exceed hard_max tokens."""
    paper_id = uuid4()
    word = "semantics "
    text = word * 300
    hard_max = 150
    chunks = list(chunk_text(paper_id, text, target_tokens=100, hard_max=hard_max, overlap=20))
    for chunk in chunks:
        assert _token_count(chunk.text) <= hard_max, (
            f"Chunk exceeds hard_max={hard_max}: {_token_count(chunk.text)} tokens"
        )


def test_chunk_char_offsets_span_source_text() -> None:
    """char_start and char_end must reference valid positions in the source."""
    paper_id = uuid4()
    text = "The quick brown fox jumps over the lazy dog. " * 20
    chunks = list(chunk_text(paper_id, text, target_tokens=20, hard_max=30, overlap=5))
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.char_start is not None
        assert chunk.char_end is not None
        # The chunk text must be recoverable from the source at the given offsets
        extracted = text[chunk.char_start : chunk.char_end]
        # Allow for encoding round-trip differences (spaces near boundaries)
        assert chunk.text.strip() in extracted or extracted.strip() in chunk.text.strip()


def test_empty_text_yields_no_chunks() -> None:
    """Empty or whitespace-only input produces no chunks."""
    paper_id = uuid4()
    assert list(chunk_text(paper_id, "")) == []
    assert list(chunk_text(paper_id, "   ")) == []


# ---------------------------------------------------------------------------
# LaTeX detection and preamble stripping
# ---------------------------------------------------------------------------

_LATEX_SAMPLE = r"""\documentclass[12pt]{article}
\usepackage{amsmath}
\usepackage{hyperref}
\newcommand{\norm}[1]{\left\|#1\right\|}

\begin{document}

\section{Introduction}
We propose a novel architecture based on self-attention.

\section{Methods}
Our model uses multi-head attention: $\alpha + \beta$.

\end{document}
"""

_PLAIN_TEXT_SAMPLE = "This is just a plain text document without any LaTeX markup."


def test_is_latex_detects_documentclass() -> None:
    """is_latex returns True for text containing \\documentclass."""
    assert is_latex(r"\documentclass{article}\n\begin{document}\nHello\n\end{document}")


def test_is_latex_detects_begin_document() -> None:
    """is_latex returns True for text containing \\begin{document}."""
    assert is_latex(r"Some preamble\n\begin{document}\nBody\n\end{document}")


def test_is_latex_returns_false_for_plain_text() -> None:
    """is_latex returns False for plain text without LaTeX markers."""
    assert not is_latex(_PLAIN_TEXT_SAMPLE)
    assert not is_latex("# Markdown heading\n\nSome content.")


def test_strip_latex_preamble_removes_preamble() -> None:
    """strip_latex_preamble removes everything up to and including \\begin{document}."""
    stripped = strip_latex_preamble(_LATEX_SAMPLE)
    assert "\\documentclass" not in stripped
    assert "\\usepackage" not in stripped
    assert "\\begin{document}" not in stripped
    # Body content must be preserved
    assert "Introduction" in stripped
    assert "self-attention" in stripped


def test_strip_latex_preamble_removes_end_document() -> None:
    """strip_latex_preamble also strips \\end{document}."""
    stripped = strip_latex_preamble(_LATEX_SAMPLE)
    assert "\\end{document}" not in stripped


def test_strip_latex_preamble_preserves_body_content() -> None:
    """Body text between \\begin{document} and \\end{document} is preserved."""
    stripped = strip_latex_preamble(_LATEX_SAMPLE)
    assert "multi-head attention" in stripped
    assert r"$\alpha + \beta$" in stripped


def test_strip_latex_preamble_no_begin_document() -> None:
    """When \\begin{document} is absent, text is returned unchanged."""
    text = r"\usepackage{amsmath}\nSome content."
    assert strip_latex_preamble(text) == text


def test_chunk_text_on_latex_strips_preamble() -> None:
    """chunk_text on LaTeX input does not include preamble content in chunks."""
    paper_id = uuid4()
    chunks = list(chunk_text(paper_id, _LATEX_SAMPLE))
    assert len(chunks) >= 1, "Expected at least one chunk from LaTeX body"

    combined = " ".join(c.text for c in chunks)
    # Preamble commands should not appear in chunks
    assert "\\documentclass" not in combined
    assert "\\usepackage" not in combined
    # Body content should appear
    assert "Introduction" in combined or "attention" in combined


def test_chunk_text_on_latex_preamble_only_yields_no_chunks() -> None:
    """LaTeX that has an empty body (only preamble) yields no chunks."""
    paper_id = uuid4()
    latex_preamble_only = "\\documentclass{article}\n\\begin{document}\n\\end{document}"
    chunks = list(chunk_text(paper_id, latex_preamble_only))
    assert chunks == [], f"Expected no chunks from empty LaTeX body, got: {chunks}"
