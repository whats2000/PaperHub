---
name: writing-agent-prompts
description: >-
  Use when writing or editing any PaperHub agent/LLM prompt — a
  backend/src/paperhub/llm/prompts/*_v1.yaml system/user template, a new
  agent/router/synthesizer/subagent stage, or any change to how a model is
  instructed. Fires whenever you are tempted to hand-write one prompt and ship
  it, tweak a single prompt line "obviously," or adopt a rewrite without
  comparing it against the current one across multiple queries.
---

# Writing Agent Prompts (PaperHub)

## Overview

A prompt is code that runs on a model. Changing one line changes behavior
**globally** — across every intent, language, and edge case that prompt touches.
The recurring failure in this repo: a dev writes one prompt freely, no
principles, ships it, and a fix for one case silently regresses three others.

**Core principle: you do not *know* a prompt is better — you *measure* it.**
Never adopt a freely-written prompt. Always produce ≥2 variants and compare them
across multiple queries with a judge before one wins.

> The existing prompts under `llm/prompts/` are **not** style exemplars — most
> predate this skill and were written ad-hoc. Copy the file *format* from them,
> not the prompt *craft*. The principles below are the source of truth.

## The Iron Rule

```
NO PROMPT ADOPTED FROM A SINGLE FREELY-WRITTEN DRAFT.
Every prompt change is: ≥2 variants × multiple queries × judged comparison.
```

If you wrote one prompt and want to commit it without comparing it against the
current/baseline version on a query set — stop. That is the exact habit this
skill exists to kill.

## Where PaperHub prompts live (format facts)

- File: `backend/src/paperhub/llm/prompts/{name}_{version}.yaml`, two block
  scalars: `system: |` and `user: |`.
- Loaded by slot `name/version` (e.g. `router/v1`) via
  `llm/prompts/registry.py`. A new revision is a **new file** (`_v2.yaml`) — do
  not silently overwrite a shipped `_v1`; bump and switch the call site so the
  old version stays diffable.
- The `user` template interpolates state with Python `.format()` `{placeholder}`
  fields. **Instructions go in `system`; runtime STATE goes in `user`.**
- Tracing rule (load-bearing, see CLAUDE.md): record the `{placeholder}` input
  values in the tracer step, **never the rendered prompt**. The template + state
  reconstruct it.

## Authoring recipe — apply all four

Write each variant to this shape. These are the four principles, tuned to the
system/user split.

1. **Clear & direct first line.** `system` opens with a direct action statement
   of the agent's job — verb-first, no hedging. "You classify the user's
   message into exactly one intent." Not "This agent might help with figuring
   out what the user wants."

2. **Be specific — output guidelines AND process steps.**
   - *Output guidelines* (always): exact shape, length, format, what to include,
     what to never emit. For JSON output, name every field and forbid prose/code
     fences. For prose, bound the length ("1–2 sentences per paper").
   - *Process steps* (for any multi-step/tool-using agent): a numbered
     `MANDATORY WORKFLOW` the model must follow in order (e.g. list → read →
     cite). Number them; mark the non-skippable ones.

3. **Delimit injected data.** In `user`, wrap every multi-line interpolated
   block in a descriptive tag so the model separates *instructions* from *data*:
   `<resolved_papers>{resolved_block}</resolved_papers>`, `<user_question>…</…>`.
   Short scalars (`{title}`, `{response_language}`) can stay on a labeled line.
   This is the project's existing `<chunk id="N">…</chunk>` convention applied to
   every data block — descriptive names beat `<data>`.

4. **Show examples for anything ambiguous.** Multi-shot the corner cases:
   anaphora resolution, sarcasm/honesty, exact JSON, the "no match / refuse"
   path. Use `input → ideal_output` pairs and, where the reason isn't obvious,
   one line on *why* that output is ideal. Examples beat adjectives.

## The enforced loop: variants → queries → judge

This is the discipline. Do not skip a step because the change "looks safe."

1. **State the hypothesis.** One sentence: what behavior must improve, and which
   behaviors must NOT regress.
2. **Produce ≥2 variants.** The current prompt is always one of them (the
   baseline). Your rewrite(s) are the others. One draft is not a comparison.
3. **Build the query set.** Multiple prompts covering (a) the target behavior and
   (b) **regression queries** — other intents/languages/edge cases the same
   prompt governs. A change with no regression queries is untested for side
   effects.
4. **Run every variant across every query, then judge.** Two tiers:
   - *Fast iteration (Claude-as-judge):* dispatch a fresh subagent per
     (variant × query), collect raw outputs, then judge them head-to-head
     yourself — **read every output**, don't trust a score you didn't read.
     5+ reps where wording is subtle; convergence across reps is itself a signal.
   - *Real-API gate (repo harness):* put variant A in the YAML, run
     `scripts/run-benchmark.ps1 -Judge` against the live `:8000` backend, save
     the results JSON; swap in variant B, run again; compare the two judged
     reports. `benchmark/judge.py` (temp 0, strict grounding, 0/1 + rationale)
     is the same judge the project already trusts. Add a `[[cases]]` entry for
     any behavior you're protecting.
5. **Adopt the winner with evidence.** Commit the variant that wins on the target
   *without* regressing the regression queries — and say so in the commit body
   (which queries, which judge, the verdict). If nothing beats baseline cleanly,
   ship nothing.

## Rationalization table

| Excuse | Reality |
|--------|---------|
| "It's an obvious improvement." | Side effects are invisible until you run the regression queries. Measure. |
| "I only changed one line." | One line changes global behavior. One line gets the same loop. |
| "I followed all four principles, so it's good." | Principles raise the odds; they don't prove a win. Still compare. |
| "No time to test variants." | Re-debugging a silent regression in prod costs more than two benchmark runs. |
| "There's no baseline to compare to." | The current prompt IS the baseline. There is always a baseline. |
| "One query passed." | One query can't reveal cross-intent/-language regression. Use the set. |
| "The judge scored it 1." | A score you didn't read is a guess. Read the outputs. |

## Red flags — STOP

- About to commit a prompt YAML having run it on **zero or one** query.
- Overwriting a shipped `_v1.yaml` in place instead of bumping the version.
- Put runtime data or an example in `system`, or instructions in `user`.
- Recording the rendered prompt in a trace instead of the `{placeholder}` state.
- Citing an existing repo prompt as the reason your style is fine.
- "I'll add regression cases later." (Later = never; the regression is why you test.)

All of these mean: build the variant set, run the query set, judge, then adopt.

## Common mistakes

- **Mega-prompt creep.** One prompt doing classification + resolution +
  generation. If a stage has a disjoint job, it's a separate slot (see how
  `paper_search` is split Parser → Processor → Finalizer → Synthesizer).
- **Adjectives instead of examples.** "Be concise and accurate" does nothing;
  a `bad → good` pair does.
- **Undelimited interpolation.** Pasting `{resolved_block}` raw into prose so the
  model can't tell the data from your instructions.
- **Tuning to one query.** A prompt that aces your favorite example and quietly
  breaks the honesty/refusal path.
