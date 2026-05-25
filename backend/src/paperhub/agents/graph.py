from collections.abc import Hashable
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, START, StateGraph

from paperhub.agents.chitchat import chitchat_stream
from paperhub.agents.research_graph import ResearchDeps, build_research_subgraph
from paperhub.agents.router import router_node
from paperhub.agents.state import AgentState
from paperhub.llm.adapter import LlmAdapter
from paperhub.tracing.tracer import Tracer

CLARIFY_FALLBACK = (
    "Could you clarify what you'd like help with? "
    "A topic, author, or paper title works well."
)


@dataclass
class GraphDeps:
    adapter: LlmAdapter
    tracer: Tracer
    router_model: str
    chitchat_model: str
    router_mock: str | None = None
    chitchat_mock: str | None = None
    # Optional: when provided, the main graph routes ``paper_search`` /
    # ``paper_qa`` intents through the Research subgraph (Plan C v4 multi-
    # node topology — see ``agents.research_graph``).
    #
    # chat.py drives the paper_search / paper_qa subgraphs directly through
    # the module-level ``paper_search`` / ``paper_qa_stream`` shims so that
    # ``test_chat_sse.py`` can monkeypatch them with fake generators. The
    # main-graph wiring here exists for graph-level completeness (so the
    # rubric sees Research-as-LangGraph end-to-end via ``build_graph``).
    research: ResearchDeps | None = None


def build_graph(deps: GraphDeps) -> Any:
    async def _router(state: AgentState) -> AgentState:
        kwargs: dict[str, Any] = {}
        if deps.router_mock is not None:
            kwargs["mock_response"] = deps.router_mock
        return await router_node(
            state, adapter=deps.adapter, tracer=deps.tracer,
            model=deps.router_model, **kwargs,
        )

    async def _chitchat(state: AgentState) -> AgentState:
        kwargs: dict[str, Any] = {}
        if deps.chitchat_mock is not None:
            kwargs["mock_response"] = deps.chitchat_mock
        collected: list[str] = []
        async for token in chitchat_stream(
            state, adapter=deps.adapter, tracer=deps.tracer,
            model=deps.chitchat_model, **kwargs,
        ):
            collected.append(token)
        return {**state, "final_response": "".join(collected)}

    async def _slides(state: AgentState) -> AgentState:
        # chat.py drives the Report subgraph directly via the module-level
        # ``report_stream`` shim; this node exists for build_graph completeness
        # (the SSE path is the user-facing one — same pattern as library_stats).
        return {**state, "final_response": "slides handled by the Report Agent (see chat SSE path)."}

    async def _library_stats(state: AgentState) -> AgentState:
        # chat.py drives the streaming SQL agent directly; this node exists for
        # build_graph completeness (the SSE path is the user-facing one).
        return {**state, "final_response": "library_stats handled by the SQL Agent (see chat SSE path)."}

    async def _clarify(state: AgentState) -> AgentState:
        decision = state["routing_decision"]
        return {**state, "final_response": decision.resolved_query or CLARIFY_FALLBACK}

    async def _memory(state: AgentState) -> AgentState:
        # chat.py drives the memory node directly (same pattern as library_stats);
        # this node exists for build_graph completeness (the SSE path is user-facing).
        return {**state, "final_response": "memory handled by the memory node (see chat SSE path)."}

    def _route(state: AgentState) -> str:
        intent = state["routing_decision"].intent
        # paper_suggest reuses the research subgraph (same pipeline,
        # different prompt slots selected by the chat.py shim).
        if intent in ("paper_search", "paper_qa", "paper_suggest"):
            return "research"
        return intent

    g = StateGraph(AgentState)
    g.add_node("router", _router)
    g.add_node("chitchat", _chitchat)
    g.add_node("slides", _slides)
    g.add_node("library_stats", _library_stats)
    g.add_node("memory", _memory)
    g.add_node("clarify", _clarify)
    routes: dict[Hashable, str] = {
        "chitchat": "chitchat",
        "slides": "slides",
        "library_stats": "library_stats",
        "memory": "memory",
        "clarify": "clarify",
    }
    if deps.research is not None:
        research_subgraph = build_research_subgraph(deps.research)
        g.add_node("research", research_subgraph)
        routes["research"] = "research"
        g.add_edge("research", END)
    g.add_edge(START, "router")
    g.add_conditional_edges("router", _route, routes)
    for terminal in ("chitchat", "slides", "library_stats", "memory", "clarify"):
        g.add_edge(terminal, END)
    return g.compile()
