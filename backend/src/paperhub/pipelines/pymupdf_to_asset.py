# backend/src/paperhub/pipelines/pymupdf_to_asset.py
"""Synchronous PyMuPDF "degraded" PaperAsset baseline (SRS v2.19, Plan F2.1).

The always-works ingestion path: no Marker dependency, returns instantly. It is
intentionally lower quality than Marker — figures get NO captions and NO owning
section (PyMuPDF can't reliably pair them), and equations are never extracted.
A background Marker pass upgrades the asset later. Sections come from the
font-size heading heuristic shared with the paper pipeline.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

from paperhub.pipelines.extract import extract_pdf_with_headings
from paperhub.pipelines.paper_asset import (
    FigureAsset,
    PaperAsset,
    SectionAsset,
    paper_asset_dir,
)


def _image_ext(data: bytes) -> str:
    """Sniff the image format from magic bytes → file extension."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    return ".png"  # default; pdflatex reads by extension


def pymupdf_to_asset(pdf_path: Path, *, source_dir: Path) -> PaperAsset:
    # Sections: dedup names, preserve first-seen document order.
    _, headings = extract_pdf_with_headings(pdf_path)
    sections: list[SectionAsset] = []
    seen_sections: set[str] = set()
    for name, _offset in headings:
        if name and name not in seen_sections:
            sections.append(SectionAsset(name=name, order=len(sections)))
            seen_sections.add(name)

    figs_dir = paper_asset_dir(source_dir) / "figures"
    figs_dir.mkdir(parents=True, exist_ok=True)

    figures: list[FigureAsset] = []
    fig_n = 0
    with pymupdf.open(pdf_path) as doc:  # type: ignore[no-untyped-call]
        for page_index in range(doc.page_count):
            page = doc[page_index]
            for img in page.get_images(full=True):
                xref = img[0]
                try:
                    extracted = doc.extract_image(xref)
                    data = extracted["image"]
                except Exception:
                    continue
                if not data:
                    continue
                fid = f"fig-{fig_n:03d}"
                ext = extracted.get("ext")
                suffix = f".{ext}" if ext else _image_ext(data)
                fname = f"{fid}{suffix}"
                (figs_dir / fname).write_bytes(data)
                figures.append(
                    FigureAsset(
                        id=fid,
                        caption="",
                        page=page_index,
                        section=None,
                        image_path=f"figures/{fname}",
                    )
                )
                fig_n += 1

    return PaperAsset(figures=figures, equations=[], sections=sections)
