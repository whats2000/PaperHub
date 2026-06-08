import base64
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from paperhub.pipelines.renderer import (
    _orphaned_env_ends,
    _pandoc_hostile_def_spans,
    _unclosed_braces,
    render_html,
)


def test_unclosed_braces_counts_stray_open() -> None:
    assert _unclosed_braces("balanced {a} {b}") == 0
    assert _unclosed_braces(r"on \owt{. Additionally") == 1  # the arXiv:2406.07524 typo
    assert _unclosed_braces("a {b {c}") == 1
    # Escaped braces and comments must not count.
    assert _unclosed_braces(r"literal \{ and \}") == 0
    assert _unclosed_braces("text % a stray { in a comment\nmore") == 0


def test_orphaned_env_ends_detects_commented_begin() -> None:
    # \begin commented out but \end live (arXiv:2501.02902) -> orphaned \end.
    tex = (
        "% \\begin{table}\n\\caption{x}\n\\begin{tabular}{cc}a & b\\\\\\end{tabular}\n"
        "\\end{table}\n"
    )
    orphans = _orphaned_env_ends(tex)
    assert len(orphans) == 1
    s, e = orphans[0]
    assert tex[s:e] == "\\end{table}"


def test_orphaned_env_ends_none_when_balanced() -> None:
    tex = "\\begin{table}\\begin{tabular}{cc}a & b\\\\\\end{tabular}\\end{table}"
    assert _orphaned_env_ends(tex) == []
    # A commented \end is ignored (not orphaned), and so is its commented \begin.
    assert _orphaned_env_ends("% \\begin{figure}\n% \\end{figure}\n") == []


def test_pandoc_hostile_def_spans_finds_newcolumntype() -> None:
    # \newcolumntype{P}[1]{...#1...} in the body makes pandoc abort on the #1
    # (arXiv:2404.07214). The span must cover the whole definition.
    tex = "before \\newcolumntype{P}[1]{>{\\centering\\arraybackslash}p{#1}} after"
    spans = _pandoc_hostile_def_spans(tex)
    assert len(spans) == 1
    s, e = spans[0]
    assert tex[s:e] == "\\newcolumntype{P}[1]{>{\\centering\\arraybackslash}p{#1}}"


def test_pandoc_hostile_def_spans_finds_newtcolorbox() -> None:
    # \newtcolorbox{name}[1][]{... title=#1 ...} (arXiv:2603.03276).
    tex = (
        "x\n\\newtcolorbox{promptbox}[1][]{\n  colback=gray!5,\n  title=#1,\n"
        "  boxrule=0.5pt,\n}\n\\begin{promptbox}[T]y\\end{promptbox}\n"
    )
    spans = _pandoc_hostile_def_spans(tex)
    assert len(spans) == 1
    s, e = spans[0]
    assert tex[s:e].startswith("\\newtcolorbox{promptbox}[1][]{")
    assert tex[s:e].endswith("}")
    # The usage that follows the definition is left intact (its content renders).
    assert "\\begin{promptbox}" in tex[e:]


def test_pandoc_hostile_def_spans_ignores_commented_and_newcommand() -> None:
    # A commented definition is invisible to pandoc — don't touch it.
    assert _pandoc_hostile_def_spans("% \\newcolumntype{P}[1]{p{#1}}\n") == []
    # \newcommand/\def are understood by pandoc — must NOT be stripped.
    assert _pandoc_hostile_def_spans("\\newcommand{\\x}[1]{y#1}") == []
    assert _pandoc_hostile_def_spans("\\tcbsetfoo{a}") == []  # not a real hostile cmd


def test_render_latex_strips_hostile_def_and_retries_pandoc(tmp_path: Path) -> None:
    """A body-level `\\newcolumntype{P}[1]{...#1...}` (arXiv:2404.07214) makes
    pandoc abort the whole parse on the `#1`. render_html must strip the hostile
    definition + retry so it produces structured HTML, not the <pre> dump."""
    if shutil.which("pandoc") is None:
        pytest.skip("pandoc binary not installed")
    src = tmp_path / "source.render.tex"
    src.write_text(
        "\\section{Introduction}\nText before.\n\n"
        "\\newcolumntype{P}[1]{>{\\centering\\arraybackslash}p{#1}}\n\n"
        "\\section{Method}\nText after.\n",
        encoding="utf-8",
    )
    out = tmp_path / "source.html"
    render_html(source=src, kind="latex", out_path=out)
    html = out.read_text(encoding="utf-8")
    assert "<pre" not in html
    assert "<h1" in html or "<h2" in html
    assert "Introduction" in html and "Method" in html


