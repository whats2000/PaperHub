"""LaTeX figures are placed on their compiled page: the page and the box of
the graphics that belong to the caption."""
from __future__ import annotations

import io
from pathlib import Path

import pymupdf
from PIL import Image

from paperhub.pipelines.figure_pages import locate_latex_figures
from paperhub.pipelines.paper_asset import FigureAsset

PROSE = ("This paragraph is body text of the paper, long enough to read as prose "
         "and not as a label inside a figure. ") * 3


def _png(size: tuple[int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 120, 60)).save(buf, format="PNG")
    return buf.getvalue()


def _fig(fid: str, caption: str) -> FigureAsset:
    return FigureAsset(fid, caption, None, None, f"figures/{fid}.png")


def _close(a: tuple[float, ...], b: tuple[float, ...], tol: float = 3.0) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def test_box_above_its_caption(tmp_path: Path) -> None:
    pdf = tmp_path / "p.pdf"
    doc = pymupdf.open()
    doc.new_page()                                   # page 0: nothing
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(pymupdf.Rect(72, 60, 540, 160), PROSE, fontsize=10)
    page.insert_image(pymupdf.Rect(150, 200, 460, 400), stream=_png((310, 200)))
    page.insert_textbox(pymupdf.Rect(72, 410, 540, 440),
                        "Figure 1: The training pipeline of our method.", fontsize=10)
    doc.save(pdf)
    out = locate_latex_figures(pdf, [_fig("fig-001", "The training pipeline of our method.")], {})
    page_no, box = out["fig-001"]
    assert page_no == 1 and _close(box, (150, 200, 460, 400))


def test_caption_above_figure(tmp_path: Path) -> None:
    pdf = tmp_path / "p.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(pymupdf.Rect(72, 100, 540, 130),
                        "Figure 2: Results across the four task groups.", fontsize=10)
    page.draw_rect(pymupdf.Rect(100, 140, 500, 330), color=(0, 0, 0))
    page.draw_line(pymupdf.Point(120, 300), pymupdf.Point(480, 160))
    page.insert_textbox(pymupdf.Rect(72, 400, 540, 520), PROSE, fontsize=10)
    doc.save(pdf)
    out = locate_latex_figures(pdf, [_fig("fig-002", "Results across the four task groups.")], {})
    assert out["fig-002"][0] == 0
    assert _close(out["fig-002"][1], (100, 140, 500, 330))


def test_two_figures_on_one_page_get_separate_boxes(tmp_path: Path) -> None:
    pdf = tmp_path / "p.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(pymupdf.Rect(100, 60, 500, 220), stream=_png((400, 160)))
    page.insert_textbox(pymupdf.Rect(72, 225, 540, 250), "Figure 1: First one shown here.",
                        fontsize=10)
    page.insert_textbox(pymupdf.Rect(72, 260, 540, 360), PROSE, fontsize=10)
    page.insert_image(pymupdf.Rect(120, 380, 480, 600), stream=_png((360, 220)))
    page.insert_textbox(pymupdf.Rect(72, 605, 540, 630), "Figure 2: Second one shown here.",
                        fontsize=10)
    doc.save(pdf)
    out = locate_latex_figures(pdf, [_fig("fig-001", "First one shown here."),
                                     _fig("fig-002", "Second one shown here.")], {})
    assert _close(out["fig-001"][1], (100, 60, 500, 220))
    assert _close(out["fig-002"][1], (120, 380, 480, 600))


