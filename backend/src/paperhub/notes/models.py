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
