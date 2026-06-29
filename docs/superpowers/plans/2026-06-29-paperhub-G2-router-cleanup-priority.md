# Plan G2 — Router cleanup (`model_tier` removal) + multi-intent priority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. ANY task that authors/edits a prompt YAML (deploy OR eval) MUST invoke the `writing-agent-prompts` skill FIRST.

**Goal:** Two router corrections discovered while evaluating the router under Plan G1.

1. **Remove `model_tier`.** The router LLM emits a `model_tier` field on every turn that **nothing consumes** — the actual model is config-driven by `intent` (`chat.py` dispatches each intent with `settings.<intent>_model`, defaulting via `small_tier_model()` / `flagship_tier_model()` in `config.py`). The field costs output tokens + prompt real estate and, worse, the frontend `RoutingBadge` *displays* a tier that may not match the model that actually ran (the honest "which model ran" answer is the trace's `tool_calls.model`). Delete it end-to-end.
2. **Make multi-intent collapse deterministic.** The router classifies into exactly one intent, but when a message carries more than one (e.g. *"find the Mamba paper and make slides"*) the choice is undefined. Add an explicit **priority tie-break** rule to the router prompt so the collapse is predictable. (True multi-intent fan-out — handling several intents in one turn — is explicitly **out of scope**; it would be its own plan touching the graph/dispatch/SSE.)

**Validation is benchmark-gated (the load-bearing process rule for this plan).** Both changes alter the router prompt, so they go through the Plan G1 agent-eval system before promotion. The current best variant is **`router/v2a`** (the user verified v2a > v1). The new variant is built **on top of v2a**, and is benchmarked **against v2a** (not v1) over the router buckets, with new multi-intent cases added. Only after the sweep shows *no intent regression + correct priority resolution + a token reduction* is the structure promoted into the deploy prompt.

**Architecture / blast radius:** `model_tier` is one field threaded through the schema, both prompt layers, the frontend badge, the replay CLI, and ~15 tests + eval corpora. The priority rule is a few lines added to one prompt (plus its eval twin). No graph, dispatch, DB schema, or SSE change.

**Tech Stack:** Python 3.11 / `uv` (backend), TypeScript / Vitest (frontend), the existing `paperhub-eval` agent-eval CLI + `litellm` (validation). No new dependency.

## Global Constraints

- **Prompt discipline (MANDATORY, per CLAUDE.md).** Every prompt edit — the deploy `router_v1.yaml`, the eval `v2a.yaml`/new variant — requires invoking the `writing-agent-prompts` skill FIRST. The new variant is authored by the ≥2-variants × query-set × judged-comparison loop, NOT hand-shipped. A prompt authored without invoking the skill is invalid and must be redone.
- **Benchmark-before-promote.** No edit to the *deploy* prompt or `RoutingDecision` schema lands until the new eval variant has been swept against `v2a` and shown safe. The eval comes first; the deploy cleanup second.
- **Python tooling:** `uv` only. From `backend/`: `uv run pytest`, `uv run ruff check src tests`, `uv run mypy src` (`--strict` on `src`).
- **Test discipline (TDD):** failing test first → minimal impl → green → commit, every task. Per-task scope = only the touched test files + targeted ruff/mypy (full suite + real-API gate at plan completion only).
- **i18n:** the `RoutingBadge` change removes displayed text, not adds — no new locale keys. Confirm no orphaned `routing.*tier*` keys exist across the four locales.
- **Commits:** Conventional Commits — `feat(router):`, `fix(router):`, `refactor(router):`, `test(...):`, `docs(...):`. Body wraps at 72 cols. Trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- **Restricted ops:** local commits/branches proceed freely; `git push` / PR / merge / the version-manifest + lockfile + CLAUDE.md-pointer bumps (the `paperhub-merge-prep` skill) need explicit per-instance approval at finish.

---

## Decision: multi-intent priority order

When more than one intent genuinely applies, the router picks the single **highest-priority** one. The two existing prompt overrides run **first** (they already resolve the common prerequisite case): **Override A** (a `paper_qa`/`slides` turn with `enabled_refs_count == 0` → `paper_search`/`paper_suggest`) and **Override B** (`slide_attached` + a *question* → `paper_qa`). The precedence below breaks genuine remaining ties.

| # | Intent | Rationale |
| --- | --- | --- |
| 1 | `memory` | A `remember/forget/update` is a durable side-effect; silently dropping it loses user data — the worst outcome. Cheap to honor; the user re-issues the other action with the preference now applied. **(The one reviewable call — flip to lower if "do X and remember Y" should prefer X.)** |
| 2 | `slides` | Concrete deck deliverable; highest-effort artifact. |
| 3 | `paper_qa` | Content answer over enabled papers. |
| 4 | `library_stats` | Query the user's own library. |
| 5 | `paper_search` | Locate a specific named paper. |
| 6 | `paper_suggest` | Topic discovery — easiest to re-ask. |
| 7 | `chitchat` | Incidental. |
| 8 | `clarify` | Pure fallback — by definition cannot co-occur with an actionable intent. |

---

## Workstream / Task list

### Phase 1 — Eval the new structure (benchmark first, no deploy change yet)

- [ ] **Task 1 — Author the new router variant `v3` (eval-only).** Invoke `writing-agent-prompts`. Start from `benchmark/agent/prompts/router/v2a.yaml` (the current winner), then: (a) delete the `## model_tier` block, the `model_tier` output field, and `model_tier` from every example; (b) add the priority tie-break rule + the precedence table above, noting it runs *after* Overrides A/B; (c) add 1–2 multi-shot examples for compound messages (e.g. *"find the Mamba paper and make slides"* with refs=0 → `paper_search` via Override A; *"summarize this and make slides"* with refs≥1 → `slides` by precedence; *"remember I like terse answers and recommend RAG papers"* → `memory`). Keep it concise/direct/XML-delimited (the v2a style). Save as `prompts/router/v3.yaml`. No deploy file touched.
- [ ] **Task 2 — Add multi-intent corpus cases + tolerant grading.** Add compound-message cases to `corpus/router.edge.jsonl` (and/or a new `router.multiintent.jsonl` bucket) with `expect.intent` set per the precedence + a `rubric` naming the competing intents. **Load-bearing subtlety:** the router stage grades by *exact intent match*, but the eval parses output via the production `RoutingDecision`, which is `extra="forbid"`. A variant that omits `model_tier` (`v3`) and one that emits it (`v2a`) cannot both parse under the same strict schema. Make the router stage's output extraction **tolerant of `model_tier`'s presence or absence** (extract `intent` from the raw JSON / parse with extras ignored) so cross-tier-boundary variants stay comparable. This is eval-code only (`stages.py` / `replay.py` / `grade.py` — no `src/` change). Failing test first.
- [ ] **Task 3 — Sweep `v3` vs `v2a` and record the experiment.** Update `router.eval.toml` `variants = ["v2a", "v3"]` (drop `v1` from the active sweep — v2a is the baseline now). Run `paperhub-eval sweep` (real API, the validation gate). **Acceptance:** (1) `v3` shows **zero intent regression** vs `v2a` on core+regression+edge; (2) the new multi-intent cases resolve to the precedence-correct intent under `v3` (and demonstrably arbitrary/inconsistent under `v2a`, which lacks the rule); (3) `v3` token count ≤ `v2a` (model_tier removal should shave output + prompt tokens). Record the matrix report. If `v3` regresses, iterate the prompt (back to Task 1) — do **not** proceed to Phase 2.

### Phase 2 — Promote + remove `model_tier` from deploy (only after Phase 1 passes)

- [ ] **Task 4 — Drop `model_tier` from the schema.** Remove the field from `RoutingDecision` and delete the now-unused `ModelTier` type ([models/domain.py](../../../backend/src/paperhub/models/domain.py)); remove from `RoutingDecisionOut` ([api/sessions.py](../../../backend/src/paperhub/api/sessions.py)). Update every backend test that constructs/asserts `model_tier` (`test_router.py`, `test_models.py`, `test_graph.py`, `test_chat_*`, `test_sessions_api.py`, …). Failing tests first (assert the field is gone / no longer required). `extra="forbid"` stays — confirm a payload *with* `model_tier` now fails (the deploy prompt is updated in Task 5 in the same phase).
- [ ] **Task 5 — Promote the validated structure into the deploy prompt.** Invoke `writing-agent-prompts`. Rewrite `llm/prompts/router_v1.yaml` to the `v3` structure: no `model_tier` block/field/examples, the priority rule added. This is the deliberate post-sweep promotion of the eval winner into production. (Optional, recommend confirming with the user: also copy `v3` → `benchmark/agent/prompts/router/v1.yaml`'s role as the new committed baseline, or leave the eval baseline as v2a/v3.) Verify the prompt's output JSON spec exactly matches the trimmed `RoutingDecision`.
- [ ] **Task 6 — Frontend cleanup.** Remove `model_tier` from the `RoutingDecision` type ([frontend/src/types/domain.ts](../../../frontend/src/types/domain.ts)); the badge shows `intent · NN%` only ([RoutingBadge.tsx](../../../frontend/src/components/chat/RoutingBadge.tsx)). Update `RoutingBadge.test.tsx`, the SSE stubs (`tests/stubs/sse.ts`), and the `useChatStream` / `sessionsSync` tests that carry `model_tier`. Confirm no locale key references a tier label.
- [ ] **Task 7 — Replay CLI.** Drop `tier=` from the run summary line ([cli/replay.py](../../../backend/src/paperhub/cli/replay.py)); update `test_replay.py` + `tests/benchmark_agent/test_replay.py`.

### Phase 3 — Verify

- [ ] **Task 8 — Full quality gates + real-API router check.** Backend: `uv run pytest`, `ruff check src tests`, `mypy src`. Frontend: `npm test`, `typecheck`, `lint`, `build`. Then the real-API `:8000` gate (ask the user to confirm the backend is live — do **not** boot your own): drive a few turns including a compound message, read the recorded run via `paperhub-replay --run-id <N>` / `tool_calls`, confirm the routing decision has **no** `model_tier`, the right intent fired per precedence, and the badge renders without a tier. Frontend visual sign-off by the user.
- [ ] **Task 9 — Docs + merge-prep.** SRS revision row + Part-0 coverage row already added for G2 (this plan). At finish, run `paperhub-merge-prep` (version manifests + lockfiles + 4 README locales + CLAUDE.md plan-table/pointers + SRS row finalization), then stop for merge/tag/push approval.

---

## Out of scope (named)

- **True multi-intent fan-out** — decomposing one message into several intents handled sequentially in a single turn (graph/dispatch/SSE change). Deferred to its own plan; the priority tie-break is the deliberate v1 answer.
- **Reintroducing a tier indicator in the UI** — if "which model ran" is wanted in the badge later, source it from the trace's actual `tool_calls.model`, not a router-claimed tier. Not built here.
- **Changing the intent→model config mapping** — the config-driven model selection (`settings.<intent>_model`) is correct and unchanged; this plan only removes the vestigial router-emitted tier.

## Notes / known subtleties

- **Schema ↔ prompt coupling.** Removing `model_tier` from `RoutingDecision` (`extra="forbid"`) and editing the deploy prompt MUST land together (Phase 2) — a half-done state (schema trimmed but prompt still asking for the field) makes every live turn fail validation. Keep Tasks 4 + 5 in the same phase / PR.
- **Eval baseline parsing.** See Task 2 — the tolerant intent extraction is what lets `v2a` (with tier) and `v3` (without) be swept side-by-side; without it the sweep can't compare across the tier boundary.
- **`memory` precedence** is the single reviewable product call — flagged in the priority table for the user to confirm or flip during plan review.
