"""Research Agent: paper_search tool-calling loop (SRS v2.3) + paper_qa stream."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from typing import Any

import aiosqlite
import litellm

from paperhub.agents.research_tools import (
    TOOL_SCHEMAS,
    add_paper_to_session_dispatch,
    find_related_papers_dispatch,
    search_arxiv_dispatch,
    search_library_dispatch,
)
from paperhub.agents.state import AgentState
from paperhub.llm.adapter import LlmAdapter
from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.pipelines.paper_pipeline import PaperPipeline
from paperhub.rag.retriever import Retriever
from paperhub.tracing.tracer import Tracer


@dataclass(frozen=True)
class FinalOnlyMessage:
    """Yielded by paper_qa_stream when the early-exit message should be sent
    as a single 'final' SSE event without any 'token' events. Used for the
    empty-references and empty-retrieved cases."""

    content: str


MAX_ARXIV_CALLS_PER_TURN = 3
# hard ceiling: ~ search_library + 3 × search_arxiv + 2 × add + slack
MAX_TOOL_ITERATIONS = 8


async def _references_block(
    conn: aiosqlite.Connection, session_id: int,
) -> tuple[int, str]:
    async with conn.execute(
        "SELECT pc.arxiv_id, pc.title, pc.year, pc.abstract "
        "FROM papers p JOIN paper_content pc ON pc.id = p.paper_content_id "
        "WHERE p.session_id = ? AND p.enabled = 1 "
        "ORDER BY p.added_at",
        (session_id,),
    ) as cur:
        rows = list(await cur.fetchall())
    if not rows:
        return 0, "(none — this session has no references yet)"
    lines: list[str] = []
    for r in rows:
        aid, title, year, abstract = r
        head = (
            f"- [arxiv:{aid}] {title} ({year or 'n.d.'})"
            if aid
            else f"- {title} ({year or 'n.d.'})"
        )
        snippet = (abstract or "")[:200].replace("\n", " ")
        ellipsis = "…" if abstract and len(abstract) > 200 else ""
        lines.append(f"{head}\n  abstract: {snippet}{ellipsis}")
    return len(rows), "\n".join(lines)


async def paper_search(
    state: AgentState,
    *,
    adapter: LlmAdapter | None,  # kept for interface parity; uses litellm directly
    tracer: Tracer,
    model: str,
    conn: aiosqlite.Connection,
    pipeline: PaperPipeline,
    registry: PromptRegistry | None = None,
    **litellm_kwargs: Any,
) -> str:
    """Tool-calling loop. Returns the final assistant message body (markdown).

    The chat endpoint surfaces this as a one-shot `final` SSE event — there
    is no token streaming inside paper_search (the trace panel + the
    automatic add_paper_to_session side-effects are what the user watches).
    """
    del adapter  # interface parity only
    user_message = state["user_message"]
    session_id = state["session_id"]
    history = state.get("history") or []

    n_refs, refs_block = await _references_block(conn, session_id)
    reg = registry or PromptRegistry()
    prompt = reg.get("paper_search/v1")
    system = prompt.system
    user = prompt.user_template.format(
        n_refs=n_refs, references_block=refs_block, user_message=user_message,
    )

    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    messages.extend(history)
    messages.append({"role": "user", "content": user})

    arxiv_calls = 0
    for iteration in range(MAX_TOOL_ITERATIONS):
        async with tracer.step(
            agent="research", tool="paper_search:plan", model=model,
        ) as step:
            step.record_args(
                {"iteration": iteration, "messages_len": len(messages)},
            )
            response = await litellm.acompletion(
                model=model,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                **litellm_kwargs,
            )
            msg = response["choices"][0]["message"]
            step.record_result(
                {
                    "had_tool_calls": bool(msg.get("tool_calls")),
                    "content_len": len(msg.get("content") or ""),
                },
            )

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            # Final response — clarification question OR summary of additions.
            return str(msg.get("content") or "(no response)")

        # Append the assistant turn that requested the tools, then dispatch each.
        messages.append(
            {
                "role": "assistant",
                "content": msg.get("content"),
                "tool_calls": tool_calls,
            },
        )

        for call in tool_calls:
            name = call["function"]["name"]
            args = json.loads(call["function"]["arguments"] or "{}")
            result: Any
            async with tracer.step(
                agent="research", tool=f"paper_search:{name}", model=None,
            ) as step:
                step.record_args(args)
                try:
                    if name == "search_library":
                        result = [
                            asdict(h)
                            for h in await search_library_dispatch(
                                conn=conn, session_id=session_id, **args,
                            )
                        ]
                    elif name == "search_arxiv":
                        if arxiv_calls >= MAX_ARXIV_CALLS_PER_TURN:
                            result = {
                                "error": "arxiv_call_cap_reached",
                                "cap": MAX_ARXIV_CALLS_PER_TURN,
                            }
                        else:
                            arxiv_calls += 1
                            result = [
                                asdict(h)
                                for h in await search_arxiv_dispatch(**args)
                            ]
                    elif name == "find_related_papers":
                        result = await find_related_papers_dispatch(**args)
                    elif name == "add_paper_to_session":
                        result = asdict(
                            await add_paper_to_session_dispatch(
                                pipeline=pipeline,
                                conn=conn,
                                session_id=session_id,
                                **args,
                            ),
                        )
                    else:
                        result = {"error": f"unknown_tool:{name}"}
                    step.record_result(
                        {
                            "summary": result
                            if isinstance(result, dict)
                            else {"count": len(result)},
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    result = {"error": str(exc), "tool": name}
                    step.record_result({"error": str(exc)})
                    step.mark_error(str(exc))

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": name,
                    "content": json.dumps(result, default=str),
                },
            )

    return (
        "I've reached the tool-call limit for this turn. "
        "Try asking again with a more specific question."
    )


async def paper_qa_stream(
    state: AgentState,
    *,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    retriever: Retriever,
    conn: aiosqlite.Connection,
    **adapter_kwargs: Any,
) -> AsyncIterator[str | FinalOnlyMessage]:
    """Stream paper_qa tokens.

    Workflow: resolve enabled_paper_content_ids → retrieve → rerank → format
    chunk context → stream LLM answer with [chunk:<id>] markers.
    """
    user_message = state["user_message"]
    session_id = state["session_id"]

    async with tracer.step(
        agent="research", tool="paper_qa:resolve", model=None,
    ) as step:
        step.record_args({"session_id": session_id})
        async with conn.execute(
            "SELECT paper_content_id FROM papers "
            "WHERE session_id = ? AND enabled = 1",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        enabled_ids = [int(r[0]) for r in rows]
        step.record_result({"enabled_paper_content_ids": enabled_ids})

    if not enabled_ids:
        yield FinalOnlyMessage(
            "No references are enabled for this session. Add a paper to the "
            "Reference Sources panel first, then ask again."
        )
        return

    placeholders = ",".join("?" * len(enabled_ids))
    async with conn.execute(
        f"SELECT COUNT(*) FROM chunks WHERE paper_content_id IN ({placeholders})",  # noqa: S608
        enabled_ids,
    ) as cur:
        row = await cur.fetchone()
    corpus_size = int(row[0]) if row else 0

    async with tracer.step(
        agent="research", tool="paper_qa:retrieve", model=None,
    ) as step:
        step.record_args({"query": user_message, "corpus_size": corpus_size})
        retrieved = retriever.retrieve(
            user_message,
            enabled_paper_content_ids=enabled_ids,
            corpus_size=corpus_size,
            top_k=10,
        )
        step.record_result({"chunk_ids": [r.chunk_id for r in retrieved]})

    if not retrieved:
        yield FinalOnlyMessage("No relevant chunks were found in the enabled references.")
        return

    chunks_context = "\n\n".join(
        f"[chunk:{r.chunk_id}] (paper {r.paper_content_id})\n{r.text}"
        for r in retrieved
    )

    async with tracer.step(
        agent="research", tool="paper_qa:generate", model=model,
    ) as step:
        step.record_args({"chunk_count": len(retrieved)})
        collected: list[str] = []
        async for token in adapter.stream(
            slot="paper_qa/v1",
            variables={
                "user_message": user_message,
                "chunks_context": chunks_context,
            },
            model=model,
            history=state.get("history"),
            **adapter_kwargs,
        ):
            collected.append(token)
            yield token
        step.record_result({"length": sum(len(c) for c in collected)})
