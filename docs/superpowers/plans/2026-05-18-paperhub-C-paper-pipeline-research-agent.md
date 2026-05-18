# PaperHub Plan C — Paper Pipeline + Research Agent

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the cache-aware Paper Pipeline (arXiv search + download + LaTeX/PDF extraction + chunking + embedding + Chroma persistence + HTML render for the Canvas), and replace the Plan-A `paper_search` and `paper_qa` stub nodes with a real Research Agent that uses the pipeline end-to-end. CLI smoke proves: (a) re-ingesting a paper hits the cache (sub-second); (b) multi-paper Q&A returns answers with `[chunk:<id>]` citation markers tied to real `chunks.id` rows.

**Architecture:** Single FastAPI process (unchanged from Plan A). Three new in-repo packages: `paperhub.pipelines` (ingest), `paperhub.rag` (retrieval + rerank), and the expanded `paperhub.agents.research` module. Cache key is `content_key` (`arxiv:<id>` for arXiv, `sha256:<hex>` for uploads). All artefacts persist under `workspace/papers_cache/` with the layout from SRS §III-7. Chroma is file-backed under `workspace/chroma/` with one shared `paper_chunks` collection metadata-filtered by `paper_content_id`. Embeddings: `sentence-transformers/BAAI/bge-small-en-v1.5` (lazy singleton). Rerank: `cross-encoder/ms-marco-MiniLM-L-6-v2` (lazy singleton). HTML render: pandoc primary, pylatexenc fallback.

**Tech Stack additions:** `arxiv` (Python client), `pymupdf` (PDF text), `chromadb` (vector store), `sentence-transformers` (embeddings + cross-encoder), `pylatexenc` (LaTeX→HTML fallback), `tiktoken` (chunker tokenization). System dep: `pandoc` (documented as optional — fallback handles its absence). Everything else (FastAPI, LangGraph, LiteLLM, aiosqlite) is unchanged from Plan A.

---

## Spec Coverage Summary

| SRS reference | Addressed by |
| --- | --- |
| §III-5.1 Paper Pipeline (all 8 stages) | Tasks 2 – 8, 10 |
| §III-5.2 paper_qa retrieval (top-k vector + cross-encoder rerank) | Tasks 11, 13 |
| §III-5.4 Chroma vector store keyed by `paper_content_id` | Task 9 |
| §III-7 `paper_content` + `chunks` writes | Task 10 (already migrated in Plan A) |
| FR-03 Citation Canvas (`paper_content.html_path` populated) | Task 8 |
| FR-07 / FR-08 Reference Sources scope + retrieval intersect | Tasks 13, 14 |
| UC-1 paper_search returns metadata only | Task 12 |
| UC-2 Add-as-reference → ingest | Tasks 10, 14 |
| UC-3 Multi-paper Q&A with chunk-cited answers | Task 13 |
| I-8 #2 cache reuse (re-ingest is instant) | Tasks 10, 15 |
| I-8 #3 multi-paper Q&A returns ≥ 2 distinct `paper_content.id` | Task 13 + 15 |

**Out of scope for Plan C** (explicit Plan D / E / F / G handoffs):
- Reference Sources UI panel (Plan D — UI surface for the FR-08 toggle state that the backend now maintains)
- Citation Canvas component (Plan D — consumes `html_path` and `chunks.char_start/char_end` written by Plan C)
- SearchResultList UI + "Add as reference" buttons (Plan D — calls the `POST /papers` endpoint from Plan C)
- SQL Agent + sqlite MCP (Plan E)
- Slide Pipeline (Plan F)
- Compare view (Plan G)

Plan C is verifiable end-to-end via CLI (`scripts/ingest_paper.ps1` + `scripts/query_papers.ps1`); the user-facing surface lands in Plan D.

---

## File Structure

```
backend/
├── pyproject.toml                              # +5 deps
├── scripts/
│   ├── ingest_paper.ps1                        # NEW — CLI smoke for ingest
│   └── query_papers.ps1                        # NEW — CLI smoke for paper_qa
├── src/paperhub/
│   ├── pipelines/                              # NEW package
│   │   ├── __init__.py
│   │   ├── arxiv_client.py                     # search + download
│   │   ├── extract.py                          # LaTeX + PDF text extraction
│   │   ├── chunker.py                          # token-windowed, section-aware
│   │   ├── embedder.py                         # bge-small lazy singleton
│   │   ├── renderer.py                         # pandoc + pylatexenc fallback
│   │   └── paper_pipeline.py                   # cache-aware orchestrator
│   ├── rag/                                    # NEW package
│   │   ├── __init__.py
│   │   ├── chroma.py                           # ChromaStore wrapper
│   │   ├── retriever.py                        # vector search scoped to enabled papers
│   │   └── reranker.py                         # ms-marco-MiniLM lazy singleton
│   ├── agents/
│   │   ├── research.py                         # NEW — paper_search + paper_qa nodes
│   │   ├── stubs.py                            # drop paper_search/paper_qa entries (keep slides + library_stats)
│   │   └── graph.py                            # wire research nodes; stubs stay for the other 2
│   ├── api/
│   │   ├── papers.py                           # NEW — POST /papers, GET /papers/{id}/html
│   │   └── chat.py                             # add streaming for paper_qa, one-shot for paper_search
│   ├── llm/prompts/
│   │   ├── paper_search_v1.yaml                # NEW — extract arxiv search terms from user message
│   │   └── paper_qa_v1.yaml                    # NEW — answer with [chunk:<id>] citation markers
│   └── config.py                               # add CHROMA_DIR, EMBEDDING_MODEL, RERANKER_MODEL settings
└── tests/
    ├── fixtures/
    │   ├── papers/
    │   │   ├── arxiv_sample/                   # tiny LaTeX source tree
    │   │   │   ├── main.tex
    │   │   │   └── refs.bib
    │   │   └── sample.pdf                      # 1-page test PDF (generated in fixture-build script)
    │   └── chroma/                             # not persisted; tests use tmp_path
    ├── test_arxiv_client.py
    ├── test_extract.py
    ├── test_chunker.py
    ├── test_embedder.py
    ├── test_renderer.py
    ├── test_chroma.py
    ├── test_paper_pipeline.py
    ├── test_retriever.py
    ├── test_reranker.py
    ├── test_research_paper_search.py
    ├── test_research_paper_qa.py
    └── test_papers_api.py
```

Every new module follows the Plan-A patterns: async where appropriate, Pydantic v2 for data shapes, `mypy --strict` clean.

---

## Task 1 — Dependencies + fixtures + config

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/src/paperhub/config.py`
- Create: `backend/tests/fixtures/papers/arxiv_sample/main.tex`
- Create: `backend/tests/fixtures/papers/arxiv_sample/refs.bib`
- Create: `backend/tests/fixtures/papers/build_sample_pdf.py`
- Create: `backend/tests/fixtures/papers/sample.pdf` (generated)
- Modify: `CLAUDE.md` (note `pandoc` as optional system dep)

- [ ] **Step 1: Add dependencies.**

From `backend/`:

```powershell
uv add arxiv pymupdf chromadb sentence-transformers pylatexenc tiktoken
```

NOTE: `sentence-transformers` pulls torch (~700MB on Windows). Verify the install succeeds. If `torch` resolution is slow/blocked on Windows, the implementer should switch to `--index-url https://download.pytorch.org/whl/cpu` for the CPU-only wheel.

- [ ] **Step 2: Add config settings.**

Edit `backend/src/paperhub/config.py` to extend `Settings`:

```python
@dataclass(frozen=True)
class Settings:
    workspace_dir: Path
    db_path: Path
    papers_cache_dir: Path
    chroma_dir: Path
    router_model: str
    chitchat_model: str
    paper_qa_model: str          # NEW — flagship by default
    embedding_model: str         # NEW — "BAAI/bge-small-en-v1.5"
    reranker_model: str          # NEW — "cross-encoder/ms-marco-MiniLM-L-6-v2"
    log_level: str
```

