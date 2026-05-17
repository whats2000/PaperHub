"""FastMCP server wrapping kermitt2/grobid-client-python.

Exposes two tools:
  * ``process_header``  — extract title, authors, abstract from a PDF.
  * ``process_fulltext`` — extract structured full text from a PDF.

Both tools validate that the PDF path is inside the workspace root before
forwarding to GROBID (defence-in-depth alongside the scope-checker gate in
:mod:`paperhub.mcp.client`).

Fallback behaviour
------------------
If GROBID is unreachable (``requests.ConnectionError`` or any HTTP error
response), the tools return a minimal stub TEI XML structure so Phase A's
paper-import flow can continue with PyMuPDF-only metadata instead of crashing.

Usage
-----
This module exposes a ``grobid_mcp`` FastMCP application object.  Callers
mount it as an in-process MCP server via::

    from paperhub.mcp.tools.grobid_server import grobid_mcp
    # … attach to a stdio transport or call via the mcp SDK test client.
"""

from __future__ import annotations

import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP

log = logging.getLogger(__name__)

grobid_mcp = FastMCP("grobid")

# ---------------------------------------------------------------------------
# Stub TEI returned when GROBID is unreachable
# ---------------------------------------------------------------------------

_STUB_TEI = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt><title level="a" type="main">UNAVAILABLE (GROBID unreachable)</title></titleStmt>
    </fileDesc>
  </teiHeader>
  <text><body><p>GROBID unreachable. Falling back to PyMuPDF metadata.</p></body></text>
</TEI>
"""


def _workspace_root() -> Path:
    """Return the workspace root from settings (lazy import to avoid circularity)."""
    try:
        from paperhub.config import Settings

        settings = Settings()
        return Path(settings.workspace_root)
    except Exception:
        # If settings aren't available (e.g. tests), use CWD as a safe default.
        return Path.cwd()


def _validate_pdf_path(pdf_path: str) -> Path:
    """Resolve path and assert it is inside the workspace root.

    Raises
    ------
    ValueError
        If the path is outside the workspace root.
    """
    path = Path(pdf_path).resolve()
    root = _workspace_root().resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"PDF path {pdf_path!r} is outside the workspace root {root}") from exc
    return path


def _call_grobid(method: str, pdf_path: Path) -> str:
    """Call grobid-client-python and return TEI XML string.

    Returns a stub TEI on any connection / HTTP failure.
    """
    try:
        # grobid-client-python uses a synchronous requests-based API.
        # The package has no py.typed marker; suppress the missing-stubs error.
        import grobid_client.grobid_client as _gc  # type: ignore[import-untyped]

        from paperhub.config import get_settings

        settings = get_settings()
        client = _gc.GrobidClient(grobid_server=settings.grobid_url)
        # processHeaderDocument / processFulltextDocument accept a single file path
        service = (
            "processHeaderDocument" if method == "process_header" else "processFulltextDocument"
        )
        _, status, result = client.process_pdf(
            service=service,
            pdf_file=str(pdf_path),
            generateIDs=False,
            consolidate_header=False,
            consolidate_citations=False,
            include_raw_affiliations=False,
            include_raw_citations=False,
            segment_sentences=False,
        )
        if status != 200 or result is None:
            log.warning("GROBID returned status %s for %s; using stub", status, pdf_path)
            return _STUB_TEI
        return str(result)
    except Exception as exc:
        log.warning("GROBID unreachable (%s); using stub TEI for %s", exc, pdf_path)
        return _STUB_TEI


@grobid_mcp.tool()
def process_header(pdf_path: str) -> str:
    """Extract title, authors, abstract from a PDF using GROBID.

    Parameters
    ----------
    pdf_path:
        Absolute path to the PDF file (must be inside the workspace root).

    Returns
    -------
    str
        TEI XML string with ``<teiHeader>`` populated, or a stub on failure.
    """
    path = _validate_pdf_path(pdf_path)
    return _call_grobid("process_header", path)


@grobid_mcp.tool()
def process_fulltext(pdf_path: str) -> str:
    """Extract structured full text from a PDF using GROBID.

    Parameters
    ----------
    pdf_path:
        Absolute path to the PDF file (must be inside the workspace root).

    Returns
    -------
    str
        TEI XML string with full ``<text>`` body, or a stub on failure.
    """
    path = _validate_pdf_path(pdf_path)
    return _call_grobid("process_fulltext", path)
