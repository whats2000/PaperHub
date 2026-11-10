"""paperhub-backfill-figure-locations: old assets learn where each figure
sits in the reader; --verify finds any figure a citation could not open."""
from __future__ import annotations

from pathlib import Path

from paperhub.cli.backfill_figure_locations import (
    _match_page,
    html_anchor_problems,
    plan_latex,
)
from paperhub.pipelines.paper_asset import FigureAsset, PaperAsset

_PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00"
        b"\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n"
        b"-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")

FLAT = r"""\begin{figure}\includegraphics{a}\caption{First.}\end{figure}
% \begin{figure}\includegraphics{b}\caption{Gone.}\end{figure}
\begin{figure}\includegraphics{c}\caption{Third.}\end{figure}
"""


def _fig(fid: str, caption: str) -> FigureAsset:
    return FigureAsset(fid, caption, None, None, f"figures/{fid}.png")


def test_plan_latex_records_env_index_and_drops_commented(tmp_path: Path) -> None:
    for n in "abc":
        (tmp_path / f"{n}.png").write_bytes(_PNG)
    old = PaperAsset(figures=[_fig("fig-001", "First."), _fig("fig-002", "Gone."),
                              _fig("fig-003", "Third.")])
    plan = plan_latex(old, tmp_path, FLAT)
    assert plan.status == "updated" and plan.dropped == ["fig-002"]
    assert plan.asset is not None
    assert [(f.id, f.env_index) for f in plan.asset.figures] == [
        ("fig-001", 0), ("fig-003", 2)]
    again = plan_latex(plan.asset, tmp_path, FLAT)
    assert again.status == "unchanged"


def test_rerun_keeps_a_figure_whose_commented_draft_shares_its_caption(
    tmp_path: Path,
) -> None:
    """Paper 62: a commented-out draft carries the same caption as the real
    figure. The first run drops the draft; a re-run must not then match the
    real figure to the draft and drop it too."""
    for n in "ab":
        (tmp_path / f"{n}.png").write_bytes(_PNG)
    flat = ("% \\begin{figure}\\includegraphics{a}\\caption{Same.}\\end{figure}\n"
            "\\begin{figure}\\includegraphics{b}\\caption{Same.}\\end{figure}\n")
    old = PaperAsset(figures=[_fig("fig-001", "Same."), _fig("fig-002", "Same.")])
    first = plan_latex(old, tmp_path, flat)
    assert first.dropped == ["fig-001"] and first.asset is not None
    assert [(f.id, f.env_index) for f in first.asset.figures] == [("fig-002", 1)]
    again = plan_latex(first.asset, tmp_path, flat)
    assert again.status == "unchanged" and again.dropped == []


def test_plan_latex_refuses_a_figure_whose_caption_moved(tmp_path: Path) -> None:
    for n in "abc":
        (tmp_path / f"{n}.png").write_bytes(_PNG)
    old = PaperAsset(figures=[_fig("fig-001", "Something else.")])
    plan = plan_latex(old, tmp_path, FLAT)
    assert plan.status == "mismatch" and plan.asset is None
    assert "fig-001" in plan.problems[0]


def test_match_page_picks_the_blocks_shaped_like_the_images() -> None:
    figs = [_fig("fig-005", ""), _fig("fig-006", "")]
    blocks = [(41.0, 101.0, 305.0, 304.0),    # 264 x 203
              (48.0, 307.0, 299.0, 365.0),    # 251 x 58, a strip: no image
              (305.0, 100.0, 570.0, 296.0)]   # 265 x 196
    picked = _match_page(figs, blocks, [264 / 203, 265 / 196])
    assert picked == [blocks[0], blocks[2]]
    assert _match_page(figs, blocks, [10.0, 10.0]) is None   # nothing fits


def test_html_anchor_problems() -> None:
    figs = [_fig("fig-001", ""), _fig("fig-002", ""), _fig("fig-003", "")]
    html = ('<p><span id="phfig-001"></span></p><figure><img></figure>'
            '<p><span id="phfig-002"></span></p>'
            '<p><span id="phfig-003"></span></p><figure><img></figure>')
    assert html_anchor_problems(html, figs) == [
        "fig-002: anchor not followed by a figure"]
    # pandoc writes figure* as <div class="figure*">.
    star = '<p><span id="phfig-001"></span></p><div class="figure*"><p><img></p></div>'
    assert html_anchor_problems(star, figs[:1]) == []
    assert html_anchor_problems("<figure></figure>", figs[:1]) == [
        "fig-001: no anchor in source.html"]


