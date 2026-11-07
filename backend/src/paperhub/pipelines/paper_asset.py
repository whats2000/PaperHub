# backend/src/paperhub/pipelines/paper_asset.py
"""Unified PaperAsset contract (SRS v2.19 §III-5.1).

Both ingestion paths (arXiv LaTeX, PDF→Marker) normalize to this file-based
bundle under <source_dir>/asset/ so it survives paper_content cache-hits and is
read directly by the F3 slide agent. No DB column — located via source_dir_path.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

_LOG = logging.getLogger("paperhub.paper_asset")


@dataclass(frozen=True)
class FigureAsset:
    id: str
    caption: str
    page: int | None
    section: str | None
    image_path: str            # relative to the asset dir, e.g. "figures/fig-001.png"

    def abs_image_path(self, source_dir: Path) -> Path:
        return paper_asset_dir(source_dir) / self.image_path


@dataclass(frozen=True)
class EquationAsset:
    id: str
    latex: str
    section: str | None


@dataclass(frozen=True)
class SectionAsset:
    name: str
    order: int


@dataclass(frozen=True)
class PaperAsset:
    figures: list[FigureAsset] = field(default_factory=list)
    equations: list[EquationAsset] = field(default_factory=list)
    sections: list[SectionAsset] = field(default_factory=list)


def paper_asset_dir(source_dir: Path) -> Path:
    return Path(source_dir) / "asset"


def write_paper_asset(asset: PaperAsset, source_dir: Path) -> None:
    d = paper_asset_dir(source_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "figures.json").write_text(
        json.dumps([asdict(f) for f in asset.figures], ensure_ascii=False, indent=2),
        encoding="utf-8")
    (d / "equations.json").write_text(
        json.dumps([asdict(e) for e in asset.equations], ensure_ascii=False, indent=2),
        encoding="utf-8")
    (d / "structure.json").write_text(
        json.dumps([asdict(s) for s in asset.sections], ensure_ascii=False, indent=2),
        encoding="utf-8")


def read_paper_asset(source_dir: Path) -> PaperAsset | None:
    d = paper_asset_dir(source_dir)
    fjson = d / "figures.json"
    if not fjson.exists():
        return None
    figs = [FigureAsset(**x) for x in json.loads(fjson.read_text(encoding="utf-8"))]
    eqs_p = d / "equations.json"
    eqs = [EquationAsset(**x) for x in json.loads(eqs_p.read_text(encoding="utf-8"))] if eqs_p.exists() else []
    sec_p = d / "structure.json"
    secs = [SectionAsset(**x) for x in json.loads(sec_p.read_text(encoding="utf-8"))] if sec_p.exists() else []
    return PaperAsset(figures=figs, equations=eqs, sections=secs)


@dataclass(frozen=True)
class BlockAsset:
    """One Marker block, persisted for PDF bilingual reading (Plan H1).

    ``page`` is 0-based; ``bbox`` is (x0, y0, x1, y1) in Marker/PDF points.
    """

    block_id: str
    block_type: str
    page: int
    bbox: tuple[float, float, float, float]
    html: str
    section: str | None


def write_block_assets(
    source_dir: Path, blocks: list[BlockAsset], *, inline_math_llm: bool,
) -> None:
    """Write ``asset/blocks.json`` (Marker reading order). Additive: touches nothing else."""
    d = paper_asset_dir(source_dir)
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "inline_math_llm": inline_math_llm,
        "blocks": [{**asdict(b), "bbox": list(b.bbox)} for b in blocks],
    }
    # Atomic: a kill mid-write must never leave a truncated blocks.json.
    fd, tmp = tempfile.mkstemp(dir=d, prefix="blocks.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, indent=2))
        os.replace(tmp, d / "blocks.json")
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_block_assets(source_dir: Path) -> tuple[list[BlockAsset], bool] | None:
    p = paper_asset_dir(source_dir) / "blocks.json"
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        blocks = [
            BlockAsset(**{**x, "bbox": tuple(float(v) for v in x["bbox"])})
            for x in raw["blocks"]
        ]
    except (ValueError, KeyError, TypeError) as exc:  # JSONDecodeError is a ValueError
        _LOG.warning("unreadable %s (%s) - treating as missing", p, exc)
        return None
    return blocks, bool(raw.get("inline_math_llm", False))
