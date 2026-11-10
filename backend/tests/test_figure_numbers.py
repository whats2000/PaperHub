"""Figure references carry the paper's own figure numbers."""
from __future__ import annotations

from paperhub.pipelines.figure_numbers import figure_label_numbers, renumber_figure_refs

TEX = r"""
\begin{figure}\includegraphics{a}\caption{One.}\label{fig:one}\end{figure}
\begin{figure*}\subfigure[A\label{fig:sub}]{\includegraphics{b}}\caption{Two.}\label{fig:two}
\end{figure*}
% \begin{figure}\caption{Commented.}\label{fig:gone}\end{figure}
\begin{figure}\includegraphics{c}\end{figure}
\begin{figure}\label{fig:early}\begin{subfigure}{.5\linewidth}\includegraphics{d}
\caption{panel}\label{fig:panel}\end{subfigure}\caption{Three.}\end{figure}
\begin{table}\caption{T.}\label{tab:t}\end{table}
\setcounter{figure}{0}
\begin{figure}\includegraphics{e}\caption{A1.}\label{fig:appendix}\end{figure}
"""


def test_numbers_follow_latex_captions() -> None:
    assert figure_label_numbers(TEX) == {
        "fig:one": "1", "fig:two": "2", "fig:early": "3", "fig:appendix": "1",
        "fig:sub": "2(a)",      # \subfigure panel (subfigure package style)
        "fig:panel": "3a",      # subfigure environment (subcaption style)
    }


def test_nested_floats_and_subfloat_panels() -> None:
    """Paper 31: a table / algorithm inside a figure counts on its own
    counter. Paper 39: \\subfloat panels read ``4a`` (subfig), not ``4(a)``."""
    tex = (r"\begin{figure}\begin{algorithm}\caption{Alg.}\label{alg:a}\end{algorithm}"
           r"\begin{table}\caption{Tab.}\label{tab:t}\end{table}\end{figure}"
           r"\begin{figure}\subfloat[x]{\includegraphics{a}\label{fig:pa}}"
           r"\subfloat[y]{\includegraphics{b}\label{fig:pb}}\caption{F.}\label{fig:f}"
           r"\end{figure}")
    assert figure_label_numbers(tex) == {"fig:f": "1", "fig:pa": "1a", "fig:pb": "1b"}


def test_captionof_table_labels_are_not_figures() -> None:
    tex = (r"\begin{figure}\includegraphics{a}\caption{F.}\label{fig:f}"
           r"\captionof{table}{T.}\label{tab:t}\end{figure}")
    assert figure_label_numbers(tex) == {"fig:f": "1"}


def test_only_figure_references_are_renumbered() -> None:
    html = ('<a href="#fig:two" data-reference-type="ref" data-reference="fig:two">5</a> '
            '<a href="#tab:t" data-reference-type="ref" data-reference="tab:t">1</a> '
            '<a href="#fig:sub" data-reference-type="ref" data-reference="fig:sub">[fig:sub]</a>')
    out = renumber_figure_refs(html, figure_label_numbers(TEX))
    assert 'data-reference="fig:two">2</a>' in out
    assert 'data-reference="tab:t">1</a>' in out          # tables untouched
    assert 'data-reference="fig:sub">2(a)</a>' in out      # a panel: N(a)
