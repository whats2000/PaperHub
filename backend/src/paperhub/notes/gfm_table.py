"""A table block's ``tableContent`` as a GFM pipe table, and back.

The agent reads a table as a pipe table (the first row is the header, the
delimiter row carries each column's alignment) and writes one the same way.
A cell reads like any runs (``run_text.read_runs``); a pipe inside a
cell is written ``\\|``. Left alignment is the editor's default, so it is
written ``---`` and ``:--`` parses to the default. Laying a table written
as text over the stored one is ``inline_merge.merge_table``.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from paperhub.notes.models import column_aligns, table_grid, table_rows
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


def parse_gfm_table(text: str) -> dict[str, Any]:
    """``tableContent`` from a GFM pipe table (the first row a header).
    Raises ``ValueError`` with a reason for a missing delimiter row or a
    row whose cell count differs from the header's."""
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
    return {"type": "tableContent", "columnWidths": [None] * width,
            "headerRows": 1,
            "rows": [{"cells": [_cell(t, a) for t, a in zip(r, aligns, strict=True)]}
                     for r in rows]}


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

    body = [[fold_cell(render(c)).replace("|", "\\|") for c in r] for r in rows]
    out = [line(body[0]), line([_ALIGN_ROW.get(a, "---") for a in aligns])]
    out += [line(r) for r in body[1:]]
    return "\n".join(out)
