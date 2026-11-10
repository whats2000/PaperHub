"""Page regions for chunks that came without one (PyMuPDF text, early Marker
chunks): each chunk's words are found on the paper's own PDF."""
from __future__ import annotations

from pathlib import Path

import pymupdf

from paperhub.pipelines.chunk_regions import locate_chunk_regions


def _pdf(path: Path) -> Path:
    doc = pymupdf.open()
    p1 = doc.new_page(width=612, height=792)
    p1.insert_text((72, 100), "Introduction to sparse experts", fontsize=12)
    p1.insert_text((72, 130), "The router assigns each token to the best expert.", fontsize=11)
    p1.insert_text((72, 150), "Load balancing keeps every expert busy.", fontsize=11)
    p2 = doc.new_page(width=612, height=792)
    p2.insert_text((72, 400), "A = W2(sR(W1(X) + b)) + b (9)", fontsize=11)
    p2.insert_text((72, 420), "where the bias b is shared by both layers.", fontsize=11)
    doc.save(path)
    return path


def test_chunks_get_their_page_and_the_box_around_their_words(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "paper.pdf")
    regions = locate_chunk_regions(pdf, [
        (1, "The router assigns each token to the best expert. Load balancing "
            "keeps every expert busy."),
        (2, "A = W2(σR(W1(X) + b)) + b\n(9)\nwhere the bias b is shared by both layers."),
        (3, "Nothing like this sentence is printed anywhere in the document."),
    ])
    page1, box1 = regions[1]
    assert page1 == 0
    x0, y0, x1, y1 = box1
    assert 70 <= x0 <= 74 and 115 <= y0 <= 125 and 150 <= y1 <= 160 and x1 > 300
    page2, box2 = regions[2]
    assert page2 == 1 and 385 <= box2[1] <= 395 and 420 <= box2[3] <= 430
    assert 3 not in regions                      # left for text search


def test_ligatures_and_hyphen_breaks_still_match(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "We define an efficient fine-tuning objective for", fontsize=11)
    page.insert_text((72, 115), "classification and regres-", fontsize=11)
    page.insert_text((72, 130), "sion tasks on every benchmark.", fontsize=11)
    doc.save(tmp_path / "l.pdf")
    regions = locate_chunk_regions(tmp_path / "l.pdf", [
        (7, "We deﬁne an efﬁcient ﬁne-tuning objective for classiﬁcation and "
            "regression tasks on every benchmark."),
    ])
    assert regions[7][0] == 0 and 125 <= regions[7][1][3] <= 140
