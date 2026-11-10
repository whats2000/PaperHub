# H4 Evidence Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The core agent reads figures and formulas from the paper's own pages (with zoom), cites the exact evidence it viewed, and every citation opens the reader on that evidence.

**Architecture:** Every paper gets a *page PDF* (its own PDF, or a locally compiled one for LaTeX papers). New page tools render pages / regions of it as images; each result carries a `[view:P/N/box]` token that citation validation accepts only if viewed. The reader resolves `[fig:]` to the figure (HTML anchor / PDF bbox) and `[view:]` to a highlighted region on the page PDF (a PDF tab for LaTeX papers). Adoption of page mode over crops is gated on an A/B/C over the cases parsing got wrong.

**Tech Stack:** Python 3.11 + uv, FastAPI, aiosqlite, PyMuPDF, Pillow, pdflatex (TeX Live); React + react-pdf + Tailwind v4 + base-ui, react-i18next, vitest/RTL/MSW.

**Spec:** `docs/superpowers/specs/2026-05-17-paperhub-srs.md` §III-10.4 *Evidence pages and citation rigor* (+ FR-03 figure citations). Worktree `D:/GitHub/PaperHub-h4`, branch `feat/plan-h4-agent-mode`.

## Global Constraints

- `uv` only; from `backend/`: `uv run pytest`, `uv run ruff check src tests`, `uv run mypy src`. Frontend: `npm test`, `npm run typecheck`, `npm run lint`.
- No push / merge / tag / PR. Never touch `D:/GitHub/PaperHub` or its workspace; live checks use the H4 instance (`:8100`, workspace `D:/paperhub-h4-workspace`) and workspace copies.
- Record principle: traces record page, box, image sha256 and ids — never pixels or rendered prompts.
- Citation tokens: `[chunk:N]`, `[fig:<paper>/<figure id>]`, `[view:<paper>/<page>/<x0>,<y0>,<x1>,<y1>]` — page 1-based, box integers in 0–1000 page coordinates (x right, y down).
- A citation survives only if its evidence was viewed this turn or is listed in a kept turn's work record.
- LaTeX page PDF: compiled locally with pdflatex into `asset/paper.pdf` (never the cache root — that would switch the reader to PDF mode); arXiv PDF fetched only when compilation fails, at most once per paper (a failure marker stops retries).
- Images sent to the model: long side ≤ 1568 px; zoom renders the region at the DPI that makes its long side 1568 px (capped at 600 DPI).
- `PAPERHUB_AGENT_VISION=0` disables every image tool. Any new env var gets a one-line entry in `backend/.env.example` in the same commit.
- Prompt changes follow the `writing-agent-prompts` skill (≥2 variants × query set × judged comparison).
- UI: every string through `t()` in en / zh-TW / zh-CN / ja; every appear/disappear animates (tab switch slides in its direction; highlight fades); respect `prefers-reduced-motion`.
- Library-changing work ships a migration wired into `backend/scripts/reingest_all_papers.ps1` and run on the H4 workspace with every paper accounted for.

## Review Focus

1. A LaTeX paper whose compile fails and that has no arXiv id (LaTeX upload) → page tools answer "no page image for this paper", `view_figure` falls back to the figure image; no `[view:]` token is ever produced for it. (Task 1, Task 4 tests)
2. The model alters a `[view:]` token (rounds a coordinate, changes the page) → the token is dropped by validation, not rendered. (Task 5 test)
3. `zoom` with a degenerate or out-of-range box, or a page beyond the document → a readable error outcome listing the valid range; no exception. (Task 4 test)
4. `paper.pdf` replaced while the server reads it (backfill during use) → written to a temp file and renamed atomically; readers never see a partial file. (Task 1 test)
5. An old message cites a figure the migration later dropped (commented out) → the reader shows the "figure no longer in this paper" toast; nothing crashes. (Task 7 test)

---

### Task 1: Page PDF for every paper

**Files:**
- Create: `backend/src/paperhub/pipelines/page_pdf.py`
- Modify: `backend/src/paperhub/pipelines/paper_pipeline.py` (arXiv LaTeX path and LaTeX-upload path, after the HTML render)
- Test: `backend/tests/test_page_pdf.py`

