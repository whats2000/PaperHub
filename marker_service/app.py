"""FastAPI wrapper around datalab-to/marker for PaperHub PDF extraction.

Exposes:
  GET  /health   -> {"status": "ok", "models_loaded": bool}
  POST /extract   (multipart `file`) -> {"blocks": [ ...flattened... ]}

The flatten contract (the shape marker_client._parse expects):
  {"blocks": [{block_type, html, latex, section_hierarchy,
               images (name->base64 PNG), bbox, page, caption}, ...]}

Reconciled against the REAL marker JSONOutput schema:
  * Top-level rendered.children == pages; each page.children == blocks; blocks
    nest recursively (a FigureGroup wraps a Figure + a Caption, etc.). So we
    walk the tree recursively.
  * Blocks expose: id, block_type, html, bbox, polygon, children,
    section_hierarchy, images. There is NO `latex` attribute and NO `page`
    attribute on a block: equation LaTeX lives inside the block html, and the
    page number is encoded in the block id (e.g. "/page/0/Block/12").
  * `images` maps a (stringified) block id -> base64-encoded PNG string.
"""
from __future__ import annotations

import re
import tempfile
from typing import Any

from fastapi import FastAPI, File, UploadFile
from marker.config.parser import ConfigParser
from marker.converters.pdf import PdfConverter
from marker.models import create_model_dict

app = FastAPI(title="paperhub-marker")

_models: dict[str, Any] | None = None

# block id looks like "/page/0/Figure/3" -> page 0
_PAGE_RE = re.compile(r"/page/(\d+)/")
# pull LaTeX out of equation html: marker emits <math display="block">...</math>
# wrapping LaTeX, or inline-math spans. We keep the inner text.
_MATH_RE = re.compile(r"<math[^>]*>(.*?)</math>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
# block types that are themselves captions (paired to a nearby figure/table)
_CAPTION_TYPES = {"Caption", "Footnote"}
_FIGURE_TYPES = {"Figure", "Picture"}


def _ensure_models() -> dict[str, Any]:
    global _models
    if _models is None:
        _models = create_model_dict()
    return _models


def _converter() -> PdfConverter:
    cfg = ConfigParser({"output_format": "json", "extract_images": True})
    return PdfConverter(
        config=cfg.generate_config_dict(),
        artifact_dict=_ensure_models(),
        renderer=cfg.get_renderer(),
        processor_list=cfg.get_processors(),
        llm_service=None,
    )


def _page_of(block_id: Any) -> int | None:
    m = _PAGE_RE.search(str(block_id or ""))
    return int(m.group(1)) if m else None


def _latex_from_html(html: str, block_type: str) -> str | None:
    """Equation blocks carry their LaTeX in the html (no `latex` attribute)."""
    if block_type != "Equation":
        return None
    if not html:
        return None
    m = _MATH_RE.search(html)
    inner = m.group(1) if m else html
    text = _TAG_RE.sub("", inner).strip()
    return text or None


def _strip(html: str) -> str:
    return _TAG_RE.sub("", html or "").strip()


def _images_to_dict(images: Any) -> dict[str, str]:
    if not images:
        return {}
    return {str(k): v for k, v in dict(images).items()}


def _flatten(node: Any, out: list[dict[str, Any]]) -> None:
    """Depth-first walk of the JSONOutput tree, emitting one record per block.

    A FigureGroup/TableGroup is a container: we recurse into it but also emit
    its own record so any caption text inside the group html is preserved. The
    caption-pairing (figure <-> nearest Caption) is done downstream in the
    flat list, which is why we preserve document order here.
    """
    block_type = str(getattr(node, "block_type", "") or "")
    block_id = getattr(node, "id", None)
    html = getattr(node, "html", "") or ""
    record = {
        "block_type": block_type,
        "html": html,
        "latex": _latex_from_html(html, block_type),
        "section_hierarchy": getattr(node, "section_hierarchy", {}) or {},
        "images": _images_to_dict(getattr(node, "images", {})),
        "bbox": list(getattr(node, "bbox", []) or []),
        "page": _page_of(block_id),
        "block_id": str(block_id) if block_id is not None else None,
    }
    # Skip the synthetic Document/Page container records (no useful payload)
    if block_type not in ("Document", "Page"):
        out.append(record)
    for child in (getattr(node, "children", None) or []):
        _flatten(child, out)


def _attach_captions(blocks: list[dict[str, Any]]) -> None:
    """Pair each figure with a caption.

    Caption text may live (a) inside the figure block html, (b) as a sibling
    Caption/Footnote block immediately after the figure, or (c) as a child of a
    FigureGroup (already adjacent in our DFS order). We attach the nearest
    following caption block's text to a figure that lacks its own caption.
    """
    for i, b in enumerate(blocks):
        if b["block_type"] not in _FIGURE_TYPES:
            continue
        own = _strip(b["html"])
        if own:
            b["caption"] = own
            continue
        # look ahead a few blocks for a caption on the same page
        for j in range(i + 1, min(i + 4, len(blocks))):
            nxt = blocks[j]
            if nxt["block_type"] in _CAPTION_TYPES and (
                nxt["page"] is None or nxt["page"] == b["page"]
            ):
                b["caption"] = _strip(nxt["html"])
                break
        else:
            b["caption"] = ""


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "models_loaded": _models is not None}


@app.post("/extract")
async def extract(file: UploadFile = File(...)) -> dict[str, Any]:
    data = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
        tf.write(data)
        path = tf.name
    rendered = _converter()(path)
    blocks: list[dict[str, Any]] = []
    _flatten(rendered, blocks)
    _attach_captions(blocks)
    return {"blocks": blocks}
