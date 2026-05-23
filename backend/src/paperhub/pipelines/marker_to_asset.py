# backend/src/paperhub/pipelines/marker_to_asset.py
"""Map a Marker MarkerDoc into the unified PaperAsset (SRS v2.19 §III-5.1)."""
from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path

from paperhub.pipelines.marker_client import MarkerBlock, MarkerDoc
from paperhub.pipelines.paper_asset import (
    EquationAsset,
    FigureAsset,
    PaperAsset,
    SectionAsset,
    paper_asset_dir,
)

_TAG = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    return _TAG.sub("", html or "").strip()


def _section_of(block: MarkerBlock) -> str | None:
    sh = block.section_hierarchy or {}
    if not sh:
        return None
    return sh[max(sh.keys())]  # deepest level wins


def marker_doc_to_asset(doc: MarkerDoc, *, source_dir: Path) -> PaperAsset:
    figs_dir = paper_asset_dir(source_dir) / "figures"
    figs_dir.mkdir(parents=True, exist_ok=True)

    figures: list[FigureAsset] = []
    equations: list[EquationAsset] = []
    sections: list[SectionAsset] = []
    seen_sections: set[str] = set()
    fig_n = eq_n = 0

    for block in doc.blocks:
        sec = _section_of(block)
        if sec and sec not in seen_sections:
            sections.append(SectionAsset(name=sec, order=len(sections)))
            seen_sections.add(sec)
        if block.block_type in ("Figure", "Picture") and block.images:
            raw = next(iter(block.images.values()))
            try:
                data = base64.b64decode(raw, validate=True)
            except (binascii.Error, ValueError):
                continue
            fid = f"fig-{fig_n:03d}"
            (figs_dir / f"{fid}.png").write_bytes(data)
            figures.append(
                FigureAsset(
                    id=fid,
                    caption=_strip_html(block.html),
                    page=block.page,
                    section=sec,
                    image_path=f"figures/{fid}.png",
                )
            )
            fig_n += 1
        elif block.block_type == "Equation" and block.latex:
            equations.append(
                EquationAsset(id=f"eq-{eq_n:03d}", latex=block.latex.strip(), section=sec)
            )
            eq_n += 1

    return PaperAsset(figures=figures, equations=equations, sections=sections)
