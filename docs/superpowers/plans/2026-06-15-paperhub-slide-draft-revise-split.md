# Slide Pipeline: Split Draft from Revise — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the all-in-one slide_agent (which both drafts AND revises) with deterministic draft stages + an agentic revise-only loop, so the deck is always drafted (no "0 tool calls" failure) and each stage is small, traceable, and reliable.

**Architecture:** Three focused stages — **Outline** (forms the talk: per-slide form + goal, from the digest), **Base Writer** (deterministically writes each slide's content → a base deck.tex, following the resolved preamble/style), and **Revise Agent** (the existing slide_agent, stripped of `initial_draft`, now revise-only: compile → read_section → replace_frame → … → done, improving visual content). Determinism where a step is always required; the agentic loop only where iteration genuinely adds value (compile-fix + visual polish).

**Tech Stack:** Python 3.10 + `uv`, litellm tool-calling, LangGraph report subgraph, pytest, the `Tracer` (every internal call recorded), Beamer/pdflatex.

---

## Design Rationale: Multi-Stage Flow vs All-in-One Fat Agent

This is the load-bearing decision, so it is recorded here rather than assumed.

| Axis | All-in-one fat agent (current) | Multi-stage flow (this plan) |
| --- | --- | --- |
| **Reliability** | Non-deterministic: the model can skip an always-required step (the live 0-tool-calls bug — it never drafted) | Deterministic: each required stage always runs; the model cannot "forget" to draft |
| **Context / tool surface** | One large prompt + full tool palette (`initial_draft`+8 revise tools) → more ways to fail, costlier per call | Each stage sees only what it needs (smaller palette → fewer failures, cheaper, the user's "reduce context = less issue") |
| **Traceability** | One giant `report:slide_agent` step; hard to see where it went wrong | One traced step per stage; a failure is localized to a stage (the user's "trace all internal agent calls") |
| **Flexibility** | The agent can adaptively decide draft vs revise from state | Fixed pipeline; less adaptive — but the *revise* stage stays agentic, so adaptivity is kept exactly where iteration matters |
| **Wiring / maintenance** | One component, one prompt | More components + wiring + a second prompt to maintain |
| **Latency / cost** | Many calls in one loop | Comparable: 1 deterministic draft call + the same revise loop (the draft call replaces the loop's first 1-2 turns) |

**Decision — HYBRID, not "decompose everything":** make the **always-required** steps deterministic (Outline forms the talk; Base Writer writes the content), and keep the **iteration-shaped** step agentic (Revise improves visuals + compile-fixes, where a fixed pipeline genuinely can't replace a feedback loop). This captures the multi-stage reliability/traceability win without throwing away the one place an agent loop earns its keep. The all-in-one's only real advantage — adaptivity — is retained precisely in the revise loop; its real cost — skipping the mandatory draft — is removed.

**Design principles (the rules this plan obeys):**
1. **A must-do step is a NODE, never a prompted agent decision.** If a step must always happen (draft the deck), it is a deterministic pipeline node, not an electable tool / instruction the model can skip. Prompting is the LAST resort for control flow, because it is not reliable — the 0-tool-calls failure was a prompted must-do step.
2. **A focused single-purpose agent ≫ one multi-target prompt.** Each stage gets a narrow prompt with one job (write the base deck; or revise visuals). A fat prompt juggling draft + revise + compile-fix + figure-design is strictly worse — more context, more ways to drift. Split by job.
3. **Trace every internal call.** Each stage is its own `Tracer` step so a failure is localized and visible (this is how the bad design surfaced at all).
4. **Use the agent loop ONLY where iteration adds value.** Drafting is one-shot deterministic; compile-fix + visual polish is a genuine feedback loop → that, and only that, stays agentic.

**Non-goals (YAGNI):** no per-slide micro-agents (over-decomposition; the revise loop already handles per-frame edits); no removing the revise loop (compile-fix needs iteration); the Outline→digest-only simplification is a *separate* redundancy cleanup (Task 6, optional) and not required for this split.

---

## Current State (already on `fix/llm-model-fallback`)

- `slide_agent` is all-in-one: `initial_draft` is an electable tool; on an empty response with an empty deck it ships nothing (the bug this plan fixes).
- `read_section` tool already exists in slide_agent (verbatim section fetch) — the Base Writer and Revise Agent both keep it.
- Figure-first + divider-discipline + per-case outline skeletons + compile `-halt-on-error` are committed.
- `run_sl_outline` still does a content-read loop (redundant once the writer/revise gather content — Task 6, optional).

## File Structure

| File | Responsibility | Change |
| --- | --- | --- |
| `backend/src/paperhub/llm/prompts/slides_base_write_v1.yaml` | Base Writer prompt: outline + bundles + preamble → COMPLETE deck.tex (figure-first; one frame per outline slide) | **Create** |
| `backend/src/paperhub/agents/sl_base_write.py` | `run_base_write(...)` — one deterministic generation → returns deck.tex (strips a ```latex fence); traced as `report:base_write` | **Create** |
| `backend/src/paperhub/llm/prompts/slides_agent_v1.yaml` | Revise-only guidance (drop the `initial_draft` / "EMPTY — call initial_draft" framing; keep figure-first/compile-safety/read_section) | **Modify** |
| `backend/src/paperhub/agents/slide_agent.py` | Remove `initial_draft` from `_tool_schemas()` + dispatch; `run_slide_agent` requires a non-empty starting deck (revise-only) | **Modify** |
| `backend/src/paperhub/agents/report_graph.py` | `_generate`: call `run_base_write` after the outline, hand its deck to `run_slide_agent` as the starting state | **Modify** |
| `backend/src/paperhub/models/slide_domain.py` | `BaseWriteResult` (deck_tex + meta) if a typed return is wanted | **Modify (maybe)** |
| `backend/tests/agents/test_sl_base_write.py` | Base Writer unit tests (stubbed adapter) | **Create** |
| `backend/tests/agents/test_slide_agent.py` | Rework: revise-only loop starts from a deck; drop `initial_draft` cases | **Modify** |
| `docs/superpowers/specs/2026-05-17-paperhub-srs.md` | §III slide pipeline: document the draft/revise split | **Modify** |

---

## Task 1: Base Writer prompt + `run_base_write` (deterministic draft)

**Files:**
- Create: `backend/src/paperhub/llm/prompts/slides_base_write_v1.yaml`
- Create: `backend/src/paperhub/agents/sl_base_write.py`
- Test: `backend/tests/agents/test_sl_base_write.py`

The Base Writer is a single generation: given the outline (per-slide form + goal + grounding excerpts) + the paper bundles + the resolved preamble, it returns the COMPLETE `deck.tex`. It does NOT use tools — the whole response IS the deck, so it is deterministic (the model cannot "skip" drafting). It reuses the figure-first + per-form rendering rules (one source of truth; see Task 5 for de-dup with the revise prompt).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/agents/test_sl_base_write.py
from typing import Any
import pytest
from paperhub.agents.sl_base_write import run_base_write
from paperhub.models.slide_domain import DeckOutline, OutlineSlide, PaperContextBundle
from paperhub.tracing.tracer import Tracer

def _outline() -> DeckOutline:
    return DeckOutline(
        talk_title="T", narrative_pattern="single_paper",
        audience_intent="i", narrative_arc="a",
        slides=[OutlineSlide(slide_index=0, goal="Title", key_message="m",
                             content_form="title")],
    )

async def test_run_base_write_returns_full_deck(migrated_db: Any) -> None:
    # Adapter stub: returns a fenced deck; run_base_write must strip the fence.
    class _Stub:
        async def stream(self, **kw: Any):
            for tok in ["```latex\n", "\\documentclass{beamer}\n",
                        "\\begin{document}\\end{document}\n", "```"]:
                yield tok
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    deck = await run_base_write(
        outline=_outline(), bundles=[], resolved_preamble=r"\documentclass{beamer}",
        response_language="en", adapter=_Stub(), tracer=tracer, model="stub",
    )
    assert deck.strip().startswith("\\documentclass")
    assert "```" not in deck
```

- [ ] **Step 2: Run it, verify it fails**

Run: `cd backend && uv run pytest tests/agents/test_sl_base_write.py -q`
Expected: FAIL (`ModuleNotFoundError: paperhub.agents.sl_base_write`).

- [ ] **Step 3: Create the prompt** `slides_base_write_v1.yaml`

Author `system` + `user_template`. `user_template` vars: `task_description`, `response_language`, `resolved_preamble`, `outline_block`, `bundles_block`, `n_bundles`, `figure_inventory_block`. The `system` holds the figure-first + per-form rendering rules (copy the shared block — Task 5 factors duplication). Instruct: "Output ONLY the complete deck.tex — preamble (use the resolved preamble verbatim) + one `frame` per outline slide, in order. No prose, no fences." **Both `system` and `user_template` braces must be doubled where literal** (`.format` is applied — see slide_agent lesson).

- [ ] **Step 4: Implement `run_base_write`**

```python
# backend/src/paperhub/agents/sl_base_write.py
"""Deterministic base-deck writer: outline + bundles + preamble -> deck.tex.
One generation (no tools); the whole response IS the deck. Traced as
report:base_write."""
from __future__ import annotations
import re
from paperhub.llm.adapter import LlmAdapter
from paperhub.models.slide_domain import DeckOutline, PaperContextBundle
from paperhub.tracing.tracer import Tracer

_FENCE = re.compile(r"^```(?:latex|tex)?\s*|\s*```$", re.IGNORECASE)

def _strip_fence(s: str) -> str:
    s = s.strip()
    s = _FENCE.sub("", s)
    return s.strip()

async def run_base_write(
    *, outline: DeckOutline, bundles: list[PaperContextBundle],
    resolved_preamble: str, response_language: str,
    adapter: LlmAdapter, tracer: Tracer, model: str,
    task_description: str = "",
) -> str:
    async with tracer.step(agent="report", tool="report:base_write", model=model) as step:
        step.record_args({"n_slides": len(outline.slides), "n_bundles": len(bundles)})
        parts: list[str] = []
        async for tok in adapter.stream(
            slot="slides_base_write/v1",
            variables={
                "task_description": task_description,
                "response_language": response_language,
                "resolved_preamble": resolved_preamble,
                "outline_block": _format_outline_block(outline),   # reuse slide_agent helper
                "bundles_block": _format_bundles_block(bundles),   # reuse slide_agent helper
                "n_bundles": len(bundles),
                "figure_inventory_block": "",  # filled by caller in Task 2
            },
            model=model,
        ):
            parts.append(tok)
        deck = _strip_fence("".join(parts))
        step.record_result({"deck_len": len(deck), "n_frames": deck.count("\\begin{frame}")})
        return deck
```

(Import `_format_outline_block` / `_format_bundles_block` from `slide_agent`, or move them to a shared `sl_format.py` in Task 5.)

- [ ] **Step 5: Run the test, verify it passes**

Run: `cd backend && uv run pytest tests/agents/test_sl_base_write.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/paperhub/agents/sl_base_write.py backend/src/paperhub/llm/prompts/slides_base_write_v1.yaml backend/tests/agents/test_sl_base_write.py
git commit -m "feat(slides): deterministic base-write stage (outline+bundles -> deck.tex)"
```

---

## Task 2: Wire `report_graph._generate` — outline → base_write → revise

**Files:**
- Modify: `backend/src/paperhub/agents/report_graph.py` (the `_generate` stage-2/3 region, ~line 849-879)

- [ ] **Step 1: Write the failing test** — extend `tests/agents/test_report_graph_f4_5.py` (or the existing report-graph test): assert that for a GENERATE turn the trace contains a `report:base_write` step BEFORE `report:slide_agent`, and that `run_slide_agent` was called with a non-empty `existing_deck_tex`. Use the existing report-graph stub fixtures.

- [ ] **Step 2: Run it, verify it fails.**

- [ ] **Step 3: Implement.** In `_generate`, after `run_sl_outline` produces `outline`, call:

```python
async with _stage_heartbeat(writer, run_id, "report:base_write"):
    base_deck = await run_base_write(
        outline=outline, bundles=bundles, resolved_preamble=preamble_with_title,
        response_language=lang, adapter=deps.adapter, tracer=deps.tracer,
        model=deps.section_model,
        task_description=effective_query(state) or state.get("user_message", ""),
    )
await _flush_steps()
# Revise stage now ALWAYS starts from the base deck:
async with _stage_heartbeat(writer, run_id, "report:drafting"):
    agent_result = await run_slide_agent(
        ..., existing_deck_tex=base_deck, ...,   # was None
    )
```

- [ ] **Step 4: Run report-graph tests, verify pass.**
- [ ] **Step 5: Commit** `feat(slides): report_graph runs base_write then revise-only agent`.

---

## Task 3: Strip `initial_draft` from slide_agent (revise-only)

**Files:**
- Modify: `backend/src/paperhub/agents/slide_agent.py` (`_tool_schemas()`, `_dispatch_tool_call`, `run_slide_agent`)
- Modify: `backend/tests/agents/test_slide_agent.py`

- [ ] **Step 1: Update tests first.** Rewrite the slide_agent tests so every scenario passes a non-empty `existing_deck_tex` and the LLM mock issues `compile_check` / `replace_frame` / `done` (never `initial_draft`). Add `test_run_slide_agent_requires_starting_deck` asserting it raises/*no-ops clearly* when `existing_deck_tex` is empty (revise-only contract).

- [ ] **Step 2: Run, verify the new tests fail.**

- [ ] **Step 3: Implement.** Remove the `initial_draft` entry from `_tool_schemas()`; remove the `if name == "initial_draft":` branch in `_dispatch_tool_call`; in `run_slide_agent` drop the `deck_state_label` "EMPTY — call initial_draft first" path (always "EXISTING — diff-edit it"); guard: if `existing_deck_tex` is empty/None, raise `ValueError("revise-only: a base deck is required")`.

- [ ] **Step 4: Run slide_agent tests, verify pass.**
- [ ] **Step 5: Commit** `refactor(slides): slide_agent is revise-only (drop initial_draft tool)`.

---

## Task 4: Update the revise prompt (`slides_agent_v1.yaml`)

**Files:**
- Modify: `backend/src/paperhub/llm/prompts/slides_agent_v1.yaml`

- [ ] **Step 1:** Remove the `initial_draft` mention + the "EMPTY — call initial_draft first" framing; reframe the opening as "You are given a BASE deck — REVISE it: compile, fix errors, and improve the VISUAL content (figures, equations, tables) per the figure-first rules." Keep figure-first, per-form rendering, TikZ compile-safety, `read_section`, tool-call discipline. Verify no literal single braces (the system block is `.format`-ed).
- [ ] **Step 2:** Load-check + slot test: `uv run python -c "from paperhub.llm.prompts.registry import PromptRegistry; PromptRegistry().get('slides_agent/v1')"` and `uv run pytest tests/agents/test_slide_agent.py -q`.
- [ ] **Step 3: Commit** `docs(slides): revise-only slide_agent prompt`.

---

## Task 5: Factor the shared drafting rules (DRY)

**Files:**
- Create: `backend/src/paperhub/agents/sl_format.py` (or a shared prompt include)

- [ ] **Step 1:** Move `_format_outline_block`, `_format_bundles_block`, `_format_figure_inventory_block` to `sl_format.py`; import from both `slide_agent` and `sl_base_write`. The figure-first + per-form rendering RULES are duplicated across the base-write and revise prompts — accept a small duplication OR extract a shared YAML partial; pick the lower-risk option and note it.
- [ ] **Step 2:** Run the full slide test set; verify green.
- [ ] **Step 3: Commit** `refactor(slides): share outline/bundle formatting between base_write and revise`.

---

## Task 6 (OPTIONAL, separate concern): simplify Outline to digest-only

Now that the Base Writer + Revise Agent gather content (`read_section`), `run_sl_outline`'s content-read loop is redundant. Drop the `read` action / `read_fn` / multi-round loop → a single digest→outline call (form the talk). Move grounding/traceback to the agents' `read_section` records. **Do this only after Tasks 1-5 are verified live** — it changes the F6.2 Sources-panel grounding source.

---

## Task 7: SRS update + live verification

- [ ] **Step 1:** SRS §III slide pipeline: document the draft/revise split + the multi-stage-vs-fat-agent rationale; add a Revision-History row.
- [ ] **Step 2: LIVE TEST (required gate — prompts/agents are not unit-verifiable for quality).** On the user's running `:8000`, single-paper case (paper 83): confirm the trace shows `report:base_write` → `report:slide_agent`, the deck **compiles to a valid PDF**, carries figures/equations/a real results table, and has 0 chapter dividers. Then survey + comparison cases.
- [ ] **Step 3: Commit** the SRS update.

---

## Self-Review notes

- **Spec coverage:** draft/revise split (Tasks 1-4), DRY (5), outline cleanup (6, optional), SRS+live (7). The 0-tool-calls failure is fixed by Task 1+3 (draft is deterministic; the tool can't be skipped).
- **Brace hazard:** every new/edited prompt's `system` + `user_template` is `.format`-ed — double all literal braces (the `{Stealth}` regression).
- **Type consistency:** `run_base_write` returns `str` (deck.tex); `run_slide_agent` takes it as `existing_deck_tex`. `report:base_write` is the traced tool name (Trace panel asserts on names — add it to any allow-list).
- **Live gate is mandatory:** unit tests prove wiring, not figure quality / compile success — Task 7 Step 2 is the real acceptance.
