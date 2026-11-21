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
    "table",
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


# A cell is 1:1 with a Markdown table cell: only its alignment may differ.
_CELL_DEFAULTS = {"backgroundColor": "default", "textColor": "default",
                  "colspan": 1, "rowspan": 1}


def _cell_runs(cell: Any) -> list[dict[str, Any]]:
    """The runs of one cell: a string, a run list or a ``tableCell`` node."""
    if isinstance(cell, str):
        return [{"type": "text", "text": cell, "styles": {}}] if cell else []
    if isinstance(cell, list):
        return [r for r in cell if isinstance(r, dict)]
    if isinstance(cell, dict) and cell.get("type") == "tableCell":
        return _cell_runs(cell.get("content"))
    raise ValueError("a table cell must be a string, runs or a tableCell")


def table_rows(content: dict[str, Any]) -> list[list[list[dict[str, Any]]]]:
    """A table's rows -> cells -> runs, every cell shape normalised."""
    return [[_cell_runs(c) for c in row.get("cells", [])]
            for row in content.get("rows", [])]


def check_table(content: dict[str, Any]) -> dict[str, Any]:
    """Raise ``ValueError`` with a reason unless ``content`` is a table a
    Markdown table can hold: rectangular, no colours or merged cells."""
    if not isinstance(content, dict) or content.get("type") != "tableContent":
        raise ValueError("a table's content must be a tableContent object")
    rows = content.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("a table needs at least one row")
    width = -1
    for row in rows:
        cells = row.get("cells") if isinstance(row, dict) else None
        if not isinstance(cells, list) or not cells:
            raise ValueError("every table row needs cells")
        if width < 0:
            width = len(cells)
        elif len(cells) != width:
            raise ValueError("table rows must all have the same cell count")
        for cell in cells:
            if isinstance(cell, dict) and cell.get("type") == "tableCell":
                props = cell.get("props") or {}
                if not isinstance(props, dict):
                    raise ValueError("a tableCell's props must be an object")
                for key, default in _CELL_DEFAULTS.items():
                    if props.get(key, default) != default:
                        raise ValueError(f"a table cell's {key} must stay {default}")
            check_inline(_cell_runs(cell))
    for key in ("headerRows", "headerCols"):
        v = content.get(key, 0)
        if isinstance(v, bool) or v not in (0, 1):
            raise ValueError(f"{key} must be 0 or 1")
    widths = content.get("columnWidths")
    if widths is not None:
        if not isinstance(widths, list) or len(widths) != width:
            raise ValueError("columnWidths must match the column count")
        for w in widths:
            if w is not None and (isinstance(w, bool) or not isinstance(w, int)):
                raise ValueError("columnWidths items must be integers or null")
    return content


class Block(BaseModel):
    id: str
    note_id: int
    parent_id: str | None
    type: BlockType
    props: dict[str, Any]
    content: list[dict[str, Any]] | dict[str, Any] | str | None
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
