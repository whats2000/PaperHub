import base64
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from paperhub.pipelines.renderer import render_html


def test_render_pdf_uses_pymupdf(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "papers" / "sample.pdf"
    out = tmp_path / "source.html"
    render_html(source=fixture, kind="pdf", out_path=out)
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>") or html.startswith("<html")
    assert "Tiny Test Paper" in html


def test_render_latex_with_pandoc_when_available(tmp_path: Path) -> None:
    if shutil.which("pandoc") is None:
        pytest.skip("pandoc binary not installed")
    fixture = Path(__file__).parent / "fixtures" / "papers" / "arxiv_sample" / "main.tex"
    out = tmp_path / "source.html"
    render_html(source=fixture, kind="latex", out_path=out)
    html = out.read_text(encoding="utf-8")
    assert "<h1" in html or "<h2" in html  # pandoc emits heading tags
    assert "Mixture-of-Experts" in html


def test_render_latex_falls_back_when_pandoc_missing(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "papers" / "arxiv_sample" / "main.tex"
    out = tmp_path / "source.html"
    with patch("paperhub.pipelines.renderer.shutil.which", return_value=None):
        render_html(source=fixture, kind="latex", out_path=out)
    html = out.read_text(encoding="utf-8")
    # pylatexenc fallback gives plainer output but should contain the body text.
    assert "Mixture-of-Experts" in html


def test_render_latex_falls_back_when_pandoc_exits_nonzero(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """pandoc may be installed but exit non-zero on idiosyncratic LaTeX.
    The renderer must fall back to pylatexenc rather than propagating
    CalledProcessError up the stack."""
    fixture = Path(__file__).parent / "fixtures" / "papers" / "arxiv_sample" / "main.tex"
    out = tmp_path / "source.html"
    err = subprocess.CalledProcessError(
        returncode=251,
        cmd=["pandoc", "--from", "latex"],
        stderr="latex error: idiosyncratic preamble",
    )
    with (
        patch("paperhub.pipelines.renderer._render_latex_pandoc", side_effect=err),
        caplog.at_level("WARNING", logger="paperhub.pipelines.renderer"),
    ):
        render_html(source=fixture, kind="latex", out_path=out)

    html = out.read_text(encoding="utf-8")
    assert "Mixture-of-Experts" in html  # pylatexenc successfully produced content
    assert any("pandoc failed" in r.message for r in caplog.records), (
        "expected a WARNING log on pandoc-exit-nonzero fallback"
    )


def test_render_latex_falls_back_to_raw_envelope_when_pylatexenc_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """When pandoc fails AND pylatexenc crashes, render_html must still
    produce a readable HTML file (raw-escaped LaTeX in a <pre>) and never
    propagate the exception.

    Bug from arxiv:2503.05641 (iclr2026_conference.tex): pandoc exit 252,
    then pylatexenc IndexError deep in macro handler. POST /papers 500'd
    and wasted 80+s of upstream download/extract work.
    """
    fixture = tmp_path / "hostile.tex"
    fixture.write_text(
        r"\documentclass{article}\begin{document}"
        r"\some_macro_that_will_explode{}"
        r"\end{document}",
        encoding="utf-8",
    )
    out = tmp_path / "source.html"

    with (
        patch(
            "paperhub.pipelines.renderer._render_latex_pandoc",
            side_effect=subprocess.CalledProcessError(
                returncode=252,
                cmd=["pandoc"],
                stderr="parse failed",
            ),
        ),
        patch(
            "paperhub.pipelines.renderer._render_latex_pylatexenc",
            side_effect=IndexError("list index out of range"),
        ),
        caplog.at_level("WARNING", logger="paperhub.pipelines.renderer"),
    ):
        render_html(source=fixture, kind="latex", out_path=out)

    html_text = out.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html_text
    assert "<pre" in html_text
    # The raw LaTeX source is visible, HTML-escaped
    assert "\\some_macro_that_will_explode" in html_text
    # pylatexenc-failure warning was logged
    assert any("pylatexenc failed" in r.message for r in caplog.records)


def test_render_latex_falls_back_when_pandoc_times_out(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """pandoc can HANG (not just exit non-zero) on pathological LaTeX. With no
    subprocess timeout this parked an entire /chat ingest indefinitely until the
    worker OOM-crashed (arxiv:2410.12557). render_html must catch
    subprocess.TimeoutExpired and fall back, never propagate/hang."""
    fixture = tmp_path / "slow.tex"
    fixture.write_text(
        r"\documentclass{article}\begin{document}Hello world\end{document}",
        encoding="utf-8",
    )
    out = tmp_path / "source.html"
    timeout_err = subprocess.TimeoutExpired(cmd=["pandoc"], timeout=60)
    with (
        patch("paperhub.pipelines.renderer._render_latex_pandoc", side_effect=timeout_err),
        caplog.at_level("WARNING", logger="paperhub.pipelines.renderer"),
    ):
        render_html(source=fixture, kind="latex", out_path=out)
    html = out.read_text(encoding="utf-8")
    assert "Hello world" in html  # pylatexenc fallback produced content
    assert any("pandoc" in r.message.lower() for r in caplog.records)


def test_render_latex_pandoc_passes_subprocess_timeout(tmp_path: Path) -> None:
    """_render_latex_pandoc must bound the pandoc subprocess with a timeout so a
    hanging pandoc cannot park ingest forever."""
    from paperhub.pipelines import renderer

    fixture = tmp_path / "main.tex"
    fixture.write_text(r"\documentclass{article}\begin{document}x\end{document}", encoding="utf-8")
    out = tmp_path / "out.html"
    with patch("paperhub.pipelines.renderer.subprocess.run") as run_mock:
        renderer._render_latex_pandoc(fixture, out)
    assert run_mock.call_args.kwargs.get("timeout"), "pandoc subprocess must set a timeout"


def test_render_latex_pandoc_uses_mathjax_and_resource_path_not_embed(tmp_path: Path) -> None:
    """Math must render via MathJax (pandoc's built-in conversion can't handle
    multi-line envs like \\begin{aligned}). We keep the MathJax <script> EXTERNAL
    (no --embed-resources, which would fetch+inline ~1.3MB from the CDN at ingest
    and slow every paper); figures are inlined separately. --resource-path still
    points pandoc at the source/ subtree."""
    from paperhub.pipelines import renderer

    tex = tmp_path / "source.flattened.tex"
    tex.write_text(r"\documentclass{article}\begin{document}x\end{document}", encoding="utf-8")
    res_dir = tmp_path / "source"
    res_dir.mkdir()
    out = tmp_path / "out.html"
    with patch("paperhub.pipelines.renderer.subprocess.run") as run_mock:
        renderer._render_latex_pandoc(tex, out, resource_dir=res_dir)
    argv = run_mock.call_args.args[0]
    assert "--mathjax" in argv
    assert "--resource-path" in argv
    assert str(res_dir) in argv
    assert "--embed-resources" not in argv  # external MathJax, fast ingest


def test_inline_local_images_embeds_relative_img_as_data_uri(tmp_path: Path) -> None:
    """Figures stay external in pandoc output (no --embed-resources); we inline
    the local raster <img> refs ourselves so the artefact is self-contained
    without fetching MathJax."""
    from paperhub.pipelines import renderer

    res_dir = tmp_path / "source"
    (res_dir / "figs").mkdir(parents=True)
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    (res_dir / "figs" / "pic.png").write_bytes(png)
    html = tmp_path / "out.html"
    html.write_text(
        '<html><body><img src="figs/pic.png" />'
        '<img src="https://x/y.png" /><img src="data:image/png;base64,AAAA" /></body></html>',
        encoding="utf-8",
    )
    renderer._inline_local_images(html, res_dir)
    out = html.read_text(encoding="utf-8")
    assert 'src="data:image/png;base64,' in out  # local figure inlined
    assert 'src="https://x/y.png"' in out  # remote left alone
    assert out.count("data:image/png;base64,AAAA") == 1  # pre-existing data: untouched


def test_render_html_embeds_image_from_resource_dir(tmp_path: Path) -> None:
    """End-to-end: a flattened .tex in one dir referencing an image that lives
    in a separate resource dir must produce HTML with the image embedded as a
    data: URI (so the Citation Canvas renders figures regardless of serving dir)."""
    if shutil.which("pandoc") is None:
        pytest.skip("pandoc binary not installed")
    tex_dir = tmp_path / "cache"
    tex_dir.mkdir()
    res_dir = tmp_path / "source"
    (res_dir / "figs").mkdir(parents=True)
    # Minimal valid 1x1 transparent PNG.
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    (res_dir / "figs" / "pic.png").write_bytes(png)
    tex = tex_dir / "source.flattened.tex"
    tex.write_text(
        r"\documentclass{article}\usepackage{graphicx}"
        r"\begin{document}\includegraphics{figs/pic.png}"
        r"\[ x_t = (1-t)x_0 + t x_1 \]"
        r"\end{document}",
        encoding="utf-8",
    )
    out = tex_dir / "source.html"
    render_html(source=tex, kind="latex", out_path=out, resource_dir=res_dir)
    html = out.read_text(encoding="utf-8")
    assert "data:image" in html, "figure should be embedded as a data: URI"
    # MathJax stays an external CDN <script> (not fetched+inlined): keeps the
    # HTML lean and ingest fast.
    assert "mathjax" in html.lower(), "MathJax script should be referenced"
    assert html.count("data:application/javascript") == 0, "MathJax must NOT be embedded"


def test_render_latex_pandoc_resolves_input_relative_to_source_dir(tmp_path: Path) -> None:
    """Pandoc must resolve \\input{...} relative to the .tex file's own directory,
    not the process cwd. Otherwise multi-file arxiv sources break silently."""
    if shutil.which("pandoc") is None:
        pytest.skip("pandoc binary not installed")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.tex").write_text(
        r"\documentclass{article}\begin{document}"
        r"\input{intro}\end{document}",
        encoding="utf-8",
    )
    (src_dir / "intro.tex").write_text(
        "This is the included introduction text from a separate file.",
        encoding="utf-8",
    )
    out = tmp_path / "source.html"
    render_html(source=src_dir / "main.tex", kind="latex", out_path=out)
    html = out.read_text(encoding="utf-8")
    assert "included introduction text from a separate file" in html
