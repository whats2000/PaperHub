import shutil
from pathlib import Path

import pymupdf
import pytest

from paperhub.pipelines.table_figures import (
    _build_snippet,
    _compile_table_to_png,
    _convert_rasterized_table_floats,
    _find_plain_tabulars,
    _is_blank_pixmap,
    _normalize_colspec_for_plain,
    _parse_newcolumntype_defs,
    _residual_dump_envs,
    _table_pixmap,
    downgrade_width_tables,
    rasterize_complex_tables,
    repair_tables_for_pandoc,
    strip_cmidrule_trim,
    unwrap_table_boxes,
)

# ---------------------------------------------------------------------------
# Repair 1 — \cmidrule trim
# ---------------------------------------------------------------------------


def test_strip_cmidrule_trim_drops_parenthetical() -> None:
    assert strip_cmidrule_trim("\\cmidrule(r{0.2cm}){1-3}") == "\\cmidrule{1-3}"
    assert (
        strip_cmidrule_trim("\\cmidrule(r{0.2cm}){1-3}\\cmidrule(l{0.2cm}){4-6}")
        == "\\cmidrule{1-3}\\cmidrule{4-6}"
    )


def test_strip_cmidrule_trim_leaves_plain_cmidrule() -> None:
    assert strip_cmidrule_trim("\\midrule\\cmidrule{1-2}") == "\\midrule\\cmidrule{1-2}"


# ---------------------------------------------------------------------------
# Repair 2 — unwrap fitting boxes around a tabular
# ---------------------------------------------------------------------------


def test_unwrap_resizebox_around_tabular() -> None:
    tex = "\\resizebox{\\linewidth}{!}{%\n\\begin{tabular}{cc}a & b\\\\\\end{tabular}}"
    out = unwrap_table_boxes(tex)
    assert "\\resizebox" not in out
    # The box's own trailing `}` is gone; the tabular (and the harmless `%` line
    # comment that sat inside the box) survive intact for pandoc.
    assert out == "%\n\\begin{tabular}{cc}a & b\\\\\\end{tabular}"


def test_unwrap_scalebox_and_adjustbox_around_tabular() -> None:
    sb = "\\scalebox{0.8}{\\begin{tabular}{cc}a&b\\\\\\end{tabular}}"
    assert unwrap_table_boxes(sb) == "\\begin{tabular}{cc}a&b\\\\\\end{tabular}"
    ab = "\\adjustbox{width=\\linewidth}{\\begin{tabular}{cc}a&b\\\\\\end{tabular}}"
    assert unwrap_table_boxes(ab) == "\\begin{tabular}{cc}a&b\\\\\\end{tabular}"


def test_unwrap_leaves_box_around_non_tabular_alone() -> None:
    # A \resizebox around a real figure (no tabular) must NOT be unwrapped.
    tex = "\\resizebox{\\linewidth}{!}{\\includegraphics{figure3.png}}"
    assert unwrap_table_boxes(tex) == tex


def test_unwrap_handles_nested_box_with_braces_in_content() -> None:
    tex = (
        "pre \\scalebox{0.9}{\\begin{tabular}{cc}"
        "\\textbf{a} & b\\\\\\end{tabular}} post"
    )
    out = unwrap_table_boxes(tex)
    assert out == "pre \\begin{tabular}{cc}\\textbf{a} & b\\\\\\end{tabular} post"


# ---------------------------------------------------------------------------
# Repair 3 — downgrade width-fixed envs
# ---------------------------------------------------------------------------


def test_downgrade_tabular_star_drops_width() -> None:
    tex = "\\begin{tabular*}{\\textwidth}{@{}lcc@{}}a & b & c\\\\\\end{tabular*}"
    out = downgrade_width_tables(tex)
    assert out == "\\begin{tabular}{@{}lcc@{}}a & b & c\\\\\\end{tabular}"


def test_downgrade_tabularx_maps_X_columns_to_l() -> None:
    tex = "\\begin{tabularx}{\\linewidth}{lXX}a & b & c\\\\\\end{tabularx}"
    out = downgrade_width_tables(tex)
    assert out == "\\begin{tabular}{lll}a & b & c\\\\\\end{tabular}"


