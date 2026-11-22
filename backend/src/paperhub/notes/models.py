"""Pydantic models for note blocks and notes."""
from __future__ import annotations

from dataclasses import dataclass
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
    "table", "toc",
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
        elif run.get("type") == "noteLink":
            _check_note_link(run.get("props"))
        elif isinstance(run.get("content"), list):  # a link's runs
            check_inline(run["content"])
    return content


def _check_note_link(props: Any) -> None:
    """A ``[[`` link: a note or a paper, optionally a block / paper section."""
    if not isinstance(props, dict):
        raise ValueError("noteLink needs props")
    if props.get("targetKind") not in ("note", "paper"):
        raise ValueError("noteLink targetKind must be 'note' or 'paper'")
    tid = props.get("targetId")
    if isinstance(tid, bool) or not isinstance(tid, int) or tid <= 0:
        raise ValueError("noteLink needs a positive integer targetId")
    for key in ("blockId", "sectionKey", "label", "name"):
        if not isinstance(props.get(key, ""), str):
            raise ValueError(f"noteLink {key} must be a string")


# A cell has no colour; its alignment and its span may differ.
_CELL_DEFAULTS = {"backgroundColor": "default", "textColor": "default"}


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


@dataclass(frozen=True)
class GridCell:
    """A real cell placed on the table's grid: its top-left ``row``/``col``,
    how many grid rows/columns it covers, and its position in
    ``rows[row].cells``."""

    row: int
    col: int
    rowspan: int
    colspan: int
    index: int


Grid = list[list["GridCell | tuple[int, int]"]]


def _span(cell: Any, key: str) -> int:
    props = cell.get("props") if isinstance(cell, dict) else None
    v = props.get(key, 1) if isinstance(props, dict) else 1
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise ValueError(f"a table cell's {key} must be a positive integer")
    return v


def table_grid(content: dict[str, Any]) -> tuple[Grid, int, int]:
    """The table on its grid: ``(grid, R, C)``, ``grid[r][c]`` the
    ``GridCell`` that starts there or the ``(row, col)`` of the cell that
    covers it. Each cell takes the first free column of its row. Raises
    ``ValueError`` with a reason on overlapping spans, a span past the
    table's edge or rows that do not fill the same width."""
    rows = content.get("rows") if isinstance(content, dict) else None
    if not isinstance(rows, list):
        raise ValueError("a table needs rows")
    n = len(rows)
    taken: dict[tuple[int, int], GridCell | tuple[int, int]] = {}
    width = -1
    for r, row in enumerate(rows):
        cells = row.get("cells") if isinstance(row, dict) else None
        if not isinstance(cells, list):
            raise ValueError("every table row needs cells")
        c = 0
        for i, cell in enumerate(cells):
            while (r, c) in taken:
                c += 1
            rs, cs = _span(cell, "rowspan"), _span(cell, "colspan")
            if r + rs > n:
                raise ValueError(
                    f"row {r + 1}, column {c + 1}: its rowspan {rs} runs past "
                    "the last row")
            anchor = GridCell(row=r, col=c, rowspan=rs, colspan=cs, index=i)
            for dr in range(rs):
                for dc in range(cs):
                    pos = (r + dr, c + dc)
                    if pos in taken:
                        raise ValueError(
                            f"row {r + 1}, column {c + 1}: its span overlaps "
                            "another merged cell")
                    taken[pos] = anchor if (dr, dc) == (0, 0) else (r, c)
            c += cs
        while (r, c) in taken:
            c += 1
        if width < 0:
            width = c
        elif c != width:
            raise ValueError(
                f"row {r + 1} covers {c} columns, row 1 covers {width}; "
                "every row must cover the same columns")
    width = max(width, 0)
    if any(col >= width for (_, col) in taken):
        raise ValueError("a merged cell runs past the table's last column")
    grid: Grid = [[taken[(r, c)] for c in range(width)] for r in range(n)]
    return grid, n, width