def test_old_first_panel_only_figure_is_recomposed(tmp_path: Path) -> None:
    from PIL import Image

    from paperhub.cli.backfill_figure_locations import apply_recompose
    from paperhub.pipelines.paper_asset import paper_asset_dir

    for n, color in (("a", (255, 0, 0)), ("b", (0, 0, 255))):
        Image.new("RGB", (200, 200), color).save(tmp_path / f"{n}.png")
    flat = (r"\begin{figure}\subfigure[L]{\includegraphics[width=.45\linewidth]{a}}"
            r"\subfigure[R]{\includegraphics[width=.45\linewidth]{b}}\caption{Both.}"
            r"\end{figure}")
    old = PaperAsset(figures=[_fig("fig-001", "Both.")])   # stored: panel (a) only
    plan = plan_latex(old, tmp_path, flat)
    assert plan.status == "updated" and list(plan.recompose) == ["fig-001"]
    apply_recompose(plan, tmp_path)
    assert plan.asset is not None
    [fig] = plan.asset.figures
    assert fig.panels == 2 and fig.image_path == "figures/fig-001.png"
    im = Image.open(paper_asset_dir(tmp_path) / fig.image_path).convert("RGB")
    assert im.getpixel((500, 150))[0] > 200 and im.getpixel((1100, 150))[2] > 200
    again = plan_latex(plan.asset, tmp_path, flat)
    assert again.status == "unchanged" and not again.recompose


def test_plan_latex_keeps_ids_an_older_ingest_numbered_differently(tmp_path: Path) -> None:
    """Paper 39: an older ingest skipped a commented-out environment, so the
    stored ids run one behind; matching by caption in order keeps them."""
    for n in "abc":
        (tmp_path / f"{n}.png").write_bytes(_PNG)
    old = PaperAsset(figures=[_fig("fig-001", "First."), _fig("fig-002", "Third.")])
    plan = plan_latex(old, tmp_path, FLAT)
    assert plan.status == "updated" and plan.asset is not None
    assert [(f.id, f.env_index) for f in plan.asset.figures] == [
        ("fig-001", 0), ("fig-002", 2)]



SUBFIG = r"""\begin{figure}
\centering
\begin{subfigure}{0.45\linewidth}\includegraphics{a}\caption{FID.}\end{subfigure}
\begin{subfigure}{0.45\linewidth}\includegraphics{b}\caption{IS.}\end{subfigure}
% \caption{An old draft caption.}
\caption{Sample quality of \textbf{our \emph{model}} vs.\ baselines~\cite{ho2020ddpm}.\label{fig:q}}
\end{figure}
\begin{figure}\includegraphics{c}\caption[Short]{Long caption printed in the paper.}\end{figure}
"""

TWO_IN_ONE = r"""\begin{figure}
\begin{minipage}{0.48\linewidth}\includegraphics{a}\caption{Dropped experts vs. loss.}\end{minipage}
\begin{minipage}{0.48\linewidth}\includegraphics{b}\caption{Rate of change in PES.}\end{minipage}
\end{figure}
"""


def test_two_figures_in_one_environment_keep_the_first_caption(tmp_path: Path) -> None:
    """Two side-by-side figures (two top-level captions) are two figures; the
    environment's stored image is the first one, so is its caption."""
    from paperhub.pipelines.latex_to_asset import figure_envs

    for n in "ab":
        (tmp_path / f"{n}.png").write_bytes(_PNG)
    assert [e.main_caption for e in figure_envs(tmp_path, TWO_IN_ONE)] == [
        "Dropped experts vs. loss."]


def test_main_caption_is_the_figures_own_printed_caption(tmp_path: Path) -> None:
    """Not a panel's caption, not a commented draft, nested braces kept,
    citation keys and labels dropped, the long form of \caption[short]{long}."""
    from paperhub.pipelines.latex_to_asset import figure_envs

    for n in "abc":
        (tmp_path / f"{n}.png").write_bytes(_PNG)
    envs = figure_envs(tmp_path, SUBFIG)
    assert [e.main_caption for e in envs] == [
        "Sample quality of our model vs. baselines .",
        "Long caption printed in the paper.",
    ]


def test_migration_rewrites_an_old_panel_caption_and_stays_idempotent(tmp_path: Path) -> None:
    for n in "abc":
        (tmp_path / f"{n}.png").write_bytes(_PNG)
    old = PaperAsset(figures=[_fig("fig-001", "FID."), _fig("fig-002", "Long caption printed in the paper.")])
    plan = plan_latex(old, tmp_path, SUBFIG)
    assert plan.status == "updated" and plan.asset is not None
    assert [(f.id, f.caption) for f in plan.asset.figures] == [
        ("fig-001", "Sample quality of our model vs. baselines ."),
        ("fig-002", "Long caption printed in the paper.")]
    again = plan_latex(plan.asset, tmp_path, SUBFIG)
    # The captions are stable; only the two-panel figure's composite is still
    # due (the CLI applies it), as before this change.
    assert again.asset == plan.asset and list(again.recompose) == ["fig-001"]



def test_main_caption_drops_every_cite_command(tmp_path: Path) -> None:
    from paperhub.pipelines.latex_to_asset import main_caption

    assert main_caption(r"\caption{ORLM \autocite{huang2024orlm}}") == "ORLM"
    assert main_caption(r"\caption{A \parencite[p.~3]{x} and \textcite{y}.}") == "A and ."