def test_normalize_colspec_preserves_letters_inside_groups() -> None:
    # X inside p{...}/>{...} (e.g. a unit or macro) must NOT be touched.
    assert _normalize_colspec_for_plain("lXp{2Xcm}X") == "llp{2Xcm}l"


def test_parse_newcolumntype_param_and_fixed() -> None:
    # Parameterised P (takes a width) -> base 'p'; fixed C -> its alignment 'c'.
    tex = (
        "\\newcolumntype{P}[1]{>{\\centering\\arraybackslash}p{#1}}\n"
        "\\newcolumntype{C}{>{\\centering}c}\n"
    )
    defs = _parse_newcolumntype_defs(tex)
    assert defs == {"P": (1, "p"), "C": (0, "c")}
    # A commented def is invisible to pandoc — don't capture it.
    assert _parse_newcolumntype_defs("% \\newcolumntype{P}[1]{p{#1}}\n") == {}


def test_repair_rewrites_custom_column_and_strips_def() -> None:
    # arXiv:2404.07214: a body `\newcolumntype{P}[1]{...p{#1}}` + a tabular whose
    # colspec uses P{30pt}. pandoc can't parse P -> dumps the whole table. The
    # repair must rewrite P{30pt} -> p{30pt} and drop the definition.
    tex = (
        "\\newcolumntype{P}[1]{>{\\centering\\arraybackslash}p{#1}}\n"
        "\\begin{tabular}{|p{56pt}|P{30pt}|P{30pt}|}a & b & c\\\\\\end{tabular}\n"
    )
    out = repair_tables_for_pandoc(tex)
    assert "\\newcolumntype" not in out
    assert "P{30pt}" not in out
    assert "{|p{56pt}|p{30pt}|p{30pt}|}" in out


def test_downgrade_handles_repeated_envs() -> None:
    tex = (
        "\\begin{tabularx}{\\hsize}{lX}a&b\\\\\\end{tabularx}"
        "MID"
        "\\begin{tabular*}{\\textwidth}{cc}c&d\\\\\\end{tabular*}"
    )
    out = downgrade_width_tables(tex)
    assert out.count("\\begin{tabular}") == 2
    assert "tabularx" not in out and "tabular*" not in out


def test_repair_composes_all_three() -> None:
    tex = (
        "\\resizebox{\\linewidth}{!}{\\begin{tabularx}{\\hsize}{lX}"
        "a & b \\cmidrule(r{2pt}){1-2}\\\\\\end{tabularx}}"
    )
    out = repair_tables_for_pandoc(tex)
    assert "\\resizebox" not in out
    assert "tabularx" not in out
    assert "\\begin{tabular}{ll}" in out
    assert "(r{2pt})" not in out and "\\cmidrule{1-2}" in out


# ---------------------------------------------------------------------------
# Residue: plain-tabular finder + comment skip
# ---------------------------------------------------------------------------


def test_find_plain_tabulars_skips_commented() -> None:
    tex = (
        "live \\begin{tabular}{c}a\\\\\\end{tabular}\n"
        "% \\begin{tabular}{c}dead\\\\\\end{tabular}\n"
    )
    envs = _find_plain_tabulars(tex)
    assert len(envs) == 1
    s, e = envs[0]
    assert tex[s:e] == "\\begin{tabular}{c}a\\\\\\end{tabular}"


def test_find_plain_tabulars_outermost_only() -> None:
    tex = (
        "\\begin{tabular}{cc}"
        "\\begin{tabular}{c}x\\\\\\end{tabular} & y\\\\"
        "\\end{tabular}"
    )
    assert len(_find_plain_tabulars(tex)) == 1


# ---------------------------------------------------------------------------
# Residue detection from pandoc output (mocked)
# ---------------------------------------------------------------------------


def test_residual_dump_envs_matches_leaked_colspec(monkeypatch: pytest.MonkeyPatch) -> None:
    tex = (
        "pre \\begin{tabular}{l|rrrr}Model & a & b & c & d\\\\\\end{tabular}"
        " mid \\begin{tabular}{cc}x & y\\\\\\end{tabular}"
    )
    # pandoc dumps the first table (colspec leaks), renders the second.
    fake_html = (
        '<div class="tabular">\n<p><span>l|rrrr</span>\nModel &amp; a</p></div>\n'
        "<table><tbody><tr><td>x</td><td>y</td></tr></tbody></table>"
    )
    monkeypatch.setattr(
        "paperhub.pipelines.table_figures._render_pandoc", lambda _t: fake_html
    )
    targets = _residual_dump_envs(tex)
    assert len(targets) == 1
    s, e = targets[0]
    assert tex[s:e].startswith("\\begin{tabular}{l|rrrr}")


