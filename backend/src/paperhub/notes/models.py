"""Pydantic models for note blocks and notes."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

# The shape of a block id: the server's ``b_…`` and the editor's UUIDs both
# fit. `[note:<id>]` citations (agents/core/citations.py, the chat's
# figureCitations.ts) match exactly this shape.
BLOCK_ID_PATTERN = r"[A-Za-z0-9_-]{1,64}"

BlockType = Literal[
    "paragraph", "heading", "bulletListItem", "numberedListItem",
    "checkListItem", "quote", "callout", "codeBlock", "math", "divider",
    "section", "paperQuote", "paperFigure", "paperEquation", "paperRegion",
]


def check_inline(content: Any) -> Any:
    """Raise ``ValueError`` for a malformed PaperHub inline node in a
    block's runs. A ``markCite`` (an inline citation of a reading mark)
    needs a ``markId`` and its alias ``text`` ("" = the quote's start)."""
    if not isinstance(content, list):
        return content
    for run in content:
        if not isinstance(run, dict):
            continue
        if run.get("type") == "markCite":
            props = run.get("props")
            if not isinstance(props, dict):
                raise ValueError("markCite needs props")
            mark = props.get("markId")
            if not isinstance(mark, str) or not mark.strip():
                raise ValueError("markCite needs a markId")
            if not isinstance(props.get("text", ""), str):
                raise ValueError("markCite text must be a string")
        elif isinstance(run.get("content"), list):  # a link's runs
            check_inline(run["content"])
    return content


class Block(BaseModel):
    id: str
    note_id: int
    parent_id: str | None
    type: BlockType
    props: dict[str, Any]
    content: list[dict[str, Any]] | str | None
    text: str
    author: Literal["user", "ai"]
    section_key: str | None
    changed_rev: int


class Note(BaseModel):
    id: int
    target_kind: str
    target_id: str
    title: str
    revision: int
    updated_at: str
    deleted_at: str | None = None
