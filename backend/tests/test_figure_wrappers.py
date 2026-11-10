"""Figure wrappers pandoc drops are unwrapped inside figure environments."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from paperhub.pipelines.figure_wrappers import unwrap_figure_wrappers


def test_subfigure_keeps_its_body_and_panel_label() -> None:
    tex = (r"\begin{figure}[t]\centering"
           r"\subfigure[MoE-M]{\includegraphics[width=.4\linewidth]{a.png}\label{x}}"
           r"\subfloat[]{\includegraphics{b.png}}"
           r"\subfigure{\includegraphics{c.png}}"
           r"\caption{Two.}\end{figure}")
    out = unwrap_figure_wrappers(tex)
    assert out == (r"\begin{figure}[t]\centering"
                   r"\includegraphics[width=.4\linewidth]{a.png}\label{x} (a) MoE-M "
                   r"\includegraphics{b.png} (b) "
                   r"\includegraphics{c.png}"
                   r"\caption{Two.}\end{figure}")


def test_panel_label_never_lands_in_a_comment_or_adds_a_blank_line() -> None:
    """Paper 39: ``{...png}% \\label{x}`` — a label on that line would be
    commented out. Paper 80: the group's own newlines must not leave a blank
    line between panels."""
    tex = ("\\begin{figure}\\subfloat[Point Reaching]{\\includegraphics{PR.png}% \\label{a}}"
           "\n\\subfigure[band]{\n\\includegraphics{b.png}\n}\n\\end{figure}")
    out = unwrap_figure_wrappers(tex)
    assert "\\includegraphics{PR.png}% \\label{a}\n (a) Point Reaching " in out
    assert "\\includegraphics{b.png} (b) band \n" in out
    assert "\n\n" not in out


def test_resizebox_scalebox_and_rule() -> None:
    tex = (r"\begin{figure*}\resizebox{0.6\textwidth}{!}{\includegraphics{c.png}}"
           r"\scalebox{0.5}[0.4]{\includegraphics{d.png}}"
           r"\caption{Masks \textcolor{blue}{\rule{7pt}{7pt}} and \rule[-1pt]{1em}{2pt}.}"
           r"\end{figure*}")
    assert unwrap_figure_wrappers(tex) == (
        r"\begin{figure*}\includegraphics{c.png}\includegraphics{d.png}"
        r"\caption{Masks \textcolor{blue}{} and .}\end{figure*}")


def test_outside_figures_and_without_wrappers_nothing_changes() -> None:
    tex = (r"A \resizebox{2cm}{!}{text} and \rule{1pt}{2pt} in prose."
           "\n" r"\begin{figure}\includegraphics{a.png}\caption{Plain.}\end{figure}"
           "\n" r"\begin{table}\resizebox{\linewidth}{!}{\begin{tabular}{c}x\end{tabular}}"
           r"\end{table}")
    assert unwrap_figure_wrappers(tex) == tex


def test_nested_wrappers_unwrap_fully() -> None:
    tex = (r"\begin{figure}\resizebox{0.8\textwidth}{!}{"
           r"\subfigure[A]{\includegraphics{a.png}}}\end{figure}")
    assert unwrap_figure_wrappers(tex) == (
        r"\begin{figure}\includegraphics{a.png} (a) A \end{figure}")


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")
def test_unwrapping_keeps_every_figure_number(tmp_path: Path) -> None:
    """Paper 80: sub-environments made pandoc number each panel as a figure,
    so the text's "Fig. 3" became "Fig. 8". Unwrapped panels must not."""
    doc = (r"\begin{figure}\subfigure[global\label{fig:g}]{\includegraphics{a.png}}"
           r"\subfigure[band]{\includegraphics{b.png}}\caption{P.}\label{fig:atomic}"
           r"\end{figure}"
           "\n" r"\begin{figure}\includegraphics{c.png}\caption{O.}\label{fig:other}"
           r"\end{figure}"
           "\n" r"See \ref{fig:atomic} and \ref{fig:other}.")

    def render(body: str) -> str:
        f = tmp_path / "n.tex"
        f.write_text("\\documentclass{article}\n\\begin{document}\n" + body
                     + "\n\\end{document}\n", encoding="utf-8")
        return subprocess.run(["pandoc", "-f", "latex", "-t", "html5", str(f)],
                              capture_output=True, text=True, encoding="utf-8").stdout

    before, after = render(doc), render(unwrap_figure_wrappers(doc))
    for label, number in (("fig:atomic", "1"), ("fig:other", "2")):
        tag = f'data-reference="{label}">{number}</a>'
        assert tag in before and tag in after
    assert after.count("<img") == 3 and before.count("<img") == 1


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")
@pytest.mark.parametrize("figure", [
    r"\begin{figure}\subfigure[M]{\includegraphics{a.png}}\subfigure[L]{\includegraphics{b.png}}"
    r"\caption{C.}\end{figure}",
    r"\begin{figure}\resizebox{0.6\textwidth}{!}{\includegraphics{a.png}}\caption{C.}\end{figure}",
    r"\begin{figure*}\includegraphics{a.png}\caption{Blue \textcolor{b}{\rule{7pt}{7pt}} x.}"
    r"\end{figure*}",
])
def test_pandoc_shows_the_image_once_unwrapped(figure: str, tmp_path: Path) -> None:
    def imgs(body: str) -> int:
        f = tmp_path / "t.tex"
        f.write_text("\\documentclass{article}\n\\begin{document}\n" + body
                     + "\n\\end{document}\n", encoding="utf-8")
        out = subprocess.run(["pandoc", "-f", "latex", "-t", "html5", str(f)],
                             capture_output=True, text=True, encoding="utf-8").stdout
        return out.count("<img")

    assert imgs(figure) == 0          # what the reader showed before
    assert imgs(unwrap_figure_wrappers(figure)) >= 1