def test_residual_dump_envs_empty_when_pandoc_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "paperhub.pipelines.table_figures._render_pandoc", lambda _t: None
    )
    tex = "\\begin{tabular}{l|rrrr}a & b & c & d\\\\\\end{tabular}"
    assert _residual_dump_envs(tex) == []


def test_residual_dump_envs_misaligned_matches_dump_by_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two envs share colspec `l|rrrr`; the SECOND dumps (its cells appear in the
    # dump), the first renders. Counts are misaligned (1 dump outcome, 2 envs),
    # so we must match by CONTENT and rasterise ONLY the dumped (second) env —
    # never the rendered first one.
    tex = (
        "\\begin{tabular}{l|rrrr}Alpha & q1 & q2 & q3 & q4\\\\\\end{tabular}"
        " mid \\begin{tabular}{l|rrrr}Bravo & z9 & z8 & z7 & z6\\\\\\end{tabular}"
    )
    monkeypatch.setattr(
        "paperhub.pipelines.table_figures._render_pandoc",
        lambda _t: (
            "<table><tbody><tr><td>Alpha</td><td>q1</td></tr></tbody></table>"
            '<div class="tabular"><p><span>l|rrrr</span>Bravo z9 z8 z7 z6</p></div>'
        ),
    )
    targets = _residual_dump_envs(tex)
    assert len(targets) == 1
    s, e = targets[0]
    assert "Bravo" in tex[s:e] and "Alpha" not in tex[s:e]


def test_residual_dump_envs_misaligned_skips_unmatched_dump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Misaligned, but no env's cells fill the dump (content < threshold) -> leave
    # it as text rather than risk rasterising a rendered table.
    tex = (
        "\\begin{tabular}{l|rrrr}Alpha & q1 & q2 & q3 & q4\\\\\\end{tabular}"
        " mid \\begin{tabular}{cc}Bravo & z9\\\\\\end{tabular}"
    )
    monkeypatch.setattr(
        "paperhub.pipelines.table_figures._render_pandoc",
        lambda _t: (
            "<table><tbody></tbody></table><table><tbody></tbody></table>"
            '<div class="tabular"><p><span>xx</span>totally unrelated words here</p></div>'
        ),
    )
    assert _residual_dump_envs(tex) == []


def test_residual_dump_envs_none_when_all_rendered(monkeypatch: pytest.MonkeyPatch) -> None:
    # No dump regions -> nothing to rasterise even if tables exist.
    monkeypatch.setattr(
        "paperhub.pipelines.table_figures._render_pandoc",
        lambda _t: "<table><tbody></tbody></table>",
    )
    tex = "\\begin{tabular}{cc}a & b\\\\\\end{tabular}"
    assert _residual_dump_envs(tex) == []


# ---------------------------------------------------------------------------
# Blank / banner-aware page picking
# ---------------------------------------------------------------------------


def test_is_blank_pixmap_detects_white_vs_content() -> None:
    blank = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 80, 80), False)
    blank.clear_with(255)
    assert _is_blank_pixmap(blank) is True
    content = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 80, 80), False)
    content.clear_with(255)
    content.set_pixel(40, 40, (0, 0, 0))
    assert _is_blank_pixmap(content) is False


class _FakeRect:
    def __init__(self, w: float, h: float) -> None:
        self.width, self.height = w, h


class _FakePage:
    def __init__(self, pix: pymupdf.Pixmap, w: float, h: float) -> None:
        self._pix = pix
        self.rect = _FakeRect(w, h)

    def get_pixmap(self, *, dpi: int) -> pymupdf.Pixmap:  # noqa: ARG002
        return self._pix


