# Remove Dead Dense-Vector RAG Stack — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the unused dense-vector RAG stack (Chroma vector store, SentenceTransformer embedder, CrossEncoder reranker, the `Retriever`, and the sibling `paperhub-modelserver` process) and the deps it drags in (`chromadb`, `sentence-transformers`, `torch`), resolving all 3 backend Dependabot alerts at the root and shrinking the install — without changing any user-visible behaviour.

**Architecture:** PaperHub's `paper_qa` went agentic-hierarchical in v2.10: it navigates each paper's section TOC over the SQLite `chunks` table (`read_section`), and the Citation Canvas resolves clicks via `GET /chunks/{id}` (SQLite). An audit (see SRS v2.27 + §III-5.4) proved `Retriever.retrieve()` has **zero callers**, `chroma.search` has exactly one caller (the dead `Retriever`), the embedder writes vectors nothing reads, and `/rerank` is never hit. The live chunk store is SQLite; Chroma/embedder/reranker/modelserver are dead weight. This plan unwires every consumer, deletes the modules, drops the deps, and removes the process orchestration — keeping the SQLite `chunks` writes (`INSERT INTO chunks`) untouched.

**Tech Stack:** Python 3.11, FastAPI, `uv` (never `pip`/system python), pytest/ruff/mypy. PowerShell on Windows. Frontend is **unaffected** (no frontend code references `/embed`, `/rerank`, or Chroma — the Canvas uses `/chunks`).

**Authoritative spec:** [docs/superpowers/specs/2026-05-17-paperhub-srs.md](../specs/2026-05-17-paperhub-srs.md) v2.27 (§II-3, §III-5.1/5.2/5.4, §III-6, §III-7).

**Ordering invariant (load-bearing):** the suite must stay green after every task. Therefore **all consumers are unwired before a module is deleted**, and each task updates its own tests. Tasks 1–3 unwire; Task 4 deletes the now-orphaned modules; Tasks 5–7 are deps/config/orchestration/docs; Task 8 is the real-API gate.

**Iron rule for every task:** NEVER touch the SQLite `chunks` table, `INSERT INTO chunks`, `api/chunks.py`, the `paper_qa` subagent (`agents/paper_qa_subagent.py`), or `library_stats`. Those are the live path. If a step seems to require editing them, STOP — the step is wrong.

**Commands** (run from `backend/` unless noted): `uv run pytest`, `uv run ruff check src tests`, `uv run mypy src`. Use `rg` (ripgrep) for grep-verification.

---

### Task 1: Remove the dead query path (retriever + reranker) and unwire its consumers

The `Retriever` and `Reranker` are never invoked — `chat.py` constructs a `Retriever` and threads it into `paper_qa_stream`/`report_stream`, where it is only ever an unused `Any`-typed parameter. The `app.py` lifespan also pre-warms them. Remove all of that, then delete the two modules and their tests.

**Files:**
- Modify: `backend/src/paperhub/api/chat.py` (remove `from paperhub.rag.retriever import Retriever`; the two `Retriever(...)` constructions in the `paper_qa` and `slides` branches; the `retriever=`/`slides_retriever=` kwargs passed to `paper_qa_stream` and `report_stream`)
- Modify: `backend/src/paperhub/agents/research.py` (drop the `retriever: Any` parameter from the `paper_qa_stream` signature + any pass-through)
- Modify: `backend/src/paperhub/agents/research_graph.py` (remove `from paperhub.rag.retriever import Retriever` and the `retriever: Retriever` field on the deps dataclass + any constructor/use)
- Modify: `backend/src/paperhub/agents/report_graph.py` (drop the `retriever: Any` parameter from `report_stream` + any pass-through)
- Modify: `backend/src/paperhub/app.py` (delete the `_prewarm_models` function and the `asyncio.create_task(_prewarm_models(...))` call + the `from paperhub.pipelines.embedder import get_embedder` / `from paperhub.rag.reranker import get_reranker` imports inside it)
- Delete: `backend/src/paperhub/rag/retriever.py`, `backend/src/paperhub/rag/reranker.py`
- Delete: `backend/tests/test_retriever.py`, `backend/tests/test_reranker.py`
- Modify: `backend/tests/test_chat_sse.py` (remove `from paperhub.rag.retriever import RetrievedChunk` + any fixture using it), `backend/tests/test_research_paper_qa.py`, `backend/tests/test_research_pipeline.py`, `backend/tests/test_research_subgraph.py` (remove `Retriever` imports + constructions; drop the `retriever=` kwarg from any `paper_qa_stream`/`report_stream` calls)