**Interfaces:**
- Produces:
  - `COMPILED_PDF = "paper.pdf"` (under `paper_asset_dir(cache_dir)`), `FAILED_MARKER = "paper.pdf.failed.json"`.
  - `page_pdf_path(cache_dir: Path) -> Path | None` — the first top-level `*.pdf` (PDF papers), else `asset/paper.pdf` if present, else None.
  - `compile_paper_pdf(main_tex: Path, cache_dir: Path, *, timeout_s: int = 300) -> Path | None` — copies `main_tex.parent` to a temp dir, runs `pdflatex -interaction=nonstopmode -halt-on-error` twice (bibtex not needed), writes the result atomically (temp file + `os.replace`) to `asset/paper.pdf`; None on failure or zero pages.
  - `fetch_arxiv_pdf(arxiv_id: str, cache_dir: Path) -> Path | None` — one GET of `https://arxiv.org/pdf/<id>` through the existing arXiv HTTP client conventions (user agent, timeout 60 s); on failure writes `FAILED_MARKER` `{reason, at}`; never retries when the marker exists.
  - `ensure_page_pdf(main_tex: Path | None, arxiv_id: str | None, cache_dir: Path) -> Path | None` — existing path, else compile, else arXiv fallback.

- [ ] **Step 1: Failing tests** in `test_page_pdf.py`:
  - `test_page_pdf_prefers_the_papers_own_pdf` — cache with `x.pdf` at root and `asset/paper.pdf` → root pdf returned; only `asset/paper.pdf` → it; neither → None.
  - `test_compile_writes_asset_pdf` (skip if `shutil.which("pdflatex") is None`) — a 2-page `article` fixture with one `\includegraphics` of a generated PNG → `asset/paper.pdf` exists, `pymupdf.open(...).page_count == 2`, root has no `*.pdf`.
  - `test_compile_failure_returns_none_and_leaves_no_partial_file` — tex with `\undefinedcommand` → None, no `asset/paper.pdf`, no temp files left in `asset/`.
  - `test_arxiv_fallback_runs_once` — monkeypatch the HTTP get to fail; first call writes the marker and returns None; second call does not call the HTTP get.
  - `test_ensure_page_pdf_order` — compile monkeypatched to fail, fetch monkeypatched to succeed → fetch called once; existing `asset/paper.pdf` → neither called.
- [ ] **Step 2:** `uv run pytest tests/test_page_pdf.py -v` → FAIL (module missing).
- [ ] **Step 3:** Implement `page_pdf.py`; call `await asyncio.to_thread(ensure_page_pdf, ...)` in both LaTeX ingest paths after the HTML render (failure only logged; ingest never fails on it).
- [ ] **Step 4:** Tests pass; `uv run pytest tests -k "pipeline or ingest" -q` still green.
- [ ] **Step 5:** Commit `feat(pipeline): give every paper a page PDF`.

### Task 2: Figure places on the page PDF

**Files:**
- Create: `backend/src/paperhub/pipelines/figure_pages.py`
- Modify: `backend/src/paperhub/pipelines/paper_pipeline.py` (after `ensure_page_pdf`: locate LaTeX figures, write the asset)
- Test: `backend/tests/test_figure_pages.py`

**Interfaces:**
- Consumes: `page_pdf_path` (Task 1); `FigureAsset.page/bbox` (0-based page, PDF points).
- Produces: `locate_latex_figures(pdf: Path, figures: list[FigureAsset], image_sizes: dict[str, tuple[int, int]]) -> dict[str, tuple[int, tuple[float, float, float, float]]]` — per figure id: the page and the figure's box. Approach: find the caption's first 8 words (`page.search_for` on whitespace-folded text, first hit in page order) → the figure box is the union of image placements (`page.get_image_info`) and vector drawings (`page.get_drawings`) on that page that overlap the caption horizontally and lie between the caption and the nearest text block above it (or below, when the caption is above them); figures without a caption are matched by an embedded raster of the same pixel size. Unlocated figures are omitted.

