# backend/src/paperhub/pipelines/marker_client.py
"""HTTP client for the Dockerized Marker extraction service (SRS v2.19 §III-6)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import pymupdf

from paperhub.config import load_settings

# A single DENSE two-column page (200+ OCR text lines) on a VRAM-starved 6 GB
# GPU can take many minutes; the read timeout must clear that worst case.
_TIMEOUT = httpx.Timeout(1800.0)


@dataclass
class MarkerBlock:
    block_type: str
    html: str = ""
    latex: str | None = None
    section_hierarchy: dict[str, str] = field(default_factory=dict)
    images: dict[str, str] = field(default_factory=dict)  # name -> base64 PNG
    bbox: list[float] = field(default_factory=list)
    page: int | None = None
    # Caption text resolved by the marker service: a figure's caption may live
    # in a sibling Caption/Footnote block rather than the figure block's own
    # html, so the service pairs them and writes the result here.
    caption: str | None = None
    # Marker block id, e.g. "/page/2/Figure/0". section_hierarchy VALUES are
    # block-id refs to SectionHeader blocks (NOT names), so the mapper resolves
    # names via a {block_id -> SectionHeader text} map keyed on this.
    block_id: str | None = None


@dataclass
class MarkerDoc:
    blocks: list[MarkerBlock]


def _parse(payload: dict[str, Any]) -> MarkerDoc:
    blocks = [
        MarkerBlock(
            block_type=str(b.get("block_type", "")),
            html=str(b.get("html", "")),
            latex=b.get("latex"),
            section_hierarchy=b.get("section_hierarchy") or {},
            images=b.get("images") or {},
            bbox=b.get("bbox") or [],
            page=b.get("page"),
            caption=b.get("caption"),
            block_id=b.get("block_id"),
        )
        for b in payload.get("blocks", [])
    ]
    return MarkerDoc(blocks=blocks)


class MarkerClient:
    def __init__(self, base_url: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=_TIMEOUT, transport=transport)

    def extract(self, pdf_bytes: bytes, *, max_pages: int | None = None) -> MarkerDoc:
        """Extract a PDF via the Marker service.

        When ``max_pages`` is ``None`` (the default), the whole PDF is sent in
        a single ``/extract`` call (legacy behavior). When set, the PDF is
        split into page-index batches of ``max_pages`` and each batch is POSTed
        with a ``page_range`` form field; the returned blocks are CONCATENATED
        in batch order. Marker's ``page_range`` keeps ABSOLUTE page numbers +
        block ids, so concatenation needs no renumbering. Batching keeps each
        call under a small GPU's VRAM, avoiding Marker's per-stage model
        hot-swap (the ~30 min slow path on a 15-page PDF / 6 GB GPU).
        """
        if max_pages is None or max_pages <= 0:
            return self._extract_one(pdf_bytes, page_range=None)

        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:  # type: ignore[no-untyped-call]
            page_count = doc.page_count
        if page_count <= 0:
            return self._extract_one(pdf_bytes, page_range=None)

        merged: list[MarkerBlock] = []
        for start in range(0, page_count, max_pages):
            end = min(start + max_pages, page_count)
            indices = list(range(start, end))
            batch = self._extract_one(pdf_bytes, page_range=indices)
            merged.extend(batch.blocks)
        return MarkerDoc(blocks=merged)

    def _extract_one(
        self, pdf_bytes: bytes, *, page_range: list[int] | None,
    ) -> MarkerDoc:
        data = None
        if page_range is not None:
            data = {"page_range": ",".join(str(i) for i in page_range)}
        resp = self._client.post(
            f"{self._base_url}/extract",
            files={"file": ("paper.pdf", pdf_bytes, "application/pdf")},
            data=data,
        )
        resp.raise_for_status()
        return _parse(resp.json())


def get_marker_client() -> MarkerClient:
    return MarkerClient(load_settings().marker_service_url)