def test_two_column_figures_stay_in_their_column(tmp_path: Path) -> None:
    pdf = tmp_path / "p.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(pymupdf.Rect(60, 100, 290, 250), stream=_png((230, 150)))
    page.insert_textbox(pymupdf.Rect(60, 255, 290, 280), "Figure 3: Left column plot.",
                        fontsize=9)
    page.insert_image(pymupdf.Rect(322, 120, 552, 230), stream=_png((230, 110)))
    page.insert_textbox(pymupdf.Rect(322, 235, 552, 260), "Figure 4: Right column plot.",
                        fontsize=9)
    doc.save(pdf)
    out = locate_latex_figures(pdf, [_fig("fig-003", "Left column plot."),
                                     _fig("fig-004", "Right column plot.")], {})
    assert _close(out["fig-003"][1], (60, 100, 290, 250))
    assert _close(out["fig-004"][1], (322, 120, 552, 230))


def test_caption_with_macro_holes_is_still_found(tmp_path: Path) -> None:
    """The asset caption is cleaned LaTeX: macros leave holes ("Overview of .
    is ...") and the PDF may use ligatures. Words matched in order find it."""
    pdf = tmp_path / "p.pdf"
    doc = pymupdf.open()
    doc.new_page()
    page = doc.new_page(width=612, height=792)
    page.insert_image(pymupdf.Rect(150, 200, 460, 400), stream=_png((310, 200)))
    page.insert_textbox(pymupdf.Rect(72, 410, 540, 470),
                        "Figure 1: Overview of MolmoAct. MolmoAct is a fully open action "
                        "reasoning model for real-world deployment.", fontsize=10)
    doc.save(pdf)
    caption = "Overview of . is a fully open action reasoning model for real-world deployment."
    out = locate_latex_figures(pdf, [_fig("fig-001", caption)], {})
    assert out["fig-001"][0] == 1 and out["fig-001"][1] is not None
    assert _close(out["fig-001"][1], (150, 200, 460, 400))


def test_caption_found_without_graphics_keeps_the_page(tmp_path: Path) -> None:
    pdf = tmp_path / "p.pdf"
    doc = pymupdf.open()
    doc.new_page()
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(pymupdf.Rect(72, 410, 540, 440),
                        "Figure 1: A figure drawn only with text labels.", fontsize=10)
    doc.save(pdf)
    out = locate_latex_figures(pdf, [_fig("fig-001", "A figure drawn only with text labels.")],
                               {})
    assert out == {"fig-001": (1, None)}


def test_captionless_figure_matched_by_image_size(tmp_path: Path) -> None:
    pdf = tmp_path / "p.pdf"
    doc = pymupdf.open()
    doc.new_page()
    page = doc.new_page(width=612, height=792)
    page.insert_image(pymupdf.Rect(200, 300, 400, 420), stream=_png((173, 97)),
                      keep_proportion=False)
    doc.save(pdf)
    out = locate_latex_figures(pdf, [_fig("fig-005", "")], {"fig-005": (173, 97)})
    assert out["fig-005"][0] == 1 and _close(out["fig-005"][1], (200, 300, 400, 420))


def test_unlocatable_figure_is_omitted(tmp_path: Path) -> None:
    pdf = tmp_path / "p.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(pdf)
    out = locate_latex_figures(pdf, [_fig("fig-009", "A caption that is not in the PDF."),
                                     _fig("fig-010", "")], {"fig-010": (11, 13)})
    assert out == {}


def test_place_latex_figures_updates_the_asset(tmp_path: Path) -> None:
    from paperhub.pipelines.figure_pages import place_latex_figures
    from paperhub.pipelines.page_pdf import COMPILED_PDF
    from paperhub.pipelines.paper_asset import PaperAsset, paper_asset_dir, write_paper_asset

    asset_dir = paper_asset_dir(tmp_path)
    (asset_dir / "figures").mkdir(parents=True)
    Image.new("RGB", (310, 200)).save(asset_dir / "figures" / "fig-001.png")
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(pymupdf.Rect(150, 200, 460, 400), stream=_png((310, 200)))
    page.insert_textbox(pymupdf.Rect(72, 410, 540, 440),
                        "Figure 1: The training pipeline of our method.", fontsize=10)
    doc.save(asset_dir / COMPILED_PDF)
    fig = FigureAsset("fig-001", "The training pipeline of our method.", None, None,
                      "figures/fig-001.png", env_index=0)
    write_paper_asset(PaperAsset(figures=[fig]), tmp_path)
    placed = place_latex_figures(tmp_path)
    assert placed is not None
    [f] = placed.figures
    assert f.page == 0 and f.bbox is not None and _close(f.bbox, (150, 200, 460, 400))
    assert f.env_index == 0 and f.caption == fig.caption


