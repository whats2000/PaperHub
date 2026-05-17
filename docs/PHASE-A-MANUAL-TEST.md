# Phase A — Manual Test Checklist

Run through this before deciding to merge `feat/phase-a-foundations`. Every step has a concrete command + expected output. If any step diverges from the expected output, **stop and report** before moving on.

All commands are PowerShell, run from the repo root unless noted. Estimated total time: ~10 minutes (first run downloads the BGE embedder model ~80 MB; subsequent runs are seconds).

---

## 0 · Prerequisites

```powershell
# Verify toolchain
uv --version            # >= 0.5
node --version          # >= 20
python --version        # >= 3.12 (uv will install if missing)

# Verify .env is configured (don't print it — just check existence)
Test-Path backend/.env  # → True
```

If `.env` is missing: `Copy-Item backend/.env.example backend/.env`, then edit to add `GEMINI_API_KEY=<your-key>` (or another provider's key).

---

## 1 · Sync deps + verify unit tests (~30 s)

```powershell
cd backend
uv sync                                # installs Python deps + creates .venv
uv run pytest -m "not e2e" -q          # expect: 94 passed, 5 deselected
uv run mypy                            # expect: Success: no issues found in 63 source files
uv run ruff check .                    # expect: All checks passed!
uv run ruff format --check .           # expect: 63 files already formatted
cd ..
```

**Pass criteria:** all four commands succeed with the expected output. **If any fail**, stop and report which.

```powershell
cd frontend
npm install                            # 1st run installs ~250 packages
npm run typecheck                      # expect: clean (no output, exit 0)
npm run lint                           # expect: clean (no output, exit 0)
npm run test                           # expect: 5 tests passed (2 files)
npm run build                          # expect: built in <500ms, dist/ emitted
cd ..
```

**Pass criteria:** all four frontend gates succeed.

---

## 2 · Boot the backend (~5 s once deps cached)

In **Terminal A**:

```powershell
cd backend
uv run uvicorn paperhub.api.app:create_app --factory --port 8765 --log-level info
```

**Pass criteria** (look for these lines in the output):
```
INFO:     Started server process [...]
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8765 (Press CTRL+C to quit)
```

You should NOT see anyio cancel-scope errors, sentence-transformers tracebacks, or anything in red.

---

## 3 · Health endpoint (instant)

In **Terminal B**:

```powershell
curl http://127.0.0.1:8765/health
```

**Expected output (exact):**
```json
{"status":"ok","app":"paperhub","schema_version":3}
```

`schema_version: 3` confirms migrations 0001 + 0002 + 0003 all applied. If it's lower, migrations didn't run — wipe `backend/.paperhub-workspace/paperhub.db` and restart the backend.

---

## 4 · Chitchat path — instant routing, no embedder load (~2 s)

```powershell
curl -N -X POST http://127.0.0.1:8765/chat `
     -H "Content-Type: application/json" `
     -d '{"message":"Hello, just saying hi","session_id":null}' `
     --max-time 30
```

**Expected output:** TWO SSE events, in order:

```
data: {"type": "routing_decision", "data": {"intent": "chitchat", "confidence": <0.9-1.0>, "model_tier": "small", "reasoning": "<...>", "fallback_to_user": false}}

data: {"type": "final", "run_id": "<uuid>", "answer": "I can only answer questions about papers you have indexed in PaperHub. Please import some papers first, then ask a question about their content."}
```

**Pass criteria:**
- `intent` is `"chitchat"` (Gemini classified correctly)
- `model_tier` is `"small"` (router used the small tier)
- Time-to-first-byte ≤ 5 s (NFR-01 warm-cache)
- No `error` event

---

## 5 · Import a real arXiv paper — Tier 1 LaTeX with figures (~15 s)

```powershell
curl -X POST http://127.0.0.1:8765/papers/import `
     -H "Content-Type: application/json" `
     -d '{"arxiv_id":"1706.03762"}' `
     --max-time 180 `
     -w "`n[HTTP %{http_code}, time %{time_total}s]`n"
```

**Expected output:** HTTP 200, JSON body with at minimum these fields:

```json
{
  "id": "<uuid>",
  "arxiv_id": "1706.03762",
  "title": "Attention Is All You Need",
  "authors": ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar", ...],
  "year": 2017,
  "abstract": "[EXTERNAL CONTENT] The dominant sequence transduction models are based on complex recurrent or convolutional...",
  "pdf_path": ".paperhub-workspace\\papers\\1706.03762\\source\\...tex",
  "source_dir_path": ".paperhub-workspace\\papers\\1706.03762\\source",
  "extraction_tier": "latex",
  "notes_md": null,
  ...
}
```

**Pass criteria:**
- `extraction_tier == "latex"` (Tier 1 succeeded)
- `notes_md == null` (NOT flagged as low-fidelity)
- `pdf_path` ends in `.tex`
- `source_dir_path` is set

---

## 6 · Verify figures are on disk (paper2slides-plus capability preserved!)

```powershell
# List the unpacked e-print directory
Get-ChildItem backend/.paperhub-workspace/papers/1706.03762/source -Recurse |
  Select-Object Name, Length | Format-Table

# Count .tex files
(Get-ChildItem backend/.paperhub-workspace/papers/1706.03762/source -Recurse -Filter *.tex).Count

# Count figure files (.png, .pdf, .eps, .jpg, .jpeg)
(Get-ChildItem backend/.paperhub-workspace/papers/1706.03762/source -Recurse `
  | Where-Object { $_.Extension -in '.png','.pdf','.eps','.jpg','.jpeg' }).Count
