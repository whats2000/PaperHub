# Scorecard B — Round 0, language EN

**Status: BLOCKED — pipeline failure** at `report:narrate` (run 295, ~60s latency, `litellm.APIConnectionError: GeminiException - Server disconnected`).

**Root cause:** Same shape as FASTer single (Scorecard A 2512.04952v2). With 3 papers in scope, the figure_inventory the narrate prompt receives is roughly the union of all 3 papers' inventories — ~25–30 figures with multi-sentence captions each. The narrate prompt drags past Gemini's per-call idle threshold; the call disconnects mid-output, no TalkOutline emitted, downstream stages don't fire.

**Score**: All 12 dimensions are **N/A** — no deck was produced.

## Why this matters for Round 1

This is the strongest single piece of evidence for the Round-1 agentic-brief architecture: **the baseline pipeline literally cannot run the multi-paper scenario at all.** This isn't "quality is 5/12 → improve to 10/12" — it's "the baseline emits nothing" → "Round 1 must make multi-paper actually generate".

Under Round 1's `sl_paper_brief` + `sl_plan_deck` topology:
- Each `sl_paper_brief` call sees ONE paper, extracts top 3-5 figures with one-line interpretations → bounded context per call.
- `sl_plan_deck` consumes N briefs (~25k tokens total for 3 papers — easily fits) → no inventory explosion.
- The structural failure mode disappears at the topology level, before quality is even on the table.

## Trace excerpt (run 295)

```
step | agent | tool                       | status | latency_ms | error
-----+-------+----------------------------+--------+------------+--------------------------------
  0  | router| classify                   | ok     | ~2000      |
  1  | report| report:detect_language     | ok     | ~600       |
  2  | report| report:understand          | ok     | ~25000     |  (paper 1)
  3  | report| report:understand          | ok     | ~25000     |  (paper 2)
  4  | report| report:understand          | ok     | ~25000     |  (paper 3)
  5  | report| report:narrate             | error  | ~60000     | GeminiException - Server disconnected
```

The 3-paper understand stage worked fine (each paper is bounded). The narrate stage failed because it had to consume all 3 paper briefs PLUS the combined figure inventory simultaneously.
