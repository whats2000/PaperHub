# Scorecard B — Round 0, language ZH

**Status: BLOCKED — partial pipeline run, deck failed to compile** (run 296, deck_id=30, page_count=0, status=error).

**Trace:** Unlike EN, ZH's narrate stage barely survived (51s latency, just under disconnect threshold). All 9 sl_draft calls succeeded. `sl_coherence` ran. `sl_assemble` ran. Then **`sl_compile` failed after 3 revise attempts** with `! File ended while scanning use of \frame` — the assembled `deck.tex` was TRUNCATED to 26 lines (3 incomplete frames), causing pdflatex to error out, and the LLM-driven revise couldn't recover because the input was fundamentally cut off.

**Score**: All 12 dimensions are **N/A** — no PDF produced.

## Root cause

Two separate baseline pipeline issues compound:

1. **`sl_assemble` truncation** — the assembled deck.tex stopped at the second content frame (`\frametitle{方法一：FASTer 框架與區塊自迴歸解碼}`), unclosed. Step 16 (assemble) reported 0ms latency in the trace, suggesting it returned early. The narrate stage's TalkOutline likely contained slide entries that referenced state that the per-frame assembler couldn't materialize.
2. **`sl_revise` can't fix truncation** — the revise loop is designed to react to LaTeX errors (Overfull, escape-char issues), not "the file is cut off". Two revise attempts (steps 19–20) each ran for ~87s but couldn't recover.

## Why this matters for Round 1

ZH compounds two failure modes the baseline can't handle:
- Narrate barely runs at all on combined inventories (one transient longer than 60s and it dies, same as EN).
- Even when narrate survives, assemble can produce structurally broken tex that the revise loop can't repair.

Round 1's `sl_paper_brief` + `sl_plan_deck` topology:
- Keeps `sl_plan_deck` bounded (briefs only, no raw inventory) — narrate-disconnect mode goes away.
- The new `sl_render_slide` (per-slide fan-out) emits well-formed `\begin{frame}...\end{frame}` per slide; assemble becomes a simple concat instead of a re-emit, removing the truncation risk.

## Trace excerpt (run 296)

```
step | agent | tool                       | status | latency_ms
-----+-------+----------------------------+--------+--------------
  0  | router| classify                   | ok     | 2906
  1  | report| report:detect_language     | ok     | 625
  2  | report| report:understand          | ok     | 23171   (paper 1)
  3  | report| report:understand          | ok     | 22905   (paper 2)
  4  | report| report:understand          | ok     | 24984   (paper 3)
  5  | report| report:narrate             | ok     | 51250   (close call, survived)
  6-14| report| report:draft × 9          | ok     | 10-26s each
 15  | report| report:coherence           | ok     | 31671
 16  | report| report:assemble            | ok     | 0       (suspicious — emitted truncated tex)
 17  | report| report:verify_figures      | ok     | 0
 18  | report| report:compile             | error  | 179641  | deck failed to compile after retries
 19  | report| report:revise              | ok     | 87906   (LLM revise — couldn't fix structural truncation)
 20  | report| report:revise              | ok     | 86438
 21  | report| report:emit                | ok     | 0       (emitted error status)
```
