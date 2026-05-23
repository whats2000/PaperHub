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

import os
import re
import tempfile
from typing import Any

from fastapi import FastAPI, File, Form, UploadFile
from marker.config.parser import ConfigParser
from marker.converters.pdf import PdfConverter
from marker.models import create_model_dict

app = FastAPI(title="paperhub-marker")

_models: dict[str, Any] | None = None


def _gemini_key() -> str | None:
    """Gemini key from env (GEMINI_API_KEY preferred, GOOGLE_API_KEY accepted)."""
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def _use_llm() -> bool:
    return bool(_gemini_key())

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


def _parse_page_range(page_range: str | None) -> list[int] | None:
    """Parse a page_range form field into the list Marker expects.

    Accepts a comma-separated list of page indices ("0,1,2,3,4") OR a
    "start-end" inclusive range ("0-4"). Returns None when absent/blank
    (whole-document behavior).
    """
    if not page_range:
        return None
    text = page_range.strip()
    if not text:
        return None
    if "-" in text and "," not in text:
        start_s, _, end_s = text.partition("-")
        start, end = int(start_s.strip()), int(end_s.strip())
        return list(range(start, end + 1))
    return [int(part.strip()) for part in text.split(",") if part.strip()]


def _converter(page_range: list[int] | None = None) -> PdfConverter:
    # Low-VRAM + trust-text-layer config (SRS v2.19 Plan F2.1).
    #
    # PaperHub's PDFs are digital-born (clean embedded text layer). On a 6 GB
    # GPU, a dense two-column page makes Marker's LineBuilder flag the page for
    # Surya re-OCR, triggering the line-level RECOGNITION pass ("Recognizing
    # Text: 0/211") which fills ~5.9 GB and crashes the worker.
    #
    #   force_ocr   -> left UNSET (defaults False): we must NOT force OCR.
    #   disable_ocr -> True: marker's LineBuilder sets `provider_lines_good =
    #     True` for every page, so NO page is flagged
    #     `text_extraction_method='surya'` and the text RECOGNITION pass (the
    #     VRAM spike) is SKIPPED — the PDF's pdftext layer is trusted instead.
    #     Crucially this only stops text *recognition*; line DETECTION, LAYOUT
    #     analysis, and the figure/equation/table processors still run, so the
    #     value-add (real figures+captions, equations->LaTeX, sections) is kept.
    #     Source: marker/builders/line.py (`if self.disable_ocr: provider_lines
    #     _good = True`) + marker/builders/ocr.py (only pages with
    #     text_extraction_method=='surya' are OCR'd).
    cfg_dict: dict[str, Any] = {
        "output_format": "json",
        "extract_images": True,
        "disable_ocr": True,
    }
    if page_range is not None:
        # Marker's ConfigParser expects page_range as a comma-separated STRING
        # (it calls parse_range_str(v) -> v.split(",")); passing a list[int]
        # raises "AttributeError: 'list' object has no attribute 'split'".
        cfg_dict["page_range"] = ",".join(str(p) for p in page_range)
    # use_llm + a Gemini service materially improves table/math/layout
    # accuracy. Enabled only when a Gemini API key is present in the env, so
    # the service still runs keyless (current behavior).
    key = _gemini_key()
    if key:
        cfg_dict["use_llm"] = True
        cfg_dict["gemini_api_key"] = key
    cfg = ConfigParser(cfg_dict)
    return PdfConverter(
        config=cfg.generate_config_dict(),
        artifact_dict=_ensure_models(),
        renderer=cfg.get_renderer(),
        processor_list=cfg.get_processors(),
        llm_service=cfg.get_llm_service() if key else None,
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
    return {
        "status": "ok",
        "models_loaded": _models is not None,
        "use_llm": _use_llm(),
    }


@app.post("/extract")
async def extract(
    file: UploadFile = File(...),
    page_range: str | None = Form(default=None),
) -> dict[str, Any]:
    data = await file.read()
    pages = _parse_page_range(page_range)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
        tf.write(data)
        path = tf.name
    rendered = _converter(pages)(path)
    blocks: list[dict[str, Any]] = []
    _flatten(rendered, blocks)
    _attach_captions(blocks)
    return {"blocks": blocks}
