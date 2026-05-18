"""Research Agent subgraph (Plan C v4): paper_search + paper_qa as
multi-node LangGraph topologies.

This module retires the v3 2-node passthrough wrapper around the umbrella
async generators in ``research.py``. The Research Agent's internal
control flow is now expressed as graph edges:

paper_search subgraph (cyclic tool-calling loop):

    START → ps_plan
    ps_plan → conditional_edges → {
        "tool_calls":  ps_dispatch_tools  → ps_plan   (loop)
        "done":        ps_finalize        → END
    }

paper_qa subgraph (count-branching):

    START → pq_resolve → conditional_edges → {
        "empty":   pq_empty       → END
        "single":  pq_single      → END
        "map":     pq_map         → pq_synthesize → END
    }

Outer dispatcher subgraph (the Research Agent proper):

    START → research_dispatch → conditional_edges → {
        "paper_search": paper_search_subgraph (compiled, embedded as node)
        "paper_qa":     paper_qa_subgraph     (compiled, embedded as node)
    }
    each → END

Streaming contract (consumed by ``api/chat.py``):

  * ``stream_mode="custom"`` carries the per-node ``tool_step`` /
    ``search_results`` / ``token`` events written via
    ``langgraph.config.get_stream_writer()``;
  * ``stream_mode="values"`` carries the final state snapshot so the chat
    layer can lift ``state["final_response"]``.

The compiled subgraph is a Runnable so LangGraph happily embeds it as a
node via ``add_node("paper_search", compiled_ps_subgraph)`` — confirmed on
LangGraph 1.2.0.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import aiosqlite
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from paperhub.agents.research import (
    MAX_TOOL_ITERATIONS,
    FinalOnlyMessage,
    _build_paper_search_messages,
    _dispatch_paper_search_tool_call,
    _extract_candidates,
    _paper_qa_map_one,
    _paper_qa_single_stream,
    _paper_qa_synthesize_stream,
    _paper_search_plan_step,
    _resolve_enabled_papers,
)
from paperhub.agents.research import (
    paper_qa_stream as _default_paper_qa_stream,
)
from paperhub.agents.research import (
    paper_search as _default_paper_search,
)
from paperhub.agents.state import AgentState
from paperhub.db.tool_calls import drain_tool_calls_since
from paperhub.llm.adapter import LlmAdapter
from paperhub.mcp.registry import MCPRegistry
from paperhub.pipelines.paper_pipeline import PaperPipeline
from paperhub.rag.retriever import Retriever
from paperhub.tracing.tracer import Tracer

ResearchExtraKwargs = dict[str, Any]
PaperSearchFn = Callable[..., AsyncIterator[Any]]
PaperQaStreamFn = Callable[..., AsyncIterator[Any]]


@dataclass
class ResearchDeps:
    """Per-request dependencies bound into the research subgraph at build
    time via closure. Rebuilt every chat turn (LangGraph compile is cheap).

    ``paper_search_fn`` / ``paper_qa_stream_fn`` are retained for
    backwards-compatibility with callers that want to inject a fake
    end-to-end async generator (e.g. ``chat.py`` exposes the legacy
    ``paper_search`` / ``paper_qa_stream`` module-level attributes so
    ``test_chat_sse.py`` can monkeypatch them with fakes — that path
    bypasses the subgraph entirely and feeds a fake generator straight
    into the SSE translation loop). The subgraph nodes themselves call
    the underlying helpers directly via the other fields.
    """

    adapter: LlmAdapter
    tracer: Tracer
    paper_qa_model: str
    conn: aiosqlite.Connection
    pipeline: PaperPipeline
    retriever: Retriever
    mcp_registry: MCPRegistry
    # Optional adapter kwargs (e.g. ``mock_response`` injected by smoke tests).
    adapter_kwargs: ResearchExtraKwargs | None = None
    # Legacy generator hooks (see class docstring); not consumed by the
    # subgraph nodes themselves, but exposed so the chat layer can fall
    # back to a fake generator under monkeypatching.
    paper_search_fn: PaperSearchFn = field(default=_default_paper_search)
    paper_qa_stream_fn: PaperQaStreamFn = field(default=_default_paper_qa_stream)


def _kwargs(deps: ResearchDeps) -> ResearchExtraKwargs:
    return dict(deps.adapter_kwargs or {})


# ---------------------------------------------------------------------------
# paper_search subgraph
# ---------------------------------------------------------------------------


def build_paper_search_subgraph(deps: ResearchDeps) -> Any:
    """Compile the paper_search cyclic tool-calling subgraph.

    Topology::

        START → ps_plan
        ps_plan → conditional_edges → {
            "tool_calls":  ps_dispatch_tools  → ps_plan   (loop)
            "done":        ps_finalize        → END
        }

    State fields used (see ``models/domain.AgentState``):

      * ps_messages: running LLM message list (system + user + assistant + tool)
      * ps_iter: iteration counter (cap: MAX_TOOL_ITERATIONS)
      * ps_pending_tool_calls: tool_calls from the last ps_plan response
      * ps_external_search_calls: external search call counter
      * ps_recent_results: paper_id → metadata accumulator
      * ps_final_text: assistant content when the loop terminates
      * ps_last_step_index: latest tool_calls.step_index already streamed
    """

    async def _drain_tool_steps(
        state: AgentState,
    ) -> tuple[list[dict[str, Any]], int]:
        run_id: int = state["run_id"]
        last = state.get("ps_last_step_index", -1)
        recs = await drain_tool_calls_since(deps.conn, run_id, last)
        if recs:
            last = recs[-1]["step_index"]
        return recs, last

    async def _ps_plan(state: AgentState) -> AgentState:
        writer = get_stream_writer()
        # Seed messages on first iteration (no ps_messages yet).
        if "ps_messages" not in state:
            messages = await _build_paper_search_messages(
                state=state, conn=deps.conn,
            )
            recent_results: dict[str, dict[str, Any]] = {}
            ps_iter = 0
            external_calls = 0
            last_step = state.get("ps_last_step_index", -1)
        else:
            messages = list(state["ps_messages"])
            recent_results = dict(state.get("ps_recent_results", {}))
            ps_iter = int(state.get("ps_iter", 0))
            external_calls = int(state.get("ps_external_search_calls", 0))
            last_step = int(state.get("ps_last_step_index", -1))

        msg = await _paper_search_plan_step(
            messages=messages,
            tracer=deps.tracer,
            model=deps.paper_qa_model,
            iteration=ps_iter,
            mcp_registry=deps.mcp_registry,
            **_kwargs(deps),
        )

        # Drain & emit the plan step that just closed.
        run_id: int = state["run_id"]
        recs = await drain_tool_calls_since(deps.conn, run_id, last_step)
        for rec in recs:
            writer({"event": "tool_step", "record": rec})
            last_step = rec["step_index"]

        tool_calls = msg.get("tool_calls") or []
        next_state: AgentState = {
            **state,
            "ps_messages": messages,
            "ps_iter": ps_iter + 1,
            "ps_recent_results": recent_results,
            "ps_external_search_calls": external_calls,
            "ps_last_step_index": last_step,
        }
        if tool_calls:
            # Append the assistant turn that requested the tools so the
            # next litellm call sees the conversation correctly.
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.get("content"),
                    "tool_calls": tool_calls,
                },
            )
            next_state["ps_messages"] = messages
            next_state["ps_pending_tool_calls"] = list(tool_calls)
            next_state["ps_final_text"] = ""
        else:
            next_state["ps_pending_tool_calls"] = []
            next_state["ps_final_text"] = str(msg.get("content") or "(no response)")
        return next_state

    def _ps_plan_branch(state: AgentState) -> str:
        pending = state.get("ps_pending_tool_calls") or []
        if not pending:
            return "done"
        if int(state.get("ps_iter", 0)) >= MAX_TOOL_ITERATIONS:
            # Edge case: model returned tool_calls on the cap iteration.
            # Skip dispatch and finalize so we don't blow past the cap.
            return "done"
        return "tool_calls"

    async def _ps_dispatch_tools(state: AgentState) -> AgentState:
        writer = get_stream_writer()
        messages = list(state["ps_messages"])
        recent_results = dict(state.get("ps_recent_results", {}))
        external_calls = int(state.get("ps_external_search_calls", 0))
        last_step = int(state.get("ps_last_step_index", -1))
        run_id: int = state["run_id"]
        session_id: int = state["session_id"]
        pending: list[dict[str, Any]] = list(state.get("ps_pending_tool_calls") or [])

        for call in pending:
            result, external_calls = await _dispatch_paper_search_tool_call(
                call=call,
                tracer=deps.tracer,
                conn=deps.conn,
                session_id=session_id,
                external_discovery_calls=external_calls,
                recent_results=recent_results,
                registry=deps.mcp_registry,
            )
            # Drain & emit the tool-dispatch step that just closed.
            recs = await drain_tool_calls_since(deps.conn, run_id, last_step)
            for rec in recs:
                writer({"event": "tool_step", "record": rec})
                last_step = rec["step_index"]

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": call["function"]["name"],
                    "content": json.dumps(result, default=str),
                },
            )

        return {
            **state,
            "ps_messages": messages,
            "ps_pending_tool_calls": [],
            "ps_recent_results": recent_results,
            "ps_external_search_calls": external_calls,
            "ps_last_step_index": last_step,
        }

    async def _ps_finalize(state: AgentState) -> AgentState:
        writer = get_stream_writer()
        ps_iter = int(state.get("ps_iter", 0))
        final_text = state.get("ps_final_text", "") or ""
        recent_results = state.get("ps_recent_results", {}) or {}

        # If the loop terminated because we hit MAX_TOOL_ITERATIONS with the
        # model still asking for tool calls, surface the cap message instead
        # of parsing a non-existent json:candidates block.
        if not final_text and ps_iter >= MAX_TOOL_ITERATIONS:
            return {
                **state,
                "final_response": (
                    "I've reached the tool-call limit for this turn. "
                    "Try asking again with a more specific question."
                ),
            }

        cleaned_text, candidates = _extract_candidates(final_text, recent_results)
        if candidates:
            writer(
                {
                    "event": "search_results",
                    "candidates": list(candidates),
                },
            )
        return {**state, "final_response": cleaned_text}

    g: StateGraph[AgentState, Any] = StateGraph(AgentState)
    g.add_node("ps_plan", _ps_plan)
    g.add_node("ps_dispatch_tools", _ps_dispatch_tools)
    g.add_node("ps_finalize", _ps_finalize)
    g.add_edge(START, "ps_plan")
    g.add_conditional_edges(
        "ps_plan",
        _ps_plan_branch,
        {"tool_calls": "ps_dispatch_tools", "done": "ps_finalize"},
    )
    g.add_edge("ps_dispatch_tools", "ps_plan")
    g.add_edge("ps_finalize", END)
    return g.compile()


# ---------------------------------------------------------------------------
# paper_qa subgraph
# ---------------------------------------------------------------------------


def build_paper_qa_subgraph(deps: ResearchDeps) -> Any:
    """Compile the paper_qa branching subgraph.

    Topology::

        START → pq_resolve
        pq_resolve → conditional_edges → {
            "empty":   pq_empty       → END
            "single":  pq_single      → END
            "map":     pq_map         → pq_synthesize → END
        }
    """

    async def _pq_resolve(state: AgentState) -> AgentState:
        writer = get_stream_writer()
        last_step = int(state.get("ps_last_step_index", -1))
        papers = await _resolve_enabled_papers(
            deps.conn, session_id=state["session_id"], tracer=deps.tracer,
        )
        # Drain and emit the paper_qa:resolve tool_step that just closed.
        run_id: int = state["run_id"]
        recs = await drain_tool_calls_since(deps.conn, run_id, last_step)
        for rec in recs:
            writer({"event": "tool_step", "record": rec})
            last_step = rec["step_index"]
        return {**state, "pq_papers": papers, "ps_last_step_index": last_step}

    def _pq_branch(state: AgentState) -> str:
        n = len(state.get("pq_papers") or [])
        if n == 0:
            return "empty"
        if n == 1:
            return "single"
        return "map"

    async def _pq_empty(state: AgentState) -> AgentState:
        return {
            **state,
            "final_response": (
                "No references are enabled for this session. Add a paper "
                "to the Reference Sources panel first, then ask again."
            ),
        }

    async def _pq_single(state: AgentState) -> AgentState:
        writer = get_stream_writer()
        paper = state["pq_papers"][0]
        collected: list[str] = []
        final_only: str | None = None
        async for item in _paper_qa_single_stream(
            paper=paper,
            user_message=state["user_message"],
            adapter=deps.adapter,
            tracer=deps.tracer,
            model=deps.paper_qa_model,
            retriever=deps.retriever,
            conn=deps.conn,
            state=state,
            **_kwargs(deps),
        ):
            if isinstance(item, FinalOnlyMessage):
                final_only = item.content
            else:
                writer({"event": "token", "text": item})
                collected.append(item)
        if final_only is not None:
            return {**state, "final_response": final_only}
        return {**state, "final_response": "".join(collected)}

    async def _pq_map(state: AgentState) -> AgentState:
        writer = get_stream_writer()
        papers = list(state["pq_papers"])
        run_id: int = state["run_id"]
        last_step = int(state.get("ps_last_step_index", -1))
        lock = asyncio.Lock()

        async def _one_with_emit(
            pid: int, title: str,
        ) -> tuple[int, str, list[Any], str]:
            result = await _paper_qa_map_one(
                pid=pid,
                title=title,
                user_message=state["user_message"],
                adapter=deps.adapter,
                tracer=deps.tracer,
                model=deps.paper_qa_model,
                retriever=deps.retriever,
                conn=deps.conn,
                **_kwargs(deps),
            )
            # Drain any rows written since the last emission and emit them
            # immediately. The lock prevents two concurrent tasks from
            # claiming the same row twice.
            async with lock:
                nonlocal last_step
                recs = await drain_tool_calls_since(deps.conn, run_id, last_step)
                for rec in recs:
                    writer({"event": "tool_step", "record": rec})
                    last_step = rec["step_index"]
            return result

        results = list(
            await asyncio.gather(
                *[_one_with_emit(pid, title) for pid, title in papers],
            ),
        )
        return {**state, "pq_per_paper": results, "ps_last_step_index": last_step}

    async def _pq_synthesize(state: AgentState) -> AgentState:
        writer = get_stream_writer()
        per_paper = state.get("pq_per_paper") or []
        # If every paper returned no chunks, short-circuit.
        if all(not chunks for _, _, chunks, _ in per_paper):
            return {
                **state,
                "final_response": (
                    "No relevant chunks were found in the enabled references."
                ),
            }
        collected: list[str] = []
        async for tok in _paper_qa_synthesize_stream(
            per_paper=per_paper,
            user_message=state["user_message"],
            adapter=deps.adapter,
            tracer=deps.tracer,
            model=deps.paper_qa_model,
            state=state,
            **_kwargs(deps),
        ):
            writer({"event": "token", "text": tok})
            collected.append(tok)
        return {**state, "final_response": "".join(collected)}

    g: StateGraph[AgentState, Any] = StateGraph(AgentState)
    g.add_node("pq_resolve", _pq_resolve)
    g.add_node("pq_empty", _pq_empty)
    g.add_node("pq_single", _pq_single)
    g.add_node("pq_map", _pq_map)
    g.add_node("pq_synthesize", _pq_synthesize)
    g.add_edge(START, "pq_resolve")
    g.add_conditional_edges(
        "pq_resolve",
        _pq_branch,
        {"empty": "pq_empty", "single": "pq_single", "map": "pq_map"},
    )
    g.add_edge("pq_empty", END)
    g.add_edge("pq_single", END)
    g.add_edge("pq_map", "pq_synthesize")
    g.add_edge("pq_synthesize", END)
    return g.compile()


# ---------------------------------------------------------------------------
# Outer Research dispatcher subgraph
# ---------------------------------------------------------------------------


def _dispatch_branch(state: AgentState) -> str:
    return state["routing_decision"].intent


async def _dispatch(state: AgentState) -> AgentState:
    return state


def build_research_subgraph(deps: ResearchDeps) -> Any:
    """Compile the Research Agent dispatcher subgraph.

    Routes on ``state["routing_decision"].intent`` to either the
    paper_search or paper_qa subgraph (compiled subgraphs are embedded
    as nodes — supported by LangGraph 1.2.0)::

        START → research_dispatch → conditional_edges → {
            "paper_search": paper_search_subgraph
            "paper_qa":     paper_qa_subgraph
        }
        each → END
    """
    ps_subgraph = build_paper_search_subgraph(deps)
    pq_subgraph = build_paper_qa_subgraph(deps)

    g: StateGraph[AgentState, Any] = StateGraph(AgentState)
    g.add_node("research_dispatch", _dispatch)
    g.add_node("paper_search", ps_subgraph)
    g.add_node("paper_qa", pq_subgraph)
    g.add_edge(START, "research_dispatch")
    g.add_conditional_edges(
        "research_dispatch",
        _dispatch_branch,
        {"paper_search": "paper_search", "paper_qa": "paper_qa"},
    )
    g.add_edge("paper_search", END)
    g.add_edge("paper_qa", END)
    return g.compile()