class _FakeDoc:
    """Minimal stand-in for pymupdf.Document (page_count + load_page)."""

    def __init__(self, *pages: _FakePage) -> None:
        self._pages = pages

    @property
    def page_count(self) -> int:
        return len(self._pages)

    def load_page(self, i: int) -> _FakePage:
        return self._pages[i]


def _blank() -> pymupdf.Pixmap:
    p = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
    p.clear_with(255)
    return p


def _content() -> pymupdf.Pixmap:
    p = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
    p.clear_with(255)
    p.set_pixel(20, 20, (0, 0, 0))
    return p


def test_table_pixmap_skips_full_page_banner() -> None:
    # A NeurIPS/ICML banner is a full letter page WITH content; the real table
    # is the small cropped page. Pick the cropped one.
    cropped = _content()
    doc = _FakeDoc(_FakePage(_content(), 612, 792), _FakePage(cropped, 259, 127))
    assert _table_pixmap(doc, dpi=150) is cropped


def test_table_pixmap_skips_blank_leading_page() -> None:
    cropped = _content()
    doc = _FakeDoc(_FakePage(_blank(), 612, 792), _FakePage(cropped, 200, 120))
    assert _table_pixmap(doc, dpi=150) is cropped


def test_table_pixmap_none_when_all_blank() -> None:
    doc = _FakeDoc(_FakePage(_blank(), 612, 792), _FakePage(_blank(), 200, 120))
    assert _table_pixmap(doc, dpi=150) is None


def test_table_pixmap_falls_back_to_only_full_page_content() -> None:
    # A genuinely huge table may crop to ~letter size; still use it (fallback).
    only = _content()
    doc = _FakeDoc(_FakePage(_blank(), 200, 120), _FakePage(only, 612, 792))
    assert _table_pixmap(doc, dpi=150) is only


# ---------------------------------------------------------------------------
# Snippet builder
# ---------------------------------------------------------------------------


def test_snippet_has_bedrock_and_document() -> None:
    snip = _build_snippet("\\begin{tabular}{c}a\\\\\\end{tabular}", preamble="", body_prefix="")
    assert "\\documentclass[border={34pt 10pt 10pt 10pt}]{standalone}" in snip
    assert "\\usepackage{booktabs}" in snip
    assert "\\begin{document}" in snip and "\\end{document}" in snip
    assert "\\begin{tabular}{c}a" in snip


def test_snippet_strips_sentinels() -> None:
    env = "\\begin{tabular}{c}aPHCHUNKANCHOR12END & b\\\\\\end{tabular}"
    assert "PHCHUNKANCHOR" not in _build_snippet(env, preamble="", body_prefix="")


def test_snippet_strips_conference_layout_packages() -> None:
    # An ICML/NeurIPS style emits a "page layout violates" banner that would
    # rasterise instead of the table; it must be stripped from the snippet.
    preamble = (
        "\\usepackage{icml2021}\n\\usepackage[preprint]{neurips_2024}\n"
        "\\usepackage{booktabs}\n\\newcommand{\\foo}{bar}"
    )
    snip = _build_snippet(
        "\\begin{tabular}{c}\\foo\\\\\\end{tabular}", preamble=preamble, body_prefix=""
    )
    assert "icml2021" not in snip
    assert "neurips_2024" not in snip
    assert "\\newcommand{\\foo}{bar}" in snip  # author macro kept


def test_snippet_drops_paper_documentclass_keeps_definecolor() -> None:
    preamble = "\\documentclass[11pt]{article}\n\\newcommand{\\dmodel}{d}"
    body_prefix = "intro \\definecolor{hl}{RGB}{0,119,255} more"
    snip = _build_snippet(
        "\\begin{tabular}{c}\\dmodel\\\\\\end{tabular}",
        preamble=preamble, body_prefix=body_prefix,
    )
    assert "\\documentclass[11pt]{article}" not in snip
    assert "\\newcommand{\\dmodel}{d}" in snip
    assert "\\definecolor{hl}{RGB}{0,119,255}" in snip


# ---------------------------------------------------------------------------
# Compile (pdflatex)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")
def test_compile_simple_table_produces_png(tmp_path: Path) -> None:
    png = tmp_path / "t.png"
    ok = _compile_table_to_png(
        "\\begin{tabular}{cc}\\toprule a & b\\\\ \\midrule 1 & 2\\\\ \\bottomrule\\end{tabular}",
        preamble="", body_prefix="", png_path=png, dpi=150,
    )
    assert ok is True
    assert png.is_file() and png.stat().st_size > 0


