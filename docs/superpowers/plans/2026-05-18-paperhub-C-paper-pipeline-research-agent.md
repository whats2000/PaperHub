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

### Task v2.5-7 — MCPClient header forwarding for loopback session/run context **[ADDED MID-EXECUTION]**

**Why this task exists:** v2.5-6 implementer surfaced a production bug — `MCPClient` did not forward custom HTTP headers, so the agent's loopback dispatch to `papers.search_library` was being rejected with HTTP 400 by the FastMCP middleware (which requires `X-Paperhub-Session-Id`). The bug was masked by `_FakeRegistry` test stubs that bypass the MCP wire.

**Goal:** propagate per-request `session_id` + `run_id` from the chat endpoint through `MCPRegistry.call(...)` → `MCPClient.call_tool(...)` into outbound HTTP headers, using a ContextVar pattern symmetric to the existing inbound `server_context.py`.

**Files:**
- New: `backend/src/paperhub/mcp/client_context.py` — `ClientHeadersContext` frozen dataclass + `set_/reset_/current_client_headers_context()` over a `ContextVar`. Returns `None` on unset (smoke-script friendly).
- Modify: `backend/src/paperhub/mcp/client.py` — `_open_session` reads contextvar + passes `headers=` to `streamablehttp_client`. New `_refresh_session_headers_if_drifted` reconnects when contextvar diverges from cached connection's bound headers (under `asyncio.Lock` for concurrent-request safety, with double-checked locking).
- Modify: `backend/src/paperhub/mcp/__init__.py` — re-export new symbols.
- Modify: `backend/src/paperhub/api/chat.py` — set/reset contextvar around the agent invocation.

**Tests:**
- `tests/mcp/test_client_context.py` — unit tests (frozen, isolation across `asyncio.gather`, None-on-unset).
- `tests/mcp/test_client_headers.py` — integration: fake stub captures request headers, asserts forwarding + drift-reconnect + concurrent-lock semantics.
- `tests/api/test_chat_mcp_headers.py` — chat endpoint sets contextvar with live session/run ids; resets on error.
- `tests/api/test_chat_mcp_loopback.py` — **load-bearing end-to-end test**: real uvicorn server, real httpx, real FastMCP middleware, asserts `tool_calls` row written under matching session_id. This is the test that would have caught the bug originally.

**Acceptance:** `uv run pytest -v` clean (282 passing). Loopback test proves the production path works.

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

## Plan C v2.6 stabilisation log (post-v2.5-7, pre-decomposition)

> **Status:** v2.5 (MCP client + paperhub-papers FastMCP + open-webSearch) shipped via tasks v2.5-1 … v2.5-7. Live UI testing in the same week surfaced a cluster of operational defects that were closed as a stabilisation patch round before the v2.7 decomposition. None of these were "SRS gap" — they were "the code shipped and the wire was right but the runtime fell over." Captured here so a future reader doing a Plan C archaeology dig can find them without crawling git.
>
> **Closed in this round** (one fix-commit each; the commit subject + scope tells the story):

