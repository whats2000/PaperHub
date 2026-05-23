import base64
import json
from pathlib import Path

from paperhub.pipelines.marker_client import MarkerBlock, MarkerDoc, _parse
from paperhub.pipelines.marker_to_asset import marker_doc_to_asset
from paperhub.pipelines.paper_asset import paper_asset_dir

_FIXTURE = Path(__file__).parent / "fixtures" / "marker_doc.json"


def test_maps_figures_equations_sections(tmp_path: Path) -> None:
    # Real Marker schema: section_hierarchy VALUES are block-id refs to
    # SectionHeader blocks (not names); figure captions arrive in `caption`.
    png = base64.b64encode(b"\x89PNG-fake").decode()
    doc = MarkerDoc(blocks=[
        MarkerBlock(block_type="SectionHeader", html="<h1>Method</h1>",
                    block_id="/page/0/SectionHeader/1",
                    section_hierarchy={"1": "/page/0/SectionHeader/1"}),
        MarkerBlock(block_type="Figure", images={"img": png},
                    caption="Figure 1: the architecture diagram.", page=2,
                    section_hierarchy={"1": "/page/0/SectionHeader/1"}),
        MarkerBlock(block_type="Equation", html="<math/>", latex=r"E=mc^2",
                    section_hierarchy={"1": "/page/0/SectionHeader/1"}),
    ])
    asset = marker_doc_to_asset(doc, source_dir=tmp_path)
    assert len(asset.figures) == 1
    f = asset.figures[0]
    assert "architecture" in f.caption.lower()
    assert f.page == 2 and f.section == "Method"  # resolved from the block-id ref
    assert (paper_asset_dir(tmp_path) / f.image_path).exists()
    assert asset.equations[0].latex == "E=mc^2"
    assert asset.equations[0].section == "Method"
    assert any(s.name == "Method" for s in asset.sections)


def test_skips_bad_base64_and_no_image_figures(tmp_path: Path) -> None:
    doc = MarkerDoc(blocks=[
        MarkerBlock(block_type="Figure", images={}, html="<p>caption only</p>", page=1),
        MarkerBlock(block_type="Figure", images={"img": "!!!not-base64!!!"}, html="<p>x</p>", page=1),
    ])
    asset = marker_doc_to_asset(doc, source_dir=tmp_path)
    assert asset.figures == []  # no decodable image → no figure assets


def test_real_fixture_maps(tmp_path: Path) -> None:
    """Reality check: map a REAL marker /extract response (captured live).

    Proves the mapper handles the actual schema — caption in the `caption`
    field, section_hierarchy as block-id refs resolved to titles, JPEG images.
    """
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    doc = _parse(payload)
    asset = marker_doc_to_asset(doc, source_dir=tmp_path)

    # The Transformer architecture figure, caption resolved by the service.
    assert any("Transformer - model architecture" in f.caption for f in asset.figures)
    fig = asset.figures[0]
    # section_hierarchy is a block-id REF in real Marker → resolved to the title.
    assert fig.section == "3 Model Architecture"
    # The real figure image is JPEG → .jpg extension (not a hard-coded .png).
    assert fig.image_path.endswith(".jpg")
    assert (paper_asset_dir(tmp_path) / fig.image_path).exists()
    # Sections come from the real SectionHeader blocks, in document order.
    names = [s.name for s in asset.sections]
    assert "Attention Is All You Need" in names
    assert "3 Model Architecture" in names
