"""A table block's ``tableContent`` as a GFM pipe table, and back.

The agent reads a table as a pipe table (the first row is the header, the
delimiter row carries each column's alignment) and writes one the same way.
A cell reads like any runs (``run_text.read_runs``); a pipe inside a
cell is written ``\\|``. Left alignment is the editor's default, so it is
written ``---`` and ``:--`` parses to the default. Laying a table written
as text over the stored one is ``inline_merge.merge_table``.

Merged cells are Obsidian Sheets Extended markers: a cell holding only
``<`` joins the cell to its left, one holding only ``^`` the cell above.
A cell whose whole text is ``<`` or ``^`` is written ``\\<`` / ``\\^``.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from paperhub.notes.models import GridCell, column_aligns, header_depth, table_grid, table_rows
from paperhub.notes.run_text import read_runs

_DELIM = re.compile(r"^\s*(:?)-+(:?)\s*$")
_ALIGN_ROW = {"center": ":-:", "right": "--:"}
_NO_DELIM = ("a table needs a header row, then a delimiter row with one "
             "cell per column (| --- | --- |)")

Runs = list[dict[str, Any]]


def _split(line: str) -> list[str] | None:
    """A row's cells (``\\|`` read as a pipe, edges trimmed), or None when
    the line has no unescaped pipe."""
    cells: list[str] = []
    cur: list[str] = []
    pipes = 0
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and line[i + 1:i + 2] == "|":
            cur.append("|")
            i += 2
            continue
        if ch == "|":
            pipes += 1
            cells.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    if not pipes:
        return None
    cells.append("".join(cur))
    stripped = line.strip()
    if stripped.startswith("|"):
        cells = cells[1:]
    if stripped.endswith("|") and not stripped.endswith("\\|"):
        cells = cells[:-1]
    return [c.strip() for c in cells]


def _align(cell: str) -> str:
    m = _DELIM.match(cell)
    if m is None:
        return "?"
    left, right = bool(m[1]), bool(m[2])
    if left and right:
        return "center"
    return "right" if right else ""


def _cell(text: str, align: str) -> dict[str, Any]:
    return {"type": "tableCell",
            "props": {"textAlignment": align} if align else {},
            "content": [{"type": "text", "text": text, "styles": {}}] if text else []}


_MARKERS = ("<", "^")
_ESCAPED = {"\\<": "<", "\\^": "^"}


def apply_merge_markers(texts: list[list[str]], aligns: list[str]
                        ) -> list[dict[str, Any]]:
    """The rows of a table written as text, its ``<`` / ``^`` marker cells
    folded into their anchors' ``colspan`` / ``rowspan`` (a ``<`` run right
    of a cell widens it, a ``^`` run below it lengthens it; inside the
    region every other cell is ``<``). Raises ``ValueError`` naming the
    row and column of a marker with nothing to join or a region that is
    not a rectangle. Whole-cell ``\\<`` / ``\\^`` read as text."""
    n = len(texts)
    width = len(texts[0]) if texts else 0
    owned: set[tuple[int, int]] = set()
    rows: list[dict[str, Any]] = [{"cells": []} for _ in range(n)]

    def where(r: int, c: int) -> str:
        return f"row {r + 1}, column {c + 1}"

    for r in range(n):
        for c in range(width):
            text = texts[r][c]
            if text in _MARKERS:
                if (r, c) not in owned:
                    if text == "<" and c == 0:
                        raise ValueError(f"{where(r, c)}: '<' joins the cell to "
                                         "its left, and there is none")
                    if text == "^" and r == 0:
                        raise ValueError(f"{where(r, c)}: '^' joins the cell "
                                         "above, and there is none")
                    raise ValueError(f"{where(r, c)}: '{text}' does not join a "
                                     "rectangular merged cell")
                continue
            cs = 1
            while c + cs < width and texts[r][c + cs] == "<" and (r, c + cs) not in owned:
                cs += 1
            rs = 1
            while r + rs < n and texts[r + rs][c] == "^":
                rs += 1
            for dr in range(rs):
                for dc in range(cs):
                    if (dr, dc) == (0, 0):
                        continue
                    want = "<" if dc else "^"
                    pos = (r + dr, c + dc)
                    if texts[pos[0]][pos[1]] != want or pos in owned:
                        raise ValueError(
                            f"{where(*pos)}: the merged cell at {where(r, c)} "
                            f"is not a rectangle (expected '{want}' here)")
                    owned.add(pos)
            cell = _cell(_ESCAPED.get(text, text), aligns[c])
            if rs > 1:
                cell["props"]["rowspan"] = rs
            if cs > 1:
                cell["props"]["colspan"] = cs
            rows[r]["cells"].append(cell)
    return rows


def parse_gfm_table(text: str) -> dict[str, Any]:
    """``tableContent`` from a GFM pipe table (the first row a header,
    merged cells from ``<`` / ``^`` markers). Raises ``ValueError`` with a
    reason for a missing delimiter row, a row whose cell count differs
    from the header's or a misplaced marker."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise ValueError(_NO_DELIM)
    header = _split(lines[0])
    delim = _split(lines[1])
    if header is None or delim is None or len(delim) != len(header):
        raise ValueError(_NO_DELIM)
    aligns = [_align(c) for c in delim]
    if "?" in aligns:
        raise ValueError(_NO_DELIM)
    width = len(header)
    rows = [header]
    for n, line in enumerate(lines[2:], start=2):
        cells = _split(line)
        count = len(cells) if cells is not None else 1
        if cells is None or count != width:
            raise ValueError(
                f"row {n} has {count} cell{'s' if count != 1 else ''}, the "
                f"header has {width}; every row needs one cell per column")
        rows.append(cells)
    content: dict[str, Any] = {
        "type": "tableContent", "columnWidths": [None] * width,
        "headerRows": 1, "rows": apply_merge_markers(rows, aligns)}
    for i, row in enumerate(content["rows"]):
        if not row["cells"]:
            raise ValueError(f"row {i + 1}: a merged cell covers the whole row; "
                             "every row needs a cell of its own")
    content["headerRows"] = header_depth(content)
    return content


def fold_cell(text: str) -> str:
    """A cell's text on one line, edges trimmed (a GFM row is one line)."""
    return " ".join(text.split("\n")).strip()


def table_to_gfm(content: dict[str, Any], quotes: dict[str, str] | None = None,
                 cell: Callable[[Runs], str] | None = None) -> str:
    """The table as a GFM pipe table: the first row as the header, then the
    delimiter row with each column's alignment, then the rest. ``cell``
    renders one cell's runs (default: as the agent reads runs)."""
    try:
        rows = table_rows(content)
        _, _, width = table_grid(content)
    except ValueError:
        return ""
    if not rows:
        return ""
    aligns = (column_aligns(content) + [""] * width)[:width]
    render = cell or (lambda runs: read_runs(runs, quotes))

    def line(cells: list[str]) -> str:
        cells = cells + [""] * (width - len(cells))
        return "| " + " | ".join(cells) + " |"

    grid, _, _ = table_grid(content)

    def text(r: int, c: int) -> str:
        at = grid[r][c]
        if not isinstance(at, GridCell):
            return "^" if at[1] == c else "<"
        t = fold_cell(render(rows[r][at.index])).replace("|", "\\|")
        return "\\" + t if t in _MARKERS else t

    body = [[text(r, c) for c in range(width)] for r in range(len(grid))]
    out = [line(body[0]), line([_ALIGN_ROW.get(a, "---") for a in aligns])]
    out += [line(r) for r in body[1:]]
    return "\n".join(out)
