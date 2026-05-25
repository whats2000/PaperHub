# backend/src/paperhub/pipelines/html_to_markdown.py
"""Render a Marker ``<table>`` HTML fragment to a markdown table (Plan F2.1 A2').

The agentic RAG flow has the model READ each chunk's markdown, so a table must
survive as a real markdown table (rows/columns intact) — NOT flattened to a
blob of cell text (the old ``strip_html`` path destroyed row/column structure
and duplicated cells). Pure stdlib (``html.parser``); no new dependency.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

_WS_RE = re.compile(r"\s+")


class _TableParser(HTMLParser):
    """Collect ``<tr>`` rows of cell text from a single ``<table>``.

    ``<br>`` becomes a space; all other inline tags are dropped (their text is
    kept). The first row that contains any ``<th>`` is the header; otherwise the
    first row is treated as the header.
    """

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.header_row_index: int | None = None
        self._in_cell = False
        self._cur_cell: list[str] = []
        self._cur_row: list[str] | None = None
        self._row_has_th = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._cur_row = []
            self._row_has_th = False
        elif tag in ("td", "th"):
            self._in_cell = True
            self._cur_cell = []
            if tag == "th":
                self._row_has_th = True
        elif tag == "br":
            if self._in_cell:
                self._cur_cell.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br" and self._in_cell:
            self._cur_cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            if self._cur_row is not None:
                self._cur_row.append(_clean_cell("".join(self._cur_cell)))
            self._in_cell = False
            self._cur_cell = []
        elif tag == "tr":
            if self._cur_row is not None and self._cur_row:
                if self._row_has_th and self.header_row_index is None:
                    self.header_row_index = len(self.rows)
                self.rows.append(self._cur_row)
            self._cur_row = None

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cur_cell.append(data)


def _clean_cell(text: str) -> str:
    """Collapse whitespace + strip a single cell's text."""
    return _WS_RE.sub(" ", text).strip()


def html_table_to_markdown(table_html: str) -> str:
    """Convert a ``<table>`` HTML fragment to a markdown table.

    Tolerates missing ``<thead>``/``<tbody>``, ragged rows (padded/truncated to
    the column count), empty cells, and ``<br>`` (→ space). When no ``<th>`` is
    present, the first ``<tr>`` is treated as the header. Returns an empty string
    when the fragment has no rows.
    """
    parser = _TableParser()
    parser.feed(table_html or "")
    parser.close()

    rows = parser.rows
    if not rows:
        return ""

    header_idx = parser.header_row_index if parser.header_row_index is not None else 0
    header = rows[header_idx]
    # The header row defines the column count; body rows are padded/truncated to
    # match (a markdown table has a fixed column count driven by its header).
    ncols = len(header)
    if ncols == 0:
        return ""

    def _fit(row: list[str]) -> list[str]:
        row = list(row[:ncols])
        row += [""] * (ncols - len(row))
        return row

    lines: list[str] = []
    lines.append("| " + " | ".join(_fit(header)) + " |")
    lines.append("| " + " | ".join(["---"] * ncols) + " |")
    for i, row in enumerate(rows):
        if i == header_idx:
            continue
        lines.append("| " + " | ".join(_fit(row)) + " |")
    return "\n".join(lines)