def header_depth(content: dict[str, Any]) -> int:
    """How many grid rows the header covers: 0 without a header, else row
    0 plus every row a row-0 cell spans into."""
    if not content.get("headerRows"):
        return 0
    try:
        grid, n, width = table_grid(content)
    except ValueError:
        return 1
    if not n:
        return 0
    depth = 1
    for c in range(width):
        a = grid[0][c]
        if isinstance(a, GridCell):
            depth = max(depth, a.rowspan)
    return depth


def column_aligns(content: dict[str, Any]) -> list[str]:
    """Each grid column's alignment (``left`` / ``center`` / ``right`` /
    ``""`` for the default): that of the first cell that starts in the
    column and spans only it."""
    try:
        grid, _, width = table_grid(content)
    except ValueError:
        return []
    rows = content.get("rows") or []
    out: list[str] = []
    for c in range(width):
        align = ""
        for line in grid:
            a = line[c]
            if isinstance(a, GridCell) and a.col == c and a.colspan == 1:
                cell = rows[a.row]["cells"][a.index]
                props = cell.get("props") if isinstance(cell, dict) else None
                v = props.get("textAlignment") if isinstance(props, dict) else None
                align = v if v in ("left", "center", "right") else ""
                break
        out.append(align)
    return out


# A table's own props: its caption (above it, "Table N.") and its notes
# (below it), plain text with ``$…$`` math. Nothing else (no colour).
TABLE_TEXT_PROPS = ("caption", "notes")
TABLE_TEXT_MAX = 2000


def check_table_props(props: dict[str, Any]) -> dict[str, Any]:
    """Raise ``ValueError`` unless ``props`` holds only a table's caption
    and notes, each a string of at most ``TABLE_TEXT_MAX`` characters
    (``None``: an update removing it)."""
    for key, value in props.items():
        if key not in TABLE_TEXT_PROPS:
            raise ValueError(f"a table's props are only caption and notes, not {key}")
        if value is None:  # an update's way to remove it
            continue
        if not isinstance(value, str):
            raise ValueError(f"a table's {key} must be a string")
        if len(value) > TABLE_TEXT_MAX:
            raise ValueError(f"a table's {key} is at most {TABLE_TEXT_MAX} characters")
    return props


def table_block_text(props: dict[str, Any], body: str) -> str:
    """A table's text with its caption first and its notes last."""
    parts = [str(props.get("caption") or ""), body, str(props.get("notes") or "")]
    return "\n".join(p for p in parts if p)


def check_table(content: dict[str, Any]) -> dict[str, Any]:
    """Raise ``ValueError`` with a reason unless ``content`` is a table a
    PaperHub stores: its cells fill a rectangular grid (merged cells
    counted by what they cover), no colours."""
    if not isinstance(content, dict) or content.get("type") != "tableContent":
        raise ValueError("a table's content must be a tableContent object")
    rows = content.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("a table needs at least one row")
    for row in rows:
        cells = row.get("cells") if isinstance(row, dict) else None
        if not isinstance(cells, list) or not cells:
            raise ValueError("every table row needs cells")
        for cell in cells:
            if isinstance(cell, dict) and cell.get("type") == "tableCell":
                props = cell.get("props") or {}
                if not isinstance(props, dict):
                    raise ValueError("a tableCell's props must be an object")
                for key, default in _CELL_DEFAULTS.items():
                    if props.get(key, default) != default:
                        raise ValueError(f"a table cell's {key} must stay {default}")
            check_inline(_cell_runs(cell))
    _, height, width = table_grid(content)
    v = content.get("headerRows", 0)
    if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= height:
        raise ValueError(f"headerRows must be 0 to {height} (the row count)")
    v = content.get("headerCols", 0)
    if isinstance(v, bool) or v not in (0, 1):
        raise ValueError("headerCols must be 0 or 1")
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