- [ ] **Step 1: Failing tests** (synthetic PDFs built with PyMuPDF): `test_box_above_its_caption`; `test_caption_above_figure`; `test_two_figures_on_one_page_get_separate_boxes`; `test_captionless_figure_matched_by_image_size`; `test_unlocatable_figure_is_omitted`.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Implement; ingest writes `page`/`bbox` onto LaTeX figures (their `page` was None before).
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(pipeline): place LaTeX figures on their compiled page`.

### Task 3: Migration — page PDFs and figure places for existing papers

**Files:**
- Create: `backend/src/paperhub/cli/backfill_page_pdfs.py` (`paperhub-backfill-page-pdfs`, flags `--dry-run`, `--paper-content-id`, `--verify`)
- Modify: `backend/pyproject.toml`, `backend/scripts/reingest_all_papers.ps1` (new step after figure locations, before the HTML re-render; `--verify` after)
- Test: `backend/tests/test_backfill_page_pdfs.py`

**Interfaces:**
- Consumes: `ensure_page_pdf` (Task 1), `locate_latex_figures` (Task 2).
- Produces: per-paper statuses `compiled | fetched | unchanged | no_source | failed`; `--verify` exits 1 and lists each paper without a page PDF and each LaTeX figure without a page place.

- [ ] **Step 1: Failing tests:** statuses for a compilable fixture (`compiled`), an existing `paper.pdf` (`unchanged`), a missing source (`no_source`); idempotent second run; `--verify` output names an unlocated figure.
- [ ] **Step 2–4:** implement; PASS.
- [ ] **Step 5:** Commit `feat(cli): backfill page PDFs and figure places`.

### Task 4: Page tools

**Files:**
- Create: `backend/src/paperhub/agents/core/page_tools.py`
- Modify: `backend/src/paperhub/agents/core/read_tools.py` (register; `view_figure` page mode), `backend/src/paperhub/agents/core/figures.py` (shared image encoding), `backend/src/paperhub/config.py` + `backend/.env.example` (`PAPERHUB_AGENT_EVIDENCE=pages|crops`, default `pages`; temporary A/B switch, removed in Task 10)
- Test: `backend/tests/agents_core/test_page_tools.py`

**Interfaces:**
- Consumes: `page_pdf_path`; `FigureAsset.page/bbox`; chunk rows `page`, `bbox`.
- Produces:
  - `view_token(paper: int, page: int, box: tuple[int, int, int, int]) -> str` → `[view:P/N/x0,y0,x1,y1]` (page 1-based).
  - `PageImage(paper, page, box, mime, data_b64, size, sha256)` sent like `FigureImage` (same `images_message`).
  - Tools: `view_page(paper, page)`; `zoom(paper, page, box: list[int])`; `view_chunk(id)`; `view_figure(paper, figure)` — pages mode: the figure's page image + text `figure box: x0,y0,x1,y1` + `cite="[fig:P/ID]"` + the page's `[view:]` token; crops mode: current behaviour. Records: `{paper, page, box, sha256, size, dpi}` (+ `figure` / `chunk`).
  - Errors (outcomes, never exceptions): page out of range (lists `1..N`), box not 4 ints in 0–1000 with x0<x1, y0<y1, paper without a page PDF ("no page image for this paper; use view_figure for its figures"), chunk without a page region ("read it with read_chunks").

- [ ] **Step 1: Failing tests:** `test_view_page_image_and_token`; `test_zoom_renders_the_box_at_high_dpi` (a 100×100-unit box on a Letter page → long side 1568 px); `test_zoom_rejects_bad_boxes` (each invalid case → error text, no image); `test_view_chunk_uses_the_chunk_region`; `test_view_figure_pages_mode_returns_page_and_box`; `test_no_page_pdf_falls_back`; `test_vision_off_registers_no_page_tool`; `test_trace_never_holds_pixels`.
- [ ] **Step 2–4:** implement; PASS.
- [ ] **Step 5:** Commit `feat(agent): page, zoom and chunk-region tools`.

### Task 5: `[view:]` citations end to end (backend)

**Files:**
- Modify: `backend/src/paperhub/agents/core/citations.py`, `agent.py`, `context.py`, `backend/src/paperhub/feedback/trajectory.py`, `backend/benchmark/scorer.py`, `backend/benchmark/judge.py`
- Test: `backend/tests/agents_core/test_view_citations.py`, `backend/tests/test_feedback_agent.py`, `backend/tests/benchmark/test_judge_thread.py`

**Interfaces:**
- Produces: `cited_views(text) -> list[str]`, `validate_view_citations(text, allowed: set[str]) -> tuple[str, list[str]]` (exact token match); work record `views` / `cited_views`, `<work_record>` line `views: …`, `Conversation.kept_views`; the export re-renders each viewed image and marks the run private if its sha256 differs; the judge receives cited views as images (like cited figures).

- [ ] **Step 1: Failing tests:** token kept when viewed this turn; altered coordinate or page dropped and listed in `view_citations_removed`; kept on the next turn via the work record; export rebuilds the image message and flags a changed page PDF; judge input carries the view image.
- [ ] **Step 2–4:** implement; PASS.
- [ ] **Step 5:** Commit `feat(agent): cite viewed page regions as [view:]`.

### Task 6: Reader APIs

**Files:**
- Modify: `backend/src/paperhub/api/papers.py`
- Test: `backend/tests/test_papers_figures_api.py`, `backend/tests/test_papers_page_pdf_api.py`

**Interfaces:**
- Produces:
  - `DocumentModeResponse` gains `page_pdf: bool`.
  - `GET /papers/content/{id}/page-pdf` → the page PDF (404 when none).
  - `GET /papers/content/{id}/figures/{fid}` adds `html_anchor: str | null` (`phfig-NNN` when present in `source.html`), `page: int | null` (0-based), `bbox: number[] | null`.
  - `GET /papers/content/{id}/view-region?page=N&box=x0,y0,x1,y1` → `{page: <0-based>, bbox: [points…], source: "own" | "compiled"}`; 400 on a malformed box, 404 on a page out of range.

- [ ] **Step 1–4:** failing tests for each response and error, implement, PASS.
- [ ] **Step 5:** Commit `feat(api): page PDF, figure locators and view regions for the reader`.

### Task 7: Reader — figure and region citations open the evidence

**Files:**
- Modify: `frontend/src/store/canvas.ts` (`openFigure(paper, figureId)`, `openView(paper, page, box)`), `frontend/src/components/canvas/CitationCanvas.tsx` (resolve both requests; PDF tab for HTML papers with a page PDF), `HtmlView.tsx` (`highlightFigureAnchor`: the first `figure`, `div[class^="figure"]` or `img` after the anchor gets the highlight class and is scrolled into view), `PdfView.tsx` (accept a `source` URL: own PDF or page PDF; region highlight reuses `bboxToRect`), `FigureCitationMarker.tsx` (button → `openFigure`; popover removed), new `ViewCitationMarker.tsx`, `frontend/src/lib/figureCitations.ts` (+ `[view:]` parsing), `rehypeFigureCitations.ts` (both markers share figure numbering), `frontend/src/lib/api.ts`, locales `canvas.json` ×4
- Test: `FigureCitationMarker.test.tsx` (rewritten), `ViewCitationMarker.test.tsx`, `CitationCanvas.figure.test.tsx`

**Interfaces:**
- Consumes: Task 6 APIs.
- Produces: i18n keys `canvas:marker.figureAria`, `canvas:marker.viewAria` ("region on page {{page}}, citation {{ordinal}}"), `canvas:tabs.html`, `canvas:tabs.pdf`, `canvas:toast.figureGone`, `canvas:toast.regionGone`; obsolete `canvas:figure.*` keys removed from all four locales.

- [ ] **Step 1: Failing tests:** clicking a `[fig:]` marker calls `openFigure` and the HTML reader highlights the element after `#phfig-003`; a PDF paper highlights the figure bbox; clicking `[view:]` on a LaTeX paper switches to the PDF tab (slide transition class present) and highlights the converted box; a figure 404 → `toast.figureGone`; markers render in all four locales (parity test).
- [ ] **Step 2–4:** implement; PASS; `npm run typecheck`, `npm run lint`.
- [ ] **Step 5:** Commit `feat(chat): figure and region citations open the reader on the evidence`.

