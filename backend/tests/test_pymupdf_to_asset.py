from pathlib import Path

import pymupdf

from paperhub.pipelines.pymupdf_to_asset import pymupdf_to_asset


def _png_bytes() -> bytes:
    """A tiny solid-colour PNG via pymupdf Pixmap."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
    pix.set_rect(pix.irect, (200, 30, 30))
    return pix.tobytes("png")


def _make_pdf_with_image(path: Path) -> None:
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    page = doc.new_page()
    # A heading-like large-font line + body text.
    page.insert_text((72, 72), "Introduction", fontsize=20)
    page.insert_text((72, 110), "Some body text here.", fontsize=11)
    page.insert_image(pymupdf.Rect(72, 140, 172, 240), stream=_png_bytes())
    doc.save(path)
    doc.close()


def _make_pdf_no_image(path: Path) -> None:
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    page = doc.new_page()
    page.insert_text((72, 72), "Hello", fontsize=11)
    doc.save(path)
    doc.close()


def test_pymupdf_to_asset_extracts_figures(tmp_path: Path) -> None:
    pdf = tmp_path / "paper.pdf"
    _make_pdf_with_image(pdf)
    source_dir = tmp_path / "src"
    source_dir.mkdir()

    asset = pymupdf_to_asset(pdf, source_dir=source_dir)

    assert asset.equations == []
    assert isinstance(asset.sections, list)
    assert len(asset.figures) >= 1
    for fig in asset.figures:
        assert fig.caption == ""
        assert fig.section is None
        assert fig.abs_image_path(source_dir).exists()


def test_pymupdf_to_asset_no_images(tmp_path: Path) -> None:
    pdf = tmp_path / "paper.pdf"
    _make_pdf_no_image(pdf)
    source_dir = tmp_path / "src"
    source_dir.mkdir()

    asset = pymupdf_to_asset(pdf, source_dir=source_dir)

    assert asset.figures == []
    assert asset.equations == []
    assert isinstance(asset.sections, list)
