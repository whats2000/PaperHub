# Per-stage agent prompt evaluation (SRS §III-9, Plan G1)

Evaluate ONE agent prompt at a time, precisely, on real recorded inputs — replay
the stage with a prompt *variant* (a YAML file in this folder), score its output
(quality + token count), and persist each run as a comparable JSONL experiment.

**Isolation:** this tool touches **no deploy code**. Variants live here, not in
`src/.../llm/prompts/`; it calls the LLM via litellm directly. Adopting a winning
variant into the production registry is a separate, deliberate step you take
AFTER a sweep — never automated. In-process: **no live backend needed**, only an
LLM API key in `backend/.env`.

## The loop (run from `backend/`)

```powershell
# 1. (optional) harvest REAL inputs from the trace DB into a new bucket
scripts/run-eval.ps1 harvest --db workspace/paperhub.db --stage router `
  --out benchmark/agent/corpus/router.harvest.jsonl

# 2. write a new variant: benchmark/agent/prompts/router/v2.yaml (system:/user:),
#    add "v2" to router.eval.toml's variants, then sweep the whole grid:
scripts/run-eval.ps1 sweep --config benchmark/agent/router.eval.toml
```

The sweep prints + writes a matrix report — variants as columns, buckets as rows,
with a Δ-vs-baseline column flagging any **regression** (⚠):

```
# Eval sweep: router
- model: gemini-2.5-flash · reps: 3 · backend: auto · variants: router/v1, router/v2
| test set   | router/v1 (score/tok) | router/v2 (score/tok) | Δ router/v2 vs router/v1 |
|---|---|---|---|
| core       | 0.86 / 1180           | 0.95 / 910            | +0.09                    |
| regression | 1.00 / 1180           | 0.80 / 910            | -0.20 ⚠                  |
| edge       | 0.50 / 1180           | 0.75 / 910            | +0.25                    |
```

That ⚠ is the point: v2 is more concise and fixes edge cases but breaks a
behaviour the regression bucket guards — adopt nothing until it's clean.

## The primitives (what `sweep` orchestrates)

```powershell
scripts/run-eval.ps1 run --stage router --version v1 `
  --corpus benchmark/agent/corpus/router.core.jsonl --model gemini/gemini-2.5-flash
scripts/run-eval.ps1 list --stage router
scripts/run-eval.ps1 compare --a 1 --b 2
# freeze the winner -> emit golden outputs (the NEXT stage's real inputs)
scripts/run-eval.ps1 golden --stage router --version v2 `
  --corpus benchmark/agent/corpus/router.core.jsonl `
  --model gemini/gemini-2.5-flash --out benchmark/agent/corpus/router.golden.jsonl
```

## Concepts

- **Variant** — a prompt version = `prompts/<stage>/<version>.yaml`, browsable +
  editable. `v1` is seeded from the shipped registry prompt (the baseline).
- **Test-set buckets** — `core` (target), `regression` (side-effect guard),
  `edge` (ambiguous/short), `harvest` (real production failures, promoted in).
- **Two scores** — output quality (`mean_score`, 0..1; deterministic intent match
  for the router, judge otherwise) + prompt quality (`mean_tokens_in`).
- **Reps** — N times per case for variance, so a delta is signal not noise.
- **Backend** — `auto` uses the provider Batch API where available (~50% cheaper)
  and degrades to concurrent requests otherwise. `--no-…`: pass `--backend concurrent`.
- **Freeze + propagate** — a frozen winner's golden outputs become the next
  stage's real input set; the cascade rolls down from the router.
- **Adopt** — to ship a winning variant, copy its YAML into
  `src/.../llm/prompts/` as a new `_vN.yaml` and switch the call site. A separate
  `writing-agent-prompts` step — the eval never does it for you.

`results/` + `*.harvest.jsonl` are gitignored; buckets, variants, and
`router.eval.toml` are committed. See SRS §III-9 + `writing-agent-prompts`.
