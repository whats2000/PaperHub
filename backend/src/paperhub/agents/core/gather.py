"""The ``gather`` tool: a small-model reading subagent that returns verbatim
chunks (never a summary) so the core agent can cite real text."""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from paperhub.agents.core.read_tools import _paper_error, register_read_tools
from paperhub.agents.core.tools import (
    Tier,
    ToolContext,
    ToolOutcome,
    ToolRegistry,
    ToolSpec,
    error_outcome,
)
from paperhub.config import small_tier_model
from paperhub.llm.adapter import ToolCall
from paperhub.llm.prompts.registry import PromptRegistry

GATHER_MAX_READS = 10
GATHER_MAX_TURNS = 16
GATHER_SLOT = "agent_gather/v1"
_LOOP_TOOLS = ("list_sections", "read_section", "search_chunks", "read_chunks")
_READ_TOOLS = ("read_section", "read_chunks")
_FORCE_SELECT: dict[str, Any] = {"type": "function", "function": {"name": "select"}}
_BUDGET_MSG = (
    f"Read budget of {GATHER_MAX_READS} is used up; call `select` now with "
    "the chunks you have read."
)
GATHER_EMPTY = "No chunk in the given papers answers this brief."


def format_gathered_chunk(
    chunk_id: int, title: str, section: str, text: str, reason: str,
) -> str:
    """One selected chunk as the core agent sees it (shared with the
    feedback export, which re-reads the chunk by id)."""
    title = title.replace('"', "'")
    sec = section.replace('"', "'")
    return (f'<chunk id="{chunk_id}" paper="{title}" section="{sec}">\n{text}\n'
            f"</chunk>\nreason: {reason.strip()}")

_SELECT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "select",
        "description": (
            "Final answer: the ids of chunks you fully read that answer the "
            "brief, each with a one-line reason. Empty list if none do."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "chunks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "reason": {"type": "string"},
                        },
                        "required": ["id", "reason"],
                    },
                }
            },
            "required": ["chunks"],
        },
    },
}


class SelectedChunk(BaseModel):
    id: int
    reason: str


class GatherSelection(BaseModel):
    chunks: list[SelectedChunk]


def _parse_selection(arguments: dict[str, Any]) -> GatherSelection:
    try:
        return GatherSelection.model_validate(arguments)
    except Exception:  # noqa: BLE001 - tolerate a malformed select
        return GatherSelection(chunks=[])


def _assistant_message(content: str, calls: list[ToolCall]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content or None,
        "tool_calls": [
            {
                "id": c.id,  # verbatim: Gemini ids carry a thought signature
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
            }
            for c in calls
        ],
    }


async def _chunk_rows(
    ctx: ToolContext, ids: list[int]
) -> dict[int, tuple[int, str, str]]:
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    async with ctx.conn.execute(
        "SELECT id, paper_content_id, section, text FROM chunks "  # noqa: S608
        f"WHERE id IN ({marks})",
        tuple(ids),
    ) as cur:
        return {r[0]: (r[1], r[2], r[3]) for r in await cur.fetchall()}


async def _scope_error(
    ctx: ToolContext, call: ToolCall, scope_ids: set[int]
) -> ToolOutcome | None:
    """Refuse read_chunks / search_chunks calls naming data outside the
    gather's papers (the core agent's own read tools may read any chunk)."""
    outside_msg = "{what} outside this gather's papers ({scope}); only those can be read."
    scope = ", ".join(str(p) for p in sorted(scope_ids))
    if call.name == "search_chunks":
        named = call.arguments.get("papers")
        if isinstance(named, list):
            bad = [p for p in named if p not in scope_ids]
            if bad:
                return error_outcome(outside_msg.format(
                    what=f"paper(s) {bad} are", scope=scope))
    if call.name == "read_chunks":
        ids = call.arguments.get("ids")
        if isinstance(ids, list) and ids and all(
            isinstance(i, int) and not isinstance(i, bool) for i in ids
        ):
            rows = await _chunk_rows(ctx, ids)
            bad = [i for i in ids if i in rows and rows[i][0] not in scope_ids]
            if bad:
                return error_outcome(outside_msg.format(
                    what=f"chunk id(s) {bad} are", scope=scope))
    return None


