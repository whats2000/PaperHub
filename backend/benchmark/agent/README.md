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
  Hand-authored corpus cases may include a `history` field (a list of prior
  `{role, content}` turns) that is threaded into the prompt before the current
  user message, enabling anaphora-resolution cases like "推薦幾篇" or "tell me
  more about this one" to be evaluated with proper context. Harvested cases
  currently carry no history because the production router's trace does not
  record prior turns yet — so harvested anaphoric cases are replayed without
  context (a future tracer enrichment, gated by the no-deploy-change rule).
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

## Ideation eval set (Plan H3, SRS §III-10.3)

`corpus/ideation.core.jsonl` holds the 10 problem-mode cases;
`ideation.eval.toml` sweeps them with two free-exploration (`mode = "explore"`,
empty `problem`) sets as separate rows:

- `ideation.explore.jsonl`: one case over the **whole library** (the input of
  run 705, the user's explore that kept nothing), graded on **yield**:
  `expect.min_valid = 5`, score = kept ideas / 5, capped at 1.
- `ideation.explore_board.jsonl`: 9 labelled pairs, each on a **small board**
  (`expect.board_docs`, 11–12 cards: 7 shared distractors — surveys, a
  dataset, unrelated fields — plus 2–3 near misses or same-field traps per
  case), graded on the **ordered** `(source_doc, target_doc)` pair at recall@5.
  On the whole library a labelled pair is one of ~3,500, so recall there was 0
  for every variant; on a board it is one of ~100 valid pairs.
- `ideation.heldout.jsonl` (round 7): 13 problem-mode cases **held out from
  tuning**, whose expected sources are cards no other case labels (76, 56, 84,
  88/89, 44, 75, 40, 37/38/52/80, 43, 53, 74, 90; 41 also accepted where it
  fits). The four `heldout-far-*` cases phrase the problem with none of the
  card's vocabulary. Never tune a variant on this set; report it next to core.
- `ideation.repair.jsonl` (round 7): `generate_ideas`' **repair call**, made
  when every first-round proposal was dropped. `rejected_xml` holds the
  sentinel `@rejected_from_first_round`: the build step validates
  `expect.first_round` with the agent's own check (`_check`) and renders the
  drops with their reasons (`status="invalid"`) exactly as the agent does,
  failing if a first-round idea would have been kept. Five cases are graded
  on yield (`min_valid` 3: run 705's real same-domain pairs, same-field pairs
  on a robotics and a language board, problem-mode ideas with a `target_doc`
  or uncited sections) and two on recall@5 of the right source when only its
  fault was rejected.

Grade = **recall@5** (`stages._recall_at_5`): 1 when `expect.source_doc` (an id,
or a list of acceptable ids) is among the first 5 ideas' `source_doc`. A case
with no `source_doc` and no `min_valid` scores `None` and falls through to the
judge (whose request text is the `problem`). **Only kept ideas count:** the
build step writes `expect.board` (each card's domains, primary first, and cited
sections) and `expect.mode`, and the scorer drops what `generate_ideas` would
drop (unknown documents, uncited sections, a target whose primary domain is one
of the source's — `cross_domain_reason` — and duplicates) before taking the top
5. The replay also runs under the deploy output cap (`StageSpec.max_tokens` =
`PROPOSE_MAX_TOKENS`). Ship gate: no problem-mode regression against the shipped
variant **on the deploy model**, plus a `pairwise` win; explore must hold.

**Variables** are exactly what `generate_ideas` records on its `ideation:propose`
step: `problem`, `tried`, `cards_xml`, `rejected_xml`, `mode`.

**Cards are filled at sweep time, not committed by hand.** Every committed case
has `cards_xml = "@cards_from_db"` (`stages.CARDS_FROM_DB`), because the cards are
the card prompt's output and change with it. Before a sweep, a pre-sweep build
step reads `ideation.core.jsonl`, replaces the sentinel with the live
library's cards rendered the same way `generate_ideas` renders them, **minus the
ids in `expect.exclude_docs`**, and writes `corpus/ideation.core.cards.jsonl` —
the file `ideation.eval.toml` points at (a sweep fails loudly until it exists);
likewise for the two explore files. Explore cases have no `exclude_docs`: both
ends of the expected pair must be on the board.
`exclude_docs` always holds `expect.problem_doc` (the paper the problem text was
taken from); otherwise the model could just propose the target paper's own card.
`problem_doc` / `exclude_docs` are build metadata; the score ignores them.

The build step is `benchmark/agent/ideation_cards.py`. It reads the cards and
domains (read-only) from a DB, which should be a **copy** of the workspace whose
cards were built with the deploy card model. Run it from `backend/`:

```powershell
foreach ($t in "core", "explore", "explore_board") {
  uv run python -m benchmark.agent.ideation_cards --db <copy>/paperhub.db `
    --in benchmark/agent/corpus/ideation.$t.jsonl `
    --out benchmark/agent/corpus/ideation.$t.cards.jsonl
}
```

The filled files contain every document's card, private documents included, so
they are gitignored (`*.cards.jsonl`). The variants and results are in
`prompts/ideation/README.md`.

| case | problem from (`problem_doc`) | expected source | exclude |
|---|---|---|---|
| bert-masking-to-ijepa | 33 I-JEPA | 68 BERT | 33, 34 V-JEPA, 35 V-JEPA 2 (SRS names I-JEPA/V-JEPA as the target side) |
| shortcut-to-grits | 41 GRITS | 32 Shortcut Models | 41 |
| image-diffusion-to-mdlm | 71 MDLM | 32 or 36 (any in-library image-diffusion paper) | 71, 31 Discrete Flow Matching (SRS target side) |
| expert-choice-to-adp | 61 Action-aware Dynamic Pruning | 47 Expert Choice routing | 61 |
| align-to-cross-embodiment | 45 Cross-Embodiment Latent Space Alignment | 42 ALIGN | 45 |
| mamba-to-dual-memory | 63 OptimusVLA (dual memory) | 87 Mamba | 63 |
| tokenization-to-faster | 62 FASTer | 94 BPE subword units | 62 |
| model-collapse-to-groot | 25 GR00T N1 | 22 Model collapse | 25 |
| user-anchor-reranker | — (user's example) | 92 Faster R-CNN | — |
| user-icl-vla-prompts | — (user's example) | 93 GPT-3 | — |

Explore boards (expected ordered pair source → target; shared distractors 20
LIVECell dataset, 39 Isaac Sim benchmark, 49 multi-cloud survey, 50 backend
security, 77 VR teleoperation, 85 LLMs-for-power-systems survey, 92 Faster
R-CNN). 88 (ghost imaging) was a distractor until its card turned out to cite
no section (a short letter with none); it was left off the boards, and since
round 7 a sectionless document cites its body as the canonical "Full text"
section, so 88 is on the whole-library sets again. The pairs are the problem-mode pairs above wherever
the source's domains do not include the target's primary domain, plus three:

| case | pair | near misses / traps on the board |
|---|---|---|
| explore-board-ssm-to-dual-memory | 87 Mamba → 63 OptimusVLA | 52 Transformer, 15 MolmoAct (same field as 63) |
| explore-board-contrastive-to-latent-alignment | 42 ALIGN → 45 | 68 BERT, 16 X-VLA (same field as 45) |
| explore-board-masked-to-ijepa | 68 BERT → 33 I-JEPA | 34 V-JEPA (same field as 33), 94 BPE, 24 YOLOSeg |
| explore-board-collapse-to-groot | 22 model collapse → 25 GR00T N1 | 90 assistive robotics, 89 sparse imaging |
| explore-board-long-context-to-alphagenome | 81 efficient transformers or 87 Mamba → 66 AlphaGenome | 83 GRAPE |
| explore-board-in-context-to-xvla | 93 GPT-3 → 16 X-VLA (its limitation: embodiment-specific fine-tuning) | 94 BPE, 62 FASTer (same field as 16) |
| explore-board-shortcut-to-grits | 32 shortcut models → 41 GRITS | 36 text-to-image survey, 79 EfficientVLA (same field as 41) |
| explore-board-expert-choice-to-adp | 47 expert choice → 61 ADP | 48 loss-free balancing, 15 MolmoAct (same field as 61) |
| explore-board-bpe-to-faster | 94 BPE → 62 FASTer | 52 Transformer, 17 WALL-OSS (same field as 62) |

Resolution notes:

- **Problem text** paraphrases the target paper's own problem statement (abstract
  / introduction) as an engineer would write it, without naming the mechanism.
  GRITS's stated problem is spillage; the SRS pair is about few-step sampling, so
  its text is the latency of its own guided denoising against its 10 Hz
  controller, phrased without hinting at a fix (no step counts or step-cutting).
- **"image diffusion"** names a class, not one work, so either in-library image
  diffusion paper counts (32 Shortcut Models, 36 the RenAIssance text-to-image
  survey with its diffusion background section).
- **"sim-to-real synthetic-data loops"** names no paper. GR00T N1 (25) is used
  because its own problem is a training mix dominated by simulated and
  video-model-generated data; 40 (Isaac Sim → ROS 2) is physics-sim transfer
  with no generated-data loop.
- **"language tokenization"** had no paper in the library, so BPE (Sennrich et
  al. 2016, "Neural Machine Translation of Rare Words with Subword Units",
  arXiv 1508.07909) was ingested for it as 94.
- The two user examples were ingested for H3 (Faster R-CNN arXiv 1506.01497 =
  92, GPT-3 arXiv 2005.14165 = 93).

The ids were resolved by title from the live DB, read-only (run from `backend/`):

```python
import sqlite3
db = sqlite3.connect("file:workspace/paperhub.db?mode=ro", uri=True)
for like in ("BERT: Pre-training%", "Self-Supervised Learning from Images with a Joint-Embedding%",
             "Revisiting Feature Prediction%", "V-JEPA 2%", "One Step Diffusion via Shortcut%",
             "GRITS%", "Simple and Effective Masked Diffusion%", "Discrete Flow Matching%",
             "RenAIssance%", "Mixture-of-Experts with Expert Choice%", "Action-aware Dynamic Pruning%",
             "Scaling Up Visual and Vision-Language Representation%", "Cross-Embodiment Robot Manipulation%",
             "Mamba: Linear-Time%", "Global Prior Meets Local Consistency%", "FASTer: Toward%",
             "AI models collapse%", "GR00T N1%", "Faster R-CNN%", "Language Models are Few-Shot%",
             "Neural Machine Translation of Rare Words%"):
    print(like, db.execute("SELECT id, title FROM paper_content WHERE title LIKE ?", (like,)).fetchall())
```
