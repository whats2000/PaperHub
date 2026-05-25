from pathlib import Path

from paperhub.pipelines.paper_asset import (
    FigureAsset,
    PaperAsset,
    paper_asset_dir,
    write_paper_asset,
)
from paperhub.pipelines.slide_pipeline.figure_inventory import (
    InventoryFigure,
    build_inventory,
    stage_inventory,
    verify_and_fix_graphics,
)


def _paper(tmp: Path, pid: int, stem: str) -> dict:
    d = tmp / f"p{pid}"
    fa = paper_asset_dir(d) / "figures"
    fa.mkdir(parents=True)
    (fa / f"{stem}.png").write_bytes(b"\x89PNG")
    write_paper_asset(
        PaperAsset(
            figures=[
                FigureAsset(
                    id=stem,
                    caption=f"cap {stem}",
                    page=1,
                    section="M",
                    image_path=f"figures/{stem}.png",
                )
            ]
        ),
        d,
    )
    return {"id": pid, "source_dir": str(d)}


def test_collision_free_keys_and_staging(tmp_path: Path) -> None:
    papers = [_paper(tmp_path, 1, "fig-000"), _paper(tmp_path, 2, "fig-000")]
    inv = build_inventory(papers)
    assert all(isinstance(f, InventoryFigure) for f in inv)
    keys = [f.key for f in inv]
    assert len(set(keys)) == 2
    assert all(k.startswith("p") for k in keys)
    dest = tmp_path / "deck" / "figures"
    stage_inventory(inv, dest)
    for f in inv:
        assert (dest / f"{f.key}.png").exists()


def test_verify_rejects_unknown_graphics(tmp_path: Path) -> None:
    allowed = {"p0-fig-000"}
    tex = (
        r"\includegraphics[width=.8\textwidth]{p0-fig-000}"
        "\n"
        r"\includegraphics{ghost-figure}"
    )
    fixed, rejected = verify_and_fix_graphics(tex, allowed)
    assert "p0-fig-000" in fixed
    assert "ghost-figure" not in fixed
    assert "ghost-figure" in rejected
    assert "[figure omitted" in fixed
