# Scorecard B — Round {round_no}, language {lang}

Multi-paper conference deck vs `D:\Github\Final_Report\slides.pdf`.

**Self-judge rule:** Fill this honestly via direct PDF reading. Do NOT
escalate to the user mid-round; only escalate when the agent's own
side-by-side comparison says *"at-parity with gold on multi-paper"*.

**Compare:**
- PaperHub: `backend/benchmark/slide_calibration/results/round_{round_no}/b/{lang}/slides.pdf`
- Gold:     `D:\GitHub\Final_Report\slides.pdf`

## Dimensions (score 0/1)

| # | Dimension | Score | Notes (one line; cite slide N if specific) |
|---|---|---|---|
| 1 | **Skeleton order** — Title → Refs-with-QR → Motivation → Bottlenecks-overview → (Concept+Math)×N → Proposed-Direction → Plan → Take-away+Open-Question |   |   |
| 2 | **References slide** — 3-column layout, per-column: tag + italic title + tiny authors + venue badge + QR + URL; stretched to equal height via `[s]` minipage |   |   |
| 3 | **Bottlenecks-overview** — connective-tissue table or 3-row visual mapping each paper to one axis of the shared problem (not 3 separate sections) |   |   |
| 4 | **Concept slide layout** — 2-column figure-left ~0.55, bullets-right ≤4 short items, `\begin{{block}}{{Result}}` with headline number |   |   |
| 5 | **Math slide layout** — vertical stacking with `\textbf{{heading:}}` above each `\[...\]`, ≤2 equations, no bullets |   |   |
| 6 | **Proposed-direction slide** — a *synthesis* not a summary; TikZ or table that says something about the three together (placeholder OK if labeled as such) |   |   |
| 7 | **Closer** — Take-away sentence framed by `\rule`s + `\begin{{block}}{{Open Question}}` italic + Thank-you/Questions; NOT bare "Thank you for listening" |   |   |
| 8 | **Figure fidelity** — every `\includegraphics` resolves to a real file from the source paper |   |   |
| 9 | **Math fidelity** — every equation traceable to a `\begin{{equation}}` in the source paper; no invented symbols |   |   |
| 10 | **Per-language notes pacing** — total words within ±15% of `duration × wpm × 0.9`; content=2×, transition=1×, detail=1.5× weighting visible |   |   |
| 11 | **Spoken-prose (notes)** — no em-dashes in body; ≤4 sentences/slide; first-person; vocabulary calibrated; cross-slide connectors present |   |   |
| 12 | **Claim audit** — every numerical claim in the deck appears verbatim in some source-paper chunk; no conflated ratios |   |   |

**Total: __ / 12**

## Self-judgement summary

- **What works:** (1–2 sentences)
- **What's still obviously weaker than gold:** (1–3 sentences — these become Round N+1 targets)
- **At-parity with gold?** YES / NO. If NO, design Round {round_no}+1 attacking the worst dimension(s) above. Do not interrupt the user yet.

## Deterministic-check signals (from _checks.json)

| Metric | PaperHub round {round_no} | Gold deck (reference) | Delta |
|---|---|---|---|
| Frame envs |   | 13 |   |
| Em-dashes in tex body |   | 0 (gold uses `---`) |   |
| Em-dashes in notes |   | 0 |   |
| Filler words in notes |   | low |   |
| Total note words ({lang}) |   | EN ~675 / ZH ~900 (12-min budget) |   |
| Over-4-sentence note slides |   | 0 |   |
| Under-2-sentence note slides |   | 0 |   |
