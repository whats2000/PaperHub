# ideation_card — mechanism-card prompt variants (Plan H3 Task 3)

The card prompt turns one document's cached PaperDigest (title, abstract,
`<sections>` of per-section insights) into a cited, **domain-free**
`problem / mechanism / assumptions / limitations / domains` card. Shipped as
`src/paperhub/llm/prompts/ideation_card_v1.yaml` = **v5** (pinned by
`tests/benchmark_agent/test_ideation_card_stage.py`).

## Set-up

- **Corpus** `corpus/ideation_card.core.jsonl`: 8 documents from the H3 ideation
  eval set (SRS §III-10.3) — BERT 68, Shortcut Models 32, Expert Choice 47,
  ALIGN 42, Mamba 87, BPE 94, Model collapse 22 (a *finding* paper), ADP 61 (a
  problem-side robotics paper). Variables are exactly what `build_card` renders
  (`cards.card_variables`). Digests were built with the deploy digest model
  (`gemini/gemini-3.8-flash` = `report_section_model`) on a **copy** of the
  workspace DB; 47 and 61 reused their cached digests.
- **Model** `gemini/gemini-3.5-flash-lite` (deploy small tier), 2 reps,
  `ideation_card.eval.toml`.
- **Deterministic gate** `stages.card_failures`: problem + mechanism each cite
  ≥ 1 section, every cited name is a digest section, ≥ 1 domain.
- **Judge** `pairwise` (order-swapped, a win only when both orders agree),
  judge model `gemini/gemini-3.8-flash`, rubric `STAGE_RUBRICS["ideation_card"]`:
  faithfulness → domain-free problem/mechanism → citations → concision.
- In-prompt examples (ResNet, Scaling Laws) are not eval documents (tested).

## Variants

| v | change | tokens in |
|---|---|---|
| v1 | baseline: direct rules, no examples | 920 |
| v2 | domain-free rule made concrete (no field nouns / method names, describe the data shape) + one ResNet bad→good example | 1204 |
| v3 | v2 + a second example for a *finding* document (Scaling Laws: mechanism = the causal process) | 1335 |
| v4 | v2 + domains = the document's own field (not where it could apply) + keep the data true ("two kinds of input", not "two sequences") | 1245 |
| v5 | v3 + v4's two rules | 1376 |

## Results

Round 1 (response model `MechanismCard`): the contract failed on 47 / 48 cards —
the model **omitted `domains`** because the field has a default and native
structured output drops optional fields. Fix in code, not prompt: the LLM call
now uses `cards.CardDraft` (every field required, 1–3 domains). Round 2 below.

Deterministic gate (round 2): **1.00 for every variant** (16 / 16 cards each).

Pairwise (A vs B, 16 = 8 docs × 2 reps):

| A | B | A wins | B wins | ties |
|---|---|---|---|---|
| v1 | v2 | 1 | 12 | 3 |
| v1 | v3 | 2 | 12 | 2 |
| v2 | v3 | 4 | 9 | 3 |
| v1 | v4 | 1 | 14 | 1 |
| v2 | v4 | 5 | 4 | 7 |
| v3 | v5 | 3 | 8 | 5 |
| v4 | v5 | 3 | 9 | 4 |
| **v1** | **v5** | **1** | **14** | **1** |

**Winner: v5** — beats the baseline 14-1-1 and every other variant it met.
v4's rules alone are a wash against v2 (5-4-7); combined with v3's finding
example they win (v3 → v5 8-3-5). Reading the outputs: v1 keeps field jargon
("masked language modeling", "visual tokens", "robotic manipulation"); v2+ phrase
the mechanism transplantably ("let each processing unit select its top items
instead of items selecting units"); the finding example is why v3 beats v2 on
model collapse on both reps; the own-field rule narrows domains (v4 tags BERT
and Expert Choice with NLP only, v5 BERT) but not fully — Shortcut Models still gets
"Robotics" (its discussion mentions robotics) and ADP "Reinforcement Learning"
on one rep.

Known residue (flash-lite): one BERT rep of v5 still says "masked language
modeling and next-sentence prediction" in the mechanism; the ideation proposer
(Task 5) reads cards, so this costs recall only if it hides the analogy.

## Round 3: domains (QA I8, 2026-11-07)

The QA pass found 29 domains for 61 papers: near-duplicates ("Audio" /
"Audio Processing", "Genomics" / "Functional Genomics"), up to three tags
that list every field a method was benchmarked on (Mamba: NLP | Genomics |
Audio), surveys tagged "Reinforcement Learning" and the dual-memory VLA paper
tagged differently from its siblings. Code now folds the variants through the
alias map and keeps at most two tags (`ideation.domains`); the prompt picks
the primary field.

| v | change | tokens in |
|---|---|---|
| v6 | v5 + domains: 1–2 from a fixed field list, primary first; a survey's field is the surveyed field; a general method is "Machine Learning" first; RL only when it learns from rewards | 2710 |
| v7 | v6 + three domain examples (a general optimizer, a survey, an imitation-trained "policy") | 2782 |

Set-up: new bucket `corpus/ideation_card.domains.jsonl`, 16 documents (the 8
core ones plus the two surveys 81 and 57, the VLA siblings 63 and 62,
AlphaGenome 66, VR teleoperation 77, LIVECell 20, ghost imaging 89), each
labelled with acceptable primary fields and allowed fields. Graded on what
`assign_domains` stores (canonical names, at most two) plus the card
contract. Deploy card model `gemini/gemini-3.5-flash-lite`, 2 reps,
concurrency 1.

| v5 (exp 322) | v6 (323) | **v7 (324)** |
|---|---|---|
| 0.75 | 0.81 | **0.94** |

- v5 misses: Mamba keeps a genomics field, the MoE survey is CV-first, the
  dual-memory VLA gets RL on one rep, BPE gets "Neural Machine Translation".
- v6 fixes those but adds "Machine Learning" as the second tag of the VLA and
  imaging papers (the "general method" rule over-applies).
- v7's examples fix that; its only miss is ghost imaging + "Machine Learning"
  (both reps).

Pairwise v5 vs v7 (judge `gemini/gemini-3.8-flash`, order swapped): **v7
17–10–5** (6 deterministic, judge 11–10), so the card wording does not
regress. **Shipped v7 as `ideation_card_v2.yaml`**; the slot bump makes
`paperhub-backfill-cards` rebuild every card and re-tag its domains.

Live run (61 cards rebuilt with v2 on the deploy model, then
`paperhub-consolidate-domains`): v2 named "Machine Learning" as the second
field of 40 of 61 papers, which merges every hull on the graph. Code now keeps
"Machine Learning" only as a primary field (`domains.stored_tags`, mirrored by
the eval's grading); re-scored on the stored outputs, v6 and v7 both reach
1.00 on the domains bucket (v5 stays 0.75). After the migration the library
has 14 domains in use (was 29): NLP 25, Robotics 22, CV 17, ML 9 (general
methods and surveys), RL 3, then two-paper and singleton fields. Known
residue: the ghost-imaging letter (88) gets "Information Retrieval" second.
