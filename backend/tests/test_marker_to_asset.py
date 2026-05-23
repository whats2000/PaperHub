import base64
from pathlib import Path

from paperhub.pipelines.marker_client import MarkerBlock, MarkerDoc
from paperhub.pipelines.marker_to_asset import marker_doc_to_asset
from paperhub.pipelines.paper_asset import paper_asset_dir


def test_maps_figures_equations_sections(tmp_path: Path) -> None:
    png = base64.b64encode(b"\x89PNG-fake").decode()
    doc = MarkerDoc(blocks=[
        MarkerBlock(block_type="SectionHeader", html="<h1>Method</h1>",
                    section_hierarchy={"1": "Method"}),
        MarkerBlock(block_type="Figure", images={"img": png},
                    html="<p>Figure 1: the architecture diagram.</p>",
                    page=2, section_hierarchy={"1": "Method"}),
        MarkerBlock(block_type="Equation", html="<math/>", latex=r"E=mc^2",
                    section_hierarchy={"1": "Theory"}),
    ])
    asset = marker_doc_to_asset(doc, source_dir=tmp_path)
    assert len(asset.figures) == 1
    f = asset.figures[0]
    assert "architecture" in f.caption.lower()
    assert f.page == 2 and f.section == "Method"
    assert (paper_asset_dir(tmp_path) / f.image_path).exists()
    assert asset.equations[0].latex == "E=mc^2"
    assert any(s.name == "Method" for s in asset.sections)


def test_skips_bad_base64_and_no_image_figures(tmp_path: Path) -> None:
    doc = MarkerDoc(blocks=[
        MarkerBlock(block_type="Figure", images={}, html="<p>caption only</p>", page=1),
        MarkerBlock(block_type="Figure", images={"img": "!!!not-base64!!!"}, html="<p>x</p>", page=1),
    ])
    asset = marker_doc_to_asset(doc, source_dir=tmp_path)
    # no decodable image → no figure assets
    assert asset.figures == []
