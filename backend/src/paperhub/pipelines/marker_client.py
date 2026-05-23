# backend/src/paperhub/pipelines/marker_client.py
"""HTTP client for the Dockerized Marker extraction service (SRS v2.19 §III-6)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from paperhub.config import load_settings

_TIMEOUT = httpx.Timeout(600.0)  # Marker on a big PDF can take minutes


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

    def extract(self, pdf_bytes: bytes) -> MarkerDoc:
        resp = self._client.post(
            f"{self._base_url}/extract",
            files={"file": ("paper.pdf", pdf_bytes, "application/pdf")},
        )
        resp.raise_for_status()
        return _parse(resp.json())


def get_marker_client() -> MarkerClient:
    return MarkerClient(load_settings().marker_service_url)
