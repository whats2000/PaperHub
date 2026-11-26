"""Unit tests for the TikZ pre-rasteriser.

End-to-end compilation requires pdflatex on PATH, which CI machines don't
have. These tests exercise the deterministic glue — detection,
substitution, graceful no-op — by monkey-patching ``_compile_tikz_to_png``
so we can assert the rewrite behaviour without launching pdflatex.

The actual pdflatex compile + rasterise path is covered by the live
``:8000`` benchmark when present, not the unit suite.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from paperhub.pipelines import tikz_figures
from paperhub.pipelines.tikz_figures import rasterize_tikz_figures


@pytest.fixture(autouse=True)
def _fake_pdflatex_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend pdflatex is on PATH so the early-bailout no-op doesn't fire
    in CI where pdflatex isn't installed. Tests that want the no-op path
    override this locally."""
    monkeypatch.setattr(tikz_figures.shutil, "which", lambda _name: "/usr/bin/pdflatex")


def _fake_compile_success(
    env: str, *, preamble: str, body_prefix: str, png_path: Path, dpi: int,
    **_kw: object,
) -> bool:
    """Pretend the compile worked: touch the PNG file at the requested path."""
    png_path.parent.mkdir(parents=True, exist_ok=True)
    png_path.write_bytes(b"\x89PNG\r\n\x1a\n")  # PNG signature only — fine for test
    return True


def _fake_compile_failure(
    env: str, *, preamble: str, body_prefix: str, png_path: Path, dpi: int,
    **_kw: object,
) -> bool:
    """Pretend pdflatex failed (graceful fallback path)."""
    return False


def test_no_tikz_env_is_a_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Source without any TikZ env passes through unchanged and the
    compile path is never invoked."""
    called = False

    def _spy(*_args: object, **_kw: object) -> bool:
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(tikz_figures, "_compile_tikz_to_png", _spy)

    src = "\\section{Intro}\nSome body text without any TikZ.\n"
    out = rasterize_tikz_figures(src, preamble="", out_dir=tmp_path)
    assert out == src
    assert not called


def test_tikzpicture_env_is_replaced_with_includegraphics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``\\begin{tikzpicture}…\\end{tikzpicture}`` block is replaced with
    ``\\includegraphics{tikz-fig-001.png}`` after a successful compile."""
    monkeypatch.setattr(
        tikz_figures, "_compile_tikz_to_png", _fake_compile_success,
    )
    src = (
        "Before.\n"
        "\\begin{figure}\n"
        "\\begin{tikzpicture}\n\\draw (0,0) -- (1,1);\n\\end{tikzpicture}\n"
        "\\caption{A diagram.}\n"
        "\\end{figure}\n"
        "After.\n"
    )
    out = rasterize_tikz_figures(src, preamble="", out_dir=tmp_path)

    assert "\\begin{tikzpicture}" not in out
    assert "\\includegraphics{tikz-fig-001.png}" in out
    # Surrounding figure structure + caption MUST survive — pandoc needs them
    # to emit <figure><figcaption>.
    assert "\\begin{figure}" in out
    assert "\\caption{A diagram.}" in out
    assert "Before." in out and "After." in out
    # Compile wrote the PNG to the out_dir.
    assert (tmp_path / "tikz-fig-001.png").is_file()


def test_forest_env_is_replaced_with_includegraphics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``\\begin{forest}…\\end{forest}`` is rasterised the same way — this
    is what the survey-roadmap leak looks like in real papers."""
    monkeypatch.setattr(
        tikz_figures, "_compile_tikz_to_png", _fake_compile_success,
    )
    src = (
        "\\begin{figure*}[tp]\n"
        "\\centering\n"
        "\\begin{forest}\n[Root [A] [B]]\n\\end{forest}\n"
        "\\caption{Taxonomy.}\n"
        "\\end{figure*}\n"
    )
    out = rasterize_tikz_figures(src, preamble="", out_dir=tmp_path)
    assert "\\begin{forest}" not in out
    assert "\\includegraphics{tikz-fig-001.png}" in out


def test_compile_failure_leaves_env_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When pdflatex fails the TikZ env is left as-is — the rest of the
    document still renders rather than disappearing."""
    monkeypatch.setattr(
        tikz_figures, "_compile_tikz_to_png", _fake_compile_failure,
    )
    src = "\\begin{tikzpicture}\\draw (0,0);\\end{tikzpicture}\n"
    out = rasterize_tikz_figures(src, preamble="", out_dir=tmp_path)
    assert out == src
    assert not (tmp_path / "tikz-fig-001.png").exists()


