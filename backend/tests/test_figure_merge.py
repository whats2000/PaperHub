"""Figures Marker split into pieces are joined back into one."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from PIL import Image

from paperhub.pipelines.figure_merge import apply_merges, plan_merges
from paperhub.pipelines.paper_asset import FigureAsset, PaperAsset, paper_asset_dir


@dataclass
class B:
    block_type: str
    page: int | None
    bbox: list[float]
    html: str = ""
    extra: list[str] = field(default_factory=list)


def _fig(fid: str, page: int, bbox: list[float], caption: str = "") -> FigureAsset:
    return FigureAsset(fid, caption, page, None, f"figures/{fid}.png",
                       bbox=(bbox[0], bbox[1], bbox[2], bbox[3]))


# V-JEPA Fig. 6: a diagram above a "(a) ..." text, then the captioned grid.
VJEPA = [
    B("SectionHeader", 9, [104, 52, 137, 62], "<h2>Frozen</h2>"),
    B("Picture", 9, [96, 62, 514, 133]),
    B("Text", 9, [55, 142, 555, 184], "<p>(a) Visualization Methodology. We train a "
      "conditional diffusion model to decode the V-JEPA feature-space predictions.</p>"),
    B("Picture", 9, [50, 186, 560, 446]),
    B("Caption", 9, [55, 533, 432, 543], "<p>Figure 6 Qualitative Analysis.</p>"),
]
# V-JEPA 2 Fig. 15: uncaptioned panels; the main caption tagged as Text.
VJEPA2 = [
    B("Text", 38, [126, 84, 204, 94], "<p>Robot observations</p>"),
    B("Picture", 38, [203, 75, 495, 289]),
    B("Text", 38, [68, 292, 542, 401], "<p>(a) Comparing accuracy of predictions.</p>"),
    B("Picture", 38, [173, 438, 486, 564]),
    B("Text", 38, [69, 645, 541, 698], "<p>Figure 15 Decoding representations.</p>"),
]
# A logo on page 0 above the title prose: never joined to anything.
LOGO = [
    B("Picture", 0, [50, 40, 150, 70]),
    B("Text", 0, [50, 80, 550, 300], "<p>" + "Long abstract prose. " * 20 + "</p>"),
    B("Picture", 0, [60, 320, 540, 500]),
    B("Caption", 0, [60, 505, 540, 520], "<p>Figure 1 Overview.</p>"),
]


def test_joins_the_captioned_figure_with_the_pieces_above_it() -> None:
    figs = [_fig("fig-006", 9, [96, 62, 514, 133]),
            _fig("fig-007", 9, [50, 186, 560, 446], "Figure 6 Qualitative Analysis.")]
    [m] = plan_merges(figs, VJEPA)
    assert (m.keep, m.absorbed, m.page) == ("fig-007", ["fig-006"], 9)
    assert m.bbox == (50, 52, 560, 446)          # diagram, its label and the grid


def test_a_figure_named_only_by_a_text_caption_collects_its_panels() -> None:
    figs = [_fig("fig-015", 38, [203, 75, 495, 289]),
            _fig("fig-016", 38, [173, 438, 486, 564])]
    [m] = plan_merges(figs, VJEPA2)
    assert (m.keep, m.absorbed) == ("fig-015", ["fig-016"])
    assert m.caption == "Figure 15 Decoding representations."


def test_a_logo_above_prose_is_never_joined() -> None:
    figs = [_fig("fig-000", 0, [50, 40, 150, 70]),
            _fig("fig-001", 0, [60, 320, 540, 500], "Figure 1 Overview.")]
    assert plan_merges(figs, LOGO) == []


def test_apply_crops_the_joined_region_and_drops_the_pieces(tmp_path: Path) -> None:
    pdf = tmp_path / "p.pdf"
    doc = pymupdf.open()
    for _ in range(10):
        doc.new_page(width=612, height=792)
    doc.save(pdf)
    (paper_asset_dir(tmp_path) / "figures").mkdir(parents=True)
    figs = [_fig("fig-006", 9, [96, 62, 514, 133]),
            _fig("fig-007", 9, [50, 186, 560, 446], "Figure 6 Qualitative Analysis.")]
    asset = apply_merges(PaperAsset(figures=figs), plan_merges(figs, VJEPA), pdf, tmp_path)
    [fig] = asset.figures
    assert fig.id == "fig-007" and fig.bbox == (50, 52, 560, 446)
    im = Image.open(fig.abs_image_path(tmp_path))
    assert abs(im.width / im.height - 510 / 394) < 0.02
    again = plan_merges(asset.figures, VJEPA)
    assert again == []                       # idempotent: nothing left to join
