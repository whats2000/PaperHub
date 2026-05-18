# PaperHub Plan C — Paper Pipeline + Research Agent

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the cache-aware Paper Pipeline (arXiv search + download + LaTeX/PDF extraction + chunking + embedding + Chroma persistence + HTML render for the Canvas), and replace the Plan-A `paper_search` and `paper_qa` stub nodes with a **library-aware, curating Research Agent** (SRS v2.3) that uses the pipeline end-to-end. The agent's mission is to **extend the session's reference set** on the user's behalf: it sees what's already in the session, decides whether to ask a clarifying question or search, prefers already-indexed library papers over re-downloading, refines up to N=3 times, default-adds the top 1-2 suggestions (immediately enabled), and reports what it did. The user retains full deterministic UI control via PATCH/DELETE/POST endpoints to adjust afterwards. CLI smoke proves: (a) re-ingesting a paper hits the cache (sub-second); (b) multi-paper Q&A returns answers with `[chunk:<id>]` citation markers tied to real `chunks.id` rows; (c) a paper_search turn with a vague prompt produces a clarifying question + no `search_arxiv` call; (d) a paper_search turn with a clear prompt produces at least one `add_paper_to_session` tool call, and the resulting `papers` row has `enabled=true`.

**Why this plan is the project's headline demo:** The course brief emphasizes (i) problem classification, (ii) tool-selection logic, (iii) model-answer-quality comparison, and (iv) tool-call traceability. Plan A's router covers (i); Plan G's Compare view covers (iii); FR-09 + the Plan B trace panel cover (iv). **The Research Agent's per-turn tool-calling loop in this plan covers (ii)** — the most visible "how the Agent picks tools" surface in the entire demo. Every `paper_search` turn in the trace panel will show a sequence like `search_library → (optional) search_arxiv ×1-3 → add_paper_to_session`, with the LLM's `reasoning` field surfaced on each step. That visible loop is what reviewers will grade.

**Architecture:** Single FastAPI process (unchanged from Plan A). Three new in-repo packages: `paperhub.pipelines` (ingest + Semantic Scholar helper), `paperhub.rag` (retrieval + rerank), and the expanded `paperhub.agents.research` module. The four research tools (`search_library`, `search_arxiv`, `find_related_papers`, `add_paper_to_session`) ship in this plan as MCP-compatible Python functions (typed args, structured returns, audit-traced); **Plan E wraps the module as the `paperhub-papers` MCP server** so the entire tool layer routes uniformly through MCP — the call signatures don't change, only the transport. Designing the tool contract right in Plan C keeps Plan E's MCP wrapping mechanical. Cache key is `content_key` (`arxiv:<id>` for arXiv, `sha256:<hex>` for uploads). All artefacts persist under `workspace/papers_cache/` with the layout from SRS §III-7. Chroma is file-backed under `workspace/chroma/` with one shared `paper_chunks` collection metadata-filtered by `paper_content_id`. Embeddings: `sentence-transformers/BAAI/bge-small-en-v1.5` (lazy singleton). Rerank: `cross-encoder/ms-marco-MiniLM-L-6-v2` (lazy singleton). HTML render: pandoc primary, pylatexenc fallback.

**Tech Stack additions:** `arxiv` (Python client), `pymupdf` (PDF text), `chromadb` (vector store), `sentence-transformers` (embeddings + cross-encoder), `pylatexenc` (LaTeX→HTML fallback), `tiktoken` (chunker tokenization), `httpx` (already a dep — used here for Semantic Scholar REST). System dep: `pandoc` (documented as optional — fallback handles its absence). New env var: `PAPERHUB_SEMANTIC_SCHOLAR_API_KEY` (optional, see `.env.example` — unauthenticated tier works at low volume). Everything else (FastAPI, LangGraph, LiteLLM, aiosqlite) is unchanged from Plan A.

---

## Spec Coverage Summary

| SRS reference | Addressed by |
| --- | --- |
| §III-5.1 Paper Pipeline (all 8 stages) | Tasks 2 – 8, Task 10 (orchestrator) |
| §III-5.2 paper_qa retrieval (top-k vector + cross-encoder rerank) | Tasks 9, 11 |
| §III-5.4 Chroma vector store keyed by `paper_content_id` | Task 9 |
| §III-7 `paper_content` + `chunks` writes | Task 10 (already migrated in Plan A) |
| FR-03 Citation Canvas (`paper_content.html_path` populated) | Task 8 |
| **FR-07 v2.3 Research Agent tool-calling loop** (`search_library` + `search_arxiv` + `add_paper_to_session`; library-first canonical flow; N=3 arXiv-call cap; default-add top 1-2) | Task 11 |
| **FR-08 v2.3 Reference set construction + adjustment** (agent extends via `add_paper_to_session` with `enabled=true`; user adjusts via PATCH/DELETE/POST endpoints) | Tasks 11, 12 |
| UC-1 v2.3 paper_search as library-aware curating loop | Task 11 |
| UC-2 v2.3 Add-as-reference (from search) + Add-from-library (manual) | Tasks 10, 12 |
| UC-3 Multi-paper Q&A with chunk-cited answers | Task 13 |
| I-8 #2 cache reuse (re-ingest is instant) | Tasks 10, 15 |
| I-8 #3 multi-paper Q&A returns ≥ 2 distinct `paper_content.id` | Tasks 13 + 15 |
| **I-8 #8 (new) clarify vs search decision** — vague prompt produces a clarifying question + zero `search_arxiv` calls | Task 15 (smoke) |
| **I-8 #9 (new) library-first preference** — when an indexed paper matches intent, `add_paper_to_session` is called with `cache_hit=true` before any `search_arxiv` invocation | Task 15 (smoke) |

