"""Unit tests for the TikZ pre-rasteriser.

End-to-end compilation requires pdflatex on PATH, which CI machines don't
have. These tests exercise the deterministic glue — detection,
substitution, graceful no-op — by monkey-patching ``_compile_tikz_to_png``
so we can assert the rewrite behaviour without launching pdflatex.

The actual pdflatex compile + rasterise path is covered by the live
``:8000`` benchmark when present, not the unit suite.
"""
from __future__ import annotations

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
) -> bool:
    """Pretend the compile worked: touch the PNG file at the requested path."""
    png_path.parent.mkdir(parents=True, exist_ok=True)
    png_path.write_bytes(b"\x89PNG\r\n\x1a\n")  # PNG signature only — fine for test
    return True


def _fake_compile_failure(
    env: str, *, preamble: str, body_prefix: str, png_path: Path, dpi: int,
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
