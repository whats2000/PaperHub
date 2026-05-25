from pathlib import Path

from paperhub.pipelines.latex_to_asset import latex_source_to_asset
from paperhub.pipelines.paper_asset import paper_asset_dir


def test_extracts_figure_caption_and_equation(tmp_path: Path) -> None:
    src = tmp_path / "source"
    (src / "figs").mkdir(parents=True)
    # a real raster figure file (PNG) so no rasterize needed for this case
    (src / "figs" / "arch.png").write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    flattened = r"""
\section{Method}
\begin{figure}\includegraphics[width=\textwidth]{figs/arch}\caption{The architecture diagram.}\end{figure}
\begin{equation} E = mc^2 \end{equation}
\section{Results}
"""
    asset = latex_source_to_asset(src, flattened, source_dir=tmp_path)
    assert any("architecture" in f.caption.lower() for f in asset.figures)
    fig = asset.figures[0]
    assert fig.section == "Method"
    assert (paper_asset_dir(tmp_path) / fig.image_path).exists()  # staged copy
    assert any("mc^2" in e.latex for e in asset.equations)
    assert [s.name for s in asset.sections][:2] == ["Method", "Results"]


def test_missing_figure_file_is_skipped(tmp_path: Path) -> None:
    src = tmp_path / "source"
    src.mkdir()
    flattened = r"\section{X}\begin{figure}\includegraphics{nonexistent}\caption{c}\end{figure}"
    asset = latex_source_to_asset(src, flattened, source_dir=tmp_path)
    assert asset.figures == []          # can't resolve the file → no figure asset
    assert [s.name for s in asset.sections] == ["X"]
