from pathlib import Path

import pymupdf

from paperhub.pipelines.figures import (
    rasterize_and_normalize_figures,
    strip_includegraphics_options,
)


def _make_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "figure")
    doc.save(str(path))
    doc.close()


def _make_png(path: Path) -> None:
    import base64

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNk"
            "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
    )


def test_rasterizes_pdf_and_rewrites_extensionless_ref(tmp_path: Path) -> None:
    """arxiv figures are commonly PDF, referenced without extension
    (\\includegraphics{figs/fig1}). Must rasterize fig1.pdf -> fig1.png AND
    rewrite the ref to the explicit .png so pandoc can embed it."""
    _make_pdf(tmp_path / "figs" / "fig1.pdf")
    tex = r"\includegraphics[width=\textwidth]{figs/fig1}"
    out = rasterize_and_normalize_figures(tex, tmp_path)
    assert "figs/fig1.png" in out
    assert (tmp_path / "figs" / "fig1.png").is_file()


def test_leaves_explicit_raster_ref_unchanged(tmp_path: Path) -> None:
    _make_png(tmp_path / "imgs" / "pic.png")
    tex = r"\includegraphics{imgs/pic.png}"
    out = rasterize_and_normalize_figures(tex, tmp_path)
    assert out == tex  # already a browser-renderable raster with explicit ext


def test_missing_figure_left_unchanged(tmp_path: Path) -> None:
    tex = r"\includegraphics{figs/ghost}"
    out = rasterize_and_normalize_figures(tex, tmp_path)
    assert out == tex  # nothing to resolve; don't fabricate


def test_extensionless_raster_gets_extension(tmp_path: Path) -> None:
    """\\includegraphics{imgs/pic} where only imgs/pic.png exists -> rewrite to
    the explicit extension so pandoc finds + embeds it."""
    _make_png(tmp_path / "imgs" / "pic.png")
    tex = r"\includegraphics{imgs/pic}"
    out = rasterize_and_normalize_figures(tex, tmp_path)
    assert "imgs/pic.png" in out


def test_strip_includegraphics_options_drops_width_hint() -> None:
    """LaTeX column-width hints (``[width=0.5\\textwidth]``) pass through
    pandoc as ``style="width:50.0%"`` on the rendered ``<img>``, which
    shrinks figures to half-width on the wide Citation Canvas. The
    pre-pandoc strip removes the bracket so the img inherits its
    natural size."""
    tex = (
        r"\includegraphics[width=0.5\textwidth]{f.png}"
        " text "
        r"\includegraphics[scale=0.8]{g.png}"
    )
    out = strip_includegraphics_options(tex)
    assert out == r"\includegraphics{f.png} text \includegraphics{g.png}"


def test_strip_includegraphics_options_leaves_optionless_calls_alone() -> None:
    """A ``\\includegraphics`` without any bracket is unchanged."""
    tex = r"\includegraphics{f.png} and \includegraphics{g.png}"
    assert strip_includegraphics_options(tex) == tex


def test_strip_includegraphics_options_handles_multi_option_bracket() -> None:
    """The bracket may contain several comma-separated options
    (``[width=1in,height=1.25in,clip,keepaspectratio]``) — strip the
    whole bracket as a unit, not just one option."""
    tex = (
        r"\includegraphics[width=1in,height=1.25in,clip,keepaspectratio]"
        r"{photo.jpg}"
    )
    assert strip_includegraphics_options(tex) == r"\includegraphics{photo.jpg}"