- [ ] **Step 1: Delete the two source modules and their unit tests**

```bash
cd backend
git rm src/paperhub/rag/retriever.py src/paperhub/rag/reranker.py tests/test_retriever.py tests/test_reranker.py
```

- [ ] **Step 2: Remove the `Retriever` wiring from `chat.py`**

Open `src/paperhub/api/chat.py`. Delete the line `from paperhub.rag.retriever import Retriever`. In the `paper_qa` branch, delete `retriever = Retriever(chroma=get_chroma(request, settings))` and remove the `retriever=retriever` kwarg from the `paper_qa_stream(...)` call. In the `slides` branch, delete `slides_retriever = Retriever(chroma=get_chroma(request, settings))` and remove the `retriever=slides_retriever` kwarg from the `report_stream(...)` call. (Leave `get_chroma`/line-606 alone — that is Task 3.)

- [ ] **Step 3: Drop the unused `retriever` parameter from the agent stream signatures**

In `src/paperhub/agents/research.py`, remove the `retriever: Any,` parameter from `paper_qa_stream(...)` (and any `retriever` forwarded into the subgraph deps). In `src/paperhub/agents/report_graph.py`, remove `retriever: Any` from `report_stream(...)`. In `src/paperhub/agents/research_graph.py`, remove `from paperhub.rag.retriever import Retriever` and the `retriever: Retriever` deps field + any place it is constructed/assigned. These were never read (verified: `rg '\.retrieve\(' src` returns nothing).

- [ ] **Step 4: Remove the lifespan pre-warm in `app.py`**

In `src/paperhub/app.py`, delete the `_prewarm_models` coroutine (it imports `get_embedder`/`get_reranker` and calls `.embed`/`.rerank`) and the `asyncio.create_task(_prewarm_models(...))` line in the lifespan. Leave the modelserver `ensure_running` spawn + ChromaStore state for Task 3 (so this task stays focused on the query path).

- [ ] **Step 5: Fix the affected tests**

In `tests/test_chat_sse.py` remove `from paperhub.rag.retriever import RetrievedChunk` and any fixture/assert referencing `RetrievedChunk`. In `tests/test_research_paper_qa.py`, `tests/test_research_pipeline.py`, `tests/test_research_subgraph.py`, remove `Retriever` imports/constructions and drop any `retriever=` kwarg from `paper_qa_stream`/`report_stream` calls.

- [ ] **Step 6: Verify no references remain and gates pass**

```bash
cd backend
rg "retriever|Retriever|reranker|Reranker|RetrievedChunk|rag.retriever|rag.reranker" src tests
```
Expected: NO matches (the only acceptable leftover is a comment; if so, remove it).
```bash
uv run pytest -q
uv run ruff check src tests
uv run mypy src
```
Expected: all green (test count drops by the deleted files; no import errors).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(rag): remove dead Retriever + Reranker query path

Retriever.retrieve() had zero callers after the v2.10 agentic paper_qa
redesign; chat.py wired it in only as an unused param. Remove the two
modules, the lifespan pre-warm, and all retriever wiring. No behaviour
change.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Stop computing embeddings + writing Chroma at ingest (keep SQLite chunks)

The Paper Pipeline and the Marker upgrade worker embed chunk texts and `chroma.add_chunks(...)` them, but nothing ever queries those vectors. Remove the embedder + Chroma writes while **keeping every `INSERT INTO chunks` (SQLite)** — that is the live chunk store the subagent + Canvas read.

