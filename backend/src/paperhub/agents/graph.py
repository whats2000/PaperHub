from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, START, StateGraph

from paperhub.agents.chitchat import chitchat_stream
from paperhub.agents.router import router_node
from paperhub.agents.state import AgentState
from paperhub.agents.stubs import stub_response
from paperhub.llm.adapter import LlmAdapter
from paperhub.tracing.tracer import Tracer


@dataclass
class GraphDeps:
    adapter: LlmAdapter
    tracer: Tracer
    router_model: str
    chitchat_model: str
    router_mock: str | None = None
    chitchat_mock: str | None = None


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

    async def _stub_paper_search(state: AgentState) -> AgentState:
        return {**state, "final_response": await stub_response(state, intent="paper_search")}

    async def _stub_paper_qa(state: AgentState) -> AgentState:
        return {**state, "final_response": await stub_response(state, intent="paper_qa")}

    async def _stub_slides(state: AgentState) -> AgentState:
        return {**state, "final_response": await stub_response(state, intent="slides")}

    async def _stub_library_stats(state: AgentState) -> AgentState:
        return {**state, "final_response": await stub_response(state, intent="library_stats")}

    def _route(state: AgentState) -> str:
        return state["routing_decision"].intent

    g = StateGraph(AgentState)
    g.add_node("router", _router)
    g.add_node("chitchat", _chitchat)
    g.add_node("paper_search", _stub_paper_search)
    g.add_node("paper_qa", _stub_paper_qa)
    g.add_node("slides", _stub_slides)
    g.add_node("library_stats", _stub_library_stats)
    g.add_edge(START, "router")
    g.add_conditional_edges("router", _route, {
        "chitchat": "chitchat",
        "paper_search": "paper_search",
        "paper_qa": "paper_qa",
        "slides": "slides",
        "library_stats": "library_stats",
    })
    for terminal in ["chitchat", "paper_search", "paper_qa", "slides", "library_stats"]:
        g.add_edge(terminal, END)
    return g.compile()