def test_render_latex_repairs_orphaned_env_end_and_retries_pandoc(tmp_path: Path) -> None:
    """A commented `% \\begin{table}` with a live `\\end{table}` (arXiv:2501.02902,
    an author typo) makes pandoc abort the whole parse. render_html must drop the
    orphaned `\\end` + retry so it produces structured HTML, not the <pre> dump."""
    if shutil.which("pandoc") is None:
        pytest.skip("pandoc binary not installed")
    src = tmp_path / "source.render.tex"
    src.write_text(
        "\\section{Introduction}\nText before the table.\n\n"
        "% \\begin{table}[t]\n\\centering\n\\caption{Spec}\n"
        "\\begin{tabular}{@{}lr@{}}\\toprule a & b\\\\ \\bottomrule\\end{tabular}\n"
        "\\end{table}\n\n"
        "\\section{Method}\nText after the table.\n",
        encoding="utf-8",
    )
    out = tmp_path / "source.html"
    render_html(source=src, kind="latex", out_path=out)
    html = out.read_text(encoding="utf-8")
    assert "<h1" in html or "<h2" in html
    assert "<pre" not in html
    assert "Introduction" in html and "Method" in html


def test_render_latex_balances_stray_brace_and_retries_pandoc(tmp_path: Path) -> None:
    """An author-typo unclosed brace makes pandoc reject the whole document
    (arXiv:2406.07524's `\\owt{`). render_html must re-balance + retry pandoc so
    it produces real structured HTML, not the plain-text <pre> fallback."""
    if shutil.which("pandoc") is None:
        pytest.skip("pandoc binary not installed")
    src = tmp_path / "source.render.tex"
    src.write_text(
        "\\section{Introduction}\n"
        "We evaluate perplexity on \\owt{. Additionally we report results.\n\n"
        "\\section{Method}\nThe model denoises masked tokens.\n",
        encoding="utf-8",
    )
    out = tmp_path / "source.html"
    render_html(source=src, kind="latex", out_path=out)
    html = out.read_text(encoding="utf-8")
    # pandoc succeeded on the balanced retry → heading tags, NOT a <pre> dump.
    assert "<h1" in html or "<h2" in html
    assert "<pre" not in html
    assert "Introduction" in html and "Method" in html


def test_render_preserves_comment_line_in_math(tmp_path: Path) -> None:
    """A long %-comment line inside a math environment must stay fully commented
    (arXiv:1706.03762's MultiHead block). Default pandoc wrapping split it so
    only the first fragment was commented and an invalid double-subscript
    leaked into live math. --wrap=preserve keeps the comment on one line."""
    if shutil.which("pandoc") is None:
        pytest.skip("pandoc binary not installed")
    src = tmp_path / "source.render.tex"
    src.write_text(
        "\\begin{align*}\n"
        "    \\mathrm{MultiHead}(Q,K,V) &= \\mathrm{Concat}(\\mathrm{head_1})W^O\\\\\n"
        "%    \\mathrm{where} \\mathrm{head_i} &= \\mathrm{Attention}"
        "(QW_Q_i^{abcdefghij}, KW_K_i^{abcdefghij}, VW^V_i^{abcdefghij})\\\\\n"
        "    \\text{where}~\\mathrm{head_i} &= \\mathrm{Attention}(QW^Q_i)\\\\\n"
        "\\end{align*}\n",
        encoding="utf-8",
    )
    out = tmp_path / "source.html"
    render_html(source=src, kind="latex", out_path=out)
    html = out.read_text(encoding="utf-8")
    # The commented double-subscript fragment, if present at all, must remain on
    # a line that is still a LaTeX comment (begins with %) — never un-commented.
    for line in html.splitlines():
        if "QW_Q_i" in line:
            assert line.lstrip().startswith("%"), (
                f"commented math leaked into live math: {line!r}"
            )


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


def test_inject_mathjax_macros_inserts_config_before_loader(tmp_path: Path) -> None:
    """The window.MathJax config must land BEFORE pandoc's MathJax loader script
    so it is in place when MathJax initializes."""
    from paperhub.pipelines.renderer import _inject_mathjax_macros

    out = tmp_path / "out.html"
    out.write_text(
        "<head>\n"
        '  <script src="https://polyfill.io/v3/polyfill.min.js"></script>\n'
        "  <script\n"
        '  src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml-full.js"\n'
        '  type="text/javascript"></script>\n'
        "</head><body>x</body>",
        encoding="utf-8",
    )
    _inject_mathjax_macros(out, {"Ls": r"\mathcal{L}"})
    html = out.read_text(encoding="utf-8")
    cfg = html.index("window.MathJax")
    loader = html.index("tex-chtml-full.js")
    assert cfg < loader, "config must precede the MathJax loader"
    # Author macro + curated package macro both present.
    assert r"\mathcal{L}" in html
    assert "mathbbm" in html
    # The polyfill script (also a <script>) must not be the injection target.
    assert html.index("polyfill") < cfg


