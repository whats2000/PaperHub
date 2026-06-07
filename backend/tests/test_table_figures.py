import shutil
from pathlib import Path

import pytest

from paperhub.pipelines.table_figures import (
    _build_snippet,
    _compile_table_to_png,
    _find_table_envs,
    _is_hostile,
    _unwrap_fitting_boxes,
    rasterize_complex_tables,
)


def test_unwrap_resizebox_around_table_image() -> None:
    # The exact shape pandoc drops (arXiv:2602.20200's LIBERO table): a
    # \resizebox wrapping our rasterised-table image, with % line-comments.
    tex = (
        "\\resizebox{\\linewidth}{!}{%\n"
        "\\includegraphics{table-fig-001.png}%\n"
        "}"
    )
    assert _unwrap_fitting_boxes(tex) == "\\includegraphics{table-fig-001.png}"


def test_unwrap_scalebox_and_adjustbox_around_table_image() -> None:
    assert (
        _unwrap_fitting_boxes("\\scalebox{0.8}{\\includegraphics{table-fig-002.png}}")
        == "\\includegraphics{table-fig-002.png}"
    )
    assert (
        _unwrap_fitting_boxes(
            "\\adjustbox{width=\\linewidth}{\\includegraphics{table-fig-003.png}}"
        )
        == "\\includegraphics{table-fig-003.png}"
    )


def test_unwrap_leaves_non_table_figures_alone() -> None:
    # A \resizebox around a REAL figure (not our table-fig-N image) must NOT be
    # unwrapped — only our generated table images are.
    tex = "\\resizebox{\\linewidth}{!}{\\includegraphics{figure3.png}}"
    assert _unwrap_fitting_boxes(tex) == tex

# ---------------------------------------------------------------------------
# Task 1: Hostility classifier
# ---------------------------------------------------------------------------


def test_starred_and_x_envs_are_hostile() -> None:
    assert _is_hostile("tabular*", "a & b \\\\")
    assert _is_hostile("tabularx", "a & b \\\\")


def test_plain_tabular_is_not_hostile() -> None:
    assert not _is_hostile("tabular", "a & b & c \\\\ \\midrule x & 1 & 2 \\\\")


def test_multirow_or_makecell_makes_plain_tabular_hostile() -> None:
    assert _is_hostile("tabular", "\\multirow{2}{*}{a} & b \\\\")
    assert _is_hostile("tabular", "\\makecell{a\\\\b} & c \\\\")


def test_multicolumn_alone_is_not_hostile_but_with_cmidrule_is() -> None:
    assert not _is_hostile("tabular", "\\multicolumn{2}{c}{a} \\\\")
    assert _is_hostile("tabular", "\\multicolumn{2}{c}{a} \\\\ \\cmidrule(lr){1-2}")


# ---------------------------------------------------------------------------
# Task 2: Env-depth-aware table finder
# ---------------------------------------------------------------------------


def test_finds_a_simple_tabular() -> None:
    tex = "before \\begin{tabular}{cc}a & b\\\\\\end{tabular} after"
    envs = _find_table_envs(tex)
    assert len(envs) == 1
    start, end, name = envs[0]
    assert name == "tabular"
    assert tex[start:end] == "\\begin{tabular}{cc}a & b\\\\\\end{tabular}"


def test_nested_tabular_inside_tabular_star_yields_one_outermost_env() -> None:
    tex = (
        "\\begin{tabular*}{\\textwidth}{cc}"
        "\\begin{tabular}{cc}x & y\\\\\\end{tabular}"
        " & z\\\\\\end{tabular*}"
    )
    envs = _find_table_envs(tex)
    assert len(envs) == 1
    start, end, name = envs[0]
    assert name == "tabular*"
    assert tex[start:end] == tex  # spans the whole outer tabular*


def test_two_sibling_tables_yield_two_envs() -> None:
    tex = (
        "\\begin{tabular}{c}a\\\\\\end{tabular}"
        "MID"
        "\\begin{tabularx}{\\linewidth}{c}b\\\\\\end{tabularx}"
    )
    envs = _find_table_envs(tex)
    assert [n for _, _, n in envs] == ["tabular", "tabularx"]


def test_unclosed_env_is_skipped() -> None:
    tex = "\\begin{tabular}{cc}a & b oops no end"
    assert _find_table_envs(tex) == []


# ---------------------------------------------------------------------------
# Task 3: Standalone-snippet builder
# ---------------------------------------------------------------------------


def test_snippet_has_bedrock_textwidth_and_document() -> None:
    snip = _build_snippet("\\begin{tabular}{c}a\\\\\\end{tabular}", preamble="", body_prefix="")
    assert "\\documentclass[border=10pt]{standalone}" in snip
    assert "\\usepackage{booktabs}" in snip
    assert "\\setlength{\\textwidth}{18cm}" in snip
    assert "\\begin{document}" in snip and "\\end{document}" in snip
    assert "\\begin{tabular}{c}a" in snip