**Files:**
- Modify: `backend/src/paperhub/pipelines/paper_pipeline.py` (remove `from paperhub.pipelines.embedder import Embedder, get_embedder` and `from paperhub.rag.chroma import ChromaStore`; drop the `embedder`/`chroma` constructor params + the `self._embedder`/`self._chroma` fields; delete the 5 `embeddings = self._embedder.embed(texts)` lines and the 5 `self._chroma.add_chunks(...)` calls and the 1 `self._chroma.delete_paper(...)` call — **keep the surrounding `INSERT INTO chunks` SQL**)
- Modify: `backend/src/paperhub/pipelines/marker_worker.py` (remove `from paperhub.rag.chroma import ChromaStore`; drop the `chroma` parameter; remove the re-embed + `add_chunks` on the re-chunk path — **keep the SQLite re-chunk write**)
- Modify: `backend/src/paperhub/cli/reingest.py` (remove `from paperhub.pipelines.embedder import get_embedder` and `from paperhub.rag.chroma import ChromaStore`; delete `embedder = get_embedder()`, `embeddings = embedder.embed(...)`, `chroma = ChromaStore(...)`, `chroma.delete_paper(...)`, `chroma.add_chunks(...)` — **keep the SQLite chunk re-insert**)
- Modify: `backend/tests/test_paper_pipeline.py`, `backend/tests/test_marker_worker.py`, `backend/tests/test_reingest_cli.py` (drop the `ChromaStore`/embedder fixtures + any assertion that vectors were written; keep/repoint assertions to the SQLite `chunks` rows)

- [ ] **Step 1: Read the pipeline to find the exact embed+add_chunks blocks**

```bash
cd backend
rg -n "embed\(|add_chunks|delete_paper|ChromaStore|get_embedder|INSERT INTO chunks" src/paperhub/pipelines/paper_pipeline.py
```
For each of the 5 ingest branches, the pattern is: build `chunks` → `INSERT INTO chunks` (KEEP) → `embeddings = self._embedder.embed(texts)` (DELETE) → `self._chroma.add_chunks(...)` (DELETE). Make the deletions; keep the SQL insert and its `chunks` list construction.

- [ ] **Step 2: Drop embedder/chroma from `PaperPipeline.__init__`**

In `src/paperhub/pipelines/paper_pipeline.py`, remove the `embedder` and `chroma` parameters from `__init__` and the `self._embedder = embedder or get_embedder()` / `self._chroma = chroma ...` assignments, and the two imports. Remove the standalone `self._chroma.delete_paper(paper_content_id)` line (paper re-ingest path) — chunk rows are replaced by the SQL path directly.

- [ ] **Step 3: Update `marker_worker.py`**

Remove the `ChromaStore` import and the `chroma` parameter from the worker entrypoint. On the re-chunk path keep the SQLite write; remove the re-embed + `add_chunks`. (It also constructs/receives the pipeline — update that call to not pass `chroma`.)

- [ ] **Step 4: Update `cli/reingest.py`**

Remove the embedder + ChromaStore imports, the `embedder`/`chroma` locals, the `.embed`/`.add_chunks`/`.delete_paper` calls, and the `chroma=` argument wherever it builds the pipeline. Keep the re-chunk → `INSERT INTO chunks` logic. `paperhub-reingest` remains useful (it re-chunks for the v2.10 `sections_json` migration).

- [ ] **Step 5: Update the three test files**

In `tests/test_paper_pipeline.py`, `tests/test_marker_worker.py`, `tests/test_reingest_cli.py`: remove the `ChromaStore` import + any fake embedder/chroma fixtures and the `chroma=`/`embedder=` constructor args; delete assertions that vectors/`add_chunks` happened; ensure each test asserts the **SQLite `chunks` rows** exist (query `SELECT count(*) FROM chunks WHERE paper_content_id=?`). If a test only existed to check embedding, delete that test.

- [ ] **Step 6: Verify SQLite writes survive + gates pass**

```bash
cd backend
rg -n "INSERT INTO chunks" src/paperhub/pipelines/paper_pipeline.py src/paperhub/cli/reingest.py
```
Expected: the inserts are STILL present (do not let them be removed).
```bash
rg "embed|add_chunks|ChromaStore|get_embedder" src/paperhub/pipelines/paper_pipeline.py src/paperhub/pipelines/marker_worker.py src/paperhub/cli/reingest.py
```
Expected: NO matches.
```bash
uv run pytest -q && uv run ruff check src tests && uv run mypy src
```
Expected: green.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(pipeline): stop embedding + Chroma writes at ingest

