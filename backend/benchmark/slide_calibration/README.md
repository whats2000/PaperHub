# F4.4 slide calibration harness

Developer-side tool for driving slide-generation runs against the live
backend on `:8000` and dumping artifacts (slides.tex, slides.pdf,
tool_calls.json) for **human review**.

**Not a scoring tool.** Style and presentability are human judgement
calls, not pattern-matched against a fixed reference. The example deck
at `D:\GitHub\Final_Report` is ONE illustration of professional output;
the system's job is to be capable of professional output in general,
not to reproduce that specific surface form. The harness drives the
pipeline + dumps artifacts; the developer reads the PDFs.

## The dev-set papers

Three arXiv papers used as a fixed scoring set so iterations are
comparable to each other. Choice of papers is opportunistic — not
load-bearing.

| arxiv_id | Short title (informal) | Topic |
| --- | --- | --- |
| `2509.22093v1` | ADP | VLA efficiency / vision token pruning |
| `2512.04952v2` | FASTer | VLA efficiency / autoregressive decoding |
| `2602.20200v2` | OptimusVLA | VLA efficiency / diffusion prior |

## Two scenarios

**Scenario A — single-paper:** ingest ONE paper, prompt a focused deck.
**Scenario B — multi-paper:** ingest all 3, prompt a conference talk.

Both scenarios run BOTH languages (EN + ZH) when comparing across
generation runs is useful.

## How to run

```powershell
# 1. Make sure backend is up (separate shell):
scripts/start.ps1

# 2. From backend/ — seed sessions. RE-SEED EACH ROUND with fresh sessions
#    so the deck_command classifier doesn't route "prepare slides" to
#    NOTES generation when an existing deck is present.
cd backend
uv run python -m benchmark.slide_calibration.seed --scenario a --paper 2509.22093v1
uv run python -m benchmark.slide_calibration.seed --scenario a --paper 2512.04952v2
uv run python -m benchmark.slide_calibration.seed --scenario a --paper 2602.20200v2
uv run python -m benchmark.slide_calibration.seed --scenario b

# 3. Generate decks for the current round:
uv run python -m benchmark.slide_calibration.run_single --round <N> --paper 2509.22093v1
uv run python -m benchmark.slide_calibration.run_single --round <N> --paper 2512.04952v2
uv run python -m benchmark.slide_calibration.run_single --round <N> --paper 2602.20200v2
uv run python -m benchmark.slide_calibration.run_multi  --round <N> --lang en
uv run python -m benchmark.slide_calibration.run_multi  --round <N> --lang zh

# 4. Dump objective metrics + open PDFs for review:
uv run python -m benchmark.slide_calibration.compare --round <N>
# Writes _checks.json (frame count, includegraphics count,
# paper_newcommands marker, xelatex magic, theme line) and opens the
# generated PDFs. No scoring. Open D:\GitHub\Final_Report\... manually
# if you want a reference, but the harness does NOT compare for you.
```

Artifacts land under `backend/benchmark/slide_calibration/results/round_<N>/`
(gitignored — local-only).

## Hard rules

- **No reference-deck content in runtime prompts.** `D:\GitHub\Final_Report\*.tex`
  is the developer's reference, NOT the LLM's. Never inject its contents
  into a prompt as a few-shot.
- **No automated style scoring.** The harness dumps artifacts; the
  developer judges style. Don't add 0/1 dimension rubrics back in.