def test_snippet_strips_sentinels() -> None:
    env = "\\begin{tabular}{c}aPHCHUNKANCHOR12END & b\\\\\\end{tabular}"
    snip = _build_snippet(env, preamble="", body_prefix="")
    assert "PHCHUNKANCHOR" not in snip


def test_snippet_drops_paper_documentclass_but_keeps_definecolor() -> None:
    preamble = "\\documentclass[11pt]{article}\n\\newcommand{\\dmodel}{d}"
    body_prefix = "intro \\definecolor{hl}{RGB}{0,119,255} more"
    snip = _build_snippet("\\begin{tabular}{c}\\dmodel\\\\\\end{tabular}",
                          preamble=preamble, body_prefix=body_prefix)
    assert "\\documentclass[11pt]{article}" not in snip   # paper's class removed
    assert "\\newcommand{\\dmodel}{d}" in snip            # author macro kept
    assert "\\definecolor{hl}{RGB}{0,119,255}" in snip    # body-prefix colour kept


# ---------------------------------------------------------------------------
# Task 4: Compile table to PNG (pdflatex + pymupdf)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")
def test_compile_simple_table_produces_png(tmp_path: Path) -> None:
    png = tmp_path / "t.png"
    ok = _compile_table_to_png(
        "\\begin{tabular}{cc}\\toprule a & b\\\\ \\midrule 1 & 2\\\\ \\bottomrule\\end{tabular}",
        preamble="",
        body_prefix="",
        png_path=png,
        dpi=150,
    )
    assert ok is True
    assert png.is_file() and png.stat().st_size > 0


def test_compile_returns_false_when_pdflatex_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("paperhub.pipelines.table_figures.shutil.which", lambda _: None)
    # No pdflatex on PATH -> FileNotFoundError inside subprocess -> graceful False.
    monkeypatch.setattr(
        "paperhub.pipelines.table_figures.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()),
    )
    ok = _compile_table_to_png(
        "\\begin{tabular}{c}a\\\\\\end{tabular}",
        preamble="", body_prefix="", png_path=tmp_path / "x.png", dpi=150,
    )
    assert ok is False


# ---------------------------------------------------------------------------
# Task 5: Orchestrator rasterize_complex_tables
# ---------------------------------------------------------------------------


def test_no_op_when_pdflatex_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("paperhub.pipelines.table_figures.shutil.which", lambda _: None)
    tex = "\\begin{tabular*}{\\textwidth}{cc}a & b\\\\\\end{tabular*}"
    assert rasterize_complex_tables(tex, preamble="", out_dir=tmp_path, dpi=150) == tex


def test_simple_tabular_is_left_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # pdflatex "present" but no hostile env -> unchanged, compiler never called.
    monkeypatch.setattr("paperhub.pipelines.table_figures.shutil.which", lambda _: "/usr/bin/pdflatex")
    called = []
    monkeypatch.setattr("paperhub.pipelines.table_figures._compile_table_to_png",
                        lambda *a, **k: called.append(1) or True)
    tex = "\\begin{tabular}{cc}a & b\\\\\\end{tabular}"
    assert rasterize_complex_tables(tex, preamble="", out_dir=tmp_path, dpi=150) == tex
    assert called == []


def test_hostile_table_replaced_with_includegraphics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("paperhub.pipelines.table_figures.shutil.which", lambda _: "/usr/bin/pdflatex")
    # Stub the compiler to "succeed" and create the PNG so we test the rewrite.
    def fake_compile(env_text: str, *, preamble: str, body_prefix: str, png_path: Path, dpi: int) -> bool:
        png_path.write_bytes(b"\x89PNG")
        return True
    monkeypatch.setattr("paperhub.pipelines.table_figures._compile_table_to_png", fake_compile)
    tex = "pre \\begin{tabular*}{\\textwidth}{cc}a & b\\\\\\end{tabular*} post"
    out = rasterize_complex_tables(tex, preamble="", out_dir=tmp_path, dpi=150)
    assert "\\includegraphics{table-fig-001.png}" in out
    assert "\\begin{tabular*}" not in out
    assert out.startswith("pre ") and out.endswith(" post")
    assert (tmp_path / "table-fig-001.png").is_file()


def test_compile_failure_leaves_env_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("paperhub.pipelines.table_figures.shutil.which", lambda _: "/usr/bin/pdflatex")
    monkeypatch.setattr("paperhub.pipelines.table_figures._compile_table_to_png", lambda *a, **k: False)
    tex = "\\begin{tabular*}{\\textwidth}{cc}a & b\\\\\\end{tabular*}"
    assert rasterize_complex_tables(tex, preamble="", out_dir=tmp_path, dpi=150) == tex