Keep the SQLite chunks INSERTs (the live store paper_qa + Citation
Canvas read); drop the embedder + chroma.add_chunks/delete_paper that
wrote vectors nothing queried. Ingest no longer touches the GPU.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Remove Chroma + modelserver from the API layer and app lifespan

After Tasks 1–2 the only remaining consumers are: the `get_chroma` dependency, its call sites in `chat.py`/`papers.py`, the paper-delete endpoint's best-effort `chroma.delete_paper`, the `ChromaStore` on `app.state`, the marker-worker `chroma=` arg in the lifespan, and the modelserver `ensure_running` spawn.

**Files:**
- Modify: `backend/src/paperhub/api/deps.py` (delete `get_chroma` + `from paperhub.rag.chroma import ChromaStore`)
- Modify: `backend/src/paperhub/api/chat.py` (delete `from paperhub.api.deps import get_chroma`; remove the `chroma=get_chroma(request, settings)` argument at the inline-ingest call (~line 606) — the PaperPipeline no longer takes `chroma` after Task 2)
- Modify: `backend/src/paperhub/api/papers.py` (change `from paperhub.api.deps import get_chroma, get_llm` → `from paperhub.api.deps import get_llm`; remove the two `chroma=get_chroma(request, settings)` pipeline args (~155, ~253); delete the best-effort Chroma block in the delete endpoint (~lines 618–626) — chunks already cascade via FK `ON DELETE CASCADE` when `paper_content` is deleted)
- Modify: `backend/src/paperhub/app.py` (delete `from paperhub.modelserver.spawn import ensure_running as _modelserver_ensure_running` + the `ensure_running(...)` call in the lifespan; delete `from paperhub.rag.chroma import ChromaStore`, `app.state.chroma = ChromaStore(settings.chroma_dir)`, and the `chroma=app.state.chroma` argument to the marker-worker construction; drop the modelserver host:port line from the boot banner)
- Modify: `backend/tests/test_papers_api.py` (drop any Chroma fixture/assert in the delete-paper test; assert chunks are gone after delete via SQLite, relying on the FK cascade)

- [ ] **Step 1: Delete `get_chroma`**

In `src/paperhub/api/deps.py` remove the `get_chroma` function and `from paperhub.rag.chroma import ChromaStore`.

- [ ] **Step 2: Update `chat.py` and `papers.py` call sites**

Remove the `get_chroma` import from both. In `chat.py`, drop the `chroma=get_chroma(...)` kwarg at the inline-ingest pipeline construction (~606). In `papers.py`, drop the two `chroma=get_chroma(...)` kwargs (~155, ~253) and delete the whole `try: chroma = get_chroma(...); chroma.delete_paper(...) except ...` block (~618–626).

- [ ] **Step 3: Update `app.py` lifespan**

Delete the modelserver import + `ensure_running(...)` call, the `ChromaStore` import + `app.state.chroma = ...` assignment, and the `chroma=app.state.chroma` arg where the marker worker / pipeline is built. Remove the `model_server_host:port` field from the boot banner string.

- [ ] **Step 4: Update `test_papers_api.py`**

In the delete-paper test, remove Chroma stubs/asserts. Assert the FK cascade: after `DELETE /papers/...` (force) the `chunks` rows for that `paper_content_id` are gone (`SELECT count(*) FROM chunks WHERE paper_content_id=?` → 0).

- [ ] **Step 5: Verify the whole `rag`/`modelserver`/`embedder` import surface is gone from `src`**

```bash
cd backend
rg "rag\.chroma|rag\.retriever|rag\.reranker|get_chroma|ChromaStore|modelserver|pipelines.embedder|pipelines._device|chroma" src
```
Expected: NO matches anywhere under `src`.
```bash
uv run pytest -q && uv run ruff check src tests && uv run mypy src
```
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(api): drop get_chroma + modelserver spawn from API/lifespan

