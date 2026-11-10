import json
from typing import Any, Literal

from pydantic import BaseModel

from paperhub.models.domain import Branch, RoutingDecision, ToolCallRecord


class SearchCandidateModel(BaseModel):
    """Pydantic mirror of agents.research.SearchCandidate for SSE emission.

    Lives in models/events.py (not models/domain.py) because it's only
    surfaced through the ``search_results`` SSE event — there's no SQLite
    schema row backing it."""

    paper_id: str
    title: str
    authors: list[str]
    year: int | None
    abstract: str | None
    arxiv_id: str | None
    has_open_pdf: bool
    reason: str
    finalize: bool
    auto_added: bool
    papers_id: int | None
    error: str | None
    already_in_session: bool
    # Default True so replay of cards persisted before this field still loads.
    verified: bool = True
    # F4.3: OA URLs attempted before giving up — empty list when none were tried.
    # Default empty so replay of cards persisted before F4.3 still loads.
    tried_urls: list[str] = []


class SearchResultsEvent(BaseModel):
    type: Literal["search_results"] = "search_results"
    run_id: int
    candidates: list[SearchCandidateModel]


class SessionEvent(BaseModel):
    type: Literal["session"] = "session"
    run_id: int
    session_id: int


class RoutingDecisionEvent(BaseModel):
    type: Literal["routing_decision"] = "routing_decision"
    run_id: int
    branch: Branch
    decision: RoutingDecision


class ToolStepEvent(BaseModel):
    type: Literal["tool_step"] = "tool_step"
    record: ToolCallRecord


class TokenEvent(BaseModel):
    type: Literal["token"] = "token"
    run_id: int
    branch: Branch
    text: str


class FinalEvent(BaseModel):
    type: Literal["final"] = "final"
    run_id: int
    branch: Branch
    message_id: int
    content: str


class AgentSummary(BaseModel):
    """The turn summary an agent run shows instead of the routing badge
    (SRS §III-10.4), built from ``runs.agent_record_json``."""

    papers_read: int
    steps: int
    tools: list[str]

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "AgentSummary":
        papers = record.get("papers")
        tools = record.get("tools")
        steps = record.get("steps")
        return cls(
            papers_read=len({p for p in papers if isinstance(p, int)})
            if isinstance(papers, list) else 0,
            steps=steps if isinstance(steps, int) else 0,
            # Distinct names in first-use order; the badge needs no repeats.
            tools=list(dict.fromkeys(
                str(t) for t in tools if isinstance(t, str)))
            if isinstance(tools, list) else [],
        )


class AgentSummaryEvent(AgentSummary):
    type: Literal["agent_summary"] = "agent_summary"
    run_id: int


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    run_id: int
    branch: Branch
    message: str


SseEvent = (
    SessionEvent
    | RoutingDecisionEvent
    | ToolStepEvent
    | TokenEvent
    | SearchResultsEvent
    | FinalEvent
    | ErrorEvent
    | AgentSummaryEvent
)


def sse_format(event: SseEvent) -> str:
    payload = event.model_dump(mode="json")
    payload.pop("type", None)
    return f"event: {event.type}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