def test_image_size_match_only_when_the_size_is_unique(tmp_path: Path) -> None:
    """Figures exported at one figsize share a pixel size: a size that occurs
    twice in the PDF, or that two figures have, places nothing (the agent then
    sees the figure image) rather than the wrong page."""
    pdf = tmp_path / "p.pdf"
    doc = pymupdf.open()
    for y in (100, 400):
        page = doc.new_page(width=612, height=792)
        page.insert_image(pymupdf.Rect(100, y, 300, y + 120), stream=_png((173, 97)),
                          keep_proportion=False)
    page.insert_image(pymupdf.Rect(100, 100, 300, 220), stream=_png((50, 40)),
                      keep_proportion=False)
    doc.save(pdf)
    out = locate_latex_figures(
        pdf, [_fig("fig-001", ""), _fig("fig-002", ""), _fig("fig-003", "")],
        {"fig-001": (173, 97), "fig-002": (50, 40), "fig-003": (50, 40)})
    assert out == {}



def test_caption_printed_without_spaces_is_found(tmp_path: Path) -> None:
    """Some PDF fonts drop the spaces of a bold caption run
    ('Visualdatadoesnotcompetewithtext.')."""
    pdf = tmp_path / "p.pdf"
    doc = pymupdf.open()
    doc.new_page()
    page = doc.new_page(width=612, height=792)
    page.insert_image(pymupdf.Rect(100, 100, 400, 300), stream=_png((300, 200)),
                      keep_proportion=False)
    page.insert_text((100, 320), "Figure5 Visualdatadoesnotcompetewithtext. Text + Video",
                     fontsize=9)
    page.insert_text((100, 332), "matches the text-only PPL on DCLM.", fontsize=9)
    doc.save(pdf)
    cap = "Visual data does not compete with text. Text + Video matches the text-only PPL on DCLM."
    out = locate_latex_figures(pdf, [_fig("fig-004", cap)], {})
    assert out["fig-004"][0] == 1
    assert out["fig-004"][1] is not None and _close(out["fig-004"][1], (100, 100, 400, 300))


def test_short_caption_only_after_a_figure_label(tmp_path: Path) -> None:
    """A one- or two-word caption is accepted only where it follows a printed
    'Figure N' / 'Fig. N' label (body text mentions it too)."""
    pdf = tmp_path / "p.pdf"
    doc = pymupdf.open()
    body = doc.new_page(width=612, height=792)
    body.insert_text((72, 100), "The Language Model (ORLM) based on fine-tuning is introduced.",
                     fontsize=10)
    body.insert_text((72, 130), "We compare Applications Percentage across fields.", fontsize=10)
    fig = doc.new_page(width=612, height=792)
    fig.insert_image(pymupdf.Rect(100, 100, 400, 300), stream=_png((300, 200)),
                     keep_proportion=False)
    fig.insert_text((100, 320), "Figure 2: ORLM (huang2024orlm)", fontsize=9)
    fig2 = doc.new_page(width=612, height=792)
    fig2.insert_image(pymupdf.Rect(100, 400, 400, 600), stream=_png((310, 210)),
                      keep_proportion=False)
    fig2.insert_text((100, 620), "Figure 9", fontsize=9)
    fig2.insert_text((100, 632), "Applications Percentage", fontsize=9)
    doc.save(pdf)
    out = locate_latex_figures(
        pdf, [_fig("fig-002", "ORLM"), _fig("fig-010", "Applications Percentage")], {})
    assert out["fig-002"][0] == 1 and out["fig-010"][0] == 2
    assert out["fig-010"][1] is not None and _close(out["fig-010"][1], (100, 400, 400, 600))