Paper delete relies on the chunks FK ON DELETE CASCADE; nothing else
needs the vector store or the model server. No behaviour change.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Delete the orphaned modules and clean up the test harness

Nothing under `src` imports these anymore (verified in Task 3). Delete them and the remaining dead tests, and remove the model-singleton reset from `conftest.py`.

**Files:**
- Delete: `backend/src/paperhub/rag/chroma.py`, `backend/src/paperhub/pipelines/embedder.py`, `backend/src/paperhub/pipelines/_device.py`, and the whole `backend/src/paperhub/modelserver/` package (`__init__.py`, `__main__.py`, `server.py`, `spawn.py`)
- Delete: `backend/tests/test_chroma.py`, `backend/tests/test_embedder.py`, `backend/tests/test_modelserver.py`
- Modify: `backend/tests/conftest.py` (delete the `_reset_model_singletons` fixture + its `from paperhub.pipelines import embedder as _embedder_mod` / `from paperhub.rag import reranker as _reranker_mod` imports and `reset_singleton()` calls; remove the `PAPERHUB_INPROCESS_MODELS=1` env set, which no longer means anything)
- Check: `backend/src/paperhub/rag/__init__.py` — if it re-exports `ChromaStore`/`Retriever`/`Reranker`, remove those exports; if `rag/` is now empty of modules, decide whether to keep the package (keep `__init__.py` only if something still lives in `rag/`; otherwise `git rm` the now-empty package)

- [ ] **Step 1: Delete the modules and dead tests**

```bash
cd backend
git rm src/paperhub/rag/chroma.py src/paperhub/pipelines/embedder.py src/paperhub/pipelines/_device.py
git rm -r src/paperhub/modelserver
git rm tests/test_chroma.py tests/test_embedder.py tests/test_modelserver.py
```

- [ ] **Step 2: Clean `conftest.py`**

Remove the embedder/reranker imports, the `_reset_model_singletons` fixture, and the `os.environ["PAPERHUB_INPROCESS_MODELS"] = "1"` line. Leave all other fixtures intact.

- [ ] **Step 3: Handle the `rag` package**

```bash
rg -n "ChromaStore|Retriever|Reranker|ChunkSearchResult|RerankResult|RetrievedChunk" src/paperhub/rag/__init__.py
```
Remove any such re-exports. If `src/paperhub/rag/` contains only `__init__.py` now and nothing imports `paperhub.rag`, `git rm -r src/paperhub/rag`. Otherwise keep the trimmed `__init__.py`.

- [ ] **Step 4: Verify nothing imports the deleted modules and gates pass**

```bash
cd backend
rg "modelserver|pipelines.embedder|pipelines._device|rag.chroma|PAPERHUB_INPROCESS_MODELS" src tests
```
Expected: NO matches.
```bash
uv run pytest -q && uv run ruff check src tests && uv run mypy src
```
Expected: green. Record the new backend test count for the release.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: delete orphaned chroma/embedder/_device/modelserver modules

All consumers unwired in prior tasks. Drop the dead modules, their unit
tests, and the conftest model-singleton reset.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Drop the dependencies, config keys, and regenerate the lockfile

With the code gone, remove `chromadb`, `sentence-transformers` (→ `torch` drops transitively), the GPU torch extras + index/sources/conflicts routing, the chromadb mypy override, the `paperhub-modelserver` script entry, and the now-dead `Settings` fields + env vars.

