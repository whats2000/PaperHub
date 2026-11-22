"""A table's own lines: every line between cells is a border of its own.

The table block's ``borders`` prop is a JSON string
``{"preset": "paper"|"notion", "h": [...], "v": [...]}`` over the table's
grid (``models.table_grid``, R rows x C columns):

- ``h`` has R+1 strings of C digits: ``h[r][c]`` is the line above grid
  row ``r`` in grid column ``c``; ``h[R]`` is the bottom rule.
- ``v`` has R strings of C+1 digits: ``v[r][c]`` is the line left of grid
  column ``c`` in grid row ``r``.

A digit is ``0`` none, ``1`` thin, ``2`` thick. A missing or empty prop
is the Paper preset's lines for the table's shape. The lines never reach
the table's text: they are drawn, exported and kept, nothing else.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from paperhub.notes.models import Grid, GridCell, header_depth, table_grid

Preset = Literal["paper", "notion"]
PRESETS: tuple[Preset, ...] = ("paper", "notion")
BORDERS_MAX = 20_000


@dataclass(frozen=True)
class Borders:
    preset: Preset
    h: tuple[str, ...]
    v: tuple[str, ...]

    @property
    def shape(self) -> tuple[int, int]:
        """``(R, C)`` of the grid these lines are for."""
        rows = len(self.v)
        cols = len(self.h[0]) if self.h else 0
        return rows, cols


def _lines(raw: Any, name: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise ValueError(f"borders.{name} must be a list of digit strings")
    for line in raw:
        if any(ch not in "012" for ch in line):
            raise ValueError(f"borders.{name} digits are 0, 1 or 2")
    return tuple(raw)


def parse_borders(raw: str | None) -> Borders | None:
    """The lines a ``borders`` prop holds (None when missing or empty).
    Raises ``ValueError`` with a reason for anything malformed."""
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        raise ValueError("a table's borders must be a string")
    if len(raw) > BORDERS_MAX:
        raise ValueError(f"a table's borders are at most {BORDERS_MAX} characters")
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("a table's borders must be JSON") from exc
    if not isinstance(obj, dict):
        raise ValueError("a table's borders must be a JSON object")
    preset = obj.get("preset")
    if preset not in PRESETS:
        raise ValueError("borders.preset must be 'paper' or 'notion'")
    h = _lines(obj.get("h"), "h")
    v = _lines(obj.get("v"), "v")
    if len(h) != len(v) + 1:
        raise ValueError("borders.h needs one line more than borders.v")
    cols = len(h[0]) if h else 0
    if not cols or any(len(x) != cols for x in h):
        raise ValueError("every borders.h line needs one digit per column")
    if any(len(x) != cols + 1 for x in v):
        raise ValueError("every borders.v line needs one digit per column, plus one")
    return Borders(preset=preset, h=h, v=v)


def dump_borders(b: Borders) -> str:
    """The prop's compact JSON (keys in the order preset, h, v)."""
    return json.dumps({"preset": b.preset, "h": list(b.h), "v": list(b.v)},
                      separators=(",", ":"))


def preset_lines(rows: int, cols: int, header_rows: int, preset: Preset) -> Borders:
    """A preset's lines for a grid shape. Paper: a thick top rule, a thin
    rule under the header (when there is one and a body), a thick end
    rule, nothing else. Notion: every line thin."""
    if preset == "notion":
        return Borders(preset, tuple("1" * cols for _ in range(rows + 1)),
                       tuple("1" * (cols + 1) for _ in range(rows)))
    h = ["0" * cols for _ in range(rows + 1)]
    if 0 < header_rows < rows:
        h[header_rows] = "1" * cols
    h[0] = "2" * cols
    h[rows] = "2" * cols
    return Borders("paper", tuple(h), tuple("0" * (cols + 1) for _ in range(rows)))


def _shape(content: dict[str, Any]) -> tuple[int, int]:
    _, rows, cols = table_grid(content)
    return rows, cols


def borders_of(props: dict[str, Any], content: dict[str, Any]) -> Borders:
    """The table's lines: its own when they fit its grid, else its
    preset's (Paper when it has none) for its shape."""
    rows, cols = _shape(content)
    try:
        b = parse_borders(props.get("borders"))
    except ValueError:
        b = None
    if b is not None and b.shape == (rows, cols):
        return b
    preset: Preset = b.preset if b is not None else "paper"
    return preset_lines(rows, cols, header_depth(content), preset)


def fit_borders(props: dict[str, Any], content: dict[str, Any]) -> dict[str, Any]:
    """``props`` with ``borders`` fitted to the table's grid: kept when its
    shape is the grid's, else its preset's lines for the new shape. A
    missing or empty prop stays so (it draws as Paper). Malformed lines raise
    ``ValueError``."""
    raw = props.get("borders")
    b = parse_borders(raw)
    if b is None:
        return props
    rows, cols = _shape(content)
    if b.shape == (rows, cols):
        return props
    fitted = preset_lines(rows, cols, header_depth(content), b.preset)
    return {**props, "borders": dump_borders(fitted)}


Side = Literal["top", "bottom", "left", "right"]


def edge(b: Borders, grid: Grid, side: Side, anchor: GridCell) -> int:
    """The weight of one side of a (merged) cell: the heaviest segment it
    covers."""
    del grid  # the anchor carries the span; kept for the callers' symmetry
    r, c, rs, cs = anchor.row, anchor.col, anchor.rowspan, anchor.colspan
    if side == "top":
        segs = [b.h[r][x] for x in range(c, c + cs)]
    elif side == "bottom":
        segs = [b.h[r + rs][x] for x in range(c, c + cs)]
    elif side == "left":
        segs = [b.v[y][c] for y in range(r, r + rs)]
    else:
        segs = [b.v[y][c + cs] for y in range(r, r + rs)]
    return max(int(s) for s in segs)