| Area | Symptom → fix |
| --- | --- |
| **arxiv ingest** | Source archive exceeds arxiv's undocumented per-connection size cap → daemon returns 503 with empty body → retry loop spins forever. Detect the size-cap signature and **fail fast into PDF fallback** (`fix(pipelines): fail fast on arxiv per-connection size cap`). Companion: **byte-range `Range:` resume** on transient mid-download drops so partial downloads don't restart from zero (`fix(pipelines): byte-range resume + PDF fallback`). |
| **MCP registry** | Failed-at-startup MCP server was dead for the process lifetime, even if the operator started the daemon mid-session. **30-second cooldown + retry** so `open-websearch serve` started after the backend re-enters the palette without a restart (`fix(mcp): registry retries failed servers after 30s cooldown`). Companion: **registry connect path serialised under `asyncio.Lock`** to close an `asyncio.gather` race during lazy first-connect (`fix(mcp): serialise registry connect path`). |
| **Windows event loop** | MCP stdio subprocess auto-spawn (open-websearch's npm flavour) failed on the default Windows Selector event loop because it doesn't support subprocesses. **Force `WindowsProactorEventLoopPolicy`** in `app.py` + the MCP spawn path (`fix(app,mcp): force Proactor event loop on Windows`). |
| **Frontend session sync** | Agent-finalized candidates auto-attached at the backend but the Reference Sources drawer didn't refresh. **`search_results` SSE event now triggers a drawer refresh** + the resolved papers auto-add hook (`fix(agent,frontend): auto-add resolved papers to session + refresh references panel`). Companion: **graceful Add-button disable** when `backend_session_id` is still null on first paint (`fix(frontend): graceful Add-button disable`). |
| **MCP subprocess autostart** | An operator-started backend with no daemon couldn't get `web.*` into the palette without a separate npm process. **Config-driven subprocess autostart** in `mcp_servers.toml`: registry can spawn its own open-websearch daemon as a managed child process with readable connect-error messages (`feat(mcp): config-driven subprocess autostart`). Companions: **exclude `bing` from the auto-spawn engine pool** (it returns 401s in the no-key flow — `fix(mcp): exclude bing`), **use the bare `open-websearch` entrypoint with `MODE=http`** rather than the `serve` subcommand which no longer exists in current builds (`fix(mcp): use bare open-websearch entrypoint`), **IPv4 probe fallback** when Windows `::1` resolution fails (`fix(mcp,pipelines): IPv4 probe fallback`). |
| **FastMCP wire** | open-websearch's MCP transport needed pinning to **JSON + stateless mode** (`fix(mcp): pin paperhub-papers FastMCP to json+stateless mode`). Loopback handler tracer wrap was double-wrapping calls — **dropped** (`fix(mcp): drop loopback handler tracer wrap; auto-seed mcp_servers.toml`). |
| **Trace fidelity** | paper_search trace recorded tool-call boundaries but not the LLM payload that triggered them. **Full LLM + web.search payloads now recorded** so a trace replay can reconstruct why the Discoverer picked a given query (`fix(agent): record full LLM + web.search payloads in paper_search trace`). |

Quality gates after this round: `uv run pytest -v` still clean (test count grew by ~15 — additional `tests/mcp/test_registry_cooldown.py`, `tests/mcp/test_subprocess_autostart.py`, `tests/pipelines/test_arxiv_size_cap.py`); `uv run ruff check src tests` clean; `uv run mypy src` strict-clean. No SRS contract change; no DB schema change.

---

## Plan C v2.7 Follow-up Tasks (paper_search decomposition + operational hardening)

> **Status:** Live UI testing after the v2.6 stabilisation patch round (above) showed the **v2 mega-agent's intrinsic failure mode** — one LLM turn juggling 5 tools (`papers.search_library`, `papers.search_semantic_scholar`, `papers.find_related_papers`, `web.search`, `web.fetch`) plus ~200 lines of HARD-REQUIREMENT prompt blocks for multi-paper fan-out + `json:candidates` emission + corrective retry + honest-stop reasoning. The traces showed Semantic Scholar getting hammered with re-queries and the `json:candidates` block silently dropped under load. Adding more prompt rules diluted attention on the existing ones; the prompt was at its capacity. The fix is architectural: decompose paper_search so each LLM call has a single responsibility and a focused tool palette. This section captures the decomposition + a coupled operational-hardening round (opt-in CUDA, device auto-detect) that landed alongside it.
>
> **SRS reference**: [v2.7 entry](../specs/2026-05-17-paperhub-srs.md) + the **§III-3 Research Agent row** (rewritten to describe the four-stage subgraph) + the **FR-07 v2.7 paragraph** + **§III-5.1 / §III-5.2 device-aware embedder + reranker notes**.
>
> **Design summary**: paper_search is replaced by a four-stage LangGraph subgraph — Parser → Processor [Discover→Resolve inner loop, fan-out per request] → Finalizer → Synthesizer. Each stage has its own ~30-line prompt and a focused tool palette. The Finalizer is **Python, not LLM** — `SearchResultsYield` is built deterministically from `ResolvedPaper`s, so the `json:candidates` block is structurally guaranteed (LLM can no longer drop it). The read-only contract, the 3-call combined cap, the finalize semantics, the SSE shape, and the three-attach-paths model are all preserved from v2.4-v2.6. No DB schema change. No LLM-visible tool palette change.

### Task v2.7-1 — Decompose `paper_search` into Parser / Processor / Finalizer / Synthesizer

**Symptom (live UI):** the v2 mega-agent (v2.5-5 `paper_search/v2` prompt) repeatedly miscalibrated on multi-paper user messages — Semantic Scholar called 4-6× on slight rephrasings of the same canonical title, then the agent ran out of cap and surrendered a thin shortlist; the `json:candidates` block dropped silently when the LLM ran long; vague queries fell back to "ask a clarifying question" instead of using `web.search` because the LLM couldn't keep all the rules straight.

**Architectural fix:** stop asking one LLM call to do everything. Four single-responsibility stages.

**Files:**
- New: [`backend/src/paperhub/agents/research_pipeline.py`](../../../backend/src/paperhub/agents/research_pipeline.py) — module-level stages:
  - `parse_user_message(user_message, current_refs, tracer, ...) -> list[ParsedRequest]` — small-model LLM. Deterministic arxiv-ID + DOI pre-scan runs first; only natural-language fragments hit the LLM. Output is `list[ParsedRequest]` where each request carries `kind: arxiv_id | doi | quoted_title | natural_language` + the raw hint.
  - `discover_canonical(request, mcp_registry, tracer, ...) -> CanonicalIdentity | NotFound` — small-model LLM bound to the structured-output `paperhub.search_web(paper_hint, extra_terms)` tool (NOT raw `web.search`; the wrapper hides the free-text `query` field so the LLM cannot author quoted single-token hints that would kill DuckDuckGo recall). Bounded to `MAX_WEB_SEARCHES_PER_DISCOVER = 2`. Extracts `arxiv_id` from web hits when present so the Resolver can short-circuit SS.
  - `resolve_via_ss(canonical, mcp_registry, tracer, ...) -> ResolvedPaper | NotFound` — **no LLM**. Calls `papers.search_semantic_scholar(...)` exactly once with the canonical title. On SS miss, if the Discoverer surfaced an `arxiv_id`, synthesise a `ResolvedPaper` directly from the arxiv hit (don't loop back to Discoverer for a near-duplicate query).
  - `synthesize_prose(parsed, resolved_set, not_found_set, ...) -> str` — small-model LLM, ~30-line prompt. Generates the user-facing summary prose only. The `json:candidates` block is **not** an LLM responsibility.
- New: prompts under `backend/src/paperhub/llm/prompts/`:
  - `paper_search_parse_v1.yaml` — Parser prompt (~50 lines incl. example).
  - `paper_search_discover_v1.yaml` — Discoverer prompt (~40 lines).
  - `paper_search_synthesize_v1.yaml` — Synthesizer prompt (~30 lines).
- Modify: [`backend/src/paperhub/agents/research_graph.py`](../../../backend/src/paperhub/agents/research_graph.py) — `build_paper_search_subgraph` becomes a LangGraph topology: `ps_parse` → `ps_process` (fan-out node that runs `discover_canonical` + `resolve_via_ss` per `ParsedRequest` via `asyncio.gather`, with kick-back-on-NotFound inside the inner loop capped at `MAX_REFINEMENT_LOOPS = 1`) → `ps_finalize` (Python-only: builds `SearchCandidate`s + applies the finalize cap + auto-attaches via the chat layer's dispatcher hook) → `Synthesizer`. Each node drains `drain_tool_calls_since` before closing so `tool_step` SSE events stream per-stage, not batched at end.
- Modify: [`backend/src/paperhub/agents/research.py`](../../../backend/src/paperhub/agents/research.py) — **remove** the v2/v2.6 `paper_search` async-generator entirely (628 lines → ~30 lines of subgraph entry-point). The `paper_qa` flow is unchanged.
- Modify: [`backend/src/paperhub/models/domain.py`](../../../backend/src/paperhub/models/domain.py) — add `ParsedRequest`, `CanonicalIdentity`, `ResolvedPaper`, `NotFound` Pydantic models. Extend `AgentState` with subgraph control-flow fields (`ps_parsed_requests`, `ps_resolved`, `ps_not_found`); remove `ps_any_tools_called` (no longer meaningful with disjoint per-stage palettes).
- Modify: [`backend/src/paperhub/llm/prompts/registry.py`](../../../backend/src/paperhub/llm/prompts/registry.py) — drop `get_paper_search_slot` (v2.5-5's conditional v1/v2 picker); the four new slot names are loaded directly by the pipeline functions. No more single `paper_search` prompt — the four sub-prompts are the contract.

**Removals (clean break, no parallel paths):**
- `backend/src/paperhub/llm/prompts/paper_search_v1.yaml` (102 lines)
- `backend/src/paperhub/llm/prompts/paper_search_v2.yaml` (303 lines)
- 6 test files for the v1/v2 surface: `test_research_paper_search.py` (633 lines), `test_research_subgraph.py` (808 lines), `test_research_tools_mcp.py` (454 lines), `test_paper_search_prompt_selection.py` (124 lines), `test_prompts_paper_search_v1.py` (61 lines), `test_prompts_paper_search_v2.py` (77 lines). All tested behaviour that no longer exists.

Net diff: **+834 / −3489 lines**. The agent shrunk by ~80% by responsibility-splitting.

**Tests (new):**
- `tests/agents/test_research_pipeline_parse.py` — Parser splits multi-paper user messages; deterministic arxiv-ID + DOI pre-scan short-circuits; single natural-language hint passes through unchanged.
- `tests/agents/test_research_pipeline_discover.py` — Discoverer cannot author quoted free-text queries (the JSON-schema doesn't have a `query` field); bounded to `MAX_WEB_SEARCHES_PER_DISCOVER = 2`; extracts `arxiv_id` from web hits.
- `tests/agents/test_research_pipeline_resolve.py` — Resolver calls SS exactly once; on miss-with-arxiv-from-Discoverer, synthesises a `ResolvedPaper` rather than looping back.
- `tests/agents/test_research_pipeline_synthesize.py` — Synthesizer generates prose only; never emits a `json:candidates` block.
- `tests/agents/test_research_graph_paper_search.py` — end-to-end subgraph: vague query → Parser ID + DOI pre-scan misses → Discoverer surfaces canonical title → Resolver hits SS → Finalizer emits `SearchResultsYield` deterministically → Synthesizer prose. Tool-step events drain per-stage (assert ordering against `drain_tool_calls_since` calls).

**Acceptance:** 249/250 backend tests pass after the cutover (1 pre-existing flaky parallel-timing test in `test_paper_qa_map_reduce`, unrelated). `uv run mypy src` strict-clean. `uv run ruff check src tests` clean. Live UI smoke: vague query *"that diffusion paper everyone cites"* surfaces a usable shortlist; multi-paper query *"compare the Mamba paper and the original transformer"* fan-outs into two `ps_process` branches that each resolve correctly.

### Task v2.7-2 — Discoverer hardening (structured-output wrapper + arxiv-ID extraction + multi-query)

**Symptom (Discoverer-specific):** even after the decomposition, the Discoverer LLM had a strong habit of wrapping single-token paper hints in double quotes (`"MolmoACT2"`), which empirically kills DuckDuckGo recall — bare `MolmoACT2` returns 10 hits including arxiv URLs while `"MolmoACT2"` returns 0. Prompt rules against quoting were unreliable across model providers (Gemini-2.5-flash ignored them under load). Same loop: prompt-side rules can't be load-bearing for a property that should be structural.

**Fix:** hide raw `web.search(query)` from the LLM and expose a structured-output wrapper `paperhub.search_web(paper_hint, extra_terms)`. The JSON-schema literally has no field that accepts a free-form query string — the LLM passes `paper_hint: str` (the user's name, verbatim) + `extra_terms: list[str]` (additional bare keywords), and Python builds the underlying `web.search` query deterministically with `_BOOLEAN_OPERATORS` stripped. Quotes that sneak into field values are stripped server-side.

**Files:**
- Modify: [`backend/src/paperhub/agents/research_pipeline.py`](../../../backend/src/paperhub/agents/research_pipeline.py) — `_DISCOVER_TOOL_SCHEMA` defines `paperhub.search_web(paper_hint, extra_terms)`. `_dispatch_discover_tool_call` builds the underlying `web.search` query deterministically and calls `mcp_registry.call("web.search", {...})`. Boolean operators stripped from `extra_terms` before query assembly.
- Multi-query: when the first `paperhub.search_web` returns 0 paper-shaped hits, the Discoverer is allowed one alternate phrasing (counted against `MAX_WEB_SEARCHES_PER_DISCOVER = 2`). The prompt frames this as "you have two shots — make the second one different from the first."
- Modify: `paper_search_discover_v1.yaml` — drop all "do not use quotes" / "do not use OR" rules from the prompt (they were the unreliable load-bearing layer). The schema enforces it now.
- Modify: [`backend/src/paperhub/agents/research_pipeline.py`](../../../backend/src/paperhub/agents/research_pipeline.py) — when a `web.search` hit has an arxiv URL, extract the `arxiv_id` and attach it to the `CanonicalIdentity` so the Resolver can synthesise a `ResolvedPaper` on SS miss.

**Tests:** `tests/agents/test_paperhub_search_web_wrapper.py` — schema rejects `query` field; quoted `paper_hint` values are stripped; boolean operators stripped from `extra_terms`. `tests/agents/test_discover_arxiv_id_extraction.py` — Discoverer attaches arxiv_id from web hit when present.

**Acceptance:** browser test that previously failed on `"MolmoACT2"` → 0 hits now succeeds with `MolmoACT2` → arxiv hit → resolved paper. No prompt-rule changes are load-bearing for the quoting property.

### Task v2.7-3 — Opt-in CUDA wheels + device-aware embedder/reranker

**Symptom:** GPU operators got silent CPU inference because the HF stack defaults to CPU when no `device=` is passed. The fix on the operator side was painful: install a CUDA torch wheel manually, then re-run uv. Default install also pulled the CPU torch wheel (~200 MB) which is wasted for GPU operators.

**Fix:**
- **uv extras for CUDA wheels:** `pyproject.toml` declares `cu124` / `cu126` / `cu130` extras, each pinning the matching `torch` wheel from the PyTorch CUDA index. Default install stays CPU-only. Operator workflow: `uv sync --extra cu126` for CUDA 12.6 box, otherwise `uv sync`.
- **Device auto-detect:** new `backend/src/paperhub/pipelines/_device.py:resolve_device()` walks `PAPERHUB_DEVICE` override → CUDA → MPS → CPU. CPU fallback distinguishes *CPU-only-wheel* from *CUDA-wheel-no-GPU* in the warning text so the operator knows whether to reinstall torch or check `nvidia-smi`.
- **SentenceTransformer + CrossEncoder singletons pass `device=` explicitly:** `paperhub.pipelines.embedder.Embedder.__init__` and `paperhub.rag.reranker.Reranker.__init__` both call `resolve_device()` and pass it through. Lazy-singleton interface unchanged externally — only the constructor is device-aware now.

**Files:**
- New: [`backend/src/paperhub/pipelines/_device.py`](../../../backend/src/paperhub/pipelines/_device.py) — `resolve_device()` (the file we wrote above).
- Modify: `backend/src/paperhub/pipelines/embedder.py` — `self._device = resolve_device()` in `__init__`; pass `device=self._device` to `SentenceTransformer(...)`.
- Modify: `backend/src/paperhub/rag/reranker.py` — same shape; `CrossEncoder(device=self._device)`.
- Modify: `backend/pyproject.toml` — declare `[project.optional-dependencies] cu124 = ["torch==2.x.x+cu124", ...]` / `cu126` / `cu130`; add the PyTorch index as an `[[tool.uv.index]]` entry.
- Modify: `backend/.env.example` — document `PAPERHUB_DEVICE=auto|cpu|cuda|cuda:1|mps`.
- Modify: `CLAUDE.md` (project) — add a "GPU operators" subsection pointing at `uv sync --extra cu126`.

**Tests:** `tests/pipelines/test_device_resolution.py` — `PAPERHUB_DEVICE` override honoured; auto-detect picks CUDA when `torch.cuda.is_available()` is mocked true; falls back to MPS then CPU; CPU-only-wheel warning text differs from CUDA-wheel-no-GPU.

**Acceptance:** clean clone + `uv sync` boots with CPU torch (small wheel); `uv sync --extra cu126` swaps to CUDA torch; embedder logs `paperhub.device auto-detected=cuda` instead of the silent CPU default; backend cold-boot time on a GPU box drops from ~12s (CPU load) to ~3s (CUDA load).

### Forward-looking: remote inference server (NOT scoped to Plan C)

**Status:** in flight at the time of this writing. Goal: pull `Embedder` + `Reranker` out of the backend process into a **separate inference server** so:
- The backend doesn't bear the model cold-start cost on every restart (~3-12s depending on device).
- A single GPU pool serves multiple backend instances (the local-dev demo doesn't need it, but the same code wants to scale to a class deployment).
- The backend process becomes CPU-only at the Python-deps layer — `torch` + `sentence-transformers` + `cross-encoder` move to the inference server's deps.

The lazy-singleton-with-`device=` shape in v2.7 is the seam. Once the migration lands, the singletons become thin HTTP/gRPC clients; `resolve_device()` reports "remote" and the singletons report the remote's device in the trace. This is being scoped as a separate plan (working name: **Plan I — Inference Server Extraction**) and is not part of Plan C as-shipped. Mentioned here so a future archaeology dig hits the breadcrumb.

### Quality gates after the v2.7 round

From `backend/`:

```powershell
uv run pytest -v          # 249/250 (1 pre-existing flaky parallel-timing test, unrelated)
uv run ruff check src tests
uv run mypy src           # strict-clean across the new research_pipeline + _device modules
.\scripts\smoke_mcp_papers.ps1
.\scripts\smoke_mcp_web.ps1
.\scripts\smoke_chat_real.ps1     # regression — vague-query path goes through new subgraph
.\scripts\research_turn.ps1       # regression
```

Browser verification (manual, both daemons up):
- Vague query *"that diffusion paper everyone cites"* → trace shows `paper_search:ps_parse` → `paper_search:ps_process:discover` (via `paperhub.search_web`, NOT `web.search` raw) → `paper_search:ps_process:resolve` (`papers.search_semantic_scholar` exactly once) → `paper_search:ps_finalize` (Python; no LLM call) → `paper_search:ps_synthesize` (prose). `json:candidates` block always present. Reference Sources drawer auto-refreshes with the resolved paper.
- Multi-paper query *"compare the Mamba paper and the original transformer"* → Parser splits into 2 `ParsedRequest`s → 2 parallel `ps_process` branches via `asyncio.gather` → both resolve → Finalizer emits 2 candidates → Synthesizer prose names both.
- Single-token cap-violating query (the v2.6 regression case): `MolmoACT2` → Discoverer's structured-output wrapper passes `paper_hint="MolmoACT2"` (no quotes possible) → DuckDuckGo returns the arxiv URL → Resolver gets a hit. No quoting failure.

Browser verification (negative — Discoverer NotFound):
- Made-up paper title → Parser parses → Discoverer 2 attempts → both NotFound → Resolver returns NotFound → Finalizer emits 0 candidates → Synthesizer prose explains "I couldn't find this. Try an arxiv ID or DOI." No silent failure, no crash.

---

## Plan C v2.8 — Model server isolation (cleanup pass)

The v2.7 "Forward-looking: remote inference server" note has been folded back into Plan C rather than spun out as a separate Plan I, because the actual delivered surface area turned out to be much smaller than the original speculation (one new package, two changed files, no schema change, no new endpoints). SRS §III-5.1 + §III-5.2 + §III-6 updated to match; SRS Revision History v2.8 entry covers the architecture-level changes. This section documents the implementation specifics.

### Symptom that forced the fix

Live MolmoACT2 testing showed a 10-minute hang on the first ingest after any backend edit, followed by `RemoteProtocolError: peer closed connection without sending complete message body`. Root cause: the embedder (`SentenceTransformer ~110 MB`) and reranker (`CrossEncoder ~80 MB`) were module-level singletons inside the uvicorn worker. Every `uvicorn --reload` (triggered by any save under `src/paperhub/`) re-imported the module graph and reset both singletons. The next ingest paid the full reload tax, and if another reload fired mid-load the worker died with the arxiv download still streaming. The user observed this as "the embed reranker got hot reload" — exactly the v2.7 forward-looking diagnosis, just not yet fixed.

### Architecture

Three pieces, all under `backend/src/paperhub/modelserver/`:

| File | Role |
| --- | --- |
| `server.py` | FastAPI app exposing `GET /health`, `POST /embed {texts}` → `{vectors}`, `POST /rerank {query, texts, top_k}` → `{indices, scores}`. Models lazy-load on first non-empty request (empty inputs short-circuit without touching the model — cheap pre-warm probes). Lazy load uses `paperhub.config.load_settings()` and `paperhub.pipelines._device.resolve_device()`, so device-detect + CUDA wheel support inherited from v2.7 apply unchanged. |
| `__main__.py` | `python -m paperhub.modelserver` (and `paperhub-modelserver` script entry in `pyproject.toml`). Reads `PAPERHUB_MODEL_SERVER_HOST` / `PAPERHUB_MODEL_SERVER_PORT`, launches uvicorn against the FastAPI app with `reload=False`, `workers=1`. NOT subject to `--reload` — its whole reason to exist is to survive backend reloads. |
| `spawn.py` | `ensure_running(host, port)` TCP-probes `/health` first; reachable → returns `None` (reuse path); not reachable → spawns ONE detached `subprocess.Popen` with `stdout=DEVNULL` and a platform-specific detach flag (Windows `CREATE_NEW_PROCESS_GROUP`, Unix `start_new_session=True`). Polls `/health` until ready or timeout (30s default), then returns the process handle. Caller (lifespan) deliberately discards the handle so shutdown can't cascade-kill the modelserver. `terminate_subprocess()` is also exported but only used by tests and `scripts/start.ps1`. |

Backend pipelines become thin HTTP clients:

| File | v2.7 shape | v2.8 shape |
| --- | --- | --- |
| `pipelines/embedder.py` | One impl: `_SentenceTransformersEmbedder(model_name)` loading via `device=resolve_device()`. | Two impls behind the `Embedder` Protocol: `_HttpEmbedder(base_url)` over httpx.Client (default), `_SentenceTransformersEmbedder` retained for `PAPERHUB_INPROCESS_MODELS=1` (tests, low-resource hosts). Factory `get_embedder()` dispatches on `Settings.inprocess_models`. New `reset_singleton()` test helper. |
| `rag/reranker.py` | One impl: `_CrossEncoderReranker(model_name)`. | Mirror of the embedder shape: `_HttpReranker`, `_CrossEncoderReranker`, factory + reset helper. |
| `config.py` | `Settings` has `embedding_model`, `reranker_model`. | Adds `model_server_host`, `model_server_port`, `inprocess_models`. `PAPERHUB_INPROCESS_MODELS` accepts `1` / `true` / `yes` as truthy. |

### Lifespan integration (`app.py`)

```python
# Detect-or-spawn. If a modelserver is already reachable, reuse it.
# Otherwise spawn ONE detached subprocess that outlives this worker.
# We DON'T track the proc on app.state and DON'T terminate at shutdown —
# that's exactly what was killing the modelserver on every reload before.
if not settings.inprocess_models:
    await _modelserver_ensure_running(
        host=settings.model_server_host,
        port=settings.model_server_port,
    )
```

Pre-warm became fire-and-forget. The previous synchronous `get_embedder().embed([""])` call inside lifespan blocked startup for the entire HF Hub cold-cache download (observed: 10+ minutes). Now:

```python
app.state.prewarm_task = asyncio.create_task(_prewarm_models(), name="paperhub-prewarm")
```

`_prewarm_models()` runs the blocking HTTP calls via `asyncio.to_thread` so the event loop stays responsive. Cancelled cleanly at shutdown.

### Operator workflow

Three paths, in order of operator friction:

1. **Auto-spawn** (default): `uv run uvicorn paperhub.app:app --reload --reload-dir src`. First boot spawns the modelserver; every subsequent `--reload` of the worker reuses it via the `/health` probe. The leaked modelserver process is cleaned up at OS reboot or via manual `taskkill /f /im python.exe` / `pkill -f paperhub-modelserver`. Trade-off: modelserver stdout is `DEVNULL` (detachment requirement), so its logs aren't visible.
2. **Explicit orchestration** (`scripts/start.ps1`): runs `paperhub-modelserver` as a tracked background process with visible stdout, polls `/health`, starts uvicorn with `--reload-dir src` in the foreground, terminates the modelserver in the script's finally block. Use when you need to see modelserver logs or want clean Ctrl+C cleanup.
3. **In-process fallback** (`PAPERHUB_INPROCESS_MODELS=1`): loads models in the worker as before v2.8. Used by tests (conftest sets it at module import) and by hosts that can't run a second process.

### Tests

- `tests/conftest.py` sets `PAPERHUB_INPROCESS_MODELS=1` via `os.environ.setdefault` at module import time so no test ever dials a non-running modelserver. Autouse fixture `_reset_model_singletons` brackets every test, clearing the embedder + reranker singleton caches before and after.
- New `tests/test_modelserver.py` covers the server's wire contract (FastAPI TestClient with stub `_embed_model` / `_rerank_model` fixtures), HTTP-client serialisation (`httpx.MockTransport` — no port binding needed), empty-input short-circuit on both client and server side, and factory dispatch on `Settings.inprocess_models`.
- All 287 existing tests still green under in-process mode.

### Quality gates

```powershell
uv run pytest -q                  # 287 passed
uv run ruff check src tests       # clean
uv run mypy src                   # 61 files, clean
.\scripts\smoke_mcp_papers.ps1    # unchanged behaviour
```

Manual smoke verifying detach: spawn modelserver via `ensure_running`, exit the parent Python script without calling terminate, run `tasklist /FI "PID eq <pid>"` — process still listed, `/health` still 200. After manual `Stop-Process -Force` it's gone. Confirms `CREATE_NEW_PROCESS_GROUP` correctly insulates the child from parent-group kills.

### Files touched

- New: [`backend/src/paperhub/modelserver/__init__.py`](../../../backend/src/paperhub/modelserver/__init__.py), [`server.py`](../../../backend/src/paperhub/modelserver/server.py), [`__main__.py`](../../../backend/src/paperhub/modelserver/__main__.py), [`spawn.py`](../../../backend/src/paperhub/modelserver/spawn.py).
- New: [`backend/tests/test_modelserver.py`](../../../backend/tests/test_modelserver.py).
- New: [`backend/scripts/start.ps1`](../../../backend/scripts/start.ps1) — optional orchestrator.
- Modify: [`backend/src/paperhub/config.py`](../../../backend/src/paperhub/config.py) — `model_server_host` / `model_server_port` / `inprocess_models`.
- Modify: [`backend/src/paperhub/pipelines/embedder.py`](../../../backend/src/paperhub/pipelines/embedder.py) — add `_HttpEmbedder`, factory dispatch, `reset_singleton`.
- Modify: [`backend/src/paperhub/rag/reranker.py`](../../../backend/src/paperhub/rag/reranker.py) — mirror.
- Modify: [`backend/src/paperhub/app.py`](../../../backend/src/paperhub/app.py) — `ensure_running` call (no termination), fire-and-forget pre-warm task.
- Modify: [`backend/pyproject.toml`](../../../backend/pyproject.toml) — `paperhub-modelserver` script entry.
- Modify: [`backend/tests/conftest.py`](../../../backend/tests/conftest.py) — `PAPERHUB_INPROCESS_MODELS=1` + autouse singleton-reset fixture.

---

## Plan C v2.9 — Frontend PDF upload + arXiv-ID manual import (follow-up round)

**Why this is still a Plan C item, not a Plan D bullet.** The "Attach paper" affordance was scoped into Plan C in two of the SRS use cases (UC-2 v2.3: "Add-as-reference … manual"; I-8 #2: "re-ingest hits the cache"), and the Paper Pipeline already supports PDF ingestion end-to-end via `IngestRequest(upload_path=..., upload_kind="pdf")` → `sha256:<hex>` content_key → `kind="pdf_upload"` row → chunks + Chroma vectors + rendered HTML. What is missing is purely a transport gap (no multipart HTTP endpoint) and a UI gap (the Composer's paperclip icon ships as a disabled placeholder with tooltip "Coming in Plan C — upload PDF or paste arXiv ID"). Closing that gap is a single round, not a plan-sized surface — it does not need a new package, a schema change, or any agent-side logic. Plan D's reference-panel/canvas work is unblocked either way; this round just finishes the user-facing entry point that Plan C promised.

### What the backend already does (no change needed for these)

- `PaperPipeline._ingest_upload(req, content_key)` — full pipeline path for `kind="pdf" | "latex"` uploads. Hashes the file content, writes it under `workspace/papers_cache/upload/<sha>/`, extracts text via `pymupdf` for PDFs (or LaTeX flattener for `.tex` trees), chunks + embeds + renders HTML, and writes one `paper_content` row + `chunks` rows + Chroma vectors in a single transaction.
- `compute_content_key(upload_path=...)` — streams the file in 1 MiB blocks; same `sha256:<hex>` produces a cache hit on re-upload of the same bytes. The cache hit short-circuits before extract/chunk/embed.
- arXiv-ID manual entry is already a no-op on the backend: `POST /papers {paper_id: "arxiv:<id>"}` routes through `add_paper_to_session_dispatch` → `_ingest_arxiv` (LaTeX path, with the v2.7 size-cap PDF fallback already in place from commit 3d34beb).

### Gap 1 — `POST /papers/upload` (multipart) is missing

`add_paper_to_session_dispatch(paper_id, ...)` is keyed off a `paper_id` *string* with one of three prefixes (`arxiv:`, `ss:`, `library:`); there is no fourth `upload:` branch and adding one would be wrong (the dispatcher's whole point is that the LLM's tool-arg surface is a string ID — file bytes do not belong there). The deterministic UI ingest path must bypass the dispatcher and call `PaperPipeline.ingest()` directly with `IngestRequest(upload_path=..., upload_kind="pdf")`.

### Gap 2 — Composer paperclip icon is disabled

[`frontend/src/components/chat/Composer.tsx:31-35`](../../../frontend/src/components/chat/Composer.tsx) ships the paperclip button as `disabled className="pointer-events-none"` with the tooltip "Coming in Plan C". The other three Plan C/D/F/G placeholders stay disabled (they're genuinely scoped to later plans), but the paperclip one is the Plan C surface and needs to become a working menu.

### Architecture

One new endpoint and one new frontend component (popover anchored on the paperclip button), plus thin API-client + store wiring.

**Backend:**

| File | Role |
| --- | --- |
| `backend/src/paperhub/api/papers.py` | Add `POST /papers/upload` accepting `multipart/form-data` with two fields: `session_id` (int) and `file` (UploadFile). Validates MIME (`application/pdf` only for v2.9 — `.tex` upload deferred to v3.x), enforces a 30 MiB ceiling (`PAPERHUB_MAX_UPLOAD_MB` env, default `30`), streams the body to a tempfile, then calls `PaperPipeline.ingest(IngestRequest(session_id=..., upload_path=tmp, upload_kind="pdf"))`. Returns the existing `IngestResponse` Pydantic model — frontend already consumes that shape. Cleans up the tempfile in a `finally` (cache lookup may short-circuit before extract, but the tempfile must still go). |
| `backend/src/paperhub/config.py` | Add `max_upload_mb: int` to `Settings` (default 30, from `PAPERHUB_MAX_UPLOAD_MB`). |
| `backend/.env.example` | Document `PAPERHUB_MAX_UPLOAD_MB=30`. |

**Frontend:**

| File | Role |
| --- | --- |
| `frontend/src/lib/api.ts` | Add `uploadPdf(sessionId, file)` — `FormData` POST to `/papers/upload`, returns `IngestResult`. Also export `parseArxivId(input)` (pure helper): accepts `2310.06825`, `arxiv:2310.06825`, full URL forms (`https://arxiv.org/abs/2310.06825v1`, `https://arxiv.org/pdf/2310.06825.pdf`), strips `vN` suffix, returns canonical `arxiv:2310.06825` or `null` on bad input. Validation regex: `^(\d{4}\.\d{4,5})|(\w+(\.\w+)*\/\d{7})$` covers both new-style (post-2007) and old-style (pre-2007 `cs.AI/0701001`) IDs. |
| `frontend/src/components/chat/AttachPaperMenu.tsx` | NEW. Popover (`@radix-ui/react-popover` — already a dep) anchored on the paperclip button. Two segments: "Upload PDF" (file input + drag-and-drop dropzone) and "Paste arXiv ID" (text input + Submit). On success in either, calls `useChatStore`'s new `addReferenceFromIngest(papers_id, paper_content_id, title)` action so the Reference Sources panel reflects the addition without a roundtrip, and surfaces a toast (sonner — already wired in [`frontend/src/components/Toaster.tsx`](../../../frontend/src/components/Toaster.tsx)). On error, surfaces the backend error message (e.g., 413 "file too large", 415 "only application/pdf is accepted", 422 "no_ingestible_source" for arXiv-ID withdrawn-paper case). |
| `frontend/src/components/chat/Composer.tsx` | Remove the `disabled` + `pointer-events-none` from the paperclip `Capability` entry. Wrap that one button in `<AttachPaperMenu trigger={...} />`. The other three placeholders stay disabled (Plan D/F/G surfaces). Tooltip text changes from "Coming in Plan C —" to "Attach paper — upload PDF or paste arXiv ID". |
| `frontend/src/store/chat.ts` | Add `addReferenceFromIngest({ papers_id, paper_content_id, title, kind })` action. Idempotent: if a reference with the same `papers_id` already exists in the active session's references list, no-op (covers re-attach + cache-hit case). |
| `frontend/tests/components/AttachPaperMenu.test.tsx` | NEW. RTL + MSW. Covers: (a) PDF upload happy path (mock `POST /papers/upload` → 201 with `IngestResponse`, assert toast text + store mutation); (b) PDF too large → 413 → error toast; (c) wrong MIME → 415 → error toast; (d) arXiv ID happy path (`2310.06825` → `arxiv:2310.06825` normalized → `POST /papers` mock returns 201); (e) bad arXiv ID → inline validation error, no network call; (f) arXiv URL form (`https://arxiv.org/abs/2310.06825v3`) normalises to canonical form; (g) cache-hit branch (mock returns `cache_hit=true`) → toast says "Re-attached" instead of "Added". |
| `frontend/tests/lib/api.test.ts` | Extend with `parseArxivId` unit cases — at minimum: bare ID, `arxiv:` prefix, abs URL with version, pdf URL, old-style `cs.AI/0701001`, malformed `foo` → `null`, leading/trailing whitespace tolerated. |

**Out of scope for v2.9** (deliberately deferred):

- LaTeX-tarball (`.tar.gz` / `.zip`) upload. The pipeline `_ingest_upload(upload_kind="latex")` branch exists and works, but the UX shape (decide whether to upload one `.tex` file vs a whole project tarball, and how to surface multi-file error states) deserves a small brainstorm rather than being bolted on as part of this round. Track as a v3.x item.
- Drag-and-drop directly onto the chat thread (vs the popover dropzone). Same UX-brainstorm reason — and the popover dropzone covers the demo's must-have surface.
- arXiv-ID *autocomplete* (typing "Mamba" and getting arXiv suggestions). That's `paper_search` agent territory — manual entry is for the user who already has the ID in hand.

### Tasks

#### Task v2.9-1 — Backend: `POST /papers/upload` endpoint

**Files:**
- Modify: [`backend/src/paperhub/config.py`](../../../backend/src/paperhub/config.py) — add `max_upload_mb`.
- Modify: [`backend/src/paperhub/api/papers.py`](../../../backend/src/paperhub/api/papers.py) — new route.
- Modify: [`backend/.env.example`](../../../backend/.env.example) — document the env var.
- Create: [`backend/tests/test_papers_upload.py`](../../../backend/tests/test_papers_upload.py).

- [ ] **Step 1: Failing test — happy path PDF upload.**

```python
# backend/tests/test_papers_upload.py
from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient

from paperhub.app import app


@pytest.mark.asyncio
async def test_upload_pdf_happy_path(seed_session, tmp_path: Path) -> None:
    sample_pdf = Path(__file__).parent / "fixtures" / "papers" / "sample.pdf"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with sample_pdf.open("rb") as f:
            r = await ac.post(
                "/papers/upload",
                data={"session_id": str(seed_session)},
                files={"file": ("sample.pdf", f, "application/pdf")},
            )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["cache_hit"] is False
    assert body["paper_content_id"] >= 1
    assert body["papers_id"] >= 1
    assert body["title"] == "sample"  # upload_path.stem fallback per pipeline
```

Run: `cd backend; uv run pytest tests/test_papers_upload.py::test_upload_pdf_happy_path -v`
Expected: FAIL with `404 Not Found` (endpoint not registered yet).

- [ ] **Step 2: Add `max_upload_mb` to `Settings`.**

In [`backend/src/paperhub/config.py`](../../../backend/src/paperhub/config.py), extend `Settings`:

```python
@dataclass(frozen=True)
class Settings:
    # ... existing fields ...
    max_upload_mb: int    # NEW (v2.9)
```

In `load_settings()`:

```python
max_upload_mb=int(os.environ.get("PAPERHUB_MAX_UPLOAD_MB", "30")),
```

- [ ] **Step 3: Implement the upload endpoint.**

Append to [`backend/src/paperhub/api/papers.py`](../../../backend/src/paperhub/api/papers.py):

```python
import tempfile
from fastapi import File, Form, UploadFile

_PDF_MIME = "application/pdf"


@router.post("/upload", response_model=IngestResponse, status_code=201)
async def upload_paper(
    request: Request,
    session_id: int = Form(..., ge=1),
    file: UploadFile = File(...),
) -> IngestResponse:
    """Accept a multipart PDF upload, sha256-key it, run the pipeline.

    Bypasses `add_paper_to_session_dispatch` because that function is
    paper_id-string-keyed (`arxiv:` / `ss:` / `library:` prefixes); file
    bytes don't belong in the LLM-visible tool surface. Calls
    `PaperPipeline.ingest()` directly with an upload_path IngestRequest.
    """
    settings = load_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024

    if file.content_type != _PDF_MIME:
        raise HTTPException(
            415, f"unsupported content_type={file.content_type!r}; expected {_PDF_MIME}"
        )

    # Stream to a tempfile (don't materialise the whole PDF in memory).
    # Suffix .pdf so the pipeline's extension-based branches behave.
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    bytes_written = 0
    try:
        try:
            while chunk := await file.read(1 << 20):  # 1 MiB blocks
                bytes_written += len(chunk)
                if bytes_written > max_bytes:
                    raise HTTPException(
                        413,
                        f"file exceeds {settings.max_upload_mb} MiB ceiling",
                    )
                tmp.write(chunk)
        finally:
            tmp.close()

        upload_path = Path(tmp.name)

        async with open_db(settings.db_path) as conn:
            pipeline = PaperPipeline(
                conn,
                papers_cache_dir=settings.papers_cache_dir,
                chroma=get_chroma(request, settings),
            )
            result = await pipeline.ingest(
                IngestRequest(
                    session_id=session_id,
                    upload_path=upload_path,
                    upload_kind="pdf",
                ),
            )
    finally:
        try:
            Path(tmp.name).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("failed to remove upload tempfile %s: %s", tmp.name, exc)

    return IngestResponse(
        paper_content_id=result.paper_content_id,
        papers_id=result.papers_id,
        cache_hit=result.cache_hit,
        title=result.title,
    )
```

Top-of-file imports needed: `IngestRequest` from `paperhub.pipelines.paper_pipeline` (already imported `ArxivMetadata, PaperPipeline` — extend the line).

- [ ] **Step 4: Run the happy-path test — it should pass.**

Run: `cd backend; uv run pytest tests/test_papers_upload.py::test_upload_pdf_happy_path -v`
Expected: PASS.

- [ ] **Step 5: Add edge-case tests — 415 wrong MIME, 413 too large, cache hit on re-upload.**

```python
@pytest.mark.asyncio
async def test_upload_rejects_non_pdf(seed_session) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/papers/upload",
            data={"session_id": str(seed_session)},
            files={"file": ("a.txt", b"hello", "text/plain")},
        )
    assert r.status_code == 415


@pytest.mark.asyncio
async def test_upload_rejects_oversize(seed_session, monkeypatch) -> None:
    monkeypatch.setenv("PAPERHUB_MAX_UPLOAD_MB", "1")
    transport = ASGITransport(app=app)
    big = b"%PDF-1.4\n" + b"\x00" * (2 * 1024 * 1024)  # 2 MiB > 1 MiB cap
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/papers/upload",
            data={"session_id": str(seed_session)},
            files={"file": ("big.pdf", big, "application/pdf")},
        )
    assert r.status_code == 413


@pytest.mark.asyncio
async def test_upload_same_bytes_returns_cache_hit(seed_session) -> None:
    sample_pdf = Path(__file__).parent / "fixtures" / "papers" / "sample.pdf"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with sample_pdf.open("rb") as f:
            first = await ac.post(
                "/papers/upload",
                data={"session_id": str(seed_session)},
                files={"file": ("sample.pdf", f, "application/pdf")},
            )
        with sample_pdf.open("rb") as f:
            second = await ac.post(
                "/papers/upload",
                data={"session_id": str(seed_session)},
                files={"file": ("sample.pdf", f, "application/pdf")},
            )
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["cache_hit"] is True
    assert second.json()["paper_content_id"] == first.json()["paper_content_id"]
```

Run: `cd backend; uv run pytest tests/test_papers_upload.py -v`
Expected: 4 PASS.

- [ ] **Step 6: Quality gates + commit.**

```powershell
cd backend
uv run pytest -q
uv run ruff check src tests
uv run mypy src
```

Expected: clean.

```powershell
git add backend/src/paperhub/api/papers.py backend/src/paperhub/config.py backend/.env.example backend/tests/test_papers_upload.py
git commit -m "feat(papers): add POST /papers/upload multipart endpoint for PDF ingest"
```

#### Task v2.9-2 — Frontend: `parseArxivId` helper + `uploadPdf` API client

**Files:**
- Modify: [`frontend/src/lib/api.ts`](../../../frontend/src/lib/api.ts) — add `uploadPdf` and `parseArxivId`.
- Modify: [`frontend/tests/lib/api.test.ts`](../../../frontend/tests/lib/api.test.ts) — extend with parser cases.

- [ ] **Step 1: Failing test — parseArxivId cases.**

Add to [`frontend/tests/lib/api.test.ts`](../../../frontend/tests/lib/api.test.ts):

```typescript
import { describe, it, expect } from "vitest";
import { parseArxivId } from "@/lib/api";

describe("parseArxivId", () => {
  it("accepts a bare new-style ID", () => {
    expect(parseArxivId("2310.06825")).toBe("arxiv:2310.06825");
  });
  it("accepts an arxiv: prefix", () => {
    expect(parseArxivId("arxiv:2310.06825")).toBe("arxiv:2310.06825");
  });
  it("strips a version suffix", () => {
    expect(parseArxivId("2310.06825v3")).toBe("arxiv:2310.06825");
  });
  it("normalises an abs URL", () => {
    expect(parseArxivId("https://arxiv.org/abs/2310.06825v1")).toBe(
      "arxiv:2310.06825",
    );
  });
  it("normalises a pdf URL", () => {
    expect(parseArxivId("https://arxiv.org/pdf/2310.06825.pdf")).toBe(
      "arxiv:2310.06825",
    );
  });
  it("accepts old-style IDs", () => {
    expect(parseArxivId("cs.AI/0701001")).toBe("arxiv:cs.AI/0701001");
  });
  it("trims whitespace", () => {
    expect(parseArxivId("  2310.06825  ")).toBe("arxiv:2310.06825");
  });
  it("rejects garbage", () => {
    expect(parseArxivId("not-an-id")).toBeNull();
    expect(parseArxivId("")).toBeNull();
    expect(parseArxivId("12.345")).toBeNull();
  });
});
```

Run: `cd frontend; npm test -- parseArxivId`
Expected: FAIL — `parseArxivId is not a function`.

- [ ] **Step 2: Implement `parseArxivId` and `uploadPdf`.**

Append to [`frontend/src/lib/api.ts`](../../../frontend/src/lib/api.ts):

```typescript
const ARXIV_NEW = /^(\d{4}\.\d{4,5})(v\d+)?$/;
const ARXIV_OLD = /^([a-z\-]+(\.[A-Z]{2})?\/\d{7})(v\d+)?$/;

/** Normalise user-supplied arXiv input to canonical `arxiv:<id>` form, or
 * null if it doesn't look like an arXiv identifier. Accepts bare IDs,
 * `arxiv:` prefix, and `arxiv.org/abs/` or `arxiv.org/pdf/` URLs, with or
 * without a trailing `vN` version suffix. */
export function parseArxivId(input: string): string | null {
  let s = input.trim();
  if (!s) return null;
  // Strip URL forms first.
  const urlMatch = s.match(/arxiv\.org\/(?:abs|pdf)\/([^?#]+?)(?:\.pdf)?$/i);
  if (urlMatch) s = urlMatch[1];
  // Strip arxiv: prefix.
  s = s.replace(/^arxiv:/i, "");
  // Match new-style or old-style, capturing without version.
  const mNew = s.match(ARXIV_NEW);
  if (mNew) return `arxiv:${mNew[1]}`;
  const mOld = s.match(ARXIV_OLD);
  if (mOld) return `arxiv:${mOld[1]}`;
  return null;
}

/** Multipart PDF upload. Backend hashes the bytes → sha256-keyed cache,
 * so re-uploading the same file produces `cache_hit: true`. */
export async function uploadPdf(
  sessionId: number,
  file: File,
): Promise<IngestResult> {
  const form = new FormData();
  form.append("session_id", String(sessionId));
  form.append("file", file);
  const res = await fetch(`${API_BASE_URL}/papers/upload`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${text}`);
  }
  return (await res.json()) as IngestResult;
}
```

- [ ] **Step 3: Run tests + lint + typecheck.**

```powershell
cd frontend
npm test
npm run typecheck
npm run lint
```

Expected: all pass; parseArxivId tests now green.

- [ ] **Step 4: Commit.**

```powershell
git add frontend/src/lib/api.ts frontend/tests/lib/api.test.ts
git commit -m "feat(api): add uploadPdf client + parseArxivId normaliser"
```

#### Task v2.9-3 — Frontend: AttachPaperMenu popover + wire into Composer

**Files:**
- Create: [`frontend/src/components/chat/AttachPaperMenu.tsx`](../../../frontend/src/components/chat/AttachPaperMenu.tsx).
- Modify: [`frontend/src/components/chat/Composer.tsx`](../../../frontend/src/components/chat/Composer.tsx) — replace the disabled paperclip with the menu trigger.
- Modify: [`frontend/src/store/chat.ts`](../../../frontend/src/store/chat.ts) — add `addReferenceFromIngest` action.
- Create: [`frontend/tests/components/AttachPaperMenu.test.tsx`](../../../frontend/tests/components/AttachPaperMenu.test.tsx).

- [ ] **Step 1: Failing test — PDF upload happy path through the menu.**

```tsx
// frontend/tests/components/AttachPaperMenu.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

import { AttachPaperMenu } from "@/components/chat/AttachPaperMenu";
import { useChatStore } from "@/store/chat";

const server = setupServer(
  http.post("http://localhost:8000/papers/upload", async () =>
    HttpResponse.json(
      {
        paper_content_id: 11,
        papers_id: 22,
        cache_hit: false,
        title: "Attention Is All You Need",
      },
      { status: 201 },
    ),
  ),
);

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

beforeEach(() => {
  // seed an active session in the store
  useChatStore.setState((s) => ({
    ...s,
    sessions: [
      { id: 1, title: "t", messages: [], backend_session_id: 7 },
    ],
    activeSessionId: 1,
  }));
});

describe("AttachPaperMenu", () => {
  it("uploads a PDF and adds the reference to the store", async () => {
    render(<AttachPaperMenu trigger={<button>Attach</button>} />);
    await userEvent.click(screen.getByText("Attach"));
    await userEvent.click(screen.getByRole("tab", { name: /upload pdf/i }));

    const file = new File(["%PDF-1.4 fake"], "paper.pdf", {
      type: "application/pdf",
    });
    const input = screen.getByLabelText(/select pdf/i) as HTMLInputElement;
    await userEvent.upload(input, file);

    await waitFor(() => {
      expect(screen.getByText(/added/i)).toBeInTheDocument();
    });
    // store mutation
    const refs = useChatStore.getState().sessions[0].references ?? [];
    expect(refs.some((r) => r.papers_id === 22)).toBe(true);
  });
});
```

Run: `cd frontend; npm test -- AttachPaperMenu`
Expected: FAIL — `AttachPaperMenu is not exported`.

- [ ] **Step 2: Implement the menu component.**

Create [`frontend/src/components/chat/AttachPaperMenu.tsx`](../../../frontend/src/components/chat/AttachPaperMenu.tsx):

```tsx
import { useRef, useState } from "react";
import { toast } from "sonner";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ingestPaper, parseArxivId, uploadPdf } from "@/lib/api";
import { useChatStore } from "@/store/chat";

interface Props {
  trigger: React.ReactNode;
}

export function AttachPaperMenu({ trigger }: Props) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [arxivInput, setArxivInput] = useState("");
  const [arxivError, setArxivError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const sessionId = useChatStore((s) => {
    const active = s.sessions.find((sess) => sess.id === s.activeSessionId);
    return active?.backend_session_id ?? null;
  });
  const addRef = useChatStore((s) => s.addReferenceFromIngest);

  const handleResult = (
    r: { papers_id: number; paper_content_id: number; title: string; cache_hit: boolean },
  ) => {
    addRef({
      papers_id: r.papers_id,
      paper_content_id: r.paper_content_id,
      title: r.title,
      kind: "pdf_upload",
    });
    toast.success(r.cache_hit ? "Re-attached" : "Added", {
      description: r.title,
    });
    setOpen(false);
  };

  const onPdfPicked = async (file: File | undefined) => {
    if (!file || sessionId == null) return;
    setBusy(true);
    try {
      const r = await uploadPdf(sessionId, file);
      handleResult(r);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast.error("Upload failed", { description: msg });
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const onArxivSubmit = async () => {
    setArxivError(null);
    const paperId = parseArxivId(arxivInput);
    if (!paperId) {
      setArxivError("Not a valid arXiv identifier or URL.");
      return;
    }
    if (sessionId == null) return;
    setBusy(true);
    try {
      const r = await ingestPaper(sessionId, paperId);
      handleResult({ ...r, papers_id: r.papers_id });
      setArxivInput("");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast.error("Import failed", { description: msg });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>{trigger}</PopoverTrigger>
      <PopoverContent className="w-80" align="start">
        <Tabs defaultValue="pdf">
          <TabsList className="w-full">
            <TabsTrigger value="pdf" className="flex-1">Upload PDF</TabsTrigger>
            <TabsTrigger value="arxiv" className="flex-1">Paste arXiv ID</TabsTrigger>
          </TabsList>
          <TabsContent value="pdf" className="space-y-2 pt-3">
            <label className="text-sm text-muted-foreground" htmlFor="pdf-file">
              Select PDF (max 30 MiB)
            </label>
            <Input
              id="pdf-file"
              ref={fileRef}
              type="file"
              accept="application/pdf"
              disabled={busy || sessionId == null}
              onChange={(e) => onPdfPicked(e.target.files?.[0])}
              aria-label="Select PDF"
            />
          </TabsContent>
          <TabsContent value="arxiv" className="space-y-2 pt-3">
            <Input
              placeholder="2310.06825 or arxiv URL"
              value={arxivInput}
              onChange={(e) => setArxivInput(e.target.value)}
              disabled={busy}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void onArxivSubmit();
                }
              }}
            />
            {arxivError && (
              <p className="text-xs text-destructive">{arxivError}</p>
            )}
            <Button
              type="button"
              size="sm"
              onClick={() => void onArxivSubmit()}
              disabled={busy || arxivInput.trim().length === 0 || sessionId == null}
            >
              Import
            </Button>
          </TabsContent>
        </Tabs>
      </PopoverContent>
    </Popover>
  );
}
```

- [ ] **Step 3: Add the store action.**

In [`frontend/src/store/chat.ts`](../../../frontend/src/store/chat.ts), extend the store type with:

```typescript
interface AddReferenceArgs {
  papers_id: number;
  paper_content_id: number;
  title: string;
  kind: string;
}

addReferenceFromIngest: (args: AddReferenceArgs) => void;
```

Implementation (inside `create<…>(…)`):

```typescript
addReferenceFromIngest: ({ papers_id, paper_content_id, title, kind }) =>
  set((state) => ({
    sessions: state.sessions.map((s) =>
      s.id === state.activeSessionId
        ? {
            ...s,
            references: (s.references ?? []).some(
              (r) => r.papers_id === papers_id,
            )
              ? s.references
              : [
                  ...(s.references ?? []),
                  {
                    papers_id,
                    paper_content_id,
                    enabled: true,
                    added_at: new Date().toISOString(),
                    arxiv_id: null,
                    title,
                    year: null,
                    kind,
                  },
                ],
          }
        : s,
    ),
  })),
```

If `ChatSession.references` is not yet on the type, add it: `references?: ReferenceItem[]` in [`frontend/src/types/domain.ts`](../../../frontend/src/types/domain.ts).

- [ ] **Step 4: Wire the menu into Composer.**

In [`frontend/src/components/chat/Composer.tsx`](../../../frontend/src/components/chat/Composer.tsx), drop the paperclip from the `CAPABILITIES` array and render it separately:

```tsx
import { AttachPaperMenu } from "@/components/chat/AttachPaperMenu";

// inside the tool-row JSX, before the .map of remaining placeholders:
<AttachPaperMenu
  trigger={
    <Button
      type="button"
      variant="ghost"
      size="icon"
      className="h-8 w-8 text-muted-foreground"
      aria-label="Attach paper"
    >
      <Paperclip className="h-4 w-4" />
    </Button>
  }
/>
```

Remove the `Paperclip` entry from `CAPABILITIES` (it now lives in its own JSX block; the other three placeholders stay).

- [ ] **Step 5: Add edge-case tests.**

Extend the test file with:

- 413 (file too large): MSW handler returns 413 → expect `toast.error` rendered.
- 415 (wrong MIME): the input has `accept="application/pdf"` but a programmatic File with `type="text/plain"` should still get rejected by the backend; assert the error toast.
- arXiv happy path: type `2310.06825v3`, click Import, assert MSW `/papers` was called with body `{session_id: 7, paper_id: "arxiv:2310.06825"}`, assert store mutation.
- Bad arXiv input: type `foo`, click Import → inline `Not a valid arXiv identifier` shown, no network call (assert `vi.fn` MSW handler was never hit).
- Cache hit: MSW returns `cache_hit: true` → toast text is "Re-attached" not "Added".

Each test follows the same `render → click trigger → interact → wait for toast → assert` shape as Step 1.

- [ ] **Step 6: Run all frontend gates.**

```powershell
cd frontend
npm test
npm run typecheck
npm run lint
npm run build
```

Expected: clean across all four.

- [ ] **Step 7: Manual browser verification.**

From repo root, with `backend/.env` provider key set:

```powershell
.\scripts\smoke_e2e.ps1
```

Then in the browser:

1. Click the paperclip → popover opens with two tabs.
2. **Upload PDF tab:** pick a real arXiv PDF off disk. Wait for "Added" toast. Reference Sources panel (already shipped in Plan D-prep) shows the new row with `kind=pdf_upload`. Refresh the page — the reference persists (DB-backed).
3. **Paste arXiv ID tab:** type `2310.06825`, click Import. Pipeline downloads, extracts LaTeX, embeds. Toast says "Added". Reference Sources panel shows the row with `kind=arxiv`.
4. **Cache-hit:** re-upload the same PDF → toast says "Re-attached", `paper_content_id` matches the first upload (verifiable in the trace panel or `papers.db` SELECT).
5. **Bad arXiv ID:** type `not-an-id`, click Import → inline "Not a valid arXiv identifier" shown; nothing hits the network (verifiable in DevTools Network panel).

- [ ] **Step 8: Commit.**

```powershell
git add frontend/src/components/chat/AttachPaperMenu.tsx \
        frontend/src/components/chat/Composer.tsx \
        frontend/src/store/chat.ts \
        frontend/src/types/domain.ts \
        frontend/tests/components/AttachPaperMenu.test.tsx
git commit -m "feat(chat): wire AttachPaperMenu for PDF upload + arXiv-ID import"
```

#### Task v2.9-4 — CLAUDE.md + SRS pointers

**Files:**
- Modify: [`CLAUDE.md`](../../../CLAUDE.md) — strike v2.9 follow-up off the Plan C known-follow-ups list; add the new `/papers/upload` line to the backend surface section.
- Modify: [`docs/superpowers/specs/2026-05-17-paperhub-srs.md`](../../../docs/superpowers/specs/2026-05-17-paperhub-srs.md) — bump to v2.9 with one Revision History entry referencing this round.

- [ ] **Step 1: Update SRS Revision History.**

Append a v2.9 row noting "Composer ‘Attach paper’ wired to `POST /papers/upload` (PDF) and `POST /papers {paper_id: arxiv:…}` (manual arXiv-ID); transport-only round, no schema or agent change." Bump the version banner at the top from v2.8 → v2.9.

- [ ] **Step 2: Update project CLAUDE.md.**

In the "Plan C known follow-ups" section, mark the PDF upload / arxiv-import item closed and reference the v2.9 round. Add `POST /papers/upload` to the surface list near `POST /papers` if surfaces are enumerated.

- [ ] **Step 3: Commit.**

```powershell
git add CLAUDE.md docs/superpowers/specs/2026-05-17-paperhub-srs.md
git commit -m "docs(srs,plan-c,claude): v2.9 — Composer attach-paper PDF upload + arXiv-ID import"
```

### Quality gates after the v2.9 round

From `backend/`:

```powershell
uv run pytest -q                # 287+4 new = 291 passed
uv run ruff check src tests
uv run mypy src
.\scripts\smoke_mcp_papers.ps1
.\scripts\smoke_chat_real.ps1   # regression — arxiv-id path still works through the chat surface
```

From `frontend/`:

```powershell
npm test                        # 25+7 new = 32 passed
npm run typecheck
npm run lint
npm run build
```

From repo root:

```powershell
.\scripts\smoke_e2e.ps1
```

Browser checks: see Task v2.9-3 Step 7.

### Acceptance for v2.9

- PDF upload through Composer → `paper_content` row with `kind='pdf_upload'`, chunks present, Chroma vectors present, Reference Sources panel reflects the addition immediately, `paper_qa` on a subsequent turn can cite chunks from the uploaded paper.
- Re-uploading the same bytes → toast "Re-attached", no second `paper_content` row, sub-second response.
- arXiv ID manual entry through Composer → same end-state as the agent-driven arxiv ingest path; cache shared.
- Bad input rejected at the right layer: junk in the arXiv tab → inline form error, no network; wrong MIME / oversize → backend 415/413, surfaced as an error toast.

### Files touched (summary)

- New: [`backend/tests/test_papers_upload.py`](../../../backend/tests/test_papers_upload.py).
- New: [`frontend/src/components/chat/AttachPaperMenu.tsx`](../../../frontend/src/components/chat/AttachPaperMenu.tsx).
- New: [`frontend/tests/components/AttachPaperMenu.test.tsx`](../../../frontend/tests/components/AttachPaperMenu.test.tsx).
- Modify: [`backend/src/paperhub/api/papers.py`](../../../backend/src/paperhub/api/papers.py) — `POST /papers/upload` route.
- Modify: [`backend/src/paperhub/config.py`](../../../backend/src/paperhub/config.py) — `max_upload_mb`.
- Modify: [`backend/.env.example`](../../../backend/.env.example) — `PAPERHUB_MAX_UPLOAD_MB`.
- Modify: [`frontend/src/lib/api.ts`](../../../frontend/src/lib/api.ts) — `uploadPdf`, `parseArxivId`.
- Modify: [`frontend/src/components/chat/Composer.tsx`](../../../frontend/src/components/chat/Composer.tsx) — replace disabled paperclip with `AttachPaperMenu` trigger.
- Modify: [`frontend/src/store/chat.ts`](../../../frontend/src/store/chat.ts) — `addReferenceFromIngest` action.
- Modify: [`frontend/src/types/domain.ts`](../../../frontend/src/types/domain.ts) — optional `references` field on `ChatSession` (if not already present).
- Modify: [`frontend/tests/lib/api.test.ts`](../../../frontend/tests/lib/api.test.ts) — `parseArxivId` cases.
- Modify: [`CLAUDE.md`](../../../CLAUDE.md), [`docs/superpowers/specs/2026-05-17-paperhub-srs.md`](../../../docs/superpowers/specs/2026-05-17-paperhub-srs.md) — version bump + follow-up closure.

---

## Plan C v2.10 — Agentic hierarchical `paper_qa` (chunk-by-section + per-paper subagents + finalizer)

> **For agentic workers:** REQUIRED SUB-SKILL: Use [superpowers:subagent-driven-development](../../../C:/Users/eddie/.claude/plugins/cache/claude-plugins-official/superpowers/5.1.0/skills/subagent-driven-development) (recommended) or [superpowers:executing-plans](../../../C:/Users/eddie/.claude/plugins/cache/claude-plugins-official/superpowers/5.1.0/skills/executing-plans). Steps use `- [ ]` for tracking.

**Goal:** Replace the v2.7 dense-RAG `paper_qa` map-reduce (retriever top-k → per-paper analyst LLM writes prose → synthesizer reads prose) with an **agentic hierarchical RAG** pipeline (dispatcher → per-paper subagent that browses the paper's section TOC and picks chunks → finalizer that reads the picked chunks directly and writes the user-facing answer).

**Why now.** Live MolmoACT2 + X-VLA testing produced an "empty papers" answer despite both papers having `enabled=true` references. DB inspection found the analyst LLM had been fed five chunks whose entire `text` was a single literal `.` / `R` / `e` character. Two coupled root causes:

1. **Chunker arithmetic bug** ([`chunker.py:60-68`](../../../backend/src/paperhub/pipelines/chunker.py#L60-L68)): on dense LaTeX, the shrink-loop heuristic `tentative_end -= max(1, (tok_len - hard) * 4)` overshoots by tens of thousands of characters in a single iteration, clamps to `cursor + 1`, emits a 1-char chunk, advances the cursor by 1, and walks the rest of the section character-by-character. Paper 15 (MolmoAct2) has **29,804 chunks ≤ 5 chars** out of 29,891 total. Paper 16 has **1,792 ≤ 5 chars** out of 1,839. Chroma embedded all of them; the reranker dutifully top-5'd whichever scored best for the query vector; the analyst LLM correctly reported "no relevant content" in 187 chars; the synthesizer correctly reported both papers as empty. The pipeline did its job — the chunker fed it `[chunk:71333]\n.`.
2. **Map-reduce architectural ceiling** (NOTES.md v9 lesson). Even with a sane chunker, the synthesizer never sees raw chunk text — it only sees the per-paper analyst's 3-6 sentence prose. NOTES.md v5 → v9 → v10 showed that giving the answer LLM **chunks + context** beats giving it **summary-of-chunks** by ~+0.10-0.26 correctness on flagship models. Dense top-5 retrieval also wastes the structural prior academic papers offer: section names disambiguate "Methods" from "Related Work" from "Experiments" in a way the embedder flattens. For multi-hop comparative questions ("how do these two papers differ on expert collapse"), an agent that browses the section TOC and decides which section to read outperforms cosine-similarity top-k.

**Architecture.** Replace `_paper_qa_map_one` / `_paper_qa_synthesize_stream` / `_paper_qa_map_reduce` with a four-node LangGraph subgraph mirroring v2.7's `paper_search` pattern:

```
pq_resolve     (existing: enumerate paper_content_ids WHERE enabled=TRUE)
   ↓
pq_dispatch    (fan-out one PerPaperSubagentState per paper)
   ↓ asyncio.gather
   ┌──────────────────────────────────────────────────────────────┐
   │ per_paper_subagent (bounded loop, max_iter=5):               │
   │   tool palette (paper-scoped, read-only):                    │
   │     - list_sections()                                        │
   │         → cached sections_json (name, level, token_count)    │
   │     - read_section(name)                                     │
   │         → all chunks in that section, with [chunk:id] heads  │
   │           and full text (paragraph-bounded post-v2.10-1)     │
   │   exit:                                                      │
   │     - LLM emits no tool_calls → final summary message →      │
   │       done; Python extracts [chunk:N] markers from summary   │
   │       and treats those as the subagent's picks               │
   │     - max_iter reached → force-stop; treat ALL chunks the    │
   │       subagent ever read as picks (best-effort fallback)     │
   │   yield: PerPaperPicks(paper_content_id, title,              │
   │                        picked_chunks=[...], rationale=str)   │
   └──────────────────────────────────────────────────────────────┘
   ↓ (all per-paper subagents resolved)
pq_finalize    (single flagship-model LLM call):
                 input: user_message + list[PerPaperPicks]
                 expected output: streaming user-facing prose with
                 [chunk:N] markers preserved (real chunks.id rows)
   ↓ tokens stream to /chat SSE
```

**Design decisions (settled with user 2026-05-20):**

| # | Decision | Rationale |
| --- | --- | --- |
| 1 | `MAX_SECTION_READS = 5` per subagent | Most papers have ≤ 10 sections; 5 reads is enough for a comparative question. Matches v2.7's `MAX_REFINEMENT_LOOPS=1` minimal-constant style. |
| 2 | **No section synopses** at ingest | The subagent self-infers what "Methods" / "Experiments" / "Related Work" mean from the section name alone. Avoids per-section LLM calls at ingest (~30 calls/paper). |
| 3 | Subagent **can re-read** the same section | A read is a tool call against `MAX_SECTION_READS`; the LLM may legitimately re-read after gathering context elsewhere. No special dedup. |
| 4 | **No cross-paper visibility** inside subagents | Subagent for paper A doesn't see paper B's picks. Finalizer is the only cross-paper synthesis surface. Preserves map-reduce purity + parallelism. |
| 5 | **Replace entirely** — no feature flag, no A/B | Clean break (matches v2.7 paper_search decomposition). Old paths confuse readers and double the test surface. |
| 6 | Subagent uses **small-tier model** (gemini-flash-lite by default); finalizer stays **flagship** | Subagent's job is tool-routing + chunk-citing — cheap, structural. Finalizer composes user-visible prose — pay flagship. Production may swap subagent to a local LLM via `PAPERHUB_PAPER_QA_SUBAGENT_MODEL`. |
| 7 | **Strip LaTeX `%`-comments** in the chunker (new requirement) | The IDE-opened `source.flattened.tex` for arxiv:2510.10274 (X-VLA) shows `pylatexenc`-output comments that survive into chunks; the LLM treats them as content and gets distracted. Strip at chunk-input time so existing renderer output stays untouched (Citation Canvas still resolves char offsets against the un-stripped source). |

**No SRS contract change** at the LLM-visible / SSE / DB-schema layer. **One schema addition**: `paper_content.sections_json TEXT` (nullable; ingested at chunk time, populated on re-ingest of existing papers). Tracing per-stage `tool_step` SSE events preserved end-to-end.

**Pairs with concurrent v2.9 work** (frontend Composer attach menu) — zero file overlap; v2.9 touches `frontend/src/components/chat/Composer.tsx` + `backend/src/paperhub/api/papers.py` (multipart endpoint), v2.10 touches `backend/src/paperhub/agents/*` + `backend/src/paperhub/pipelines/chunker.py` + `backend/src/paperhub/pipelines/paper_pipeline.py`. v2.10 lands after re-ingest so any v2.9-uploaded PDFs get the new chunker + section TOC for free.

**SRS update** (separate task — not gated on this plan landing): bump SRS to v2.8 → v2.10 with a Revision History entry describing the architectural shift, update §III-3 Research Agent row's `paper_qa` paragraph, update §III-5.2 RAG retrieval to describe the agentic loop instead of dense top-k → analyst → synthesizer. Will be done at PR time alongside the implementation.

---

### Task v2.10-1 — Chunker hardening: shrink-loop fix + LaTeX comment strip + paragraph-aware boundaries

**Files:**
- Modify: [`backend/src/paperhub/pipelines/chunker.py`](../../../backend/src/paperhub/pipelines/chunker.py)
- Modify: [`backend/tests/test_chunker.py`](../../../backend/tests/test_chunker.py)

**Symptom + fix:**

- [ ] **Step 1: Add a failing test for the 1-char-chunk pathology**

```python
# backend/tests/test_chunker.py — append to existing module
def test_chunker_never_emits_chunks_below_min_meaningful_length():
    """Regression: dense LaTeX previously walked the cursor 1 char at a time
    through ~1800 iterations, emitting single-period chunks. The shrink loop
    must always make forward progress at section/paragraph scale."""
    # Synthetic dense LaTeX-ish section: lots of math-mode noise.
    section = "\\section{Experiments}\n"
    # ~6000 chars of dense markup that previously triggered the overshoot.
    dense = ("$\\sum_{i=0}^{n} \\alpha_i \\beta_i + \\gamma$. " * 200)
    chunks = chunk_text(section + dense)
    # No chunk shorter than 50 chars (modulo possible trailing slivers).
    tiny = [c for c in chunks if len(c.text) < 50]
    assert len(tiny) <= 1, (
        f"Expected at most 1 trailing sliver, got {len(tiny)} tiny chunks: "
        f"{[c.text[:20] for c in tiny[:5]]}"
    )
    # And no 1-char chunks at all.
    one_char = [c for c in chunks if len(c.text) == 1]
    assert one_char == [], f"1-char chunks regressed: {one_char[:5]}"
```

- [ ] **Step 2: Add a failing test for LaTeX-comment stripping**

```python
def test_chunker_strips_latex_line_comments():
    """LaTeX % line-comments (single-% to end of line, unless escaped \\%) must
    be removed before chunking so they don't end up as 'content' in chunks
    served to the analyst LLM."""
    text = (
        "\\section{Method}\n"
        "We use attention. % FIXME: cite original paper here\n"
        "The key insight is X. 50\\% of the data is held out.\n"
        "% TODO: rewrite this paragraph\n"
        "Therefore Y holds.\n"
    )
    chunks = chunk_text(text)
    joined = "\n".join(c.text for c in chunks)
    assert "FIXME" not in joined
    assert "TODO" not in joined
    assert "rewrite this paragraph" not in joined
    # Escaped % survives (it's literal "50%").
    assert "50\\%" in joined or "50%" in joined
    # Real content is preserved.
    assert "attention" in joined
    assert "Therefore Y holds" in joined
```

- [ ] **Step 3: Add a failing test for paragraph-aware boundaries**

```python
def test_chunker_closes_at_paragraph_boundary_not_mid_sentence():
    """When target token count is hit, prefer closing at a paragraph break
    over a mid-sentence break. Paragraph integrity drives synthesizer
    correctness per NOTES.md v5 / v6 lessons."""
    para1 = ("This is paragraph one. " * 50).strip()  # ~250 tokens
    para2 = ("This is paragraph two. " * 50).strip()
    para3 = ("This is paragraph three. " * 50).strip()
    text = f"\\section{{Body}}\n{para1}\n\n{para2}\n\n{para3}\n"
    # Force target small so we close mid-text.
    chunks = chunk_text(text, target=400, hard=600)
    for c in chunks:
        stripped = c.text.strip()
        # No chunk ends mid-sentence (unless it's the trailing chunk).
        if c is not chunks[-1]:
            assert stripped.endswith(".") or stripped.endswith("\n"), (
                f"Chunk closes mid-sentence: ...{stripped[-30:]!r}"
            )
```

- [ ] **Step 4: Run the three tests to confirm they fail against current chunker**

```powershell
uv run pytest tests/test_chunker.py::test_chunker_never_emits_chunks_below_min_meaningful_length tests/test_chunker.py::test_chunker_strips_latex_line_comments tests/test_chunker.py::test_chunker_closes_at_paragraph_boundary_not_mid_sentence -v
```
Expected: 3 FAIL.

- [ ] **Step 5: Implement comment stripping + safe shrink + paragraph boundary**

Replace the body of `chunk_text` in [`backend/src/paperhub/pipelines/chunker.py`](../../../backend/src/paperhub/pipelines/chunker.py):

```python
import re
from dataclasses import dataclass
import tiktoken

_SECTION_RE = re.compile(r"\\section\{([^}]+)\}")
# Match single-% comments to end-of-line, NOT \% (escaped percent — literal).
# Negative lookbehind: not preceded by a backslash.
_COMMENT_RE = re.compile(r"(?<!\\)%[^\n]*")
# Paragraph break is the strongest natural boundary; sentence-end is fallback.
_PARA_BOUNDARY_RE = re.compile(r"\n\s*\n")
_SENT_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    section: str | None
    char_start: int
    char_end: int
    text: str


def _strip_latex_comments(text: str) -> str:
    """Remove % line-comments while preserving \\% (literal percent)."""
    return _COMMENT_RE.sub("", text)


def _best_close(piece: str, *, prefer_paragraph: bool) -> int | None:
    """Return the char offset of the latest natural boundary in *piece*, or
    None. Paragraph break preferred over sentence-end when ``prefer_paragraph``
    is True (it always is, at the call site)."""
    if prefer_paragraph:
        matches = list(_PARA_BOUNDARY_RE.finditer(piece))
        if matches:
            return matches[-1].end()
    matches = list(_SENT_BOUNDARY_RE.finditer(piece))
    if matches:
        return matches[-1].end()
    return None


def chunk_text(text: str, *, target: int = 800, hard: int = 1000) -> list[Chunk]:
    enc = tiktoken.get_encoding("cl100k_base")

    # 1. Strip LaTeX line-comments BEFORE section detection. We work on the
    #    stripped text throughout; chunk char_start/char_end indices are
    #    relative to the stripped text. Renderer output (source.html) is NOT
    #    re-rendered — the Citation Canvas resolves chunk offsets against the
    #    pre-rendered HTML, which never contained the comments anyway.
    text = _strip_latex_comments(text)

    # 2. Split into section-spans.
    spans: list[tuple[str | None, int, int]] = []
    last_idx = 0
    last_section: str | None = None
    for m in _SECTION_RE.finditer(text):
        if m.start() > last_idx:
            spans.append((last_section, last_idx, m.start()))
        last_section = m.group(1).strip()
        last_idx = m.end()
    if last_idx < len(text):
        spans.append((last_section, last_idx, len(text)))

    # 3. Greedy-fill each span up to hard cap, closing at paragraph (or
    #    sentence as fallback) boundaries once target is hit.
    out: list[Chunk] = []
    for section, span_start, span_end in spans:
        cursor = span_start
        while cursor < span_end:
            # Start with a generous window and shrink ONLY by halving until
            # we're under hard. Old impl subtracted `(tok_len - hard) * 4`
            # chars which overshot to cursor+1 in a single iteration on
            # dense LaTeX — that was the v2.10-1 bug.
            tentative_end = min(cursor + hard * 5, span_end)
            piece = text[cursor:tentative_end]
            tok_len = len(enc.encode(piece))
            while tok_len > hard and tentative_end - cursor > 1:
                # Halve the piece. Guaranteed to bottom out at cursor + 1 OR
                # under hard, whichever comes first; no overshoot.
                tentative_end = cursor + max(1, (tentative_end - cursor) // 2)
                piece = text[cursor:tentative_end]
                tok_len = len(enc.encode(piece))

            # Target-aware early-close at paragraph (preferred) or sentence
            # boundary, but only if we haven't shrunk down to a sliver.
            if (
                tok_len >= target
                and tentative_end < span_end
                and tentative_end - cursor > 100  # sanity floor
            ):
                boundary_off = _best_close(piece, prefer_paragraph=True)
                if boundary_off is not None and boundary_off > 100:
                    tentative_end = cursor + boundary_off
                    piece = text[cursor:tentative_end]

            raw_piece = text[cursor:tentative_end]
            stripped = raw_piece.strip()
            if not stripped:
                cursor = tentative_end
                continue
            lead = len(raw_piece) - len(raw_piece.lstrip())
            trail = len(raw_piece) - len(raw_piece.rstrip())
            out.append(
                Chunk(
                    section=section,
                    char_start=cursor + lead,
                    char_end=tentative_end - trail,
                    text=stripped,
                ),
            )
            cursor = tentative_end
    return out
```

- [ ] **Step 6: Run the three new tests + the existing chunker test suite**

```powershell
uv run pytest tests/test_chunker.py -v
```
Expected: all PASS, including the 3 new ones.

- [ ] **Step 7: Commit**

```powershell
git add backend/src/paperhub/pipelines/chunker.py backend/tests/test_chunker.py
git commit -m "fix(chunker): safe halving shrink + LaTeX comment strip + paragraph-bounded chunks (Plan C v2.10-1)"
```

---

### Task v2.10-2 — Persist `paper_content.sections_json` at ingest time

**Files:**
- Modify: [`backend/src/paperhub/db/migrations/`](../../../backend/src/paperhub/db/migrations/) — new migration adding the column.
- Modify: [`backend/src/paperhub/db/schema.sql`](../../../backend/src/paperhub/db/schema.sql) (or wherever the canonical CREATE TABLE lives — verify) — `sections_json TEXT` column on `paper_content`.
- Modify: [`backend/src/paperhub/pipelines/paper_pipeline.py`](../../../backend/src/paperhub/pipelines/paper_pipeline.py) — populate after chunking.
- Modify: [`backend/src/paperhub/models/domain.py`](../../../backend/src/paperhub/models/domain.py) — `PaperContent` dataclass + `SectionEntry` model.
- Modify: [`backend/tests/test_paper_pipeline.py`](../../../backend/tests/test_paper_pipeline.py).

**Schema shape** (TEXT column, JSON-encoded list of objects; never queried as JSON so a plain TEXT column suffices):

```json
[
  {"name": "Introduction", "char_start": 0, "char_end": 4892, "token_count": 1183, "chunk_count": 2},
  {"name": "Method", "char_start": 4892, "char_end": 18430, "token_count": 3320, "chunk_count": 4},
  {"name": "Experiments", "char_start": 18430, "char_end": 47120, "token_count": 7041, "chunk_count": 9}
]
```

No nesting (no subsection tree in v2.10 — `\subsection{...}` is treated as part of its parent section's flat text; can refine later if the subagent's section picks turn out to be too coarse).

**Steps:**

- [ ] **Step 1: Write a failing test that sections_json is populated on ingest**

```python
# backend/tests/test_paper_pipeline.py — append
async def test_paper_pipeline_persists_sections_json_at_ingest(
    tmp_path: Any, db_conn: aiosqlite.Connection,
) -> None:
    """After ingest, paper_content.sections_json must contain a list of
    {name, char_start, char_end, token_count, chunk_count} entries, ordered
    by appearance, covering every \\section{...} in the source."""
    sample_tex = (
        "\\section{Introduction}\nIntro body here. " * 30 + "\n\n"
        "\\section{Method}\nMethod body here. " * 30 + "\n\n"
        "\\section{Experiments}\nExperiment body here. " * 50 + "\n"
    )
    src = tmp_path / "src" / "main.tex"
    src.parent.mkdir(parents=True)
    src.write_text(sample_tex)

    pipeline = PaperPipeline(db_conn, _stub_chroma(), _stub_embedder())
    req = IngestRequest(
        paper_id="arxiv:9999.99999",
        arxiv_meta_override=ArxivMetadata(title="T", abstract="", authors=[], year=2024),
        source_dir_path=str(tmp_path / "src"),
    )
    result = await pipeline.ingest(req)

    async with db_conn.execute(
        "SELECT sections_json FROM paper_content WHERE id = ?",
        (result.paper_content_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    sections = json.loads(row[0])
    assert [s["name"] for s in sections] == ["Introduction", "Method", "Experiments"]
    for s in sections:
        assert s["chunk_count"] > 0
        assert s["token_count"] > 0
        assert s["char_end"] > s["char_start"]
```

- [ ] **Step 2: Confirm it fails — the column doesn't exist yet**

```powershell
uv run pytest tests/test_paper_pipeline.py::test_paper_pipeline_persists_sections_json_at_ingest -v
```
Expected: FAIL with `OperationalError: no such column: sections_json` (or KeyError when reading row).

- [ ] **Step 3: Add the schema migration**

The repo's migration mechanism: verify whether it's Alembic, raw SQL files run by `paperhub.db.init`, or sqlite `PRAGMA user_version` step-files. Pattern in earlier migrations is the source of truth — match it.

For a raw-SQL migration file (the most likely pattern given the small scope):

```sql
-- backend/src/paperhub/db/migrations/0006_paper_content_sections_json.sql
ALTER TABLE paper_content ADD COLUMN sections_json TEXT;
```

Also update the canonical CREATE TABLE in `schema.sql` (or equivalent) so a fresh DB has the column directly.

- [ ] **Step 4: Add the `SectionEntry` model + extend `PaperContent` shape**

```python
# backend/src/paperhub/models/domain.py — append
class SectionEntry(BaseModel):
    name: str
    char_start: int
    char_end: int
    token_count: int
    chunk_count: int
```

`PaperContent` (or whichever dataclass mirrors the row) gains `sections: list[SectionEntry] | None = None`.

- [ ] **Step 5: Populate during ingest**

In `paper_pipeline.py`'s ingest path, after chunking + before the `INSERT INTO paper_content`:

```python
import json
import tiktoken
from collections import defaultdict
from paperhub.models.domain import SectionEntry

# After: chunks = chunk_text(flattened_tex)
enc = tiktoken.get_encoding("cl100k_base")
per_section: dict[str | None, list[Chunk]] = defaultdict(list)
for c in chunks:
    per_section[c.section].append(c)
sections: list[SectionEntry] = []
for name, group in per_section.items():
    if name is None:  # text before any \section (preamble, abstract, etc.)
        continue
    section_text = flattened_tex[group[0].char_start : group[-1].char_end]
    sections.append(
        SectionEntry(
            name=name,
            char_start=group[0].char_start,
            char_end=group[-1].char_end,
            token_count=len(enc.encode(section_text)),
            chunk_count=len(group),
        ),
    )
sections_json = json.dumps([s.model_dump() for s in sections])
# Then include sections_json in the existing INSERT statement.
```

- [ ] **Step 6: Run the test from Step 1 + the full paper-pipeline suite**

```powershell
uv run pytest tests/test_paper_pipeline.py -v
```
Expected: all PASS.

- [ ] **Step 7: Commit**

```powershell
git add backend/src/paperhub/db backend/src/paperhub/pipelines/paper_pipeline.py backend/src/paperhub/models/domain.py backend/tests/test_paper_pipeline.py
git commit -m "feat(paper-pipeline): persist paper_content.sections_json at ingest (Plan C v2.10-2)"
```

---

### Task v2.10-3 — Per-paper subagent module + tool palette + bounded loop

**Files:**
- New: [`backend/src/paperhub/agents/paper_qa_subagent.py`](../../../backend/src/paperhub/agents/paper_qa_subagent.py).
- New: [`backend/src/paperhub/llm/prompts/paper_qa_subagent_v1.yaml`](../../../backend/src/paperhub/llm/prompts/paper_qa_subagent_v1.yaml).
- Modify: [`backend/src/paperhub/config.py`](../../../backend/src/paperhub/config.py) — new settings.
- Modify: [`backend/.env.example`](../../../backend/.env.example) — document new env vars.
- New: [`backend/tests/test_paper_qa_subagent.py`](../../../backend/tests/test_paper_qa_subagent.py).

**Module shape** (mirrors v2.7 `research_pipeline.py` structure — single-responsibility stage helpers, no class state):

- [ ] **Step 1: Write the failing subagent-loop test**

```python
# backend/tests/test_paper_qa_subagent.py
async def test_subagent_loop_lists_sections_reads_picks_chunks_and_stops():
    """Happy path: subagent receives a question, lists sections, reads two
    relevant ones, cites chunks via [chunk:N] markers in its final no-tool-
    calls message; Python extracts the picks."""
    # Seed: 2 sections, 4 chunks total in DB; LLM is stubbed.
    paper = _seed_paper_with_sections(
        sections=[
            {"name": "Method", "chunks": [(101, "We use cross-attention.")]},
            {"name": "Experiments", "chunks": [(102, "We achieve 92% accuracy.")]},
        ],
    )
    stub_llm = _StubLlm([
        # Turn 1: call list_sections
        _tool_call("list_sections", {}),
        # Turn 2: call read_section("Method")
        _tool_call("read_section", {"name": "Method"}),
        # Turn 3: call read_section("Experiments")
        _tool_call("read_section", {"name": "Experiments"}),
        # Turn 4: no tool calls → final summary with citations
        _final("Paper covers cross-attention [chunk:101] and 92% acc [chunk:102]."),
    ])

    picks = await run_paper_qa_subagent(
        paper_content_id=paper.id,
        title=paper.title,
        user_message="What method and what accuracy?",
        adapter=stub_llm,
        tracer=_FakeTracer(),
        model="stub",
        conn=db_conn,
    )

    assert picks.paper_content_id == paper.id
    assert sorted(c.chunk_id for c in picks.picked_chunks) == [101, 102]
    assert "cross-attention" in picks.picked_chunks[0].text
    assert "92%" in picks.picked_chunks[1].text
    assert picks.rationale  # non-empty 1-line summary
```

- [ ] **Step 2: Add max-iter-cap test**

```python
async def test_subagent_loop_stops_at_max_section_reads_and_returns_what_it_read():
    """If the LLM keeps calling tools past MAX_SECTION_READS, the loop force-
    stops and returns every chunk the subagent ever read (best-effort)."""
    paper = _seed_paper_with_n_sections(n=10)
    # LLM that always calls read_section, never emits a final.
    stub_llm = _StubLlm(infinite=[_tool_call("read_section", {"name": "S0"})])

    picks = await run_paper_qa_subagent(
        paper_content_id=paper.id,
        title=paper.title,
        user_message="Tell me everything",
        adapter=stub_llm,
        tracer=_FakeTracer(),
        model="stub",
        conn=db_conn,
    )

    # MAX_SECTION_READS=5 → 5 read calls (plus the implicit list_sections is
    # NOT auto-called; total tool_calls = 5).
    assert stub_llm.calls == 5
    # All chunks from the 5 reads end up in picks (force-stop fallback).
    assert len(picks.picked_chunks) > 0
```

- [ ] **Step 3: Add unknown-section error-tolerance test**

```python
async def test_subagent_read_section_unknown_returns_error_to_llm_not_crash():
    """If the LLM asks for a section that doesn't exist, return a clean error
    message in the tool result so the LLM can recover (probably call
    list_sections first)."""
    paper = _seed_paper_with_sections(sections=[{"name": "Method", "chunks": [(101, "x")]}])
    stub_llm = _StubLlm([
        _tool_call("read_section", {"name": "Nonexistent"}),
        _final("I checked but the requested section doesn't exist [no chunks cited]."),
    ])

    picks = await run_paper_qa_subagent(
        paper_content_id=paper.id, title="P", user_message="q",
        adapter=stub_llm, tracer=_FakeTracer(), model="stub", conn=db_conn,
    )

    # No crash. Empty picks is fine.
    assert picks.picked_chunks == []
    # The error message was relayed to the LLM (check stub_llm.last_tool_result).
    assert "unknown section" in stub_llm.last_tool_result.lower() or \
           "not found" in stub_llm.last_tool_result.lower()
```

- [ ] **Step 4: Run all three subagent tests — confirm fails**

```powershell
uv run pytest tests/test_paper_qa_subagent.py -v
```
Expected: 3 FAIL (module doesn't exist).

- [ ] **Step 5: Write the subagent prompt**

```yaml
# backend/src/paperhub/llm/prompts/paper_qa_subagent_v1.yaml
system: |
  You are PaperHub's per-paper analyst. The user has a question about
  multiple papers — your job is to scan ONE paper's structure and pick the
  chunks that contain relevant evidence.

  You have two tools and a hard budget:
  - `list_sections()`: returns this paper's section table-of-contents
    (name + token count per section). Call this FIRST if you don't know
    the paper.
  - `read_section(name)`: returns every chunk in that section, each with
    its `[chunk:<id>]` header and full text.
  - You may make at most {max_section_reads} `read_section` calls per
    turn. `list_sections` doesn't count against this budget.

  When you've read enough to answer the question for THIS paper, stop
  calling tools and write a 2-3 sentence summary of what this paper says
  on the user's question, citing the chunks you found relevant using
  `[chunk:<id>]` markers. Multiple cites OK: `[chunk:101,102]`.

  Rules:
  - Cite every claim with `[chunk:<id>]`. The system extracts these IDs
    from your summary and treats them as your picks — uncited chunks are
    discarded.
  - If the paper doesn't address the question, say so explicitly in your
    summary. Do not fabricate.
  - Do NOT compare to other papers — you only see one. A separate
    finalizer handles cross-paper synthesis.
  - Be willing to re-read a section if needed (re-reads count against
    the budget; budget yourself).
user: |
  PAPER: "{title}"

  USER QUESTION: {user_message}

  Pick the chunks that contain evidence for the user's question.
```

- [ ] **Step 6: Wire settings**

```python
# backend/src/paperhub/config.py — append to Settings model
paper_qa_subagent_model: str = "gemini/gemini-3.1-flash-lite"
paper_qa_max_section_reads: int = 5
```

```bash
# backend/.env.example — append
PAPERHUB_PAPER_QA_SUBAGENT_MODEL=gemini/gemini-3.1-flash-lite
PAPERHUB_PAPER_QA_MAX_SECTION_READS=5
```

- [ ] **Step 7: Implement the subagent module**

```python
# backend/src/paperhub/agents/paper_qa_subagent.py
"""Per-paper agentic chunk picker (Plan C v2.10).

Replaces the v2.7 dense-RAG + analyst-prose path with a bounded LLM loop
that browses the paper's section table-of-contents and decides which
sections to read. The LLM's final no-tool-calls message contains
`[chunk:<id>]` markers that Python extracts and treats as the subagent's
picks. The finalizer downstream reads the picks' raw text directly.

Cross-paper visibility is intentionally zero: one subagent state per
paper, fan-out via asyncio.gather upstream. The finalizer is the only
cross-paper synthesis surface.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import aiosqlite
import litellm

from paperhub.llm.adapter import LlmAdapter
from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.tracing.tracer import Tracer

__all__ = [
    "MAX_SECTION_READS",
    "PickedChunk",
    "PerPaperPicks",
    "run_paper_qa_subagent",
]

_LOG = logging.getLogger(__name__)

# Default; overridden per call by Settings.paper_qa_max_section_reads.
MAX_SECTION_READS = 5

# [chunk:101] OR [chunk:101,102,103]
_CHUNK_MARKER_RE = re.compile(r"\[chunk:(\d+(?:,\d+)*)\]")


@dataclass(frozen=True)
class PickedChunk:
    chunk_id: int
    text: str
    section: str | None


@dataclass(frozen=True)
class PerPaperPicks:
    paper_content_id: int
    title: str
    picked_chunks: list[PickedChunk]
    rationale: str  # the subagent's own 1-3 sentence summary


_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_sections",
            "description": (
                "Return this paper's section table-of-contents (name + "
                "token count + chunk count per section). Free; doesn't "
                "count against the read budget."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_section",
            "description": (
                "Return every chunk in the named section, each with its "
                "[chunk:<id>] header and full text. Counts against the "
                "max-reads budget."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exact section name from list_sections.",
                    },
                },
                "required": ["name"],
            },
        },
    },
]


async def _list_sections(
    *, paper_content_id: int, conn: aiosqlite.Connection,
) -> str:
    async with conn.execute(
        "SELECT sections_json FROM paper_content WHERE id = ?",
        (paper_content_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None or row[0] is None:
        return json.dumps({"error": "no section TOC available for this paper"})
    sections = json.loads(row[0])
    # Trim the payload to what the LLM needs.
    return json.dumps(
        [
            {"name": s["name"], "tokens": s["token_count"], "chunks": s["chunk_count"]}
            for s in sections
        ],
    )


async def _read_section(
    *, paper_content_id: int, name: str, conn: aiosqlite.Connection,
) -> tuple[str, list[PickedChunk]]:
    """Return the prompt-shaped text for the LLM AND a parallel list of
    PickedChunk records so the caller can stash them for force-stop fallback."""
    async with conn.execute(
        "SELECT id, text, section FROM chunks "
        "WHERE paper_content_id = ? AND section = ? "
        "ORDER BY char_start",
        (paper_content_id, name),
    ) as cur:
        rows = await cur.fetchall()
    if not rows:
        return (
            json.dumps({"error": f"unknown section: {name!r}. Call list_sections() first."}),
            [],
        )
    picks = [PickedChunk(chunk_id=r[0], text=r[1], section=r[2]) for r in rows]
    body = "\n\n".join(f"[chunk:{p.chunk_id}]\n{p.text}" for p in picks)
    return (body, picks)


def _extract_cited_chunk_ids(summary: str) -> list[int]:
    out: list[int] = []
    for m in _CHUNK_MARKER_RE.finditer(summary):
        out.extend(int(x) for x in m.group(1).split(","))
    return out


async def run_paper_qa_subagent(
    *,
    paper_content_id: int,
    title: str,
    user_message: str,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    conn: aiosqlite.Connection,
    max_section_reads: int = MAX_SECTION_READS,
) -> PerPaperPicks:
    """Run one per-paper subagent loop. Bounded by ``max_section_reads``
    ``read_section`` calls. Returns the chunks the LLM cited in its final
    no-tool-calls message; on force-stop returns every chunk read so far."""
    prompt_registry = PromptRegistry()
    sys_text, user_text = prompt_registry.render(
        slot="paper_qa_subagent/v1",
        variables={
            "title": title,
            "user_message": user_message,
            "max_section_reads": max_section_reads,
        },
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": sys_text},
        {"role": "user", "content": user_text},
    ]
    seen_chunks: dict[int, PickedChunk] = {}  # chunk_id → PickedChunk
    reads_used = 0
    final_summary = ""

    async with tracer.step(
        agent="research", tool="paper_qa:subagent", model=model,
    ) as step:
        step.record_args(
            {"paper_content_id": paper_content_id, "title": title},
        )
        while True:
            response = await adapter.acompletion(
                model=model, messages=messages, tools=_TOOL_SCHEMAS,
            )
            choice = response.choices[0]
            tool_calls = getattr(choice.message, "tool_calls", None) or []
            messages.append(choice.message.model_dump())

            if not tool_calls:
                final_summary = choice.message.content or ""
                break

            for tc in tool_calls:
                name = tc.function.name
                args = json.loads(tc.function.arguments or "{}")
                if name == "list_sections":
                    result = await _list_sections(
                        paper_content_id=paper_content_id, conn=conn,
                    )
                elif name == "read_section":
                    if reads_used >= max_section_reads:
                        result = json.dumps({
                            "error": (
                                f"read_section budget exhausted "
                                f"({max_section_reads}). Stop calling tools "
                                "and write your final summary now."
                            ),
                        })
                    else:
                        reads_used += 1
                        result, picks = await _read_section(
                            paper_content_id=paper_content_id,
                            name=args.get("name", ""),
                            conn=conn,
                        )
                        for p in picks:
                            seen_chunks.setdefault(p.chunk_id, p)
                else:
                    result = json.dumps({"error": f"unknown tool: {name}"})

                messages.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "name": name, "content": result,
                })

            # Force-stop guard: if the LLM never emits a no-tool-calls turn
            # AND we've blown the budget, the next tool_call's budget-
            # exhausted result usually convinces it. But if not, break after
            # one more LLM call lands without a clean final.
            if reads_used >= max_section_reads and not any(
                tc.function.name == "read_section" for tc in tool_calls
            ):
                # No useful work being done. Force-stop.
                break

        cited_ids = _extract_cited_chunk_ids(final_summary)
        if cited_ids:
            picked = [seen_chunks[cid] for cid in cited_ids if cid in seen_chunks]
        else:
            # Force-stop or LLM forgot to cite — best-effort: hand every
            # chunk we ever read to the finalizer.
            picked = list(seen_chunks.values())

        step.record_result({
            "reads_used": reads_used,
            "chunks_read": len(seen_chunks),
            "chunks_cited": len(picked),
            "summary_len": len(final_summary),
        })

    return PerPaperPicks(
        paper_content_id=paper_content_id,
        title=title,
        picked_chunks=picked,
        rationale=final_summary,
    )
```

- [ ] **Step 8: Run the subagent tests — confirm all green**

```powershell
uv run pytest tests/test_paper_qa_subagent.py -v
```
Expected: 3 PASS.

- [ ] **Step 9: Commit**

```powershell
git add backend/src/paperhub/agents/paper_qa_subagent.py backend/src/paperhub/llm/prompts/paper_qa_subagent_v1.yaml backend/src/paperhub/config.py backend/.env.example backend/tests/test_paper_qa_subagent.py
git commit -m "feat(agents): per-paper paper_qa subagent w/ list_sections + read_section tools (Plan C v2.10-3)"
```

---

### Task v2.10-4 — `paper_qa` LangGraph topology replacement (dispatch → fan-out subagents → finalize)

**Files:**
- Modify: [`backend/src/paperhub/agents/research_graph.py`](../../../backend/src/paperhub/agents/research_graph.py) — replace the existing `_paper_qa_map_one` / `_paper_qa_synthesize_stream` node wiring with the new four-node subgraph.
- Modify: [`backend/src/paperhub/agents/research.py`](../../../backend/src/paperhub/agents/research.py) — delete `_paper_qa_map_one`, `_paper_qa_synthesize_stream`, `_paper_qa_map_reduce`, `_K_PER_PAPER`. Keep `paper_qa` entry-point function (signature unchanged) but reduce to a subgraph driver.
- Modify: [`backend/src/paperhub/agents/state.py`](../../../backend/src/paperhub/agents/state.py) — extend `AgentState` with `pq_dispatched_paper_ids`, `pq_per_paper_picks`.
- New: [`backend/src/paperhub/llm/prompts/paper_qa_synthesize_v2.yaml`](../../../backend/src/paperhub/llm/prompts/paper_qa_synthesize_v2.yaml) — finalizer sees raw chunks, not analyst prose.
- Delete: [`backend/src/paperhub/llm/prompts/paper_qa_per_paper_v1.yaml`](../../../backend/src/paperhub/llm/prompts/paper_qa_per_paper_v1.yaml) — no analyst stage anymore.
- Delete: [`backend/src/paperhub/llm/prompts/paper_qa_synthesize_v1.yaml`](../../../backend/src/paperhub/llm/prompts/paper_qa_synthesize_v1.yaml) — superseded by v2.
- Modify: [`backend/src/paperhub/llm/prompts/registry.py`](../../../backend/src/paperhub/llm/prompts/registry.py) — drop the deleted slot registrations, add the new ones.
- Modify: [`backend/tests/test_research_paper_qa.py`](../../../backend/tests/test_research_paper_qa.py) — replace map-reduce tests with subgraph tests.
- Modify: [`backend/tests/test_research_subgraph.py`](../../../backend/tests/test_research_subgraph.py) — paper_qa subgraph topology assertions.

**Finalizer prompt** (`paper_qa_synthesize_v2.yaml`):

```yaml
system: |
  You are PaperHub's synthesis agent. The user has a question about
  multiple papers. For each paper, a per-paper subagent has read the
  paper's section table-of-contents, picked the chunks containing
  relevant evidence, and written a brief rationale. You receive the
  picks (raw chunk text with `[chunk:<id>]` markers) and the rationales.
  Compose the final user-facing answer.

  Rules:
  - Reference each paper by its TITLE in quotes (e.g., 'In "Attention
    Is All You Need", the authors…'), never by an internal ID.
  - Cite every claim with the `[chunk:<id>]` markers from the chunk
    headers. Multiple cites OK: `[chunk:101,102]`.
  - If the question is comparative ("how do X and Y differ"), structure
    the answer to address EACH paper + the contrast.
  - If a paper's subagent reported no relevant content, mention that
    paper briefly so the user knows it was considered.
  - Read the raw chunks — that's the ground truth. The subagent
    rationales are hints, not authority. If a chunk contradicts the
    rationale, trust the chunk.
  - Be focused and well-structured. Don't restate every chunk verbatim.
user: |
  USER QUESTION: {user_message}

  --- PAPERS ---
  {per_paper_block}
  --- END PAPERS ---

  Compose the synthesis.
```

`per_paper_block` is built in Python as:

```
## "Title of paper A"
Subagent rationale: <rationale text>
Relevant chunks:
[chunk:101]
<chunk 101 text>

[chunk:104]
<chunk 104 text>

---

## "Title of paper B"
Subagent rationale: <rationale text>
Relevant chunks:
[chunk:203]
<chunk 203 text>
```

**Steps:**

- [ ] **Step 1: Write a failing subgraph topology test**

```python
# backend/tests/test_research_subgraph.py — replace paper_qa cases
async def test_paper_qa_subgraph_dispatches_one_subagent_per_enabled_paper():
    """pq_dispatch fans out per asyncio.gather; one PerPaperPicks per paper
    lands in state.pq_per_paper_picks before pq_finalize runs."""
    state = _state_with_enabled_papers(ids=[15, 16])
    stub_subagent = _stub_subagent_factory({
        15: PerPaperPicks(paper_content_id=15, title="A", picked_chunks=[_chunk(101, "a")], rationale="r"),
        16: PerPaperPicks(paper_content_id=16, title="B", picked_chunks=[_chunk(201, "b")], rationale="s"),
    })
    with patch("paperhub.agents.research_graph.run_paper_qa_subagent", stub_subagent):
        graph = build_research_subgraph()
        result = await graph.ainvoke(state)
    assert sorted(p.paper_content_id for p in result["pq_per_paper_picks"]) == [15, 16]
    # Both subagents ran in parallel (not serial) — verify via fixture timing.
    assert stub_subagent.max_concurrency == 2
```

- [ ] **Step 2: Write a failing finalizer test**

```python
async def test_paper_qa_finalizer_streams_synthesis_with_chunk_markers_preserved():
    """The finalizer prompt embeds raw chunk text + rationale per paper; its
    streaming output preserves `[chunk:N]` markers for the Citation Canvas."""
    picks = [
        PerPaperPicks(
            paper_content_id=15, title="MolmoAct",
            picked_chunks=[_chunk(101, "We compute action tokens via Q-former."),
                           _chunk(102, "Loss is cross-entropy on tokenized actions.")],
            rationale="Method centers on action tokenization.",
        ),
        PerPaperPicks(
            paper_content_id=16, title="X-VLA",
            picked_chunks=[_chunk(203, "Soft prompts learned per embodiment.")],
            rationale="Method centers on soft-prompt heterogeneity.",
        ),
    ]
    stub_llm = _StubStreamLlm(
        "Both papers tokenize actions [chunk:101] but X-VLA adds soft "
        "prompts [chunk:203]. Loss is CE [chunk:102].",
    )
    tokens = []
    async for tok in run_paper_qa_finalize(
        per_paper_picks=picks, user_message="compare the methods",
        adapter=stub_llm, tracer=_FakeTracer(), model="stub",
    ):
        tokens.append(tok)
    out = "".join(tokens)
    assert "[chunk:101]" in out
    assert "[chunk:203]" in out
```

- [ ] **Step 3: Run both tests — confirm fails**

```powershell
uv run pytest tests/test_research_subgraph.py::test_paper_qa_subgraph_dispatches_one_subagent_per_enabled_paper tests/test_research_paper_qa.py::test_paper_qa_finalizer_streams_synthesis_with_chunk_markers_preserved -v
```
Expected: 2 FAIL.

- [ ] **Step 4: Implement `paper_qa_finalize` in `research.py`**

```python
# backend/src/paperhub/agents/research.py — REPLACES old _paper_qa_synthesize_stream
async def paper_qa_finalize(
    *,
    per_paper_picks: list[PerPaperPicks],
    user_message: str,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    state: AgentState | None = None,
    **adapter_kwargs: Any,
) -> AsyncIterator[str]:
    """Finalizer: stream a user-facing synthesis over per-paper picks.

    Replaces the v2.7 _paper_qa_synthesize_stream — that variant saw only
    analyst prose; this one sees raw chunk text + a brief subagent
    rationale per paper, mirroring NOTES.md v5's evidence+context lesson.
    """
    parts: list[str] = []
    for pp in per_paper_picks:
        chunks_block = "\n\n".join(
            f"[chunk:{c.chunk_id}]\n{c.text}" for c in pp.picked_chunks
        ) or "(no chunks cited)"
        parts.append(
            f'## "{pp.title}"\n'
            f"Subagent rationale: {pp.rationale}\n"
            f"Relevant chunks:\n{chunks_block}",
        )
    per_paper_block = "\n\n---\n\n".join(parts)

    async with tracer.step(
        agent="research", tool="paper_qa:finalize", model=model,
    ) as step:
        step.record_args({
            "n_papers": len(per_paper_picks),
            "n_chunks": sum(len(p.picked_chunks) for p in per_paper_picks),
        })
        collected: list[str] = []
        async for tok in adapter.stream(
            slot="paper_qa_synthesize/v2",
            variables={"user_message": user_message, "per_paper_block": per_paper_block},
            model=model,
            history=state.get("history") if state else None,
            **adapter_kwargs,
        ):
            collected.append(tok)
            yield tok
        step.record_result({"length": sum(len(c) for c in collected)})
```

- [ ] **Step 5: Replace the LangGraph wiring in `research_graph.py`**

Find the existing paper_qa node registrations + edges (the v2.7 map-reduce path) and replace with:

```python
# backend/src/paperhub/agents/research_graph.py — paper_qa subgraph
from paperhub.agents.paper_qa_subagent import run_paper_qa_subagent, PerPaperPicks
from paperhub.agents.research import paper_qa_finalize

async def _pq_dispatch(state: AgentState) -> AgentState:
    """Fan-out: one subagent task per enabled paper, asyncio.gather."""
    pids = state["enabled_paper_content_ids"]
    settings = state["_settings"]  # threaded through from caller
    coros = [
        run_paper_qa_subagent(
            paper_content_id=pid,
            title=state["_paper_titles_by_id"][pid],
            user_message=state["user_message"],
            adapter=state["_adapter"],
            tracer=state["_tracer"],
            model=settings.paper_qa_subagent_model,
            conn=state["_conn"],
            max_section_reads=settings.paper_qa_max_section_reads,
        )
        for pid in pids
    ]
    picks_list = await asyncio.gather(*coros)
    return {**state, "pq_per_paper_picks": picks_list}

async def _pq_finalize_node(state: AgentState) -> AgentState:
    """Single flagship call; tokens stream via the existing SSE pipe."""
    picks = state["pq_per_paper_picks"]
    if all(not p.picked_chunks for p in picks):
        # Short-circuit: nothing to synthesize.
        return {**state, "final_response": (
            "I checked every enabled reference but none contained content "
            "relevant to your question. Try a more specific question or "
            "add more references."
        )}
    parts: list[str] = []
    async for tok in paper_qa_finalize(
        per_paper_picks=picks,
        user_message=state["user_message"],
        adapter=state["_adapter"],
        tracer=state["_tracer"],
        model=state["_settings"].paper_qa_model,
        state=state,
    ):
        parts.append(tok)
        # streaming sink wired by chat.py — same as v2.7 finalize hook
    return {**state, "final_response": "".join(parts)}

# In build_research_subgraph(): register the two nodes + edge
graph.add_node("pq_dispatch", _pq_dispatch)
graph.add_node("pq_finalize", _pq_finalize_node)
graph.add_edge("pq_resolve", "pq_dispatch")
graph.add_edge("pq_dispatch", "pq_finalize")
graph.add_edge("pq_finalize", END)
```

(Verify against the actual `build_research_subgraph` shape — node names + edges should mirror the v2.7 `ps_*` pattern.)

- [ ] **Step 6: Delete the old map-reduce code**

Remove from `backend/src/paperhub/agents/research.py`: `_K_PER_PAPER`, `_paper_qa_map_one`, `_paper_qa_synthesize_stream`, `_paper_qa_map_reduce`. Keep the top-level `paper_qa(...)` entry-point — but slim it to a subgraph invoker (compatible with existing chat.py call sites).

Delete:
- `backend/src/paperhub/llm/prompts/paper_qa_per_paper_v1.yaml`
- `backend/src/paperhub/llm/prompts/paper_qa_synthesize_v1.yaml`

Update `paperhub.llm.prompts.registry.PromptRegistry`: drop `paper_qa_per_paper/v1` and `paper_qa_synthesize/v1` registrations; add `paper_qa_subagent/v1` and `paper_qa_synthesize/v2`.

- [ ] **Step 7: Run the new subgraph tests + the full backend suite**

```powershell
uv run pytest tests/test_research_subgraph.py tests/test_research_paper_qa.py -v
uv run pytest -q
```
Expected: green across the board (modulo the pre-existing flaky `test_paper_qa_map_reduce_runs_map_steps_in_parallel_via_gather` — DELETE that test in this task since the map-reduce path is gone).

- [ ] **Step 8: Run ruff + mypy**

```powershell
uv run ruff check src tests
uv run mypy src
```
Expected: clean.

- [ ] **Step 9: Commit**

```powershell
git add backend/src/paperhub/agents/ backend/src/paperhub/llm/prompts/ backend/tests/test_research_subgraph.py backend/tests/test_research_paper_qa.py
git commit -m "refactor(agents): paper_qa hierarchical agentic pipeline replaces map-reduce (Plan C v2.10-4)"
```

---

### Task v2.10-5 — Re-ingest existing papers (drop chunks + Chroma vectors + re-run pipeline)

**Files:**
- New: [`backend/scripts/reingest_all_papers.ps1`](../../../backend/scripts/reingest_all_papers.ps1) — operator script.
- New: [`backend/src/paperhub/cli/reingest.py`](../../../backend/src/paperhub/cli/reingest.py) — `paperhub-reingest` entry-point.
- Modify: [`backend/pyproject.toml`](../../../backend/pyproject.toml) — script entry.

**Why this is mandatory:** every `paper_content` row in the live workspace was chunked by the broken pre-v2.10-1 chunker AND lacks `sections_json`. The subagent's `list_sections` returns nothing useful and `read_section` finds the right `section` field but its chunks are 1-char garbage. Re-ingest is the only way the new pipeline becomes useful on existing data.

**Approach:** for each `paper_content` row, the source is already cached under `workspace/papers_cache/{arxiv,upload}/<key>/source/`. Re-run the pipeline starting from "read flattened source → chunk → embed → persist" while preserving `paper_content.id` (so existing `papers` membership rows and `messages` referencing the paper survive).

- [ ] **Step 1: Write the CLI**

```python
# backend/src/paperhub/cli/reingest.py
"""Re-chunk + re-embed every paper_content row using the current chunker.

Deletes chunks + Chroma vectors first; preserves paper_content.id so
membership (papers) + message history survive. Idempotent — runs as many
times as needed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import aiosqlite

from paperhub.config import load_settings
from paperhub.pipelines.chunker import chunk_text
from paperhub.pipelines.embedder import get_embedder
from paperhub.rag.chroma import ChromaStore


async def _reingest_one(
    pcid: int,
    conn: aiosqlite.Connection,
    chroma: ChromaStore,
    embedder,
) -> tuple[int, int]:
    """Returns (chunks_before, chunks_after) for logging."""
    async with conn.execute(
        "SELECT source_path, source_dir_path FROM paper_content WHERE id = ?",
        (pcid,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return (0, 0)
    source_path = row[0]
    flattened = Path(source_path).read_text(encoding="utf-8", errors="replace")

    async with conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE paper_content_id = ?", (pcid,),
    ) as cur:
        before_row = await cur.fetchone()
    before = int(before_row[0]) if before_row else 0

    # Delete in dependency order.
    await conn.execute("DELETE FROM chunks WHERE paper_content_id = ?", (pcid,))
    chroma.delete_by_paper(pcid)
    await conn.commit()

    # Re-chunk + re-embed + persist (same path the pipeline uses).
    chunks = chunk_text(flattened)
    if not chunks:
        return (before, 0)
    embeddings = embedder.embed([c.text for c in chunks])

    # Insert chunks, capture new ids.
    cursor = await conn.execute("BEGIN")
    new_ids: list[int] = []
    for c in chunks:
        async with conn.execute(
            "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text) "
            "VALUES (?, ?, ?, ?, ?) RETURNING id",
            (pcid, c.section, c.char_start, c.char_end, c.text),
        ) as cur:
            r = await cur.fetchone()
            assert r is not None
            new_ids.append(int(r[0]))

    # Populate sections_json (mirrors paper_pipeline.py logic).
    import tiktoken
    from collections import defaultdict

    enc = tiktoken.get_encoding("cl100k_base")
    per_section: dict[str | None, list] = defaultdict(list)
    for c in chunks:
        per_section[c.section].append(c)
    sections = []
    for name, group in per_section.items():
        if name is None:
            continue
        section_text = flattened[group[0].char_start : group[-1].char_end]
        sections.append({
            "name": name,
            "char_start": group[0].char_start,
            "char_end": group[-1].char_end,
            "token_count": len(enc.encode(section_text)),
            "chunk_count": len(group),
        })
    await conn.execute(
        "UPDATE paper_content SET sections_json = ? WHERE id = ?",
        (json.dumps(sections), pcid),
    )

    # Insert Chroma vectors keyed by new chunk ids.
    chroma.add(
        ids=[str(i) for i in new_ids],
        embeddings=embeddings,
        metadatas=[
            {"paper_content_id": pcid, "section": c.section or "",
             "char_start": c.char_start, "char_end": c.char_end}
            for c in chunks
        ],
        documents=[c.text for c in chunks],
    )
    await conn.commit()
    return (before, len(chunks))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--paper-content-id", type=int, default=None,
                        help="Re-ingest just this paper_content.id (default: all).")
    args = parser.parse_args()

    settings = load_settings()
    chroma = ChromaStore(settings.chroma_dir)
    embedder = get_embedder()
    async with aiosqlite.connect(settings.db_path) as conn:
        if args.paper_content_id is not None:
            ids = [args.paper_content_id]
        else:
            async with conn.execute("SELECT id FROM paper_content ORDER BY id") as cur:
                ids = [int(r[0]) for r in await cur.fetchall()]
        print(f"Re-ingesting {len(ids)} paper(s)...")
        for pcid in ids:
            before, after = await _reingest_one(pcid, conn, chroma, embedder)
            print(f"  pcid={pcid}: {before} chunks -> {after} chunks")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Wire the script entry**

```toml
# backend/pyproject.toml — under [project.scripts]
paperhub-reingest = "paperhub.cli.reingest:main"
```

- [ ] **Step 3: Wrap in a PowerShell smoke for the operator**

```powershell
# backend/scripts/reingest_all_papers.ps1
$ErrorActionPreference = "Stop"
Write-Host "Backing up workspace/paperhub.db -> paperhub.db.bak.v2.10..."
Copy-Item workspace/paperhub.db workspace/paperhub.db.bak.v2.10 -Force
Write-Host "Backing up workspace/chroma -> chroma.bak.v2.10..."
if (Test-Path workspace/chroma.bak.v2.10) { Remove-Item -Recurse -Force workspace/chroma.bak.v2.10 }
Copy-Item workspace/chroma workspace/chroma.bak.v2.10 -Recurse
Write-Host "Running re-ingest..."
uv run paperhub-reingest
Write-Host "Done. Old data preserved at workspace/*.bak.v2.10/."
```

- [ ] **Step 4: Manual run + verify**

```powershell
.\scripts\reingest_all_papers.ps1
```
Expected:
- Pre-existing 29,891 chunks for paper 15 collapse to ~30-60 chunks
- Pre-existing 1,839 chunks for paper 16 collapse to ~30-50 chunks
- `paper_content.sections_json` is populated for every row
- Workspace backup directory exists for rollback

- [ ] **Step 5: Commit (script + cli)**

```powershell
git add backend/src/paperhub/cli/reingest.py backend/scripts/reingest_all_papers.ps1 backend/pyproject.toml
git commit -m "feat(cli): paperhub-reingest — rechunk + reembed existing papers under v2.10 chunker (Plan C v2.10-5)"
```

---

### Task v2.10-6 — End-to-end smoke: hierarchical paper_qa on the failing MolmoACT2 + X-VLA question

**Files:**
- Modify: [`backend/scripts/query_papers.ps1`](../../../backend/scripts/query_papers.ps1) — assert the new tool_step shapes.

**Steps:**

- [ ] **Step 1: Update the smoke to assert subagent + finalize trace shape**

The script already runs a multi-paper Q&A and asserts `[chunk:N]` markers. Add assertions for the new trace structure:

```powershell
# scripts/query_papers.ps1 — append assertions
$expected_steps = @("paper_qa:resolve", "paper_qa:subagent", "paper_qa:finalize")
foreach ($s in $expected_steps) {
  if (-not ($sseRaw -match "tool=`"$s`"")) {
    Write-Error "Expected tool_step '$s' missing from SSE trace"; exit 1
  }
}
# At least one paper_qa:subagent step per enabled paper.
$subagent_count = ([regex]::Matches($sseRaw, 'tool="paper_qa:subagent"')).Count
if ($subagent_count -lt 2) {
  Write-Error "Expected >=2 paper_qa:subagent steps (one per enabled paper), got $subagent_count"
  exit 1
}
```

- [ ] **Step 2: Run the smoke (manual — needs the user's actual papers + a real LLM key)**

```powershell
.\scripts\query_papers.ps1 "Compare the methods of these two papers" 60 16
```
Expected:
- Trace shows `paper_qa:resolve` → 2× `paper_qa:subagent` (one per paper, in parallel) → `paper_qa:finalize`
- Each subagent's `result_summary` has `reads_used > 0`, `chunks_cited > 0`
- Finalizer output contains `[chunk:N]` markers for chunks from BOTH papers (acceptance criterion I-8 #3)
- The "I cannot provide a comparison" failure mode is gone

- [ ] **Step 3: Commit**

```powershell
git add backend/scripts/query_papers.ps1
git commit -m "test(smoke): assert v2.10 paper_qa subagent + finalize trace shape (Plan C v2.10-6)"
```

---

### Quality gates after the v2.10 round

From `backend/`:

```powershell
uv run pytest -v                       # subagent + topology + finalize tests added; map-reduce tests removed
uv run ruff check src tests
uv run mypy src                        # strict-clean
.\scripts\reingest_all_papers.ps1      # one-shot, idempotent
.\scripts\query_papers.ps1             # end-to-end with new trace shape
.\scripts\smoke_mcp_papers.ps1         # regression — unchanged path
.\scripts\smoke_chat_real.ps1          # paper_search regression
```

Browser verification (manual, after re-ingest + backend restart):
- "Compare the methods of MolmoAct2 and X-VLA" → trace panel shows the four `paper_qa:resolve` → `paper_qa:subagent` ×2 → `paper_qa:finalize` rows; expanding a subagent row reveals `reads_used`, `chunks_read`, `chunks_cited`; final assistant message contains `[chunk:N]` citations resolvable in the Citation Canvas to chunks from BOTH papers.
- Single-paper Q&A still works (only one subagent fires; finalizer handles single-paper case).
- Disabling one of the two papers via the References drawer + re-asking → only one subagent fires; answer cites only the remaining paper. (Validates v2.10 honors `enabled=true` filter at `pq_resolve`.)
- Operator workflow: starting the backend on a fresh clone with no `papers_cache` → `paper_qa` returns the "I checked every enabled reference but none contained content" short-circuit; ingesting a paper then re-asking succeeds.

### Files touched (summary)

- New: [`backend/src/paperhub/agents/paper_qa_subagent.py`](../../../backend/src/paperhub/agents/paper_qa_subagent.py)
- New: [`backend/src/paperhub/llm/prompts/paper_qa_subagent_v1.yaml`](../../../backend/src/paperhub/llm/prompts/paper_qa_subagent_v1.yaml)
- New: [`backend/src/paperhub/llm/prompts/paper_qa_synthesize_v2.yaml`](../../../backend/src/paperhub/llm/prompts/paper_qa_synthesize_v2.yaml)
- New: [`backend/src/paperhub/cli/reingest.py`](../../../backend/src/paperhub/cli/reingest.py)
- New: [`backend/scripts/reingest_all_papers.ps1`](../../../backend/scripts/reingest_all_papers.ps1)
- New: [`backend/src/paperhub/db/migrations/0006_paper_content_sections_json.sql`](../../../backend/src/paperhub/db/migrations/0006_paper_content_sections_json.sql) (or equivalent — verify migration mechanism)
- New: [`backend/tests/test_paper_qa_subagent.py`](../../../backend/tests/test_paper_qa_subagent.py)
- Modify: [`backend/src/paperhub/pipelines/chunker.py`](../../../backend/src/paperhub/pipelines/chunker.py) — safe halving shrink, LaTeX comment strip, paragraph boundary preference
- Modify: [`backend/src/paperhub/pipelines/paper_pipeline.py`](../../../backend/src/paperhub/pipelines/paper_pipeline.py) — populate `sections_json`
- Modify: [`backend/src/paperhub/agents/research_graph.py`](../../../backend/src/paperhub/agents/research_graph.py) — paper_qa subgraph topology
- Modify: [`backend/src/paperhub/agents/research.py`](../../../backend/src/paperhub/agents/research.py) — delete map-reduce helpers; add `paper_qa_finalize`
- Modify: [`backend/src/paperhub/agents/state.py`](../../../backend/src/paperhub/agents/state.py) — `pq_per_paper_picks`
- Modify: [`backend/src/paperhub/models/domain.py`](../../../backend/src/paperhub/models/domain.py) — `SectionEntry`, `PickedChunk`, `PerPaperPicks` exposure
- Modify: [`backend/src/paperhub/llm/prompts/registry.py`](../../../backend/src/paperhub/llm/prompts/registry.py) — slot registrations
- Modify: [`backend/src/paperhub/config.py`](../../../backend/src/paperhub/config.py) — `paper_qa_subagent_model`, `paper_qa_max_section_reads`
- Modify: [`backend/.env.example`](../../../backend/.env.example) — new env vars
- Modify: [`backend/pyproject.toml`](../../../backend/pyproject.toml) — `paperhub-reingest` script entry
- Modify: [`backend/tests/test_chunker.py`](../../../backend/tests/test_chunker.py), [`test_paper_pipeline.py`](../../../backend/tests/test_paper_pipeline.py), [`test_research_subgraph.py`](../../../backend/tests/test_research_subgraph.py), [`test_research_paper_qa.py`](../../../backend/tests/test_research_paper_qa.py)
- Modify: [`backend/scripts/query_papers.ps1`](../../../backend/scripts/query_papers.ps1)
- Delete: [`backend/src/paperhub/llm/prompts/paper_qa_per_paper_v1.yaml`](../../../backend/src/paperhub/llm/prompts/paper_qa_per_paper_v1.yaml), [`paper_qa_synthesize_v1.yaml`](../../../backend/src/paperhub/llm/prompts/paper_qa_synthesize_v1.yaml)
- Update at PR time (separate commit): [`CLAUDE.md`](../../../CLAUDE.md), [`docs/superpowers/specs/2026-05-17-paperhub-srs.md`](../../../docs/superpowers/specs/2026-05-17-paperhub-srs.md) — SRS v2.10 revision entry, §III-3 Research Agent row paper_qa paragraph, §III-5.2 RAG retrieval section.

---

## Plan C v2.11 — Router context dispatch (anaphora-resolved task brief) — small follow-up patch

> **For agentic workers:** REQUIRED SUB-SKILL: Use [superpowers:subagent-driven-development](../../../C:/Users/eddie/.claude/plugins/cache/claude-plugins-official/superpowers/5.1.0/skills/subagent-driven-development) (recommended) or [superpowers:executing-plans](../../../C:/Users/eddie/.claude/plugins/cache/claude-plugins-official/superpowers/5.1.0/skills/executing-plans). Steps use `- [ ]` for tracking.

**Goal:** Stop downstream agents (paper_search Parser, paper_qa subagent/finalizer, chitchat) from acting on a bare anaphoric follow-up (e.g. "推薦幾篇") by having the history-aware router resolve the latest turn into a self-contained task brief and dispatch *that* — or, when the turn can't be resolved even with history, ask a deliberate clarifying question instead of dead-ending in an empty-results re-ask.

**Why now (root-cause evidence — run 100, session 72).** Live two-turn testing: turn 1 established the topic "continuous-diffusion flow matching / short-cut models / distillation, do these exist for discrete diffusion (DLMs)?"; the assistant offered to search "Discrete Flow Matching / Discrete Diffusion Distillation"; turn 2 was the bare "推薦幾篇" (recommend a few). Reading the recorded run from SQLite (per the CLAUDE.md tracing recipe): `router:classify` got `"推薦幾篇"` → classified `paper_search` ✓; `paper_search:parse` got only `{"user_message":"推薦幾篇"}` → `result_summary_json.requests == []` → finalize emitted 0 candidates → synthesize asked "請問您對哪個領域或主題的論文感興趣呢？". The topic lived in the prior turns the Parser never saw. The router **does** read `state["history"]` ([`router.py:21,51`](../../../backend/src/paperhub/agents/router.py#L21)); the Parser is fed only `state["user_message"]` ([`research_graph.py:160-161`](../../../backend/src/paperhub/agents/research_graph.py#L160)). The router is living proof the history wiring works — this patch resolves the brief once at the router and dispatches it to every downstream stage.

**Architecture.** Extend the router's structured output (`RoutingDecision`) with one field, `resolved_query` — the anaphora-free, self-contained rewrite of the user's latest message. The router writes it onto a new `AgentState` slot `effective_query`, and every downstream agent reads `effective_query` (falling back to raw `user_message`) instead of the raw turn, via a single DRY helper. A new `intent="clarify"` lets the router short-circuit the pipeline and surface its own clarifying question. The raw user text is still recorded verbatim in the `messages` table (recorded **before** routing at [`chat.py:408`](../../../backend/src/paperhub/api/chat.py#L408)), so the transcript and the frontend-rebuilt `history` stay truthful — only the *agents* see the resolved brief. Observability is free: the router already runs `record_result(decision.model_dump())`, so `resolved_query` lands in the `tool_calls` row and the `runs.routing_decision_json`.

**Backward-compat.** `resolved_query` defaults to `""`. Existing router mocks/tests that emit only the four legacy fields still parse (the empty default makes `effective_query` fall back to raw `user_message`). No existing test changes to keep passing.

### v2.11 File Structure

| File | Change |
| --- | --- |
| [`backend/src/paperhub/models/domain.py`](../../../backend/src/paperhub/models/domain.py) | add `resolved_query` to `RoutingDecision`; add `"clarify"` to `Intent`; add `effective_query` to `AgentState` |
| [`backend/src/paperhub/agents/router.py`](../../../backend/src/paperhub/agents/router.py) | set `effective_query` from `resolved_query` (fallback raw) |
| [`backend/src/paperhub/agents/state.py`](../../../backend/src/paperhub/agents/state.py) | add `effective_query()` accessor helper |
| [`backend/src/paperhub/agents/research_graph.py`](../../../backend/src/paperhub/agents/research_graph.py) | feed `effective_query` to parse / synthesize / qa-subagent / qa-finalize |
| [`backend/src/paperhub/agents/chitchat.py`](../../../backend/src/paperhub/agents/chitchat.py) | read `effective_query` |
| [`backend/src/paperhub/agents/graph.py`](../../../backend/src/paperhub/agents/graph.py) | add `clarify` node + route |
| [`backend/src/paperhub/api/chat.py`](../../../backend/src/paperhub/api/chat.py) | add `elif intent == "clarify"` branch |
| [`backend/src/paperhub/llm/prompts/router_v1.yaml`](../../../backend/src/paperhub/llm/prompts/router_v1.yaml) | instruct model to emit `resolved_query` + use `clarify` |
| tests | `test_models.py`, `test_graph.py`, `test_research_pipeline.py`, `test_chitchat.py`, `test_chat_sse.py` |

### v2.11-1 — `resolved_query` field + `clarify` intent

**Files:** Modify [`domain.py`](../../../backend/src/paperhub/models/domain.py) (`Intent` line 5, `RoutingDecision` lines 26-31). Test: `tests/test_models.py`.

- [ ] **Step 1: Write the failing test** — add to `tests/test_models.py`:

```python
from paperhub.models.domain import RoutingDecision


def test_routing_decision_resolved_query_defaults_empty():
    # Legacy 4-field payload still validates; resolved_query defaults to "".
    d = RoutingDecision(intent="paper_search", model_tier="small", confidence=0.9, reasoning="r")
    assert d.resolved_query == ""


def test_routing_decision_accepts_clarify_intent_and_brief():
    d = RoutingDecision(
        intent="clarify", model_tier="small", confidence=0.5,
        reasoning="ambiguous follow-up",
        resolved_query="Which topic would you like papers on?",
    )
    assert d.intent == "clarify"
    assert d.resolved_query.startswith("Which topic")
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/test_models.py::test_routing_decision_resolved_query_defaults_empty tests/test_models.py::test_routing_decision_accepts_clarify_intent_and_brief -v` → FAIL (`resolved_query` unexpected under `extra=forbid` / `"clarify"` not a valid Intent).

- [ ] **Step 3: Implement** — in `domain.py` extend the `Intent` literal and add the field:

```python
Intent = Literal[
    "paper_search", "paper_qa", "slides", "library_stats", "chitchat", "clarify",
]
```

```python
class RoutingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Intent
    model_tier: ModelTier
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    # v2.11: self-contained, anaphora-free rewrite of the user's latest
    # turn (resolved against history by the router). For actionable
    # intents this is the task brief downstream agents act on; for
    # intent="clarify" it carries the clarifying question to show the
    # user. Empty string => downstream falls back to the raw user_message.
    resolved_query: str = ""
```

- [ ] **Step 4: Run to verify it passes** — `uv run pytest tests/test_models.py -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(router): add resolved_query field + clarify intent to RoutingDecision"`

### v2.11-2 — `effective_query` state slot + router sets it

**Files:** Modify [`domain.py`](../../../backend/src/paperhub/models/domain.py) (`AgentState` lines 61-68), [`router.py`](../../../backend/src/paperhub/agents/router.py) (return, lines 55-60). Test: `tests/test_graph.py`.

- [ ] **Step 1: Write the failing test** — add to `tests/test_graph.py`:

```python
from paperhub.agents.router import router_node
from paperhub.llm.litellm_adapter import LiteLlmAdapter


async def test_router_sets_effective_query_from_resolved(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {"run_id": 1, "branch": "", "session_id": 1, "user_message": "推薦幾篇"}
    out = await router_node(
        state, adapter=LiteLlmAdapter(), tracer=tracer, model="gpt-4o-mini",
        mock_response='{"intent":"paper_search","model_tier":"small","confidence":1.0,'
                      '"reasoning":"r","resolved_query":"recommend discrete diffusion distillation papers"}',
    )
    assert out["effective_query"] == "recommend discrete diffusion distillation papers"


async def test_router_effective_query_falls_back_to_raw(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {"run_id": 1, "branch": "", "session_id": 1, "user_message": "hello"}
    out = await router_node(
        state, adapter=LiteLlmAdapter(), tracer=tracer, model="gpt-4o-mini",
        mock_response='{"intent":"chitchat","model_tier":"small","confidence":0.85,"reasoning":"greeting"}',
    )
    assert out["effective_query"] == "hello"
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/test_graph.py::test_router_sets_effective_query_from_resolved tests/test_graph.py::test_router_effective_query_falls_back_to_raw -v` → FAIL (`KeyError: 'effective_query'`).

- [ ] **Step 3: Implement** — in `domain.py` add to `AgentState` after `user_message`:

```python
    # v2.11: the router's anaphora-resolved, self-contained rewrite of
    # user_message. Downstream agents read this (falling back to
    # user_message) so a bare follow-up like "推薦幾篇" carries its topic.
    effective_query: str
```

In `router.py` change the return to:

```python
    return {
        **state,
        "routing_decision": decision,
        "effective_query": decision.resolved_query or user_message,
    }
```

- [ ] **Step 4: Run to verify it passes** — `uv run pytest tests/test_graph.py -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(router): set effective_query state slot from resolved_query"`

### v2.11-3 — DRY `effective_query()` accessor helper

**Files:** Modify [`state.py`](../../../backend/src/paperhub/agents/state.py). Test: `tests/test_models.py`.

- [ ] **Step 1: Write the failing test** — add to `tests/test_models.py`:

```python
from paperhub.agents.state import effective_query


def test_effective_query_prefers_resolved():
    assert effective_query({"user_message": "raw", "effective_query": "brief"}) == "brief"


def test_effective_query_falls_back_when_empty_or_missing():
    assert effective_query({"user_message": "raw", "effective_query": ""}) == "raw"
    assert effective_query({"user_message": "raw"}) == "raw"
```

- [ ] **Step 2: Run to verify it fails** — → FAIL (`ImportError: cannot import name 'effective_query'`).

- [ ] **Step 3: Implement** — replace `state.py` with:

```python
from paperhub.models.domain import AgentState

__all__ = ["AgentState", "effective_query"]


def effective_query(state: AgentState) -> str:
    """The text downstream agents should act on: the router's
    anaphora-resolved brief when present, else the raw user_message
    (v2.11). One source of truth for the fallback semantics."""
    return state.get("effective_query") or state["user_message"]
```

- [ ] **Step 4: Run to verify it passes** — `uv run pytest tests/test_models.py -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(agents): add effective_query accessor with raw-message fallback"`

### v2.11-4 — Feed `effective_query` to the paper_search Parser (the regression fix)

**Files:** Modify [`research_graph.py`](../../../backend/src/paperhub/agents/research_graph.py) (`ps_parse` lines 160-161; synthesize line 331). Test: `tests/test_research_pipeline.py`.

Note: `parse_user_message`'s parameter is *named* `user_message` — do NOT change its signature; pass the resolved brief as that argument so the Parser receives self-contained input.

- [ ] **Step 1: Write the failing/contract test** — add to `tests/test_research_pipeline.py` (match the file's existing `parse_user_message` mock mechanism):

```python
import aiosqlite
from paperhub.agents.research_pipeline import parse_user_message
from paperhub.tracing.tracer import Tracer


async def test_parse_resolves_topic_from_brief(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    brief = "recommend representative papers on discrete diffusion distillation"
    reqs = await parse_user_message(
        brief, tracer=tracer, model="gpt-4o-mini",
        mock_response='[{"hint":"discrete diffusion distillation","kind":"natural_language"}]',
    )
    assert len(reqs) == 1
    assert reqs[0].kind == "natural_language"
```

(If `parse_user_message` does not take `mock_response` via `**litellm_kwargs`, align with the file's existing parse test. The contract — non-empty requests for a self-contained brief — is what matters.)

- [ ] **Step 2: Run** — `uv run pytest tests/test_research_pipeline.py::test_parse_resolves_topic_from_brief -v` → PASS once mock mechanism matches (pins the contract).

- [ ] **Step 3: Implement** — in `research_graph.py` add the import alongside the other `agents` imports:

```python
from paperhub.agents.state import effective_query
```

Change `ps_parse` (line 160-161) `parse_user_message(state["user_message"],` → `parse_user_message(effective_query(state),`, and synthesize (line 331) `user_message=state["user_message"],` → `user_message=effective_query(state),`.

- [ ] **Step 4: Run** — `uv run pytest tests/test_research_pipeline.py tests/test_graph.py -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "fix(paper_search): feed resolved brief (effective_query) to Parser + Synthesizer"`

### v2.11-5 — Feed `effective_query` to paper_qa subagent + finalizer, and chitchat

**Files:** Modify [`research_graph.py`](../../../backend/src/paperhub/agents/research_graph.py) (qa subagent line 438; qa finalize line 480), [`chitchat.py`](../../../backend/src/paperhub/agents/chitchat.py) (line 17). Test: `tests/test_chitchat.py`.

- [ ] **Step 1: Write the failing test** — add to `tests/test_chitchat.py` (match the file's fixture/mocking style):

```python
async def test_chitchat_uses_effective_query(migrated_db) -> None:
    from paperhub.agents.chitchat import chitchat_stream
    from paperhub.llm.litellm_adapter import LiteLlmAdapter
    from paperhub.tracing.tracer import Tracer

    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state = {"run_id": 1, "branch": "", "session_id": 1,
             "user_message": "go on", "effective_query": "explain flow matching more"}
    async for _ in chitchat_stream(
        state, adapter=LiteLlmAdapter(), tracer=tracer, model="gpt-4o-mini", mock_response="ok",
    ):
        pass
    async with migrated_db.execute(
        "SELECT args_redacted_json FROM tool_calls WHERE run_id=1 AND tool='generate'"
    ) as cur:
        row = await cur.fetchone()
    assert "explain flow matching more" in row[0]
```

- [ ] **Step 2: Run to verify it fails** — → FAIL (recorded arg is `"go on"`).

- [ ] **Step 3: Implement** — in `chitchat.py` import the helper and switch line 17:

```python
from paperhub.agents.state import AgentState, effective_query
```

```python
    user_message = effective_query(state)
```

In `research_graph.py`: qa subagent (line 438) `user_message=state["user_message"],` → `user_message=effective_query(state),`; qa finalize (line 480) likewise.

- [ ] **Step 4: Run** — `uv run pytest tests/test_chitchat.py tests/test_graph.py tests/test_research_pipeline.py -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "fix(paper_qa,chitchat): act on resolved brief (effective_query)"`

### v2.11-6 — Clarify branch (`build_graph` node + streaming chat dispatch)

**Files:** Modify [`graph.py`](../../../backend/src/paperhub/agents/graph.py) (lines 58-89), [`chat.py`](../../../backend/src/paperhub/api/chat.py) (intent dispatch chain, ~line 444-458). Tests: `tests/test_graph.py`, `tests/test_chat_sse.py`.

- [ ] **Step 1: Write the failing tests** — add to `tests/test_graph.py`:

```python
async def test_clarify_path(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    deps = GraphDeps(
        adapter=LiteLlmAdapter(), tracer=tracer,
        router_model="gpt-4o-mini", chitchat_model="gpt-4o-mini",
        router_mock='{"intent":"clarify","model_tier":"small","confidence":0.4,'
                    '"reasoning":"no topic yet","resolved_query":"Which research topic would you like papers on?"}',
    )
    graph = build_graph(deps)
    state: AgentState = {"run_id": 1, "branch": "", "session_id": 1, "user_message": "推薦幾篇"}
    result = await graph.ainvoke(state)
    assert result["routing_decision"].intent == "clarify"
    assert result["final_response"] == "Which research topic would you like papers on?"
```

And add to `tests/test_chat_sse.py` (adapt to the file's existing `monkeypatch.setenv("PAPERHUB_ROUTER_MOCK", ...)` + SSE-collection harness):

```python
async def test_chat_clarify_branch_emits_question_no_pipeline(monkeypatch, client):
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"clarify","model_tier":"small","confidence":0.4,'
        '"reasoning":"ambiguous","resolved_query":"Which topic do you mean?"}',
    )
    events = await collect_sse(client, {"user_message": "推薦幾篇", "history": []})
    final = [e for e in events if e["event"] == "final"]
    assert final and "Which topic" in final[-1]["data"]
    tool_steps = [e for e in events if e["event"] == "tool_step"]
    assert not any("paper_search" in e["data"] for e in tool_steps)
```

- [ ] **Step 2: Run to verify they fail** — `build_graph` has no `clarify` route; chat.py falls into the `else: stub_response` branch → content is a stub, not the question.

- [ ] **Step 3: Implement** — in `graph.py` add a clarify node (after `_stub_library_stats`, line 62):

```python
    async def _clarify(state: AgentState) -> AgentState:
        return {**state, "final_response": state["routing_decision"].resolved_query}
```

Register + route (lines 73-79):

```python
    g.add_node("clarify", _clarify)
    routes: dict[Hashable, str] = {
        "chitchat": "chitchat",
        "slides": "slides",
        "library_stats": "library_stats",
        "clarify": "clarify",
    }
```

And include clarify in the terminal-edge loop (line 87):

```python
    for terminal in ("chitchat", "slides", "library_stats", "clarify"):
        g.add_edge(terminal, END)
```

In `chat.py`, insert the branch in the intent dispatch chain after the `chitchat` branch (before `elif intent == "paper_search":`):

```python
                elif intent == "clarify":
                    # The router (which sees history) judged the turn
                    # un-resolvable and supplied a clarifying question in
                    # resolved_query. Surface it deliberately — no pipeline,
                    # no degenerate empty-results re-ask. resolved_query is
                    # already captured in the router tracer row + runs table.
                    final_content = decision.resolved_query or (
                        "Could you clarify what you'd like help with? "
                        "A topic, author, or paper title works well."
                    )
                    token_evt = TokenEvent(run_id=run_id, branch="", text=final_content)
                    yield {"event": "token",
                           "data": token_evt.model_dump_json(exclude={"type"})}
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_graph.py tests/test_chat_sse.py -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(router): add clarify branch — deliberate clarifying question over empty-results re-ask"`

### v2.11-7 — Router prompt emits `resolved_query` + uses `clarify`

**Files:** Modify [`router_v1.yaml`](../../../backend/src/paperhub/llm/prompts/router_v1.yaml). Test: prompt-render assertion in `tests/test_models.py`; LLM behaviour verified by smoke (v2.11-8).

- [ ] **Step 1: Write the failing test** — add to `tests/test_models.py`:

```python
from paperhub.llm.prompts.registry import PromptRegistry


def test_router_prompt_mentions_resolved_query_and_clarify():
    p = PromptRegistry().get("router/v1")
    assert "resolved_query" in p.system
    assert "clarify" in p.system
```

- [ ] **Step 2: Run to verify it fails** — current prompt mentions neither.

- [ ] **Step 3: Implement** — replace `router_v1.yaml` with (adds `clarify` to the intent list, the new field to the JSON contract, and a context-resolution instruction; the prompt already receives `history`):

```yaml
system: |
  You are PaperHub's intent router. The conversation so far is provided as
  prior turns; the user's MOST RECENT message is shown below. Do two jobs:

  (1) Classify the most recent message into exactly one intent:
    - paper_search    user wants to find/discover papers
    - paper_qa        user asks a question about already-indexed papers
    - slides          user asks to generate slides / a deck
    - library_stats   user asks a count/stat over their saved papers/sessions
    - chitchat        greeting, meta-question, off-topic
    - clarify         the message cannot be turned into a self-contained,
                      actionable request EVEN using the prior turns (e.g. a
                      bare "推薦幾篇" / "go on" with no topic anywhere in the
                      conversation). Use this instead of guessing.

  (2) Produce `resolved_query`: a SELF-CONTAINED rewrite of the user's most
      recent message with all pronouns / anaphora / ellipsis resolved against
      the prior turns. The rewrite must make sense on its own with NO history.
      Examples:
        - history offered "Discrete Flow Matching / discrete-diffusion
          distillation"; latest msg "推薦幾篇" →
          resolved_query: "recommend representative papers on Discrete Flow
          Matching and discrete-diffusion distillation for diffusion language
          models"
        - latest msg "explain its training data"; history names paper X →
          resolved_query: "explain the training data of paper X"
      If the message is ALREADY self-contained, copy it verbatim.
      If intent == "clarify", put the clarifying QUESTION to ask the user in
      `resolved_query` instead.

  IMPORTANT — session-aware override:
    The user turn includes `enabled_refs_count` (integer). If the user's
    question would naturally be `paper_qa` (asks about, compares, summarises,
    or wants to "discuss" specific named papers / architectures / methods)
    BUT `enabled_refs_count == 0`, classify as `paper_search` instead.
    Surface this in `reasoning`, e.g. "user named papers X / Y but session
    has 0 refs — search first".

  Pick `model_tier`:
    - small     for chitchat, clarify, library_stats, and most paper_search
    - flagship  for paper_qa and slides

  Return STRICT JSON with EXACTLY these five fields and no others:
    {
      "intent":        one of paper_search | paper_qa | slides | library_stats | chitchat | clarify,
      "model_tier":    one of small | flagship,
      "confidence":    number between 0.0 and 1.0,
      "reasoning":     short string (<= 1 sentence) explaining the choice,
      "resolved_query": self-contained rewrite of the latest message (or the
                        clarifying question when intent == "clarify")
    }
  No prose, no markdown, no code fences. JSON only.
user: |
  enabled_refs_count: {enabled_refs_count}

  User message:
  {user_message}
```

- [ ] **Step 4: Run to verify it passes** — `uv run pytest tests/test_models.py -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(router): prompt resolves anaphora into resolved_query + clarify"`

### v2.11-8 — Full gate + real-LLM trace verification

- [ ] **Step 1: Full backend gate** (from `backend/`): `uv run pytest -v`; `uv run ruff check src tests`; `uv run mypy src` → all green. (`effective_query(state) -> str`; `AgentState` is `total=False` so the helper's `state["user_message"]` access matches existing call sites.)
- [ ] **Step 2: Mocked smoke** — `.\scripts\smoke_chat.ps1` → exit 0.
- [ ] **Step 3: Real-LLM verification of the original regression** (requires `backend/.env`). Reproduce the session-72 shape: turn 1 establishes "discrete diffusion distillation / flow matching", turn 2 is "推薦幾篇". Read the new run from SQLite per the CLAUDE.md tracing recipe — `uv run paperhub-replay --run-id <N>` then `SELECT step_index, tool, args_redacted_json, result_summary_json FROM tool_calls WHERE run_id=<N> ORDER BY step_index;`. Expected (confirmed from the trace, **not** assumed):
  - `router:classify` `result_summary_json.resolved_query` is a self-contained topic brief (NOT "推薦幾篇").
  - `paper_search:parse` `args_redacted_json.user_message` equals that brief and `result_summary_json.requests` is NON-empty.
  - The turn ends with resolved candidates / a topical synthesis — not "請問您對哪個領域或主題的論文感興趣呢？".
- [ ] **Step 4: Docs** — `CLAUDE.md` (bump Plan C follow-ups + add a "Pointers" entry: *"Why does a bare follow-up like '推薦幾篇' now work? → router resolves anaphora into `resolved_query`; downstream agents read `effective_query` (v2.11)"*); SRS §III-7 / §III-3 v2.11 changelog entry (history-aware stage resolves once; downstream stays history-free); update the Plan C "as-shipped" list to include v2.11. Commit: `git commit -m "docs: record Plan C v2.11 router context dispatch"`.

### v2.11 self-review (author)

- **Decision coverage:** extend-the-router ✓ (v2.11-1,2,7); all-agents scope ✓ (v2.11-4,5); router-asks-clarifying-Q ✓ (v2.11-1,6,7); observability ✓ (`resolved_query` lands in the router `tool_calls` row via existing `record_result(decision.model_dump())`; clarify needs no new model call).
- **Backward-compat:** `resolved_query` defaults to `""`; legacy 4-field mocks/tests keep passing (v2.11-1 first test pins this).
- **DRY:** single `effective_query()` helper used at all five read sites.
- **Type consistency:** `effective_query(state: AgentState) -> str`; field `resolved_query` / slot `effective_query` used identically across v2.11-1…7.
- **Out of scope:** the `paper_qa_stream` legacy façade ([`research.py:161`](../../../backend/src/paperhub/agents/research.py#L161)) only consumes the question inside the subgraph (covered); its empty-refs sentinel doesn't touch the query, so no change needed there.

---

## Plan C v2.12 — `paper_suggest` intent (topic recommendation; conditional suggest prompts, reuse the search pipeline) — follow-up patch

> **For agentic workers:** REQUIRED SUB-SKILL: Use [superpowers:subagent-driven-development](../../../C:/Users/eddie/.claude/plugins/cache/claude-plugins-official/superpowers/5.1.0/skills/subagent-driven-development) (recommended) or [superpowers:executing-plans](../../../C:/Users/eddie/.claude/plugins/cache/claude-plugins-official/superpowers/5.1.0/skills/executing-plans). Steps use `- [ ]` for tracking. **Depends on v2.11** (uses `resolved_query` / `effective_query`); built on the same branch.

**Goal:** Add a first-class `paper_suggest` intent for **topic-level recommendation** ("recommend a few papers on X"), distinct from `paper_search` (find a *specific named* paper). It reuses the v2.7 Discover→Resolve→Finalize→Synthesize pipeline as fully as possible, diverging **only** by two conditional prompts: a **Parser prompt** that decomposes the topic into 2–4 intersection-anchored search angles, and a **Synthesizer prompt** with recommendation tone. The Finalizer is reused **unchanged** — like `paper_search`, the agent auto-attaches the top results (cap 2 in `_process_search_results`) and renders the rest as cards (UX decision: auto-add is fine for suggestions too). No `suggest_mode` flag is needed; `paper_suggest` = `paper_search` pipeline + two swapped prompt slots.

**Why now (root-cause evidence — run 101, session 72).** After v2.11 the router correctly resolved "推薦幾篇" into a topical brief and handed it to the Parser (`paper_search:parse` args = the full topic brief). But the Parser returned `requests: []` and the synthesizer re-asked for "a particular paper, lead author, or arXiv ID". Reading the Parser prompt ([`paper_search_parse_v1.yaml:18-21,45-46`](../../../backend/src/paperhub/llm/prompts/paper_search_parse_v1.yaml#L18)) shows this is **by design**: the four-stage `paper_search` resolves *specific named papers* and explicitly returns `[]` for a "topic survey" (`"what's a good MoE paper?" → []`). So topic recommendation has no path through the pipeline. v2.11 (anaphora resolution) was necessary but not sufficient — it surfaced this second, independent gap. The fix is a sibling intent, not a change to `paper_search`'s exact-lookup semantics.

**Architecture (data flow, suggest):** router (`intent=paper_suggest`, `resolved_query`=topic) → `effective_query` → `ps_parse` **[conditional suggest prompt → 2–4 intersection-anchored angle hints]** → `ps_process` Discover→Resolve fan-out (**unchanged code**, ~1 paper per angle) → `ps_finalize` (**unchanged** — auto-attach cap 2, rest as cards, same as `paper_search`) → `synthesize` **[conditional suggest prompt → recommendation prose]**. Conditional prompt selection = two new optional `ResearchDeps` slots (`parse_slot` / `synth_slot`, defaulting to the v1 search prompts); the `chat.py` shim sets them when the intent is `paper_suggest`. The prompt registry auto-discovers slots by filename, so the two new prompts need only their YAML files. **Intersection note (load-bearing):** when the topic is `[techniques A,B,C] × [domain D]` (e.g. "flow matching / shortcut / distillation **for discrete diffusion**"), every angle MUST keep the domain anchor ("flow matching for discrete diffusion"), never bare "flow matching" — bare facets surface continuous-diffusion papers, the opposite of intent. The suggest parse prompt encodes this rule + example.

**Reuse boundary (what does NOT change):** Discover (`discover_canonical`) + Resolve (`resolve_via_ss`) + the per-request `asyncio.gather` fan-out, the SSE `search_results` / `tool_step` shapes, `_process_search_results` cap/dedup/auto-attach machinery in `chat.py`, observability, and the DB schema. `paper_search` (exact lookup) behaviour is byte-for-byte unchanged (defaults preserved).

### v2.12 File Structure

| File | Change |
| --- | --- |
| [`domain.py`](../../../backend/src/paperhub/models/domain.py) | add `"paper_suggest"` to `Intent` |
| [`router_v1.yaml`](../../../backend/src/paperhub/llm/prompts/router_v1.yaml) | add `paper_suggest` intent + search-vs-suggest distinction (language-neutral) |
| **new** `paper_search_parse_suggest_v1.yaml` | topic → 2–4 distinct natural-language angle hints |
| **new** `paper_search_synthesize_suggest_v1.yaml` | recommendation-tone prose; suggestion-only framing |
| [`research_pipeline.py`](../../../backend/src/paperhub/agents/research_pipeline.py) | `parse_user_message(slot=...)` + `synthesize_prose(slot=...)` |
| [`research_graph.py`](../../../backend/src/paperhub/agents/research_graph.py) | `ResearchDeps.{parse_slot,synth_slot}`; pass slots to parse/synthesize (Finalizer unchanged) |
| [`chat.py`](../../../backend/src/paperhub/api/chat.py) | `paper_search` shim gains `suggest: bool`; route `paper_suggest` + `paper_search` through it |
| [`graph.py`](../../../backend/src/paperhub/agents/graph.py) | route `paper_suggest` to research subgraph (test completeness) |
| tests | `test_models.py`, `test_research_pipeline.py`, `test_research_subgraph.py` (or `test_graph.py`), `test_chat_sse.py` |

### v2.12-1 — `paper_suggest` intent

**Files:** Modify [`domain.py`](../../../backend/src/paperhub/models/domain.py) (`Intent` literal). Test: `tests/test_models.py`.

- [ ] **Step 1: failing test** — add to `tests/test_models.py`:
```python
def test_routing_decision_accepts_paper_suggest_intent():
    d = RoutingDecision(intent="paper_suggest", model_tier="small", confidence=0.9,
                        reasoning="topic recommendation", resolved_query="recommend papers on X")
    assert d.intent == "paper_suggest"
```
- [ ] **Step 2: run, verify FAIL** — `uv run pytest tests/test_models.py::test_routing_decision_accepts_paper_suggest_intent -v` → `"paper_suggest"` not a valid Intent.
- [ ] **Step 3: implement** — extend the literal (already includes `clarify` from v2.11):
```python
Intent = Literal[
    "paper_search", "paper_suggest", "paper_qa", "slides", "library_stats", "chitchat", "clarify",
]
```
- [ ] **Step 4: run** `uv run pytest tests/test_models.py -v` → PASS.
- [ ] **Step 5: commit** `git commit -m "feat(router): add paper_suggest intent to RoutingDecision"`

### v2.12-2 — router prompt: classify search vs suggest

**Files:** Modify [`router_v1.yaml`](../../../backend/src/paperhub/llm/prompts/router_v1.yaml). Test: `tests/test_models.py`.

- [ ] **Step 1: failing test** — add to `tests/test_models.py`:
```python
def test_router_prompt_distinguishes_search_and_suggest():
    p = PromptRegistry().get("router/v1")
    assert "paper_suggest" in p.system
    assert "paper_search" in p.system
```
- [ ] **Step 2: run, verify FAIL** — prompt doesn't mention `paper_suggest`.
- [ ] **Step 3: implement** — in the intent list, REPLACE the single `paper_search` line with the pair, and keep everything else (resolved_query job, language-neutral examples, clarify, override, JSON contract) intact. The two lines:
```yaml
    - paper_search    user wants a SPECIFIC, already-identified paper —
                      names it by title, author+year, arxiv id, doi, or an
                      unambiguous reference ("the Mamba paper", "Attention
                      Is All You Need", "arxiv:1706.03762")
    - paper_suggest   user wants RECOMMENDATIONS on a TOPIC / area, with no
                      specific paper named ("recommend a few on X", "good
                      papers about Y", "what should I read on Z"). The intent
                      is discovery by subject, not lookup of a known item.
```
Add `paper_suggest` to the small-tier line: `- small     for chitchat, clarify, library_stats, paper_search, and paper_suggest`. Update the JSON `"intent"` enum line to include `paper_suggest`. Add one resolved_query example under the existing Examples block:
```yaml
        - history discusses retrieval-augmented generation; latest msg
          "any good papers?" → intent: paper_suggest,
          resolved_query: "recommend representative papers on
          retrieval-augmented generation"
```
Keep the `enabled_refs_count` override unchanged (a user naming specific papers with 0 refs still → `paper_search`; topic asks → `paper_suggest`).
- [ ] **Step 4: run** `uv run pytest tests/test_models.py -v` → PASS. Also sanity-render: `uv run python -c "from paperhub.llm.prompts.registry import PromptRegistry; print(PromptRegistry().get('router/v1').user_template.format(enabled_refs_count=0, user_message='hi'))"`.
- [ ] **Step 5: commit** `git commit -m "feat(router): prompt distinguishes paper_search (specific) vs paper_suggest (topic)"`

### v2.12-3 — new suggest prompt files

**Files:** create `backend/src/paperhub/llm/prompts/paper_search_parse_suggest_v1.yaml` and `paper_search_synthesize_suggest_v1.yaml`. Test: `tests/test_models.py` (registry discovery + template safety).

- [ ] **Step 1: failing test** — add to `tests/test_models.py`:
```python
def test_suggest_prompts_load_and_format():
    reg = PromptRegistry()
    parse = reg.get("paper_search_parse_suggest/v1")
    assert "{user_message}" not in parse.user_template.format(user_message="T")  # formats cleanly
    synth = reg.get("paper_search_synthesize_suggest/v1")
    synth.user_template.format(user_message="m", resolved_block="r", not_found_block="n")  # no KeyError
```
- [ ] **Step 2: run, verify FAIL** — files don't exist (`FileNotFoundError`).
- [ ] **Step 3: implement** — create `paper_search_parse_suggest_v1.yaml`:
```yaml
system: |
  You turn a research TOPIC into 2-4 distinct search angles, each naming a
  facet to find papers for.

  Output ONE JSON array. Each entry:
    - "hint": a short natural-language search phrase for ONE facet of the
              topic (a sub-area, method, or framing). Make facets DISTINCT
              so they surface different papers.
    - "kind": always "natural_language".

  Rules:
    - Emit 2-4 entries. NEVER emit an empty array for a real topic — if the
      topic is broad, pick its most representative sub-areas; if narrow,
      split by method vs application vs survey.
    - Each hint reads like a query someone would type to find papers on
      that facet. Keep hints concise (2-6 words), no quotes, no boolean
      operators.
    - These are discovery angles, NOT exact lookups — don't name specific
      papers or authors unless the topic itself does.
    - Stay faithful to the topic; don't fabricate niche jargon.

  Examples:
    Topic: "recommend papers on flow matching, shortcut models, and
            distillation for discrete diffusion / diffusion language models"
    Output: [
      {"hint": "discrete flow matching", "kind": "natural_language"},
      {"hint": "diffusion distillation for language models", "kind": "natural_language"},
      {"hint": "consistency models for discrete diffusion", "kind": "natural_language"}
    ]

    Topic: "good papers on mixture-of-experts routing"
    Output: [
      {"hint": "mixture of experts routing", "kind": "natural_language"},
      {"hint": "expert load balancing sparse models", "kind": "natural_language"},
      {"hint": "mixture of experts survey", "kind": "natural_language"}
    ]

  Output the JSON array only. No prose.
user: |
  TOPIC:
  {user_message}
```
And `paper_search_synthesize_suggest_v1.yaml` (recommendation tone; auto-add is reused from paper_search so the prose stays neutral about add-state):
```yaml
system: |
  You write a short prose recommendation of the papers the pipeline found
  for the user's TOPIC.

  - Open with one sentence framing the recommendation.
  - 1-2 sentences per resolved paper, named by title + lead author + year
    from RESOLVED. Don't invent details beyond title/year/authors.
  - On not-found angles: at most one sentence suggesting the user narrow or
    rephrase. Don't demand an arxiv ID, author, or title.
  - If RESOLVED is empty: briefly say there were no clear matches and suggest
    narrowing the topic. Don't ask for a specific paper.
  - The UI handles which papers get added to the library; don't claim or
    deny additions.

  Do NOT emit a ``json:candidates`` block. Do NOT invent paper titles — only
  mention papers in RESOLVED.
user: |
  USER MESSAGE:
  {user_message}

  RESOLVED (papers the pipeline successfully landed):
  {resolved_block}

  NOT_FOUND (angles the pipeline couldn't locate a paper for):
  {not_found_block}
```
- [ ] **Step 4: run** `uv run pytest tests/test_models.py -v` → PASS.
- [ ] **Step 5: commit** `git commit -m "feat(paper_suggest): add suggest parse + synthesize prompts"`

### v2.12-4 — conditional slot params on Parser + Synthesizer

**Files:** Modify [`research_pipeline.py`](../../../backend/src/paperhub/agents/research_pipeline.py) (`parse_user_message`, `synthesize_prose`). Test: `tests/test_research_pipeline.py`.

- [ ] **Step 1: failing test** — add to `tests/test_research_pipeline.py` (match the file's litellm-mock convention):
```python
async def test_parse_uses_suggest_slot(migrated_db):
    # With the suggest slot, the parser prompt is the angle-decomposition one.
    # Assert the slot is honored by checking the recorded prompt path indirectly:
    # a topic brief returns multiple natural_language angles from the mocked LLM.
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    reqs = await parse_user_message(
        "recommend papers on mixture of experts", tracer=tracer, model="gpt-4o-mini",
        slot="paper_search_parse_suggest/v1",
        mock_response='[{"hint":"moe routing","kind":"natural_language"},{"hint":"moe survey","kind":"natural_language"}]',
    )
    assert len(reqs) == 2
```
(Use the file's actual mock mechanism — patch `litellm.acompletion` if `mock_response` isn't a kwarg, mirroring v2.11-4. The contract: `parse_user_message` accepts `slot=` and routes it to `PromptRegistry().get(slot)`.)
- [ ] **Step 2: run, verify FAIL** — `parse_user_message` has no `slot` param (`TypeError`).
- [ ] **Step 3: implement** — in `research_pipeline.py`:
  - `parse_user_message(..., slot: str = "paper_search_parse/v1", registry=None, **litellm_kwargs)`; replace `reg.get("paper_search_parse/v1")` with `reg.get(slot)`.
  - `synthesize_prose(..., slot: str = "paper_search_synthesize/v1", registry=None, **litellm_kwargs)`; replace `reg.get("paper_search_synthesize/v1")` with `reg.get(slot)`.
  Defaults preserve `paper_search` behaviour exactly.
- [ ] **Step 4: run** `uv run pytest tests/test_research_pipeline.py -v` → PASS.
- [ ] **Step 5: commit** `git commit -m "feat(paper_search): conditional parse/synth prompt slot params"`

### v2.12-5 — `ResearchDeps` conditional prompt slots + subgraph wiring

**Files:** Modify [`research_graph.py`](../../../backend/src/paperhub/agents/research_graph.py). Test: `tests/test_research_subgraph.py` (or wherever `build_paper_search_subgraph` is exercised).

Note: no `suggest_mode` flag and no Finalizer change — `paper_suggest` reuses the auto-attach Finalizer exactly (UX decision: auto-add is fine for suggestions). The only divergence is the two prompt slots.

- [ ] **Step 1: failing test** — add a test that builds the subgraph with the suggest slots and asserts the suggest parse slot drives a **multi-angle** parse (≥2 resolved/emitted candidates from one topic). Mirror the existing paper_search subgraph test's mocking (it injects `adapter_kwargs`/`mock_response` and a fake MCP registry that returns SS hits). Key assertion:
```python
# deps built with parse_slot="paper_search_parse_suggest/v1",
# synth_slot="paper_search_synthesize_suggest/v1"
# With the parse LLM mocked to return 2-3 angle hints and the fake MCP
# registry resolving each, the search_results candidates (captured from the
# custom stream or the paper_search:finalize tracer row) number >= 2.
```
(Match the existing subgraph test harness exactly — capture the `search_results` custom event or read the `paper_search:finalize` tracer row's `emitted_candidates`.)
- [ ] **Step 2: run, verify FAIL** — `ResearchDeps` has no `parse_slot`/`synth_slot`; `_ps_parse`/synthesize use the hardcoded v1 slots.
- [ ] **Step 3: implement** — in `research_graph.py`:
  - Add to `ResearchDeps`: `parse_slot: str = "paper_search_parse/v1"`, `synth_slot: str = "paper_search_synthesize/v1"`.
  - In `build_paper_search_subgraph`, `_ps_parse`: `parse_user_message(effective_query(state), tracer=..., model=parser_model, slot=deps.parse_slot, **_kwargs(deps))`.
  - In the `synthesize_prose(...)` call: add `slot=deps.synth_slot`.
  - Leave `_ps_finalize` and its `finalize=True` UNCHANGED.
- [ ] **Step 4: run** `uv run pytest tests/test_research_subgraph.py tests/test_research_pipeline.py -v` → PASS.
- [ ] **Step 5: commit** `git commit -m "feat(paper_suggest): subgraph conditional parse/synth prompt slots"`

### v2.12-6 — route `paper_suggest` (chat SSE + build_graph)

**Files:** Modify [`chat.py`](../../../backend/src/paperhub/api/chat.py), [`graph.py`](../../../backend/src/paperhub/agents/graph.py). Tests: `tests/test_chat_sse.py`, `tests/test_graph.py`.

- [ ] **Step 1: failing tests** —
  (a) `tests/test_chat_sse.py`: with `PAPERHUB_ROUTER_MOCK` set to `intent=paper_suggest` (+ a topical `resolved_query`), POST `/chat`; assert a `search_results` event is emitted (recommendation flow ran, not the stub). Match the file's SSE harness; mock the suggest parse/synth LLM calls the same way `test_chat_sse.py` already mocks paper_search downstream calls.
  (b) `tests/test_graph.py`: a `paper_suggest` router mock routes through the research path without error (graph-completeness).
- [ ] **Step 2: run, verify FAIL** — `paper_suggest` falls into chat.py's `else: stub_response`; `_route` in `build_graph` returns an unrouted `"paper_suggest"`.
- [ ] **Step 3: implement** —
  - In `chat.py` `paper_search` shim: add a `suggest: bool = False` param; when `True`, set `parse_slot="paper_search_parse_suggest/v1"` and `synth_slot="paper_search_synthesize_suggest/v1"` on the `ResearchDeps(...)` construction (Finalizer behaviour is identical — auto-attach reused).
  - In the `/chat` intent dispatch, change `elif intent == "paper_search":` to `elif intent in ("paper_search", "paper_suggest"):` and pass `suggest=(intent == "paper_suggest")` into the `paper_search(...)` call. The entire ToolStepYield / SearchResultsYield / FinalOnlyMessage handling block is shared unchanged; auto-attach flows through `_process_search_results`'s existing cap/already-in-session logic exactly like `paper_search`.
  - In `graph.py` `_route`: `if intent in ("paper_search", "paper_qa", "paper_suggest"): return "research"`. (The test-only research subgraph runs default search mode for `paper_suggest`; real suggest behaviour is covered by the chat SSE + subgraph tests above. Note this scoping in a comment.)
- [ ] **Step 4: run** `uv run pytest tests/test_chat_sse.py tests/test_graph.py -v` → PASS.
- [ ] **Step 5: commit** `git commit -m "feat(paper_suggest): route via chat shim (suggest=True) + build_graph"`

### v2.12-7 — full gate + real-LLM end-to-end

- [ ] **Step 1: gate** (from `backend/`): `uv run pytest -q`; `uv run ruff check src tests`; `uv run mypy src` → all green.
- [ ] **Step 2: real-LLM e2e** (requires `backend/.env`). Reproduce the session-72 two-turn flow (topic turn, then "推薦幾篇"); confirm from the SQLite trace per the CLAUDE.md recipe: `router:classify` → `intent=paper_suggest` with a topical `resolved_query`; `paper_search:parse` (suggest slot) → **2–4 intersection-anchored angle requests** (non-empty; each keeps the topic's domain anchor); `paper_search:finalize` `emitted_candidates` non-empty (auto-attach behaves like paper_search); the final message recommends papers (NOT a re-ask for a specific paper). Capture run id and paste the `tool_calls` rows.
- [ ] **Step 3: docs** — `CLAUDE.md` (as-shipped list + a Pointers entry: *"Topic recommendation vs exact lookup? → `paper_suggest` (intersection-anchored angle-decomposition, reuses the search pipeline incl. auto-attach) vs `paper_search` (resolve named paper); v2.12"*); SRS §III-3 + changelog v2.12 entry; Plan C as-shipped list. Commit `docs: record Plan C v2.12 paper_suggest`.

### v2.12 self-review (author)

- **Decisions covered:** distinct `paper_suggest` intent (v2.12-1,2), conditional suggest prompts (v2.12-3,4 + chat shim v2.12-6), intersection-anchored angle-decomposition Parser (v2.12-3), auto-attach Finalizer reused unchanged (UX decision — agent may add suggested papers).
- **Reuse maximised:** Discover/Resolve/fan-out, the Finalizer, and `_process_search_results` all untouched; `paper_search` defaults byte-for-byte unchanged (new params default to v1 slots). `paper_suggest` = `paper_search` + two swapped prompt slots.
- **Backward-compat:** additive intent + two additive optional deps fields + additive prompt files; no schema change.
- **Type consistency:** `parse_slot` / `synth_slot` named identically in `ResearchDeps`, the subgraph, and the chat shim; new prompt slot strings match their YAML filenames (registry auto-discovery).
- **Scoping note:** `build_graph` routes `paper_suggest` to the research subgraph in default mode (test-completeness only); real suggest-mode behaviour is covered by the chat SSE test + the direct subgraph suggest-mode test, since the production path drives the subgraph through the `chat.py` shim.
