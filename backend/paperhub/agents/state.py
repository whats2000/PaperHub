"""LangGraph agent state definition (design §5).

``AgentState`` is a ``TypedDict`` so LangGraph can diff and merge state
fragments at each graph node.  All fields that are not yet filled in at a
given graph step use ``NotRequired`` — this avoids forcing every node to set
every field on every invocation.
"""

from __future__ import annotations

from typing import NotRequired
from uuid import UUID

from typing_extensions import TypedDict

from paperhub.data.models import RoutingDecision
from paperhub.rag.retriever import RetrievedChunk


class AgentState(TypedDict):
    """Mutable graph state shared across all agent nodes in a run."""

    run_id: UUID
    user_message: str
    project_id: NotRequired[UUID | None]

    # Routing
    routing_decision: NotRequired[RoutingDecision | None]

    # RAG
    retrieved_chunks: NotRequired[list[RetrievedChunk]]

    # SQL node (Phase B)
    sql_query: NotRequired[str | None]
    sql_result: NotRequired[list[dict[str, object]] | None]

    # MCP calls audit (list of tool + result summaries for the run)
    mcp_calls: NotRequired[list[dict[str, object]]]

    # Slide generation (Phase C)
    slide_artifacts: NotRequired[list[str]]

    # Terminal output
    final_response: NotRequired[str | None]
