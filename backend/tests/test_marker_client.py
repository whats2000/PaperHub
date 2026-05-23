import httpx

from paperhub.pipelines.marker_client import MarkerClient, MarkerDoc


def test_extract_parses_marker_json() -> None:
    sample = {"blocks": [
        {"block_type": "SectionHeader", "html": "<h1>Method</h1>", "section_hierarchy": {"1": "Method"}},
        {"block_type": "Figure", "images": {"fig": "<base64png>"}, "html": "<p>Figure 1: arch</p>",
         "bbox": [0, 0, 10, 10], "page": 2},
        {"block_type": "Equation", "html": "<math>E=mc^2</math>", "latex": "E=mc^2"},
    ]}
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/extract"
        return httpx.Response(200, json=sample)
    client = MarkerClient("http://marker:8002", transport=httpx.MockTransport(handler))
    doc: MarkerDoc = client.extract(b"%PDF-1.4 fake")
    assert any(b.block_type == "Figure" for b in doc.blocks)
    assert doc.blocks[2].latex == "E=mc^2"
    assert doc.blocks[1].images == {"fig": "<base64png>"}
    assert doc.blocks[0].section_hierarchy == {"1": "Method"}