def test_compile_returns_false_when_pdflatex_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
# table/table* float -> figure
# ---------------------------------------------------------------------------


def test_rasterized_table_float_becomes_figure() -> None:
    tex = (
        "\\begin{table*}[h]\\centering\\caption{Perf}\\label{t}\n"
        "\\includegraphics{table-fig-001.png}\n\\end{table*}"
    )
    out = _convert_rasterized_table_floats(tex)
    assert "\\begin{figure}[h]" in out and "\\end{figure}" in out
    assert "\\begin{table*}" not in out
    assert "\\caption{Perf}" in out


def test_non_rasterized_table_float_left_alone() -> None:
    tex = "\\begin{table}\\caption{c}\\begin{tabular}{cc}a & b\\\\\\end{tabular}\\end{table}"
    assert _convert_rasterized_table_floats(tex) == tex


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _both_present(name: str) -> str:
    return "/usr/bin/" + name


def test_orchestrator_repairs_even_without_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No pandoc/pdflatex -> still apply the pure-text repairs (no rasterisation).
    monkeypatch.setattr("paperhub.pipelines.table_figures.shutil.which", lambda _: None)
    tex = "\\resizebox{\\linewidth}{!}{\\begin{tabularx}{\\hsize}{lX}a & b\\\\\\end{tabularx}}"
    out = rasterize_complex_tables(tex, preamble="", out_dir=tmp_path, dpi=150)
    assert "\\resizebox" not in out and "tabularx" not in out
    assert "\\begin{tabular}{ll}" in out


def test_orchestrator_leaves_rendered_tables_unrasterised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("paperhub.pipelines.table_figures.shutil.which", _both_present)
    monkeypatch.setattr(
        "paperhub.pipelines.table_figures._render_pandoc",
        lambda _t: "<table><tbody></tbody></table>",  # all rendered, no dumps
    )
    called: list[int] = []
    monkeypatch.setattr(
        "paperhub.pipelines.table_figures._compile_table_to_png",
        lambda *a, **k: called.append(1) or True,
    )
    tex = "\\begin{tabular}{cc}a & b\\\\\\end{tabular}"
    assert rasterize_complex_tables(tex, preamble="", out_dir=tmp_path, dpi=150) == tex
    assert called == []  # nothing rasterised


def test_orchestrator_rasterizes_residual_dump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("paperhub.pipelines.table_figures.shutil.which", _both_present)
    monkeypatch.setattr(
        "paperhub.pipelines.table_figures._render_pandoc",
        lambda _t: '<div class="tabular"><p><span>l|rrrr</span>a</p></div>',
    )

    def fake_compile(env_text: str, *, preamble: str, body_prefix: str, png_path: Path, dpi: int) -> bool:
        png_path.write_bytes(b"\x89PNG")
        return True

    monkeypatch.setattr("paperhub.pipelines.table_figures._compile_table_to_png", fake_compile)
    tex = "pre \\begin{tabular}{l|rrrr}M & a & b & c & d\\\\\\end{tabular} post"
    out = rasterize_complex_tables(tex, preamble="", out_dir=tmp_path, dpi=150)
    assert "\\includegraphics{table-fig-001.png}" in out
    assert "\\begin{tabular}" not in out
    assert out.startswith("pre ") and out.endswith(" post")
    assert (tmp_path / "table-fig-001.png").is_file()


def test_orchestrator_compile_failure_leaves_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("paperhub.pipelines.table_figures.shutil.which", _both_present)
    monkeypatch.setattr(
        "paperhub.pipelines.table_figures._render_pandoc",
        lambda _t: '<div class="tabular"><p><span>cc</span>a</p></div>',
    )
    monkeypatch.setattr(
        "paperhub.pipelines.table_figures._compile_table_to_png", lambda *a, **k: False
    )
    tex = "\\begin{tabular}{cc}a & b\\\\\\end{tabular}"
    assert rasterize_complex_tables(tex, preamble="", out_dir=tmp_path, dpi=150) == tex
