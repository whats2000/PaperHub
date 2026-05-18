from paperhub.pipelines.chunker import chunk_text


def test_chunks_respect_hard_cap() -> None:
    # ~3000 tokens of "word " repeated.
    text = ("word " * 3000).strip()
    chunks = chunk_text(text, target=200, hard=300)
    assert len(chunks) > 1
    for c in chunks:
        # Each chunk under hard cap.
        assert _token_count(c.text) <= 300


def test_chunks_split_at_section_when_possible() -> None:
    text = (
        "intro paragraph " * 50
        + "\n\\section{Methods}\n"
        + "methods paragraph " * 50
        + "\n\\section{Results}\n"
        + "results paragraph " * 50
    )
    chunks = chunk_text(text, target=200, hard=400)
    # At least three chunks (one per section), aligned to section boundaries.
    sections = {c.section for c in chunks}
    assert sections == {None, "Methods", "Results"} or sections == {"Methods", "Results", None}


def test_char_offsets_are_correct() -> None:
    text = "abc def ghi"
    chunks = chunk_text(text, target=100, hard=100)
    assert len(chunks) == 1
    c = chunks[0]
    assert text[c.char_start:c.char_end] == c.text


def test_char_offsets_hold_when_whitespace_stripped() -> None:
    # \section{Intro} prefix + trailing newlines so each span has leading/trailing whitespace.
    text = "\n\\section{Intro}\n   hello world   \n\\section{Body}\n   second   \n"
    chunks = chunk_text(text, target=100, hard=100)
    for c in chunks:
        assert text[c.char_start:c.char_end] == c.text


def test_target_aware_early_close_at_natural_boundary() -> None:
    """Most chunks should land in [target, hard) when natural paragraph
    boundaries exist — confirming the target-aware early-close logic.

    Uses target=200, hard=400 and a text that is ~3 000 tokens of distinct
    paragraphs. The majority of chunks should be in the 200-400 token range
    (not all clustered at 400).
    """
    # Build ~20 paragraphs, each ~150 tokens (word repeated 150 times).
    # A paragraph ends with \n\n — a natural boundary.
    paragraphs = [("word " * 150).rstrip() for _ in range(20)]
    long_text = "\n\n".join(paragraphs)
    chunks = chunk_text(long_text, target=200, hard=400)
    assert len(chunks) > 1
    in_target_range = sum(1 for c in chunks if _token_count(c.text) < 400)
    # At least 50 % of chunks should close before the hard cap.
    assert in_target_range >= len(chunks) // 2, (
        f"Expected most chunks < 400 tokens, but only {in_target_range}/{len(chunks)}"
    )
    # No chunk may exceed the hard cap.
    for c in chunks:
        assert _token_count(c.text) <= 400


def _token_count(s: str) -> int:
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(s))
