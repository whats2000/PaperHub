# backend/src/paperhub/pipelines/markdown_strip.py
"""Turn assembled organized-markdown back into clean reading-order plain text
(Plan F2.1 Addendum A1).

The Marker re-chunk path stores each chunk's ``text`` as organized markdown
(headings, italic figure captions, ``$$``-delimited equations, table cells) so
the embedder + answering model get richer structured context. But the Citation
Canvas resolver matches a start-anchored *prefix* of a chunk against the PDF
text layer, which has no markdown markers — so we also store a markdown-stripped
``match_text`` per chunk. This module is that strip.

Whitespace is only lightly normalized (collapse runs of 3+ newlines): the
frontend resolver normalizes whitespace itself, and over-normalizing here would
diverge from the PDF text layer's own spacing.
"""
from __future__ import annotations

import re

# Image refs FIRST (drop entirely) before link handling, since both share the
# ``[...](...)`` shape — an image is ``![alt](url)``.
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
# Link ``[text](url)`` → keep ``text``.
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# Heading markers at line start: ``#``..``######`` + following space.
_HEADING_RE = re.compile(r"^[ \t]*#{1,6}[ \t]+", re.MULTILINE)
# Table separator rows: pipes, dashes, colons, whitespace only (e.g. ``| --- |``).
_TABLE_SEP_RE = re.compile(r"^[ \t]*\|?[ \t:|-]*-[ \t:|-]*\|?[ \t]*$", re.MULTILINE)
# Emphasis markers: ``**``/``*``/``__``/``_`` (drop the markers, keep content).
_EMPHASIS_RE = re.compile(r"\*\*|\*|__|_")
# Math delimiters: ``$$`` (block) then ``$`` (inline) — drop, keep inner content.
_MATH_RE = re.compile(r"\${1,2}")
# Runs of 3+ newlines → 2 (light normalization only).
_BLANKS_RE = re.compile(r"\n{3,}")


def strip_markdown(md: str) -> str:
    """Convert assembled markdown to clean plain text matching a PDF text layer."""
    if not md:
        return ""
    out = md
    out = _IMAGE_RE.sub("", out)          # drop image refs entirely
    out = _LINK_RE.sub(r"\1", out)        # keep link text, drop the URL
    out = _HEADING_RE.sub("", out)        # drop heading ## markers
    out = _TABLE_SEP_RE.sub("", out)      # drop |---|---| separator rows
    out = out.replace("|", " ")           # table pipes → space
    out = _MATH_RE.sub("", out)           # drop $/$$ delimiters, keep content
    out = _EMPHASIS_RE.sub("", out)       # drop emphasis markers, keep content
    out = _BLANKS_RE.sub("\n\n", out)     # collapse excess blank lines
    return out.strip()
