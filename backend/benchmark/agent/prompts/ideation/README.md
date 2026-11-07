# ideation — proposer prompt variants (Plan H3 Task 5)

The proposer reads every library document's mechanism card (`<cards>`) and
returns up to 8 ranked ideas (`IdeationOutput`): `problem ← mechanism` in
problem mode, `document → document` across domains in explore mode. Shipped as
`src/paperhub/llm/prompts/ideation_propose_v3.yaml` = **v18** since round 8
(`ideation_propose_v2.yaml` = v12 and `ideation_propose_v1.yaml` = v11 stay for
diffing; v8 before round 6; pinned
by `tests/benchmark_agent/test_ideation_prompt.py`). Rounds 1–5
below ran on `gemini-3.8-flash`, which is **not** the deploy model; round 6 is
the first on the deploy model and supersedes their verdict.

## Set-up

- **Corpus**: the 10 labelled problem-mode cases (`ideation.core.jsonl`) and the 2
  explore cases (`ideation.explore.jsonl`), filled with
  `benchmark/agent/ideation_cards.py` (see the agent README, "Ideation eval set").
- **Cards**: built on a **copy** of the workspace
  (`.superpowers/tmp/ws-copy`). It is a sqlite backup of the DB plus each
  document's `asset/` and `digest.json`, with `source_dir_path`/`source_path`
  rewritten to the copy. Nothing was written to `backend/workspace`.
  - Missing digests were built with the deploy digest model `gemini/gemini-3.8-flash`.
  - Cards were built with `build_card` and the deploy card model
    `gemini/gemini-3.5-flash-lite` (`ideation_card/v1`).
  - Cards were built for 60 of 61 documents. Document 57 (the MoE survey) has no
    card: `read_paper_asset` raises `FigureAsset.__init__() got an unexpected
    keyword argument 'kind'`, so it is skipped, as it would be in production.
  - Each filled `cards_xml` is about 10.9k tokens.
- **Model**: `gemini/gemini-3.8-flash`. This is the deploy ideation model
  (`PAPERHUB_IDEATION_MODEL` defaults to the flagship tier). The sweep runs at
  2 reps, temperature 0 and backend concurrent.
- **Grade**: `recall@5`, meaning the expected source (explore: the ordered pair)
  is among the first 5 ideas.
- **Gate**: ≥ 7 of 10 core cases, plus a pairwise win over the baseline. The
  explore bucket is reported but not gated.
- **Pairwise judge**: `gemini/gemini-3.8-flash` with the order swapped; a win
  counts only when both orders agree. Rubric: `STAGE_RUBRICS["ideation"]`, which
  ranks fit, then cross-domain value, then grounding, then ranking and variety.
  - A deterministic recall miss always loses.
  - Explore was also judged on an unlabelled copy of the explore corpus. Every
    variant misses the explore recall, so the deterministic check alone would
    score all explore comparisons as ties.
- **Examples avoid the eval mechanisms.** A test checks this: no masking,
  anchors, few-shot, subword, state space, shortcut or contrastive. The examples
  are a vibration-sensor drift problem (finance EWMA), Simpson's paradox (a
  finding card), and per-block bounds for ray tracing (explore).

## Variants

