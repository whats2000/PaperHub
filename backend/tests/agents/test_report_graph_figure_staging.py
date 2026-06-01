"""F4.5 figure-staging — closes the 'no figures in PDF' bug.

The Phase-10 report_graph rewrite (commit 7aa679a) deleted R1's
``sl_assemble.stage_inventory`` step but did NOT add a replacement.
Result: ``deck.tex`` correctly emits ``\\includegraphics{p0-fig-001}``
(the inventory key gather_context registers), but the corresponding
figure file is never copied into ``workdir`` → every generated deck
rendered placeholders instead of images.

``_stage_figures`` copies each paper's figures into ``workdir`` under
the inventory-key-matching name ``p{paper_idx}-{fig.id}{src.suffix}`` so
pdflatex's default ``\\graphicspath`` (= the document directory) finds
them. These tests pin the key scheme + the soft-fail posture.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from paperhub.agents.report_graph import _stage_figures
from paperhub.pipelines.paper_asset import (
    FigureAsset,
    PaperAsset,
    write_paper_asset,
)


def _seed_paper_with_figures(
    source_dir: Path, figure_specs: list[tuple[str, str, tuple[int, int]]]
) -> None:
    """Write a PaperAsset + on-disk figure files under ``source_dir/asset/``.

    Each spec is ``(fig_id, image_relpath, (width, height))``. ``image_relpath``
    is relative to the asset dir (e.g. ``"figures/fig-001.png"``).
    """
    asset_root = source_dir / "asset"
    asset_root.mkdir(parents=True, exist_ok=True)
    figures: list[FigureAsset] = []
    for fig_id, image_relpath, (w, h) in figure_specs:
        on_disk = asset_root / image_relpath
        on_disk.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (w, h), color=(255, 0, 0)).save(on_disk)
        figures.append(
            FigureAsset(
                id=fig_id, caption="x", page=1, section="Method",
                image_path=image_relpath,
            )
        )
    write_paper_asset(
        PaperAsset(figures=figures, equations=[], sections=[]),
        source_dir=source_dir,
    )


@pytest.mark.asyncio
async def test_stage_figures_copies_each_figure_to_workdir(tmp_path: Path) -> None:
    """Each figure file lands in workdir under the inventory-key-matching name."""
    src_dir = tmp_path / "paper_source"
    _seed_paper_with_figures(
        src_dir,
        [
            ("fig-001", "figures/fig-001.png", (100, 100)),
            ("fig-002", "figures/fig-002.png", (200, 100)),
        ],
    )
    workdir = tmp_path / "slides"

    staged = await _stage_figures(
        papers=[{"id": 1, "source_dir": str(src_dir)}],
        workdir=workdir,
    )

    assert (workdir / "p0-fig-001.png").exists()
    assert (workdir / "p0-fig-002.png").exists()
    # Sanity: real bytes copied (not zero-length stub)
    assert (workdir / "p0-fig-001.png").stat().st_size > 0
    assert set(staged) == {"p0-fig-001.png", "p0-fig-002.png"}


@pytest.mark.asyncio
async def test_stage_figures_skips_missing_source(tmp_path: Path) -> None:
    """A figure whose source file doesn't exist is silently skipped."""
    src_dir = tmp_path / "paper_source"
    asset_root = src_dir / "asset"
    asset_root.mkdir(parents=True)
    # Write an asset that REFERENCES a non-existent file (no on-disk image).
    write_paper_asset(
        PaperAsset(
            figures=[
                FigureAsset(
                    id="missing", image_path="figures/missing.png",
                    caption="", page=1, section="",
                ),
            ],
            equations=[], sections=[],
        ),
        source_dir=src_dir,
    )
    workdir = tmp_path / "slides"

    staged = await _stage_figures(
        papers=[{"id": 1, "source_dir": str(src_dir)}],
        workdir=workdir,
    )

    assert not (workdir / "p0-missing.png").exists()
    assert staged == []


@pytest.mark.asyncio
async def test_stage_figures_uses_paper_idx_prefix_for_multi_paper(
    tmp_path: Path,
) -> None:
    """Multi-paper decks: each paper's figures use the right p{idx}- prefix."""
    src1 = tmp_path / "p1"
    src2 = tmp_path / "p2"
    _seed_paper_with_figures(src1, [("fig-001", "figures/fig-001.png", (100, 100))])
    _seed_paper_with_figures(src2, [("fig-001", "figures/fig-001.png", (200, 100))])
    workdir = tmp_path / "slides"

    staged = await _stage_figures(
        papers=[
            {"id": 1, "source_dir": str(src1)},
            {"id": 2, "source_dir": str(src2)},
        ],
        workdir=workdir,
    )

    assert (workdir / "p0-fig-001.png").exists()
    assert (workdir / "p1-fig-001.png").exists()
    # Distinct papers → distinct files even with the same fig.id
    assert (workdir / "p0-fig-001.png").stat().st_size != (
        workdir / "p1-fig-001.png"
    ).stat().st_size
    assert set(staged) == {"p0-fig-001.png", "p1-fig-001.png"}


@pytest.mark.asyncio
async def test_stage_figures_skips_paper_without_source_dir(tmp_path: Path) -> None:
    """A paper dict without ``source_dir`` is silently skipped (degrades gracefully)."""
    workdir = tmp_path / "slides"
    staged = await _stage_figures(
        papers=[{"id": 1}, {"id": 2, "source_dir": ""}],
        workdir=workdir,
    )
    # workdir was still created (no figures to stage)
    assert workdir.exists()
    assert staged == []


@pytest.mark.asyncio
async def test_stage_figures_skips_paper_with_no_asset(tmp_path: Path) -> None:
    """A source_dir with no asset/ subdir → skip (no read_paper_asset → None)."""
    src_dir = tmp_path / "no_asset_paper"
    src_dir.mkdir()
    workdir = tmp_path / "slides"
    staged = await _stage_figures(
        papers=[{"id": 1, "source_dir": str(src_dir)}],
        workdir=workdir,
    )
    assert staged == []
    assert workdir.exists()


@pytest.mark.asyncio
async def test_stage_figures_preserves_source_extension(tmp_path: Path) -> None:
    """Non-PNG extensions (e.g. .jpg, .pdf) are preserved on the staged file."""
    src_dir = tmp_path / "paper_source"
    asset_root = src_dir / "asset"
    asset_root.mkdir(parents=True)
    (asset_root / "figures").mkdir()
    # JPEG source — Pillow needs RGB mode.
    Image.new("RGB", (100, 100)).save(asset_root / "figures" / "fig-001.jpg")
    write_paper_asset(
        PaperAsset(
            figures=[
                FigureAsset(
                    id="fig-001", image_path="figures/fig-001.jpg",
                    caption="", page=1, section="",
                ),
            ],
            equations=[], sections=[],
        ),
        source_dir=src_dir,
    )
    workdir = tmp_path / "slides"

    await _stage_figures(
        papers=[{"id": 1, "source_dir": str(src_dir)}],
        workdir=workdir,
    )

    assert (workdir / "p0-fig-001.jpg").exists()
    # Did NOT silently turn into .png
    assert not (workdir / "p0-fig-001.png").exists()
