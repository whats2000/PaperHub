"""The app's chunk-citation markers, parsed once for every feedback module.

Answers cite with ``[chunk:7]`` and also with multi-id markers — ``[chunk:101,102]``,
``[chunk: 101, 102]`` and ``[chunk:1, chunk:2]`` (see the paper_qa finalizer
and ``frontend/src/lib/chunkCitations.ts``). Every ``[chunk:…]`` bracket counts
and every number inside it is a cited chunk id.
"""
from __future__ import annotations

import re

_MARKER_RE = re.compile(r"\[chunk:[^\]]*\]")
_ID_RE = re.compile(r"\d+")


def cited_chunk_ids(text: str) -> list[int]:
    """Distinct cited chunk ids in order of first appearance."""
    ids = (int(n) for m in _MARKER_RE.findall(text) for n in _ID_RE.findall(m))
    return list(dict.fromkeys(ids))


# [unsourced:<reason>]: a mark the citation validator puts where a citation
# failed (see agents.core.citations). It informs the reader; it is not text
# the model wrote, so a training target leaves it out.
_UNSOURCED_RE = re.compile(r"[ \t]?\[unsourced:\s*[a-z_]+\s*\]")


def strip_unsourced(text: str) -> str:
    """``text`` without the validator's ``[unsourced:…]`` marks."""
    return _UNSOURCED_RE.sub("", text)
