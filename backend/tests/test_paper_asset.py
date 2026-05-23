from pathlib import Path

from paperhub.pipelines.paper_asset import (
    EquationAsset,
    FigureAsset,
    PaperAsset,
    SectionAsset,
    paper_asset_dir,
    read_paper_asset,
    write_paper_asset,
)


def test_roundtrip(tmp_path: Path) -> None:
    asset = PaperAsset(
        figures=[FigureAsset(id="fig-001", caption="The architecture.", page=2,
                             section="Method", image_path="figures/fig-001.png")],
        equations=[EquationAsset(id="eq-001", latex=r"E=mc^2", section="Theory")],
        sections=[SectionAsset(name="Method", order=0)],
    )
    d = paper_asset_dir(tmp_path)  # tmp_path/asset
    (d / "figures").mkdir(parents=True, exist_ok=True)
    (d / "figures" / "fig-001.png").write_bytes(b"\x89PNG")
    write_paper_asset(asset, tmp_path)
    loaded = read_paper_asset(tmp_path)
    assert loaded is not None
    assert loaded.figures[0].caption == "The architecture."
    assert loaded.figures[0].abs_image_path(tmp_path).exists()
    assert loaded.equations[0].latex == "E=mc^2"
    assert loaded.sections[0].name == "Method"


def test_read_missing_returns_none(tmp_path: Path) -> None:
    assert read_paper_asset(tmp_path) is None