def test_multiple_envs_get_distinct_png_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two TikZ envs in one document get sequential filenames so the
    inventory doesn't collide on a single deck."""
    monkeypatch.setattr(
        tikz_figures, "_compile_tikz_to_png", _fake_compile_success,
    )
    src = (
        "\\begin{tikzpicture}A\\end{tikzpicture}\n"
        "Some text in between.\n"
        "\\begin{forest}B\\end{forest}\n"
    )
    out = rasterize_tikz_figures(src, preamble="", out_dir=tmp_path)
    assert "\\includegraphics{tikz-fig-001.png}" in out
    assert "\\includegraphics{tikz-fig-002.png}" in out
    assert (tmp_path / "tikz-fig-001.png").is_file()
    assert (tmp_path / "tikz-fig-002.png").is_file()


def test_pdflatex_absent_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No pdflatex on PATH → no-op (the slide pipeline already documents
    this as an optional capability; this module follows the same posture)."""
    monkeypatch.setattr(tikz_figures.shutil, "which", lambda _name: None)
    spy_called = False

    def _spy(*_args: object, **_kw: object) -> bool:
        nonlocal spy_called
        spy_called = True
        return True

    monkeypatch.setattr(tikz_figures, "_compile_tikz_to_png", _spy)
    src = "\\begin{tikzpicture}A\\end{tikzpicture}\n"
    out = rasterize_tikz_figures(src, preamble="", out_dir=tmp_path)
    assert out == src
    assert not spy_called


def test_context_gather_pulls_definecolor_from_body_prefix() -> None:
    """``\\definecolor`` declared in the body BEFORE the figure must end
    up in the standalone preamble — surveys define colours inline at the
    top of \\begin{document}, not in the file preamble (arXiv:2503.07137
    declares ``\\definecolor{line-color}{RGB}{0,119,255}`` at flat line 20
    and the forest figure references it). Without this the standalone
    compile fails with 'undefined color'."""
    preamble = "\\usepackage{tikz}\n\\usepackage{forest}\n"
    body_prefix = (
        "\\definecolor{line-color}{RGB}{0, 119, 255}\n"
        "Some body text.\n"
        "\\tikzstyle{leaf}=[rectangle, draw=line-color]\n"
    )
    ctx = tikz_figures._gather_tikz_context(preamble, body_prefix)
    assert "\\usepackage{tikz}" in ctx
    assert "\\usepackage{forest}" in ctx
    assert "\\definecolor{line-color}{RGB}{0, 119, 255}" in ctx
    assert "\\tikzstyle{leaf}=[rectangle, draw=line-color]" in ctx


def test_context_gather_strips_documentclass_from_preamble() -> None:
    """The paper's ``\\documentclass`` (e.g. IEEEtran, article) must NOT
    leak through — our standalone classdec replaces it, and two
    documentclass declarations make pdflatex error out."""
    preamble = "\\documentclass[journal]{IEEEtran}\n\\usepackage{tikz}\n"
    ctx = tikz_figures._gather_tikz_context(preamble, "")
    assert "\\documentclass" not in ctx
    assert "\\usepackage{tikz}" in ctx


def test_context_gather_pulls_setlength_from_body_prefix_in_order() -> None:
    r"""tikzplotlib figures size the axis with ``width=\figurewidth``, which
    arXiv:2501.02902 resets in the body just before each figure
    (``\setlength{\figurewidth}{0.48\textwidth}``). Without those the
    preamble's 0.24\textwidth applied and every plot came out crammed; the
    latest setting must come last so it wins."""
    body_prefix = (
        "\\setlength{\\figurewidth}{0.48\\textwidth}\n"
        "text\n"
        "\\setlength\\figurewidth{.91\\textwidth}\n"
    )
    ctx = tikz_figures._gather_tikz_context("", body_prefix)
    first = ctx.index("\\setlength{\\figurewidth}{0.48\\textwidth}")
    second = ctx.index("\\setlength\\figurewidth{.91\\textwidth}")
    assert first < second


def test_compiled_document_suppresses_the_paper_page_style(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    r"""A paper that sets ``\pagestyle{fancy}`` (arXiv:2601.03329) printed its
    header rule and page number onto the figure; the figure page has no
    header or footer."""
    written: list[str] = []

    def _run(cmd: list[str], **kw: object) -> object:
        written.append((Path(str(kw["cwd"])) / cmd[-1]).read_text(encoding="utf-8"))
        return tikz_figures.subprocess.CompletedProcess(cmd, 1, "", "")

    monkeypatch.setattr(tikz_figures.subprocess, "run", _run)
    tikz_figures._compile_tikz_to_png(
        r"\begin{tikzpicture}\end{tikzpicture}",
        preamble="\\usepackage{fancyhdr}\n\\pagestyle{fancy}\n",
        body_prefix="", png_path=tmp_path / "f.png", dpi=72,
    )
    assert "\\begin{document}\n\\thispagestyle{empty}\n" in written[0]


def test_compile_points_texinputs_at_the_paper_source_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pdflatex runs in a temp dir, so a paper-bundled style file
    (``neurips_2025.sty`` in arXiv:2505.23416) or a figure path
    (``image/x.pdf``) only resolves if TEXINPUTS points at the paper's
    source dir (= the PNG's dir). The trailing path separator keeps the
    default texmf search path."""
    seen: dict[str, str] = {}

    def _fake_run(*_args: object, **kw: object) -> object:
        env = kw.get("env")
        assert isinstance(env, dict)
        seen["TEXINPUTS"] = env["TEXINPUTS"]
        return tikz_figures.subprocess.CompletedProcess([], 1, "", "")

    monkeypatch.setattr(tikz_figures.subprocess, "run", _fake_run)
    ok = tikz_figures._compile_tikz_to_png(
        r"\begin{tikzpicture}\end{tikzpicture}",
        preamble="", body_prefix="",
        png_path=tmp_path / "tikz-fig-001.png", dpi=72,
    )
    assert ok is False  # the fake produced no PDF
    # The compile dir comes FIRST: kpathsea searches TEXINPUTS before ".",
    # so arXiv:1508.07909's own subword.tex in the source dir was compiled
    # instead of the figure file of the same name.
    assert seen["TEXINPUTS"].startswith(
        "." + os.pathsep + str(tmp_path.resolve()) + os.pathsep,
    )