```

**Pass criteria:**
- At least 1 `.tex` file present
- At least 1 figure file present (the Transformer paper has multiple `.pdf` architecture diagrams)

**This is the SRS §1.1 compliance check** — Tier 1 must give us the figures for Phase B's slide pipeline.

---

## 7 · Verify DB persistence

```powershell
cd backend
uv run python -c "
import sqlite3
conn = sqlite3.connect('.paperhub-workspace/paperhub.db')
conn.row_factory = sqlite3.Row
for table in ['papers', 'chunks', 'runs', 'messages', 'chat_sessions', 'tool_calls']:
    rows = conn.execute(f'SELECT COUNT(*) AS n FROM {table}').fetchone()
    print(f'  {table}: {rows[\"n\"]} rows')
print('---')
p = conn.execute('SELECT id, arxiv_id, title, extraction_tier, source_dir_path FROM papers').fetchone()
if p:
    print(f'  paper: arxiv_id={p[\"arxiv_id\"]}, tier={p[\"extraction_tier\"]}, source={p[\"source_dir_path\"]}')
"
cd ..
```

**Pass criteria:**
- `papers: 1` rows
- `chunks: N > 0` rows (LaTeX got chunked + embedded)
- `runs: ≥ 1` rows (from step 4)
- `messages: ≥ 1` rows (user message from step 4)
- `chat_sessions: ≥ 1` rows
- `tool_calls: 0` rows is fine — the chitchat path doesn't emit tool_step events (paper_qa does)
- The paper row's `extraction_tier='latex'` and `source_dir_path` is non-null

---

## 8 · Paper Q&A — full RAG flow with Gemini + citations (~10-30 s)

```powershell
curl -N -X POST http://127.0.0.1:8765/chat `
     -H "Content-Type: application/json" `
     -d '{"message":"What architecture does this paper propose, and what is its main advantage over RNNs?","session_id":null}' `
     --max-time 120
```

**Expected output:** SSE events, in order:

```
data: {"type": "routing_decision", "data": {"intent": "paper_qa", "confidence": <high>, "model_tier": "flagship", ...}}

data: {"type": "tool_step", "data": {"run_id": "...", "step_index": 0, "agent": "research_agent", "tool": "research_qa", "model": "gemini/gemini-2.5-pro", "args_redacted": {"question": "..."}, "result_summary": {"chunks_retrieved": 5, "answer_length": <N>}, "latency_ms": <N>, "status": "ok"}}

data: {"type": "token", "data": "The paper proposes the Transformer architecture, ... ($§...$, p.$N$)"}

data: {"type": "citation", "chunk_id": "...", "section": ..., "page": ...}
(repeated for each retrieved chunk used as citation)

data: {"type": "final", "run_id": "...", "answer": "..."}
```

**Pass criteria:**
- `intent` is `"paper_qa"`
- `model_tier` is `"flagship"`
- The `token` event's text mentions **"Transformer"** (case-insensitive)
- The answer contains an **inline citation marker** matching `(§..., p....)` — even a loose pattern is fine
- No `error` event
- Time-to-first-byte ≤ 10 s; total ≤ 30 s (NFR-01 warm-cache budget; first run may be slower on cold embedder)

---

## 9 · Trace UI (browser)

In **Terminal C**:

```powershell
cd frontend
npm run dev
```

Open http://localhost:5173 in your browser.

**Manual interactions:**

1. The page renders the dark-themed chat shell with a Sidebar and a Composer.
2. Type "Hello" in the Composer and press Enter (or click Send).
   - A `RoutingBadge` (small pill) appears showing `chitchat · small` BEFORE the assistant text.
   - The assistant's polite refusal text appears.
3. Type "What architecture does this paper propose?" and press Enter.
   - `RoutingBadge` shows `paper_qa · flagship` BEFORE the assistant text.
   - The assistant's answer streams in (full chunk for now per Phase A; Phase B will tokenize).
   - A collapsible `TraceInline` element shows under the answer — click it to expand, you should see the `research_qa` tool step.

**Pass criteria:**
- RoutingBadge renders BEFORE the assistant text in both cases
- The TraceInline shows ≥ 1 step for paper_qa
- No console errors in the browser DevTools

---

## 10 · Error path — invalid arxiv ID fails loud

```powershell
curl -X POST http://127.0.0.1:8765/papers/import `
     -H "Content-Type: application/json" `
     -d '{"arxiv_id":"9999.99999"}' `
     --max-time 120 `
     -w "`n[HTTP %{http_code}]`n"
```

