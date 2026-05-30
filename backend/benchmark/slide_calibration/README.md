# F4.4 slide calibration harness

Developer-side scoring tool for F4.4 (slide quality refinement). See
[`docs/superpowers/plans/2026-05-31-paperhub-F4.4-slide-quality-refinement.md`](../../../docs/superpowers/plans/2026-05-31-paperhub-F4.4-slide-quality-refinement.md)
for full context.

**Not a CI gate.** **Not a long-lived regression suite.** This harness
exists so each Round N of F4.4 is comparable to Round N-1 — without a
fixed scoring set, "did this round improve things" is unanswerable.
Once F4.4 is done, the harness's job is done.

## The dev-set papers

Three arXiv papers used in `D:\GitHub\Final_Report`'s hand-built gold deck:

| arxiv_id | Short title (gold deck) | Topic |
| --- | --- | --- |
| `2509.22093v1` | ADP — Action-aware Dynamic Pruning | VLA efficiency / vision token pruning |
| `2512.04952v2` | FASTer — Block-wise AR VLA | VLA efficiency / autoregressive decoding |
| `2602.20200v2` | OptimusVLA — Dual-Memory Augmented VLA | VLA efficiency / diffusion prior |

Per-paper gold decks live under `D:\GitHub\Final_Report\<arxiv_id>\slides.pdf`;
multi-paper gold lives at `D:\GitHub\Final_Report\slides.pdf`.

## Two scenarios

**Scenario A — single-paper contract fidelity** (run for EACH paper):
ingest ONE paper, prompt ~15-slide academic deck. Compare against the
paper's gold per-paper deck + (optionally) `reference/paper2slides-plus`
output on the same paper.

**Scenario B — multi-paper conference deck:** ingest all 3, prompt
12-minute conference talk. Compare against the multi-paper gold.

Both scenarios run BOTH languages (EN + ZH) starting Round 1+.

## How to run a round

```powershell
# 1. Make sure backend is up (separate shell):
scripts/start.ps1

# 2. From backend/ — seed sessions (one Scenario A per paper + one Scenario B):
cd backend
uv run python -m benchmark.slide_calibration.seed --scenario a --paper 2509.22093v1
uv run python -m benchmark.slide_calibration.seed --scenario a --paper 2512.04952v2
uv run python -m benchmark.slide_calibration.seed --scenario a --paper 2602.20200v2
uv run python -m benchmark.slide_calibration.seed --scenario b

# 3. Generate decks for the current round (uses the session ids from seed):
uv run python -m benchmark.slide_calibration.run_single --session <id> --round <N> --paper 2509.22093v1
uv run python -m benchmark.slide_calibration.run_single --session <id> --round <N> --paper 2512.04952v2
uv run python -m benchmark.slide_calibration.run_single --session <id> --round <N> --paper 2602.20200v2
uv run python -m benchmark.slide_calibration.run_multi  --session <id> --round <N> --lang en
uv run python -m benchmark.slide_calibration.run_multi  --session <id> --round <N> --lang zh

# 4. Compare & score:
uv run python -m benchmark.slide_calibration.compare --round <N>
# Opens PaperHub PDFs side-by-side with gold; runs deterministic checks
# (em-dash sweep, word count, figure-existence); creates blank scorecards.
# Agent then fills the scorecards via direct PDF reading (see plan §self-judge).
```

Artifacts land under `benchmark/slide_calibration/results/round_<N>/`.
Each scenario writes `slides.tex` + `slides.pdf` + `tool_calls.json` +
`speaker_notes_*.tex` (when produced).

## Hard rule — no comparison-target leak

`D:\GitHub\Final_Report\*.tex` and per-paper `<arxiv_id>/slides.tex` must
NEVER be fed into a runtime LLM prompt. The harness COMPARES PaperHub
output to them; the pipeline NEVER reads them. See the plan's
"Boundary: methodology references vs comparison targets" section.

## When to escalate to the user

After scoring, agent self-judges via direct PDF comparison. If the
agent's honest read says *"mine is still obviously weaker on dimension
X"*, design Round N+1 — do not interrupt the user. Only when the agent's
own comparison says *"mine is at-parity with the gold on both scenarios,
notes pass read-aloud"* does the agent escalate for user verification +
the production test on the user's own novel papers.