_FOREACH_LOOP = r"""\foreach \pdf/\title [count=\i] in {
    {image/a.pdf}/{(a) A},
    {image/b.pdf}/{(b) B ($x$})} {%
    \begin{tikzpicture}
        \node (img\i) {\includegraphics{\pdf}};
        \node[above] {\title};
    \end{tikzpicture}\par\vspace{0.4cm}
}"""

_FIG_HEAD = "\\begin{figure}\n\\begin{minipage}{1.2\\linewidth}\n"
_FIG_TAIL = "\n\\end{minipage}\n\\caption{Heatmaps.}\n\\end{figure}\n"
_FOREACH_FIGURE = _FIG_HEAD + _FOREACH_LOOP + _FIG_TAIL


def _png_spy(compiled: list[tuple[str, bool]]) -> object:
    def _spy(env: str, *, png_path: Path, varwidth: bool = False,
             **_kw: object) -> bool:
        compiled.append((env, varwidth))
        png_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return True
    return _spy


def test_top_level_foreach_around_tikz_is_one_figure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    r"""A ``\foreach`` loop that wraps a tikzpicture (arXiv:2505.23416's
    heatmap figure) is compiled as ONE unit with the loop around it: the
    inner env alone can't compile (``\pdf`` / ``\i`` are loop variables)
    and a bare ``\foreach`` left in the source makes pandoc fail on the
    whole paper."""
    compiled: list[tuple[str, bool]] = []
    monkeypatch.setattr(tikz_figures, "_compile_tikz_to_png", _png_spy(compiled))
    out = rasterize_tikz_figures(_FOREACH_FIGURE, preamble="", out_dir=tmp_path)

    assert compiled == [(_FOREACH_LOOP, True)]  # varwidth: the body uses \par
    assert out == _FIG_HEAD + "\\includegraphics{tikz-fig-001.png}" + _FIG_TAIL


