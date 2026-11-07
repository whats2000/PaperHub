# paper_qa_subagent variants

The per-paper paper_qa subagent (`agents/paper_qa_subagent.py`) is a
tool-using loop (`list_sections` → `read_section` → cited summary), so the
single-call sweep engine cannot replay it. These variants were compared by
running the real `run_paper_qa_subagent` loop, one paper at a time and one
call in flight, on the deploy model (`gemini/gemini-3.5-flash-lite`, the
`paper_qa_subagent_model`), with the deploy read budget (8), against the live
library read-only, and grading which sections each run read plus its summary.

## Why (run 892)

In an idea chat about porting GISC sparsity constraints (paper 89) into
YOLOSeg low-contrast defect segmentation (paper 24), the user asked
「給我更詳細實驗方法」. The subagents only saw the idea block and the bare
rewrite 「請給我更詳細的實驗方法內容」, so each summarised its own paper's
method and the answer described the two papers separately (marked
off_topic). v2 gives the subagent the last exchange in `<recent_conversation>`
(`agents/recent_conversation.py`: previous user message ≤1000 chars, head of
the previous answer ≤1500 chars, citation markers stripped) and frames its job
as gathering evidence for the answering agent.

## Variants

| ver | change |
|---|---|
| v1 | baseline (shipped `paper_qa_subagent/v1`); no conversation |
| v2a | v1 + evidence-gatherer framing, a read-in-context rule, `<recent_conversation>` and `<user_request>` tags |
| v2b | concise rewrite: the same framing, one follow-up example (a different domain from the test cases), numbered workflow, same tags |
| v2c | v2b + report only what the chunks state, a section named for the topic first |

Shipped as `src/paperhub/llm/prompts/paper_qa_subagent_v2.yaml` = **v2b**.

## Query set

Targets (with history): T1/T2 run 892's GISC→YOLOSeg follow-up with router/v6's
rewrite, on papers 24 and 89; T3 the same on 24 with the OLD bare rewrite
(「請給我更詳細的實驗方法內容」); T4/T5 "how would I evaluate that?" after a
shortcut-models→GRITS discussion, on papers 41 and 32. Regression: R1
augmentation question (24), R2 GISC data-fidelity term (89), R3 "What's in
Table 2?" (52, Transformer), all first messages; R4 a self-contained
augmentation question in the GISC idea chat; R5 a question paper 89 does not
address (anchor boxes). 2 reps (v1/v2a/v2b round 1, v2b/v2c round 2).

## Judged (Claude-as-judge, every output read)

| case | v1 | v2a | v2b | v2c |
|---|---|---|---|---|
| T1 reads data/metrics, framed for the port | 0/2 (one "does not address") | 1/2 | **4/4** | 2/2 |
| T2 sparsity formulation framed for the port | 0/2 (own method) | 2/2 | 4/4 | 2/2 |
| T3 bare rewrite still framed | 0/2 (hardware + tricks) | 1/2 | **4/4** | 2/2 |
| T4 GRITS evaluation setup + metrics | 0/2 ("does not address") | 2/2 | 4/4 | 2/2 |
| T5 shortcut experiments | 2/2 | 2/2 | 4/4 | 2/2 |
| R1 augmentation (DDPM + disabled mosaic) | 2/2 | 2/2 | 4/4 | 2/2 |
| R2 data-fidelity term | 2/2 | 2/2 | 4/4 | 2/2 |
| R3 Table 2 | 1/1 (+1 503) | 2/2 | 4/4 | **1/2** (claimed Table 2 does not exist) |
| R4 independent question stays on augmentation | 2/2 | 2/2 | 4/4 | 2/2 |
| R5 says "not addressed" | 2/2 | 2/2 | 4/4 | 2/2 |

v1 either summarised each paper's own method or declared the follow-up "not
addressed", dropping the evidence the answer needed (dataset, metrics,
evaluation setup). v2b read 「Image dataset」 + 「Metrics for evaluating the
model performance」 on every T1/T3 run and regressed nothing; v2c regressed R3.

## Known limits

- v2b sometimes adds its own uncited proposal to the summary (e.g. "add an L1
  sparsity term to the loss"). The finalizer receives it as the subagent's
  rationale beside the cited chunk text; v2c's "report only what the chunks
  state" did not remove it reliably and cost a table answer.
- v2b reads one section less on R1 (no 「Effects of DDPM image augmentation」),
  though its cited answer is still correct.
