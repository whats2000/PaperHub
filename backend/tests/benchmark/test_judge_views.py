"""The judge's [view:] images: a cited region it cannot render is reported
as missing instead of aborting the case."""
from __future__ import annotations

from pathlib import Path

import pymupdf

from benchmark.judge import _view_parts


def test_view_beyond_the_pdf_is_reported_missing(tmp_path: Path) -> None:
    pdf = tmp_path / "p.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(pdf)
    text, parts = _view_parts([
        {"key": "1/1/0,0,1000,1000", "paper_content_id": 1, "page": 1,
         "box": [0, 0, 1000, 1000], "pdf_path": str(pdf)},
        {"key": "1/9/0,0,500,500", "paper_content_id": 1, "page": 9,
         "box": [0, 0, 500, 500], "pdf_path": str(pdf)},
    ])
    assert "[view:1/1/0,0,1000,1000] (region of page 1; image attached below)" in text
    assert "[view:1/9/0,0,500,500] (page region; image missing)" in text
    assert sum(1 for p in parts if p["type"] == "image_url") == 1