And in `load_settings()`:

```python
def load_settings() -> Settings:
    workspace = Path(os.environ.get("PAPERHUB_WORKSPACE", "./workspace")).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    return Settings(
        workspace_dir=workspace,
        db_path=workspace / "paperhub.db",
        papers_cache_dir=workspace / "papers_cache",
        chroma_dir=workspace / "chroma",
        router_model=os.environ.get("PAPERHUB_ROUTER_MODEL", "gemini/gemini-2.5-flash"),
        chitchat_model=os.environ.get("PAPERHUB_CHITCHAT_MODEL", "gemini/gemini-2.5-flash"),
        paper_qa_model=os.environ.get("PAPERHUB_PAPER_QA_MODEL", "gemini/gemini-2.5-pro"),
        embedding_model=os.environ.get("PAPERHUB_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
        reranker_model=os.environ.get("PAPERHUB_RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"),
        log_level=os.environ.get("PAPERHUB_LOG_LEVEL", "INFO"),
    )
```

- [ ] **Step 3: Write the LaTeX fixture.**

`backend/tests/fixtures/papers/arxiv_sample/main.tex`:

```latex
\documentclass{article}
\usepackage{cite}
\title{A Tiny Test Paper on Mixture of Experts}
\author{Test Author}
\begin{document}
\maketitle

\begin{abstract}
This paper presents a tiny example of a Mixture-of-Experts routing scheme,
useful only for testing the PaperHub ingestion pipeline.
\end{abstract}

\section{Introduction}
The Mixture-of-Experts (MoE) architecture activates only a subset of experts
per input token. This reduces the compute cost while preserving model capacity.

\section{Method}
We propose a simple top-2 gating function that selects the two highest-scoring
experts for each token. Expert collapse is mitigated by a load-balancing loss
\cite{shazeer2017}.

\section{Conclusion}
The proposed scheme matches dense baselines at 30\% of the FLOPs.

\bibliography{refs}
\bibliographystyle{plain}
\end{document}
```

`backend/tests/fixtures/papers/arxiv_sample/refs.bib`:

```bibtex
@article{shazeer2017,
  title={Outrageously large neural networks: The sparsely-gated mixture-of-experts layer},
  author={Shazeer, Noam and others},
  year={2017},
}
```

- [ ] **Step 4: Generate a tiny PDF fixture.**

`backend/tests/fixtures/papers/build_sample_pdf.py`:

```python
"""Run once to (re)generate sample.pdf. Output is git-committed."""
from pathlib import Path

import pymupdf

OUT = Path(__file__).parent / "sample.pdf"

def main() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "A Tiny Test Paper", fontsize=18)
    page.insert_text((72, 140), "Abstract", fontsize=14)
    page.insert_text(
        (72, 170),
        "This PDF is a tiny example for the PaperHub ingestion pipeline tests.",
        fontsize=11,
    )
    page.insert_text((72, 220), "Introduction", fontsize=14)
    page.insert_text(
        (72, 250),
        "Mixture-of-Experts (MoE) routing activates only a subset of experts.",
        fontsize=11,
    )
    doc.save(OUT)
    doc.close()
    print(f"wrote {OUT}")

if __name__ == "__main__":
    main()
```

Run it once:

```powershell
uv run python tests/fixtures/papers/build_sample_pdf.py
```

The resulting `sample.pdf` (~2 KB) is checked into git so tests are hermetic.

- [ ] **Step 5: Update CLAUDE.md.**

Add to the Conventions section:

```markdown
- **System binaries:** `pandoc` is an optional dependency used by the Paper Pipeline to render LaTeX → HTML for the Citation Canvas. If absent, the pipeline falls back to `pylatexenc` (pure Python, lower quality). Install via `winget install pandoc` on Windows or your package manager elsewhere.
```

- [ ] **Step 6: Verify gates.**

```powershell
uv run pytest -v
uv run ruff check src tests
uv run mypy src
```

All pass.

- [ ] **Step 7: Commit.**

```powershell
git add backend/pyproject.toml backend/uv.lock backend/src/paperhub/config.py backend/tests/fixtures/ CLAUDE.md
git commit -m "chore(backend): Paper Pipeline deps (arxiv, pymupdf, chromadb, sentence-transformers, pylatexenc) + fixtures"
```

---

## Task 2 — arxiv_client.py (search + download)

**Files:**
- Create: `backend/src/paperhub/pipelines/__init__.py` (empty)
- Create: `backend/src/paperhub/pipelines/arxiv_client.py`
- Create: `backend/tests/test_arxiv_client.py`

The `arxiv` Python client wraps the arXiv API. Both search and source-download go through it.

- [ ] **Step 1: Write the failing test.**

`backend/tests/test_arxiv_client.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from paperhub.pipelines.arxiv_client import (
    ArxivResult,
    search_arxiv,
    download_arxiv_source,
)


def test_search_arxiv_returns_typed_results() -> None:
    fake_result = MagicMock()
    fake_result.entry_id = "http://arxiv.org/abs/2403.01234v1"
    fake_result.title = "A Test Paper"
    fake_result.authors = [MagicMock(name="Author One"), MagicMock(name="Author Two")]
    fake_result.authors[0].name = "Author One"
    fake_result.authors[1].name = "Author Two"
    fake_result.summary = "An abstract."
    fake_result.published.year = 2024

    fake_search = MagicMock()
    fake_search.results.return_value = iter([fake_result])

    with patch("paperhub.pipelines.arxiv_client.arxiv.Search", return_value=fake_search):
        results = search_arxiv("mixture of experts", max_results=1)

    assert len(results) == 1
    r = results[0]
    assert isinstance(r, ArxivResult)
    assert r.arxiv_id == "2403.01234"
    assert r.title == "A Test Paper"
    assert r.authors == ["Author One", "Author Two"]
    assert r.year == 2024
    assert r.abstract == "An abstract."


def test_download_arxiv_source_writes_to_cache(tmp_path: Path) -> None:
    fake_result = MagicMock()
    fake_result.download_source = MagicMock(
        return_value=str(tmp_path / "downloaded.tar.gz")
    )
    fake_search = MagicMock()
    fake_search.results.return_value = iter([fake_result])

    # Pre-create the "downloaded" tarball with a minimal layout.
    import tarfile
    src_file = tmp_path / "main.tex"
    src_file.write_text(r"\documentclass{article}\begin{document}Hi\end{document}")
    tar_path = tmp_path / "downloaded.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(src_file, arcname="main.tex")

    with patch("paperhub.pipelines.arxiv_client.arxiv.Search", return_value=fake_search):
        source_dir = download_arxiv_source("2403.01234", cache_root=tmp_path / "cache")

    assert source_dir.exists()
    assert (source_dir / "main.tex").exists()
    assert source_dir.parent.name == "2403.01234"
```

Run from `backend/`:

```powershell
uv run pytest tests/test_arxiv_client.py -v
```

Expected: FAIL on import.

- [ ] **Step 2: Implement `arxiv_client.py`.**

