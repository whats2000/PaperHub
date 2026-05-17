"""Text chunker for the RAG pipeline (Phase A — greedy windowing only).

Phase B refinement: section-aware splitting on TEI XML. For Phase A we use
greedy token-windowed chunking over plain text.

Strategy
--------
* Tokenise the full text with ``cl100k_base`` (same family as GPT-4 encodings).
* Walk tokens greedily: accumulate until ``target_tokens`` is reached, then
  emit a chunk.  The next window starts ``overlap`` tokens before the end of
  the previous chunk so context isn't severed abruptly.
* Hard cap: no chunk ever exceeds ``hard_max`` tokens (rare; only fires when a
  single "sentence" exceeds target_tokens).

LaTeX support (Phase A — basic preamble strip)
----------------------------------------------
When the input is LaTeX source (detected by :func:`is_latex`), the preamble
(everything up to and including ``\\begin{document}``) and the closing
``\\end{document}`` are stripped before chunking.  This drops boilerplate that
wastes tokens.  Full LaTeX-aware chunking (e.g. section-level splits) is
deferred to Phase B.

Each :class:`~paperhub.data.models.Chunk` carries ``char_start`` and
``char_end`` so callers can map back to the source text.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from uuid import UUID, uuid4

import tiktoken

from paperhub.data.models import Chunk

_ENCODING = tiktoken.get_encoding("cl100k_base")

# ---------------------------------------------------------------------------
# LaTeX detection + preamble stripping
# ---------------------------------------------------------------------------

_LATEX_MARKERS = re.compile(r"\\documentclass|\\begin\{document\}")
_BEGIN_DOCUMENT = re.compile(r"\\begin\{document\}", re.IGNORECASE)
_END_DOCUMENT = re.compile(r"\\end\{document\}", re.IGNORECASE)


def is_latex(text: str) -> bool:
    """Return True if *text* looks like a LaTeX source file.

    Checks for the presence of ``\\documentclass`` or ``\\begin{document}``
    markers which are unambiguous indicators of a LaTeX source file.
    """
    return bool(_LATEX_MARKERS.search(text))


def strip_latex_preamble(text: str) -> str:
    """Strip the LaTeX preamble and closing ``\\end{document}`` from *text*.

    Everything up to and including ``\\begin{document}`` is removed (the
    preamble contains ``\\usepackage``, ``\\newcommand`` etc. — mostly noise
    for retrieval).  The ``\\end{document}`` at the end is also removed.

    If ``\\begin{document}`` is not found, *text* is returned unchanged.
    """
    m = _BEGIN_DOCUMENT.search(text)
    if m:
        text = text[m.end() :]
    # Strip trailing \\end{document}
    m_end = _END_DOCUMENT.search(text)
    if m_end:
        text = text[: m_end.start()]
    return text


def chunk_text(
    paper_id: UUID,
    text: str,
    *,
    target_tokens: int = 800,
    hard_max: int = 1000,
    overlap: int = 50,
) -> Iterator[Chunk]:
    """Yield :class:`~paperhub.data.models.Chunk` instances for *text*.

    Parameters
    ----------
    paper_id:
        UUID of the owning paper (propagated to every chunk).
    text:
        Plain-text or LaTeX content to split.  If *text* starts with a LaTeX
        preamble (detected by :func:`is_latex`), the preamble and closing
        ``\\end{document}`` are stripped before chunking.
    target_tokens:
        Soft window size in tokens.  Each chunk will be at most this many
        tokens (unless a single token run exceeds *hard_max*).
    hard_max:
        Absolute maximum tokens per chunk.  Chunks are truncated to this if
        they somehow exceed it.
    overlap:
        Number of tokens shared between consecutive chunks to avoid severing
        context at boundaries.
    """
    if not text.strip():
        return

    # Strip LaTeX preamble / postamble before tokenising.
    # Full LaTeX-aware section chunking is Phase B work.
    if is_latex(text):
        text = strip_latex_preamble(text)
        if not text.strip():
            return

    tokens: list[int] = _ENCODING.encode(text)
    if not tokens:
        return

    # Build a parallel list of (token, char_start, char_end) by decoding each
    # token individually so we can track character offsets precisely.
    token_chars: list[tuple[int, int, int]] = []
    pos = 0
    for tok in tokens:
        decoded = _ENCODING.decode([tok])
        start = pos
        end = pos + len(decoded)
        token_chars.append((tok, start, end))
        pos = end

    window_start = 0
    n = len(token_chars)

    while window_start < n:
        window_end = min(window_start + target_tokens, n)
        # Enforce hard_max
        window_end = min(window_end, window_start + hard_max)

        chunk_tokens = [tc[0] for tc in token_chars[window_start:window_end]]
        chunk_text_str = _ENCODING.decode(chunk_tokens)
        char_start = token_chars[window_start][1]
        char_end = token_chars[window_end - 1][2]

        yield Chunk(
            id=uuid4(),
            paper_id=paper_id,
            section=None,  # Phase B: parse TEI section headings
            page=None,  # Phase B: embed page numbers from PDF coordinates
            char_start=char_start,
            char_end=char_end,
            text=chunk_text_str,
        )

        if window_end >= n:
            break
        # Advance by (window_size - overlap), ensuring at least 1 token progress
        step = max(1, (window_end - window_start) - overlap)
        window_start += step