**Out of scope for Plan C** (explicit Plan D / E / F / G handoffs):
- Reference Sources UI panel (Plan D — UI surface for the FR-08 toggle state and the new manual-attach affordances that the backend now exposes)
- Citation Canvas component (Plan D — consumes `html_path` and `chunks.char_start/char_end` written by Plan C)
- SearchResultList UI + "Add as reference" buttons + agent-added-paper toast (Plan D — calls `POST /papers` from Plan C; renders auto-added papers from the agent's final message)
- Library Browser UI (Plan D — calls `GET /papers/library` + `POST /papers/from-library`)
- SQL Agent + sqlite MCP (Plan E)
- Slide Pipeline (Plan F)
- Compare view (Plan G)

Plan C is verifiable end-to-end via CLI (`scripts/ingest_paper.ps1` + `scripts/query_papers.ps1` + `scripts/research_turn.ps1`); the user-facing surface lands in Plan D.

---

## Reconciliation log (Task 14)

The implementation diverged from the plan as written in these specific places. Each was a spec defect or a misalignment with what existing code actually exposed:

1. **`IngestResult.title`** — added by Task 10 (commit 44d899c). The plan's Task 8 dataclass omitted it but Task 10's `add_paper_to_session_dispatch` read it. `IngestResult` now has 4 fields, not 3.
2. **`paper_content.abstract` column** — added by Task 10 (commit 2cab8a1) because `search_library_dispatch` SQL selects `pc.abstract`. The plan listed neither the column nor the migration. (See follow-up #4.)
3. **`PaperPipeline` is NOT an `app.state` singleton.** It takes a positional `conn` arg, which is per-request. The plan's Task 11 sketch tried to construct one at lifespan time; the shipped implementation constructs `PaperPipeline` per-request inside `chat.py` and `papers.py`, using the shared `app.state.chroma` ChromaStore.
4. **`ChromaStore.aclose()` does not exist** — the plan's lifespan called it. The shipped lifespan does not.
5. **`get_renderer()` / `renderer=` kwarg does not exist** — renderer is module-level `render_html()` functions. Plan Task 11's PaperPipeline construction sketch incorrectly included `renderer=get_renderer()`; shipped code drops it.
6. **`settings.research_model` does not exist** — shipped code uses `settings.paper_qa_model` for both `paper_search` and `paper_qa` flows.
7. **`chunker.target` parameter is declared but unused** in the shipped impl — faithful to the plan's literal code, but the parameter is dead. (See follow-up #1.)
8. **`Chunk` char offsets aligned with stripped text** — the plan's chunker code stored `piece.strip()` as `text` while using raw cursor positions for `char_start`/`char_end`. Fixed in Task 4 commit `4e62b83`.
9. **Pandoc subprocess `cwd=tex_path.parent`** — the plan's renderer didn't set cwd; multi-file LaTeX `\input{...}` resolution would have broken in production. Fixed in commit `67a47cf`.
10. **Tarball unlink in finally** — Task 2's plan code would have leaked a tarball on extraction error. Fixed in commit `f4b0073`.
11. **Test fixtures: `fake_tracer`, `fake_pipeline`, `seed_library`** — the plan listed test cases but not fixture definitions. Added in `tests/conftest.py` by Task 10.
12. **`paper_qa_stream` was defined twice** in the plan's Task 10 code block (partial stub + full version). Shipped code has one definition (the full version).
13. **`%`-strip in `search_library_dispatch` LIKE escape** — the plan's escape `\%` was incorrect SQLite syntax. Shipped code strips `%` from the query as a Plan F follow-up.
14. **`respx` dev-dep added** in Task 10 for `test_semantic_scholar.py` HTTP mocking.
15. **`_FakeEmbedder` duplicate `embed()` method** — the plan's test fixture for `test_retriever.py` silently shadowed the first `embed()` with the second (Python method resolution). Fixed in commit `813da21` by merging into one definition with call tracking.
16. **PATCH/DELETE commit-before-rowcheck bug** — the plan's `papers.py` sketch committed the transaction before checking `rowcount`, meaning a 404 response would still commit a no-op write. Fixed in commit `ba9b2fe`. A 410 Gone path for missing `html_path` files was also added in the same fix.
17. **`ignore_errors = true` redundancy in mypy chromadb override** — the plan's `pyproject.toml` sketch included both `ignore_errors` and `ignore_missing_imports` for chromadb. `ignore_errors` was dropped in commit `00f1107` as redundant.

---

## File Structure

```
backend/
├── pyproject.toml                              # +6 deps (arxiv, pymupdf, chromadb, sentence-transformers, pylatexenc, tiktoken)
├── .env.example                                # +PAPERHUB_SEMANTIC_SCHOLAR_API_KEY (optional)
├── scripts/
│   ├── ingest_paper.ps1                        # NEW — CLI smoke for POST /papers ingest
│   ├── query_papers.ps1                        # NEW — CLI smoke for paper_qa with [chunk:N] assertion
│   └── research_turn.ps1                       # NEW — CLI smoke for paper_search agent (clarify / library-first / arxiv-fallback / default-add)
├── src/paperhub/
│   ├── pipelines/                              # NEW package
│   │   ├── __init__.py
│   │   ├── arxiv_client.py                     # arXiv search + download (sync; agent wraps in asyncio.to_thread)
│   │   ├── extract.py                          # LaTeX + PDF text extraction
│   │   ├── chunker.py                          # token-windowed, section-aware
│   │   ├── embedder.py                         # bge-small lazy singleton
│   │   ├── renderer.py                         # pandoc + pylatexenc fallback
│   │   ├── semantic_scholar.py                 # NEW (v2.3) — find_related_papers via SS REST API
│   │   └── paper_pipeline.py                   # cache-aware orchestrator
│   ├── rag/                                    # NEW package
│   │   ├── __init__.py
│   │   ├── chroma.py                           # ChromaStore wrapper
│   │   ├── retriever.py                        # vector search scoped to enabled papers
│   │   └── reranker.py                         # ms-marco-MiniLM lazy singleton
│   ├── agents/
│   │   ├── research.py                         # NEW — paper_search tool-calling loop + paper_qa streaming node
│   │   ├── research_tools.py                   # NEW (v2.3) — MCP-compatible tool defs (JSON-schema args + structured returns) and async dispatchers for search_library / search_arxiv / find_related_papers / add_paper_to_session. Plan E wraps this module as the `paperhub-papers` MCP server; in Plan C the agent invokes them as direct in-process Python calls. Contract is identical either way — only transport changes.
│   │   ├── stubs.py                            # drop paper_search/paper_qa entries (keep slides + library_stats)
│   │   └── graph.py                            # wire research nodes; stubs stay for the other 2
│   ├── api/
│   │   ├── papers.py                           # NEW — POST /papers, GET /papers/{id}/html, GET /papers/library, POST /papers/from-library, PATCH /papers/{id}, DELETE /papers/{id}
│   │   └── chat.py                             # add streaming for paper_qa, one-shot for paper_search
│   ├── llm/prompts/
│   │   ├── paper_search_v1.yaml                # NEW (v2.3) — tool-calling loop instructions: canonical library-first → clarify → arxiv → default-add flow
│   │   └── paper_qa_v1.yaml                    # NEW — answer with [chunk:<id>] citation markers
│   └── config.py                               # add CHROMA_DIR, EMBEDDING_MODEL, RERANKER_MODEL, SEMANTIC_SCHOLAR_API_KEY settings
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
    ├── test_semantic_scholar.py                # NEW (v2.3) — find_related_papers; HTTP mocked via respx
    ├── test_research_tools.py                  # NEW (v2.3) — tool dispatchers (search_library SQL, add_paper_to_session calls pipeline)
    ├── test_research_paper_search.py           # 5 cases: vague→clarify; clear→library-hit→add; clear→library-miss→arxiv→add; library-partial→arxiv-refine→add; arxiv-refine-cap (N=3)
    ├── test_research_paper_qa.py
    └── test_papers_api.py                      # POST /papers + GET /library + POST /from-library + PATCH /{id} + DELETE /{id}
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

## Task 10 — Research Agent (v2.3 tool-calling loop + paper_qa streaming)

**Files:**
- Create: `backend/src/paperhub/agents/research.py`
- Create: `backend/src/paperhub/agents/research_tools.py`
- Create: `backend/src/paperhub/pipelines/semantic_scholar.py`
- Create: `backend/src/paperhub/llm/prompts/paper_search_v1.yaml`
- Create: `backend/src/paperhub/llm/prompts/paper_qa_v1.yaml`
- Create: `backend/tests/test_semantic_scholar.py`
- Create: `backend/tests/test_research_tools.py`
- Create: `backend/tests/test_research_paper_search.py`
- Create: `backend/tests/test_research_paper_qa.py`

`paper_search` is now a **tool-calling loop** (SRS v2.3) — the agent reads `AgentState.history + user_message + current-references context block` and picks among four tools per turn. Canonical flow encoded in the prompt: **(1) query formable? → if no, ask clarifying question and stop; (2) call `search_library` first; (3) if hits cover intent → `add_paper_to_session` for top 1-2 → respond; (4) else `search_arxiv` (up to N=3 refined calls); (5) `add_paper_to_session` for top 1-2 → respond.** Three layers of deduplication (prompt context block, `search_library` excluding session, DB `UNIQUE` constraints) prevent duplicate work. Agent-added papers land with `enabled=true` so the next `paper_qa` turn sees them.

`paper_qa` keeps the linear pipeline from the prior draft: resolve `enabled_paper_content_ids` → retrieve → rerank → stream LLM answer with `[chunk:<id>]` markers.

- [ ] **Step 1: Write the YAML prompts.**

`paper_search_v1.yaml`:

```yaml
system: |
  You are PaperHub's Research Agent. Your job is to **extend the user's session
  reference set** with papers that actually help them. The user retains full
  control via the UI — you decide what to suggest and default-add the top 1-2.

  You see three contexts on every turn:
    1. Conversation history (prior turns)
    2. User's latest message
    3. CURRENT REFERENCES: every paper already enabled in this session
       (title, arxiv_id, year, short abstract). Do NOT propose any paper
       already in this list — search for genuine extensions or follow-ups.

  You have FOUR tools (call any combination; do not respond with prose until
  you've decided you're done):

    - search_library(query, max_results)
        Search the user's already-indexed library (deduplicated across all
        prior sessions, excluding papers already in this session). CHEAP —
        prefer this when the user might already have a matching paper indexed.

    - search_arxiv(query, max_results)
        External arXiv full-text search. Use when library doesn't cover the
        intent. You may call this up to 3 times per turn with refined queries
        if the first result set is weak. Hard cap: 3 calls per turn.

    - find_related_papers(arxiv_id, mode, max_results)
        Semantic Scholar citation-graph navigation. `mode` is one of:
          - "cites": papers cited by the target (its references)
          - "cited_by": papers that cite the target (forward citations,
            i.e. follow-up work) — USE THIS for "find me follow-up work to X"
          - "similar": Semantic Scholar's recommendation API
        Prefer this over search_arxiv when the user is asking for follow-up
        or related work to a SPECIFIC paper that already exists (in library
        or in arxiv).

    - add_paper_to_session(paper_id, reason)
        Attach a paper to the current session. Cache-aware: if the paper is
        already in the library (you saw it via search_library) it's a fast
        INSERT; if from arxiv it triggers the full Paper Pipeline.
        `paper_id` accepts either an `arxiv:<id>` string or a
        `library:<paper_content_id>` string. `reason` is a short string
        explaining WHY this paper matches — surfaced in the trace.
        The paper lands with `enabled=true`, immediately in scope for paper_qa.

  CANONICAL DECISION FLOW (follow this order unless you have a strong reason
  to deviate):

    1. Can you form a meaningful query from the message + context?
         NO  → respond with one short clarifying question. STOP. No tool call.
         YES → continue.

    2. Call search_library(query). Wait for results.

    3. Do library results cover the user's intent?
         YES → call add_paper_to_session for the best 1-2 library hits.
               Then respond with a short summary naming what you added.
               STOP.
         NO or PARTIAL → continue.

    4. Call search_arxiv(query). If the result set is weak, refine the query
       and call search_arxiv again (max 3 total search_arxiv calls per turn).

    5. Call add_paper_to_session for the best 1-2 arxiv results. Then respond
       with a short summary naming what you auto-added AND listing the
       remaining arxiv hits so the user can click "Add as reference" if they
       want more.

  Rules:
    - Never propose a paper already in CURRENT REFERENCES.
    - Never call add_paper_to_session for the same paper_id twice in one turn.
    - Prefer library hits over arxiv hits when both fit the user's intent.
    - Be terse. The user does not need an essay — just say what you did.
user: |
  CURRENT REFERENCES ({n_refs} papers in this session):
  {references_block}

  USER MESSAGE:
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

- [ ] **Step 2a: Semantic Scholar HTTP helper.**

`backend/src/paperhub/pipelines/semantic_scholar.py`:

```python
"""Semantic Scholar REST client for citation-graph navigation (SRS v2.3).

Public REST API, free tier (rate-limited to ~100 req / 5 min unauthenticated,
~1 req/s with PAPERHUB_SEMANTIC_SCHOLAR_API_KEY). No auth required for the
demo. See https://api.semanticscholar.org/api-docs/.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

import httpx

API_BASE = "https://api.semanticscholar.org/graph/v1"
_TIMEOUT = httpx.Timeout(10.0)
_FIELDS = "title,abstract,year,authors.name,externalIds"

Mode = Literal["cites", "cited_by", "similar"]


@dataclass(frozen=True)
class RelatedPaper:
    """Semantic Scholar result coerced into PaperHub shape.

    `arxiv_id` may be None if the related paper isn't on arXiv —
    `add_paper_to_session` will need a non-arXiv ingestion path or
    must skip it. For Plan C we surface but cannot ingest non-arxiv papers.
    """
    title: str
    abstract: str
    year: int | None
    authors: list[str]
    arxiv_id: str | None  # extracted from externalIds.ArXiv when present


def _headers() -> dict[str, str]:
    key = os.environ.get("PAPERHUB_SEMANTIC_SCHOLAR_API_KEY")
    return {"x-api-key": key} if key else {}


def _coerce(item: dict) -> RelatedPaper:
    return RelatedPaper(
        title=item.get("title") or "",
        abstract=item.get("abstract") or "",
        year=item.get("year"),
        authors=[a["name"] for a in item.get("authors") or [] if a.get("name")],
        arxiv_id=(item.get("externalIds") or {}).get("ArXiv"),
    )


async def find_related(
    arxiv_id: str,
    *,
    mode: Mode,
    max_results: int = 8,
) -> list[RelatedPaper]:
    """Return papers related to the given arXiv ID via Semantic Scholar.

    Caller is expected to wrap in tracer.step() — this helper is transport-only.
    """
    paper_id = f"arXiv:{arxiv_id}"
    if mode == "cites":
        url = f"{API_BASE}/paper/{paper_id}/references"
        items_key = "data"
        sub_key = "citedPaper"
    elif mode == "cited_by":
        url = f"{API_BASE}/paper/{paper_id}/citations"
        items_key = "data"
        sub_key = "citingPaper"
    else:  # similar
        url = f"{API_BASE}/paper/{paper_id}/related"
        items_key = "data"
        sub_key = None  # similar endpoint returns paper objects directly

    params = {"limit": str(max_results), "fields": _FIELDS}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(url, params=params, headers=_headers())
    resp.raise_for_status()
    raw = resp.json().get(items_key) or []
    items = [(r.get(sub_key) if sub_key else r) for r in raw]
    return [_coerce(i) for i in items if i]
```

- [ ] **Step 2b: Research tools module (MCP-compatible contract).**

`backend/src/paperhub/agents/research_tools.py`:

```python
"""Research Agent tool dispatchers (SRS v2.3, FR-07).

Each function here is intended to be exposed as a tool to the LLM.
Contracts (JSON-schema args, structured returns) are MCP-compatible —
Plan E wraps this module as the `paperhub-papers` MCP server with
zero call-shape changes.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Any

import aiosqlite

from paperhub.pipelines.arxiv_client import search_arxiv as _search_arxiv_sync
from paperhub.pipelines.paper_pipeline import IngestRequest, PaperPipeline
from paperhub.pipelines.semantic_scholar import Mode, find_related
from paperhub.tracing.tracer import Tracer


@dataclass(frozen=True)
class LibraryHit:
    paper_content_id: int
    arxiv_id: str | None
    title: str
    abstract: str
    year: int | None


@dataclass(frozen=True)
class ArxivHit:
    arxiv_id: str
    title: str
    abstract: str
    year: int | None
    authors: list[str]


@dataclass(frozen=True)
class AddResult:
    paper_content_id: int
    papers_id: int
    cache_hit: bool
    title: str


# The JSON-schemas LiteLLM (and later the MCP wrapper) hands to the LLM.
# Keep field names + descriptions stable across Plan C/E — they become
# part of the public MCP contract.
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_library",
            "description": (
                "Search the user's already-indexed paper library (deduplicated "
                "across all sessions). Excludes papers already attached to the "
                "current session. Cheap. Prefer this before search_arxiv."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Free-text search terms."},
                    "max_results": {"type": "integer", "default": 8, "minimum": 1, "maximum": 25},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_arxiv",
            "description": (
                "Search arXiv full-text. Use when search_library doesn't cover "
                "the intent. May be called up to 3 times per turn with refined "
                "queries — the loop enforces this cap."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 8, "minimum": 1, "maximum": 25},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_related_papers",
            "description": (
                "Citation-graph navigation via Semantic Scholar. Use when the "
                "user wants follow-up work to a specific paper (mode=cited_by), "
                "the references of a paper (mode=cites), or generally similar "
                "work (mode=similar). Prefer over search_arxiv when the user "
                "is asking 'what's next after paper X'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "arxiv_id": {"type": "string"},
                    "mode": {"type": "string", "enum": ["cites", "cited_by", "similar"]},
                    "max_results": {"type": "integer", "default": 8, "minimum": 1, "maximum": 25},
                },
                "required": ["arxiv_id", "mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_paper_to_session",
            "description": (
                "Attach a paper to the current session with enabled=true (so it "
                "is immediately in scope for paper_qa). paper_id accepts "
                "'arxiv:<id>' for arXiv ingestion or 'library:<paper_content_id>' "
                "for an already-indexed library paper. `reason` is a short "
                "human-readable string explaining WHY this paper matches the "
                "user's intent — surfaced in the trace for FR-02."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {
                        "type": "string",
                        "description": "Either 'arxiv:<id>' or 'library:<paper_content_id>'.",
                    },
                    "reason": {"type": "string"},
                },
                "required": ["paper_id", "reason"],
            },
        },
    },
]


async def search_library_dispatch(
    *, query: str, max_results: int = 8,
    conn: aiosqlite.Connection, session_id: int,
) -> list[LibraryHit]:
    """Full-text search across paper_content, excluding rows already in
    this session. SQLite has no FTS in the schema yet — use LIKE on
    title + abstract for Plan C; FTS5 is a Plan F follow-up.
    """
    # Match terms on title OR abstract; exclude already-attached.
    like = f"%{query.strip().replace('%', '\\%')}%"
    sql = (
        "SELECT pc.id, pc.arxiv_id, pc.title, pc.abstract, pc.year "
        "FROM paper_content pc "
        "WHERE (pc.title LIKE ? OR pc.abstract LIKE ?) "
        "  AND pc.id NOT IN ("
        "    SELECT paper_content_id FROM papers WHERE session_id = ?"
        "  ) "
        "ORDER BY pc.year DESC NULLS LAST "
        "LIMIT ?"
    )
    async with conn.execute(sql, (like, like, session_id, max_results)) as cur:
        rows = await cur.fetchall()
    return [
        LibraryHit(
            paper_content_id=int(r[0]),
            arxiv_id=r[1],
            title=r[2] or "",
            abstract=r[3] or "",
            year=int(r[4]) if r[4] is not None else None,
        )
        for r in rows
    ]


async def search_arxiv_dispatch(
    *, query: str, max_results: int = 8,
) -> list[ArxivHit]:
    """arxiv.Search.results() is sync + network-bound — wrap in to_thread
    to avoid blocking the event loop (review C4 fix)."""
    results = await asyncio.to_thread(_search_arxiv_sync, query, max_results)
    return [
        ArxivHit(
            arxiv_id=r.arxiv_id,
            title=r.title,
            abstract=r.abstract,
            year=r.year,
            authors=list(r.authors),
        )
        for r in results
    ]


async def find_related_papers_dispatch(
    *, arxiv_id: str, mode: Mode, max_results: int = 8,
) -> list[dict[str, Any]]:
    related = await find_related(arxiv_id, mode=mode, max_results=max_results)
    return [asdict(r) for r in related]


async def add_paper_to_session_dispatch(
    *, paper_id: str, reason: str,
    pipeline: PaperPipeline, conn: aiosqlite.Connection, session_id: int,
) -> AddResult:
    """`paper_id` discriminator: 'arxiv:<id>' triggers ingest; 'library:<int>'
    skips ingest and just inserts a papers row referencing the existing
    paper_content. Either path lands enabled=true (schema default)."""
    if paper_id.startswith("library:"):
        pcid = int(paper_id.removeprefix("library:"))
        # Idempotent: ON CONFLICT (UNIQUE session_id+paper_content_id) → no-op,
        # then SELECT the existing papers row.
        await conn.execute(
            "INSERT OR IGNORE INTO papers (session_id, paper_content_id) VALUES (?, ?)",
            (session_id, pcid),
        )
        await conn.commit()
        async with conn.execute(
            "SELECT p.id, pc.title FROM papers p JOIN paper_content pc ON pc.id = p.paper_content_id "
            "WHERE p.session_id = ? AND p.paper_content_id = ?",
            (session_id, pcid),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None, "library paper not found"
        return AddResult(paper_content_id=pcid, papers_id=int(row[0]),
                         cache_hit=True, title=row[1] or "")

    if paper_id.startswith("arxiv:"):
        arxiv_id = paper_id.removeprefix("arxiv:")
        result = await pipeline.ingest(IngestRequest(session_id=session_id, arxiv_id=arxiv_id))
        return AddResult(
            paper_content_id=result.paper_content_id,
            papers_id=result.papers_id,
            cache_hit=result.cache_hit,
            title=result.title,
        )

    raise ValueError(f"add_paper_to_session: unrecognised paper_id prefix in {paper_id!r}")
```

- [ ] **Step 2c: Implement the Research Agent loop + paper_qa stream.**

`backend/src/paperhub/agents/research.py`:

```python
"""Research Agent: paper_search tool-calling loop (SRS v2.3) + paper_qa stream."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Any

import aiosqlite
import litellm

from paperhub.agents.research_tools import (
    TOOL_SCHEMAS,
    add_paper_to_session_dispatch,
    find_related_papers_dispatch,
    search_arxiv_dispatch,
    search_library_dispatch,
)
from paperhub.agents.state import AgentState
from paperhub.llm.adapter import LlmAdapter
from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.pipelines.paper_pipeline import PaperPipeline
from paperhub.rag.retriever import Retriever
from paperhub.tracing.tracer import Tracer

MAX_ARXIV_CALLS_PER_TURN = 3
MAX_TOOL_ITERATIONS = 8  # hard ceiling: ~ search_library + 3 × search_arxiv + 2 × add + slack


async def _references_block(conn: aiosqlite.Connection, session_id: int) -> tuple[int, str]:
    async with conn.execute(
        "SELECT pc.arxiv_id, pc.title, pc.year, pc.abstract "
        "FROM papers p JOIN paper_content pc ON pc.id = p.paper_content_id "
        "WHERE p.session_id = ? AND p.enabled = 1 "
        "ORDER BY p.added_at",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    if not rows:
        return 0, "(none — this session has no references yet)"
    lines = []
    for r in rows:
        aid, title, year, abstract = r
        head = f"- [arxiv:{aid}] {title} ({year or 'n.d.'})" if aid else f"- {title} ({year or 'n.d.'})"
        snippet = (abstract or "")[:200].replace("\n", " ")
        lines.append(f"{head}\n  abstract: {snippet}{'…' if abstract and len(abstract) > 200 else ''}")
    return len(rows), "\n".join(lines)


async def paper_search(
    state: AgentState,
    *,
    adapter: LlmAdapter,  # kept for interface parity; agent uses litellm directly for tools
    tracer: Tracer,
    model: str,
    conn: aiosqlite.Connection,
    pipeline: PaperPipeline,
    registry: PromptRegistry | None = None,
    **litellm_kwargs: Any,
) -> str:
    """Tool-calling loop. Returns the final assistant message body (markdown).

    The chat endpoint surfaces this as a one-shot `final` SSE event — there
    is no token streaming inside paper_search (the trace panel + the
    automatic add_paper_to_session side-effects are what the user watches).
    """
    user_message = state["user_message"]
    session_id = state["session_id"]
    history = state.get("history") or []

    n_refs, refs_block = await _references_block(conn, session_id)
    reg = registry or PromptRegistry()
    prompt = reg.get("paper_search/v1")
    system = prompt.system
    user = prompt.user_template.format(
        n_refs=n_refs, references_block=refs_block, user_message=user_message,
    )

    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    messages.extend(history)
    messages.append({"role": "user", "content": user})

    arxiv_calls = 0
    for _iteration in range(MAX_TOOL_ITERATIONS):
        async with tracer.step(
            agent="research", tool="paper_search:plan", model=model,
        ) as step:
            step.record_args({"iteration": _iteration, "messages_len": len(messages)})
            response = await litellm.acompletion(
                model=model, messages=messages, tools=TOOL_SCHEMAS,
                tool_choice="auto", **litellm_kwargs,
            )
            msg = response["choices"][0]["message"]
            step.record_result({
                "had_tool_calls": bool(msg.get("tool_calls")),
                "content_len": len(msg.get("content") or ""),
            })

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            # Final response — clarification question OR summary of additions.
            return msg.get("content") or "(no response)"

        # Append the assistant turn that requested the tools, then dispatch each.
        messages.append({"role": "assistant", "content": msg.get("content"),
                         "tool_calls": tool_calls})

        for call in tool_calls:
            name = call["function"]["name"]
            args = json.loads(call["function"]["arguments"] or "{}")
            result: Any
            async with tracer.step(
                agent="research", tool=f"paper_search:{name}", model=None,
            ) as step:
                step.record_args({**args, "reason": args.get("reason")})
                try:
                    if name == "search_library":
                        result = [asdict(h) for h in await search_library_dispatch(
                            conn=conn, session_id=session_id, **args)]
                    elif name == "search_arxiv":
                        if arxiv_calls >= MAX_ARXIV_CALLS_PER_TURN:
                            result = {"error": "arxiv_call_cap_reached",
                                      "cap": MAX_ARXIV_CALLS_PER_TURN}
                        else:
                            arxiv_calls += 1
                            result = [asdict(h) for h in await search_arxiv_dispatch(**args)]
                    elif name == "find_related_papers":
                        result = await find_related_papers_dispatch(**args)
                    elif name == "add_paper_to_session":
                        result = asdict(await add_paper_to_session_dispatch(
                            pipeline=pipeline, conn=conn, session_id=session_id, **args))
                    else:
                        result = {"error": f"unknown_tool:{name}"}
                    step.record_result({"summary": result if isinstance(result, dict)
                                        else {"count": len(result)}})
                except Exception as exc:  # noqa: BLE001
                    result = {"error": str(exc), "tool": name}
                    step.record_result({"error": str(exc)})

            messages.append({
                "role": "tool", "tool_call_id": call["id"],
                "name": name, "content": json.dumps(result, default=str),
            })

    return ("I've reached the tool-call limit for this turn. "
            "Try asking again with a more specific question.")


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

- [ ] **Step 3: Tests.**

The Research Agent loop must be deterministic in tests despite using a real LiteLLM call shape. Two strategies, both used:

1. **`mock_response` / `mock_completion`** — `litellm.acompletion(..., mock_response=...)` returns a pre-canned response. For tool-calling tests, use `mock_response={"choices": [{"message": {"tool_calls": [...]}}]}` to drive the loop through a scripted sequence.
2. **Stubbed dispatchers** — monkey-patch the four `*_dispatch` functions in `research_tools` to return deterministic results without hitting arXiv / Semantic Scholar / Chroma.

`backend/tests/test_research_paper_search.py` covers five cases — each exercises a different branch of the canonical decision flow:

```python
"""Research Agent paper_search loop tests (SRS v2.3, FR-07)."""
from __future__ import annotations

from dataclasses import asdict
import json

import pytest
from unittest.mock import patch

from paperhub.agents.research import paper_search
from paperhub.agents.research_tools import ArxivHit, LibraryHit, AddResult

pytestmark = pytest.mark.asyncio


def _msg(content=None, tool_calls=None):
    """Build a fake LiteLLM response message."""
    m = {"role": "assistant", "content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    return {"choices": [{"message": m}]}


def _tool_call(call_id, name, args):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


# ---------- Case 1: vague prompt → clarifying question, zero tool calls ----------
async def test_vague_prompt_emits_clarifying_question(
    migrated_db, fake_tracer, fake_pipeline,
):
    state = {"run_id": 1, "branch": "", "session_id": 1,
             "user_message": "find me good ML papers"}
    seq = [_msg(content="What problem are you trying to solve — routing, "
                       "training stability, or something else?")]
    with patch("paperhub.agents.research.litellm.acompletion",
               side_effect=seq) as comp:
        out = await paper_search(state, adapter=None, tracer=fake_tracer,
                                 model="gemini/gemini-2.5-flash",
                                 conn=migrated_db, pipeline=fake_pipeline)
    assert "?" in out
    assert comp.call_count == 1
    # No tool dispatched
    assert "search_arxiv" not in out


# ---------- Case 2: clear prompt → library hit → add → respond (no arxiv) ----------
async def test_library_hit_skips_arxiv(
    migrated_db, fake_tracer, fake_pipeline, seed_library,
):
    """seed_library inserts 2 paper_content rows the agent can hit."""
    state = {"run_id": 2, "branch": "", "session_id": 1,
             "user_message": "I want the original transformer paper"}
    lib_hits = [LibraryHit(paper_content_id=42, arxiv_id="1706.03762",
                           title="Attention Is All You Need",
                           abstract="...", year=2017)]
    seq = [
        _msg(tool_calls=[_tool_call("c1", "search_library",
                                    {"query": "transformer", "max_results": 8})]),
        _msg(tool_calls=[_tool_call("c2", "add_paper_to_session",
                                    {"paper_id": "library:42",
                                     "reason": "the original transformer paper"})]),
        _msg(content="Added 'Attention Is All You Need' from your library."),
    ]
    with patch("paperhub.agents.research.litellm.acompletion", side_effect=seq), \
         patch("paperhub.agents.research.search_library_dispatch",
               return_value=lib_hits), \
         patch("paperhub.agents.research.add_paper_to_session_dispatch",
               return_value=AddResult(42, 99, cache_hit=True,
                                      title="Attention Is All You Need")) as add:
        out = await paper_search(state, adapter=None, tracer=fake_tracer,
                                 model="m", conn=migrated_db, pipeline=fake_pipeline)
    assert "Attention Is All You Need" in out
    add.assert_called_once()
    # I-8 #9: library-first preference — no search_arxiv ever called
    assert "search_arxiv" not in [c.args for c in add.mock_calls]


# ---------- Case 3: library miss → arxiv → add → respond ----------
async def test_library_miss_falls_through_to_arxiv(
    migrated_db, fake_tracer, fake_pipeline,
):
    state = {"run_id": 3, "branch": "", "session_id": 1,
             "user_message": "find me mixture-of-experts routing papers"}
    arx_hits = [ArxivHit(arxiv_id="2403.00001", title="MoE Routing X",
                         abstract="...", year=2024, authors=["A"])]
    seq = [
        _msg(tool_calls=[_tool_call("c1", "search_library",
                                    {"query": "mixture of experts routing"})]),
        _msg(tool_calls=[_tool_call("c2", "search_arxiv",
                                    {"query": "mixture of experts routing"})]),
        _msg(tool_calls=[_tool_call("c3", "add_paper_to_session",
                                    {"paper_id": "arxiv:2403.00001",
                                     "reason": "top MoE routing hit"})]),
        _msg(content="Added MoE Routing X from arXiv."),
    ]
    with patch("paperhub.agents.research.litellm.acompletion", side_effect=seq), \
         patch("paperhub.agents.research.search_library_dispatch", return_value=[]), \
         patch("paperhub.agents.research.search_arxiv_dispatch", return_value=arx_hits), \
         patch("paperhub.agents.research.add_paper_to_session_dispatch",
               return_value=AddResult(7, 12, cache_hit=False, title="MoE Routing X")):
        out = await paper_search(state, adapter=None, tracer=fake_tracer,
                                 model="m", conn=migrated_db, pipeline=fake_pipeline)
    assert "MoE Routing X" in out


# ---------- Case 4: arxiv refinement loop (N=2 calls, both succeed) ----------
async def test_arxiv_refinement_within_cap(
    migrated_db, fake_tracer, fake_pipeline,
):
    state = {"run_id": 4, "branch": "", "session_id": 1,
             "user_message": "find recent paper_qa work"}
    seq = [
        _msg(tool_calls=[_tool_call("c1", "search_library", {"query": "paper qa"})]),
        _msg(tool_calls=[_tool_call("c2", "search_arxiv", {"query": "paper QA"})]),
        # First arxiv call weak — refine
        _msg(tool_calls=[_tool_call("c3", "search_arxiv",
                                    {"query": "scientific paper question answering 2024"})]),
        _msg(tool_calls=[_tool_call("c4", "add_paper_to_session",
                                    {"paper_id": "arxiv:2404.00002", "reason": "best refined hit"})]),
        _msg(content="Added one paper after refining the query."),
    ]
    with patch("paperhub.agents.research.litellm.acompletion", side_effect=seq), \
         patch("paperhub.agents.research.search_library_dispatch", return_value=[]), \
         patch("paperhub.agents.research.search_arxiv_dispatch",
               side_effect=[[], [ArxivHit("2404.00002", "Paper QA", "...", 2024, [])]]), \
         patch("paperhub.agents.research.add_paper_to_session_dispatch",
               return_value=AddResult(8, 13, False, "Paper QA")):
        out = await paper_search(state, adapter=None, tracer=fake_tracer,
                                 model="m", conn=migrated_db, pipeline=fake_pipeline)
    assert "refining" in out.lower() or "Paper QA" in out


# ---------- Case 5: arxiv cap (N=3) enforced — 4th call returns cap error ----------
async def test_arxiv_cap_enforced_at_three(
    migrated_db, fake_tracer, fake_pipeline,
):
    """4th search_arxiv must NOT actually invoke the dispatcher;
    tool result returns {error: arxiv_call_cap_reached}."""
    state = {"run_id": 5, "branch": "", "session_id": 1,
             "user_message": "keep refining"}
    call4 = _tool_call("c4", "search_arxiv", {"query": "v4"})
    seq = [
        _msg(tool_calls=[_tool_call("c1", "search_arxiv", {"query": "v1"})]),
        _msg(tool_calls=[_tool_call("c2", "search_arxiv", {"query": "v2"})]),
        _msg(tool_calls=[_tool_call("c3", "search_arxiv", {"query": "v3"})]),
        _msg(tool_calls=[call4]),  # 4th — must be capped
        _msg(content="I've reached the search cap."),
    ]
    arx_dispatcher_calls = 0
    async def fake_arxiv(**_):
        nonlocal arx_dispatcher_calls
        arx_dispatcher_calls += 1
        return []
    with patch("paperhub.agents.research.litellm.acompletion", side_effect=seq), \
         patch("paperhub.agents.research.search_arxiv_dispatch", side_effect=fake_arxiv):
        await paper_search(state, adapter=None, tracer=fake_tracer,
                           model="m", conn=migrated_db, pipeline=fake_pipeline)
    assert arx_dispatcher_calls == 3  # dispatcher never invoked on the 4th
```

`backend/tests/test_research_paper_qa.py` reuses Plan A's pattern (mock `Retriever.retrieve` to return canned chunks; assert tokens stream + `[chunk:N]` appears in concatenated output). Plus one extra assertion for I-8 #3: with two seeded papers, the concatenated content cites at least two distinct `chunk_id`s whose `paper_content_id`s differ.

`backend/tests/test_research_tools.py` covers the dispatchers in isolation:
- `search_library_dispatch` excludes rows already in the session (insert a row in `papers`, confirm the matching `paper_content` is filtered).
- `add_paper_to_session_dispatch` with `library:<id>` is idempotent on the UNIQUE constraint (call twice, only one papers row).
- `add_paper_to_session_dispatch` with `arxiv:<id>` calls `PaperPipeline.ingest` with the right shape (use a fake pipeline that records calls).

`backend/tests/test_semantic_scholar.py` mocks `httpx.AsyncClient.get` via `respx` to return canned JSON for each of the three `mode` values; asserts `arxiv_id` is correctly extracted from `externalIds.ArXiv` (and is `None` when absent).

- [ ] **Step 4: Commit.**

```powershell
uv run pytest tests/test_semantic_scholar.py tests/test_research_tools.py `
              tests/test_research_paper_search.py tests/test_research_paper_qa.py -v
git add backend/src/paperhub/agents/research.py `
        backend/src/paperhub/agents/research_tools.py `
        backend/src/paperhub/pipelines/semantic_scholar.py `
        backend/src/paperhub/llm/prompts `
        backend/tests/test_semantic_scholar.py `
        backend/tests/test_research_tools.py `
        backend/tests/test_research_paper_*.py
git commit -m "feat(agents): v2.3 Research Agent — 4-tool loop (library/arxiv/semantic-scholar/add)"
```

---

## Task 11 — Wire research nodes into LangGraph + /chat SSE

**Files:**
- Modify: `backend/src/paperhub/agents/graph.py`
- Modify: `backend/src/paperhub/agents/stubs.py` (remove paper_search + paper_qa entries; keep slides + library_stats)
- Modify: `backend/src/paperhub/app.py` (construct singleton `ChromaStore` and `PaperPipeline` on `app.state`)
- Modify: `backend/src/paperhub/api/chat.py` (branch on `intent` for paper_search / paper_qa)
- Modify: `backend/tests/test_chat_sse.py` (add paper_search one-shot test + paper_qa streaming test)

Two dependency-lifecycle decisions matter:

1. **ChromaStore is a singleton on `app.state`.** Constructing it per-request would re-load the embedding model (~200 MB) and walk the persistent dir on every chat turn. Build it once in the FastAPI lifespan; reuse across requests.
2. **PaperPipeline is also `app.state`-scoped.** It holds the ChromaStore + a tracer factory; per-request construction is cheap since the heavyweight singletons live inside it.

- [ ] **Step 1: Add `app.state` singletons in `app.py`.**

```python
# backend/src/paperhub/app.py — additions inside lifespan
from paperhub.rag.chroma import ChromaStore
from paperhub.pipelines.paper_pipeline import PaperPipeline
from paperhub.pipelines.embedder import get_embedder
from paperhub.pipelines.renderer import get_renderer

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... existing schema migration ...
    app.state.chroma = ChromaStore(settings.chroma_dir)
    app.state.pipeline = PaperPipeline(
        chroma=app.state.chroma,
        embedder=get_embedder(),  # lazy singleton
        renderer=get_renderer(),
        cache_root=settings.papers_cache_dir,
    )
    try:
        yield
    finally:
        await app.state.chroma.aclose()
```

- [ ] **Step 2: Branch `chat.py` on intent.**

Plan A's `chat.py` has `if intent == "chitchat": ...`. Replace the body of `_run_chat` with an intent-driven dispatch. paper_search is **one-shot** (no token events — the trace panel does the visualisation); paper_qa **streams** like chitchat.

```python
# backend/src/paperhub/api/chat.py — sketch of the new branching block
# (the surrounding tracer / SSE-formatter / message-persistence machinery
# from Plan A is unchanged — only the `intent` dispatch is new)
from paperhub.agents.research import paper_search, paper_qa_stream
from paperhub.rag.retriever import Retriever

intent = decision.intent
if intent == "chitchat":
    async for tok in chitchat_stream(state, adapter=adapter, tracer=tracer,
                                     model=settings.chitchat_model,
                                     mock_response=os.environ.get("PAPERHUB_CHITCHAT_MOCK")):
        collected.append(tok)
        yield SseFormatter.token(run_id, tok)
    final_content = "".join(collected)

elif intent == "paper_search":
    # One-shot: agent runs its tool-calling loop internally; we emit a single final.
    final_content = await paper_search(
        state, adapter=adapter, tracer=tracer,
        model=settings.research_model,
        conn=conn, pipeline=request.app.state.pipeline,
    )

elif intent == "paper_qa":
    retriever = Retriever(chroma=request.app.state.chroma)
    async for tok in paper_qa_stream(
        state, adapter=adapter, tracer=tracer,
        model=settings.research_model,
        retriever=retriever, conn=conn,
    ):
        collected.append(tok)
        yield SseFormatter.token(run_id, tok)
    final_content = "".join(collected)

else:
    # slides + library_stats still stubbed in Plan C — Plans E + F replace.
    final_content = await stub_for_intent(intent)

# Persist + emit final + close run (unchanged from Plan A) ...
```

- [ ] **Step 3: Drop `paper_search` + `paper_qa` from `stubs.py`** — keep `slides` + `library_stats` stubs (Plans F + E replace those).

- [ ] **Step 4: Tests.** Add to `test_chat_sse.py`:
  - `test_chat_sse_paper_search_one_shot` — set `PAPERHUB_ROUTER_MOCK` so intent=paper_search, monkey-patch `paper_search` to return a canned string, assert SSE has `routing_decision` then `final` (no `token` events between).
  - `test_chat_sse_paper_qa_streams` — set router mock for `paper_qa`, seed a session with one paper_content + chunks, monkey-patch `Retriever.retrieve` to return canned chunks, monkey-patch `litellm.acompletion(stream=True)` to yield 2 tokens — assert SSE has `routing_decision` + 2 `token` events + `final`.
  - The existing chitchat test must continue to pass unchanged.

- [ ] **Step 5: Commit.**

```powershell
git add backend/src/paperhub/agents/graph.py `
        backend/src/paperhub/agents/stubs.py `
        backend/src/paperhub/app.py `
        backend/src/paperhub/api/chat.py `
        backend/tests/test_chat_sse.py
git commit -m "feat(chat): wire v2.3 research agent (paper_search one-shot + paper_qa stream)"
```

---

## Task 12 — Papers REST surface (ingest + curation + library)

**Files:**
- Create: `backend/src/paperhub/api/papers.py`
- Modify: `backend/src/paperhub/app.py` (register the papers router)
- Create: `backend/tests/test_papers_api.py`

Six endpoints land here. Per SRS v2.3, the Research Agent invokes ingest indirectly via `add_paper_to_session_dispatch` — the REST endpoints are for the deterministic UI: manually attaching a search result, browsing the library, toggling `enabled`, removing a paper, and serving the rendered HTML for the Citation Canvas.

| Method | Path | Purpose | Caller |
|---|---|---|---|
| POST | `/papers` | Ingest from `arxiv_id` (or upload — Plan F adds multipart). Cache-aware. | Plan D "Add as reference" button on a search result |
| GET | `/papers/library?session_id=&q=&limit=&offset=` | List indexed `paper_content` rows excluding those in `session_id`. Optional `q` filter on title/abstract. | Plan D Library Browser |
| POST | `/papers/from-library` | Attach an existing `paper_content_id` to a session. Idempotent via UNIQUE. | Plan D Library Browser |
| PATCH | `/papers/{papers_id}` | Toggle `enabled`. Body: `{"enabled": bool}`. | Plan D Reference Sources panel toggle |
| DELETE | `/papers/{papers_id}` | Remove from session (does NOT touch `paper_content`). | Plan D Reference Sources panel remove |
| GET | `/papers/content/{paper_content_id}/html` | Serve the pre-rendered HTML from `paper_content.html_path`. | Plan D Citation Canvas |

- [ ] **Step 1: Implement.**

```python
# backend/src/paperhub/api/papers.py
"""Papers REST surface (SRS v2.3, FR-08). Backs the deterministic UI
gestures; the Research Agent uses research_tools dispatchers instead."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from paperhub.db.connection import get_conn
from paperhub.pipelines.paper_pipeline import IngestRequest

router = APIRouter(prefix="/papers", tags=["papers"])


class IngestBody(BaseModel):
    session_id: int
    arxiv_id: str


class IngestResponse(BaseModel):
    paper_content_id: int
    papers_id: int
    cache_hit: bool
    title: str


class FromLibraryBody(BaseModel):
    session_id: int
    paper_content_id: int


class PatchBody(BaseModel):
    enabled: bool


class LibraryItem(BaseModel):
    paper_content_id: int
    arxiv_id: str | None
    title: str
    abstract: str | None
    year: int | None


@router.post("", response_model=IngestResponse)
async def ingest_paper(body: IngestBody, request: Request) -> IngestResponse:
    pipeline = request.app.state.pipeline
    result = await pipeline.ingest(IngestRequest(
        session_id=body.session_id, arxiv_id=body.arxiv_id,
    ))
    return IngestResponse(
        paper_content_id=result.paper_content_id,
        papers_id=result.papers_id,
        cache_hit=result.cache_hit,
        title=result.title,
    )


@router.get("/library", response_model=list[LibraryItem])
async def list_library(
    session_id: int = Query(...),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[LibraryItem]:
    """Indexed paper_content rows NOT already in `session_id`."""
    where = ["pc.id NOT IN (SELECT paper_content_id FROM papers WHERE session_id = ?)"]
    args: list[Any] = [session_id]
    if q:
        where.append("(pc.title LIKE ? OR pc.abstract LIKE ?)")
        like = f"%{q.replace('%', '\\%')}%"
        args.extend([like, like])
    sql = (
        "SELECT pc.id, pc.arxiv_id, pc.title, pc.abstract, pc.year "
        f"FROM paper_content pc WHERE {' AND '.join(where)} "
        "ORDER BY pc.year DESC NULLS LAST, pc.id DESC "
        "LIMIT ? OFFSET ?"
    )
    args.extend([limit, offset])
    async with get_conn() as conn:
        async with conn.execute(sql, args) as cur:
            rows = await cur.fetchall()
    return [LibraryItem(
        paper_content_id=int(r[0]), arxiv_id=r[1],
        title=r[2] or "", abstract=r[3], year=int(r[4]) if r[4] is not None else None,
    ) for r in rows]


@router.post("/from-library", response_model=IngestResponse)
async def attach_from_library(body: FromLibraryBody) -> IngestResponse:
    """Idempotent on UNIQUE(session_id, paper_content_id). Re-attach returns
    the existing `papers` row instead of erroring."""
    async with get_conn() as conn:
        # Confirm paper_content exists.
        async with conn.execute(
            "SELECT title FROM paper_content WHERE id = ?", (body.paper_content_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(404, f"paper_content {body.paper_content_id} not found")
        title = row[0] or ""
        await conn.execute(
            "INSERT OR IGNORE INTO papers (session_id, paper_content_id) VALUES (?, ?)",
            (body.session_id, body.paper_content_id),
        )
        await conn.commit()
        async with conn.execute(
            "SELECT id FROM papers WHERE session_id = ? AND paper_content_id = ?",
            (body.session_id, body.paper_content_id),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
    return IngestResponse(
        paper_content_id=body.paper_content_id, papers_id=int(row[0]),
        cache_hit=True, title=title,
    )


@router.patch("/{papers_id}", response_model=dict[str, bool])
async def toggle_enabled(papers_id: int, body: PatchBody) -> dict[str, bool]:
    async with get_conn() as conn:
        cur = await conn.execute(
            "UPDATE papers SET enabled = ? WHERE id = ?",
            (1 if body.enabled else 0, papers_id),
        )
        await conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, f"papers row {papers_id} not found")
    return {"enabled": body.enabled}


@router.delete("/{papers_id}", status_code=204)
async def remove_from_session(papers_id: int) -> None:
    """Removes the membership row only — `paper_content` (and its chunks +
    Chroma vectors + cached on-disk artefacts) are untouched, so re-attaching
    later is a cache hit."""
    async with get_conn() as conn:
        cur = await conn.execute("DELETE FROM papers WHERE id = ?", (papers_id,))
        await conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, f"papers row {papers_id} not found")


@router.get("/content/{paper_content_id}/html")
async def serve_html(paper_content_id: int) -> FileResponse:
    """Served as a file to keep the Citation Canvas in Plan D simple
    (just point an iframe / fetch at this URL)."""
    async with get_conn() as conn:
        async with conn.execute(
            "SELECT html_path FROM paper_content WHERE id = ?", (paper_content_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row or not row[0]:
        raise HTTPException(404, f"no html for paper_content {paper_content_id}")
    path = Path(row[0])
    if not path.is_file():
        raise HTTPException(410, f"html_path on disk missing: {path}")
    return FileResponse(path, media_type="text/html")
```

- [ ] **Step 2: Register router in `app.py`.**

```python
# backend/src/paperhub/app.py
from paperhub.api import papers as papers_api
app.include_router(papers_api.router)
```

- [ ] **Step 3: Tests.** `backend/tests/test_papers_api.py` covers:
  - **`test_post_papers_ingests_then_cache_hits`**: POST `/papers` with an arxiv_id (use a fake `app.state.pipeline` that records calls and returns `cache_hit=False` first time, `True` second). Two POSTs → second response has `cache_hit=True`.
  - **`test_get_library_excludes_session_rows`**: seed two `paper_content` rows, attach one to session 1, GET `/papers/library?session_id=1` → returns only the unattached row.
  - **`test_get_library_filters_by_q`**: seed rows with distinct titles, GET with `q=transformer` → only matching rows.
  - **`test_post_from_library_idempotent`**: POST `/papers/from-library` twice with same `(session_id, paper_content_id)` → both 200, same `papers_id`, only one row in DB.
  - **`test_patch_toggles_enabled`**: insert papers row with enabled=1, PATCH `{"enabled": false}` → DB row updated.
  - **`test_delete_removes_papers_row_only`**: insert papers row, DELETE → 204; assert `paper_content` row still exists, chunks untouched.
  - **`test_get_html_serves_file`**: write a tmp HTML file, set `paper_content.html_path`, GET → 200 with `text/html`.
  - **`test_get_html_404_when_missing`**: nonexistent paper_content_id → 404.

- [ ] **Step 4: Commit.**

```powershell
uv run pytest tests/test_papers_api.py -v
git add backend/src/paperhub/api/papers.py backend/src/paperhub/app.py backend/tests/test_papers_api.py
git commit -m "feat(api): papers REST (ingest + library + from-library + toggle + remove + html)"
```

---

## Task 13 — CLI smoke scripts (ingest + paper_qa + research_turn)

**Files:**
- Create: `backend/scripts/ingest_paper.ps1`
- Create: `backend/scripts/query_papers.ps1`
- Create: `backend/scripts/research_turn.ps1`

Each script follows the same harness pattern as `smoke_chat_real.ps1`: load `.env`, pre-flight port-free check, boot uvicorn on a dedicated port (8767 / 8768 / 8769), wait for `/health`, run assertions, kill the whole process tree on exit. Provided as full bodies — no placeholders.

`ingest_paper.ps1` (excerpt — full body matches `smoke_chat_real.ps1` structure):

```powershell
# After uvicorn is up on :8767 ...
$arxivId = if ($args.Count -gt 0) { $args[0] } else { "1706.03762" }  # default: Vaswani transformer

$first  = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8767/papers" `
    -ContentType "application/json" -Body (@{ session_id = 1; arxiv_id = $arxivId } | ConvertTo-Json -Compress)
if ($first.cache_hit) { throw "ASSERTION: first ingest must be cache_miss, got cache_hit=true" }
Write-Host "first ingest OK — paper_content_id=$($first.paper_content_id), title=$($first.title)"

$second = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8767/papers" `
    -ContentType "application/json" -Body (@{ session_id = 2; arxiv_id = $arxivId } | ConvertTo-Json -Compress)
if (-not $second.cache_hit) { throw "ASSERTION: second ingest must be cache_hit=true" }
if ($second.paper_content_id -ne $first.paper_content_id) { throw "ASSERTION: same paper_content_id expected" }
Write-Host "second ingest OK — cache_hit=true, ~$([int]($sw.Elapsed.TotalMilliseconds))ms total"
```

Acceptance — I-8 #2: second invocation reports `cache_hit: true` and the wall-clock for the second POST is under 500 ms.

`query_papers.ps1`: pre-seeds two papers (`1706.03762` + one other; both attached to session 3), POSTs a chat with a comparative question, captures SSE, asserts:
- `routing_decision` event present with `intent: "paper_qa"`
- `[chunk:\d+]` appears at least **twice** in `final.content`
- The cited `chunk_id`s, joined back to `chunks.paper_content_id`, span **≥ 2 distinct values** (I-8 #3 — the prior plan only checked for ONE chunk marker, which a single-paper answer satisfies trivially).

```powershell
# After SSE captured into $sseRaw and final content extracted into $final ...
$chunkIds = [regex]::Matches($final, '\[chunk:(\d+)\]') | ForEach-Object { [int]$_.Groups[1].Value }
if ($chunkIds.Count -lt 2) { throw "ASSERTION: expected >= 2 [chunk:N] markers, got $($chunkIds.Count)" }
$inList = ($chunkIds | ForEach-Object { $_ }) -join ","
$db = "$env:PAPERHUB_WORKSPACE/paperhub.db"
$paperIds = & sqlite3 $db "SELECT DISTINCT paper_content_id FROM chunks WHERE id IN ($inList);" | Sort-Object -Unique
if ($paperIds.Count -lt 2) { throw "ASSERTION I-8 #3: citations span < 2 distinct paper_content rows ($paperIds)" }
Write-Host "paper_qa OK — $($chunkIds.Count) chunk citations across $($paperIds.Count) papers"
```

`research_turn.ps1` is **new for v2.3** — exercises the Research Agent tool-calling loop end-to-end against the real LLM. Three sub-tests, each in its own session:

```powershell
# Sub-test 1 (I-8 #8): vague prompt → clarifying question, zero search_arxiv tool_calls
$resp = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8769/chat" `
    -ContentType "application/json" `
    -Body (@{ user_message = "find me good ML papers" } | ConvertTo-Json -Compress)
# Parse SSE → assert at least one tool_step row exists with tool="paper_search:plan"
# AND zero tool_step rows with tool starting "paper_search:search_arxiv".
# AND final.content contains "?" (clarifying question heuristic).

# Sub-test 2 (I-8 #9): clear prompt + library hit → library-first preference
# Pre-attach 1706.03762 to a different session so it's in the dedup'd library.
# POST /chat with "I want the original transformer paper"
# Assert: tool_step "paper_search:search_library" appears before any "paper_search:search_arxiv"
# AND "paper_search:add_paper_to_session" was called with paper_id starting "library:"
# AND final.content names the attached paper.

# Sub-test 3 (basic happy path): clear prompt, no library hit
# POST /chat with "find recent papers about retrieval augmented generation"
# Assert: at least one "paper_search:add_paper_to_session" tool_step,
# and the corresponding papers row exists in DB with enabled=1.
```

- [ ] **Step 1**: write all three scripts following the harness pattern.
- [ ] **Step 2**: run each; capture wall-clock and any failures.
- [ ] **Step 3**: commit.

```powershell
git add backend/scripts/ingest_paper.ps1 backend/scripts/query_papers.ps1 backend/scripts/research_turn.ps1
git commit -m "test(pipelines): CLI smokes — ingest cache-hit, paper_qa ≥2-paper citations, research-turn tool-loop"
```

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

- `uv run pytest -v` — all tests pass (~70 backend tests after v2.3: existing ~35 + ~5 each for pipelines/extract/chunker/embedder/renderer/chroma/retriever/reranker/paper_pipeline + 4 semantic_scholar + 4 research_tools + 5 research_paper_search + 3 research_paper_qa + 8 papers_api + 2 chat_sse for paper_search/paper_qa). Frontend `npm test` still passes the Plan B count unchanged.
- `uv run ruff check src tests` + `uv run mypy src` — clean.
- `cd backend; .\scripts\ingest_paper.ps1 1706.03762` — first run downloads + processes; second run reports `cache_hit: true` in < 500ms (I-8 #2).
- `cd backend; .\scripts\query_papers.ps1 "Compare the methods of these two papers"` — assistant streams an answer containing `[chunk:<id>]` markers; at least 2 markers, citing chunks from **≥ 2 distinct `paper_content.id`** (I-8 #3 — strict).
- `cd backend; .\scripts\research_turn.ps1` — all three sub-tests pass:
  - **I-8 #8 (new)**: a vague prompt produces a clarifying question and zero `search_arxiv` tool calls.
  - **I-8 #9 (new)**: a clear prompt with a matching library paper triggers `search_library` BEFORE any `search_arxiv`, and `add_paper_to_session` is invoked with `paper_id="library:<id>"`.
  - **Happy path**: a clear prompt with no library match triggers at least one `add_paper_to_session` with `paper_id="arxiv:<id>"`, and the resulting `papers` row exists with `enabled=1`.
- The router's stubs are gone for `paper_search` and `paper_qa` (slides + library_stats still stubbed; Plans F + E replace).
- New `.env.example` entry `PAPERHUB_SEMANTIC_SCHOLAR_API_KEY` documented as optional.

I-8 acceptance lit by Plan C:
- #1 router accuracy (unchanged from Plan A)
- #2 cache reuse — verifiable
- #3 multi-paper Q&A with citations — verifiable, **tightened** to require ≥ 2 distinct `paper_content.id` cited
- #4 trace replay (unchanged from Plan A — every loop iteration writes a `tool_calls` row)
- #8 clarify-vs-search behavior (new, smoke-verifiable)
- #9 library-first preference (new, smoke-verifiable)

Remaining I-8 criteria (#5 Compare, #6 no-silent-failure UI, #7 freshness) hand off to Plans G + D + E as before.

---

## Plan self-review

- **Spec coverage** — every §III-5 stage maps to a task; FR-03 / FR-07 v2.3 / FR-08 v2.3 / UC-1 v2.3 / UC-2 v2.3 / UC-3 all addressed; I-8 #2 + #3 + #8 + #9 acceptance criteria covered. The Research Agent's three-tool palette + library-first canonical flow + default-add + UI-side curation endpoints match SRS §III-3 row exactly.
- **Placeholder scan** — every step contains real code or commands. Test bodies for the 5 paper_search cases are full Python; the other test files have explicit test-case lists (titles + asserts described) for the executor to fill in following the shown templates.
- **Type consistency** — `Chunk`, `ChunkSearchResult`, `RetrievedChunk`, `ArxivResult`, `LibraryHit`, `ArxivHit`, `RelatedPaper`, `AddResult`, `IngestRequest`, `IngestResult` shapes are used consistently across files. Tool-schema JSON in `research_tools.TOOL_SCHEMAS` is the public contract that Plan E's MCP wrapper will preserve verbatim.
- **No scope creep** — no Reference Sources UI, no Citation Canvas component, no slide pipeline, no Compare view, no MCP server wrapping. All explicitly deferred.
- **MCP-forward design** — tool dispatchers are pure async functions with typed args + structured returns; the MCP wrapper in Plan E only adds the JSON-RPC transport, no business-logic refactor needed.
- **Demo headline** — the per-turn trace panel for `paper_search` shows `search_library → search_arxiv (×1-3) → add_paper_to_session (×1-2)` with the LLM's `reason` field on each step. This is the headline "how the Agent picks tools" demonstration the project brief asks for.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-18-paperhub-C-paper-pipeline-research-agent.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task with spec + code-quality reviews between tasks. Same pattern as Plans A and B.
2. **Inline Execution** — batch with checkpoints in this session.

Which approach?

---

## Plan C v2.4 Follow-up Tasks (post-merge revision)

> **Status:** Plan C as originally scoped shipped and merged. Real-API verification (4 review rounds) + manual browser testing surfaced four operational gaps **and two design re-prioritisations** — the second design call (browser-test follow-up) reverts the v2.3 "default-add top 1-2" behavior to **suggest-only**: the agent never downloads, the user is the sole ingestion trigger. These five tasks are the **v2.4 patch round** — they ship to the same branch / PR cadence Plan C used. SRS v2.4 entry captures the design intent; this section is the executor-facing decomposition.
>
> **Full feedback brief** for the dispatching code agent: [docs/superpowers/feedback/2026-05-18-plan-c-v2.4-feedback.md](../feedback/2026-05-18-plan-c-v2.4-feedback.md).
>
> **v2.4 design summary** (the agent — read-only loop): the agent has three read tools (`search_library`, `search_semantic_scholar`, `find_related_papers`) and **no write tool**. It shortlists 3-5 candidates per turn with one-line reasons. The chat endpoint emits a new `search_results` SSE event before `final` carrying the structured candidate list. The frontend renders these as a `SearchResultList` with per-row Add buttons. Clicking Add calls `POST /papers` with a prefix-discriminated `paper_id` (`arxiv:<id>` / `ss:<paperId>` / `library:<paper_content_id>`) — that's the only path that triggers ingestion. Reference Sources panel manages already-attached papers.

### Task v2.4-1 — Frontend session_id roundtrip (CRITICAL)

**Symptom:** demo broke during phase-B testing — user clicked Add on a candidate card → paper attached to backend session N → next `paper_qa` turn returns *"No references are enabled for this session"* because that turn opened backend session N+1. (In the v2.3 timeline the symptom showed via the agent's auto-add; in v2.4 the symptom shows via the user's manual Add click — same root cause.)

**Root cause:** [`frontend/src/hooks/useChatStream.ts:42`](../../../frontend/src/hooks/useChatStream.ts#L42) hardcodes `session_id: null` on every POST `/chat`. The backend `_ensure_session` creates a fresh `chat_sessions` row each time. Papers attached (by user click or library browser) belong to backend session N; subsequent turn queries backend session N+1.

**Also impacts** `POST /papers` and `POST /papers/from-library` from the frontend Add-button flows — they need to send the user's *current backend* `session_id`, which means the frontend must learn and persist it on the first SSE event.

**Files:**
- Modify: `backend/src/paperhub/models/events.py` — extend `RoutingDecisionEvent` (or add a new `SessionEvent`) to carry `session_id: int` so the frontend learns the backend session ID on the first turn.
- Modify: `backend/src/paperhub/api/chat.py` — emit the `session_id` on the first event of every run.
- Modify: `frontend/src/types/domain.ts` — `ChatSession` gains `backend_session_id: number | null`.
- Modify: `frontend/src/store/chat.ts` — add `patchSessionBackendId(sessionId, backendId)` action.
- Modify: `frontend/src/hooks/useChatStream.ts` — read `backend_session_id` from the active frontend session before posting; thread it through `session_id` in the POST body.

**Test:** new `tests/test_chat_sse.py::test_chat_emits_session_id_on_first_event` asserts the SSE stream emits `session_id` in the first event. Frontend `tests/hooks/useChatStream.test.ts` adds a multi-turn assertion: send turn 1 → store the `backend_session_id` → send turn 2 → MSW handler must receive the same `session_id` in the second POST body.

### Task v2.4-2 — Stream `tool_step` events during `paper_search` (CRITICAL)

**Symptom:** trace panel sits empty for 60-90s during a paper_search turn (during arXiv download + extract + chunk + embed), then all 6-10 rows appear at once at the end.

**Root cause:** [`backend/src/paperhub/api/chat.py:182-189`](../../../backend/src/paperhub/api/chat.py#L182-L189) awaits `paper_search()` (returns `str`) before draining `tool_calls`. The agent's whole tool-calling loop runs synchronously before chat.py gets a chance to emit any `tool_step` events.

**Files:**
- Modify: `backend/src/paperhub/agents/research.py` — convert `paper_search` from `async def -> str` to `async def -> AsyncIterator[ToolStepRecord | FinalOnlyMessage]`. After each `tracer.step()` context closes, the agent yields the just-written tool_calls row(s); after the loop concludes, yield a `FinalOnlyMessage(content=...)`.
- Modify: `backend/src/paperhub/api/chat.py` — switch the `paper_search` branch to iterate the async generator, forwarding `ToolStepRecord` as `tool_step` SSE events in real time (matches the `paper_qa_stream` pattern).
- Optional: a small helper `_emit_steps_since(tracer, after_step)` to dedupe the drain-tool_calls logic that lives in both branches now.

**Test:** `tests/test_chat_sse.py::test_paper_search_streams_tool_step_events_incrementally` — set `PAPERHUB_ROUTER_MOCK` to force paper_search, monkey-patch `litellm.acompletion` with a side_effect sequence that yields canned tool_calls, and time-track the SSE events: each `tool_step` must arrive *before* the next `paper_search:plan` step finishes (i.e., consume events as they arrive, not buffer to end).

### Task v2.4-3 — TraceInline expand-on-click with args/result/reason (CRITICAL)

**Symptom:** trace panel shows only `[main#5] research · paper_search:search_arxiv (-) 2577ms ok` per row — no `query`, no `reason`, no `result_summary`. The "tool selection logic" demo is reduced to opaque latency numbers.

**Root cause:** [`frontend/src/components/chat/TraceInline.tsx:35-37`](../../../frontend/src/components/chat/TraceInline.tsx#L35-L37) renders only the headline. The `ToolCallRecord` payload includes `args_redacted_json` and `result_summary_json` but the component discards them. SRS §III-2 FR-02 specifies *"Click a row to expand `args_redacted` + `result_summary`"* — never implemented.

**Files:**
- Modify: `frontend/src/components/chat/TraceInline.tsx` — each `<li>` becomes its own collapsible. On expand, render two `<dl>` blocks (Args / Result) with structured rendering for the known keys: `reason` rendered prominently in italics (it's the LLM's *why*); `query` / `paper_id` / `arxiv_id` / `mode` shown as labelled rows; `count` / `title` / `cache_hit` / `papers_id` shown in the result; everything else falls through to a pretty-printed JSON block. Keep the outer "Trace · N steps" toggle for the collapsed default.
- Add: `frontend/tests/components/TraceInline.test.tsx` — three new cases: expand reveals reason; expand reveals query+count for a search_library call; error status renders error field in red.

**Test:** `npm test -- TraceInline` — all existing assertions plus the three new ones.

### Task v2.4-4 — Reference Sources panel + Library Browser + **SearchResultList** (IMPORTANT)

**Symptom:** three missing UI surfaces:
1. User can't see what's attached to the current session, can't toggle, can't remove.
2. User can't browse + attach already-indexed papers from prior sessions.
3. **User can't add papers from a paper_search result** — the agent shortlists candidates (v2.4 suggest-only), but the frontend has nowhere to render them as actionable cards. Without SearchResultList, the agent's shortlist is just text in the assistant message and the user has no path to ingest anything.

**Scope:** originally a Plan D surface; SearchResultList in particular is now Plan-C-critical because v2.4's suggest-only flow makes the cards the only attach surface in a paper_search turn. Citation Canvas + Compare view stay deferred to Plan D / G.

**Files (three components, two API endpoints, one store slice):**

**Backend:**
- Modify: `backend/src/paperhub/api/papers.py` —
  - Add `GET /papers?session_id=N` (list this session's references joined to `paper_content`).
  - Extend `POST /papers` to accept `paper_id: str` (prefix-discriminated) in addition to the legacy `arxiv_id: str` (for backwards compat with existing scripts). Body now `{session_id: int, paper_id?: str, arxiv_id?: str}` — at least one of `paper_id` or `arxiv_id` required. If `paper_id` is given, dispatches via the new `add_paper_to_session_dispatch` paper_id router (see Task v2.4-5). If `arxiv_id` is given, internally constructs `paper_id=f"arxiv:{arxiv_id}"` so the dispatch path is unified.

**Frontend — three new components:**
- Create: `frontend/src/components/references/ReferenceSourcesDrawer.tsx` — **collapsible right-edge drawer** (NOT a fixed sidebar panel; user-requested in v2.4 design notes). Uses shadcn `Sheet` primitive. Trigger button stays fixed in the viewport with a count badge. Drawer body lists `GET /papers?session_id={current_backend_session_id}` results. Each row: title, year, arxiv_id linked, enable/disable Switch (→ PATCH), trash icon (→ DELETE). Auto-refreshes on `search_results` (when `auto_added` candidates land) and on user-initiated Add success. On `auto_added` arrival, flash the trigger button's count badge — do not auto-open the drawer (disrupts reading flow).
- Create: `frontend/src/components/references/LibraryBrowserModal.tsx` — opened by an "Add from library" button at the top of the drawer. Searches `GET /papers/library?session_id={current}&q=...` (300ms debounce); each row has an Attach button → `POST /papers/from-library`.
- Create: `frontend/src/components/chat/SearchResultList.tsx` — **renders inline below an assistant message** whose corresponding run produced a `search_results` event. Each candidate card shows title, authors (truncated), year, abstract (clamped to 3 lines), source-badge (`arXiv` / `Semantic Scholar` / `Already in library`), the agent's `reason` in muted italics, and a state-dependent action area (see Task v2.4-5 architecture diagram for the full matrix):
  - `auto_added=true` → "Added by agent ✓" badge, no Add button (paper is already in the session via finalize auto-attach).
  - `error="no_ingestible_source"` → "Source unavailable" badge, greyed action.
  - Otherwise → "Add as reference" button → `POST /papers` with `{session_id, paper_id}`. On 200, button transitions to "Added ✓"; drawer auto-refreshes; the `paper_id` enters a frontend `addedPaperIds` set so re-renders show the added state.

**Frontend — wiring:**
- Modify: `frontend/src/types/domain.ts` — add `SearchResultCandidate` shape (mirrors backend `SearchResultsEvent` payload). Add a new field on `ChatMessage`: `search_results?: SearchResultCandidate[]` populated from the `search_results` SSE event.
- Modify: `frontend/src/hooks/useChatStream.ts` — handle the new `search_results` event: parse + call store `setSearchResults(sessionId, runId, candidates)`.
- Modify: `frontend/src/store/chat.ts` — new state slices: (a) `referencesBySession: Record<number, ReferenceItem[]>` with `setReferences`, `patchReferenceEnabled`, `removeReferenceLocal`; (b) `setSearchResults(sessionId, runId, candidates)` that finds the assistant message with matching `run_id` and patches its `search_results` field. Also add a frontend-only `addedPaperIds: Set<string>` updated whenever a POST /papers returns 200, so SearchResultList can render the per-card "already added" state across re-renders.
- Modify: `frontend/src/lib/api.ts` — typed wrappers: `listSessionReferences(sessionId)`, `toggleReference(papersId, enabled)`, `removeReference(papersId)`, `listLibrary(sessionId, q, limit, offset)`, `attachFromLibrary(sessionId, paperContentId)`, `ingestPaper(sessionId, paperId)` (new — POST /papers with prefix-discriminated paper_id).
- Modify: `frontend/src/components/chat/MessageBubble.tsx` (or `ChatPage.tsx` if the layout is owned there) — render `<SearchResultList candidates={message.search_results} />` directly below the assistant message bubble whenever `message.search_results` is populated.
- Modify: `frontend/src/components/layout/Sidebar.tsx` — mount `<ReferenceSourcesPanel />` below the existing sessions section. Show only when the active frontend session has a `backend_session_id`.

**Tests:**
- Backend `tests/test_papers_api.py`:
  - `test_list_session_references_returns_joined_paper_content_fields`
  - `test_post_papers_accepts_paper_id_arxiv_prefix`
  - `test_post_papers_accepts_paper_id_ss_prefix_with_arxiv_externalId` (mocks SS metadata fetch)
  - `test_post_papers_accepts_paper_id_library_prefix_is_idempotent`
  - `test_post_papers_legacy_arxiv_id_field_still_works`
- Frontend `tests/components/SearchResultList.test.tsx`:
  - `test_renders_candidates_with_title_authors_year_abstract`
  - `test_add_button_calls_post_papers_with_prefix_discriminated_paper_id`
  - `test_add_button_disabled_for_library_candidate_already_in_session`
  - `test_no_ingestible_source_badge_disables_add_button`
  - `test_after_add_success_button_shows_added_state`
- Frontend `tests/components/ReferenceSourcesPanel.test.tsx` — load / toggle / remove (MSW catches PATCH / DELETE).
- Frontend `tests/components/LibraryBrowserModal.test.tsx` — search debounce + attach.

### Task v2.4-5 — Shortlist-with-finalize agent + SS-primary palette + PDF ingestion fallback (DESIGN CHANGE)

**Symptom drives three coupled shifts:**
1. **Browser test:** "the download source not use at all… the agent picks up a lot of references, but make just pick up some related" — research naturally surfaces many candidates; auto-downloading them all wastes resources for papers the user didn't choose. **Tames v2.3 "default-add top 1-2" into "shortlist 3-5 with optional `finalize: true` marker on 1-2 most-confident picks".**
2. **Three attach sources defined**: agent-finalize (auto-attach with server cap 2), user clicks Add on SearchResultList card, user attaches from Library Browser. Suggested-only candidates are never downloaded.
3. **Plan C v2.4 design:** Semantic Scholar broader coverage (~200M vs arXiv subset) + better metadata; arXiv reserved for raw-source downloading.

**Design:** see SRS v2.4 entry + FR-07 + FR-08 + §III-3 Research Agent row + UC-1 + UC-2 paragraph 3.

**Architectural shape (read this first):**

```
┌──────────────────────────────────────────────────────────────────────┐
│  paper_search turn                                                   │
│                                                                      │
│  Agent (read-only loop, no write tool):                              │
│    tool palette = {search_library, search_semantic_scholar,          │
│                    find_related_papers}                              │
│    Final assistant message ends with ```json:candidates``` block:    │
│      [{paper_id, reason, finalize?:bool}, ...]                       │
│    Yields ToolStepYield per step + SearchResultsYield(candidates)    │
│           + FinalOnlyMessage(prose_text)                             │
│                                                                      │
│  chat.py (paper_search branch):                                      │
│    forwards ToolStepYield → SSE tool_step                            │
│    on SearchResultsYield:                                            │
│       1. Cap finalize-flagged candidates to MAX=2 (truncate rest     │
│          to suggested-only).                                         │
│       2. For each finalize-flagged candidate, call                   │
│          add_paper_to_session_dispatch(paper_id) → records           │
│          papers_id on the candidate. On NoIngestibleSourceError,     │
│          set candidate.error="no_ingestible_source", auto_added=false│
│       3. Emit SSE search_results with the enriched candidate list    │
│          (finalize, auto_added, papers_id, error all populated).     │
│    forwards FinalOnlyMessage → SSE final                             │
│                                                                      │
│  Three attach paths (all → add_paper_to_session_dispatch):           │
│    A. Agent-finalized: chat.py auto-calls during paper_search turn   │
│    B. POST /papers {session_id, paper_id}: user clicks Add card      │
│    C. POST /papers/from-library: user attaches from Library Browser  │
│                                                                      │
│  Frontend (Task v2.4-4):                                             │
│    handles search_results event → stores candidates on the           │
│    assistant message → SearchResultList renders cards:               │
│      finalize-flagged + auto_added=true → "Added by agent ✓"         │
│      finalize=false → Add button (calls POST /papers)                │
│      error="no_ingestible_source" → greyed-out "Source unavailable"  │
└──────────────────────────────────────────────────────────────────────┘
```

**Suggested-only candidates are never downloaded** — they exist as metadata in the `search_results` SSE payload only. Cache semantics for the three attach paths are identical because they all go through the same dispatcher: same paper picked via finalize / Add card / Library Browser resolves to the same `paper_content` row.

**Files:**

**Semantic Scholar layer:**
- Modify: `backend/src/paperhub/pipelines/semantic_scholar.py`
  - Add `search_papers(query, max_results) -> list[SemanticScholarHit]` (free-text search via `/graph/v1/paper/search?fields=title,abstract,year,authors,externalIds,openAccessPdf`).
  - Add `fetch_paper_metadata(paper_id) -> SemanticScholarMetadata` (single-paper fetch, same field set).
  - Both helpers send `x-api-key` header from `PAPERHUB_SEMANTIC_SCHOLAR_API_KEY` env when present. Module-level `httpx.AsyncClient` singleton, 10s timeout, User-Agent matching `arxiv_client.py`. Raise typed exceptions on 429.

**Pipeline PDF ingestion:**
- Modify: `backend/src/paperhub/pipelines/paper_pipeline.py`
  - Add `_ingest_pdf_from_url(req, pdf_url, *, title, abstract, authors, year)` — download via httpx → compute `sha256:<hex>` from bytes → cache lookup (short-circuit if hit) → write `cache_root/<sha256>/source.pdf` → run `_render_pdf` → chunk → embed → persist `paper_content(kind='pdf_upload')` + chunks in single transaction → insert papers row → return `IngestResult`.
  - `ingest()` dispatch: dispatch on `req.paper_id` prefix (`arxiv:` / `ss:` / `library:`). Cache lookup at the top must handle both `arxiv:<id>` and `sha256:<hex>` content keys.

**Research tools — read-only palette:**
- Modify: `backend/src/paperhub/agents/research_tools.py`
  - **Remove `search_arxiv` from `TOOL_SCHEMAS`.** Keep dispatcher as internal Python helper (Plan F may reuse it via direct call). Ensure no LLM-callable schema references it.
  - **Remove `add_paper_to_session` from `TOOL_SCHEMAS`.** Keep dispatcher as `add_paper_to_session_dispatch` — but it is now invoked **only by the `POST /papers` endpoint**, never by the agent loop. Update its signature to take `paper_id` (prefix-discriminated). Existing `library:` / `arxiv:` branches stay; add the `ss:` branch (SS metadata fetch → branch on `externalIds.ArXiv` → recurse into `arxiv:<id>` path; else `openAccessPdf.url` → `_ingest_pdf_from_url`; else raise typed `NoIngestibleSourceError` → HTTP 422 in the API layer).
  - Add `search_semantic_scholar(query, max_results)` schema + dispatcher.
  - Update `find_related_papers(paper_id, mode, max_results)` dispatcher — `paper_id` accepts `arxiv:<id>` or `ss:<paperId>`. Internal logic unchanged.

**Agent loop (read-only + emit candidates):**
- Modify: `backend/src/paperhub/agents/research.py`
  - Drop `add_paper_to_session` from the prompt + tool dispatch.
  - After the LLM's final no-tool-calls response, **build the shortlist** from the most recent `search_library` / `search_semantic_scholar` / `find_related_papers` tool results that the agent surfaced in its message. Cleanest implementation: have the agent emit a structured JSON block in its final message (e.g., a fenced ```json:candidates``` block listing `[{paper_id, reason}, ...]` for the picks), then the agent function parses that out, looks up the full metadata from the prior tool-call results cache, and yields a `SearchResultsYield(candidates=...)` event. Strip the JSON block from the assistant message before yielding the `FinalOnlyMessage`.
  - Update prompt to require the agent to end with a `json:candidates` block (described below).
  - Rename `MAX_ARXIV_CALLS_PER_TURN` → `MAX_EXTERNAL_SEARCH_CALLS_PER_TURN`, apply to `search_semantic_scholar`. `find_related_papers` is uncapped.
  - Yield types updated: `paper_search` now `-> AsyncIterator[ToolStepYield | SearchResultsYield | FinalOnlyMessage]` (Task v2.4-2 introduced the first two).

**Prompt update:**
- Modify: `backend/src/paperhub/llm/prompts/paper_search_v1.yaml`
  - Drop all mentions of `search_arxiv` and `add_paper_to_session`.
  - Describe the read-only palette + the shortlist-via-json-block contract:

    ```
    After you've gathered enough information, compose your final assistant
    message in two parts:
      1. A short human-readable summary of the picks (3-5 papers max) with
         a one-line reason per pick.
      2. A fenced code block tagged ```json:candidates``` containing a JSON
         array of {paper_id, reason} objects, one per pick. Use the exact
         paper_id from the tool result (library:<id>, arxiv:<id>, or
         ss:<paperId>). Reason should be the same one-line text shown in
         the prose summary.

    The system parses the json:candidates block to render Add buttons; the
    prose summary is what the user reads. DO NOT include other tool calls
    in the final turn — pick your shortlist and stop.
    ```

**New SSE event:**
- Modify: `backend/src/paperhub/models/events.py`
  - Add `SearchResultsEvent(type="search_results", run_id, candidates: list[SearchCandidate])` where `SearchCandidate` has `paper_id: str`, `title: str`, `authors: list[str]`, `year: int | None`, `abstract: str | None`, `arxiv_id: str | None`, `has_open_pdf: bool`, `reason: str`, `already_in_session: bool`. Frontend uses `has_open_pdf` + `arxiv_id` to decide button-enabled state pre-emptively (no need to ping the backend).

**Chat endpoint:**
- Modify: `backend/src/paperhub/api/chat.py`
  - The `paper_search` branch (after Task v2.4-2's async-generator refactor) now also handles `SearchResultsYield` items, emitting them as `search_results` SSE events before the `final` event.

**API endpoint (extends Task v2.4-4's POST /papers change):**
- Modify: `backend/src/paperhub/api/papers.py`
  - The `POST /papers` body accepts `paper_id` (or legacy `arxiv_id`). For `paper_id="ss:<id>"`, the dispatcher's `NoIngestibleSourceError` translates to **HTTP 422** with body `{"detail": "no_ingestible_source", "title": "...", "paper_id": "..."}` so the frontend can grey out the button rather than crash.

**Env:**
- Modify: `backend/.env.example` — confirm `PAPERHUB_SEMANTIC_SCHOLAR_API_KEY` line is present.

**Tests:**
- `tests/test_semantic_scholar.py` — `test_search_papers_extracts_externalIds_and_pdf_url` (respx-mocked). `test_fetch_paper_metadata_handles_429`.
- `tests/test_research_tools.py`:
  - `test_search_arxiv_not_in_tool_schemas`
  - `test_add_paper_to_session_not_in_tool_schemas`
  - `test_search_semantic_scholar_in_tool_schemas`
  - `test_add_paper_to_session_dispatch_ss_prefers_arxiv` (still tested as a Python function — endpoint calls it)
  - `test_add_paper_to_session_dispatch_ss_falls_back_to_pdf`
  - `test_add_paper_to_session_dispatch_ss_raises_no_ingestible_source_when_neither_available`
- `tests/test_research_paper_search.py` — update the 5 existing test cases:
  - Drop the `add_paper_to_session` tool-call assertions.
  - Add `test_paper_search_emits_search_results_yield_with_top_3to5_candidates` — mock LLM to emit a final message with a `json:candidates` block; assert the function yields a `SearchResultsYield` with parsed candidates.
  - Add `test_paper_search_search_results_includes_metadata_for_rendering` — verify the yield carries title / abstract / has_open_pdf so the frontend doesn't need to re-fetch.
- `tests/test_chat_sse.py` — `test_paper_search_emits_search_results_event_before_final`.
- `tests/test_paper_pipeline.py` — `test_ingest_pdf_from_url_persists_pdf_upload_kind` with the existing PDF fixture.
- Update `scripts/research_turn.ps1` — sub-test 3 now asserts (a) `paper_search:search_semantic_scholar` tool_step appears, (b) a `search_results` SSE event arrives with ≥ 1 candidate, (c) **no `papers` row is created during the turn** (zero auto-add). Sub-test 4 (new): POST `/papers` with one of the surfaced `paper_id`s → assert the paper attaches and `paper_content.kind` matches the discovery source.

**Cross-check with Task v2.4-4:** the `SearchResultList` component consumes the `search_results` SSE event's payload directly; the structural contract is defined here in Task 5 and consumed there in Task 4. Same `SearchCandidate` shape on both sides — keep them in sync via `frontend/src/types/domain.ts`.

### Quality gates after the v2.4 round

From `backend/`:

```powershell
uv run pytest -v          # +5-8 new tests across 4 files
uv run ruff check src tests
uv run mypy src
.\scripts\smoke_chat_real.ps1     # regression
.\scripts\ingest_paper.ps1 1706.03762   # I-8 #2
.\scripts\query_papers.ps1        # I-8 #3
.\scripts\research_turn.ps1       # I-8 #8 + #9 + SS primary
```

From `frontend/`:

```powershell
npm test          # +3-5 new tests across ReferenceSourcesPanel, LibraryBrowserModal, TraceInline
npm run typecheck
npm run lint
npm run build
```

Browser verification (manual):
- Turn 1: paper_search → trace events arrive incrementally as the loop runs; expand a row → reason + query visible; final message names what was added.
- Turn 2 in same session: paper_qa → cites both newly-added paper AND any existing references; **no "No references enabled" regression**.
- Sidebar: Reference Sources panel populates; toggle a paper off → paper_qa no longer cites it.
- Library Browser: open, search, attach an existing paper → appears in Reference Sources without re-download.

---

## Plan C v2.5 Follow-up Tasks (MCP client layer + open-webSearch + paperhub-papers FastMCP)

> **Status:** v2.4 shipped. Manual paper-search testing on short / vague queries (e.g. "that diffusion paper everyone cites", "Mamba 的後續工作") surfaced **a UX failure inside the v2.4 read-only loop** — Semantic Scholar's lexical match consistently misses on indirect references; the agent burns its 3-call external-search budget on slight rephrasings and surrenders a thin shortlist. v2.5 fixes this by **shipping the MCP client layer** Plan C's original SRS intent expected and integrating `Aas-ee/open-webSearch` as a no-key multi-engine discovery step. **SRS v2.6 update**: the `paperhub-papers` FastMCP server originally deferred to Plan E is **pulled into v2.5 scope** — keeping a dual dispatch path (in-process `papers.*`, MCP `web.*`) re-introduces the exact "manual tool" violation the course-correction set out to eliminate. The same client layer is reused unchanged by Plan E (sqlite MCP) and Plan G (filesystem MCP).
>
> **SRS reference**: [v2.6 entry](../specs/2026-05-17-paperhub-srs.md) + [v2.5 entry](../specs/2026-05-17-paperhub-srs.md) + §III-6 (open-webSearch + paperhub-papers rows + §III-6.1 MCP client layer subsection) + FR-07 v2.5 paragraph + §III-3 Research Agent v2.5 paragraph.
>
> **Design summary**: every tool the Research Agent has — `papers.*` (the three local dispatchers, now exposed via FastMCP at `/mcp` on the same FastAPI app) + `web.*` (open-webSearch over HTTP at `http://localhost:3000/mcp`) — flows through **one** MCPClient dispatch path. **Discovery-only** tools (`web.search`, `web.fetch`) enter the palette only when the registry has a reachable `web` server; their results carry no `paper_id` and cannot enter `json:candidates`. The 3-call external-search cap broadens to cover `papers.search_semantic_scholar` + `web.search` + `web.fetch` combined. open-webSearch-down case: registry has no `web` server, web tools don't enter the palette, `paper_search/v1` loads instead of `paper_search/v2`, behaviour reverts to v2.4 exactly. open-webSearch is an **optional external dependency** (operator runs `npm install -g open-websearch && open-websearch serve`); `paperhub-papers` is **always available** (mounted in-process by the backend itself).
>
> **Task sequencing**: 1 → 2 → **3 (NEW — paperhub-papers FastMCP server)** → 4 → 5 → 6. Tasks 4–6 below were numbered v2.5-3 through v2.5-5 before the v2.6 update; they have been renumbered.

### Task v2.5-1 — MCP client core (`paperhub.mcp.{config,errors,client}`)

**Goal:** establish the per-server connector that talks to any MCP server reachable via streamable HTTP (or stdio later). Reused by Plan E + Plan G with zero code changes.

**Files:**
- New: `backend/src/paperhub/mcp/__init__.py` — module root, exports `MCPClient`, `MCPRegistry`, `MCPServerConfig`, `MCPUnavailableError`, `MCPToolError`.
- New: `backend/src/paperhub/mcp/config.py` — `MCPServerConfig` dataclass (`name`, `transport`, `url` or `command`+`args`, `expose: list[str]`, `aliases: dict[str, str]`, `timeout_seconds: float`). Loader `load_mcp_servers(path: Path) -> list[MCPServerConfig]` reads `mcp_servers.toml` and validates each block.
- New: `backend/src/paperhub/mcp/errors.py` — `MCPUnavailableError` (transport/connection failure), `MCPToolError` (upstream tool returned an error).
- New: `backend/src/paperhub/mcp/client.py` — `MCPClient` wrapping `mcp.client.streamable_http.streamablehttp_client` + `mcp.ClientSession`. Methods: `connect()`, `disconnect()`, `list_tools() -> list[ToolSchema]` (returns LiteLLM-shaped JSON-schema dicts, namespaced `<server_name>.<tool>`, allowlist + alias applied), `call_tool(name: str, args: dict) -> Any` (where `name` is the un-namespaced tool name). Idempotent connect; reconnect-with-backoff on a stale-session error (cap 4 attempts).
- New: `backend/pyproject.toml` — add `mcp>=1.0.0` to `[project] dependencies`.
- New: `backend/mcp_servers.toml.example` — checked-in template with the `web` server block commented in; real `mcp_servers.toml` is gitignored.
- Modify: `backend/.gitignore` — add `mcp_servers.toml`.

**Tests:** new `tests/mcp/__init__.py`, `tests/mcp/test_config.py` (valid TOML, missing fields, alias validation), `tests/mcp/test_client.py` (stub `streamablehttp_client` with `asynccontextmanager` → fake server returning canned `tools/list` + `tools/call`; assert connect → list → call round-trip; assert namespacing; assert timeout; assert `MCPUnavailableError` on transport failure).

**Acceptance:** `uv run pytest tests/mcp/` passes; `uv run mypy src/paperhub/mcp` strict-clean.

### Task v2.5-2 — Registry + FastAPI lifespan wiring (`paperhub.mcp.registry`) **[v2.6 UPDATE: lazy connect]**

**Goal:** stand up a process-wide MCP registry that loads `mcp_servers.toml` at FastAPI startup and exposes `aggregate_tool_schemas()` + `call(namespaced_name, args)` to consumers. Connection to each server is **lazy on first tool use** (not eager at startup) — required because the `papers` server (added in v2.5-3) points at the backend's own port over loopback, and uvicorn isn't accepting connections during lifespan startup. Lazy connect is simpler than special-casing loopback servers and applies the same rule uniformly to every configured server.

**Files:**
- New: `backend/src/paperhub/mcp/registry.py` — `MCPRegistry` class:
  - `startup(config_path: Path) -> None` — loads `mcp_servers.toml` via `load_mcp_servers(...)`, **constructs** `MCPClient` instances for each block (but does NOT call `connect()` yet), stashes them in `self._clients: dict[str, MCPClient]` keyed by server name. Missing `mcp_servers.toml` → log INFO and continue with an empty registry (fresh-clone-friendly).
  - `shutdown() -> None` — calls `await client.disconnect()` on every client that ever connected; idempotent disconnects.
  - `aggregate_tool_schemas() -> list[dict]` — **on first call**, this triggers connection to all configured servers (lazy connect; failures log WARN and skip), then returns the union of per-server `list_tools()` results. Subsequent calls return a cached list (invalidate-on-failure).
  - `call(namespaced_name: str, args: dict) -> Any` — splits `<server>.<tool>`, ensures the server's client is connected (lazy-connecting if not), dispatches. If the server is unreachable, raises `MCPUnavailableError`.
  - `has_tool(namespaced_name: str) -> bool` — peeks into the cached aggregated schema list (triggering lazy connect if not yet done).
- Modify: `backend/src/paperhub/api/main.py` (or wherever FastAPI's lifespan is defined — verify location) — attach the registry: `app.state.mcp_registry = MCPRegistry()` + `await app.state.mcp_registry.startup(mcp_servers_toml_path)` in lifespan startup, `await app.state.mcp_registry.shutdown()` in shutdown.

**Tests:** `tests/mcp/test_registry.py` — load a TOML fixture with two servers (one reachable, one unreachable, both stubbed); assert `startup()` does NOT connect either client; assert `aggregate_tool_schemas()` triggers connection to both, returns the reachable one's tools, and logs WARN for the unreachable one; `call("web.search", {...})` routes correctly; `call("unknown.tool", {...})` raises a clear error. Also: `startup(path_to_nonexistent_toml)` succeeds and yields an empty registry.

**Acceptance:** existing `uv run pytest -v` runs unchanged (registry initialised but no MCP servers exposed by default in test env); add `tests/api/test_lifespan_mcp.py` asserting `app.state.mcp_registry` exists after startup and does not block on unreachable loopback servers (test the boot path with `mcp_servers.toml` pointing at the backend itself — must not deadlock).

### Task v2.5-3 — `paperhub-papers` FastMCP server (mount on FastAPI at `/mcp`) **[v2.6 ADDITION]**

**Goal:** stand up an in-process FastMCP server that re-exposes the three existing Research Agent tool dispatchers (`search_library`, `search_semantic_scholar`, `find_related_papers`) over the MCP wire protocol. Mounted on the existing FastAPI app at `/mcp` (no extra process, no second port). External MCP clients (Claude Desktop, Cursor) and the backend's own Research Agent reach the same URL — uniform interface.

**Architectural notes:**
- **No subprocess.** FastMCP supports being mounted as an ASGI sub-app on FastAPI. The MCP HTTP transport listens on the backend's own port at `/mcp`.
- **Same code path.** The FastMCP tool handlers call the *exact same* dispatcher functions in `agents/research_tools.py` (`search_library_dispatch`, `search_semantic_scholar_dispatch`, `find_related_papers_dispatch`). Zero behaviour change at the SQL / HTTP / Chroma layer — only the surface in front of them changes.
- **Same tracer-step contract.** Tracer step name remains `paper_search:papers.<tool>` (the namespace prefix comes from the MCP server name `papers`). The dispatcher functions write `tool_calls` rows themselves via the existing `Tracer` instance the agent passes in — the FastMCP layer must thread the live `Tracer` + `aiosqlite.Connection` + `session_id` through tool-call invocations. Pattern: per-request FastMCP context populated from the parent FastAPI request scope so the tool handler can reach `request.app.state` + the active run's tracer.
- **`add_paper_to_session_dispatch` stays in-process.** It is not in the LLM palette (v2.4) and is called by FastAPI endpoints (`POST /papers`, `POST /papers/from-library`). Do NOT expose it via MCP in this task.

**Files:**
- New: `backend/src/paperhub/mcp/server.py` — `build_paperhub_papers_server() -> FastMCP` factory. Defines three tools matching the LiteLLM JSON-schemas already in `research_tools.TOOL_SCHEMAS`. Each tool handler resolves its per-call context (live `Tracer`, `aiosqlite.Connection`, `session_id`) from the FastMCP request scope (set via `paperhub_papers_request_context()` middleware below) and delegates to the existing `*_dispatch` function. Tool return shape matches today's structured-return dataclasses, JSON-serialised by FastMCP.
- New: `backend/src/paperhub/mcp/server_context.py` — `PaperhubPapersRequestContext` dataclass + `set_request_context(ctx) -> token` / `current_request_context() -> PaperhubPapersRequestContext` using `contextvars.ContextVar`. The HTTP request handling middleware sets the context per-MCP-request from the parent FastAPI request, tool handlers read it.
- Modify: [`backend/src/paperhub/api/main.py`](../../../backend/src/paperhub/api/main.py) (or wherever the FastAPI app is constructed — verify location) — instantiate the FastMCP server during app startup; mount it: `app.mount("/mcp", paperhub_papers.streamable_http_app())` (verify the exact mount API against the installed FastMCP version). Add a small middleware on the mounted sub-app that captures the parent request scope (session_id from header / cookie / query string — pick the same path used by `/chat` for consistency) and populates `PaperhubPapersRequestContext`.
- Modify: [`backend/mcp_servers.toml.example`](../../../backend/mcp_servers.toml.example) — add a *commented-in* `papers` server block at the top of the file showing the loopback URL pattern:
  ```toml
  [[server]]
  name = "papers"
  transport = "streamable_http"
  url = "http://localhost:${PAPERHUB_BACKEND_PORT:-8000}/mcp"
  expose = ["search_library", "search_semantic_scholar", "find_related_papers"]
  timeout_seconds = 8.0
  ```
  Keep the `web` block below it as a separate `[[server]]` entry. (Env-var-interpolation syntax depends on what `load_mcp_servers` supports — Task v2.5-1 may have implemented it; if not, hardcode a port + add an `env` flag in v2.5-6 cleanup.)
- New: `backend/pyproject.toml` — verify `mcp>=1.0.0` from v2.5-1 already bundles `FastMCP`; if it's a separate package (e.g. `fastmcp`), add it.

**Tests:** new `tests/mcp/test_server.py`:
- Build the FastMCP server via the factory; spin up a test FastAPI app with it mounted at `/mcp`; use `httpx.AsyncClient` against the in-process app to:
  - Call MCP `tools/list` → assert all three tools are advertised with correct names + JSON-schemas (matching the existing `TOOL_SCHEMAS`).
  - Call `tools/call` for `search_library` against a seeded in-memory SQLite → assert the same rows that `search_library_dispatch` would return.
  - Call `tools/call` for `search_semantic_scholar` with `respx` stubbing the Semantic Scholar HTTP API → assert the dispatcher's structured-return shape round-trips through FastMCP serialization unchanged.
  - Call `tools/call` for `find_related_papers` similarly.
- Assert that every tool call writes a `tool_calls` row through the threaded `Tracer` (use a real `Tracer` against a temp DB; query the table after each call).
- Assert that an MCP call with an out-of-context (no `session_id`) returns a clean error rather than crashing.

Additional regression coverage in `tests/test_research_tools.py` — the existing tests must still pass (the dispatcher functions are unchanged; we're only adding a new surface in front of them).

**Acceptance:**
- `uv run pytest tests/mcp/ -v` passes (Task v2.5-1's 24 tests + new v2.5-3 tests).
- `uv run pytest tests/test_research_tools.py -v` passes unchanged.
- `uv run ruff check src tests` clean.
- `uv run mypy src/paperhub/mcp src/paperhub/api` strict-clean.

**Out of scope for this task** (handled in v2.5-4 + v2.5-5):
- Switching the agent to call via `MCPClient` instead of in-process dispatch. This task only stands up the MCP surface; v2.5-4 migrates the agent.
- Adding `papers.*` to the `paper_search/v2` prompt's tool description (v2.5-5).
- Removing the now-redundant `TOOL_SCHEMAS` module-level constant. The agent still uses it directly in v2.5-3 — v2.5-4 is the cutover.

### Task v2.5-4 — Research Agent migration to uniform MCP dispatch **[v2.6 RESHAPE]**

**Goal:** make every Research Agent tool call flow through `MCPRegistry.call(...)` — eliminate the in-process `papers.*` dispatch branch entirely. After this task there is exactly one dispatch path, and the SRS-original "every tool via MCP, no manual tools" architecture holds end-to-end.

**Files:**
- Modify: [`backend/src/paperhub/agents/research_tools.py`](../../../backend/src/paperhub/agents/research_tools.py) — replace the module-level `TOOL_SCHEMAS` constant with a builder `build_tool_schemas(mcp_registry: MCPRegistry) -> list[dict]` that returns `mcp_registry.aggregate_tool_schemas()`. **The base three schemas are no longer hardcoded here** — they come from the `papers.*` MCP server registered in `mcp_servers.toml`. The `*_dispatch` Python functions stay in this module (the FastMCP server from v2.5-3 imports them); they are no longer called by the agent directly.
- Modify: [`backend/src/paperhub/agents/research.py`](../../../backend/src/paperhub/agents/research.py) — `_dispatch_paper_search_tool_call` reduces to a single branch: `result = await mcp_registry.call(tool_name, args)`. All tool names are now namespaced (`papers.search_library`, `web.search`, etc.). Tracer step name format: `paper_search:<namespaced_name>` (e.g. `paper_search:papers.search_library`, `paper_search:web.search`). Web hits remain not-indexed into `recent_results`; papers hits ARE indexed (they were before — that part keeps working). Errors caught and translated to `{"error": str(exc), "tool": name}`.
- Modify: `MAX_EXTERNAL_SEARCH_CALLS_PER_TURN = 3` — rename to `MAX_EXTERNAL_DISCOVERY_CALLS_PER_TURN`; cap counter increments on `papers.search_semantic_scholar` + `web.*` calls (NOT on `papers.search_library` or `papers.find_related_papers` — those remain cheap / uncapped, matching v2.4 semantics).
- Modify: [`backend/src/paperhub/api/chat.py`](../../../backend/src/paperhub/api/chat.py) and [`backend/src/paperhub/agents/research_graph.py`](../../../backend/src/paperhub/agents/research_graph.py) — thread `app.state.mcp_registry` through to the paper_search subgraph; the agent now requires the registry (no fallback to in-process dispatch).
- Modify: [`backend/mcp_servers.toml.example`](../../../backend/mcp_servers.toml.example) — uncomment the `papers` block from v2.5-3 (it must be active by default for the agent to work). Document at the top of the file: "the `papers` server is required; do not disable it."

**Tests:**
- Update `tests/test_research_tools.py` — existing tests assert the dispatcher functions' behaviour against SQLite + respx mocks. These remain valid (the dispatcher functions are unchanged). Add: assert `build_tool_schemas(registry)` returns the registry's schemas verbatim (no in-process fallback).
- New `tests/test_research_tools_mcp.py`:
  - Stub the registry with a fake `papers.search_library` + `web.search`; assert dispatch routes through the registry, tracer step is named `paper_search:papers.search_library` / `paper_search:web.search`.
  - Assert the renamed cap blocks a 4th external call regardless of mix (1 papers.search_semantic_scholar + 3 web → 4th rejected; 3 papers.search_semantic_scholar + 1 web → 4th rejected).
  - Assert `papers.search_library` / `papers.find_related_papers` are NOT capped (uncapped, matching v2.4 semantics).
- Update `tests/test_research_paper_search.py` — the 5 existing cases need the registry threaded in. Stub it to route `papers.*` calls back into the in-process dispatchers under the hood (cheapest path that keeps the test surface in shape).
- Update `tests/test_chat_sse.py` — same: stub `app.state.mcp_registry` in the test app builder.

**Acceptance:**
- `uv run pytest -v` clean across the board (no regression in any existing test).
- `uv run mypy src` strict-clean (the `TOOL_SCHEMAS = build_tool_schemas(None)` transitional shim from the original v2.5-3 plan is **not** present — there is no longer an in-process fallback).
- Manual smoke (operator): start backend, ensure `papers` MCP server is reachable at `/mcp`, run `scripts/research_turn.ps1` — every tool_step row in the trace now carries a namespaced tool name.

**Migration safety:** since the dispatcher functions themselves are unchanged, the only failure modes introduced by this cutover are (a) FastMCP serialization round-trip bugs (caught by v2.5-3's tests) and (b) the agent failing to find the `papers` server in the registry (manifests as a clean error at startup). Both are detectable before merge.

### Task v2.5-5 — `paper_search/v2` prompt + conditional dispatch

**Goal:** rewrite the paper_search prompt to (a) use the new namespaced tool names (`papers.search_library`, `papers.search_semantic_scholar`, `papers.find_related_papers`) — required by v2.5-4's MCP-only dispatch — and (b) teach the agent the discover-then-refine pattern when `web.*` is available. Load v2 only when the MCP registry exposes `web.search`; otherwise load v1 — but **v1 must also be updated** to use namespaced names since v2.5-4 cut the in-process path entirely.

**Files:**
- Modify: `backend/src/paperhub/llm/prompts/paper_search_v1.yaml` — rename tool references from `search_library` / `search_semantic_scholar` / `find_related_papers` to `papers.search_library` / `papers.search_semantic_scholar` / `papers.find_related_papers`. This is a mechanical rename matching v2.5-4's namespacing. No discovery / `web.*` tools mentioned (this is the daemon-down prompt).
- New: `backend/src/paperhub/llm/prompts/paper_search_v2.yaml` — copy the (updated) v1, add `web.search` + `web.fetch` to the tool catalogue with descriptions emphasising **discovery-only** and the "must round-trip through `papers.search_semantic_scholar` (or `papers.search_library`) for a citable hit" rule. Insert a worked-example trajectory in the canonical-flow section: vague user query → `web.search` → `papers.search_semantic_scholar` (refined) → `json:candidates`. Update the call-budget note: the combined cap covers `papers.search_semantic_scholar` + `web.search` + `web.fetch` (not `papers.search_library` or `papers.find_related_papers` — those stay uncapped).
- Modify: [`backend/src/paperhub/llm/prompts/registry.py`](../../../backend/src/paperhub/llm/prompts/registry.py) — add a helper `get_paper_search_slot(mcp_registry: MCPRegistry) -> str` that returns `"paper_search/v2"` if the registry has `web.search`, else `"paper_search/v1"`. (Registry is now always non-None per v2.5-4.)
- Modify: `backend/src/paperhub/agents/research.py` — `_build_paper_search_messages` calls the helper instead of hardcoding `"paper_search/v1"`.

**Tests:** new `tests/test_paper_search_prompt_selection.py` — assert v2 loads when the fake registry advertises `web.search`; assert v1 loads otherwise. New `tests/llm/test_prompts_paper_search_v2.py` — assert the v2 YAML parses, uses namespaced tool names, mentions both `web.search` and `web.fetch`, and includes the discovery-only rule. New `tests/llm/test_prompts_paper_search_v1.py` — assert v1 YAML parses and uses namespaced tool names (regression coverage for the v2.6 rename).

**Acceptance:** both v1 and v2 use namespaced tool names matching what `MCPRegistry.aggregate_tool_schemas()` actually advertises; v2 only loads when warranted. No regression in existing `test_research_paper_search.py` (the 5 cases now exercise the namespaced names end-to-end).

### Task v2.5-6 — Operator surface (smoke scripts, docs)

**Goal:** make this usable by an operator from a clean clone. Add smoke scripts for both MCP surfaces (`papers.*` always-on, `web.*` daemon-up), and document the optional external dependency.

**Files:**
- New: `backend/scripts/smoke_mcp_papers.ps1` — operator-facing smoke: hits `http://localhost:8000/mcp` directly via the Python `MCPClient`, calls `papers.search_library` against the live workspace DB. Verifies the in-process FastMCP server is mounted correctly. Always runnable (no external dep).
- New: `backend/scripts/smoke_mcp_web.ps1` — operator-facing smoke: curl `http://localhost:3000/health`, then run a single `web.search` via `MCPClient`, print the first 3 hits. Skipped in CI (no daemon). Documented in `CLAUDE.md` alongside the other smoke scripts.
- Modify: `CLAUDE.md` (project) — add `open-websearch` to the optional-external-dependency block next to `pandoc`. Document `open-websearch serve` as the prerequisite for v2 paper_search behaviour. Note that `paperhub-papers` MCP is mounted in-process and requires no external install. Update the `backend/scripts/` smoke-script list.
- Modify: `backend/scripts/smoke_chat_real.ps1` — when the operator has the daemon running (`scripts/smoke_mcp_web.ps1` succeeded), add an assertion that a vague-query turn routes through `paper_search:web.search` → `paper_search:papers.search_semantic_scholar` → `search_results` SSE event with ≥ 1 candidate. When the daemon is down, assert the turn still completes (papers.* only) with a clean trace.
- Modify: `README.md` (if any) — add a "Web search (optional)" subsection pointing at the open-websearch install + serve commands; mention `paperhub-papers` is bundled.

**Tests:** none new — this is documentation + operator-facing scripts.

**Acceptance:** an operator following only CLAUDE.md + README.md can clone the repo, start the backend, run `scripts/smoke_mcp_papers.ps1` and see it pass; install open-webSearch + run `open-websearch serve`, then run `scripts/smoke_mcp_web.ps1` and see it pass; restart the backend and observe v2 paper_search behaviour without reading any other docs.

### Quality gates after the v2.5 round

From `backend/`:

```powershell
uv run pytest -v          # +30-40 new tests across tests/mcp/{test_config,test_client,test_server,test_registry}.py, tests/test_research_tools_mcp.py, tests/test_paper_search_prompt_selection.py
uv run ruff check src tests
uv run mypy src           # strict-clean over the new paperhub.mcp package
.\scripts\smoke_mcp_papers.ps1    # NEW (v2.5-6) — papers.* in-process MCP path
.\scripts\smoke_mcp_web.ps1       # NEW (v2.5-6) — daemon-up path verification (skipped if no daemon)
.\scripts\smoke_chat_real.ps1     # regression; daemon-down path uses papers.* only
.\scripts\research_turn.ps1       # regression
```

Browser verification (manual, both daemons up):
- Turn 1 with a vague query ("that diffusion paper everyone cites") → trace shows `paper_search:web.search` → optional `paper_search:web.fetch` → `paper_search:papers.search_semantic_scholar` → `search_results` SSE event surfaces the actual paper. **Every tool_step row carries a namespaced tool name** (no bare `search_semantic_scholar` — confirms the v2.6 cutover).
- Turn 2 in same session: regular paper_qa works unchanged.

Browser verification (manual, open-webSearch daemon down):
- Same vague-query turn: trace shows only `paper_search:papers.search_library` + `paper_search:papers.search_semantic_scholar` calls (no `web.*`), agent surfaces a thinner shortlist or asks a clarifying question. **No errors, no broken UX** — v2.4 behaviour exactly, but every tool call still flows through MCP (papers.* is in-process and always available).

Browser verification (negative — `papers.*` server unreachable for any reason):
- The Research Agent fails cleanly at the first tool call with an `MCPUnavailableError` surfaced as a red trace row; FR-09's "no silent failure" property holds. (Should never happen in practice — the FastMCP sub-app is mounted on the same process, so failure here implies the backend itself is broken.)

---
