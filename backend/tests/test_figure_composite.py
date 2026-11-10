"""Multi-panel LaTeX figures become one composite image."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from paperhub.pipelines.figure_composite import Panel, compose_panels, width_share
from paperhub.pipelines.latex_to_asset import figure_panels, latex_source_to_asset


def _png(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> None:
    Image.new("RGB", size, color).save(path)


def test_width_share() -> None:
    assert width_share(r"[width=0.47\linewidth,trim={0 5cm 0 0},clip]") == 0.47
    assert width_share(r"[width=\textwidth]") == 1.0
    assert width_share("[width=3.25in]") == 0.5
    assert width_share("[height=1.5in]") is None and width_share("") is None


def test_figure_panels_reads_order_widths_breaks_and_labels(tmp_path: Path) -> None:
    for n in "abc":
        _png(tmp_path / f"{n}.png", (10, 10), (0, 0, 0))
    body = (r"\centering\subfigure[Linear]{\includegraphics[width=0.45\linewidth]{a}"
            r"\label{fig:a}}\hfill\subfigure[Radial]{\includegraphics[width=0.45\linewidth]{b}}"
            "\n\n" r"\includegraphics[width=0.9\linewidth]{c}\caption{Three.}")
    panels = figure_panels(body, tmp_path)
    assert [(p.resolved.name, p.width, p.new_row, p.label) for p in panels] == [
        ("a.png", 0.45, False, "(a) Linear"),
        ("b.png", 0.45, False, "(b) Radial"),
        ("c.png", 0.9, True, None),
    ]


def test_compose_lays_panels_out_in_rows(tmp_path: Path) -> None:
    _png(tmp_path / "a.png", (400, 200), (255, 0, 0))
    _png(tmp_path / "b.png", (400, 200), (0, 0, 255))
    _png(tmp_path / "c.png", (800, 200), (0, 255, 0))
    out = tmp_path / "fig.png"
    w, h = compose_panels([
        Panel(tmp_path / "a.png", 0.5, False, "(a) Linear"),
        Panel(tmp_path / "b.png", 0.5, False, "(b) Radial"),
        Panel(tmp_path / "c.png", 1.0, False, None),   # full line: a new row
    ], out)
    im = Image.open(out).convert("RGB")
    assert (w, h) == im.size and w == 1600
    # Row 1: red left, blue right; row 2: green across.
    assert im.getpixel((200, 100))[0] > 200 and im.getpixel((1400, 100))[2] > 200
    assert im.getpixel((800, h - 50))[1] > 200


def test_multi_panel_figure_gets_one_composite_asset(tmp_path: Path) -> None:
    src = tmp_path / "source"
    src.mkdir()
    _png(src / "a.png", (300, 300), (255, 0, 0))
    _png(src / "b.png", (300, 300), (0, 0, 255))
    flat = (r"\begin{figure}\subfigure[L]{\includegraphics[width=0.45\linewidth]{a}}"
            r"\subfigure[R]{\includegraphics[width=0.45\linewidth]{b}}\caption{Both.}"
            r"\end{figure}")
    asset = latex_source_to_asset(src, flat, source_dir=tmp_path)
    [fig] = asset.figures
    assert fig.id == "fig-001" and fig.panels == 2 and fig.caption == "Both."
    im = Image.open(fig.abs_image_path(tmp_path)).convert("RGB")
    assert im.width == 1600
    assert im.getpixel((500, 150))[0] > 200 and im.getpixel((1100, 150))[2] > 200
