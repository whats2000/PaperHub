# PaperHub real-API benchmark

A config-driven harness that drives the **live** backend (`:8000`) as a
simulated user — attaching cached reference papers, routing prompts through
`/chat`, and collecting grounding evidence (cited chunk text + agent trace)
into a reviewable report. Scoring is **answer correctness + grounding**:
the report lays out each answer next to the *actual cited chunk text* and the
trace step statuses so a reviewer (you, or Claude) can mark each case **0/1**.

It uses the backend the user is already running — it does **not** boot its own
(a separate instance would race the user's DB / have stale code).

## Quick start

```powershell
# 1. Make sure the backend is up (separate shell):
scripts/start.ps1

# 2. See what's cached, to build/adjust a config:
cd backend; uv run python -m benchmark.list_papers

# 3. Run the example sweep (20 cases) — from backend/:
scripts/run-benchmark.ps1
# or a subset:
scripts/run-benchmark.ps1 -Only qa-01-mha,rpt-01-transformer
# or your own config:
scripts/run-benchmark.ps1 -Config benchmark/my-cases.toml
# resume after a transient failure (e.g. a network drop mid-sweep) — carries
# over cases that already completed, re-runs only the failed/missing ones:
scripts/run-benchmark.ps1 -Resume benchmark/results/paperhub-rag-qa-<ts>.json
```

## Resume

A long sweep writes its `.json` after **every** case, so a crash or network
drop never loses completed work. To finish an interrupted run, pass the prior
`.json` to `-Resume` (or `--resume` on the runner): cases that completed cleanly
(no error, a run was created) are carried over verbatim, and only the
failed/missing cases are re-run — all merged into one fresh report. Combine
with `-Only` to force-rerun a specific subset regardless of prior status.

Output lands in `backend/benchmark/results/<name>-<timestamp>.{json,md}`
(gitignored). The `.md` is the human review sheet; the `.json` has the full
payload (every step, every cited chunk).

## Writing a config

See [`cases.example.toml`](cases.example.toml). Shape:

```toml
[benchmark]
name = "my-eval"
base_url = "http://127.0.0.1:8000"
db_path = "workspace/paperhub.db"   # for cache resolution + chunk/trace reads

[papersets]                          # reusable named reference sets
moe = ["arxiv:2202.09368", "arxiv:2412.14711"]

[[cases]]
id = "qa-01"
paperset = "moe"                     # attach a named set …
# papers = ["arxiv:1706.03762"]      # … or list inline (overrides/extends)
expect_intent = "paper_qa"           # asserted against the router's decision
prompt = "How does ReMoE's routing differ from TopK?"   # the simulated user turn
rubric = "what a correct, grounded answer must contain"  # shown to the reviewer
# session_group = "conv-a"           # optional: cases sharing a group chat in one session
# current_view_page = 3              # optional: for 'edit this slide' deck turns
```

Paper keys are `paper_content.content_key` — `arxiv:<id>` or `sha256:<hash>`.
Cached keys attach via the dedup cache (`library:<pc_id>`, no re-ingest);
uncached `arxiv:` keys ingest on attach; uncached `sha256:` uploads error
(an upload can't be reconstructed from its hash — pre-cache it).

## LLM-as-Judge (automated 0/1 scoring)

The deterministic checks catch obvious failures; the **correctness + grounding**
call can be automated with an LLM judge. The judge sees only what a reviewer
sees — prompt, rubric, answer, and the *actual cited chunk text* — and returns a
structured `{score, confidence, rationale}`. It scores an existing results
`.json` (no backend calls), so it's cheap to re-run and can use a stronger model
than the agents under test. It is strict on grounding: a factually-true claim
that isn't supported by the *cited* chunks scores 0.

```powershell
# judge a fresh sweep inline:
scripts/run-benchmark.ps1 -Judge
# or judge an existing result file:
cd backend
uv run python -m benchmark.judge --results benchmark/results/<name>.json --config benchmark/cases.example.toml
```

The judge needs an LLM API key; it loads `backend/.env` (override with `--env`).
Verdicts are written back into the `.json` and the `.md` Score column is filled.

## How a case is scored

The runner records, per case:

- **Routing** — expected vs actual intent (`intent_match`).
- **Trace** — every `tool_calls` step + status (`all_steps_ok`); errored steps
  are flagged by name.
- **Grounding** — chunk IDs the answer cites (`[chunk:N]` markers ∪ the
  subagents' recorded `chunks_cited_ids`), resolved to their real text from the
  `chunks` table (`citations_present`, `citations_resolve`).
- **Slides** — `deck_generated` (deck present with `page_count > 0`).

Those deterministic checks catch the obvious failures. The **final 0/1** is a
correctness+grounding judgement made by reading the answer against the cited
chunk text in the `.md` report (the `Score` column is left `_TBD_` for you).

## Files

| File | Purpose |
|---|---|
| `driver.py` | SSE client for `/sessions`, `/papers`, `/chat`. |
| `config.py` | TOML config → `BenchmarkConfig` / `Case`. |
| `resolve.py` | source key → cache `library:<id>` (or ingest fallback). |
| `scorer.py` | trace + cited-chunk extraction + deterministic checks. |
| `runner.py` | orchestrates the sweep, writes JSON + MD. |
| `list_papers.py` | list cached `content_key`s for building a config. |
| `cases.example.toml` | 20-case example (16 paper_qa + 4 slides). |