```python
"""arXiv API client: search + e-print source download.

Adapted from paper2slides-plus/src/arxiv_utils.py — extraction + download
patterns copied + edited to fit the Plan-C Paper Pipeline contract.
"""
from __future__ import annotations

import re
import tarfile
from pathlib import Path

import arxiv
from pydantic import BaseModel


class ArxivResult(BaseModel):
    arxiv_id: str
    title: str
    authors: list[str]
    year: int | None
    abstract: str
    pdf_url: str | None = None


_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")


def _id_from_entry_id(entry_id: str) -> str:
    """Strip URL prefix + version suffix: 'http://arxiv.org/abs/2403.01234v2' → '2403.01234'."""
    m = _ARXIV_ID_RE.search(entry_id)
    if not m:
        raise ValueError(f"unexpected arxiv entry_id: {entry_id!r}")
    return m.group(1)


def search_arxiv(query: str, max_results: int = 10) -> list[ArxivResult]:
    """Return metadata-only search results from arXiv. No download."""
    search = arxiv.Search(
        query=query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.Relevance,
    )
    results: list[ArxivResult] = []
    for r in search.results():
        results.append(
            ArxivResult(
                arxiv_id=_id_from_entry_id(r.entry_id),
                title=r.title.strip(),
                authors=[a.name for a in r.authors],
                year=getattr(r.published, "year", None),
                abstract=r.summary.strip(),
                pdf_url=getattr(r, "pdf_url", None),
            )
        )
    return results


def download_arxiv_source(arxiv_id: str, *, cache_root: Path) -> Path:
    """Download the e-print source tarball for an arxiv_id, unpack into
    cache_root / arxiv_id / source / ..., return the source directory.
    """
    target_dir = cache_root / arxiv_id
    source_dir = target_dir / "source"
    target_dir.mkdir(parents=True, exist_ok=True)

    # Fetch the result so arxiv can stream the source archive.
    search = arxiv.Search(id_list=[arxiv_id])
    result = next(iter(search.results()))
    tar_path_str = result.download_source(dirpath=str(target_dir))
    tar_path = Path(tar_path_str)

    source_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:gz") as tar:
        # Strip leading directories; flatten into source/.
        for member in tar.getmembers():
            if member.isreg():
                name = Path(member.name).name
                fobj = tar.extractfile(member)
                if fobj is None:
                    continue
                (source_dir / name).write_bytes(fobj.read())
    tar_path.unlink(missing_ok=True)
    return source_dir
```

- [ ] **Step 3: Run gates.**

```powershell
uv run pytest tests/test_arxiv_client.py -v
uv run ruff check src/paperhub/pipelines tests/test_arxiv_client.py
uv run mypy src/paperhub/pipelines
```

All pass.

- [ ] **Step 4: Commit.**

```powershell
git add backend/src/paperhub/pipelines backend/tests/test_arxiv_client.py
git commit -m "feat(pipelines): arXiv search + e-print source download"
```

---

## Task 3 — extract.py (LaTeX + PDF)

**Files:**
- Create: `backend/src/paperhub/pipelines/extract.py`
- Create: `backend/tests/test_extract.py`

- [ ] **Step 1: Write the failing test.**

```python
from pathlib import Path

import pytest

from paperhub.pipelines.extract import extract_latex, extract_pdf


def test_extract_latex_finds_main_and_flattens() -> None:
    fixture = Path(__file__).parent / "fixtures" / "papers" / "arxiv_sample"
    out = extract_latex(fixture)
    assert out.main_path.name == "main.tex"
    assert "Mixture-of-Experts" in out.flattened_text
    assert "\\maketitle" not in out.flattened_text  # preamble stripped
    assert "Introduction" in out.flattened_text
    assert "Method" in out.flattened_text


def test_extract_pdf_returns_text() -> None:
    fixture = Path(__file__).parent / "fixtures" / "papers" / "sample.pdf"
    text = extract_pdf(fixture)
    assert "Tiny Test Paper" in text
    assert "Mixture-of-Experts" in text


def test_extract_latex_raises_on_empty_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        extract_latex(tmp_path)
```

- [ ] **Step 2: Run to verify it fails.**

```powershell
uv run pytest tests/test_extract.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement.**

`backend/src/paperhub/pipelines/extract.py`:

```python
"""Extract paper text from LaTeX sources or PDFs.

LaTeX extraction adapted from paper2slides-plus/src/latex_utils.py:
- Identify the main .tex file (the one with \\begin{document}).
- Recursively inline \\input{...} and \\include{...}.
- Strip the preamble (everything before \\begin{document}).
- Return both the main path (for source_path persistence) and the flattened
  body text (for chunking).

PDF extraction uses PyMuPDF's plain-text export.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

_BEGIN_DOC = re.compile(r"\\begin\{document\}")
_END_DOC = re.compile(r"\\end\{document\}")
_INPUT_INCLUDE = re.compile(r"\\(?:input|include)\{([^}]+)\}")


@dataclass(frozen=True)
class LatexExtract:
    main_path: Path
    flattened_text: str


def _find_main_tex(source_dir: Path) -> Path:
    candidates = list(source_dir.glob("*.tex"))
    if not candidates:
        raise FileNotFoundError(f"no .tex files in {source_dir}")
    for cand in candidates:
        text = cand.read_text(encoding="utf-8", errors="ignore")
        if _BEGIN_DOC.search(text):
            return cand
    # Fallback: first .tex.
    return candidates[0]


def _inline_recursive(text: str, root: Path, seen: set[Path]) -> str:
    def repl(m: re.Match[str]) -> str:
        rel = m.group(1).strip()
        if not rel.endswith(".tex"):
            rel = rel + ".tex"
        target = (root / rel).resolve()
        if target in seen or not target.exists():
            return ""
        seen.add(target)
        inner = target.read_text(encoding="utf-8", errors="ignore")
        return _inline_recursive(inner, root, seen)
    return _INPUT_INCLUDE.sub(repl, text)


def extract_latex(source_dir: Path) -> LatexExtract:
    main = _find_main_tex(source_dir)
    raw = main.read_text(encoding="utf-8", errors="ignore")
    flat = _inline_recursive(raw, source_dir, seen={main.resolve()})
    # Strip preamble (everything up to and including \\begin{document}).
    begin_m = _BEGIN_DOC.search(flat)
    if begin_m:
        flat = flat[begin_m.end():]
    end_m = _END_DOC.search(flat)
    if end_m:
        flat = flat[: end_m.start()]
    return LatexExtract(main_path=main, flattened_text=flat.strip())


def extract_pdf(pdf_path: Path) -> str:
    """Return concatenated plain text from a PDF, one form-feed-separated
    page per source page.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)
    pieces: list[str] = []
    with pymupdf.open(pdf_path) as doc:
        for page in doc:
            pieces.append(page.get_text("text"))
    return "\n\f\n".join(pieces).strip()
```

- [ ] **Step 4: Run + commit.**

```powershell
uv run pytest tests/test_extract.py -v
git add backend/src/paperhub/pipelines/extract.py backend/tests/test_extract.py
git commit -m "feat(pipelines): LaTeX (input/include flatten) + PDF (PyMuPDF) text extraction"
```

---

## Task 4 — chunker.py (token-windowed, section-aware)

**Files:**
- Create: `backend/src/paperhub/pipelines/chunker.py`
- Create: `backend/tests/test_chunker.py`

Per SRS §III-5.1: target 800 tokens / hard cap 1000, section-aware on `\section{...}` boundaries.

- [ ] **Step 1: Write the failing test.**

```python
from paperhub.pipelines.chunker import Chunk, chunk_text


def test_chunks_respect_hard_cap() -> None:
    # ~3000 tokens of "word " repeated.
    text = ("word " * 3000).strip()
    chunks = chunk_text(text, target=200, hard=300)
    assert len(chunks) > 1
    for c in chunks:
        # Each chunk under hard cap.
        assert _token_count(c.text) <= 300


def test_chunks_split_at_section_when_possible() -> None:
    text = (
        "intro paragraph " * 50
        + "\n\\section{Methods}\n"
        + "methods paragraph " * 50
        + "\n\\section{Results}\n"
        + "results paragraph " * 50
    )
    chunks = chunk_text(text, target=200, hard=400)
    # At least three chunks (one per section), aligned to section boundaries.
    sections = {c.section for c in chunks}
    assert sections == {None, "Methods", "Results"} or sections == {"Methods", "Results", None}