| v | change | tokens in |
|---|---|---|
| v1 | baseline: direct rules, no examples, no workflow | 10 860 |
| v2 | v1 + numbered workflow (abstract the problem's shape → scan every mechanism → prefer distant fields) + one example | 11 193 |
| v3 | v2 + explore example + "fill 8 slots, one per source" | 11 299 |
| v4 | v1 + finding cards count when the user's plan would trigger the failure + explore ranks by fit, not distance, and skips survey/benchmark/dataset cards | 10 981 |
| v5 | v4 + examples for the three ambiguous cases (distant mechanism, finding card, explore pair) | 11 247 |
| v6 | v5 + cross-field quota: at most 2 of the first 5 problem-mode ideas from the problem's own field | 11 290 |
| v7 | v6 + explore workflow (start from target cards' concrete problems) + "one section name, never the whole cites list" | 11 371 |
| v8 | v6 + the rationale describes the mechanism in words, never a doc id | 11 307 |
| v9 | v8 + the explore rule matches `generate_ideas` (the target's primary domain is new to the source, not "share no domain") + the quota without the silent "first name the field" step + `<mode>` tag | 11 352 |
| v10 | v9 + explore: start from a target card's stated bottleneck (problem or limitation) | 11 388 |
| **v11** | v10 with the quota replaced by a preference: another field's card wins only at equal fit | 11 387 |

## Results: core recall@5 (mean over 10 cases × 2 reps, per sweep run)

| round | v1 (baseline) | variants |
|---|---|---|
| 1 | 0.90 | v2 0.75 · v3 0.80 |
| 2 | 0.85 | v4 0.80 · v5 0.90 |
| 3 | 0.75 | v5 0.90 · v6 **1.00** |
| 4 | 0.85 | v6 **1.00** · v7 0.85 |
| 5 | 0.85 | v6 0.95 · v8 **1.00** |
| replicate | — | v8 0.95 |

The **gate is met.** v8 put the expected source in the top 5 on 39 of 40 reps:
10/10 cases in round 5, and 10/10 in the replicate with one rep miss
(shortcut-to-grits r0, rank 6). The baseline v1 also passes the 7-of-10 bar
(0.75–0.90) but is unstable.

Rank of the expected source per case (each number is one rep; `-` = not in the 8):

| case | v1 (5 runs) | v8 (2 runs) |
|---|---|---|
| bert-masking-to-ijepa | 1 1 2 1 1 1 2 1 1 1 | 1 1 · 1 1 |
| shortcut-to-grits | 1 1 1 1 1 1 1 1 1 1 | 1 2 · 6 2 |
| image-diffusion-to-mdlm | 2 1 1 1 2 2 1 1 1 1 | 1 3 · 3 3 |
| expert-choice-to-adp | 4 5 5 5 5 - 5 4 5 4 | 3 2 · 1 3 |
| align-to-cross-embodiment | 4 4 - - - 6 5 6 5 - | 1 1 · 1 2 |
| mamba-to-dual-memory | 1 × 10 | 1 1 · 1 1 |
| tokenization-to-faster | 1 2 1 2 2 2 1 2 2 2 | 1 1 · 1 1 |
| model-collapse-to-groot | - - - 5 6 - - - - - | 1 1 · 1 1 |
| user-anchor-reranker | 1 × 10 | 1 2 · 2 2 |
| user-icl-vla-prompts | 1 × 10 | 1 1 · 1 1 |

(v8 column: round 5 · replicate.)

**Explore recall@5 (not gated): 0.00 for every variant in every round.** The one
exception is v6 in round 5, at 0.25, when 42 → 45 was found at rank 4.
- The expected pairs are two out of ~3,500 cross-domain pairs.
- v8 finds near misses, such as Mamba 87 → MolmoAct 15 (a VLA, but not
  OptimusVLA 63), and pairs such as Shortcut → MDLM and BPE → cross-embodiment.
- The baseline's top explore pairs were domain-distant but loose: load
  balancing → a multi-cloud survey, Isaac Sim → an LLM-for-power-systems survey.

Validation pass-through: `generate_ideas`' validator was replayed over every
v8 idea using the copy's cards and domains. Every idea was valid: 142/142 in
round 5, 139/139 in the replicate and 32/32 in explore. Recall@5 after
validation is the same as before.
- Earlier variants copied the whole cites list (`"A; B"`) into `source_section`
  on about 10 % of ideas (v1 18 of 121 in round 5).
- The agent now resolves such a list to the first name the card cites (commit
  `fix(ideation): accept a copied cites list…`). v8 does this on 7 of 142 ideas
  in core and 0 in explore.
- v6's explore rationales said "Doc 87's …" on 7 of 32 ideas; v8 has 0.

## Pairwise (A = v1 baseline, B = variant)

| B | run | corpus | A wins | B wins | ties |
|---|---|---|---|---|---|
| v5 | round 2 | core | 6 | 8 | 6 |
| v5 | round 3 | core | 8 | 8 | 4 |
| v6 | round 3 | core | 9 | 11 | 0 |
| v6 | round 4 | core | 10 | 8 | 2 |
| v6 | round 4 | explore (judge) | 1 | 3 | 0 |
| **v8** | round 5 | core | **9** | **11** | 0 |
| **v8** | round 5 | explore (judge) | **0** | **4** | 0 |

**Winner of rounds 1–5 (gemini-3.8-flash): v8.** It has the best recall (1.00 and 0.95, against 0.75–0.90 for
the baseline), and it beats the baseline pairwise: 11–9 on core and 4–0 on
explore, 15–9 in total. The core margin comes from recall: v8 wins 3 cases
deterministically, and on the cases both variants hit the judge splits 8–9.

Reading the outputs:
- The cross-field quota (v6/v8) is what fixes the two cases the baseline
  misses. It moves ALIGN's contrastive dual encoder and the model-collapse
  finding into the top 2, where the baseline led with in-field robotics cards
  (X-VLA soft prompts, cross-embodiment goal reaching).
- The cost is visible on shortcut-to-grits. v8 sometimes fills top-5 slots with
  looser distant ideas (an RL critic, sparse experts) in place of the in-field
  feature caching. That is why the judge slightly prefers v1 on cases both hit.
- v2/v3's "distant field beats nearby" wording without a quota lowered recall;
  the model then over-weighted far fields everywhere.
- v7's explore workflow did not help explore and cost 0.15 core recall.

## Round 6: the deploy model, the real library, explore with evidence

Run 705 (the user's explore) kept no idea. Read from its trace: the deploy
ideation model is **`gemini/gemini-pro-latest`** (the flagship in the settings
DB; `PAPERHUB_IDEATION_MODEL` is unset), a thinking model that spent the
8192-token cap on reasoning and truncated its JSON (reproduced 2/2; 2/2 valid at
32768 with 7.0k and 10.2k reasoning tokens), so the call fell back to the small
tier, whose 3 pairs `generate_ideas` then dropped as "same domain". Fixed in
code (cap 32768, primary-domain cross-field rule, one repair round, uncitable
cards off the board); the eval now replays the cap and the validator.

Set-up: cards from a fresh copy of the live DB (61 documents, all card-ready;
88 is off every board because its card cites no section), `gemini-pro-latest`,
2 reps, concurrency 4. Sets: core (10), explore = the whole library graded on
yield toward 5 kept ideas, explore_board = 9 labelled pairs on 11–12-card
boards graded on pair recall@5 among kept ideas. Round 6a ran with paper 88
still on the boards and 57 without a card; 6b after both fixes.

| v | core 6a | core 6b | core total | explore yield (6a · 6b) | board 6a | board 6b | board total |
|---|---|---|---|---|---|---|---|
| v8 (shipped) | 13/20 | 12/20 | 25/40 | 1.00 · 1.00 | 7/18 | 4/18 | 11/36 |
| v9 | 14/20 | 15/20 | 29/40 | 1.00 · 1.00 | 6/18 | 7/18 | 13/36 |
| v10 | 15/20 | 12/20 | 27/40 | 1.00 · 1.00 | 5/18 | 8/18 | 13/36 |
| **v11** | 15/20 | **17/20** | **32/40** | 1.00 · 1.00 | 7/18 | 7/18 | **14/36** |

Pairwise (judge `gemini-3.8-flash`, order swapped, round 6b; A = v8):

| B | corpus | A wins | B wins | ties |
|---|---|---|---|---|
| v10 | core (6a) | 5 | 9 | 6 |
| **v11** | core | **2** | **13** (5 deterministic, 8 judge) | 5 |
| **v11** | explore_board | **2** | **7** | 9 |
| **v11** | explore (whole library) | 1 | 0 | 1 |

**Winner: v11** (20–5 over v8 in total). Reading the outputs:
- The old gate's 39/40 does not hold on the deploy model: v8 scores 25/40 there.
  Its quota made `gemini-pro-latest` fill the top 5 with loose distant ideas
  (BPE merges for ViT pretraining, a routing paper for cross-embodiment
  transfer); v11's "another field wins only at equal fit" recovers 7 of those.
- v11 proposes fewer ideas in problem mode (114 vs 153 over 20 runs, about 6 per
  run); every explore run still kept ≥ 7.
- Every variant keeps ≥ 5 ideas on the whole library once the cap and the
  cross-field rule are fixed, so explore yield no longer separates prompts; the
  boards do. The board misses are mostly loose look-alike pairs ranked above
  the labelled one (e.g. a representation-collapse fix for the model-collapse
  card), never invalid ones (0–4 of ~125 proposals per variant were dropped).
- Live check after shipping v8 + the code fixes: run 779, 8 new ideas, 0
  dropped, no model fallback.

## Round 7: held-out set, repair round, board ranking, reasoning budget

Set-up: `gemini/gemini-pro-latest` only (the flagship in the settings DB;
`PAPERHUB_IDEATION_MODEL` unset), cards from a fresh copy of the live DB after
the "Full text" migration (61 documents, 88 now on the whole-library sets),
concurrency 4, 2 reps unless noted, `backend = "concurrent"`. Each cell is a
score sum over runs, with the experiment id from `results/experiments.jsonl`
in brackets. Two new sets (see the agent README): **heldout** (13
problem-mode cases, never tuned on) and **repair** (7 replays of
`generate_ideas`' repair call).

| v | change |
|---|---|
| v12 | v11 + `<rejected_ideas>` semantics: dismissed/failed ideas never return; an `invalid` one (the repair round's drops) may return with its fault fixed |
| v13 | v12 + use a card's mechanism as stated; an idea that needs an added step or a recast mechanism is a loose fit |
| v14 | v13 + one example of such a loose fit |
| v15 | v12 + explore: each pair once, one direction; cards that only survey / categorize / benchmark / measure / collect data are left out |
| v11t2k, v12t2k, v12t4k | the same prompt with `params: thinking` = 2048 / 2048 / 4096 reasoning tokens |

### Held-out vs tuned recall@5

| set | v11 | v12 | v13 | v14 |
|---|---|---|---|---|
| heldout (13 cases) | **26/26** (224); first 9 cases 18/18 (214) | 26/26 (225) · 26/26 (277) | 26/26 | 26/26 |
| core (tuned, 10 cases) | 14/20 (219) · 17/20 (232) · 19/30 (254) = **50/70** | 13/20 (233) · 22/30 (262) · 13/20 (273) = **48/70** | 15/20 | 14/20 |

**No overfitting defect:** v11's held-out recall@5 is 1.00 (26/26), above its
tuned-set recall (0.71 this round, 32/40 in round 6). Every held-out case put
the expected source at rank 1 in every rep, the four `heldout-far-*` cases
(no shared vocabulary) included. Caveat: the held-out problems were written
from their source cards' own problem statements, so they are easier than the
core problems, which paraphrase the *target paper* and need a cross-field
analogy. Core recall varies by about ±3 per 20 between identical runs (v11:
14, 17, 19/30), so v11 vs v12 on core (50/70 vs 48/70) is a tie; the judge
agrees (below).

### Repair round (7 cases: 5 graded on yield toward 3 kept ideas, 2 on recall of the fixable source)

| v | repair | notes |
|---|---|---|
| v11 | 10/14 (228) | explore cases 6/6, problem-mode yield 4/4, **recall cases 0/4**: it never re-proposed Faster R-CNN (rejected only for its `target_doc`) or GPT-3 (rejected only for an uncited section); it read every `<rejected_ideas>` entry as "never again" |
| **v12** | **14/14 (229) · 14/14 (279)** | both sources come back at rank 1 with the fault fixed |
| v13 / v14 | 13.7/14 · 14/14 | |

The repair round works on the deploy model for every variant on the explore
cases (6–8 kept pairs per run, none same-domain); run 705's real input yields
6–8 kept pairs. The one defect was the rejected-ideas wording; v12 fixes it.

### Boards (explore_board recall@5, 9 labelled pairs)

| v11 | v12 | v13 | v14 | v15 |
|---|---|---|---|---|
| 8/18 (222) · 9/18 (236) · 11/27 (240) = 28/63 | 9/18 (237) · 11/27 (241) · 10/18 (284) = 30/63 | 5/18 | 9/18 | 5/27 |

**Board recall is at a ceiling of about 45% for prompt changes.** Evidence:
- Both targeted fixes regressed: v13's "use the mechanism as stated" (5/18)
  and v15's explicit one-direction / no-survey rule (5/27). Naming surveys and
  benchmarks made the model pair them *more* often (39 → 85 appeared in the
  top 5 of 10 of 27 v15 runs).
- Rule violations are not what pushes the labelled pair out. Re-scoring the
  stored v11/v12 board outputs with an oracle that drops reverse duplicates
  (A → B after B → A) and every survey / benchmark / dataset card (20, 36, 39,
  49, 50, 81, 85) changes recall by −1 to +2 per 18 runs.
- Most misses are other valid, often defensible pairs: shortcut models → the
  EfficientVLA card (79, whose iterative-generation redundancy is the same
  bottleneck) at rank 1 in all 7 v12 runs instead of → GRITS (41); BPE → WALL-OSS (17)
  instead of → FASTer (62); self-attention → Faster R-CNN. These are the
  near-miss cards the boards were built with, so recall@5 of one labelled
  pair under-counts good output. A better board metric needs graded labels
  (several acceptable pairs per board) or the judge, not prompt work.

### Pairwise (judge `gemini-3.8-flash`, order swapped; a win counts only when both orders agree)

| A | B | corpus | A wins | B wins | ties |
|---|---|---|---|---|---|
| v11 (228) | **v12** (229) | repair | 1 | **9** (4 deterministic) | 4 |
| v11 (254) | v12 (262) | core | 9 | 10 | 11 |
| **v12** (273) | v12t2k (274) | core | **8** | 6 | 6 |
| **v12** (277) | v12t2k (278) | heldout | **10** | 5 | 11 |

**Winner: v12**, shipped as `ideation_propose_v2.yaml`: 14/14 vs 10/14 on
repair and 9–1 judged there, ties on core (48/70 vs 50/70; judged 10–9),
held-out (26/26 both) and boards (30/63 vs 28/63).

### Reasoning budget (Gemini `thinkingBudget` via LiteLLM `thinking`)

Means per call (latency is the eval's wall clock per call at concurrency 4):

| set | unbounded: score · reasoning tokens · latency | 2048 | 4096 |
|---|---|---|---|
| core | v12 13/20 · 3.4k · 30 s (273) | 14/20 · 1.8k · 22 s (274) | — |
| heldout | v12 26/26 · 2.1k · 20 s (277) | 26/26 · 1.2k · 16 s (278) | — |
| repair | v12 14/14 · 4.6k · 34 s (279) | 14/14 · 2.7k · 27 s (280) | — |
| explore_board | v11 8/18 · 7.2k · 48 s (222); v12 10/18 · 7.1k · 48 s (284) | v11t2k **1/18** · 2.7k · 25 s (223) | v12t4k **4/18** · 3.2k · 27 s (285) |
| explore (whole library, yield) | v12 2/2 · 7.7k · 52 s (281) | — | 2/2 · 3.6k · 33 s (282) |

**No bound is shipped by default.** In problem mode 2048 keeps recall and
saves about 45% of the reasoning tokens and 25% of the latency, but it
proposes fewer ideas (4.2 vs 5.0 per core run, 2.6 vs 3.5 held-out) and loses
the judged comparison 11–18. In free exploration any bound collapses board
recall. The code takes the budget as an opt-in setting,
`PAPERHUB_IDEATION_PROBLEM_THINKING_BUDGET` (default 0 = unbounded; explore is
never capped), and every LLM call's prompt / completion / reasoning tokens and
latency are now recorded on its trace step (`_llm_usage`, `token_in` /
`token_out`). The ~60 s / 7–10k-token runs are free exploration over the whole
library; problem-mode runs spend 2–5k.

### The two steady problem-mode misses (read from the stored outputs)

- **bert-masking-to-ijepa** (BERT, 68). Across the deploy-model runs (exps
  135–147, 219, 232–234, 254, 262, 273) BPE (94) leads most runs, then ALIGN
  (42) or MDLM (71); BERT is at rank 2–4 when it appears and missing in about
  half the runs. The BPE rationales recast a vocabulary-compression method as
  a prediction objective ("compress patches into sub-units and train the
  encoder to predict them"), a step the card does not describe, and ALIGN's
  contrastive loss is close to what the user already tried. v13's "use the
  mechanism as stated" rule removed BPE but removed BERT too (masking an image
  is also a recast), leaving 1–2 ideas: no fix.
- **align-to-cross-embodiment** (ALIGN, 42). Every variant ranks the
  in-library cross-embodiment methods first: the shared sensory-change action
  space (60) and X-VLA's soft prompts (16), then MoE routing (74) and V-JEPA
  2's latent planning (35); ALIGN lands at rank 5–7. 60 and 16 solve the
  stated problem directly, so ranking them first follows the prompt's "never
  trade a direct fit for distance". The miss comes from the label preferring
  the cross-field answer, not from a ranking error a prompt rule can fix
  without breaking that rule (v6's cross-field quota did fix it, and cost 7
  other cases on this model in round 6).

Neither is fixed: no variant moved them without a regression elsewhere.

## Limits of this eval

- **Overfitting risk.** All variants were tuned on the same 10 core cases.
  Round 7 added a 13-case held-out set (26/26 for v11 and v12), so no
  overfitting shows, but its problems are easier than core's (see round 7).
- **`rejected_xml` from real dismissals is still untested.** The repair set
  covers the `invalid` entries `generate_ideas` writes itself; dismissed and
  failed ideas are only enforced deterministically (`generate_ideas` drops a
  repeat, pinned in `tests/test_ideation_agent.py`). Add cases once real
  dismissals exist.

## Round 8: QA findings — language, relevance gate, named sources (2026-11-07)

The QA pass found: a zh-TW problem got English ideas (I1); "bake better
sourdough bread" got 8 confident ideas citing BERT and Mamba (I2); rationales
never named the paper the mechanism came from (I3); re-runs restated existing
ideas (I9). `generate_ideas` now passes `response_language` (empty in problem
mode = the problem's language; the client's UI language in explore) and
`existing_xml` (the scope's live ideas), and the call's schema
(`ProposeDraft`) has a required `no_fit_reason`.

| v | change |
|---|---|
| v16 | v12 + write in `<response_language>` (else the problem's language); no ideas + `no_fit_reason` when nothing fits; the rationale names the mechanism and its paper; do not restate `<existing_ideas>` |
| v17 | v16 + examples (a rationale, a Japanese problem, a no-fit library) |
| v18 | v16 + a stricter gate: a fit transplants the card's mechanism (a computation, model or data process), not a life principle it resembles |
| v19 | v18 + the no-fit example |
| v20 | v18 with the gate scoped to problem mode |

New bucket **qa** (`corpus/ideation.qa.jsonl`, 8 cases): the tester's zh-TW
problem, zh-TW and zh-CN translations of two core cases (labelled sources),
three irrelevant problems (sourdough, a barking dog, a zh-TW garden question:
score 1 only for no kept idea plus a reason in the right language), the
tester's English hallucination problem, and a zh-TW free exploration. Ideas
are also scored on the language (`stages.in_language`) and on the share whose
rationale names its source paper (`stages.names_source`).

Set-up: cards from the live DB after the card v2 rebuild, deploy model
`gemini/gemini-pro-latest`, 1 rep per run, concurrency 1. Experiment ids in
brackets.

| set | v12 | v16 | v17 | **v18** | v19 | v20 |
|---|---|---|---|---|---|---|
| qa | 0.07 (325) | 0.75 (326) | 0.66 (327) | **1.00** (337) | 0.88 (338) | 1.00 (347) |
| core | 0.80 (328) | 0.90 (329) | 0.80 (330) | **0.90** (339) | 0.70 (340) | 0.80 (348) |
| repair | 1.00 (331) | 0.95 (332) | 0.95 (333) | **1.00** (341) | 1.00 (342) | — |
| explore (yield) | 1.00 | 1.00 | 1.00 | — | — | — |
| explore_board | 0.78 (344) · 0.78 (350) | — | — | **0.56 (343) · 0.89 (349)** | — | 0.67 (346) |
| heldout | 26/26 (round 7) | — | — | **1.00 (345)** | — | — |

- v12 answers every irrelevant problem and writes English for every Chinese
  problem. v16 fixes language and naming but still answers sourdough and the
  dog with metaphors ("track fermentation with a parallel dough sample" from
  an XR teleoperation paper). v17's examples taught it to describe papers
  ("the recursive-generation paper") instead of naming them and gave a false
  no-fit on image-diffusion-to-mdlm. v18's "life principle is not a fit"
  sentence fixes both irrelevant English cases with no false no-fit on core.
  v19's example brings the false no-fit back (core 0.70) and misses the dog.
- Boards: 13/18 for v18 vs 14/18 for v12 over two reps (the round-7 ceiling
  is ~45–60%); v18 keeps fewer explore pairs per board (2–8 vs 6–8). v20
  scoped the gate to problem mode to test that; it did not beat v18.
- Pairwise core, v12 (328) vs v18 (339), judge `gemini/gemini-3.8-flash`
  order-swapped: **v18 9–1**.

**Winner: v18**, shipped as `ideation_propose_v3.yaml`.