**Files:**
- Modify: `backend/pyproject.toml` (remove `"chromadb>=1.5.9"` and `"sentence-transformers>=5.5.0"` from `dependencies`; delete the entire `[project.optional-dependencies]` block (`cu124`/`cu126`/`cu130`); delete all three `[[tool.uv.index]]` pytorch blocks; delete the `[tool.uv.sources]` `torch = [...]` routing; delete the `[tool.uv] conflicts = [...]` block; delete the `[[tool.mypy.overrides]]` block for `module = ["chromadb.*"]`; remove the `paperhub-modelserver = "..."` line from `[project.scripts]`)
- Modify: `backend/src/paperhub/config.py` (remove the `chroma_dir`, `embedding_model`, `reranker_model`, `model_server_host`, `model_server_port`, `inprocess_models` fields from the `Settings` dataclass and their `load_settings()` assignments + env reads: `PAPERHUB_EMBEDDING_MODEL`, `PAPERHUB_RERANKER_MODEL`, `PAPERHUB_MODEL_SERVER_HOST`, `PAPERHUB_MODEL_SERVER_PORT`, `PAPERHUB_INPROCESS_MODELS`, and the `chroma_dir = workspace / "chroma"` line. **Keep `PAPERHUB_DEVICE`** only if anything else reads it — verify with `rg "PAPERHUB_DEVICE|resolve_device" src`; after Task 4 there are no readers, so remove it too)
- Modify: `backend/.env.example` and `backend/.env` (remove the `PAPERHUB_EMBEDDING_MODEL`, `PAPERHUB_RERANKER_MODEL`, `PAPERHUB_DEVICE`, `PAPERHUB_MODEL_SERVER_HOST`, `PAPERHUB_MODEL_SERVER_PORT`, `PAPERHUB_INPROCESS_MODELS` lines + any `chroma` comment)
- Regenerate: `backend/uv.lock`

- [ ] **Step 1: Edit `config.py` first (so mypy catches stragglers)**

Remove the six `Settings` fields and their `load_settings` wiring listed above. Run:
```bash
cd backend
rg "chroma_dir|embedding_model|reranker_model|model_server_host|model_server_port|inprocess_models|PAPERHUB_DEVICE|PAPERHUB_INPROCESS|PAPERHUB_MODEL_SERVER|PAPERHUB_EMBEDDING|PAPERHUB_RERANKER" src tests
```
Expected: NO matches (if a test referenced a removed setting, fix it).

- [ ] **Step 2: Edit `pyproject.toml`**

Remove the deps, extras, index/sources/conflicts, the chromadb mypy override, and the `paperhub-modelserver` script entry as listed in **Files**.

- [ ] **Step 3: Edit `.env.example` and `.env`**

Delete the embedding/rerank/device/modelserver/inprocess lines and any chroma comment.

- [ ] **Step 4: Regenerate the lockfile and verify torch is gone**

```bash
cd backend
uv lock
rg -n "name = \"(torch|chromadb|sentence-transformers)\"" uv.lock
```
Expected: NO matches (torch, chromadb, sentence-transformers fully removed from the resolved graph). If `torch` still appears, find the remaining puller with `uv tree | rg -i torch` and resolve before continuing.

- [ ] **Step 5: Sync + full gates**

```bash
cd backend
uv sync
uv run pytest -q && uv run ruff check src tests && uv run mypy src
```
Expected: green; install is now CPU/torch-free.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore(deps): drop chromadb + sentence-transformers + torch

Removes the GPU torch extras/index routing, the chromadb mypy override,
the paperhub-modelserver script, and the dead embedding/modelserver
Settings + env vars. uv.lock no longer resolves torch.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Remove the modelserver from process orchestration

The boot script and Docker stack still launch/await the modelserver.

**Files:**
- Modify: `backend/scripts/start.ps1` (remove the modelserver spawn, the `ModelServerPort` param, the `PAPERHUB_MODEL_SERVER_HOST/PORT` env setup, the modelserver health-poll loop, and the modelserver teardown in the `finally`/cleanup; keep the MCP-up + backend launch)
- Modify: `backend/scripts/backfill_assets.ps1`, `backend/scripts/reingest_all_papers.ps1` (only if they set modelserver env or wait on `:8001` — `rg -n "MODEL_SERVER|8001|modelserver|INPROCESS" backend/scripts/*.ps1`; remove such lines, keep the CLI invocation)
- Modify: `docker-compose.yml` (delete the entire `modelserver` service block; remove `PAPERHUB_MODEL_SERVER_HOST/PORT`, `PAPERHUB_INPROCESS_MODELS`, `PAPERHUB_DEVICE` from the `backend` service env; remove the `paperhub-models` named volume + its mount; remove the `chroma` comment on the workspace volume)
- Modify: `docker-compose.gpu.yml` (delete the `modelserver` GPU reservation/override block + `TORCH_EXTRA`/`PAPERHUB_DEVICE=cuda` for it; if the file is now empty of meaningful overrides, leave a comment or delete it)
- Modify: `backend/Dockerfile` (remove the `TORCH_HOME`/`HF_HOME` env + any torch/modelserver-specific comment + the modelserver command-override comment; the backend image no longer needs model caches)

