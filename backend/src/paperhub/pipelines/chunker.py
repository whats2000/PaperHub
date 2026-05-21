"""Token-windowed greedy chunker with section-aware boundaries.

Target ~800 tokens per chunk, hard cap 1000 (configurable). Splits at
\\section{...} boundaries when possible. LaTeX %-comments are stripped
before chunking via a single-pass parity check that correctly handles
``\\\\%`` (LaTeX line-break followed by a comment). Within a section,
the shrink loop uses safe halving (never overshoots below ``cursor + 1``)
and prefers paragraph breaks over sentence-end. Paragraph-boundary close
fires unconditionally (a clean boundary beats a mid-paragraph cut);
sentence-boundary close is gated on ``tok_len >= target`` to avoid
over-splitting small pieces.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import tiktoken

_SECTION_RE = re.compile(r"\\section\{([^}]+)\}")
# Matches a run of backslashes (group 1) followed by % and the rest of line
# (group 2). Used by _strip_latex_comments to decide whether % is a comment
# (even-count backslash prefix) or an escaped literal percent (odd-count).
_COMMENT_FULL_RE = re.compile(r"(\\*)(%[^\n]*)")
# Paragraph break is the strongest natural boundary; sentence-end is fallback.
_PARA_BOUNDARY_RE = re.compile(r"\n\s*\n")
_SENT_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Chunk:
    section: str | None
    char_start: int
    char_end: int
    text: str
    dom_id: str | None = None


def strip_latex_comments(text: str) -> str:
    """Remove LaTeX % line-comments while preserving \\% (literal percent).

    A ``%`` preceded by an **odd** number of backslashes is an escaped literal
    percent (``\\%``) — preserved unchanged.  A ``%`` preceded by an **even**
    number of backslashes (including zero) starts a comment — stripped to
    end-of-line.

    This single-pass regex approach handles the ``\\\\%`` case (LaTeX
    line-break ``\\\\`` followed by a comment ``%``) which a one-pass
    negative-lookbehind cannot — ``re`` has no variable-length lookbehind.

    Public so callers that receive the original (un-stripped) text can apply
    the same normalisation before slicing by chunk char offsets (which are
    always relative to the post-strip text produced here).
    """

    def _replace(m: re.Match[str]) -> str:
        backslashes = m.group(1)
        if len(backslashes) % 2 == 1:
            # Odd prefix: last backslash escapes the %; keep the whole match.
            return m.group(0)
        # Even prefix (includes zero): % is a comment start; strip it.
        return backslashes

    return _COMMENT_FULL_RE.sub(_replace, text)


def chunk_text(
    text: str,
    *,
    target: int = 800,
    hard: int = 1000,
    sections: list[tuple[str, int]] | None = None,
    strip_comments: bool = True,
) -> list[Chunk]:
    """Token-windowed chunker with section-aware boundaries.

    ``sections`` — explicit ``(name, char_offset)`` boundaries for sources
    without LaTeX ``\\section{}`` markers (the PDF path). When provided, spans
    are split on these offsets instead of the regex, and every chunk is tagged
    with the owning section name. When ``None`` (LaTeX path), the ``\\section{}``
    regex is used as before.

    ``strip_comments`` — run :func:`strip_latex_comments` first (default).
    PDF text is not LaTeX, so the PDF path passes ``False`` to avoid truncating
    lines at any ``%`` (e.g. ``"95% accuracy"``).
    """
    enc = tiktoken.get_encoding("cl100k_base")

    # Strip LaTeX line-comments BEFORE section detection. Chunk char_start /
    # char_end indices are relative to the stripped text. The pre-rendered
    # HTML used by the Citation Canvas never contained the comments anyway.
    # PDF text isn't LaTeX (strip_comments=False) — see docstring.
    if strip_comments:
        text = strip_latex_comments(text)

    # Split into section-spans.
    spans: list[tuple[str | None, int, int]] = []
    if sections is not None:
        # Caller-supplied boundaries (PDF path). Sort by offset, clamp into
        # range, and emit one span per [offset, next_offset). Any text before
        # the first boundary is left section=None (callers prepend a (name, 0)
        # boundary when they want full coverage).
        ordered = sorted(
            ((name, off) for name, off in sections if 0 <= off <= len(text)),
            key=lambda x: x[1],
        )
        if ordered and ordered[0][1] > 0:
            spans.append((None, 0, ordered[0][1]))
        for i, (name, off) in enumerate(ordered):
            end = ordered[i + 1][1] if i + 1 < len(ordered) else len(text)
            if end > off:
                spans.append((name, off, end))
    else:
        last_idx = 0
        last_section: str | None = None
        for m in _SECTION_RE.finditer(text):
            if m.start() > last_idx:
                spans.append((last_section, last_idx, m.start()))
            last_section = m.group(1).strip()
            last_idx = m.end()
        if last_idx < len(text):
            spans.append((last_section, last_idx, len(text)))

    # Greedy-fill each span up to hard cap, closing at paragraph (or
    # sentence as fallback) boundaries once target is hit.
    out: list[Chunk] = []
    for section, span_start, span_end in spans:
        cursor = span_start
        while cursor < span_end:
            tentative_end = min(cursor + hard * 5, span_end)
            piece = text[cursor:tentative_end]
            tok_len = len(enc.encode(piece))
            # Safe halving — never overshoots below cursor + 1. The previous
            # `tentative_end -= (tok_len - hard) * 4` would clamp to cursor
            # +1 on dense LaTeX and emit 1-char chunks indefinitely.
            while tok_len > hard and tentative_end - cursor > 1:
                tentative_end = cursor + max(1, (tentative_end - cursor) // 2)
                piece = text[cursor:tentative_end]
                tok_len = len(enc.encode(piece))

            # Early-close at a natural boundary when we are not at the last
            # chunk in the span and the piece is not a sliver (floor 100 chars).
            # Paragraph-boundary close fires unconditionally (no tok_len gate)
            # so chunks always align to paragraph breaks. This intentionally
            # produces under-target chunks when paragraphs are individually
            # smaller than target: a clean paragraph boundary is preferable to a
            # mid-paragraph cut, and the next chunk simply picks up the slack.
            # Sentence-boundary close is gated on tok_len >= target to avoid
            # over-splitting already-small pieces.
            if tentative_end < span_end and tentative_end - cursor > 100:
                para_matches = list(_PARA_BOUNDARY_RE.finditer(piece))
                if para_matches and para_matches[-1].end() > 100:
                    # Close at the last paragraph break inside the piece.
                    boundary_off = para_matches[-1].end()
                    tentative_end = cursor + boundary_off
                    piece = text[cursor:tentative_end]
                elif tok_len >= target:
                    # No paragraph break — fall back to sentence boundary.
                    sent_matches = list(_SENT_BOUNDARY_RE.finditer(piece))
                    if sent_matches and sent_matches[-1].end() > 100:
                        boundary_off = sent_matches[-1].end()
                        tentative_end = cursor + boundary_off
                        piece = text[cursor:tentative_end]

            raw_piece = text[cursor:tentative_end]
            stripped = raw_piece.strip()
            if not stripped:
                cursor = tentative_end
                continue
            lead = len(raw_piece) - len(raw_piece.lstrip())
            trail = len(raw_piece) - len(raw_piece.rstrip())
            out.append(
                Chunk(
                    section=section,
                    char_start=cursor + lead,
                    char_end=tentative_end - trail,
                    text=stripped,
                ),
            )
            cursor = tentative_end
    return out