### Task 8: Problem-case evaluation suite

**Files:**
- Create: `backend/benchmark/cases.h4-evidence.toml`
- Modify: `backend/benchmark/README.md` (how to run A/B/C)

Cases (ground truth read off the page images and the LaTeX source, each with a rubric that also checks the citation supports the claim): multi-panel figures (paper 39 Fig. 4 panels d–h, paper 80 Fig. 4 (c)/(e), paper 56 Fig. 1 (b), paper 83 Fig. 2 (b)); Marker-split figures (paper 34 Fig. 6 top diagram, paper 35 Fig. 14 (a)); figures pandoc dropped (paper 47 Fig. 1, paper 61 LIBERO figure); the commented-out figure (GR00T real-robot bar chart — correct answer: it is not in the paper; Table 3 holds the numbers); small print (LIVECell Fig. 3e values, a dense table rendered as an image); Marker formulas (5 equations from Task 9's audit where Marker is wrong, 3 where it is right); plus 3 text-only regression cases.

- [ ] **Step 1:** Author the cases; for each, store the evidence location (paper, page, box) in a comment.
- [ ] **Step 2:** `uv run python -m benchmark.runner --config benchmark/cases.h4-evidence.toml --dry-run` parses all cases.
- [ ] **Step 3:** Commit `test(benchmark): evidence-page problem cases`.

### Task 9: Marker formula audit

**Files:**
- Create: `backend/benchmark/marker_formula_audit.py` (repeatable measurement)
- Modify: `docs/superpowers/specs/2026-05-17-paperhub-srs.md` (record the measured rate)

**Interfaces:**
- Produces: `uv run python -m benchmark.marker_formula_audit --sample 40 --db <db>` → for sampled Marker `Equation` blocks: the block's page crop + Marker's LaTeX are judged by the vision judge (temperature 0) as `match | minor | wrong`, plus a CSV for manual spot checks; prints the rates.

- [ ] **Step 1:** Implement; run on the H4 workspace; spot-check 10 verdicts by viewing the crops.
- [ ] **Step 2:** Record the rate in the SRS; decide the formula rule (mandatory `view_chunk` before stating a formula if `wrong` ≥ 5 %).
- [ ] **Step 3:** Commit `test(benchmark): audit Marker formula transcription`.

### Task 10: Prompt and A/B/C gate

**Files:**
- Create: `backend/src/paperhub/llm/prompts/core_agent_v8.yaml`; eval variants under `backend/benchmark/agent/prompts/core_agent/`
- Modify: `agent.py` (slot), tool descriptions, `config.py` / `.env.example` (remove the `PAPERHUB_AGENT_EVIDENCE` switch after the decision), SRS as-built note

- [ ] **Step 1:** Invoke `writing-agent-prompts`; draft ≥2 v8 variants (page-mode workflow: view the page, zoom for detail, cite the narrowest evidence; the formula rule from Task 9).
- [ ] **Step 2:** Run the suite three ways, each on its own instance: **A** original parsing — base worktree `D:/GitHub/PaperHub-h4-base` (commit `27b58ff7`) on an un-migrated workspace copy, `:8300`; **B** repaired crops — H4 code, migrated copy, `PAPERHUB_AGENT_EVIDENCE=crops`, core_agent v7; **C** pages + zoom — H4 code, migrated copy, `pages`, v8 variants. Figure-aware judge; read every answer.
- [ ] **Step 3:** Adopt C only if correctness and citation support are ≥ B on every category with no new failure; else keep B. Remove the losing path and the switch; SRS as-built (numbers per category).
- [ ] **Step 4:** Commit with the evidence in the body.

### Task 11: Migration on the H4 workspace and live check

- [ ] **Step 1:** Back up `D:/paperhub-h4-workspace/paperhub.db`; run `paperhub-backfill-figure-locations`, `paperhub-backfill-page-pdfs`, `paperhub-rerender-html`, both `--verify`s → every paper accounted for.
- [ ] **Step 2:** Restart `:8100`; live: one question per category through `/chat`, read each trace (tools, views, citations kept / removed).
- [ ] **Step 3:** Ask the user to check the GUI: a `[fig:]` and a `[view:]` citation on a LaTeX and a PDF paper open the reader with the highlight.