- [ ] **Step 1: Edit `start.ps1`**

```bash
rg -n "modelserver|ModelServer|MODEL_SERVER|8001|INPROCESS|paperhub-modelserver" backend/scripts/start.ps1
```
Remove every hit's logic (param, env, spawn, health poll, teardown). Keep `paperhub-mcp-up` + the backend `uvicorn` launch. Verify the script still parses:
```bash
pwsh -NoProfile -Command "& { . { } }"   # smoke: or just re-read the file for balance
```

- [ ] **Step 2: Edit the two helper scripts (if needed)**

```bash
rg -n "MODEL_SERVER|8001|modelserver|INPROCESS|PAPERHUB_DEVICE" backend/scripts/backfill_assets.ps1 backend/scripts/reingest_all_papers.ps1
```
Remove any modelserver env/wait lines; keep the CLI calls.

- [ ] **Step 3: Edit `docker-compose.yml` + `docker-compose.gpu.yml` + `backend/Dockerfile`**

Delete the modelserver service + volume + the env vars listed. In the GPU compose, delete the modelserver override. In the Dockerfile, drop the model-cache env. 

- [ ] **Step 4: Verify orchestration is clean**

```bash
rg -n "modelserver|MODEL_SERVER|8001|paperhub-models|chroma|TORCH_HOME|HF_HOME|INPROCESS|cu12" docker-compose.yml docker-compose.gpu.yml backend/Dockerfile backend/scripts/start.ps1
```
Expected: NO matches.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore(ops): remove modelserver from start.ps1 + docker stack

Drops the :8001 spawn/health-poll/teardown, the docker modelserver
service + paperhub-models volume, and the torch/model-cache env.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Update documentation (CLAUDE.md + README)

Bring the contributor docs in line with the spec (the SRS itself was already updated to v2.27 in the brainstorming step).

**Files:**
- Modify: `CLAUDE.md` (in **Dev-environment caveats**: delete the "Model server is a sibling process" bullet and the "External MCP daemons" mention of the modelserver if coupled; in **Conventions**: delete the "GPU operators (optional)" bullet about `uv sync --extra cu124/cu126/cu130` + `resolve_device`; in **Where things live**: remove `backend/src/paperhub/modelserver/` line; update any **Pointers to common questions** entries that reference the modelserver / embedder / "Why does the embedder live in a separate process?" / "Tests failing with httpx.ConnectError on embedder calls" / "How do I see the modelserver's logs?" — delete those Q&As or replace with a one-line "removed in v2.27" note)
- Modify: `README.md` (remove any modelserver / Chroma / GPU-extras / `:8001` mention from setup + architecture; if there is a tests badge, it is updated by the merge-prep skill later, not here)

- [ ] **Step 1: Edit CLAUDE.md**

```bash
rg -n "modelserver|model server|embedder|Chroma|chroma|cu124|cu126|cu130|resolve_device|PAPERHUB_DEVICE|INPROCESS|8001|sentence-transformers|cross-encoder" CLAUDE.md
```
For each hit, delete the bullet/Q&A or replace with a "removed in v2.27 — see SRS §III-5.4" note where a pointer is still useful. Keep the `marker` GPU notes (marker is out of scope and keeps its own torch).

- [ ] **Step 2: Edit README.md**

```bash
rg -n "modelserver|Chroma|chroma|embedder|cu124|cu126|8001|GPU extra|sentence-transformers" README.md
```
Remove modelserver/Chroma/GPU-extra setup + architecture mentions. Leave the marker section alone.

- [ ] **Step 3: Verify**

