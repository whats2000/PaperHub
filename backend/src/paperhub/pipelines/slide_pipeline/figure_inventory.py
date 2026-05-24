# backend/src/paperhub/pipelines/slide_pipeline/figure_inventory.py
"""Deck-wide figure inventory + deterministic no-hallucination guard.

SRS v2.19 §III-5.3 contract 2 (never a non-existent figure). Reads each
enabled paper's PaperAsset, assigns collision-free deck-unique keys, stages
the real figure files into one deck dir, and provides
``verify_and_fix_graphics`` which replaces any ``\\includegraphics`` of a
non-inventory key with a placeholder before compile.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from paperhub.pipelines.paper_asset import read_paper_asset

_GRAPHICS_RE = re.compile(r"\\includegraphics(\[[^\]]*\])?\{([^}]+)\}")


@dataclass(frozen=True)
class InventoryFigure:
    key: str
    caption: str
    abs_path: str
    paper_id: int


def build_inventory(papers: list[dict[str, object]]) -> list[InventoryFigure]:
    """Build a deck-unique figure inventory from the enabled papers.

    ``papers`` items are ``{"id": int, "source_dir": str}``. Keys are made
    collision-free by prefixing each figure id with the paper's enumerated
    index (``p{idx}-{figure_id}``).
    """
    inventory: list[InventoryFigure] = []
    for idx, p in enumerate(papers):
        source_dir = Path(str(p["source_dir"]))
        asset = read_paper_asset(source_dir)
        if asset is None:
            continue
        for fig in asset.figures:
            inventory.append(
                InventoryFigure(
                    key=f"p{idx}-{fig.id}",
                    caption=fig.caption,
                    abs_path=str(fig.abs_image_path(source_dir)),
                    paper_id=int(str(p["id"])),
                )
            )
    return inventory


def stage_inventory(inv: list[InventoryFigure], dest_dir: Path) -> None:
    """Copy each inventory figure's real file into one deck figures dir.

    Files are renamed to ``{key}{suffix}`` so the deck-unique key is also the
    on-disk filename the LaTeX ``\\includegraphics{key}`` resolves to.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    for fig in inv:
        src = Path(fig.abs_path)
        if src.exists():
            shutil.copy2(src, dest_dir / f"{fig.key}{src.suffix or '.png'}")


def verify_and_fix_graphics(
    tex: str, allowed_keys: set[str]
) -> tuple[str, list[str]]:
    """Replace any ``\\includegraphics`` of a non-inventory key with a placeholder.

    Deterministic no-hallucination guard. A graphic is kept verbatim only if
    its name (or its stem) is in ``allowed_keys``; otherwise it is recorded in
    ``rejected`` and replaced with ``\\textit{[figure omitted]}``.
    """
    rejected: list[str] = []

    def _replace(m: re.Match[str]) -> str:
        name = m.group(2)
        stem = Path(name).stem
        if stem in allowed_keys or name in allowed_keys:
            return m.group(0)
        rejected.append(name)
        return r"\textit{[figure omitted]}"

    fixed = _GRAPHICS_RE.sub(_replace, tex)
    return fixed, rejected
