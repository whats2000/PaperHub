# backend/src/paperhub/pipelines/paper_asset.py
"""Unified PaperAsset contract (SRS v2.19 §III-5.1).

Both ingestion paths (arXiv LaTeX, PDF→Marker) normalize to this file-based
bundle under <source_dir>/asset/ so it survives paper_content cache-hits and is
read directly by the F3 slide agent. No DB column — located via source_dir_path.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


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
