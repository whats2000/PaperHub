# Plan F2 — Marker-based ingestion + unified `PaperAsset` (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace crude PyMuPDF PDF extraction with a Dockerized **Marker** service that yields real figures+captions, LaTeX equations, and structured sections; normalize both PDF (Marker) and arXiv (LaTeX source) ingestion into one **`PaperAsset`** contract cached per paper; re-derive RAG chunks + the Citation Canvas HTML from that structured output.

**Architecture:** A new docker-compose `marker` service (FastAPI wrapper around `datalab-to/marker`'s `PdfConverter`) is called by the Paper Pipeline over HTTP (thin client, mirroring the `modelserver` `_HttpEmbedder` pattern). PDF papers go through Marker; arXiv papers keep the LaTeX-source path, extended to emit the same `PaperAsset`. `PaperAsset` is **file-based** under `papers_cache/<key>/asset/` (`figures/*.png` + `figures.json`, `equations.json`, `structure.json`) so it survives `paper_content` cache-hits and is read directly by the F3 slide agent. No DB schema change — the asset bundle is located via the existing `paper_content.source_dir_path`.

**Tech Stack:** Python 3.11 + `uv`, FastAPI, `datalab-to/marker` (Docker), Docker Compose, httpx, aiosqlite, PyMuPDF (arXiv figure rasterize only), pandoc.

**Spec:** SRS v2.19 — §III-5.1 (structured extraction → `PaperAsset`), §III-6 (`marker` compose service). **This plan is F2; F3 (the PhD slide agent) consumes `PaperAsset` and is a separate plan.**

**Conventions:** TDD. From `backend/`: `uv run pytest`, `uv run ruff check src tests`, `uv run mypy src`. Tests set `PAPERHUB_INPROCESS_MODELS=1` (conftest) and must **mock the Marker HTTP client** (no real Docker/Marker in unit tests). Conventional Commits; body wraps 72 cols; every commit ends with `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`. Provenance comment on any copied reference code.

**Key current-code facts (from the F2 exploration):**
- `PaperPipeline.ingest(IngestRequest)` in `backend/src/paperhub/pipelines/paper_pipeline.py`; arXiv path `_ingest_arxiv`, PDF/LaTeX path `_ingest_upload`; persistence `_persist_paper_content_and_chunks(...)`; section JSON via `PaperPipeline._build_sections_json(chunks, full_text, strip_comments=)`.
- PDF text today: `extract_pdf_with_headings(pdf_path) -> (full_text, headings)` in `pipelines/extract.py`; chunker `chunk_text(text, *, sections=, strip_comments=)` → `list[Chunk]` (`Chunk(section, char_start, char_end, text, dom_id)`).
- arXiv: `download_arxiv_source(arxiv_id, cache_root=)` → `source/` dir; `extract_latex(source_dir) -> LatexExtract(main_path, flattened_text)`; figures via `rasterize_and_normalize_figures(tex, resource_dir)`.
- HTML render: `render_html(*, source, kind, out_path, resource_dir=)` (pandoc for latex, PyMuPDF for pdf) → `paper_content.html_path`.
- Modelserver pattern to mirror: `modelserver/server.py` (FastAPI `/health` `/embed`), `pipelines/embedder.py` `_HttpEmbedder` + `get_embedder()` factory dispatching on `settings.inprocess_models`.
- Config: `config.py` `Settings` (frozen dataclass) + `load_settings()` (env-var defaults).
- Cache dirs: `papers_cache/arxiv/<id>/` and `papers_cache/upload/<sha>/`.
- Tests: `FakeEmbedder` + `pipeline_env` fixture in `test_paper_pipeline.py`; `patch("paperhub.pipelines.paper_pipeline.download_arxiv_source", ...)`.

---

## File Structure

**New — Marker service (top-level, compose-managed):**
- `docker-compose.yml` (repo root) — the `marker` service (+ a placeholder for backend/modelserver to follow as the project goes full-Docker).
- `marker_service/Dockerfile` — Marker + FastAPI image (models baked in).
- `marker_service/app.py` — FastAPI app: `GET /health`, `POST /extract`.
- `marker_service/requirements.txt` — `marker-pdf`, `fastapi`, `uvicorn`.

**New — backend:**
- `backend/src/paperhub/pipelines/paper_asset.py` — `PaperAsset` + `FigureAsset`/`EquationAsset`/`SectionAsset` dataclasses + `write_paper_asset(asset, dir)` / `read_paper_asset(dir)` / `paper_asset_dir(source_dir_path)`.
- `backend/src/paperhub/pipelines/marker_client.py` — `MarkerClient.extract(pdf_bytes) -> MarkerDoc`, factory `get_marker_client()`; `MarkerDoc` typed view of Marker JSON.
- `backend/src/paperhub/pipelines/marker_to_asset.py` — `marker_doc_to_asset(doc, *, asset_dir) -> PaperAsset` (writes figure PNGs, builds figures/equations/sections).
- `backend/src/paperhub/pipelines/latex_to_asset.py` — `latex_source_to_asset(source_dir, flattened_text, *, asset_dir) -> PaperAsset` (real figure files + `\caption` extraction + sections + equations).

**Modified — backend:**
- `pipelines/paper_pipeline.py` — PDF branch → Marker→PaperAsset; arXiv branch → latex→PaperAsset; both write the asset bundle + re-derive chunks/sections + render HTML from structured output for PDFs.
- `pipelines/renderer.py` — PDF HTML from Marker markdown/HTML (not raw PyMuPDF).
- `config.py` — `marker_service_url` (+ host/port), `inprocess_marker`.

**Tests:** `test_paper_asset.py`, `test_marker_client.py`, `test_marker_to_asset.py`, `test_latex_to_asset.py`, extend `test_paper_pipeline.py` (PDF-via-mocked-Marker + arXiv-emits-asset), `backend/tests/fixtures/marker_doc.json`.

**Scripts:** `backend/scripts/smoke_marker.ps1` (asserts `marker` service `/health` + a sample `/extract` when the container is up; skips gracefully when down).

---

## Task 1: `PaperAsset` contract + file IO

**Files:**
- Create: `backend/src/paperhub/pipelines/paper_asset.py`
- Test: `backend/tests/test_paper_asset.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_paper_asset.py
from pathlib import Path
from paperhub.pipelines.paper_asset import (
    PaperAsset, FigureAsset, EquationAsset, SectionAsset,
    write_paper_asset, read_paper_asset, paper_asset_dir,
)


def test_roundtrip(tmp_path: Path) -> None:
    asset = PaperAsset(
        figures=[FigureAsset(id="fig-001", caption="The architecture.", page=2,
                             section="Method", image_path="figures/fig-001.png")],
        equations=[EquationAsset(id="eq-001", latex=r"E=mc^2", section="Theory")],
        sections=[SectionAsset(name="Method", order=0)],
    )
    d = paper_asset_dir(tmp_path)  # tmp_path/asset
    # pretend the image exists
    (d / "figures").mkdir(parents=True, exist_ok=True)
    (d / "figures" / "fig-001.png").write_bytes(b"\x89PNG")
    write_paper_asset(asset, tmp_path)
    loaded = read_paper_asset(tmp_path)
    assert loaded is not None
    assert loaded.figures[0].caption == "The architecture."
    assert loaded.figures[0].abs_image_path(tmp_path).exists()
    assert loaded.equations[0].latex == "E=mc^2"
    assert loaded.sections[0].name == "Method"


def test_read_missing_returns_none(tmp_path: Path) -> None:
    assert read_paper_asset(tmp_path) is None
```

- [ ] **Step 2: Run → FAIL.** `cd backend; uv run pytest tests/test_paper_asset.py -v` → ModuleNotFoundError.

- [ ] **Step 3: Implement `paper_asset.py`**

```python
# backend/src/paperhub/pipelines/paper_asset.py
"""Unified PaperAsset contract (SRS v2.19 §III-5.1).

Both ingestion paths (arXiv LaTeX, PDF→Marker) normalize to this file-based
bundle under <source_dir>/asset/ so it survives paper_content cache-hits and is
read directly by the F3 slide agent. No DB column — located via source_dir_path.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FigureAsset:
    id: str                    # deck-unique-able stem, e.g. "fig-001"
    caption: str
    page: int | None
    section: str | None
    image_path: str            # relative to the asset dir, e.g. "figures/fig-001.png"

    def abs_image_path(self, source_dir: Path) -> Path:
        return paper_asset_dir(source_dir) / self.image_path


@dataclass(frozen=True)
class EquationAsset:
    id: str
    latex: str
    section: str | None


@dataclass(frozen=True)
class SectionAsset:
    name: str
    order: int


@dataclass(frozen=True)
class PaperAsset:
    figures: list[FigureAsset] = field(default_factory=list)
    equations: list[EquationAsset] = field(default_factory=list)
    sections: list[SectionAsset] = field(default_factory=list)


def paper_asset_dir(source_dir: Path) -> Path:
    return Path(source_dir) / "asset"


def write_paper_asset(asset: PaperAsset, source_dir: Path) -> None:
    d = paper_asset_dir(source_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "figures.json").write_text(
        json.dumps([asdict(f) for f in asset.figures], ensure_ascii=False, indent=2),
        encoding="utf-8")
    (d / "equations.json").write_text(
        json.dumps([asdict(e) for e in asset.equations], ensure_ascii=False, indent=2),
        encoding="utf-8")
    (d / "structure.json").write_text(
        json.dumps([asdict(s) for s in asset.sections], ensure_ascii=False, indent=2),
        encoding="utf-8")


def read_paper_asset(source_dir: Path) -> PaperAsset | None:
    d = paper_asset_dir(source_dir)
    fjson = d / "figures.json"
    if not fjson.exists():
        return None
    figs = [FigureAsset(**x) for x in json.loads(fjson.read_text(encoding="utf-8"))]
    eqs_p = d / "equations.json"
    eqs = [EquationAsset(**x) for x in json.loads(eqs_p.read_text(encoding="utf-8"))] if eqs_p.exists() else []
    sec_p = d / "structure.json"
    secs = [SectionAsset(**x) for x in json.loads(sec_p.read_text(encoding="utf-8"))] if sec_p.exists() else []
    return PaperAsset(figures=figs, equations=eqs, sections=secs)
```

- [ ] **Step 4: Run → PASS.** `cd backend; uv run pytest tests/test_paper_asset.py -v`

- [ ] **Step 5: ruff + mypy.** `uv run ruff check src/paperhub/pipelines/paper_asset.py tests/test_paper_asset.py; uv run mypy src/paperhub/pipelines/paper_asset.py`

- [ ] **Step 6: Commit.**
```bash
git add backend/src/paperhub/pipelines/paper_asset.py backend/tests/test_paper_asset.py
git commit -m "feat(ingest): unified PaperAsset contract + file IO" -m "Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: Config + Marker HTTP client

**Files:**
- Modify: `backend/src/paperhub/config.py`
- Create: `backend/src/paperhub/pipelines/marker_client.py`
- Test: `backend/tests/test_marker_client.py`

- [ ] **Step 1: Add config fields.** In `Settings` add:
```python
    # ── 10. Marker PDF extraction service (v2.19) ───────────────────────
    marker_service_url: str
    inprocess_marker: bool
```
In `load_settings()`:
```python
        marker_service_url=os.environ.get("PAPERHUB_MARKER_URL", "http://127.0.0.1:8002"),
        inprocess_marker=os.environ.get("PAPERHUB_INPROCESS_MARKER", "0") == "1",
```
(Match the existing field-ordering/style; keyword-only construction means no positional breakage. Run `uv run pytest tests/test_config.py -q` if it exists.)

- [ ] **Step 2: Write the failing test** (mocks the HTTP transport — no real service):

```python
# backend/tests/test_marker_client.py
import httpx, pytest
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
```

- [ ] **Step 3: Run → FAIL.**

- [ ] **Step 4: Implement `marker_client.py`** (mirror `_HttpEmbedder`):

```python
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
```
> Note: the test constructs `MarkerClient(...)` directly with a `MockTransport`; production uses `get_marker_client()`. The `/extract` accepts a multipart `file` (matches the service in Task 7).

- [ ] **Step 5: Run → PASS.** **Step 6: ruff + mypy.** **Step 7: Commit** `feat(ingest): Marker HTTP client + config`.

---

## Task 3: Marker doc → `PaperAsset` mapper

**Files:**
- Create: `backend/src/paperhub/pipelines/marker_to_asset.py`
- Test: `backend/tests/test_marker_to_asset.py` + `backend/tests/fixtures/marker_doc.json`

- [ ] **Step 1: Write the failing test** (uses a small canned `MarkerDoc`):

```python
# backend/tests/test_marker_to_asset.py
import base64
from pathlib import Path
from paperhub.pipelines.marker_client import MarkerDoc, MarkerBlock
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
    # the figure PNG was written under asset/figures/
    assert (paper_asset_dir(tmp_path) / f.image_path).exists()
    assert asset.equations[0].latex == "E=mc^2"
    assert any(s.name == "Method" for s in asset.sections)
```

- [ ] **Step 2: Run → FAIL. Step 3: implement `marker_to_asset.py`:**

```python
# backend/src/paperhub/pipelines/marker_to_asset.py
"""Map a Marker MarkerDoc into the unified PaperAsset (SRS v2.19)."""
from __future__ import annotations

import base64
import re
from pathlib import Path

from paperhub.pipelines.marker_client import MarkerDoc
from paperhub.pipelines.paper_asset import (
    EquationAsset, FigureAsset, PaperAsset, SectionAsset, paper_asset_dir,
)

_TAG = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    return _TAG.sub("", html or "").strip()


def _section_of(block) -> str | None:  # type: ignore[no-untyped-def]
    sh = block.section_hierarchy or {}
    if not sh:
        return None
    # deepest level wins
    return sh[max(sh.keys())]


def marker_doc_to_asset(doc: MarkerDoc, *, source_dir: Path) -> PaperAsset:
    figs_dir = paper_asset_dir(source_dir) / "figures"
    figs_dir.mkdir(parents=True, exist_ok=True)

    figures: list[FigureAsset] = []
    equations: list[EquationAsset] = []
    sections: list[SectionAsset] = []
    seen_sections: set[str] = set()
    fig_n = eq_n = 0

    for block in doc.blocks:
        bt = block.block_type
        sec = _section_of(block)
        if sec and sec not in seen_sections:
            sections.append(SectionAsset(name=sec, order=len(sections)))
            seen_sections.add(sec)
        if bt in ("Figure", "Picture") and block.images:
            # take the first image; write it to figures/fig-NNN.png
            raw = next(iter(block.images.values()))
            try:
                data = base64.b64decode(raw)
            except Exception:
                continue
            fid = f"fig-{fig_n:03d}"
            (figs_dir / f"{fid}.png").write_bytes(data)
            figures.append(FigureAsset(
                id=fid, caption=_strip_html(block.html), page=block.page,
                section=sec, image_path=f"figures/{fid}.png"))
            fig_n += 1
        elif bt == "Equation" and block.latex:
            equations.append(EquationAsset(id=f"eq-{eq_n:03d}", latex=block.latex.strip(), section=sec))
            eq_n += 1

    return PaperAsset(figures=figures, equations=equations, sections=sections)
```
> Caption note: Marker emits the caption as an adjacent `Caption`/`FigureGroup` text block; in practice the figure block's `html` or the next block carries it. The `_strip_html(block.html)` captures the common case; if a `Caption` block-type is observed in real output, extend `marker_doc_to_asset` to pair the nearest following `Caption` block with the figure (add a test from a real `marker_doc.json` fixture captured in Task 7 Step 5).

- [ ] **Step 4: Run → PASS. Step 5: ruff + mypy. Step 6: Commit** `feat(ingest): Marker doc → PaperAsset mapper`.

---

## Task 4: arXiv LaTeX source → `PaperAsset`

**Files:**
- Create: `backend/src/paperhub/pipelines/latex_to_asset.py`
- Test: `backend/tests/test_latex_to_asset.py`

Extract real figure files + `\caption` text + sections + equations from the flattened LaTeX + the `source/` dir.

- [ ] **Step 1: Write the failing test:**

```python
# backend/tests/test_latex_to_asset.py
from pathlib import Path
from paperhub.pipelines.latex_to_asset import latex_source_to_asset
from paperhub.pipelines.paper_asset import paper_asset_dir


def test_extracts_figure_caption_and_equation(tmp_path: Path) -> None:
    src = tmp_path / "source"
    (src / "figs").mkdir(parents=True)
    (src / "figs" / "arch.pdf").write_bytes(b"%PDF-1.4 fake")  # a real figure file
    flattened = r"""
\section{Method}
\begin{figure}\includegraphics{figs/arch}\caption{The architecture diagram.}\end{figure}
\begin{equation} E = mc^2 \end{equation}
\section{Results}
"""
    asset = latex_source_to_asset(src, flattened, source_dir=tmp_path)
    assert any("architecture" in f.caption.lower() for f in asset.figures)
    fig = asset.figures[0]
    assert (paper_asset_dir(tmp_path) / fig.image_path).exists()  # staged copy/raster
    assert any("mc^2" in e.latex for e in asset.equations)
    assert [s.name for s in asset.sections][:2] == ["Method", "Results"]
```

- [ ] **Step 2: Run → FAIL. Step 3: implement `latex_to_asset.py`:**

Implement `latex_source_to_asset(source_dir: Path, flattened_text: str, *, source_dir as the cache root: Path) -> PaperAsset`:
- Find every `\begin{figure}...\includegraphics[...]{NAME}...\caption{CAP}...\end{figure}` via a multiline regex; for each: resolve `NAME` to a real file in the `source/` dir (try extensions `.pdf .png .jpg .jpeg .eps`), **rasterize PDFs to PNG** (reuse the rasterize helper in `pipelines/figures.py` or PyMuPDF), copy/write into `asset/figures/fig-NNN.png`, capture the `\caption{...}` text (strip nested LaTeX commands to plain text), and the enclosing `\section{...}` name (track the most recent `\section{...}` before the figure).
- Equations: capture `\begin{equation}...\end{equation}` and `\[...\]` bodies as `EquationAsset.latex` (verbatim LaTeX), tagged with the current section.
- Sections: ordered `\section{...}` names.
- Reuse `pipelines/figures.py` rasterize logic where possible (provenance comment). Keep the function pure-ish (writes only into `asset/`).

(Provide the full implementation following the test's expectations — figure regex, section tracking, extension resolution, PDF→PNG rasterize via `pymupdf` `page.get_pixmap()`.)

- [ ] **Step 4: Run → PASS. Step 5: ruff + mypy. Step 6: Commit** `feat(ingest): arXiv LaTeX source → PaperAsset`.

---

## Task 5: Wire PDF ingestion through Marker → `PaperAsset`

**Files:**
- Modify: `backend/src/paperhub/pipelines/paper_pipeline.py` (`_ingest_upload` PDF branch + ctor)
- Modify: `backend/src/paperhub/pipelines/renderer.py` (PDF HTML from Marker)
- Test: extend `backend/tests/test_paper_pipeline.py`

- [ ] **Step 1: Inject a Marker client into `PaperPipeline`.** Add a `marker_client` constructor param (default `None` → lazily `get_marker_client()`), mirroring how `embedder` is injected. Tests pass a fake.

- [ ] **Step 2: Write the failing test** (PDF ingest with a FAKE marker client, no Docker):

```python
# add to backend/tests/test_paper_pipeline.py
class _FakeMarker:
    def extract(self, pdf_bytes: bytes):
        from paperhub.pipelines.marker_client import MarkerDoc, MarkerBlock
        import base64
        png = base64.b64encode(b"\x89PNG-fake").decode()
        return MarkerDoc(blocks=[
            MarkerBlock(block_type="SectionHeader", html="<h1>Intro</h1>", section_hierarchy={"1": "Intro"}),
            MarkerBlock(block_type="Text", html="<p>Body text about transformers.</p>", section_hierarchy={"1": "Intro"}),
            MarkerBlock(block_type="Figure", images={"i": png}, html="<p>Figure 1: arch.</p>", page=1, section_hierarchy={"1": "Intro"}),
            MarkerBlock(block_type="Equation", html="<math/>", latex=r"a^2+b^2=c^2", section_hierarchy={"1": "Intro"}),
        ])

@pytest.mark.asyncio
async def test_pdf_ingest_writes_paper_asset(pipeline_env_with_marker, tmp_path) -> None:
    pipeline, conn, cache_root = pipeline_env_with_marker  # fixture passes marker_client=_FakeMarker()
    pdf = tmp_path / "p.pdf"; pdf.write_bytes(_minimal_pdf_bytes())  # reuse the upload test's pdf builder
    res = await pipeline.ingest(IngestRequest(session_id=1, upload_path=pdf, upload_kind="pdf"))
    from paperhub.pipelines.paper_asset import read_paper_asset
    # locate the cache dir from paper_content.source_dir_path
    async with conn.execute("SELECT source_dir_path FROM paper_content WHERE id=?", (res.paper_content_id,)) as cur:
        sdp = (await cur.fetchone())[0]
    asset = read_paper_asset(Path(sdp))
    assert asset is not None and len(asset.figures) == 1
    assert asset.equations[0].latex == "a^2+b^2=c^2"
    # chunks derived from structured text exist
    async with conn.execute("SELECT COUNT(*) FROM chunks WHERE paper_content_id=?", (res.paper_content_id,)) as cur:
        assert (await cur.fetchone())[0] >= 1
```
Add a `pipeline_env_with_marker` fixture mirroring `pipeline_env` but passing `marker_client=_FakeMarker()`.

- [ ] **Step 3: Run → FAIL. Step 4: rewrite the PDF branch** of `_ingest_upload`:
  - Read the PDF bytes; call `self._marker_client.extract(bytes)` → `MarkerDoc`.
  - `marker_doc_to_asset(doc, source_dir=cache_dir)` → write the asset bundle (`write_paper_asset`).
  - **Derive `full_text` + section boundaries from the Marker structure** (concatenate text blocks in order, recording each section's char offset → `sections: list[(name, offset)]` for `chunk_text(..., sections=sections, strip_comments=False)`). Replace `extract_pdf_with_headings`.
  - Build `sections_json` via `_build_sections_json(chunks, full_text, strip_comments=False)`.
  - Render HTML for the Canvas from Marker (Task-step below) instead of PyMuPDF.
  - Persist via `_persist_paper_content_and_chunks(...)` unchanged.
  - Metadata: keep the existing PDF metadata extraction (title/authors/year) — Marker also has `metadata.table_of_contents`; use the existing path for now.

- [ ] **Step 5: PDF HTML from Marker.** In `renderer.py`, add `render_marker_html(markdown_or_html: str, out_path: Path)` (Marker can emit HTML directly — request `output_format="html"` OR convert its markdown). Simplest: have the marker service ALSO return a `html` field for the whole doc (Task 7), and write it to `source.html`. Update the PDF branch to write that instead of `_render_pdf`. Keep `_render_pdf` as a fallback when the marker html is empty.

- [ ] **Step 6: Run → PASS** (`uv run pytest tests/test_paper_pipeline.py -v`). **Step 7: full gate** `uv run pytest -q; uv run ruff check src tests; uv run mypy src`. **Step 8: Commit** `feat(ingest): PDF papers ingest via Marker → PaperAsset`.

---

## Task 6: Wire arXiv ingestion to also emit `PaperAsset`

**Files:**
- Modify: `backend/src/paperhub/pipelines/paper_pipeline.py` (`_ingest_arxiv`)
- Test: extend `backend/tests/test_paper_pipeline.py`

- [ ] **Step 1: Write the failing test** — the existing arXiv ingest test (mocked download + a fixture `source/` with a `\begin{figure}\caption{...}` + a real figure file) now asserts `read_paper_asset(source_dir)` returns figures with captions + sections.

- [ ] **Step 2: Run → FAIL. Step 3:** in `_ingest_arxiv`, after `extract_latex(...)`, call `latex_source_to_asset(source_dir, full_text, source_dir=cache_dir)` and `write_paper_asset(...)`. (Additive — the existing flatten/chunk/HTML path stays.) Keep `sections_json` as today (or re-derive from the asset sections if cleaner).

- [ ] **Step 4: Run → PASS. Step 5: full gate. Step 6: Commit** `feat(ingest): arXiv papers emit PaperAsset (additive)`.

---

## Task 7: Marker docker-compose service

**Files:**
- Create: `marker_service/app.py`, `marker_service/Dockerfile`, `marker_service/requirements.txt`
- Create: `docker-compose.yml` (repo root)
- Test: manual + `backend/scripts/smoke_marker.ps1`

- [ ] **Step 1: `marker_service/app.py`** — FastAPI app:
```python
# marker_service/app.py
"""FastAPI wrapper around datalab-to/marker (SRS v2.19 §III-6)."""
from __future__ import annotations
import base64, tempfile
from pathlib import Path
from fastapi import FastAPI, UploadFile, File
from marker.converters.pdf import PdfConverter
from marker.models import create_model_dict
from marker.config.parser import ConfigParser

app = FastAPI(title="paperhub-marker")
_models = None

def _converter():
    global _models
    if _models is None:
        _models = create_model_dict()
    cfg = ConfigParser({"output_format": "json", "extract_images": True})
    return PdfConverter(config=cfg.generate_config_dict(), artifact_dict=_models,
                        renderer=cfg.get_renderer())

@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "models_loaded": _models is not None}

