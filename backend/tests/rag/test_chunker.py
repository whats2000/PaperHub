"""Tests for the greedy text chunker."""

from __future__ import annotations

from uuid import uuid4

import tiktoken

from paperhub.rag.chunker import chunk_text

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