def test_foreach_inside_tikz_env_is_left_to_the_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    r"""A ``\foreach`` INSIDE a tikzpicture is ordinary TikZ: the env is
    the unit, compiled without varwidth, exactly as before."""
    compiled: list[tuple[str, bool]] = []
    monkeypatch.setattr(tikz_figures, "_compile_tikz_to_png", _png_spy(compiled))
    env = (
        "\\begin{tikzpicture}\n"
        "\\foreach \\n in {a,b} { \\draw (\\n.north) -- (\\n.south); }\n"
        "\\end{tikzpicture}"
    )
    out = rasterize_tikz_figures(env + "\n", preamble="", out_dir=tmp_path)
    assert compiled == [(env, False)]
    assert out == "\\includegraphics{tikz-fig-001.png}\n"


def test_failed_foreach_figure_is_dropped_not_left_for_pandoc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    r"""If the loop figure won't compile, leaving the bare ``\foreach``
    in place would make pandoc reject the whole paper (the HTML falls back
    to a plain-text dump). Dropping that one figure keeps the rest."""
    monkeypatch.setattr(
        tikz_figures, "_compile_tikz_to_png", _fake_compile_failure,
    )
    out = rasterize_tikz_figures(_FOREACH_FIGURE, preamble="", out_dir=tmp_path)
    assert out == _FIG_HEAD + _FIG_TAIL