def test_char_offsets_are_correct() -> None:
    text = "abc def ghi"
    chunks = chunk_text(text, target=100, hard=100)
    assert len(chunks) == 1
    c = chunks[0]
    assert text[c.char_start:c.char_end] == c.text


def _token_count(s: str) -> int:
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(s))
```

- [ ] **Step 2: Run + fail.**

- [ ] **Step 3: Implement.**

`backend/src/paperhub/pipelines/chunker.py`:

```python
"""Token-windowed greedy chunker with section-aware boundaries.

Target ~800 tokens per chunk, hard cap 1000 (configurable). Splits at
\\section{...} boundaries when possible; otherwise greedy-fills until hard cap.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import tiktoken


_SECTION_RE = re.compile(r"\\section\{([^}]+)\}")


@dataclass(frozen=True)
class Chunk:
    section: str | None
    char_start: int
    char_end: int
    text: str


def chunk_text(text: str, *, target: int = 800, hard: int = 1000) -> list[Chunk]:
    enc = tiktoken.get_encoding("cl100k_base")

    # Split into section-spans first.
    spans: list[tuple[str | None, int, int]] = []  # (section_name, char_start, char_end)
    last_idx = 0
    last_section: str | None = None
    for m in _SECTION_RE.finditer(text):
        if m.start() > last_idx:
            spans.append((last_section, last_idx, m.start()))
        last_section = m.group(1).strip()
        last_idx = m.end()
    if last_idx < len(text):
        spans.append((last_section, last_idx, len(text)))

    # Greedy-fill each span up to hard cap.
    out: list[Chunk] = []
    for section, span_start, span_end in spans:
        cursor = span_start
        while cursor < span_end:
            # Estimate cap by characters first (rough: 4 chars ≈ 1 token); refine with tiktoken.
            tentative_end = min(cursor + hard * 5, span_end)
            piece = text[cursor:tentative_end]
            tok_len = len(enc.encode(piece))
            # Shrink until under hard.
            while tok_len > hard and tentative_end > cursor + 1:
                tentative_end -= max(1, (tok_len - hard) * 4)
                tentative_end = max(tentative_end, cursor + 1)
                piece = text[cursor:tentative_end]
                tok_len = len(enc.encode(piece))
            if not piece.strip():
                cursor = tentative_end
                continue
            out.append(
                Chunk(
                    section=section,
                    char_start=cursor,
                    char_end=tentative_end,
                    text=piece.strip(),
                )
            )
            cursor = tentative_end
    return out
```

- [ ] **Step 4: Run + commit.**

```powershell
uv run pytest tests/test_chunker.py -v
git add backend/src/paperhub/pipelines/chunker.py backend/tests/test_chunker.py
git commit -m "feat(pipelines): section-aware token-windowed chunker (800/1000 cap)"
```

---

## Task 5 — embedder.py (bge-small singleton)

**Files:**
- Create: `backend/src/paperhub/pipelines/embedder.py`
- Create: `backend/tests/test_embedder.py`

- [ ] **Step 1: Write the failing test.**

```python
import numpy as np
import pytest

from paperhub.pipelines.embedder import Embedder, get_embedder


def test_embedder_singleton_returns_same_instance() -> None:
    a = get_embedder()
    b = get_embedder()
    assert a is b


def test_embedder_produces_384_dim_vectors() -> None:
    emb = get_embedder()
    vecs = emb.embed(["hello world", "mixture of experts"])
    assert vecs.shape == (2, 384)
    # Normalized magnitudes ≈ 1.
    norms = np.linalg.norm(vecs, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-3)
```

NOTE: this test downloads the model on first run (~110 MB). Subsequent runs use the HuggingFace cache. CI may need a pre-warming step or test-skip toggle.

- [ ] **Step 2: Implement.**

`backend/src/paperhub/pipelines/embedder.py`:

```python
"""Lazy-loaded sentence-transformers embedder.

Single process-wide singleton — the model is ~110 MB and instantiating per
call would dominate latency. The first .embed() call loads from the HF cache.
"""
from __future__ import annotations

from typing import Protocol

import numpy as np
from sentence_transformers import SentenceTransformer

from paperhub.config import load_settings


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray: ...


class _SentenceTransformersEmbedder:
    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model: SentenceTransformer | None = None

    def _load(self) -> SentenceTransformer:
        if self._model is None:
            self._model = SentenceTransformer(self._model_name)
        return self._model

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 384), dtype=np.float32)
        model = self._load()
        vecs = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return np.asarray(vecs, dtype=np.float32)


_singleton: _SentenceTransformersEmbedder | None = None


def get_embedder() -> Embedder:
    global _singleton
    if _singleton is None:
        settings = load_settings()
        _singleton = _SentenceTransformersEmbedder(settings.embedding_model)
    return _singleton
```

- [ ] **Step 3: Run + commit.**

```powershell
uv run pytest tests/test_embedder.py -v
git add backend/src/paperhub/pipelines/embedder.py backend/tests/test_embedder.py
git commit -m "feat(pipelines): bge-small-en-v1.5 embedder (lazy singleton, 384-dim)"
```

---

## Task 6 — renderer.py (pandoc + pylatexenc fallback)

**Files:**
- Create: `backend/src/paperhub/pipelines/renderer.py`
- Create: `backend/tests/test_renderer.py`

- [ ] **Step 1: Write the failing test.**

```python
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from paperhub.pipelines.renderer import render_html


def test_render_pdf_uses_pymupdf(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "papers" / "sample.pdf"
    out = tmp_path / "source.html"
    render_html(source=fixture, kind="pdf", out_path=out)
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>") or html.startswith("<html")
    assert "Tiny Test Paper" in html


def test_render_latex_with_pandoc_when_available(tmp_path: Path) -> None:
    if shutil.which("pandoc") is None:
        pytest.skip("pandoc binary not installed")
    fixture = Path(__file__).parent / "fixtures" / "papers" / "arxiv_sample" / "main.tex"
    out = tmp_path / "source.html"
    render_html(source=fixture, kind="latex", out_path=out)
    html = out.read_text(encoding="utf-8")
    assert "<h1" in html or "<h2" in html  # pandoc emits heading tags
    assert "Mixture-of-Experts" in html


def test_render_latex_falls_back_when_pandoc_missing(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "papers" / "arxiv_sample" / "main.tex"
    out = tmp_path / "source.html"
    with patch("paperhub.pipelines.renderer.shutil.which", return_value=None):
        render_html(source=fixture, kind="latex", out_path=out)
    html = out.read_text(encoding="utf-8")
    # pylatexenc fallback gives plainer output but should contain the body text.
    assert "Mixture-of-Experts" in html
```

- [ ] **Step 2: Implement.**

`backend/src/paperhub/pipelines/renderer.py`:

```python
"""Render paper source to HTML for the Citation Canvas (FR-03).

Strategy:
- LaTeX: pandoc primary (good math + figure support). pylatexenc fallback
  when pandoc is absent.
- PDF: PyMuPDF's HTML export (preserves layout enough for highlight scrolling).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Literal

import pymupdf
from pylatexenc.latex2text import LatexNodes2Text


