# Scorecard A — Round 0, paper 2602.20200v2 (OptimusVLA)

Single-paper contract fidelity vs `reference/paper2slides-plus` + the per-paper hand-built gold.

**Status:** BASELINE — current F3/F4 pipeline before any F4.4 changes. Pipeline succeeded; deck 29, 12 pages.

**Compare:**
- PaperHub: `round_00/a/2602.20200v2/slides.tex` (12 frames)
- Gold:     `D:\GitHub\Final_Report\2602.20200v2\slides.tex` (Berlin/dolphin, 14pt, 16:9)

## Dimensions (score 0/1)

| # | Dimension | Score | Notes |
|---|---|---|---|
| 1 | **Specificity** | **1** | Nearly every bullet quantified: "2.9x speedup", "98.6%", "42.9%", "52.4%", "13.5%", "38%", benchmark names (LIBERO, CALVIN, RoboTwin 2.0, Hard). Strongest dim of any baseline run. PASS |
| 2 | **Equation+itemize pattern** | **0** | Slides 5/6/7 follow the items-then-equation pattern. Symbols ($\mu$, $\alpha_i$, $C_i$, $\bar s$, $\lambda$, $N$) appear without definition. Same backwards pattern as ADP |
| 3 | **Figure-only frame** | **0** | Slides 4, 9, 10 are 2-column (text + figure inside `\begin{columns}`). Closer to gold than ADP (which inlined figures under itemize), but still violates the OLD project's "no text other than caption" rule |
| 4 | **Item density** | **1** | All items under 15 words, ≤4 per block. PASS |
| 5 | **Preamble carries paper's commands** | **0** | No `\input{ADDITIONAL.tex}`. OptimusVLA uses $\mathcal{P}_{re}$, $\mathbf{B}_t$, $\mathbf{X}_t$ — paper may define these via `\newcommand` for consistency; we don't preserve those |
| 6 | **No bare Thank-you slide** | **1** | Ends on "Conclusion". PASS |
| 7 | **ChkTeX-clean** | **1** | Compiled. Presumed clean |
| 8 | **Abbreviation convention** | **0** | Uses VLA, GPM, LCM, NFEs, LIBERO, CALVIN — good. Does NOT use `w/`, `w/o` |
| 9 | **Audience calibration** | **0** | Generic academic |
| 10 | **Speaker-notes conversational** | **N/A** | No notes generated (F4 decoupling) |

**Total: 4 / 9 scorable dimensions = 44%**

## Self-judgement summary

- **What works:** OptimusVLA's quantified results land cleanly — every benchmark number is specific and named. 2-column figure layout is closer to gold than ADP's inline-figure-under-bullets.
- **What's still obviously weaker than gold:** Same three structural gaps as ADP:
  1. 2-column slides still have bullets co-existing with figures (violates the strict figure-only-frame contract).
  2. Equations dropped without notation explanation.
  3. No ADDITIONAL.tex plumbing — would break on papers with non-trivial `\newcommand` use.
- **At-parity with gold?** **NO.** Round 1+ needed.

## Deterministic-check signals

| Metric | PaperHub round 0 | Gold deck (ref) | Delta |
|---|---|---|---|
| Frame envs | 12 | ~13 | -1 ✓ |
| Em-dashes in tex body | 0 | 0 | 0 ✓ |
| Figure keys used | p0-fig-002, p0-fig-003, p0-fig-005 | figures/fig1.pdf, fig2.pdf, fig3.pdf | Different key schemes |
| Notes generated | 0 (F4 decoupling) | N/A | N/A |
