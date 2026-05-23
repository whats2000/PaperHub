"""Tests for paperhub.pipelines.slide_pipeline.figures."""
from __future__ import annotations

from pathlib import Path

from paperhub.pipelines.slide_pipeline.figures import (
    FigureIndex,
    collect_figures,
    neutralize_unknown_graphics,
)

# ---------------------------------------------------------------------------
# collect_figures
# ---------------------------------------------------------------------------


def test_collect_figures_finds_image_dirs_and_stems(tmp_path: Path) -> None:
    """Walk: picks up dirs that contain image files and their stems."""
    (tmp_path / "source" / "Figures").mkdir(parents=True)
    (tmp_path / "source" / "vis").mkdir(parents=True)
    (tmp_path / "source" / "Figures" / "model.pdf").write_bytes(b"")
    (tmp_path / "source" / "vis" / "attn.png").write_bytes(b"")

    idx = collect_figures([str(tmp_path)])

    fig_dir = (tmp_path / "source" / "Figures").as_posix()
    vis_dir = (tmp_path / "source" / "vis").as_posix()
    assert fig_dir in idx.dirs
    assert vis_dir in idx.dirs
    assert "model" in idx.stems
    assert "attn" in idx.stems


def test_collect_figures_dirs_are_forward_slashed(tmp_path: Path) -> None:
    """Dirs in the index must be forward-slashed (posix), never backslash."""
    (tmp_path / "Figures").mkdir(parents=True)
    (tmp_path / "Figures" / "fig1.eps").write_bytes(b"")

    idx = collect_figures([str(tmp_path)])

    for d in idx.dirs:
        assert "\\" not in d, f"Backslash found in dir: {d!r}"


def test_collect_figures_dirs_are_sorted(tmp_path: Path) -> None:
    """dirs list is deterministically sorted."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "x.png").write_bytes(b"")
    (tmp_path / "b" / "y.png").write_bytes(b"")

    idx = collect_figures([str(tmp_path)])

    assert idx.dirs == sorted(idx.dirs)


def test_collect_figures_nonexistent_dir_skipped() -> None:
    """A non-existent path must not crash; it is just skipped."""
    idx = collect_figures(["/no/such/path/abc123"])
    assert idx.dirs == []
    assert idx.stems == set()


def test_collect_figures_empty_input() -> None:
    idx = collect_figures([])
    assert isinstance(idx, FigureIndex)
    assert idx.dirs == []
    assert idx.stems == set()


def test_collect_figures_ignores_non_image_files(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_text("a,b")
    (tmp_path / "readme.txt").write_text("hi")
    (tmp_path / "fig.png").write_bytes(b"")

    idx = collect_figures([str(tmp_path)])

    # only the image dir is collected
    assert tmp_path.as_posix() in idx.dirs
    assert "fig" in idx.stems
    assert "data" not in idx.stems
    assert "readme" not in idx.stems


def test_collect_figures_multiple_roots(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    (root_a / "figs").mkdir(parents=True)
    (root_b / "images").mkdir(parents=True)
    (root_a / "figs" / "alpha.jpg").write_bytes(b"")
    (root_b / "images" / "beta.svg").write_bytes(b"")

    idx = collect_figures([str(root_a), str(root_b)])

    assert "alpha" in idx.stems
    assert "beta" in idx.stems


def test_collect_figures_extension_case_insensitive(tmp_path: Path) -> None:
    (tmp_path / "Fig.PNG").write_bytes(b"")

    idx = collect_figures([str(tmp_path)])

    assert "Fig" in idx.stems


# ---------------------------------------------------------------------------
# neutralize_unknown_graphics
# ---------------------------------------------------------------------------


def test_neutralize_keeps_known_figure() -> None:
    tex = r"\includegraphics{model}"
    out = neutralize_unknown_graphics(tex, known_stems={"model"})
    assert out == tex


def test_neutralize_replaces_unknown_figure() -> None:
    tex = r"\includegraphics[width=.5\textwidth]{ghost}"
    out = neutralize_unknown_graphics(tex, known_stems={"model"})
    assert r"\includegraphics" not in out
    assert "ghost" in out
    assert r"\textit{[figure omitted: ghost]}" in out


def test_neutralize_keeps_known_by_stem_with_extension() -> None:
    tex = r"\includegraphics{model.pdf}"
    out = neutralize_unknown_graphics(tex, known_stems={"model"})
    assert out == tex


def test_neutralize_mixed_known_and_unknown() -> None:
    tex = (
        r"\includegraphics{model}" + "\n"
        + r"\includegraphics[width=1cm]{ghost}" + "\n"
        + r"\includegraphics{attn.png}"
    )
    out = neutralize_unknown_graphics(tex, known_stems={"model", "attn"})
    assert r"\includegraphics{model}" in out
    assert r"\includegraphics{attn.png}" in out
    assert "ghost" in out
    assert r"\textit{[figure omitted: ghost]}" in out


def test_neutralize_empty_known_set_replaces_all() -> None:
    tex = r"\includegraphics{anything}"
    out = neutralize_unknown_graphics(tex, known_stems=set())
    assert r"\includegraphics" not in out
    assert r"\textit{[figure omitted: anything]}" in out


def test_neutralize_no_includegraphics_unchanged() -> None:
    tex = r"\begin{frame}{Title}\item hello\end{frame}"
    out = neutralize_unknown_graphics(tex, known_stems={"fig"})
    assert out == tex