def test_foreach_without_tikz_is_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A top-level loop with no TikZ env inside is not this module's
    business — source passes through byte-identical."""
    monkeypatch.setattr(
        tikz_figures, "_compile_tikz_to_png", _fake_compile_success,
    )
    src = "\\foreach \\x in {1,2} {\\x, }\n"
    assert rasterize_tikz_figures(src, preamble="", out_dir=tmp_path) == src


def test_context_gather_keeps_brackets_inside_braces_in_tikzstyle() -> None:
    r"""A ``\tikzstyle`` option list ends at the first ``]`` OUTSIDE braces
    (LaTeX optional-argument rule). arXiv:2505.23416 writes
    ``\tikzstyle{bar} = [{Bar[width=3pt]}-{Bar[width=3pt]}]``; cutting it at
    the inner ``]`` left an unclosed ``{`` that broke every later figure."""
    body_prefix = (
        r"\tikzstyle{bar} = [{Bar[width=3pt]}-{Bar[width=3pt]}]" "\n"
        r"\tikzstyle{edge} = [" "\n  draw=black,\n  dotted\n]\n"
    )
    ctx = tikz_figures._gather_tikz_context("", body_prefix)
    assert r"\tikzstyle{bar} = [{Bar[width=3pt]}-{Bar[width=3pt]}]" in ctx
    assert "\\tikzstyle{edge} = [\n  draw=black,\n  dotted\n]" in ctx


def test_commented_out_envs_and_loops_are_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    r"""A figure the author commented out (``% \begin{forest}`` …
    arXiv:2507.11181 keeps two) is not part of the paper: it is never
    compiled and the comment stays as written. A commented ``\begin`` with
    no commented ``\end`` must not swallow the real figure after it."""
    compiled: list[tuple[str, bool]] = []
    monkeypatch.setattr(tikz_figures, "_compile_tikz_to_png", _png_spy(compiled))
    commented = (
        "% \\begin{forest}\n% [A [B]]\n% \\end{forest}\n"
        "%\\begin{tikzpicture} no commented end here\n"
        "  % \\foreach \\x in {1} {\\begin{tikzpicture}\\end{tikzpicture}}\n"
        "50\\% of text \\begin{tikzpicture}\\draw;\\end{tikzpicture}\n"
    )
    real = "\\begin{forest}\n[Real]\n\\end{forest}"
    out = rasterize_tikz_figures(commented + real + "\n", preamble="",
                                 out_dir=tmp_path)
    assert [env for env, _ in compiled] == [
        "\\begin{tikzpicture}\\draw;\\end{tikzpicture}", real,
    ]
    assert out == (
        commented.replace(
            "\\begin{tikzpicture}\\draw;\\end{tikzpicture}",
            "\\includegraphics{tikz-fig-001.png}",
        )
        + "\\includegraphics{tikz-fig-002.png}\n"
    )


def test_context_gather_strips_conference_layout_packages() -> None:
    r"""Conference styles (``neurips_2025``, ``naaclhlt2019``, …) impose the
    paper's page geometry on the standalone figure: KVzip's figure landed
    on page 2 of a letter page and BERT's drew outside the page box, both
    rasterising as blank pages. The figure needs none of them."""
    preamble = (
        "\\usepackage[final]{neurips_2025}\n"
        "\\usepackage[hyperref]{naaclhlt2019}\n"
        "\\usepackage{tikz}\n"
    )
    ctx = tikz_figures._gather_tikz_context(preamble, "")
    assert "neurips" not in ctx
    assert "naacl" not in ctx
    assert "\\usepackage{tikz}" in ctx


def test_overlay_pictures_are_not_figures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    r"""``[remember picture, overlay]`` draws decoration on the page (a
    dashed rule beside a table, a cover background): there is no figure to
    rasterise, so it is left as written and never compiled."""
    compiled: list[tuple[str, bool]] = []
    monkeypatch.setattr(tikz_figures, "_compile_tikz_to_png", _png_spy(compiled))
    src = (
        "\\begin{tikzpicture}[remember picture,overlay]\n"
        "\\draw[dashed] (0,0) -- (0,4);\n\\end{tikzpicture}\n"
        "\\begin{tikzpicture}[remember picture, overlay]\\fill;\\end{tikzpicture}\n"
    )
    assert rasterize_tikz_figures(src, preamble="", out_dir=tmp_path) == src
    assert compiled == []


def test_gnuplot_tables_shipped_with_the_paper_set_the_jobname(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    r"""pgfplots' ``raw gnuplot`` reads a precomputed ``<jobname>.<id>.table``
    (arXiv:1508.07909 ships ``subword.de-500k.table``). Compiled as
    ``fig.tex`` the plot found no data and came out empty; compiling under
    the paper's jobname finds them."""
    (tmp_path / "subword.de-500k.table").write_text("1 0.5\n", encoding="utf-8")
    seen: list[object] = []

    def _spy(env: str, *, png_path: Path, **kw: object) -> bool:
        seen.append(kw.get("jobname"))
        png_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return True

    monkeypatch.setattr(tikz_figures, "_compile_tikz_to_png", _spy)
    src = (
        "\\begin{tikzpicture}\\addplot+[raw gnuplot, id=de-500k] gnuplot "
        "{plot 'x'};\\end{tikzpicture}\n"
        "\\begin{tikzpicture}\\draw;\\end{tikzpicture}\n"
    )
    rasterize_tikz_figures(src, preamble="", out_dir=tmp_path)
    assert seen == ["subword", None]


_FIG = (100, 60)
_LETTER = (612, 792)


def _fake_pdflatex_writing(
    pages: list[tuple[tuple[int, int], bool]],
) -> object:
    """A ``subprocess.run`` stand-in that writes ``<job>.pdf`` with one page
    per ``(size, ink)`` entry: ink draws a line, otherwise it stays blank."""
    import pymupdf

    def _run(cmd: list[str], **kw: object) -> object:
        job = Path(cmd[-1]).stem
        doc = pymupdf.open()
        for (w, h), ink in pages:
            page = doc.new_page(width=w, height=h)
            if ink:
                page.draw_line((10, 30), (90, 30), width=1)
        doc.save(str(Path(str(kw["cwd"])) / f"{job}.pdf"))
        return tikz_figures.subprocess.CompletedProcess(cmd, 1, "", "")
    return _run


@pytest.mark.parametrize(
    ("pages", "ok"),
    [
        ([(_FIG, True)], True),
        ([(_FIG, False)], False),
        ([(_LETTER, True), (_FIG, True)], True),
        ([(_FIG, True), (_FIG, False)], False),
        ([(_LETTER, True)], False),
    ],
    ids=[
        "drawn", "blank", "junk-page-then-figure", "blank-last-page",
        "paper-sized",
    ],
)
def test_compile_renders_the_last_page_and_rejects_blank_or_paper_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    pages: list[tuple[tuple[int, int], bool]], ok: bool,
) -> None:
    r"""The figure is the document body, so it is the LAST page: class-only
    preamble commands (ACL's ``\setlength\titlebox{5cm}``, elsarticle's
    ``\journal{…}``) typeset stray text onto an extra first page. A blank
    last page (a plot with no data) or a paper-sized one (a layout style
    overrode standalone's cropping) showed readers a blank box, so the env
    is left uncompiled instead."""
    monkeypatch.setattr(
        tikz_figures.subprocess, "run", _fake_pdflatex_writing(pages),
    )
    png = tmp_path / "tikz-fig-001.png"
    got = tikz_figures._compile_tikz_to_png(
        r"\begin{tikzpicture}\end{tikzpicture}",
        preamble="", body_prefix="", png_path=png, dpi=72,
    )
    assert got is ok
    assert png.is_file() is ok
