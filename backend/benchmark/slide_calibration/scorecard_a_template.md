# Scorecard A — Round {round_no}, paper {arxiv_id}

Single-paper contract fidelity vs `reference/paper2slides-plus`.

**Self-judge rule:** Fill this honestly via direct PDF reading. Do NOT
escalate to the user mid-round; only escalate when the agent's own
side-by-side comparison says *"at-parity with gold on this paper"*.

**Compare:**
- PaperHub: `backend/benchmark/slide_calibration/results/round_{round_no}/a/{arxiv_id}/slides.pdf`
- Gold:     `D:\GitHub\Final_Report\{arxiv_id}\slides.pdf`

## Dimensions (score 0/1)

| # | Dimension | Score | Notes (one line; cite slide N if specific) |
|---|---|---|---|
| 1 | **Specificity** — no abstract claims; metrics quantified ("14% better X on benchmark Y"); benchmarks named |   |   |
| 2 | **Equation+itemize pattern** — equations followed by itemize explaining notations + meaning |   |   |
| 3 | **Figure-only frame** — frames with a figure carry ONLY figure + 1-sentence caption (no frametitle, no items) |   |   |
| 4 | **Item density** — ≤4 items per itemize; each ≤15 words; overflow strategy is SPLIT not shrink |   |   |
| 5 | **Preamble carries paper's commands** — `\input{{ADDITIONAL.tex}}` or equivalent; paper-specific `\newcommand`s survive |   |   |
| 6 | **No bare Thank-you slide** — deck does NOT end on a lone "Thank you" frame |   |   |
| 7 | **ChkTeX-clean** — zero high-severity ChkTeX warnings on slide source (modulo ADDITIONAL.tex noise) |   |   |
| 8 | **Abbreviation convention** — standard abbrevs (GANs/VAEs/SGD/LLM); `w/`, `w/o`; no `—`, use `---` |   |   |
| 9 | **Audience calibration** — vocabulary matches the audience parameter (default: ML PhD students), not generically academic |   |   |
| 10 | **Speaker-notes conversational** — first-person, 2–4 sentences, cross-slide connectors ("Remember from slide 3…") |   |   |

**Total: __ / 10**

## Self-judgement summary

- **What works:** (1–2 sentences)
- **What's still obviously weaker than gold:** (1–3 sentences — these become Round N+1 targets)
- **At-parity with gold?** YES / NO. If NO, design Round {round_no}+1 attacking the worst dimension(s) above. Do not interrupt the user yet.

## Deterministic-check signals (from _checks.json)

| Metric | PaperHub round {round_no} | Gold deck (reference) | Delta |
|---|---|---|---|
| Frame envs |   |   |   |
| Em-dashes in tex body |   | 0 (gold uses `---`) |   |
| Em-dashes in notes |   | 0 |   |
| Filler words in notes (essentially/actually/really/obviously) |   | low |   |
| Over-4-sentence note slides |   | 0 |   |