def test_drawings_count_only_where_their_clip_lets_them_show(tmp_path: Path) -> None:
    """Plot bars are often drawn far wider than shown and trimmed by a clip
    path; a raw drawing rect then reaches off the page (paper 74: x0=-448)."""
    pdf = tmp_path / "p.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    # clip to x 100..300, then fill a bar from x -400 to 280
    doc.update_stream(xref, b"q 100 492 200 200 re W n 0 0 1 rg -400 600 680 20 re f "
                            b"0 1 0 rg 120 520 100 40 re f Q")
    page.set_contents(xref)
    page.insert_text((100, 320), "Figure 1: Bars drawn wider than their clip region are trimmed.",
                     fontsize=9)
    doc.save(pdf)
    out = locate_latex_figures(
        pdf, [_fig("fig-001", "Bars drawn wider than their clip region are trimmed.")], {})
    box = out["fig-001"][1]
    assert box is not None and box[0] >= 99 and box[2] <= 301



def test_white_background_fills_are_not_figure_content(tmp_path: Path) -> None:
    """A white fill behind a page region (clipped smaller than the page) is a
    background, not part of the figure (paper 16: the box reached y=0)."""
    pdf = tmp_path / "p.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    # white fill over the top 300pt of the page, then a blue plot at y 120..280
    doc.update_stream(xref, b"q 0 492 612 300 re W n 1 1 1 rg 0 0 612 792 re f Q "
                            b"0 0 1 rg 120 512 300 160 re f")
    page.set_contents(xref)
    page.insert_text((100, 300), "Figure 1: A plot drawn over a white background region.",
                     fontsize=9)
    doc.save(pdf)
    out = locate_latex_figures(
        pdf, [_fig("fig-001", "A plot drawn over a white background region.")], {})
    box = out["fig-001"][1]
    assert box is not None and _close(box, (120, 120, 420, 280))



def test_a_page_covering_image_is_background_even_past_the_page_edge(tmp_path: Path) -> None:
    """An image larger than the page (a background, partly off the page) is
    not figure content (paper 16: clipping it to the page made it count)."""
    pdf = tmp_path / "p.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(pymupdf.Rect(-20, -40, 640, 830), stream=_png((60, 80)),
                      keep_proportion=False, overlay=False)
    page.insert_image(pymupdf.Rect(100, 100, 400, 300), stream=_png((300, 200)),
                      keep_proportion=False)
    page.insert_text((100, 320), "Figure 1: A plot on a page with a full-bleed background image.",
                     fontsize=9)
    doc.save(pdf)
    out = locate_latex_figures(
        pdf, [_fig("fig-001", "A plot on a page with a full-bleed background image.")], {})
    box = out["fig-001"][1]
    assert box is not None and _close(box, (100, 100, 400, 300))



def test_an_image_hanging_off_the_page_top_does_not_stretch_the_box(tmp_path: Path) -> None:
    """Paper 16: an image placed at y=-19..98 (trimmed in the PDF by a clip we
    cannot see on images) must not pull the figure's box up to the page top."""
    pdf = tmp_path / "p.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(pymupdf.Rect(137, -19, 509, 98), stream=_png((120, 40)),
                      keep_proportion=False)
    page.insert_image(pymupdf.Rect(61, 112, 546, 200), stream=_png((300, 60)),
                      keep_proportion=False)
    page.insert_text((110, 220), "Figure 7: Real-world evaluation results on three robots.",
                     fontsize=9)
    doc.save(pdf)
    out = locate_latex_figures(
        pdf, [_fig("fig-006", "Real-world evaluation results on three robots.")], {})
    box = out["fig-006"][1]
    assert box is not None and box[1] >= 100
