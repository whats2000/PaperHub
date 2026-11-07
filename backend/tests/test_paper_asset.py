import contextlib
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


def test_block_assets_round_trip(tmp_path: Path) -> None:
    from paperhub.pipelines.paper_asset import (
        BlockAsset,
        read_block_assets,
        write_block_assets,
    )

    assert read_block_assets(tmp_path) is None
    blocks = [
        BlockAsset("/page/0/Text/1", "Text", 0, (1.0, 2.0, 3.0, 4.5), "<p>hi</p>", "Intro"),
        BlockAsset("/page/1/PageFooter/2", "PageFooter", 1, (0.0, 0.0, 1.0, 1.0), "2", None),
    ]
    write_block_assets(tmp_path, blocks, inline_math_llm=True)
    assert read_block_assets(tmp_path) == (blocks, True)
    write_block_assets(tmp_path, [], inline_math_llm=False)
    assert read_block_assets(tmp_path) == ([], False)


def test_truncated_blocks_json_reads_as_missing(tmp_path: Path) -> None:
    from paperhub.pipelines.paper_asset import paper_asset_dir, read_block_assets

    d = paper_asset_dir(tmp_path)
    d.mkdir(parents=True)
    (d / "blocks.json").write_text('{"inline_math_llm": tr', encoding="utf-8")
    assert read_block_assets(tmp_path) is None


def test_write_block_assets_is_atomic(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import os

    from paperhub.pipelines.paper_asset import (
        BlockAsset,
        paper_asset_dir,
        read_block_assets,
        write_block_assets,
    )

    b = [BlockAsset("a", "Text", 0, (0.0, 0.0, 1.0, 1.0), "x", None)]
    write_block_assets(tmp_path, b, inline_math_llm=False)

    def boom(src, dst):  # type: ignore[no-untyped-def]
        raise OSError("killed")
    monkeypatch.setattr(os, "replace", boom)
    with contextlib.suppress(OSError):
        write_block_assets(tmp_path, [], inline_math_llm=True)
    assert read_block_assets(tmp_path) == (b, False)  # old file intact
    assert [p.name for p in paper_asset_dir(tmp_path).iterdir()] == ["blocks.json"]


def test_read_paper_asset_tolerates_unknown_keys_and_imageless_figures(
    tmp_path: Path,
) -> None:
    """A figures.json written by another tool can carry extra keys (e.g.
    ``kind``) and figures with no rendered image (``image_path == ""``).
    The reader keeps the usable figures instead of raising TypeError."""
    import json

    from paperhub.pipelines.paper_asset import FigureAsset, paper_asset_dir, read_paper_asset

    d = paper_asset_dir(tmp_path)
    d.mkdir(parents=True)
    (d / "figures.json").write_text(json.dumps([
        {"id": "fig-001", "caption": "tikz roadmap", "page": None,
         "section": "Intro", "image_path": "", "kind": "tikz"},
        {"id": "fig-002", "caption": "MoE", "page": None,
         "section": "Basics", "image_path": "figures/fig-002.png", "kind": "raster"},
        {"id": "fig-003", "caption": "missing keys"},
    ]), encoding="utf-8")
    asset = read_paper_asset(tmp_path)
    assert asset is not None
    assert asset.figures == [
        FigureAsset("fig-002", "MoE", None, "Basics", "figures/fig-002.png"),
    ]


def test_read_paper_asset_ignores_unknown_section_and_equation_keys(
    tmp_path: Path,
) -> None:
    import json

    from paperhub.pipelines.paper_asset import (
        EquationAsset,
        SectionAsset,
        paper_asset_dir,
        read_paper_asset,
    )

    d = paper_asset_dir(tmp_path)
    d.mkdir(parents=True)
    (d / "figures.json").write_text("[]", encoding="utf-8")
    (d / "structure.json").write_text(json.dumps(
        [{"name": "Intro", "order": 0, "depth": 1}]), encoding="utf-8")
    (d / "equations.json").write_text(json.dumps(
        [{"id": "eq-1", "latex": "x", "section": None, "label": "e1"}]), encoding="utf-8")
    asset = read_paper_asset(tmp_path)
    assert asset is not None
    assert asset.sections == [SectionAsset("Intro", 0)]
    assert asset.equations == [EquationAsset("eq-1", "x", None)]
