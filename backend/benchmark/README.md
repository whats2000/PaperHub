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

## Session cleanup

The runner deletes every chat session it created when it ends, also when a case
fails or the run is interrupted. Sessions a case reuses through `session_id`
are left alone. Pass `-KeepSessions` (`--keep-sessions`) to keep them, e.g. to
review a generated deck in the frontend.

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

Multi-turn and gate fields (H4):

```toml
[benchmark]
suite = "regression"                 # optional label; "regression" = the G-c set

[[cases]]
scenario = 1                         # H4 scenario 1-8 (G-a, G-b)
kind = "chitchat"                    # "chitchat" / "library_count" = timed by G-d
turns = [                            # seeded earlier turns, sent as `history`
  { user = "Can GISC help YOLOSeg?", assistant = "Plausibly ... [chunk:101]" },
]
```

A non-empty `history` replaces the session's stored messages on both
backends, so `turns` cannot be combined with `session_group` or `session_id`
(the config loader rejects it): seeded turns would hide the shared session's
real earlier turns.

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

The judge returns three 0/1 criteria — `correct`, `grounded`, `on_thread`
(does the answer continue the seeded conversation's subject?) — and a case
scores 1 only when all three hold.

## Comparing two versions (H4 merge gate)

`-Compare <url> -CompareDbPath <db>` runs every case on a second (baseline)
backend too; `-BaseUrl`/`-DbPath` are the candidate. The baseline's result is
stored under each case's `compare` key. With `-Judge`, both sides are judged,
scenario 1/2/3/6/7/8 cases get a pairwise verdict in both orders
(`pairwise.orders`, from `benchmark/agent/grade.py`'s judge), and
`<name>.gate.json` + the `.md` header hold G-a…G-d with PASS/FAIL
(`benchmark/gate.py`). Merge the scenario and regression runs into one gate:

```powershell
scripts/run-benchmark.ps1 -Config benchmark/cases.h4-scenarios.toml -Judge `
    -BaseUrl http://127.0.0.1:8100 -DbPath D:/paperhub-h4-workspace/paperhub.db `
    -Compare http://127.0.0.1:8200 -CompareDbPath D:/paperhub-v241-eval-workspace/paperhub.db
uv run python -m benchmark.gate --results <scenarios>.json <regression>.json --out gate.json
```

`--compare` rejects cases with `session_id`: a session id names a row in one
workspace, and the two instances' workspace copies need not share ids.

The gate never lets a missing verdict help the candidate: in G-b every
scenario 1/2/3/6/7/8 case counts two comparisons, and one without a pairwise
verdict (judge error or never compared) counts as two candidate losses; G-c
fails on any unjudged verdict or uncompared regression case; G-d drops rows
that errored on either side from the medians and fails if the candidate
errored on a simple turn. Every count is in the gate's numbers.

## Evidence pages: the A/B/C comparison

`cases.h4-evidence.toml` holds the cases where the original parsing failed —
multi-panel figures, Marker-split figures, figures pandoc dropped, a figure
commented out of the LaTeX, small print, display equations Marker got wrong
(and right) — plus text regressions. Each case comments its evidence
location (paper, 1-based page, 0-1000 box). Run it once per arm, each on its
own backend instance and workspace copy (each with its own `mcp_servers.toml`
pointing at its port, and its own `-Out` folder: two runs started in the same
second write the same file name), then compare the judged reports per `kind`.
The 2026-11 results are recorded in `agent/prompts/core_agent/README.md`:

| Arm | Code | Workspace | Settings |
| --- | --- | --- | --- |
| A — original parsing | base worktree (`27b58ff7`) | un-migrated copy | as shipped |
| B — repaired crops | H4 at `1dbee386` (the last commit with the switch) | migrated copy | `PAPERHUB_AGENT_EVIDENCE=crops`, core_agent v7 |
| C — pages + zoom | H4 | migrated copy | core_agent v8 (pages is the only mode now) |

```powershell
scripts/run-benchmark.ps1 -Config benchmark/cases.h4-evidence.toml -Judge `
    -BaseUrl http://127.0.0.1:<arm port> -DbPath <arm workspace>/paperhub.db
```

The judge sees each cited figure and `[view:]` region as an image, so a claim
cited to evidence that does not show it fails. Read every answer, not only
the scores.

`marker_formula_audit.py` measures how often the LaTeX of a display equation
is wrong: the crop of the printed equation vs the chunk's LaTeX (`--source
chunks`, what the agent reads; default) or `asset/blocks.json` (`--source
blocks`, what the PDF translation card renders). A vision judge at
temperature 0 labels each one (`--prompt a|b` judge variants); it
over-reports, so hand-check the `wrong` rows in `audit.csv` against the crops
before quoting a rate:

```powershell
uv run python -m benchmark.marker_formula_audit --db D:/paperhub-h4-workspace/paperhub.db
```

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
| `runner.py` | orchestrates the sweep (and `--compare`), writes JSON + MD. |
| `judge.py` | LLM judge: correct + grounded + on_thread. |
| `gate.py` | H4 merge gate G-a…G-d from `--compare` results. |
| `list_papers.py` | list cached `content_key`s for building a config. |
| `cases.example.toml` | 20-case example (16 paper_qa + 4 slides). |
