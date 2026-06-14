"""F6.2 slide_agent — the REVISE-ONLY tool-using agent (stage 2 of 3).

A deterministic Base Writer drafts the deck first; this agent only REVISES it.
It requires a non-empty starting deck and receives PaperContextBundles +
resolved preamble + canvas budget + layout examples; it emits the revised
deck.tex via a bounded tool-call loop.

The agent's palette is EDIT-only (replace/insert/delete frame, replace
preamble, read_section) + ``submit``. The must-do verification steps are
PIPELINE GUARDS, not electable tools: this loop deterministically runs the
density check after every edit turn and the compile check on ``submit``. The
agent decides WHAT to edit; the pipeline decides WHEN to verify (always).

Tool-call budget: default 30 calls (10-20 diff edits + submit cycles for a
real-API run). Budget exhaustion ships the current deck state with
satisfied=False — same fallback posture as compile_with_revise's
imperfect-deck-shipping.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from paperhub.agents._canvas_budget import load_canvas_budget
from paperhub.agents._layout_examples import load_layout_examples
from paperhub.agents.sl_format import (
    _format_bundles_block,
    _format_figure_inventory_block,
    _format_outline_block,
)
from paperhub.agents.sl_read import read_section_chunks
from paperhub.agents.slide_agent_compile import (
    run_compile_check,
    run_density_check,
)
from paperhub.agents.slide_agent_tools import (
    DeckState,
    apply_delete_frame,
    apply_insert_frame_after,
    apply_replace_frame,
    apply_replace_preamble,
)
from paperhub.agents.style_resolver import set_session_override
from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.models.slide_domain import (
    CompileCheckResult,
    DeckOutline,
    KeyFigureBundle,
    PaperContextBundle,
)
from paperhub.tracing.tracer import Tracer

LlmAcompletion = Callable[..., Awaitable[Any]]

DEFAULT_MAX_TOOL_CALLS: Final[int] = 30

# Transient connection-drop signatures we retry mid-loop. Mirrors
# ``LiteLlmAdapter._is_transient_stream_error`` — same class of fault, but
# the slide_agent uses non-streaming ``acompletion`` calls inside its
# tool-use loop. ``litellm.num_retries=3`` (set in app lifespan) doesn't
# cover this reliably: it fires only BEFORE the call starts, and the
# Gemini ``APIConnectionError`` class isn't always in litellm's
# retry-eligible set (real-API Run 341 / case 5 crashed step #5 with
# ``GeminiException - Server disconnected``).
_TRANSIENT_SUBSTRINGS: tuple[str, ...] = (
    "Server disconnected",
    "MidStreamFallbackError",
    "APIConnectionError",
    "ServerDisconnectedError",
    "ConnectError",
    "RemoteProtocolError",
    "ReadTimeout",
    "ConnectTimeout",
    "503",
    "504",
    "502",
    "GeminiException",
)


def _is_transient(exc: BaseException) -> bool:
    needle = type(exc).__name__ + ": " + str(exc)
    return any(s in needle for s in _TRANSIENT_SUBSTRINGS)


async def _acompletion_with_retry(
    llm_acompletion: LlmAcompletion,
    *,
    max_attempts: int = 5,
    **kwargs: Any,
) -> Any:
    """Wrap one ``acompletion`` call in a transient-retry loop.

    Backoff: 1s, 2s, 4s, 8s, 16s (~31s total patience). Non-transient
    exceptions propagate immediately. Bumped from 3 attempts (~7s) after
    real-API Run 351 saw the slide_agent crash on a Gemini disconnect
    that lasted longer than 7s.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await llm_acompletion(**kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt >= max_attempts or not _is_transient(exc):
                raise
            await asyncio.sleep(1.0 * (2 ** (attempt - 1)))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("acompletion retry loop fell through")


@dataclass(frozen=True)
class SlideAgentResult:
    deck_tex: str
    preamble: str
    satisfied: bool
    tool_calls_used: int
    last_compile_check: CompileCheckResult | None
    preamble_persisted: bool


def _tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "read_section",
                "description": (
                    "Fetch the VERBATIM source text of one paper section (sliced "
                    "from the flattened LaTeX). Use it to copy an exact results "
                    "TABLE, an equation's full form, or a precise number that the "
                    "bundle only summarizes. Args: paper_id (from the bundle) + "
                    "section_name (a section listed in that paper's bundle)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "paper_id": {"type": "integer"},
                        "section_name": {"type": "string"},
                    },
                    "required": ["paper_id", "section_name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "replace_frame",
                "description": (
                    "Swap one frame at 0-based frame_index. new_frame_tex must "
                    "be exactly one frame env."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "frame_index": {"type": "integer"},
                        "new_frame_tex": {"type": "string"},
                    },
                    "required": ["frame_index", "new_frame_tex"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "insert_frame_after",
                "description": (
                    "Insert one new frame after frame_index. frame_index=-1 "
                    "inserts before first frame."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "frame_index": {"type": "integer"},
                        "new_frame_tex": {"type": "string"},
                    },
                    "required": ["frame_index", "new_frame_tex"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "delete_frame",
                "description": "Remove the frame at frame_index.",
                "parameters": {
                    "type": "object",
                    "properties": {"frame_index": {"type": "integer"}},
                    "required": ["frame_index"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "replace_preamble",
                "description": (
                    "Swap the preamble. persist=True (default) writes to "
                    "slide_style_overrides."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "new_preamble": {"type": "string"},
                        "persist": {"type": "boolean", "default": True},
                    },
                    "required": ["new_preamble"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "submit",
                "description": (
                    "Signal the deck is complete. The pipeline then compiles "
                    "it; if there are compile errors or unrendered-math frames, "
                    "they are returned to you to fix and you continue revising. "
                    "Only call submit when you believe the deck is done."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
    ]


def _format_canvas_budget_block() -> str:
    cb = load_canvas_budget()
    rows: list[str] = []
    for layout in cb.layouts:
        rows.append(
            f"- {layout.name}: "
            f"text_region={layout.text_region_cm[0]:.1f}x{layout.text_region_cm[1]:.1f}cm "
            f"matches_aspect={layout.matches_aspect} "
            f"hint={layout.text_structure_hint[:80]}"
        )
    return "\n".join(rows)


def _format_layout_examples_block() -> str:
    return "\n\n".join(
        f"### {e.id}\npurpose: {e.purpose}\nwhen: {e.when_to_use}\n"
        f"matches_aspect: {e.matches_aspect}\nexample:\n```latex\n{e.example}\n```"
        for e in load_layout_examples()
    )


async def _dispatch_tool_call(
    *,
    name: str,
    args: dict[str, Any],
    state: DeckState,
    bundles: list[PaperContextBundle],
    figure_inventory: dict[str, KeyFigureBundle],
    workdir: Path,
    session_id: int | None,
    conn: Any,
    script: str,
) -> tuple[DeckState, str, CompileCheckResult | None]:
    """Apply one EDIT tool call (or the ``submit`` placeholder).

    Returns ``(new_state, result_str, compile_check_or_None)``. The third
    element is vestigial (always ``None``): the must-do verification steps
    (compile / density) are pipeline guards owned by the loop, not tool
    dispatch. ``submit`` is handled by the loop too — dispatch returns a
    neutral placeholder for it so the per-call tool-response message stays
    valid.
    """
    try:
        if name == "submit":
            # The loop owns submit (it runs the compile guard). Return a neutral
            # placeholder result so the per-call tool-response message is valid.
            return state, json.dumps({"ok": True, "submitted": True}), None
        if name == "read_section":
            # Agentic context-gather: pull the VERBATIM flattened-LaTeX text of a
            # section so the agent can copy an exact table/equation/number the
            # bundle only summarizes. Read-only; no state/compile change.
            pid = int(args.get("paper_id", 0))
            section = str(args.get("section_name", "")).strip()
            known = {b.paper_id for b in bundles}
            if pid not in known:
                return state, json.dumps(
                    {"error": f"paper_id {pid} is not in this deck; known: {sorted(known)}"}
                ), None
            if not section:
                return state, json.dumps({"error": "section_name is required"}), None
            if conn is None:
                return state, json.dumps({"error": "no database connection"}), None
            res = await read_section_chunks(
                paper_content_id=pid, section_name=section, conn=conn,
            )
            text = res.text or ""
            cap = 8000  # enough for a results table; bounds prompt growth
            return state, json.dumps(
                {
                    "paper_id": pid,
                    "section_name": section,
                    "chunk_ids": res.chunk_ids,
                    "text": text[:cap],
                    "truncated": len(text) > cap,
                    "empty": not text,
                },
                ensure_ascii=False,
            ), None
        if name == "replace_frame":
            state = apply_replace_frame(
                state,
                frame_index=int(args["frame_index"]),
                new_frame_tex=str(args["new_frame_tex"]),
            )
            return (
                state,
                json.dumps({"ok": True, "frame_index": int(args["frame_index"])}),
                None,
            )
        if name == "insert_frame_after":
            state = apply_insert_frame_after(
                state,
                frame_index=int(args["frame_index"]),
                new_frame_tex=str(args["new_frame_tex"]),
            )
            return (
                state,
                json.dumps({"ok": True, "inserted_after": int(args["frame_index"])}),
                None,
            )
        if name == "delete_frame":
            state = apply_delete_frame(state, frame_index=int(args["frame_index"]))
            return (
                state,
                json.dumps({"ok": True, "deleted": int(args["frame_index"])}),
                None,
            )
        if name == "replace_preamble":
            new_preamble = str(args["new_preamble"])
            persist = bool(args.get("persist", True))
            state = apply_replace_preamble(state, new_preamble=new_preamble)
            if persist and session_id is not None and conn is not None:
                await set_session_override(
                    session_id=session_id,
                    preamble_tex=new_preamble,
                    source="agent_inferred",
                    conn=conn,
                )
            return state, json.dumps({"ok": True, "persisted": persist}), None
        return state, json.dumps({"error": f"unknown tool {name!r}"}), None
    except Exception as exc:  # noqa: BLE001 — surface to LLM as a normal error
        return (
            state,
            json.dumps({"error": f"{type(exc).__name__}: {exc}"}),
            None,
        )


async def run_slide_agent(
    *,
    bundles: list[PaperContextBundle],
    task_description: str,
    response_language: str,
    resolved_preamble: str,
    workdir: Path,
    existing_deck_tex: str | None,
    outline: DeckOutline | None = None,
    figure_inventory: dict[str, KeyFigureBundle],
    memory_context: str,
    tracer: Tracer,
    model: str,
    session_id: int | None = None,
    conn: Any = None,
    script: str = "en",
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    registry: PromptRegistry | None = None,
    llm_acompletion: LlmAcompletion | None = None,
) -> SlideAgentResult:
    # Revise-only: a deterministic Base Writer must have drafted the deck first.
    # An empty / whitespace starting deck is a programmer error, not something
    # this agent recovers from (it no longer has an initial_draft tool).
    if existing_deck_tex is None or not existing_deck_tex.strip():
        raise ValueError("revise-only: a base deck is required")

    reg = registry or PromptRegistry()
    prompt = reg.get("slides_agent/v1")
    if llm_acompletion is None:
        import litellm

        llm_acompletion = litellm.acompletion

    state = DeckState(
        deck_tex=existing_deck_tex,
        preamble=resolved_preamble,
        workdir=workdir,
        dirty=True,
    )

    user = prompt.user_template.format(
        task_description=task_description,
        response_language=response_language,
        resolved_preamble=resolved_preamble,
        outline_block=_format_outline_block(outline),
        bundles_block=_format_bundles_block(bundles),
        n_bundles=len(bundles),
        figure_inventory_block=_format_figure_inventory_block(figure_inventory),
        canvas_budget_block=_format_canvas_budget_block(),
        layout_examples_block=_format_layout_examples_block(),
        deck_state_label="EXISTING — diff-edit it",
        existing_deck_block=existing_deck_tex,
    )
    system = prompt.system.format(
        response_language=response_language,
        memory_context=memory_context or "(no active memories)",
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    tools_schema = _tool_schemas()

    pending_compile_check: CompileCheckResult | None = None
    preamble_persisted = False
    accepted_done = False
    tool_calls_used = 0

    async with tracer.step(
        agent="report", tool="report:slide_agent", model=model
    ) as step:
        step.record_args(
            {
                "n_bundles": len(bundles),
                "task": task_description[:200],
                "existing_deck": existing_deck_tex is not None,
                "max_tool_calls": max_tool_calls,
            }
        )
        tool_call_log: list[dict[str, Any]] = []

        try:
            while tool_calls_used < max_tool_calls:
                try:
                    response = await _acompletion_with_retry(
                        llm_acompletion,
                        model=model,
                        messages=messages,
                        tools=tools_schema,
                        tool_choice="auto",
                    )
                except Exception as exc:
                    # Transient retry exhausted (or non-transient error). The
                    # deck always starts non-empty (revise-only), so a transient
                    # failure ships the current deck imperfect (mirrors the
                    # budget-exhaustion ship-imperfect path). The guard stays as
                    # defense-in-depth.
                    if _is_transient(exc) and state.deck_tex:
                        tool_call_log.append(
                            {
                                "tool": "_transient_exhausted",
                                "args_redacted": {},
                                "result_excerpt": (
                                    f"{type(exc).__name__}: {str(exc)[:200]}"
                                ),
                            }
                        )
                        break
                    raise
                msg = response["choices"][0]["message"]
                tool_calls = msg.get("tool_calls") or []
                if not tool_calls:
                    # Agent gave up without a clean submit — ship current state
                    # as imperfect.
                    break

                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.get("content"),
                        "tool_calls": tool_calls,
                    }
                )

                # Per-turn signals for the pipeline guards (decided after the
                # turn, not by the model): did the model request submit, and did
                # any edit actually land this turn?
                submit_requested = False
                edit_applied = False
                _EDIT_TOOLS = {
                    "replace_frame",
                    "insert_frame_after",
                    "delete_frame",
                    "replace_preamble",
                }

                for call in tool_calls:
                    if tool_calls_used >= max_tool_calls:
                        break
                    tool_calls_used += 1
                    name = call["function"]["name"]
                    try:
                        args = json.loads(call["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {}

                    state, result_str, _ = await _dispatch_tool_call(
                        name=name,
                        args=args,
                        state=state,
                        bundles=bundles,
                        figure_inventory=figure_inventory,
                        workdir=workdir,
                        session_id=session_id,
                        conn=conn,
                        script=script,
                    )
                    if name == "submit":
                        submit_requested = True
                    if name == "replace_preamble":
                        try:
                            parsed = json.loads(result_str)
                        except json.JSONDecodeError:
                            parsed = {}
                        if isinstance(parsed, dict) and parsed.get("persisted"):
                            preamble_persisted = True
                    if name in _EDIT_TOOLS:
                        # An edit landed iff dispatch reported ok (it surfaces
                        # apply errors as {"error": ...} without raising).
                        try:
                            parsed_edit = json.loads(result_str)
                        except json.JSONDecodeError:
                            parsed_edit = {}
                        if isinstance(parsed_edit, dict) and parsed_edit.get("ok"):
                            edit_applied = True

                    tool_call_log.append(
                        {
                            "tool": name,
                            "args_redacted": (
                                {
                                    k: (v[:200] if isinstance(v, str) else v)
                                    for k, v in args.items()
                                }
                                if isinstance(args, dict)
                                else {}
                            ),
                            "result_excerpt": result_str[:400],
                        }
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "name": name,
                            "content": result_str,
                        }
                    )

                # Pipeline guards run AFTER the turn — the agent never elects a
                # check tool; the loop deterministically verifies.
                if submit_requested:
                    # Submit → compile guard. The compile is a pipeline run, not
                    # a model tool call: do NOT increment tool_calls_used.
                    check = await run_compile_check(
                        deck_tex=state.deck_tex,
                        bundles=bundles,
                        figure_inventory=figure_inventory,
                        workdir=workdir,
                        script=script,  # type: ignore[arg-type]
                    )
                    pending_compile_check = check
                    if check.compile_errors or check.unrendered_math_frames:
                        # Forced revision round: feed the failures back and keep
                        # going. Do NOT accept done.
                        messages.append(
                            {
                                "role": "user",
                                "content": json.dumps(
                                    {
                                        "submit_rejected": True,
                                        "reason": (
                                            "Fix these before submitting again."
                                        ),
                                        "compile_errors": check.compile_errors,
                                        "unrendered_math_frames": [
                                            f.model_dump()
                                            for f in check.unrendered_math_frames
                                        ],
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        )
                        continue
                    accepted_done = True
                    break
                elif edit_applied:
                    # Edit turn (no submit) → density guard. Also a pipeline run,
                    # not a model tool call. Feed the signals back automatically
                    # so the agent sees density feedback without asking.
                    density = await run_density_check(
                        deck_tex=state.deck_tex,
                        bundles=bundles,
                        script=script,  # type: ignore[arg-type]
                        figure_inventory=figure_inventory,
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "density_feedback": density.model_dump(),
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )
        finally:
            # Defense-in-depth: always record the partial trace, even if an
            # unexpected exception escapes the loop body. Without this the
            # tracer would capture status=error but lose the tool_call_log —
            # an agent-flow observability iron-rule violation.
            step.record_result(
                {
                    "satisfied": accepted_done,
                    "tool_calls_used": tool_calls_used,
                    "final_deck_len": len(state.deck_tex),
                    "last_compile_check": (
                        pending_compile_check.model_dump()
                        if pending_compile_check
                        else None
                    ),
                    "tool_call_log": tool_call_log,
                }
            )

    return SlideAgentResult(
        deck_tex=state.deck_tex,
        preamble=state.preamble,
        satisfied=accepted_done,
        tool_calls_used=tool_calls_used,
        last_compile_check=pending_compile_check,
        preamble_persisted=preamble_persisted,
    )
