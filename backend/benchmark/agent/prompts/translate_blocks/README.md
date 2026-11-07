# translate_blocks — block translation prompt (Plan H1)

Shipped as `src/paperhub/llm/prompts/translate_blocks_v1.yaml` = **v9**.

## Hypothesis

The prompt must translate 1–5 adjacent blocks per call while keeping every
`⟦n⟧` / `⟦BR⟧` exactly (a dropped, duplicated or invented token forces a
retry, then a flagship escalation). A mid-paragraph `⟦BR⟧` must stay mid-text.
The prompt must also keep identifiers and numbers, apply remembered
terminology (and only that), honour a free-text regional language, and return
one item per input key. It must not lose fidelity or fluency while doing so.

## Setup

- **Query set:** `corpus/translate_blocks.core.jsonl` — 10 calls carrying **14
  blocks**:
  - 4 formula-dense blocks: one is a 17-formula Nature-style paragraph, one is
    a PDF paragraph with `⟦BR⟧`.
  - 3 blocks with citation placeholders, 2 list items, 1 caption.
  - 2 plain blocks.
  - 2 terminology probes (added in fix round 1):
    - **conflict**: memory says "token" → 符元 and "embedding" → 嵌入表示. The
      output must contain both and must not contain 詞元.
    - **none**: empty terminology. Any rendering is fine; 詞元 is tracked as a
      possible leak but not graded, because it is a legitimate default.
  - The two older terminology calls (memory "token" → 詞元) require 詞元.
  - Targets are zh-TW, ja and `繁體中文（香港）`.
- **Sweep model:** `gemini/gemini-3.5-flash-lite`, the deploy small tier
  (`PAPERHUB_MODEL_SMALL` in `backend/.env`), at temperature 0.
- **Deterministic grade** (`stages.translate_failures`). Any failure scores 0
  and loses the pairwise. A call passes only if every item:
  - passes `placeholders_intact`;
  - passes `br_position_intact`: a mid-paragraph `⟦BR⟧` is not moved to the
    start or end;
  - matches the input key set exactly, with no duplicates;
  - contains every `expect.require` term and no `expect.forbid` term.
- **Pairwise judge** (`cli pairwise`): `gemini/gemini-3.8-flash` (flagship
  tier) judges fidelity first, then fluency. It runs twice with A/B swapped,
  and a win counts only when both orders agree; otherwise the pair is a tie.

## Variants

| ver | change |
|---|---|
| v1 | Numbered rules, no example |
| v2 | Concise bullets + one worked example in the user turn (example term: token → 詞元) |
| v3 | v2 + an explicit `⟦BR⟧` rule, a no-invention rule and a `⟦BR⟧` example |
| v4 | v3's rules with v2's single example (no `⟦BR⟧` example) |
| v5 | v3 with the `⟦BR⟧` example fully translated (v3 kept "epoch" in English) |
| v6 | v5 + a token-free example block |
| v7 | v4 + a sharper `⟦BR⟧` placement rule |
| v8 | v7 with the example term moved off the corpus (dropout → 隨機失活) |
| **v9** | v8 + "terminology covers plural/derived forms; never splice the English word into a rendering" (illustrated with an off-corpus term, layer → 層) |

## Results — fix round 1 (14 blocks, current grader)

| ver | reps | pass | tokens in | failures |
|---|---|---|---|---|
| v4 | 3 | 28/30 | 489 | `⟦BR⟧` moved to the paragraph end (2) |
| v7 | 3 | 30/30 | 507 | none, but 詞元 adherence came from its example (see v8) |
| v8 | 3 | 28/30 | 516 | missing 詞元: wrote "詞token", "詞vocabulary" (2) |
| **v9** | 3+3 | **60/60** | **564** | none |

Terminology probes:
- **Conflict case** (符元 + 嵌入表示): every variant passes 3/3, so the models
  do read `<terminology>` when it differs from the example.
- **Empty-terminology case**, rate of 詞元 appearing: v4 1/3 (leaked from its
  詞元 example), v7 0/3, v8 0/3, v9 0/6. v9 writes "token" in this case.