def test_inject_mathjax_macros_noop_without_loader(tmp_path: Path) -> None:
    """A render with no MathJax loader (e.g. a fallback path) is left untouched."""
    from paperhub.pipelines.renderer import _inject_mathjax_macros

    out = tmp_path / "out.html"
    original = "<head></head><body><pre>raw latex</pre></body>"
    out.write_text(original, encoding="utf-8")
    _inject_mathjax_macros(out, {"Ls": r"\mathcal{L}"})
    assert out.read_text(encoding="utf-8") == original


_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def test_externalize_local_images_rewrites_to_relative_asset_path(tmp_path: Path) -> None:
    """Figures must NOT be base64-inlined (a 70MB-HTML OOM'd the Citation Canvas
    iframe — arxiv:2605.02881 had 70MB of figures). Instead each local raster
    <img src> is rewritten to a relative ``asset/<path>`` URL the iframe resolves
    against its backend src; the figure bytes stay on disk and are served lazily.
    Remote, data:, and missing srcs are left untouched."""
    from paperhub.pipelines import renderer

    # html lives at tmp_path/out.html; figures under tmp_path/source/ — mirrors
    # production (html_path = cache_dir/source.html, resource_dir = cache_dir/source).
    res_dir = tmp_path / "source"
    (res_dir / "figs").mkdir(parents=True)
    (res_dir / "figs" / "pic.png").write_bytes(base64.b64decode(_TINY_PNG_B64))
    html = tmp_path / "out.html"
    html.write_text(
        '<html><body><img src="figs/pic.png" />'
        '<img src="https://x/y.png" />'
        '<img src="data:image/png;base64,AAAA" />'
        '<img src="figs/missing.png" /></body></html>',
        encoding="utf-8",
    )
    renderer._externalize_local_images(html, res_dir)
    out = html.read_text(encoding="utf-8")
    assert 'src="asset/source/figs/pic.png"' in out  # rewritten to served path
    assert "data:image/png;base64,iVBOR" not in out  # NEVER base64-inlined
    assert 'src="https://x/y.png"' in out  # remote left alone
    assert 'src="data:image/png;base64,AAAA"' in out  # pre-existing data: untouched
    assert 'src="figs/missing.png"' in out  # missing file left as-is
    assert (res_dir / "figs" / "pic.png").is_file()  # bytes stay on disk


def test_externalize_data_uri_images_writes_files_and_rewrites(tmp_path: Path) -> None:
    """PyMuPDF's get_text('html') inlines page images as base64 data URIs (the
    PDF-render counterpart of the figure-bloat bug). Extract each to a file under
    out_dir and rewrite the src to a relative asset/ URL so the PDF-render HTML
    is no longer a multi-MB inline blob."""
    from paperhub.pipelines import renderer

    html = (
        "<html><body>"
        f'<img src="data:image/png;base64,{_TINY_PNG_B64}"/>'
        f'<img src="data:image/png;base64,{_TINY_PNG_B64}"/>'
        "</body></html>"
    )
    out_dir = tmp_path / "pdf_assets"
    new_html = renderer._externalize_data_uri_images(
        html, out_dir=out_dir, html_dir=tmp_path
    )
    assert "data:image" not in new_html, "data URIs must be extracted, not kept inline"
    srcs = re.findall(r'src="(asset/[^"]+)"', new_html)
    assert len(srcs) == 2, "both images rewritten to relative asset paths"
    for rel in srcs:
        on_disk = tmp_path / rel[len("asset/") :]
        assert on_disk.is_file(), f"extracted image {rel} should exist on disk"


def test_render_html_externalizes_figure_not_inline(tmp_path: Path) -> None:
    """End-to-end: a flattened .tex referencing an image must produce HTML that
    references the figure by a relative asset/ URL, NOT as an inline data: URI
    (which is what OOM'd the canvas)."""
    if shutil.which("pandoc") is None:
        pytest.skip("pandoc binary not installed")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    res_dir = cache_dir / "source"  # under cache dir, like production
    (res_dir / "figs").mkdir(parents=True)
    (res_dir / "figs" / "pic.png").write_bytes(base64.b64decode(_TINY_PNG_B64))
    tex = cache_dir / "source.flattened.tex"
    tex.write_text(
        r"\documentclass{article}\usepackage{graphicx}"
        r"\begin{document}\includegraphics{figs/pic.png}"
        r"\[ x_t = (1-t)x_0 + t x_1 \]"
        r"\end{document}",
        encoding="utf-8",
    )
    out = cache_dir / "source.html"
    render_html(source=tex, kind="latex", out_path=out, resource_dir=res_dir)
    html = out.read_text(encoding="utf-8")
    assert "data:image" not in html, "figure must NOT be inlined as a data: URI"
    assert "asset/source/figs/pic.png" in html, "figure rewritten to served relative URL"
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
