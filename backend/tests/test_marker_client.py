import httpx
import pymupdf

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


def _make_pdf(num_pages: int) -> bytes:
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {i}")
    data: bytes = doc.tobytes()
    doc.close()
    return data


def test_extract_batches_pages_and_concatenates() -> None:
    pdf_bytes = _make_pdf(12)
    seen_ranges: list[str | None] = []
    call_count = {"n": 0}

    def _read_page_range(req: httpx.Request) -> str | None:
        # Parse the multipart body for the page_range form field.
        body = req.content
        marker = b'name="page_range"\r\n\r\n'
        idx = body.find(marker)
        if idx == -1:
            return None
        start = idx + len(marker)
        end = body.find(b"\r\n", start)
        return body[start:end].decode()

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/extract"
        pr = _read_page_range(req)
        seen_ranges.append(pr)
        n = call_count["n"]
        call_count["n"] += 1
        # Return a distinct block per batch so we can verify concatenation.
        return httpx.Response(200, json={"blocks": [
            {"block_type": "Text", "html": f"<p>batch {n}</p>"},
        ]})

    client = MarkerClient("http://marker:8002", transport=httpx.MockTransport(handler))
    doc = client.extract(pdf_bytes, max_pages=5)

    # 12 pages / 5 -> 3 batches: 0-4, 5-9, 10-11
    assert call_count["n"] == 3
    assert seen_ranges == ["0,1,2,3,4", "5,6,7,8,9", "10,11"]
    # Merged doc concatenates all returned blocks in batch order.
    assert [b.html for b in doc.blocks] == [
        "<p>batch 0</p>", "<p>batch 1</p>", "<p>batch 2</p>",
    ]


def test_extract_single_call_when_max_pages_none() -> None:
    pdf_bytes = _make_pdf(3)
    calls = {"n": 0, "had_page_range": False}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        calls["had_page_range"] = b'name="page_range"' in req.content
        return httpx.Response(200, json={"blocks": [
            {"block_type": "Text", "html": "<p>whole doc</p>"},
        ]})

    client = MarkerClient("http://marker:8002", transport=httpx.MockTransport(handler))
    doc = client.extract(pdf_bytes)  # max_pages=None (default)

    assert calls["n"] == 1
    assert calls["had_page_range"] is False
    assert [b.html for b in doc.blocks] == ["<p>whole doc</p>"]