@app.post("/extract")
async def extract(file: UploadFile = File(...)) -> dict[str, object]:
    data = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
        tf.write(data); pdf_path = tf.name
    rendered = _converter()(pdf_path)
    # Flatten rendered JSON pages → blocks the backend expects.
    blocks = []
    for page in rendered.children:
        for b in page.children:
            blocks.append({
                "block_type": b.block_type,
                "html": getattr(b, "html", "") or "",
                "latex": getattr(b, "latex", None),
                "section_hierarchy": getattr(b, "section_hierarchy", {}) or {},
                "images": getattr(b, "images", {}) or {},  # base64 PNGs
                "bbox": list(getattr(b, "bbox", []) or []),
                "page": getattr(page, "page_id", None),
            })
    return {"blocks": blocks}
```
> Verify the exact `rendered`/`block` attribute names against the installed marker version (`block.block_type`, `block.images`, `block.section_hierarchy`, `block.html`, `block.latex`) — the marker JSON schema iteration was confirmed in the brainstorm (`for page in rendered.children: for block in page.children`). Adjust the flatten if the attribute is `block.id`/`json_to_html(block)` based. Capture a real `/extract` response into `backend/tests/fixtures/marker_doc.json` and add a parse test in Task 3.

- [ ] **Step 2: `requirements.txt`** — `marker-pdf`, `fastapi`, `uvicorn[standard]`.

- [ ] **Step 3: `Dockerfile`** — base `python:3.11` (or a CUDA base for GPU), `pip install -r requirements.txt`, pre-download Marker models in a build step so they bake into the image (run a tiny `create_model_dict()` at build, or `marker` model-download CLI), `CMD uvicorn app:app --host 0.0.0.0 --port 8002`.

- [ ] **Step 4: `docker-compose.yml`** (repo root):
```yaml
name: paperhub
services:
  marker:
    build: ./marker_service
    ports: ["8002:8002"]
    # GPU (optional): uncomment for CUDA hosts with nvidia-container-toolkit
    # deploy: { resources: { reservations: { devices: [{ capabilities: [gpu] }] } } }
    restart: unless-stopped
