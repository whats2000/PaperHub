"""F4.5 slide_agent — THE monolithic tool-using agent (stage 2 of 3).

Owns the deck across draft AND revise. Receives PaperContextBundles + resolved
preamble + canvas budget + layout examples; emits the final deck.tex via a
bounded tool-call loop.

Tool-call budget: default 30 calls (initial_draft + 1-2 compile_checks +
10-20 diff edits + done = ~20-25 calls for a real-API run). Raised from 15
after the real-API benchmark Run 342-346 saw all 5 cases hit the 15-call
ceiling without successfully reaching done(). Budget exhaustion ships the
current deck state with satisfied=False — same fallback posture as the
existing compile_with_revise's imperfect-deck-shipping.
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
from paperhub.agents.slide_agent_compile import (
    run_compile_check,
    run_density_check,
)
from paperhub.agents.slide_agent_tools import (
    DeckState,
    apply_delete_frame,
    apply_initial_draft,
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
                "name": "initial_draft",
                "description": (
                    "Write the complete deck.tex (preamble + every frame). "
                    "Call ONCE at start when no deck exists."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"deck_tex": {"type": "string"}},
                    "required": ["deck_tex"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "compile_check",
                "description": (
                    "Run pdflatex + overflow + math-frame audit. Returns "
                    "structured signals."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "density_check",
                "description": (
                    "Run overflow + math audit WITHOUT pdflatex (speculative)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"deck_tex_excerpt": {"type": "string"}},
                    "required": [],
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
                "name": "done",
                "description": (
                    "Signal satisfied. Rejected if compile_errors or "
                    "unrendered_math_frames are present."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
    ]


def _format_bundles_block(bundles: list[PaperContextBundle]) -> str:
    rows: list[dict[str, Any]] = []
    for b in bundles:
        rows.append(
            {
                "paper_id": b.paper_id,  # real paper_content.id — use in % cite: markers
                "paper_idx": b.paper_idx,
                "title": b.title,
                "authors": b.authors[:5],
                "year": b.year,
                "narrative_summary": b.narrative_summary,
                "key_figures": [
                    {
                        "key": f.key,
                        "role": f.role,
                        "interp": f.one_line_interpretation,
                        "aspect": round(f.dimensions.aspect_ratio, 2),
                    }
                    for f in b.key_figures
                ],
                "key_equations": [
                    {
                        "latex": e.latex[:200],
                        "role": e.role,
                        "notation": e.notation_legend[:100],
                    }
                    for e in b.key_equations
                ],
                "section_excerpts": [
                    {"section": s.section_name, "text": s.text[:600]}
                    for s in b.section_excerpts
                ],
                "paper_newcommands": b.paper_newcommands[:30],
            }
        )
    return json.dumps(rows, ensure_ascii=False, indent=2)


def _format_figure_inventory_block(inv: dict[str, KeyFigureBundle]) -> str:
    if not inv:
        return "(empty — no figures)"
    return "\n".join(
        f"- {key}: aspect={fig.dimensions.aspect_ratio:.2f} "
        f"({fig.dimensions.width_px}x{fig.dimensions.height_px}) role={fig.role}"
        for key, fig in inv.items()
    )


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


_EXCERPT_MAX_CHARS: Final[int] = 300


def _format_outline_block(outline: DeckOutline | None) -> str:
    """Render the approved outline for the drafter. The drafter MUST render
    exactly one frame per slide, in order — no add/drop/split (the 1:1 contract
    that keeps grounding mapped to pages by slide_index).

    Each slide line now includes:
    - ``[form: <content_form>]`` so the drafter knows the intended render style.
    - An ``Evidence:`` sub-list of ``support_excerpts`` (truncated to
      ``_EXCERPT_MAX_CHARS`` chars) so the drafter writes from fetched material
      rather than hallucinating. Omitted when the slide has no excerpts.
    """
    if outline is None:
        return ""
    lines = [
        "## APPROVED TALK OUTLINE — render EXACTLY one frame per slide below, "
        "in this order. Do NOT add, drop, merge, or split slides.",
        f"Talk title: {outline.talk_title}",
        f"Audience intent: {outline.audience_intent}",
        f"Narrative arc: {outline.narrative_arc}",
        "",
        "Slides:",
    ]
    for s in outline.slides:
        fig = f" [figure: {s.figure_key}]" if s.figure_key else ""
        msg = f" — {s.key_message}" if s.key_message else ""
        form = f" [form: {s.content_form}]"
        lines.append(f"{s.slide_index + 1}. {s.goal}{msg}{form}{fig}")
        if s.support_excerpts:
            lines.append("   Evidence:")
            for excerpt in s.support_excerpts:
                if len(excerpt) > _EXCERPT_MAX_CHARS:
                    excerpt = excerpt[:_EXCERPT_MAX_CHARS] + "..."
                lines.append(f"   - {excerpt}")
    return "\n".join(lines)


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
    pending_done_check: CompileCheckResult | None,
) -> tuple[DeckState, str, CompileCheckResult | None, bool]:
    """Apply one tool call.

    Returns ``(new_state, result_str, compile_check_or_None, accepted_done)``.

    ``accepted_done=True`` ONLY when ``name=='done'`` and the last
    ``compile_check`` passed the gate (``compile_errors`` empty AND
    ``unrendered_math_frames`` empty).
    """
    try:
        if name == "initial_draft":
            state = apply_initial_draft(state, deck_tex=str(args["deck_tex"]))
            return state, json.dumps({"ok": True, "deck_set": True}), None, False
        if name == "compile_check":
            check = await run_compile_check(
                deck_tex=state.deck_tex,
                bundles=bundles,
                figure_inventory=figure_inventory,
                workdir=workdir,
                script=script,  # type: ignore[arg-type]
            )
            return state, check.model_dump_json(), check, False
        if name == "density_check":
            density = await run_density_check(
                deck_tex=str(args.get("deck_tex_excerpt", state.deck_tex)),
                bundles=bundles,
                script=script,  # type: ignore[arg-type]
                figure_inventory=figure_inventory,
            )
            return state, density.model_dump_json(), None, False
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
                False,
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
                False,
            )
        if name == "delete_frame":
            state = apply_delete_frame(state, frame_index=int(args["frame_index"]))
            return (
                state,
                json.dumps({"ok": True, "deleted": int(args["frame_index"])}),
                None,
                False,
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
            return state, json.dumps({"ok": True, "persisted": persist}), None, False
        if name == "done":
            if pending_done_check is None:
                return (
                    state,
                    json.dumps(
                        {
                            "error": (
                                "done() rejected — call compile_check first to "
                                "verify contracts"
                            )
                        }
                    ),
                    None,
                    False,
                )
            if pending_done_check.compile_errors:
                return (
                    state,
                    json.dumps(
                        {
                            "error": (
                                "done() rejected — compile_errors are non-empty; "
                                "fix them first"
                            ),
                            "compile_errors": pending_done_check.compile_errors,
                        }
                    ),
                    None,
                    False,
                )
            if pending_done_check.unrendered_math_frames:
                return (
                    state,
                    json.dumps(
                        {
                            "error": (
                                "done() rejected — contract #2 violated: "
                                "math-content frames lack math blocks"
                            ),
                            "unrendered_math_frames": [
                                f.model_dump()
                                for f in pending_done_check.unrendered_math_frames
                            ],
                        }
                    ),
                    None,
                    False,
                )
            return state, json.dumps({"ok": True, "done_accepted": True}), None, True
        return state, json.dumps({"error": f"unknown tool {name!r}"}), None, False
    except Exception as exc:  # noqa: BLE001 — surface to LLM as a normal error
        return (
            state,
            json.dumps({"error": f"{type(exc).__name__}: {exc}"}),
            None,
            False,
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
    reg = registry or PromptRegistry()
    prompt = reg.get("slides_agent/v1")
    if llm_acompletion is None:
        import litellm

        llm_acompletion = litellm.acompletion

    state = DeckState(
        deck_tex=existing_deck_tex or "",
        preamble=resolved_preamble,
        workdir=workdir,
        dirty=bool(existing_deck_tex),
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
        deck_state_label=(
            "EXISTING — diff-edit it"
            if existing_deck_tex
            else "EMPTY — call initial_draft first"
        ),
        existing_deck_block=existing_deck_tex or "(no deck yet)",
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
                    # Transient retry exhausted (or non-transient error). If we
                    # have ANY deck state from a prior tool call, ship it
                    # imperfect (mirrors the budget-exhaustion ship-imperfect
                    # path). If the deck is empty (initial_draft never landed),
                    # re-raise — there's nothing to ship.
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
                    # Agent gave up without done() — ship current state as imperfect.
                    break

                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.get("content"),
                        "tool_calls": tool_calls,
                    }
                )

                for call in tool_calls:
                    if tool_calls_used >= max_tool_calls:
                        break
                    tool_calls_used += 1
                    name = call["function"]["name"]
                    try:
                        args = json.loads(call["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {}

                    state, result_str, new_check, this_done = await _dispatch_tool_call(
                        name=name,
                        args=args,
                        state=state,
                        bundles=bundles,
                        figure_inventory=figure_inventory,
                        workdir=workdir,
                        session_id=session_id,
                        conn=conn,
                        script=script,
                        pending_done_check=pending_compile_check,
                    )
                    if new_check is not None:
                        pending_compile_check = new_check
                    if name == "replace_preamble":
                        try:
                            parsed = json.loads(result_str)
                        except json.JSONDecodeError:
                            parsed = {}
                        if isinstance(parsed, dict) and parsed.get("persisted"):
                            preamble_persisted = True
                    if this_done:
                        accepted_done = True

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

                    if accepted_done:
                        break
                if accepted_done:
                    break
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