**Expected output:** HTTP 502 with a body listing each tier that was tried and why:

```
{"detail": "All import tiers failed for 9999.99999: [('latex', '<error message>'), ('raw', '<error message>')]"}
[HTTP 502]
```

**Pass criteria:**
- Status 502 (not 200, not 500 with traceback)
- Detail names BOTH `'latex'` and `'raw'` tiers
- No silent empty paper row written to the DB (verify with the SQL count from step 7 — still `papers: 1`)

---

## 11 · Run the live e2e tests

In **Terminal B** (backend still running in Terminal A):

```powershell
cd backend
uv run pytest -m e2e -v --tb=short
cd ..
```

**Expected output (last few lines):**
```
tests/api/test_papers_import.py::test_papers_import_e2e_real_arxiv SKIPPED
tests/integration/test_latex_first_ladder_e2e.py::test_latex_first_import_real_arxiv PASSED
tests/integration/test_latex_first_ladder_e2e.py::test_latex_first_import_preserves_raw_source_and_figures PASSED
tests/integration/test_latex_first_ladder_e2e.py::test_chat_paper_qa_against_latex_import PASSED
tests/integration/test_paper_qa_e2e.py::test_paper_qa_end_to_end SKIPPED

=========== 3 passed, 2 skipped, 94 deselected in ~70s ===========
```

**Pass criteria:**
- 3 e2e tests PASSED
- 2 e2e tests SKIPPED (the older placeholder tests; safe to ignore)
- Total time ≤ 90 s

If `tests/integration/test_paper_qa_e2e.py::test_paper_qa_end_to_end` errors instead of skipping, it's fine — it's a legacy stub that may need an env-var; not a Phase A regression.

---

## 12 · Teardown

In Terminal C (frontend): `Ctrl+C`
In Terminal A (backend): `Ctrl+C`

Verify no zombie processes:

```powershell
Get-Process | Where-Object { $_.ProcessName -in 'python', 'node', 'uvx' -and $_.StartTime -gt (Get-Date).AddMinutes(-30) }
```

Expected: empty output (or only your own pre-existing processes). The arxiv-latex-mcp subprocess should have exited when the backend lifespan tore down its session.

---

## What this test covered

| Item | SRS reference | Step(s) |
|---|---|---|
| App boots; migrations apply | NFR-04, FR-11 substrate | 2, 3 |
| `/health` schema_version reporting | NFR-04 | 3 |
| Router classifies correctly (chitchat) | FR-08 (binary) | 4 |
| User message persistence | FR-11 | 7 |
| Run lifecycle (created → finalized) | FR-11 | 4, 7 |
| Tier 1 LaTeX import via arxiv-latex-mcp | §1.1 clause 1 / FR-01 | 5 |
| Raw e-print archive download (figures preserved) | §1.1 clause 1 (Tier 1) / FR-05 substrate | 6 |
| RAG indexing (chunks + vectors) | FR-03 | 7 |
| Router classifies correctly (paper_qa) | FR-08 (binary) | 8 |
| Research Agent RAG pipeline with citations | FR-03 | 8 |
| SSE event ordering + types | design §8 | 4, 8 |
| Tool-Call Tracer writes `tool_calls` rows | FR-11 | 8 |
| Chat UI renders RoutingBadge + TraceInline | NFR-05 | 9 |
| Fail-loud on unsalvageable import (no silent downgrade) | §1.1 clause 1 fail-loud rule | 10 |
| Live e2e tests pass against real arxiv + real Gemini | (test discipline per CLAUDE.md) | 11 |
| No zombie subprocesses on shutdown | design §6 lifespan ownership | 12 |

## What this test did NOT cover (Phase B work)

- Marker container (Tier 2) — scaffolded, not deployed
- Agentic search → read → decide → download flow — Phase B
- Multi-paper slide generation — Phase B (Report Agent)
- NL2SQL via SQL Agent — Phase B
- Relation graph / research-direction — Phase B
- Multi-project management — Phase B
- Evaluation harness — Phase C
- Batch import (≥ 10 arXiv IDs) per Acceptance #1 — Phase C
- Real-time token streaming (Phase A emits full answer in one `token` event) — Phase B

---

## If everything passed

You're good to merge. Reply "all pass" (or whatever) and I'll re-run the finish-branch question.

## If something failed

Tell me the step number + the actual output. We iterate before merge.