```bash
rg -n "modelserver|paperhub-modelserver|PAPERHUB_MODEL_SERVER|PAPERHUB_INPROCESS|cu124|cu126|cu130" CLAUDE.md README.md
```
Expected: NO matches (a single historical mention inside a quoted SRS changelog is acceptable; modelserver setup instructions must be gone).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "docs: drop modelserver/Chroma/GPU-extras from CLAUDE.md + README

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Final gates + real-API `:8000` verification

pytest proves the wiring compiles; the actual correctness test is a live `paper_qa` turn + a Citation Canvas click, confirming chunk resolution still works entirely from SQLite after the vector store is gone (per the SRS gate).

**Files:** none (verification only).

- [ ] **Step 1: Full backend + frontend gates**

```bash
cd backend && uv run pytest -q && uv run ruff check src tests && uv run mypy src
cd ../frontend && npm test -- --run && npm run typecheck && npm run lint && npm run build
```
Expected: all green. Frontend is unaffected but must stay green. Record both test counts for the release commit.

- [ ] **Step 2: Confirm `:8000` is live (do NOT boot your own backend)**

```bash
curl -s -m 3 http://127.0.0.1:8000/health
```
If not reachable, STOP and ask the user to start the backend with the (now modelserver-free) `backend/scripts/start.ps1`. Note: the user must restart `:8000` so it runs this branch's code.

- [ ] **Step 3: Drive a real `paper_qa` turn against a paper with chunks**

```bash
# Create a session, attach a paper (arXiv id), then ask a grounded question.
# Use the same HTTP shape the frontend uses: POST /sessions -> POST /papers -> POST /chat
# Confirm the streamed answer contains [chunk:<id>] markers.
```
Then trace the run from SQLite:
```bash
cd backend
uv run paperhub-replay --run-id <N>
# and: SELECT step_index, tool, status, result_summary_json FROM tool_calls WHERE run_id = <N>;
```
Expected: `paper_qa:subagent` + `paper_qa:finalize` steps `status=ok`; `chunks_cited_ids` populated; NO step references an embedder/reranker/chroma; the cited chunk IDs are real `chunks.id` rows.

- [ ] **Step 4: Ask the user to confirm the Citation Canvas visually**

Ask the user to open the frontend, click an inline `[chunk:N]` citation from the Step-3 answer, and confirm the canvas opens, switches to the paper, scrolls to and highlights the chunk (resolved via `GET /chunks/{id}` from SQLite). This is the human-in-the-loop sign-off that removal preserved FR-03.

- [ ] **Step 5: Hand off to merge-prep**

Do NOT bump versions or write the release commit here — that is the `paperhub-merge-prep` skill's job (it bumps `paperhub`/`frontend`/`paperhub-marker` to 2.27.0, regenerates the three lockfiles, updates README badges + the SRS revision row counts, and stops for merge approval). Report completion + the recorded test counts and let the user invoke merge-prep.

---

## Self-Review

**Spec coverage (SRS v2.27 surfaces):** §III-5.1 embedding/persistence → Task 2 + Task 5; §III-5.2 retriever → Task 1; §III-5.4 vector store → Tasks 2/4; §III-6 modelserver → Tasks 3/4/6; §III-7 chroma dir → Task 6 (compose) + Task 5 (config) ; deps/alerts → Task 5; orchestration → Task 6; docs → Task 7; gate → Task 8. The SQLite `chunks` table is explicitly preserved in Task 2 (the iron rule). ✓

**Placeholder scan:** every code step names exact files + the exact symbols to remove + a `rg` verification with an expected result. No "handle edge cases"/"TBD". ✓

**Consistency:** `Retriever`/`Reranker` removed in Task 1 before their modules are deleted in Task 1; `ChromaStore`/embedder consumers unwired in Tasks 2–3 before module deletion in Task 4; deps dropped (Task 5) only after no code imports them (verified end of Task 4). Ordering keeps the suite green after each task. ✓

**Out of scope (do NOT touch):** `marker_service/` (its own torch/pillow — the user ruled it out of scope); the SQLite `chunks` table + `api/chunks.py` + `paper_qa_subagent.py` + `library_stats`; the chromadb Critical alert has no patch and stays open per the user's decision (the removal makes it moot by dropping chromadb entirely).
