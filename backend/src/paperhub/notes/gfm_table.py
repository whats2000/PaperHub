"""A table block's ``tableContent`` as a GFM pipe table, and back.

The agent reads a table as a pipe table (the first row is the header, the
delimiter row carries each column's alignment) and writes one the same way.
A cell reads like any runs (``inline_merge.read_runs``); a pipe inside a
cell is written ``\\|``. Left alignment is the editor's default, so it is
written ``---`` and ``:--`` parses to the default.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Callable
from typing import Any

from paperhub.notes.inline_merge import (
    is_rich,
    preserve_runs,
    read_runs,
    unwrap_cite_tags,
)
from paperhub.notes.models import table_aligns, table_rows

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


def _fold(text: str) -> str:
    """A cell's text on one line, edges trimmed (a GFM row is one line)."""
    return " ".join(text.split("\n")).strip()


def table_to_gfm(content: dict[str, Any], quotes: dict[str, str] | None = None,
                 cell: Callable[[Runs], str] | None = None) -> str:
    """The table as a GFM pipe table: the first row as the header, then the
    delimiter row with each column's alignment, then the rest. ``cell``
    renders one cell's runs (default: as the agent reads runs)."""
    try:
        rows = table_rows(content)
    except ValueError:
        return ""
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    aligns = (table_aligns(content) + [""] * width)[:width]
    render = cell or (lambda runs: read_runs(runs, quotes))

    def line(cells: list[str]) -> str:
        cells = cells + [""] * (width - len(cells))
        return "| " + " | ".join(cells) + " |"

    body = [[_fold(render(c)).replace("|", "\\|") for c in r] for r in rows]
    out = [line(body[0]), line([_ALIGN_ROW.get(a, "---") for a in aligns])]
    out += [line(r) for r in body[1:]]
    return "\n".join(out)


def _cell_node(cell: Any) -> dict[str, Any]:
    """A parsed cell as a ``tableCell`` node."""
    if isinstance(cell, dict) and cell.get("type") == "tableCell":
        return cell
    return {"type": "tableCell", "props": {},
            "content": table_rows({"rows": [{"cells": [cell]}]})[0][0]}


def merge_table(old: dict[str, Any], new: dict[str, Any],
                quotes: dict[str, str]) -> tuple[dict[str, Any], dict[str, list[Any]]]:
    """``new`` (a table the agent wrote as text) laid over the stored
    ``old`` one, cell by cell: a cell whose text is unchanged keeps its
    runs (the same place first, else an unused old cell of that text), a
    changed cell keeps the runs its text did not change (``preserve_runs``
    against the cell in the same place). The header / column settings of
    ``old`` stay while the column count does. ``(content, report)``,
    ``report`` the inline nodes ``kept`` and ``dropped``."""
    old_rows = table_rows(old)
    old_text = [[_fold(read_runs(c, quotes)) for c in r] for r in old_rows]
    used: set[tuple[int, int]] = set()
    report: dict[str, list[Any]] = {"kept": [], "dropped": []}

    def same_text(r: int, c: int, text: str) -> tuple[int, int] | None:
        if (r < len(old_rows) and c < len(old_rows[r])
                and (r, c) not in used and old_text[r][c] == text):
            return r, c
        for i, row in enumerate(old_text):
            for j, t in enumerate(row):
                if t == text and (i, j) not in used:
                    return i, j
        return None

    rows: list[dict[str, Any]] = []
    for r, row in enumerate(new.get("rows", [])):
        cells: list[dict[str, Any]] = []
        for c, raw in enumerate(row.get("cells", [])):
            node = _cell_node(raw)
            text = unwrap_cite_tags(read_runs(node.get("content") or []))
            runs: Runs = ([{"type": "text", "text": text, "styles": {}}]
                          if text else [])
            hit = same_text(r, c, _fold(text))
            if hit is not None:
                used.add(hit)
                runs = copy.deepcopy(old_rows[hit[0]][hit[1]])
            elif (r < len(old_rows) and c < len(old_rows[r])
                  and is_rich(old_rows[r][c])):
                runs, rep = preserve_runs(old_rows[r][c], text, quotes)
                report["kept"] += rep["kept"]
                report["dropped"] += rep["dropped"]
            cells.append({**node, "content": runs})
        rows.append({"cells": cells})
    out = {**new, "rows": rows}
    width = len(rows[0]["cells"]) if rows else 0
    for key in ("headerRows", "headerCols"):
        if key in old:
            out[key] = old[key]
    widths = old.get("columnWidths")
    if isinstance(widths, list) and len(widths) == width:
        out["columnWidths"] = list(widths)
    return out, report


def is_plain_table(content: dict[str, Any]) -> bool:
    """Whether every cell is plain text (a table written as text): only
    such a table is laid over the stored one cell by cell."""
    try:
        rows = table_rows(content)
    except ValueError:
        return False
    return not any(is_rich(c) for row in rows for c in row)