```
(Leave a commented placeholder noting backend + modelserver services will join here as the project goes full-Docker.)

- [ ] **Step 5: Build + capture a real fixture** — `docker compose build marker; docker compose up -d marker`; POST a small real PDF to `/extract`; save the JSON to `backend/tests/fixtures/marker_doc.json`; add a `test_marker_to_asset.py` case loading it (confirms the real schema maps correctly — adjust `marker_to_asset.py` / the service flatten if the attribute names differ). This is the **reality check** that the mapper matches real Marker output.

- [ ] **Step 6: `smoke_marker.ps1`** — `GET :8002/health`; if up, POST a sample PDF + assert `blocks` non-empty + at least one Figure/Equation; if down, print "start the marker service (docker compose up -d marker)" + exit 0.

- [ ] **Step 7: Commit** `feat(ingest): Dockerized Marker extraction service + compose`.

---

## Task 8: Documentation + end-to-end check

- [ ] **Step 1:** Update `CLAUDE.md` "system binaries / external services": add the `marker` docker-compose service (PDF ingestion; `docker compose up -d marker`; GPU-optional; cold start downloads models). Note `PAPERHUB_INPROCESS_MARKER` is not a thing — tests mock the client; the service is required only for real PDF ingestion (arXiv works without it).
- [ ] **Step 2:** Real check (with the marker container up): ingest a real PDF-only paper via `POST /papers/upload`, then confirm `papers_cache/upload/<sha>/asset/figures.json` has real figures with captions + `equations.json` has LaTeX. Confirm RAG still answers (chunks derived). Confirm the Citation Canvas shows the Marker HTML.
- [ ] **Step 3: Commit** `docs: marker ingestion service + PaperAsset`.

---

## Self-review notes (author)
- **Spec coverage (SRS v2.19 §III-5.1/§III-6):** PaperAsset contract ✓ (T1), Marker client+config ✓ (T2), Marker→asset ✓ (T3), arXiv→asset ✓ (T4), PDF ingest via Marker + chunks/sections re-derived ✓ (T5), arXiv emits asset ✓ (T6), compose `marker` service + real-fixture reality check ✓ (T7), Canvas HTML from Marker ✓ (T5 Step 5).
- **No DB schema change** — PaperAsset is file-based under `source_dir/asset/`, located via `paper_content.source_dir_path`; survives cache-hits. (If F3 needs faster lookup, a future `asset` column is additive.)
- **Type consistency:** `MarkerDoc`/`MarkerBlock` shape identical across T2/T3/T5; `PaperAsset`/`FigureAsset` fields identical across T1/T3/T4/F3; `read_paper_asset(source_dir)` is the single reader F3 uses.
- **Reality-check risk:** the Marker block attribute names (T3/T7) must be validated against the installed marker version — T7 Step 5 captures a real fixture and adjusts the mapper before F3 depends on it. This is the load-bearing verification gate for F2.
