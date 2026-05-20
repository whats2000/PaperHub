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