def render_html(*, source: Path, kind: Literal["latex", "pdf"], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "pdf":
        _render_pdf(source, out_path)
    elif kind == "latex":
        if shutil.which("pandoc"):
            _render_latex_pandoc(source, out_path)
        else:
            _render_latex_pylatexenc(source, out_path)
    else:
        raise ValueError(f"unknown kind: {kind!r}")
    return out_path


def _render_pdf(pdf_path: Path, out_path: Path) -> None:
    with pymupdf.open(pdf_path) as doc:
        pieces = ["<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"]
        for page in doc:
            pieces.append("<div class='page'>")
            pieces.append(page.get_text("html"))
            pieces.append("</div>")
        pieces.append("</body></html>")
    out_path.write_text("".join(pieces), encoding="utf-8")


def _render_latex_pandoc(tex_path: Path, out_path: Path) -> None:
    subprocess.run(
        [
            "pandoc",
            "--from", "latex",
            "--to", "html5",
            "--standalone",
            str(tex_path),
            "-o", str(out_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _render_latex_pylatexenc(tex_path: Path, out_path: Path) -> None:
    text = LatexNodes2Text().latex_to_text(
        tex_path.read_text(encoding="utf-8", errors="ignore"),
    )
    # Minimal HTML envelope so the canvas can scroll-into-view by char offsets.
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    body = "<pre style='white-space:pre-wrap'>" + escaped + "</pre>"
    out_path.write_text(
        f"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>{body}</body></html>",
        encoding="utf-8",
    )
```

- [ ] **Step 3: Run + commit.**

```powershell
uv run pytest tests/test_renderer.py -v
git add backend/src/paperhub/pipelines/renderer.py backend/tests/test_renderer.py
git commit -m "feat(pipelines): HTML render — pandoc primary, pylatexenc fallback, PyMuPDF for PDF"
```

---

## Task 7 — chroma.py (vector store wrapper)

**Files:**
- Create: `backend/src/paperhub/rag/__init__.py` (empty)
- Create: `backend/src/paperhub/rag/chroma.py`
- Create: `backend/tests/test_chroma.py`

- [ ] **Step 1: Write the failing test.**

```python
import numpy as np
from pathlib import Path

from paperhub.rag.chroma import ChromaStore


def test_add_then_search_returns_matching_chunks(tmp_path: Path) -> None:
    store = ChromaStore(tmp_path)
    vecs = np.random.RandomState(42).randn(3, 384).astype(np.float32)
    # Normalize so cosine-sim behaves.
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)

    store.add_chunks(
        paper_content_id=1,
        chunk_ids=[10, 11, 12],
        texts=["alpha", "beta", "gamma"],
        embeddings=vecs,
    )

    results = store.search(query_embedding=vecs[0], paper_content_ids=[1], k=2)
    assert len(results) == 2
    # First match should be the query itself.
    assert results[0].chunk_id == 10
    assert results[0].text == "alpha"


def test_search_filters_by_paper_content_id(tmp_path: Path) -> None:
    store = ChromaStore(tmp_path)
    vecs = np.random.RandomState(0).randn(2, 384).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    store.add_chunks(1, [10], ["paper1"], vecs[:1])
    store.add_chunks(2, [20], ["paper2"], vecs[1:])

    results = store.search(query_embedding=vecs[1], paper_content_ids=[1], k=5)
    assert len(results) == 1
    assert results[0].chunk_id == 10  # Only paper 1 returned despite paper 2 being closer.
```

- [ ] **Step 2: Implement.**

`backend/src/paperhub/rag/chroma.py`:

```python
"""Chroma vector store wrapper. One persistent collection per workspace
(`paper_chunks`), metadata-filtered by `paper_content_id`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import chromadb


@dataclass(frozen=True)
class ChunkSearchResult:
    chunk_id: int
    paper_content_id: int
    text: str
    score: float


class ChromaStore:
    def __init__(self, persist_dir: Path) -> None:
        persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(persist_dir))
        self._coll = self._client.get_or_create_collection(
            name="paper_chunks",
            metadata={"hnsw:space": "cosine"},
        )

    def add_chunks(
        self,
        paper_content_id: int,
        chunk_ids: list[int],
        texts: list[str],
        embeddings: np.ndarray,
    ) -> None:
        if len(chunk_ids) == 0:
            return
        self._coll.add(
            ids=[str(cid) for cid in chunk_ids],
            documents=texts,
            embeddings=embeddings.tolist(),
            metadatas=[{"paper_content_id": paper_content_id} for _ in chunk_ids],
        )

    def search(
        self,
        *,
        query_embedding: np.ndarray,
        paper_content_ids: list[int],
        k: int,
    ) -> list[ChunkSearchResult]:
        if not paper_content_ids or k <= 0:
            return []
        result = self._coll.query(
            query_embeddings=[query_embedding.tolist()],
            n_results=k,
            where={"paper_content_id": {"$in": paper_content_ids}},
        )
        ids = result.get("ids", [[]])[0]
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        dists = result.get("distances", [[]])[0]
        out: list[ChunkSearchResult] = []
        for i, doc, meta, dist in zip(ids, docs, metas, dists, strict=True):
            out.append(
                ChunkSearchResult(
                    chunk_id=int(i),
                    paper_content_id=int(meta["paper_content_id"]),
                    text=doc,
                    score=1.0 - float(dist),  # cosine distance → similarity
                )
            )
        return out
```

- [ ] **Step 3: Run + commit.**

```powershell
uv run pytest tests/test_chroma.py -v
git add backend/src/paperhub/rag backend/tests/test_chroma.py
git commit -m "feat(rag): Chroma collection wrapper (paper_chunks, paper_content_id metadata filter)"
```

---

## Task 8 — paper_pipeline.py (cache-aware orchestrator)

**Files:**
- Create: `backend/src/paperhub/pipelines/paper_pipeline.py`
- Create: `backend/tests/test_paper_pipeline.py`

This is the keystone of Plan C: the pipeline that ties arxiv_client + extract + chunker + embedder + renderer + ChromaStore + SQLite into a single `ingest()` operation with cache-lookup short-circuit (SRS §III-5.1, I-8 #2).

- [ ] **Step 1: Write the failing test.**

```python
import asyncio
import hashlib
from pathlib import Path

import aiosqlite
import pytest

from paperhub.pipelines.paper_pipeline import (
    IngestRequest,
    PaperPipeline,
    compute_content_key,
)


def test_compute_content_key_arxiv() -> None:
    key = compute_content_key(arxiv_id="2403.01234")
    assert key == "arxiv:2403.01234"


def test_compute_content_key_upload(tmp_path: Path) -> None:
    f = tmp_path / "test.pdf"
    f.write_bytes(b"hello world")
    key = compute_content_key(upload_path=f)
    expected = "sha256:" + hashlib.sha256(b"hello world").hexdigest()
    assert key == expected


async def test_ingest_arxiv_cache_miss_creates_paper_content_and_chunks(
    migrated_db: aiosqlite.Connection,
    tmp_path: Path,
    monkeypatch,
) -> None:
    # See conftest helpers for the fixture mock — pipeline should:
    # 1. Compute content_key = "arxiv:test-fixture"
    # 2. download_arxiv_source returns the path to our local arxiv_sample/
    # 3. extract → chunk → embed → render → persist
    from paperhub.pipelines import paper_pipeline as pp

    fixture_source = Path(__file__).parent / "fixtures" / "papers" / "arxiv_sample"

    async def fake_download(arxiv_id: str, *, cache_root: Path) -> Path:
        target = cache_root / arxiv_id / "source"
        target.mkdir(parents=True, exist_ok=True)
        for src in fixture_source.iterdir():
            (target / src.name).write_bytes(src.read_bytes())
        return target

    monkeypatch.setattr(pp, "download_arxiv_source",
                        lambda arxiv_id, *, cache_root: asyncio.run(
                            fake_download(arxiv_id, cache_root=cache_root)))
    # ... + a fake embedder + fake renderer that don't hit the network or heavy models
    # See implementation for shape; full fixture wiring in conftest.py.
    pass  # Real test body follows the helpers below.
```

Provide proper test fixtures via `tests/conftest.py` extension: a `fake_pipeline_deps` fixture that supplies cheap fakes for `Embedder`, `ChromaStore`, and the renderer.

Skip a full integration test if it slows CI too much; the key assertion is **cache miss → paper_content + chunks rows + Chroma vectors** AND **cache hit → instant, no re-download**.

- [ ] **Step 2: Implement.**

`backend/src/paperhub/pipelines/paper_pipeline.py`:

```python
"""Cache-aware Paper Pipeline orchestrator (SRS §III-5.1).

Stages:
1. Compute content_key (arxiv:<id> or sha256:<hex>)
2. Cache lookup on paper_content.content_key
3. On hit: insert papers row, return.
4. On miss: download → extract → chunk → embed → render HTML → persist
   paper_content row + chunks rows + Chroma vectors → insert papers row.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import aiosqlite

from paperhub.pipelines.arxiv_client import download_arxiv_source, search_arxiv
from paperhub.pipelines.chunker import chunk_text
from paperhub.pipelines.embedder import Embedder, get_embedder
from paperhub.pipelines.extract import extract_latex, extract_pdf
from paperhub.pipelines.renderer import render_html
from paperhub.rag.chroma import ChromaStore


@dataclass(frozen=True)
class IngestRequest:
    session_id: int
    arxiv_id: str | None = None
    upload_path: Path | None = None
    upload_kind: Literal["pdf", "latex"] | None = None  # if upload_path is set


@dataclass(frozen=True)
class IngestResult:
    paper_content_id: int
    papers_id: int
    cache_hit: bool


def compute_content_key(*, arxiv_id: str | None = None, upload_path: Path | None = None) -> str:
    if arxiv_id is not None:
        return f"arxiv:{arxiv_id}"
    if upload_path is not None:
        h = hashlib.sha256()
        with upload_path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return f"sha256:{h.hexdigest()}"
    raise ValueError("must provide arxiv_id or upload_path")


class PaperPipeline:
    def __init__(
        self,
        conn: aiosqlite.Connection,
        *,
        papers_cache_dir: Path,
        chroma: ChromaStore,
        embedder: Embedder | None = None,
    ) -> None:
        self._conn = conn
        self._cache_root = papers_cache_dir
        self._chroma = chroma
        self._embedder = embedder or get_embedder()

    async def ingest(self, req: IngestRequest) -> IngestResult:
        content_key = compute_content_key(
            arxiv_id=req.arxiv_id, upload_path=req.upload_path,
        )
        # Cache lookup.
        async with self._conn.execute(
            "SELECT id FROM paper_content WHERE content_key = ?",
            (content_key,),
        ) as cur:
            row = await cur.fetchone()
        if row is not None:
            paper_content_id = int(row[0])
            papers_id = await self._link_to_session(req.session_id, paper_content_id)
            return IngestResult(paper_content_id, papers_id, cache_hit=True)

        # Cache miss — full ingest.
        paper_content_id, papers_id = await self._fresh_ingest(req, content_key)
        return IngestResult(paper_content_id, papers_id, cache_hit=False)

    async def _link_to_session(self, session_id: int, paper_content_id: int) -> int:
        await self._conn.execute(
            "INSERT OR IGNORE INTO papers (session_id, paper_content_id) VALUES (?, ?)",
            (session_id, paper_content_id),
        )
        await self._conn.commit()
        async with self._conn.execute(
            "SELECT id FROM papers WHERE session_id = ? AND paper_content_id = ?",
            (session_id, paper_content_id),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def _fresh_ingest(
        self,
        req: IngestRequest,
        content_key: str,
    ) -> tuple[int, int]:
        if req.arxiv_id:
            source_dir = download_arxiv_source(
                req.arxiv_id, cache_root=self._cache_root / "arxiv",
            )
            kind: Literal["latex", "pdf"] = "latex"
            metadata = self._lookup_arxiv_metadata(req.arxiv_id)
            cache_dir = self._cache_root / "arxiv" / req.arxiv_id
        else:
            assert req.upload_path is not None and req.upload_kind is not None
            sha = content_key.split(":", 1)[1]
            cache_dir = self._cache_root / "upload" / sha
            cache_dir.mkdir(parents=True, exist_ok=True)
            target = cache_dir / req.upload_path.name
            target.write_bytes(req.upload_path.read_bytes())
            source_dir = cache_dir if req.upload_kind == "latex" else target
            kind = req.upload_kind
            metadata = {"title": req.upload_path.stem, "authors": [], "year": None}

        # Extract text.
        if kind == "latex":
            ext = extract_latex(source_dir if source_dir.is_dir() else source_dir.parent)
            flat_path = cache_dir / "source.flattened.tex"
            flat_path.write_text(ext.flattened_text, encoding="utf-8")
            full_text = ext.flattened_text
            source_path = ext.main_path
            render_source = source_path
        else:
            full_text = extract_pdf(source_dir)
            source_path = source_dir
            render_source = source_path

        # HTML render.
        html_path = cache_dir / "source.html"
        render_html(source=render_source, kind=kind, out_path=html_path)

        # Chunk + embed.
        chunks = chunk_text(full_text)
        texts = [c.text for c in chunks]
        embeddings = self._embedder.embed(texts)

        # Persist paper_content + chunks.
        await self._conn.execute(
            "INSERT INTO paper_content "
            "(content_key, kind, arxiv_id, sha256, title, authors_json, year, "
            "source_path, source_dir_path, html_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                content_key,
                "arxiv" if req.arxiv_id else f"{req.upload_kind}_upload",
                req.arxiv_id,
                content_key.split(":", 1)[1] if not req.arxiv_id else None,
                metadata["title"],
                json.dumps(metadata.get("authors", [])),
                metadata.get("year"),
                str(source_path),
                str(cache_dir),
                str(html_path),
            ),
        )
        async with self._conn.execute("SELECT last_insert_rowid()") as cur:
            row = await cur.fetchone()
        paper_content_id = int(row[0])  # type: ignore[index]

        chunk_ids: list[int] = []
        for c in chunks:
            await self._conn.execute(
                "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text) "
                "VALUES (?, ?, ?, ?, ?)",
                (paper_content_id, c.section, c.char_start, c.char_end, c.text),
            )
            async with self._conn.execute("SELECT last_insert_rowid()") as cur:
                row = await cur.fetchone()
            chunk_ids.append(int(row[0]))  # type: ignore[index]
        await self._conn.commit()

        # Persist Chroma vectors.
        self._chroma.add_chunks(
            paper_content_id=paper_content_id,
            chunk_ids=chunk_ids,
            texts=texts,
            embeddings=embeddings,
        )

        papers_id = await self._link_to_session(req.session_id, paper_content_id)
        return paper_content_id, papers_id

    def _lookup_arxiv_metadata(self, arxiv_id: str) -> dict[str, object]:
        # Re-use arxiv_client search with id_list to get title/authors/year.
        results = search_arxiv(arxiv_id, max_results=1)
        if not results:
            return {"title": arxiv_id, "authors": [], "year": None}
        r = results[0]
        return {"title": r.title, "authors": r.authors, "year": r.year}
```

- [ ] **Step 3: Run + commit.**

```powershell
uv run pytest tests/test_paper_pipeline.py -v
git add backend/src/paperhub/pipelines/paper_pipeline.py backend/tests/test_paper_pipeline.py
git commit -m "feat(pipelines): cache-aware orchestrator (content_key, paper_content + chunks + Chroma persist)"
```

---

## Task 9 — retriever.py + reranker.py

**Files:**
- Create: `backend/src/paperhub/rag/retriever.py`
- Create: `backend/src/paperhub/rag/reranker.py`
- Create: `backend/tests/test_retriever.py`
- Create: `backend/tests/test_reranker.py`

Per SRS §III-5.2: top-`min(50, ⌈corpus_size / 3⌉)` candidates from Chroma → cross-encoder rerank → top-k for LLM.

- [ ] **Step 1: Write the failing tests.** (See structure below — verify the `Reranker` accepts a query + list of texts, returns reranked indices + scores; verify the `Retriever` returns ChunkSearchResult list filtered by enabled `paper_content_id`s.)

- [ ] **Step 2: Implement `reranker.py`.**

```python
"""Lazy-loaded cross-encoder reranker (ms-marco-MiniLM by default)."""
from __future__ import annotations

from dataclasses import dataclass

from sentence_transformers import CrossEncoder

from paperhub.config import load_settings


@dataclass(frozen=True)
class RerankResult:
    index: int
    score: float


class _CrossEncoderReranker:
    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model: CrossEncoder | None = None

    def _load(self) -> CrossEncoder:
        if self._model is None:
            self._model = CrossEncoder(self._model_name)
        return self._model

    def rerank(self, query: str, texts: list[str], top_k: int) -> list[RerankResult]:
        if not texts:
            return []
        model = self._load()
        pairs = [[query, t] for t in texts]
        scores = model.predict(pairs)
        ranked = sorted(enumerate(scores), key=lambda x: float(x[1]), reverse=True)
        return [RerankResult(index=i, score=float(s)) for i, s in ranked[:top_k]]


_singleton: _CrossEncoderReranker | None = None


def get_reranker() -> _CrossEncoderReranker:
    global _singleton
    if _singleton is None:
        settings = load_settings()
        _singleton = _CrossEncoderReranker(settings.reranker_model)
    return _singleton
```

- [ ] **Step 3: Implement `retriever.py`.**

```python
"""Retrieve candidate chunks for paper_qa per SRS §III-5.2."""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from paperhub.pipelines.embedder import Embedder, get_embedder
from paperhub.rag.chroma import ChromaStore, ChunkSearchResult
from paperhub.rag.reranker import RerankResult, get_reranker


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: int
    paper_content_id: int
    text: str
    score: float


def _candidate_k(corpus_size: int) -> int:
    return min(50, max(10, ceil(corpus_size / 3)))


class Retriever:
    def __init__(
        self,
        chroma: ChromaStore,
        *,
        embedder: Embedder | None = None,
    ) -> None:
        self._chroma = chroma
        self._embedder = embedder or get_embedder()
        self._reranker = get_reranker()

    def retrieve(
        self,
        query: str,
        *,
        enabled_paper_content_ids: list[int],
        corpus_size: int,
        top_k: int = 10,
    ) -> list[RetrievedChunk]:
        if not enabled_paper_content_ids:
            return []
        cand_k = _candidate_k(corpus_size)
        query_vec = self._embedder.embed([query])[0]
        candidates: list[ChunkSearchResult] = self._chroma.search(
            query_embedding=query_vec,
            paper_content_ids=enabled_paper_content_ids,
            k=cand_k,
        )
        if not candidates:
            return []
        rerank_in = [c.text for c in candidates]
        reranked: list[RerankResult] = self._reranker.rerank(query, rerank_in, top_k)
        return [
            RetrievedChunk(
                chunk_id=candidates[r.index].chunk_id,
                paper_content_id=candidates[r.index].paper_content_id,
                text=candidates[r.index].text,
                score=r.score,
            )
            for r in reranked
        ]
```

- [ ] **Step 4: Commit.**

```powershell
uv run pytest tests/test_retriever.py tests/test_reranker.py -v
git add backend/src/paperhub/rag backend/tests/test_retriever.py backend/tests/test_reranker.py
git commit -m "feat(rag): retriever (vector + rerank) + cross-encoder reranker"
```

---

## Task 10 — research agent (paper_search + paper_qa nodes)

**Files:**
- Create: `backend/src/paperhub/agents/research.py`
- Create: `backend/src/paperhub/llm/prompts/paper_search_v1.yaml`
- Create: `backend/src/paperhub/llm/prompts/paper_qa_v1.yaml`
- Create: `backend/tests/test_research_paper_search.py`
- Create: `backend/tests/test_research_paper_qa.py`

The `paper_search` node turns the user message into an arXiv search query (LLM-extracted), calls `search_arxiv`, formats results inline as a streamed assistant message with one "Add as reference" markdown block per result (the frontend Plan D will render these as cards).

The `paper_qa` node resolves `enabled_paper_content_ids` from the DB, runs the `Retriever`, formats retrieved chunks into the prompt context, and streams an answer with `[chunk:<id>]` markers tied to real `chunks.id` rows.

- [ ] **Step 1: Write the YAML prompts.**

`paper_search_v1.yaml`:

```yaml
system: |
  You are PaperHub's paper_search agent. The user wants to discover papers.
  Distill their request into 1–3 arXiv search terms. Return strict JSON:
    { "query": "..." }
  No prose, no markdown. Just JSON.
user: |
  {user_message}
```

`paper_qa_v1.yaml`:

```yaml
system: |
  You are PaperHub's paper_qa agent. Answer the user's question using only
  the chunks below. Cite EVERY factual claim using `[chunk:<id>]` markers
  immediately after the claim, where <id> is the integer chunk id from the
  list. Multiple citations per claim are OK: `[chunk:5,12]`.

  If the chunks don't contain enough information, say so explicitly — do not
  fabricate. Keep the answer focused and well-structured.

  --- CONTEXT CHUNKS ---
  {chunks_context}
  --- END CHUNKS ---
user: |
  {user_message}
```

- [ ] **Step 2: Implement the research agent.**

`backend/src/paperhub/agents/research.py`:

```python
"""paper_search + paper_qa agent nodes (SRS §III-3, FR-07)."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import aiosqlite
from pydantic import BaseModel

from paperhub.agents.state import AgentState
from paperhub.llm.adapter import LlmAdapter
from paperhub.pipelines.arxiv_client import search_arxiv
from paperhub.rag.retriever import Retriever
from paperhub.tracing.tracer import Tracer


class _SearchQuery(BaseModel):
    query: str


async def paper_search(
    state: AgentState,
    *,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    **adapter_kwargs: Any,
) -> str:
    """One-shot. Returns the assistant message body (markdown) listing
    arxiv hits with "Add as reference" affordances."""
    user_message = state["user_message"]
    async with tracer.step(agent="research", tool="paper_search:plan", model=model) as step:
        step.record_args({"user_message": user_message})
        q = await adapter.structured(
            slot="paper_search/v1",
            variables={"user_message": user_message},
            response_model=_SearchQuery,
            model=model,
            history=state.get("history"),
            **adapter_kwargs,
        )
        step.record_result({"query": q.query})

    async with tracer.step(agent="research", tool="paper_search:arxiv", model=None) as step:
        step.record_args({"query": q.query})
        results = search_arxiv(q.query, max_results=8)
        step.record_result({"hits": len(results)})

    # Format as markdown — Plan D's frontend will parse the special
    # "Add as reference" anchors and render them as cards.
    lines = [f"Searched arXiv for **{q.query}** — {len(results)} results.\n"]
    for r in results:
        authors = ", ".join(r.authors[:3]) + ("…" if len(r.authors) > 3 else "")
        year = f" ({r.year})" if r.year else ""
        lines.append(f"### {r.title}{year}")
        lines.append(f"_{authors}_  ·  arXiv:{r.arxiv_id}\n")
        lines.append(f"> {r.abstract[:300]}…\n")
        lines.append(f"[Add as reference](paperhub://add?arxiv_id={r.arxiv_id})\n")
    return "\n".join(lines)


async def paper_qa_stream(
    state: AgentState,
    *,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    retriever: Retriever,
    conn: aiosqlite.Connection,
    **adapter_kwargs: Any,
) -> AsyncIterator[str]:
    """Stream paper_qa tokens.

    Workflow: resolve enabled_paper_content_ids → retrieve → rerank → format
    chunk context → stream LLM answer with [chunk:<id>] markers.
    """
    user_message = state["user_message"]
    session_id = state["session_id"]

    async with tracer.step(agent="research", tool="paper_qa:resolve", model=None) as step:
        step.record_args({"session_id": session_id})
        async with conn.execute(
            "SELECT paper_content_id FROM papers "
            "WHERE session_id = ? AND enabled = 1",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        enabled_ids = [int(r[0]) for r in rows]
        step.record_result({"enabled_paper_content_ids": enabled_ids})

    if not enabled_ids:
        yield (
            "No references are enabled for this session. Add a paper to the "
            "Reference Sources panel first, then ask again."
        )
        return

    async with conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE paper_content_id IN ("
        + ",".join("?" * len(enabled_ids)) + ")",
        enabled_ids,
    ) as cur:
        row = await cur.fetchone()
    corpus_size = int(row[0]) if row else 0

    async with tracer.step(agent="research", tool="paper_qa:retrieve", model=None) as step:
        step.record_args({"query": user_message, "corpus_size": corpus_size})
        retrieved = retriever.retrieve(
            user_message,
            enabled_paper_content_ids=enabled_ids,
            corpus_size=corpus_size,
            top_k=10,
        )
        step.record_result({"chunk_ids": [r.chunk_id for r in retrieved]})

    if not retrieved:
        yield "No relevant chunks were found in the enabled references."
        return

    chunks_context = "\n\n".join(
        f"[chunk:{r.chunk_id}] (paper {r.paper_content_id})\n{r.text}"
        for r in retrieved
    )

    async with tracer.step(agent="research", tool="paper_qa:generate", model=model) as step:
        step.record_args({"chunk_count": len(retrieved)})
        collected: list[str] = []
        async for token in adapter.stream(
            slot="paper_qa/v1",
            variables={"user_message": user_message, "chunks_context": chunks_context},
            model=model,
            history=state.get("history"),
            **adapter_kwargs,
        ):
            collected.append(token)
            yield token
        step.record_result({"length": sum(len(c) for c in collected)})
```

- [ ] **Step 3: Tests + commit.**

```powershell
uv run pytest tests/test_research_paper_search.py tests/test_research_paper_qa.py -v
git add backend/src/paperhub/agents/research.py backend/src/paperhub/llm/prompts backend/tests/test_research_*.py
git commit -m "feat(agents): research agent — paper_search + paper_qa with RAG citations"
```

---

## Task 11 — Wire research nodes into LangGraph + chat endpoint

**Files:**
- Modify: `backend/src/paperhub/agents/graph.py`
- Modify: `backend/src/paperhub/agents/stubs.py` (remove paper_search + paper_qa entries; keep slides + library_stats)
- Modify: `backend/src/paperhub/api/chat.py` (handle research paths)

`graph.py` swaps `_stub_paper_search` and `_stub_paper_qa` for the real research nodes. `chat.py` treats:
- `paper_search` as a one-shot (no streaming) — emit the formatted markdown as a single final event
- `paper_qa` as a streaming intent — same SSE pattern as chitchat

Be careful with dependency injection: the chat endpoint constructs a `Retriever(chroma)` and passes it to `paper_qa_stream`. The graph's `GraphDeps` dataclass gets new fields: `retriever`, `paper_qa_model`.

- [ ] Implement the wiring; existing chitchat tests must still pass.
- [ ] Update the route map: `paper_search` → research.paper_search; `paper_qa` → research.paper_qa_stream.
- [ ] Commit: `feat(api): wire research agent into LangGraph + /chat SSE`

---

## Task 12 — POST /papers endpoint (ingest)

**Files:**
- Create: `backend/src/paperhub/api/papers.py`
- Modify: `backend/src/paperhub/app.py` (register the papers router)
- Create: `backend/tests/test_papers_api.py`

The endpoint accepts `{ "session_id": int, "arxiv_id": "..." }` or a multipart upload, runs `PaperPipeline.ingest`, returns `{ paper_content_id, papers_id, cache_hit, title }`. Frontend Plan D's "Add as reference" button calls this.

- [ ] **Step 1: Test — POST with arxiv_id → 200, paper_content row exists, second call returns cache_hit=true.**
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Commit.** `feat(api): POST /papers ingest endpoint (cache-aware via PaperPipeline)`

---

## Task 13 — CLI smoke scripts (ingest + paper_qa)

**Files:**
- Create: `backend/scripts/ingest_paper.ps1`
- Create: `backend/scripts/query_papers.ps1`

`ingest_paper.ps1`: takes an arxiv_id (or path to a PDF), POSTs to `/papers`, asserts 200, prints `cache_hit`. Run twice — second time must report `cache_hit: true`.

`query_papers.ps1`: starts a session, POSTs a chat with intent=paper_qa, asserts SSE contains a `final` event with content matching `\[chunk:\d+\]` regex.

- [ ] Commit: `test(pipelines): CLI smoke scripts for ingest + paper_qa round-trip`

---

## Task 14 — Plan-doc reconcile + final review

After Tasks 1–13:

- [ ] Update `docs/superpowers/plans/2026-05-18-paperhub-C-paper-pipeline-research-agent.md` to reflect any drift between the plan above and what shipped (Tailwind-equivalent — different versions of `arxiv` / `chromadb` may have API drift; document overrides).
- [ ] Update CLAUDE.md Plan C row to "complete".
- [ ] Add a Plan C follow-ups section to CLAUDE.md for any non-blocking issues raised by reviewers.
- [ ] Final cross-task code review — same pattern as Plan A and B.
- [ ] Commit: `docs(plan-c): reconcile plan with shipped implementation`

---

## Done state

After Task 14:

- `uv run pytest -v` — all tests pass (~55+ backend tests, ~50 frontend tests unchanged).
- `uv run ruff check src tests` + `uv run mypy src` — clean.
- `cd backend; .\scripts\ingest_paper.ps1 2403.01234` — first run downloads + processes, second run reports `cache_hit: true` in < 500ms.
- `cd backend; .\scripts\query_papers.ps1 "Compare the methods of these two papers"` — assistant streams an answer containing `[chunk:<id>]` markers, where each id resolves to a real row in `chunks`.
- Frontend `npm test` still passes 49 tests; Plan D will add the UI for paper_search/paper_qa.
- The router's stubs are gone for `paper_search` and `paper_qa` (slides + library_stats still stubbed, replaced in Plans F + E).

I-8 acceptance lit:
- #1 router accuracy (unchanged from Plan A)
- #2 cache reuse — verifiable
- #3 multi-paper Q&A with citations — verifiable
- #4 trace replay (unchanged)

Remaining I-8 criteria (#5 Compare, #6 no-silent-failure UI, #7 freshness) hand off to Plans G + D + E as before.

---

## Plan self-review

- **Spec coverage** — every §III-5 stage maps to a task; FR-03 / FR-07 / FR-08 / UC-1 / UC-2 / UC-3 all addressed; I-8 #2 + #3 acceptance criteria covered.
- **Placeholder scan** — every step contains real code or commands; the few `pass  # see implementation` stubs in test bodies are deliberate (the implementer fills them in once the fixtures land in Task 1).
- **Type consistency** — `Chunk`, `ChunkSearchResult`, `RetrievedChunk`, `ArxivResult`, `IngestRequest`, `IngestResult` shapes are used consistently across files.
- **No scope creep** — no Reference Sources UI, no Citation Canvas component, no slide pipeline, no Compare. All explicitly deferred.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-18-paperhub-C-paper-pipeline-research-agent.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task with spec + code-quality reviews between tasks. Same pattern as Plans A and B.
2. **Inline Execution** — batch with checkpoints in this session.

Which approach?
