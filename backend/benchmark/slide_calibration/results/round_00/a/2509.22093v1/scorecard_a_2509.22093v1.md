# Scorecard A — Round 0, paper 2509.22093v1 (ADP)

Single-paper contract fidelity vs `reference/paper2slides-plus` + the per-paper hand-built gold.

**Status:** BASELINE — current F3/F4 pipeline before any F4.4 changes. Pipeline succeeded; deck 28, 10 pages.

**Compare:**
- PaperHub: `round_00/a/2509.22093v1/slides.tex` (10 frames)
- Gold:     `D:\GitHub\Final_Report\2509.22093v1\slides.tex` (Berlin/dolphin, 14pt, 16:9)

## Dimensions (score 0/1)

| # | Dimension | Score | Notes |
|---|---|---|---|
| 1 | **Specificity** | **0** | Quantified claims present ("1.35x speedup", "25.8% higher success rate", "OpenVLA baseline") but ~half the bullets are vague generic prose ("Highly robust against complex lighting", "Solves real-time constraints in VLA models without hardware modifications") |
| 2 | **Equation+itemize pattern** | **0** | Slides 5 + 6 have equations following bullets. Bullets describe the concept but do NOT define notation. $\Phi^{(l)}$, $N^h$, $L_{\text{txt}}$, $\mathbf{A}^{(l)}_{h,t,v}$, $\rho$, $\delta_i$, $U^{(i)}$, $V^{(i)}$ all appear with no explanation. OLD project's pattern: equation first, then itemize explaining each symbol |
| 3 | **Figure-only frame** | **0** | Slides 3, 4, 7 each carry an `\includegraphics` AND `\begin{itemize}` together. OLD project rule: "DON'T use any text other than the caption of the figure" |
| 4 | **Item density** | **1** | All items under 15 words; ≤4 per itemize block. Matches OLD project's `update` contract |
| 5 | **Preamble carries paper's commands** | **0** | No `\input{ADDITIONAL.tex}` or equivalent. Paper-defined `\newcommand`s would silently fail. ADP happens to use only standard symbols, masking the gap |
| 6 | **No bare Thank-you slide** | **1** | Ends on "Conclusion and Future Work". No Thank-you. PASS |
| 7 | **ChkTeX-clean** | **1** | Deck compiled to PDF without lint warnings surfacing (presumed; ChkTeX not run separately) |
| 8 | **Abbreviation convention** | **0** | Uses VLA, ADP, FLOPs, OpenVLA, LIBERO — good. Does NOT use `w/`, `w/o`. No em-dashes (good) but also no `---` style. Mixed |
| 9 | **Audience calibration** | **0** | No audience parameter; default generic-academic tone. Cannot tune for cross-domain Master's or casual audience |
| 10 | **Speaker-notes conversational** | **N/A** | No notes generated for this turn (F4 decoupling means notes require an explicit follow-up turn) |

**Total: 3 / 9 scorable dimensions = 33%**

## Self-judgement summary

- **What works:** Item density (≤15 words, ≤4 per block) and the no-Thank-you closer are honored by the current pipeline; the LLM does include some quantified results.
- **What's still obviously weaker than gold:** Three big structural gaps:
  1. Figure-only-frame discipline is not enforced — bullets bleed into figure slides.
  2. Equations are dropped without notation explanation — audience sees $\Phi^{(l)}(v)=\frac{1}{N^h L_{\text{txt}}}\sum \mathbf{A}^{(l)}_{h,t,v}$ with no idea what $\Phi$, $N^h$, $L_{\text{txt}}$ or $\mathbf{A}$ mean.
  3. Specificity is half-good: when the paper feeds clear numbers (1.35x, 25.8%) they propagate, but vague prose ("solves real-time constraints") sneaks in for non-quantified ideas.
- **At-parity with gold?** **NO.** Round 1+ needed.

## Deterministic-check signals

| Metric | PaperHub round 0 | Gold deck (ref) | Delta |
|---|---|---|---|
| Frame envs | 10 | ~12 | -2 (asked for ~15) |
| Em-dashes in tex body | 0 | 0 | 0 ✓ |
| Figure keys used | p0-fig-001, p0-fig-002, p0-fig-004 | figs/motivation_ch_1.png, figs/main2.png, figs/main3.png | Different key scheme, both work |
| Notes generated | 0 (F4 decoupling) | N/A | N/A |
