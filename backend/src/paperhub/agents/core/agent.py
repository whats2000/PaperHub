"""The core agent loop (SRS §III-10.4, FR-21).

One flagship model holds the conversation and, step by step, calls tools
(read, gather, search, query, remember, slides, ideation) or answers. Each
loop step is one ``agent:step`` trace row; each tool call is its own
``tool:<name>`` row (written by ``ToolRegistry.run``).

Streaming (ruling R7): text deltas stream live as Token events. When a
turn that streamed text turns out to call tools, an answer_reset event
tells the UI to clear that preamble. Final carries the citation-validated
answer, which replaces the streamed text.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

from paperhub.agents.core.citations import (
    cited_figures,
    cited_ids,
    cited_views,
    figure_key,
    validate_citations,
    validate_figure_citations,
    validate_view_citations,
    verify_quotes,
    view_key,
)
from paperhub.agents.core.context import (
    AGENT_KEEP_TURNS,
    ContextBlocks,
    TurnTokenCounter,
    count_tokens,
    load_conversation,
    load_enabled_papers,
    render_session_state,
    session_state_parts,
)
from paperhub.agents.core.figures import IMAGE_TOKENS, images_message
from paperhub.agents.core.tools import Tier, ToolContext, ToolOutcome, ToolRegistry
from paperhub.agents.idea_context import build_idea_context
from paperhub.agents.slide_context import build_slide_context
from paperhub.config import Settings, flagship_tier_model
from paperhub.db.connection import write_transaction
from paperhub.llm.adapter import ToolCall, ToolTurn
from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.models.domain import AgentState
from paperhub.tracing.tracer import Tracer

AGENT_MAX_STEPS = 12
AGENT_MAX_TURN_TOKENS = 120_000
CORE_AGENT_SLOT = "core_agent/v8"
BUDGET_INSTRUCTION = (
    "The step budget for this turn is used up. Do not call tools. Answer now "
    "with what you have, citing only chunks you read, and say plainly what "
    "you did not get to."
)
EMPTY_TURN_NUDGE = (
    "Your last turn was empty. Answer my message now from what you have "
    "read, or call a tool if you still need something."
)
EMPTY_ANSWER = (
    "I could not finish an answer this turn. Please ask again or narrow the "
    "question."
)
PENDING_RETRY_CONTENT = (
    "Still waiting for the user to confirm {summary}; do not call it again.")
DUPLICATE_CALL_CONTENT = (
    "Duplicate of an earlier identical call in this step; not run again. "
    "Its result is above.")
# Tool UI events kept in the agent record so a reloaded chat can replay the
# chips and confirmation cards (ruling R11), in the order they streamed.
REPLAYED_UI_EVENTS = frozenset({"paper_added", "memory_saved", "confirm_request"})
_PROPOSAL = re.compile(r"<proposal>(.*?)</proposal>", re.DOTALL)
_WORK_RECORD = re.compile(r"\s*<work_record>.*?</work_record>", re.DOTALL)


# ------------------------------------------------------------ events


@dataclass(frozen=True)
class Token:
    text: str


@dataclass(frozen=True)
class Step:
    record: dict[str, Any]


@dataclass(frozen=True)
class ToolEvent:
    """A UI event a tool returned (``search_results``, ``deck``,
    ``paper_added``, ``memory_saved``, ``confirm_request``, ``tool_step``)."""

    name: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class Final:
    text: str
    record: dict[str, Any]


AgentEvent = Token | Step | ToolEvent | Final


def prompt_fingerprint(
    schemas: list[dict[str, Any]], system_template: str,
) -> dict[str, str]:
    """What a turn's prompt was built from: the prompt slot, and hashes of the
    tool schemas and the unfilled system template. The feedback export
    refuses to rebuild a turn whose fingerprint no longer matches."""
    return {
        "prompt_slot": CORE_AGENT_SLOT,
        "tools_sha256": hashlib.sha256(
            json.dumps(schemas, sort_keys=True).encode("utf-8")).hexdigest(),
        "system_sha256": hashlib.sha256(
            system_template.encode("utf-8")).hexdigest(),
    }


async def _no_emit(_name: str, _payload: dict[str, Any]) -> None:
    return None


@dataclass
class AgentDeps:
    adapter: Any  # LlmAdapter with ``tool_turn``
    tracer: Tracer
    conn: aiosqlite.Connection
    settings: Settings
    registry: ToolRegistry
    emit: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None
    mcp_registry: Any = None
    # Set by the chat layer's Stop branch before it closes the loop: only then
    # is a GeneratorExit a Stop (else it is a caller error or GC finalising).
    stop_requested: bool = False


def _assistant_message(content: str, calls: list[ToolCall]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content or None,
        "tool_calls": [
            {
                "id": c.id,  # verbatim: Gemini ids carry a thought signature
                "type": "function",
                "function": {"name": c.name,
                             "arguments": json.dumps(c.arguments, ensure_ascii=False)},
            }
            for c in calls
        ],
    }


def _result_status(outcome: ToolOutcome) -> str:
    if outcome.record.get("error"):
        return "error"
    if outcome.record.get("pending_action_id"):
        return "pending"
    return "ok"


def _result_ids(outcome: ToolOutcome) -> list[int]:
    if outcome.chunk_ids_read:
        return list(outcome.chunk_ids_read)
    for key in ("hit_ids", "chunk_ids"):
        raw = outcome.record.get(key)
        if isinstance(raw, list):
            return [i for i in raw if isinstance(i, int)]
    return []


# Tools whose ``paper`` / ``papers`` argument is a paper they read; an action
# on a paper (remove_paper) does not count as reading it.
_READING_TOOLS = frozenset({
    "list_sections", "read_section", "list_figures_tables",
    "read_layout_object", "view_figure", "gather",
})


def _papers_from_args(calls: list[ToolCall]) -> list[int]:
    out: list[int] = []
    for c in calls:
        if c.name not in _READING_TOOLS:
            continue
        for key in ("paper", "papers"):
            raw = c.arguments.get(key)
            items = raw if isinstance(raw, list) else [raw]
            out += [p for p in items if isinstance(p, int) and not isinstance(p, bool)]
    return out


async def _papers_of_chunks(conn: aiosqlite.Connection, ids: list[int]) -> list[int]:
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    async with conn.execute(
        f"SELECT DISTINCT paper_content_id FROM chunks WHERE id IN ({marks})",  # noqa: S608
        tuple(ids),
    ) as cur:
        return [int(r[0]) for r in await cur.fetchall()]


_QueueItem = tuple[str, Any]  # ("token", str) | ("event", (name, payload))


def _call_key(call: ToolCall) -> str:
    return call.name + ":" + json.dumps(call.arguments, sort_keys=True, default=str)


def _confirm_summary(outcome: ToolOutcome, name: str) -> str:
    """The pending action as the model was told it (``tool target``)."""
    return str(outcome.record.get("args_summary") or name)


@dataclass
class _StepResult:
    content: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    outcomes: list[ToolOutcome] = field(default_factory=list)
    empty: bool = False
    streamed: bool = False
    final_from_tool: bool = False


def _tool_final_answer(res: _StepResult, record: dict[str, Any]) -> str | None:
    """A step whose ONLY call ran and returned a user-ready ``final_answer``
    (query_library with ``answer_user``) ends the turn with it: another model
    step would only restate it."""
    if len(res.calls) != 1 or len(res.outcomes) != 1:
        return None
    if record.get("skipped_pending_retry"):
        return None
    outcome = res.outcomes[0]
    if _result_status(outcome) != "ok":
        return None
    text = (outcome.final_answer or "").strip()
    return text or None


async def _run_step(
    *, deps: AgentDeps, ctx: ToolContext, model: str,
    messages: list[dict[str, Any]], schemas: list[dict[str, Any]], force: bool,
    record: dict[str, Any], queue: asyncio.Queue[_QueueItem | None],
    allowed_ids: set[int], retry_empty: bool, pending: dict[str, str],
    allowed_figures: set[str] | None = None,
    allowed_views: set[str] | None = None,
    allow_tool_answer: bool = False,
) -> _StepResult:
    """One ``agent:step``: the model turn (its text deltas go to ``queue`` as
    they arrive), then its tool calls run concurrently. If a turn that
    streamed text turns out to call tools, an ``answer_reset`` event precedes
    its tool events. Tool UI events go to ``queue`` in call order as each
    call returns; ``None`` closes the queue. A call repeating one that is
    already waiting for the user's confirmation (``pending``: call key ->
    summary) is not run again. The answer (no tool calls) is citation-checked
    into ``record['answer']``. ``empty`` is set when the model returned
    nothing at all and ``retry_empty`` allows asking again. With
    ``allow_tool_answer`` (the turn's first tool step), a lone call's
    ``final_answer`` becomes the answer (``record['answer_from_tool']``)."""
    res = _StepResult()
    parts: list[str] = []

    async def on_token(text: str) -> None:
        parts.append(text)
        res.streamed = True
        queue.put_nowait(("token", text))

    try:
        async with deps.tracer.step(agent="agent", tool="agent:step", model=model) as st:
            st.record_args({"step": record["step"], "forced": force})
            turn: ToolTurn = await deps.adapter.tool_turn(
                messages=messages, tools=schemas, model=model, on_token=on_token,
                tool_choice="none" if force else "auto",
            )
            res.content = turn.content or "".join(parts)
            if turn.dropped_calls:
                record["dropped_calls"] = turn.dropped_calls
            if turn.usage.get("finish_reason"):
                record["finish_reason"] = turn.usage["finish_reason"]
            res.calls = [] if force else list(turn.tool_calls)
            if force and turn.tool_calls:
                record["ignored_calls"] = [c.name for c in turn.tool_calls]
            if res.calls:
                await _run_calls(deps, ctx, res, record, queue, pending)
                direct = _tool_final_answer(res, record) if allow_tool_answer else None
                if direct is not None:
                    answer, removed = validate_citations(direct, allowed_ids)
                    answer = await _verify_quotes(deps.conn, answer, allowed_ids, record)
                    answer = _check_evidence(answer, allowed_figures or set(),
                                            allowed_views or set(), record)
                    record["answer"] = answer
                    record["citations_removed"] = removed
                    record["answer_from_tool"] = res.calls[0].name
                    res.final_from_tool = True
            elif not res.content.strip() and retry_empty:
                res.empty = True
                record["empty_response"] = True
                if res.streamed:
                    queue.put_nowait(("event", ("answer_reset", {})))
            else:
                raw = _WORK_RECORD.sub("", res.content).strip() or EMPTY_ANSWER
                answer, removed = validate_citations(raw, allowed_ids)
                answer = await _verify_quotes(deps.conn, answer, allowed_ids, record)
                answer = _check_evidence(answer, allowed_figures or set(),
                                        allowed_views or set(), record)
                record["answer"] = answer
                record["citations_removed"] = removed
            st.record_result(record)
    finally:
        queue.put_nowait(None)
    return res


async def _verify_quotes(
    conn: aiosqlite.Connection, answer: str, allowed_ids: set[int], record: dict[str, Any],
) -> str:
    """A quotation cited to a chunk must be in that chunk (moved to the read
    chunk that holds it, else its citation is removed); fixes are traced."""
    if not allowed_ids or ('"' not in answer and "“" not in answer):
        return answer
    ids = sorted(allowed_ids)
    texts: dict[int, str] = {}
    for i in range(0, len(ids), 500):
        batch = ids[i:i + 500]
        marks = ",".join("?" * len(batch))
        async with conn.execute(
            f"SELECT id, text FROM chunks WHERE id IN ({marks})", batch,  # noqa: S608
        ) as cur:
            texts.update({int(r[0]): str(r[1]) for r in await cur.fetchall()})
    answer, fixes = verify_quotes(answer, texts)
    if fixes:
        record["quote_citations_fixed"] = fixes
    return answer


def _check_evidence(
    answer: str, figures: set[str], views: set[str], record: dict[str, Any],
) -> str:
    """Drop ``[fig:]`` / ``[view:]`` citations of figures and page regions
    the agent never looked at; a region inside one it viewed snaps to it."""
    answer, removed = validate_figure_citations(answer, figures)
    if removed:
        record["figure_citations_removed"] = removed
    answer, removed_views, snapped = validate_view_citations(answer, views)
    if removed_views:
        record["view_citations_removed"] = removed_views
    if snapped:
        record["view_citations_snapped"] = [list(pair) for pair in snapped]
    return answer


def _live_beat(run_id: int, tool: str) -> _QueueItem:
    """A live-only trace beat (negative step_index, never persisted): the
    frontend shows the latest one as the in-progress phase at the trace tail,
    the same channel the Report stage heartbeats use."""
    return ("event", ("tool_step", {"record": {
        "run_id": run_id, "step_index": -1, "branch": "", "parent_step": None, "agent": "agent",
        "tool": tool, "model": "", "args_redacted_json": None,
        "result_summary_json": {"stage": True, "elapsed_s": 0},
        "latency_ms": 0, "token_in": 0, "token_out": 0, "status": "ok",
        "error": None,
    }}))


async def _run_calls(
    deps: AgentDeps, ctx: ToolContext, res: _StepResult, record: dict[str, Any],
    queue: asyncio.Queue[_QueueItem | None], pending: dict[str, str],
) -> None:
    calls = res.calls
    record["tool_calls"] = [{"name": c.name, "args": c.arguments} for c in calls]
    if res.content:
        record["preamble"] = res.content
    if res.streamed:
        queue.put_nowait(("event", ("answer_reset", {})))

    async def waiting(summary: str) -> ToolOutcome:
        return ToolOutcome(content=PENDING_RETRY_CONTENT.format(summary=summary),
                           record={"skipped_pending_retry": True})

    async def duplicate_of(first: asyncio.Task[ToolOutcome], name: str) -> ToolOutcome:
        outcome = await first
        if outcome.record.get("pending_action_id"):
            return await waiting(_confirm_summary(outcome, name))
        return ToolOutcome(content=DUPLICATE_CALL_CONTENT,
                           record={"skipped_pending_retry": True})

    def is_confirm(name: str) -> bool:
        try:
            return deps.registry.get(name).tier is Tier.CONFIRM
        except KeyError:
            return False

    def is_serial(name: str) -> bool:
        try:
            return deps.registry.get(name).serial
        except KeyError:
            return False

    async def after(prev: asyncio.Task[ToolOutcome] | None, call: ToolCall) -> ToolOutcome:
        if prev is not None:
            await asyncio.wait({prev})
        return await deps.registry.run(ctx, call)

    tasks: list[asyncio.Task[ToolOutcome]] = []
    skipped: list[bool] = []
    first_in_step: dict[str, asyncio.Task[ToolOutcome]] = {}
    last_serial: asyncio.Task[ToolOutcome] | None = None
    # A running tool has no tool_calls row until it closes; name it live.
    queue.put_nowait(_live_beat(ctx.run_id, "agent:" + ", ".join(dict.fromkeys(
        c.name for c in calls))))
    for c in calls:
        key = _call_key(c)
        if key in pending:
            tasks.append(asyncio.create_task(waiting(pending[key])))
            skipped.append(True)
        elif key in first_in_step and is_confirm(c.name):
            # Same confirm-tier call twice in one step: one pending action.
            tasks.append(asyncio.create_task(
                duplicate_of(first_in_step[key], c.name)))
            skipped.append(True)
        elif is_serial(c.name):
            task = asyncio.create_task(after(last_serial, c))
            last_serial = task
            first_in_step.setdefault(key, task)
            tasks.append(task)
            skipped.append(False)
        else:
            task = asyncio.create_task(deps.registry.run(ctx, c))
            first_in_step.setdefault(key, task)
            tasks.append(task)
            skipped.append(False)
    try:
        for c, task in zip(calls, tasks, strict=True):
            outcome = await task
            res.outcomes.append(outcome)
            if outcome.record.get("pending_action_id"):
                pending[_call_key(c)] = _confirm_summary(outcome, c.name)
            for event in outcome.events:
                queue.put_nowait(("event", event))
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    results = []
    for c, o, skip in zip(calls, res.outcomes, skipped, strict=True):
        entry: dict[str, Any] = {
            "name": c.name, "status": "pending" if skip else _result_status(o),
            "ids": _result_ids(o)}
        if skip:
            entry["skipped_pending_retry"] = True
        results.append(entry)
    record["tool_results"] = results
    if any(skipped):
        record["skipped_pending_retry"] = True


async def run_core_agent(
    state: AgentState, deps: AgentDeps,
) -> AsyncGenerator[AgentEvent, None]:
    """Run one chat turn. Yields ``Token`` per streamed text delta (live),
    ``ToolEvent`` per tool UI event (in call order; ``answer_reset`` when
    streamed text turned out to be a tool-calling preamble), ``Step`` per
    loop step and a closing ``Final`` whose text is the citation-validated
    answer (it replaces the streamed text). Writes the turn's work record to
    ``runs.agent_record_json`` — also, with ``error``, when the model call
    fails (the exception is re-raised)."""
    settings = deps.settings
    model = settings.agent_model or flagship_tier_model()
    max_steps = settings.agent_max_steps or AGENT_MAX_STEPS
    max_tokens = settings.agent_max_turn_tokens or AGENT_MAX_TURN_TOKENS
    keep_turns = settings.agent_keep_turns or AGENT_KEEP_TURNS

    session_id = int(state["session_id"])
    run_id = int(state["run_id"])
    user_message = str(state["user_message"])
    history = list(state.get("history") or [])
    conn = deps.conn

    # Session-scoped context the tools and the prompt both read.
    idea = await build_idea_context(conn, session_id=session_id)
    idea_id = idea.idea_id if idea is not None else None
    if "idea_context" not in state:
        state["idea_context"] = idea.block if idea is not None else None
    if state.get("slide_attached") and not state.get("slide_context"):
        state["slide_context"] = await build_slide_context(
            conn, session_id=session_id,
            current_view_page=state.get("current_view_page"),
        )
    papers = await load_enabled_papers(conn, session_id)
    state_parts = await session_state_parts(conn, session_id, state, papers=papers)
    session_state = render_session_state(state_parts)
    conv = await load_conversation(conn, session_id, history, keep_turns=keep_turns)

    slot = PromptRegistry().get(CORE_AGENT_SLOT)
    instructions = slot.system.format(max_steps=max_steps)
    state_message = slot.user_template.format(session_state=session_state)
    schemas = deps.registry.schemas()
    this_turn: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
    # The fixed blocks are tokenised once per turn (not per step).
    token_counter = TurnTokenCounter(ContextBlocks(
        instructions=instructions, tools=schemas, session_state=state_message,
        conversation=conv.messages, this_turn=[],
    ))

    # Ruling R10: a running tool may push transient progress (stage
    # heartbeats, its pipeline's tool_step rows) through ctx.emit. It goes
    # on the active step's queue, so it is yielded live and de-duplicated
    # like any other tool event; final cards still come back in
    # ToolOutcome.events when the tool finishes.
    active: dict[str, asyncio.Queue[_QueueItem | None] | None] = {"queue": None}
    fallback_emit = deps.emit or _no_emit

    async def emit_live(name: str, payload: dict[str, Any]) -> None:
        q = active["queue"]
        if q is None:
            await fallback_emit(name, payload)
        else:
            q.put_nowait(("event", (name, payload)))

    ctx = ToolContext(
        conn=conn, tracer=deps.tracer, session_id=session_id, run_id=run_id,
        settings=settings, adapter=deps.adapter, emit=emit_live,
        enabled_papers=[(p.id, p.title) for p in papers], history=history,
        state=state, mcp_registry=deps.mcp_registry,
    )

    read_ids: list[int] = []
    viewed_figures: list[str] = []  # "<paper>/<figure id>" seen this turn
    viewed_views: list[str] = []    # "<paper>/<page>/<box>" seen this turn
    tools_used: list[str] = []
    touched_papers: list[int] = []
    pending: dict[str, str] = {}
    tool_tokens = 0
    forwarded_steps: set[int] = set()
    step_no = 0          # every loop step (trace numbering)
    budget_steps = 0     # steps that count against max_steps
    empty_retries = 0
    answer = ""
    removed: list[int] = []
    budget_reached = False
    nudge_next = False
    answer_streamed = False

    async def save_record(extra: dict[str, Any]) -> dict[str, Any]:
        papers_touched = list(dict.fromkeys(
            touched_papers + await _papers_of_chunks(conn, read_ids)))
        work = {"tools": tools_used, "read": read_ids, "cited": cited_ids(answer),
                "papers": papers_touched, "steps": step_no, **extra}
        if viewed_figures or cited_figures(answer):
            work["figures"] = viewed_figures
            work["cited_figures"] = cited_figures(answer)
        if viewed_views or cited_views(answer):
            work["views"] = viewed_views
            work["cited_views"] = cited_views(answer)
        async with write_transaction(conn):
            await conn.execute(
                "UPDATE runs SET agent_record_json = ? WHERE id = ?",
                (json.dumps(work), run_id),
            )
        return work

    try:
        while True:
            step_no += 1
            if step_no > 1:
                # Papers added or removed since the last step (by a tool, a
                # confirmed action or the UI) are what the tools now see.
                ctx.enabled_papers[:] = [
                    (p.id, p.title)
                    for p in await load_enabled_papers(conn, session_id)]
            force = budget_steps >= max_steps or tool_tokens >= max_tokens
            messages = [
                {"role": "system", "content": instructions},
                {"role": "user", "content": state_message},
                *conv.messages,
                *this_turn,
            ]
            if force:
                budget_reached = True
                messages.append({"role": "user", "content": BUDGET_INSTRUCTION})
            elif nudge_next:
                messages.append({"role": "user", "content": EMPTY_TURN_NUDGE})
                nudge_next = False
            record: dict[str, Any] = {
                "step": step_no, "model": model,
                "context_tokens": token_counter.counts(this_turn),
            }
            if force:
                record["budget_reached"] = True
            if step_no == 1:
                # Enough to rebuild this turn's prompt (feedback export):
                # papers are re-read by id; the mutable parts are kept as is.
                record["session_state"] = {
                    "papers": [p.id for p in papers], "idea_id": idea_id,
                    "slide_attached": bool(state.get("slide_context")),
                    "conversation_messages": len(conv.messages),
                    "folded_turns": conv.folded_turns,
                    "kept_ids": sorted(conv.kept_ids),
                    **({"kept_figures": sorted(conv.kept_figures)}
                       if conv.kept_figures else {}),
                    **({"kept_views": sorted(conv.kept_views)}
                       if conv.kept_views else {}),
                    "max_steps": max_steps, "keep_turns": keep_turns,
                    "idea_context": state_parts.idea_context,
                    "slide_context": state_parts.slide_context,
                    "memories": state_parts.memories,
                    "ui_language": state_parts.ui_language,
                    **prompt_fingerprint(schemas, slot.system),
                }
            # The step runs in its own task so text deltas and each tool's UI
            # events reach the caller while the step is still running.
            queue: asyncio.Queue[_QueueItem | None] = asyncio.Queue()
            if step_no > 1:
                # Replaces the previous step's tool beat at the trace tail.
                queue.put_nowait(_live_beat(run_id, "agent:thinking"))
            active["queue"] = queue
            step_task = asyncio.create_task(_run_step(
                deps=deps, ctx=ctx, model=model, messages=messages,
                schemas=schemas, force=force, record=record, queue=queue,
                allowed_ids=set(read_ids) | conv.kept_ids,
                allowed_figures=set(viewed_figures) | conv.kept_figures,
                allowed_views=set(viewed_views) | conv.kept_views,
                retry_empty=empty_retries == 0, pending=pending,
                # Only the turn's first tool step: later, the model has read
                # or done something the tool's answer would not include.
                allow_tool_answer=len(this_turn) == 1,
            ))
            try:
                while (item := await queue.get()) is not None:
                    kind, value = item
                    if kind == "token":
                        yield Token(str(value))
                        continue
                    name, payload = value
                    if name == "tool_step":
                        idx = (payload.get("record") or {}).get("step_index")
                        # Negative = Report stage heartbeats, which reuse
                        # indexes across stages: never de-duplicated.
                        if isinstance(idx, int) and idx >= 0:
                            if idx in forwarded_steps:
                                continue
                            forwarded_steps.add(idx)
                    yield ToolEvent(name, payload)
                res = await step_task
            finally:
                active["queue"] = None
                if not step_task.done():
                    step_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await step_task
            if res.empty:
                # Seen live on Gemini: a turn with neither text nor tool
                # calls. The identical request tends to come back empty again,
                # so the one retry carries a nudge; it does not use a budget
                # step.
                empty_retries += 1
                nudge_next = True
            elif res.calls:
                budget_steps += 1
                this_turn.append(_assistant_message(res.content, res.calls))
                for c, o in zip(res.calls, res.outcomes, strict=True):
                    this_turn.append(
                        {"role": "tool", "tool_call_id": c.id, "content": o.content})
                    tools_used.append(c.name)
                    for i in o.chunk_ids_read:
                        if i not in read_ids:
                            read_ids.append(i)
                    tool_tokens += count_tokens(o.content)
                for c, o in zip(res.calls, res.outcomes, strict=True):
                    paper, fig = o.record.get("paper"), o.record.get("figure")
                    if (c.name == "view_figure" and _result_status(o) == "ok"
                            and isinstance(paper, int) and isinstance(fig, str)):
                        key = figure_key(paper, fig)
                        if key not in viewed_figures:
                            viewed_figures.append(key)
                    page, box = o.record.get("page"), o.record.get("box")
                    if (_result_status(o) == "ok" and isinstance(paper, int)
                            and isinstance(page, int) and isinstance(box, list)
                            and len(box) == 4):
                        vkey = view_key(paper, page, (box[0], box[1], box[2], box[3]))
                        if vkey not in viewed_views:
                            viewed_views.append(vkey)
                images = [i for o in res.outcomes for i in o.images]
                if images:
                    # Tool messages carry text only on most providers: the
                    # step's figure images follow them as one user message.
                    this_turn.append(images_message(images))
                    tool_tokens += IMAGE_TOKENS * len(images)
                touched_papers += _papers_from_args(
                    [c for c, o in zip(res.calls, res.outcomes, strict=True)
                     if _result_status(o) == "ok"])
                if res.final_from_tool:
                    answer = str(record["answer"])
                    removed = list(record["citations_removed"])
            else:
                answer = str(record["answer"])
                removed = list(record["citations_removed"])
                answer_streamed = res.streamed
            yield Step(record)
            if res.final_from_tool or (not res.calls and not res.empty):
                break
    except (asyncio.CancelledError, GeneratorExit) as exc:
        # Stop: keep what already happened (a paper attached, a memory saved)
        # so the stopped turn still shows its chips and Undo. A GeneratorExit
        # is a Stop only when the caller says so (it landed while the caller
        # handled a yielded event); otherwise the caller failed or the loop
        # is being garbage-collected, and the run's own record stands.
        if isinstance(exc, asyncio.CancelledError) or deps.stop_requested:
            await asyncio.shield(save_record({"cancelled": True, "ui_events": [
                {"event": n, "data": dict(p)} for n, p in ctx.done_events
                if n in REPLAYED_UI_EVENTS]}))
        raise
    except Exception as exc:
        await save_record({"error": str(exc)})
        raise

    if not answer_streamed:
        # Nothing streamed for the answer (e.g. the fallback sentence).
        yield Token(answer)
    work_record = await save_record({})
    yield Final(answer, {
        **work_record,
        "proposals": [p.strip() for p in _PROPOSAL.findall(answer) if p.strip()],
        "citations_removed": removed,
        "budget_reached": budget_reached,
        "empty_retries": empty_retries,
    })


__all__ = [
    "AGENT_MAX_STEPS", "AGENT_MAX_TURN_TOKENS", "AgentDeps", "AgentEvent",
    "Final", "Step", "Token", "ToolEvent", "run_core_agent",
]
