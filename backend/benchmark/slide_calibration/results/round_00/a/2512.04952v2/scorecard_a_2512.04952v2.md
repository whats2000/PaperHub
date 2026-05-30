# Scorecard A — Round 0, paper 2512.04952v2 (FASTer)

**Status: BLOCKED — pipeline failure.** Both attempts (runs 292 + 294) failed at `report:narrate` with `litellm.APIConnectionError: GeminiException - Server disconnected` after ~60s.

**Root cause** (from `tool_calls` row, step 3, run 294 args_redacted_json):
- `briefs_block_len = 1605` chars (normal)
- `figure_inventory` block is huge — FASTer has 13 figures (compression_and_reflect, exp_setup, fastervq_vrr, quality_vis, realexperiment, task_visulization, teaser2, vla, vlabench, vq, vrr_detail, vrr_generalization, zeroshot), each with multi-sentence captions
- The combined narrate prompt drags past Gemini's per-call idle threshold
- 2 retries, same failure pattern → not transient, a structural issue

**Score**: All 10 dimensions are **N/A** — no deck was produced to score.

## Why this matters for Round 1

This is a STRONG validator for the Round-1 agentic-brief architecture (per `round0-findings.md`):

- Under the current pipeline, `sl_narrate` receives the FULL figure inventory verbatim → fails on figure-heavy papers.
- Under the Round-1 architecture, `sl_paper_brief` extracts only the top 3-5 figures per paper with one-line interpretations into a dense `PaperTalkBrief`. The planner consumes briefs, not raw inventories — bounded context, bounded latency.
- This isn't a quality issue we're trying to lift from 4/10 to 8/10 — it's a structural failure where the baseline literally cannot produce a deck. Round 1's win condition for FASTer is **"deck generates at all"**.

## Deterministic-check signals

| Metric | PaperHub round 0 | Gold deck (ref) | Delta |
|---|---|---|---|
| Frame envs | 0 (no deck) | ~13 | -13 (blocked) |
| Em-dashes in tex body | n/a | 0 | n/a |
| Figure keys used | n/a (none) | 13 figures | n/a |
