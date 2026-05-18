"""Token-windowed greedy chunker with section-aware boundaries.

Target ~800 tokens per chunk, hard cap 1000 (configurable). Splits at
\\section{...} boundaries when possible; otherwise greedy-fills until hard cap.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import tiktoken

_SECTION_RE = re.compile(r"\\section\{([^}]+)\}")


@dataclass(frozen=True)
class Chunk:
    section: str | None
    char_start: int
    char_end: int
    text: str


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

    # Greedy-fill each span up to hard cap.
    out: list[Chunk] = []
    for section, span_start, span_end in spans:
        cursor = span_start
        while cursor < span_end:
            # Estimate cap by characters first (rough: 4 chars ≈ 1 token); refine with tiktoken.
            tentative_end = min(cursor + hard * 5, span_end)
            piece = text[cursor:tentative_end]
            tok_len = len(enc.encode(piece))
            # Shrink until under hard.
            while tok_len > hard and tentative_end > cursor + 1:
                tentative_end -= max(1, (tok_len - hard) * 4)
                tentative_end = max(tentative_end, cursor + 1)
                piece = text[cursor:tentative_end]
                tok_len = len(enc.encode(piece))
            if not piece.strip():
                cursor = tentative_end
                continue
            out.append(
                Chunk(
                    section=section,
                    char_start=cursor,
                    char_end=tentative_end,
                    text=piece.strip(),
                )
            )
            cursor = tentative_end
    return out
