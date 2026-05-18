"""Token-windowed greedy chunker with section-aware boundaries.

Target ~800 tokens per chunk, hard cap 1000 (configurable). Splits at
\\section{...} boundaries when possible; otherwise greedy-fills up to the
hard cap, closing early at a natural paragraph / sentence boundary whenever
the token count has already reached the *target*.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import tiktoken

_SECTION_RE = re.compile(r"\\section\{([^}]+)\}")

# Natural boundaries ordered by preference (strongest first).
_BOUNDARY_RE = re.compile(r"\n\n|(?<=[.!?]) ")


@dataclass(frozen=True)
class Chunk:
    section: str | None
    char_start: int
    char_end: int
    text: str


def _last_natural_boundary(piece: str) -> int | None:
    """Return the end offset of the last natural boundary in *piece*, or None."""
    last: int | None = None
    for m in _BOUNDARY_RE.finditer(piece):
        last = m.end()
    return last


def chunk_text(text: str, *, target: int = 800, hard: int = 1000) -> list[Chunk]:
    enc = tiktoken.get_encoding("cl100k_base")

    # Split into section-spans first.
    spans: list[tuple[str | None, int, int]] = []  # (section_name, char_start, char_end)
    last_idx = 0
    last_section: str | None = None
    for m in _SECTION_RE.finditer(text):
        if m.start() > last_idx:
            spans.append((last_section, last_idx, m.start()))
        last_section = m.group(1).strip()
        last_idx = m.end()
    if last_idx < len(text):
        spans.append((last_section, last_idx, len(text)))

    # Greedy-fill each span up to hard cap, closing early at natural
    # boundaries once the token count has reached *target*.
    out: list[Chunk] = []
    for section, span_start, span_end in spans:
        cursor = span_start
        while cursor < span_end:
            # Estimate cap by characters first (rough: 4 chars ≈ 1 token);
            # refine with tiktoken.
            tentative_end = min(cursor + hard * 5, span_end)
            piece = text[cursor:tentative_end]
            tok_len = len(enc.encode(piece))
            # Shrink until under hard.
            while tok_len > hard and tentative_end > cursor + 1:
                tentative_end -= max(1, (tok_len - hard) * 4)
                tentative_end = max(tentative_end, cursor + 1)
                piece = text[cursor:tentative_end]
                tok_len = len(enc.encode(piece))
            # Target-aware early-close: if we are at or above the target and a
            # natural boundary exists inside the piece, close there instead of
            # continuing to the hard cap.
            if tok_len >= target and tentative_end < span_end:
                boundary_off = _last_natural_boundary(piece)
                if boundary_off is not None and boundary_off > 0:
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
                )
            )
            cursor = tentative_end
    return out
