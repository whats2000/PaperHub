"""Figure anchors: PHFIGANCHOR<NNN>END goes before each figure environment
(never inside one, never after it) and becomes <span id="phfig-NNN">."""
from __future__ import annotations

from paperhub.pipelines.latex_to_asset import figure_env_starts
from paperhub.pipelines.sentinels import (
    figure_dom_id,
    figure_token,
    inject_figure_sentinels,
    inject_sentinels,
    postprocess_figure_sentinels,
)

TEX = r"""Intro text.

\begin{figure}\includegraphics{a}\caption{A.}\end{figure}
\begin{figure*}\includegraphics{b}\caption{B with $x^2$.}\end{figure*}
More text \textbf{bold}\begin{figure}\includegraphics{c}\end{figure}
"""


def test_tokens_and_dom_ids() -> None:
    assert figure_token("fig-003") == "PHFIGANCHOR003END"
    assert figure_dom_id("fig-003") == "phfig-003"
    assert figure_token("table-1") is None and figure_dom_id("x") is None


def test_each_anchor_sits_right_before_its_own_figure() -> None:
    starts = figure_env_starts(TEX)
    out, anchored = inject_figure_sentinels(
        TEX, starts, [("fig-001", 0), ("fig-002", 1), ("fig-004", 2)])
    assert anchored == {"fig-001", "fig-002", "fig-004"}
    new_starts = figure_env_starts(out)
    for token, env in (("PHFIGANCHOR001END", 0), ("PHFIGANCHOR002END", 1),
                       ("PHFIGANCHOR004END", 2)):
        at = out.index(token)
        assert at < new_starts[env]                      # before its figure
        assert env == 0 or at > new_starts[env - 1]      # after the previous one
        between = out[at + len(token):new_starts[env]]
        assert "PHFIGANCHOR" not in between              # no other anchor between
    # Only tokens were added.
    stripped = out
    for t in ("PHFIGANCHOR001END", "PHFIGANCHOR002END", "PHFIGANCHOR004END"):
        stripped = stripped.replace(t, "")
    assert stripped == TEX


def test_no_figures_is_a_no_op_and_chunk_sentinels_are_untouched() -> None:
    marked, _ = inject_sentinels(TEX, [0, TEX.index("More text")])
    out, anchored = inject_figure_sentinels(marked, figure_env_starts(marked), [])
    assert out == marked and anchored == set()
    out, _ = inject_figure_sentinels(
        marked, figure_env_starts(marked), [("fig-001", 0)])
    assert out.replace("PHFIGANCHOR001END", "") == marked


def test_anchor_never_lands_in_a_comment_and_fits_after_maketitle() -> None:
    """Paper 17/93: the nearest safe point was the end of a ``%`` line, which
    comments the token out. Paper 62: the figure follows ``\\maketitle``
    directly, so the only place is right before ``\\begin``."""
    tex = ("Intro text.\n\n%%%%%%%%%%%%%%%% a comment line\n"
           "\\begin{figure}\\includegraphics{a}\\end{figure}\n"
           "\\maketitle\n\\begin{figure}\\includegraphics{b}\\end{figure}\n")
    starts = figure_env_starts(tex)
    out, anchored = inject_figure_sentinels(tex, starts, [("fig-001", 0), ("fig-002", 1)])
    assert anchored == {"fig-001", "fig-002"}
    for line in out.splitlines():
        if "PHFIGANCHOR" in line:
            assert "%" not in line.split("PHFIGANCHOR")[0]
    assert "\\maketitle\nPHFIGANCHOR002END\\begin{figure}" in out


def test_postprocess_turns_tokens_into_anchor_spans() -> None:
    html = "<p>x PHFIGANCHOR003END</p><figure><img src='a.png'></figure>"
    out, found = postprocess_figure_sentinels(html)
    assert out == "<p>x <span id=\"phfig-003\"></span></p><figure><img src='a.png'></figure>"
    assert found == {"fig-003"}
