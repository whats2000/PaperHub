"""Token-windowed greedy chunker with section-aware boundaries.

Target ~800 tokens per chunk, hard cap 1000 (configurable). Splits at
\\section{...} boundaries when possible. LaTeX %-comments are stripped
before chunking. Within a section, the shrink loop uses safe halving
(never overshoots below ``cursor + 1``) and prefers paragraph breaks
over sentence-end when closing a chunk at or above target.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import tiktoken

_SECTION_RE = re.compile(r"\\section\{([^}]+)\}")
# Single-% line comments to end-of-line, EXCEPT escaped \% (literal percent).
_COMMENT_RE = re.compile(r"(?<!\\)%[^\n]*")
# Paragraph break is the strongest natural boundary; sentence-end is fallback.
_PARA_BOUNDARY_RE = re.compile(r"\n\s*\n")
_SENT_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    section: str | None
    char_start: int
    char_end: int
    text: str


def _strip_latex_comments(text: str) -> str:
    """Remove % line-comments while preserving \\% (literal percent)."""
    return _COMMENT_RE.sub("", text)


def chunk_text(text: str, *, target: int = 800, hard: int = 1000) -> list[Chunk]:
    enc = tiktoken.get_encoding("cl100k_base")

    # Strip LaTeX line-comments BEFORE section detection. Chunk char_start /
    # char_end indices are relative to the stripped text. The pre-rendered
    # HTML used by the Citation Canvas never contained the comments anyway.
    text = _strip_latex_comments(text)

    # Split into section-spans.
    spans: list[tuple[str | None, int, int]] = []
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
            # Paragraph-boundary close fires whenever a \n\n exists (regardless
            # of tok_len vs target) so that chunks always align to paragraph
            # breaks when possible. Sentence-boundary close is gated on
            # tok_len >= target to avoid over-splitting short pieces.
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