async def _gather_tool(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    papers, raw_brief = args.get("papers"), args.get("brief")
    if not isinstance(raw_brief, str) or not raw_brief.strip():
        return error_outcome("brief must be a non-empty string.")
    brief = raw_brief
    if (
        not isinstance(papers, list)
        or not papers
        or not all(isinstance(p, int) and not isinstance(p, bool) for p in papers)
    ):
        return error_outcome("papers must be a non-empty list of integer paper ids.")
    for p in papers:
        if (err := _paper_error(ctx, p)) is not None:
            return err
    titles = dict(ctx.enabled_papers)
    scope_ids = {p for p in papers}
    scoped = [(p, titles[p]) for p in dict.fromkeys(papers)]
    model = ctx.settings.agent_gather_model or small_tier_model()

    inner = ToolRegistry()
    register_read_tools(inner)
    inner_ctx = ToolContext(
        conn=ctx.conn, tracer=ctx.tracer, session_id=ctx.session_id,
        run_id=ctx.run_id, settings=ctx.settings, adapter=ctx.adapter,
        emit=ctx.emit, enabled_papers=scoped, history=[], state=ctx.state,
    )
    loop_schemas = [
        s for s in inner.schemas() if s["function"]["name"] in _LOOP_TOOLS
    ] + [_SELECT_SCHEMA]

    slot = PromptRegistry().get(GATHER_SLOT)
    papers_block = "\n".join(f"{p}: {t}" for p, t in scoped)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": slot.system.format(max_reads=GATHER_MAX_READS)},
        {"role": "user", "content": slot.user_template.format(
            papers_block=papers_block, brief=brief)},
    ]

    read_ids: list[int] = []
    sections_read: list[dict[str, Any]] = []
    call_log: list[dict[str, Any]] = []
    reads_used = 0
    selection: GatherSelection | None = None
    dropped_calls: list[dict[str, Any]] = []

    async with ctx.tracer.step(agent="agent", tool="agent:gather", model=model) as step:
        step.record_args({"brief": brief, "papers": [p for p, _ in scoped]})
        for _ in range(GATHER_MAX_TURNS):
            turn = await ctx.adapter.tool_turn(
                messages=messages, tools=loop_schemas, model=model,
            )
            dropped_calls += turn.dropped_calls
            if not turn.tool_calls:
                break
            messages.append(_assistant_message(turn.content, turn.tool_calls))
            chosen: GatherSelection | None = None
            for call in turn.tool_calls:
                if call.name == "select":
                    chosen = _parse_selection(call.arguments)
                    continue
                entry: dict[str, Any] = {"name": call.name, "arguments": call.arguments}
                if call.name not in _LOOP_TOOLS:
                    out = error_outcome(
                        f"Unknown tool {call.name!r}. Available tools: "
                        f"{', '.join((*_LOOP_TOOLS, 'select'))}."
                    )
                elif call.name in _READ_TOOLS and reads_used >= GATHER_MAX_READS:
                    out = error_outcome(_BUDGET_MSG)
                elif (
                    scope_err := await _scope_error(ctx, call, scope_ids)
                ) is not None:
                    out = scope_err
                else:
                    out = await inner._execute(inner_ctx, call)  # noqa: SLF001
                    if call.name in _READ_TOOLS and not out.record.get("error"):
                        reads_used += 1
                        if call.name == "read_section":
                            sections_read.append({
                                "paper": call.arguments.get("paper"),
                                "section": call.arguments.get("section"),
                            })
                read_ids += [i for i in out.chunk_ids_read if i not in read_ids]
                entry["chunk_ids"] = out.chunk_ids_read
                entry["error"] = out.record.get("error")
                if call.name == "search_chunks":
                    # Hits are snippets, not reads, so they are not in
                    # chunk_ids; keep them so a miss is told from a hit.
                    for key in ("hit_ids", "match", "query_as_sent"):
                        if key in out.record:
                            entry[key] = out.record[key]
                call_log.append(entry)
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": out.content}
                )
            if chosen is not None:
                selection = chosen
                break
        if selection is None:
            # Out of turns or the model stopped without selecting: force it.
            forced = await ctx.adapter.tool_turn(
                messages=messages, tools=[_SELECT_SCHEMA], model=model,
                tool_choice=_FORCE_SELECT,
            )
            dropped_calls += forced.dropped_calls
            sel_call = next((c for c in forced.tool_calls if c.name == "select"), None)
            selection = (
                _parse_selection(sel_call.arguments)
                if sel_call is not None else GatherSelection(chunks=[])
            )

        read_set = set(read_ids)
        kept: list[SelectedChunk] = []
        dropped_ids: list[int] = []
        dropped_reasons: dict[str, str] = {}
        for c in selection.chunks:
            if c.id not in read_set:
                dropped_ids.append(c.id)
                dropped_reasons[str(c.id)] = "not_read"
            elif all(k.id != c.id for k in kept):
                kept.append(c)
        rows = await _chunk_rows(ctx, [c.id for c in kept])
        for c in kept:
            if c.id not in rows or rows[c.id][0] not in scope_ids:
                dropped_ids.append(c.id)
                dropped_reasons[str(c.id)] = "out_of_scope"
        kept = [c for c in kept if c.id in rows and rows[c.id][0] in scope_ids]
        record: dict[str, Any] = {
            "brief": brief,
            "papers": [p for p, _ in scoped],
            "sections_read": sections_read,
            "chunk_ids_selected": [c.id for c in kept],
            "reasons": {str(c.id): c.reason for c in kept},
            "dropped_ids": dropped_ids,
            "dropped_reasons": dropped_reasons,
        }
        step.record_result({
            **record,
            "selection": [c.model_dump() for c in selection.chunks],
            "chunk_ids_read": read_ids,
            "tool_calls": call_log,
            "reads_used": reads_used,
            "dropped_calls": dropped_calls,
        })

    if not kept:
        return ToolOutcome(content=GATHER_EMPTY, record=record)
    parts = []
    for c in kept:
        pid, section, text = rows[c.id]
        parts.append(format_gathered_chunk(
            c.id, titles.get(pid, str(pid)), section, text, c.reason))
    return ToolOutcome(
        content="\n\n".join(parts), record=record,
        chunk_ids_read=[c.id for c in kept],
    )


def register_gather_tool(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(
        "gather",
        "Delegate broad reading to a cheap reading agent. Give it paper ids and "
        "a self-contained brief (what to find and why; it cannot see the "
        "conversation). Returns the relevant chunks verbatim with a one-line "
        "reason each, never a summary, so you can cite them.",
        {
            "type": "object",
            "properties": {
                "papers": {"type": "array", "items": {"type": "integer"}},
                "brief": {"type": "string"},
            },
            "required": ["papers", "brief"],
        },
        Tier.FREE, _gather_tool,
    ))