Pairwise judge (3 reps × 10 calls = 30 pairs; A / B / tie):

| A vs B | A | B | tie |
|---|---|---|---|
| v9 vs v7 | 10 | 11 | 9 |
| v9 vs v4 | 12 | 11 | 7 (2 of v9's wins are v4's `⟦BR⟧` failures) |

## Results — round 0 (12 blocks), regraded with the current grader

| ver | reps | pass | tokens in | notes |
|---|---|---|---|---|
| v1 | 3 | 17/24 | 444 | dropped `⟦BR⟧`; invented `⟦0⟧` in token-free blocks; duplicated `⟦1⟧` |
| v2 | 3 | 21/24 | 459 | dropped `⟦BR⟧`, `⟦BR⟧` at the end |
| v3 | 3+5 | 64/64 | 597 | code-mixed "單 unitary" 2/5 |
| v4 | 3+5 | 58/64 | 501 | `⟦BR⟧` moved to the paragraph end 6/8 |
| v5 | 5 | 38/40 | 596 | invented `⟦BR⟧` in a list item |
| v6 | 5 | 40/40 | 636 | caption numbers mangled ("1,25M", "1,250 萬" for 125M) |
| v7 | 5 | 40/40 | 519 | — |

Round-0 pairwise (5 reps × 8 calls = 40 pairs unless noted):

| A vs B | A | B | tie |
|---|---|---|---|
| v1 vs v4 (3 reps) | 6 | 14 | 4 |
| v2 vs v4 (3 reps) | 7 | 9 | 8 |
| v3 vs v4 | 16 | 16 | 8 |
| v6 vs v4 | 12 | 18 | 10 |
| v6 vs v3 | 10 | 20 | 10 |
| v7 vs v4 | 20 | 15 | 5 |
| v7 vs v6 | 16 | 15 | 9 |

v7 vs v6 (16–15–9) is a statistical tie on prose. v7's edge over v6 was its
lower token cost and the absence of number mangling.

## Why v9

The judge does not separate v9, v7 and v4 on prose: v9 vs v7 is 10–11–9 and v9
vs v4 is 12–11–7, both ties. The decision rests on the deterministic contract:

- **v4** moves a mid-paragraph `⟦BR⟧` to the end (fails `br_position_intact`).
- **v7's 詞元 cases were indistinguishable from copying its example.** Its
  worked example demonstrates exactly the corpus term (token → 詞元), so its
  output can't show whether it read `<terminology>`. v8 removes only that,
  and the model then splices English into the rendering ("詞token") in 2 of
  3 runs.
- **v9** keeps the example off-corpus, adds the derived-forms / no-splicing
  rule, and passes 60/60. It also leaks no example term into the
  empty-terminology case.

The cost is +57 input tokens over v7.

Round 0 also showed that adding more worked examples (v3, v5, v6) makes the
model copy their surface habits: an untranslated term, rewritten numbers. A
precise rule works without those side effects.

## Known residuals (v9)

- **Derived terminology forms:** "tokenizer" varies (分詞器 / 詞元分析器 /
  詞元化工具) even when the memory fixes "token" as 詞元. All of these are
  readable and none splices English.
- **Regional variety:** `繁體中文（香港）` output reads as standard Traditional
  Chinese with few Hong Kong-specific word choices. Every variant does this.

Reproduce (from `backend/`):

```powershell
scripts/run-eval.ps1 sweep --config benchmark/agent/translate_blocks.eval.toml
scripts/run-eval.ps1 pairwise --stage translate_blocks --a <exp> --b <exp> --corpus benchmark/agent/corpus/translate_blocks.core.jsonl --judge-model gemini/gemini-3.8-flash
```

The eval config lists only the fix-round-1 contenders (`v4`, `v7`, `v8`,
`v9`). To rerun a round-0 row, add its version (`v1`–`v6`) to `variants` in
`benchmark/agent/translate_blocks.eval.toml`. Round 0 ran on the earlier
12-block corpus, which commit `04b909f` replaced, so totals on the current
corpus will differ from the round-0 table.
