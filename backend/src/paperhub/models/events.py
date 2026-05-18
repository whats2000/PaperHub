import json
from typing import Literal

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
)


def sse_format(event: SseEvent) -> str:
    payload = event.model_dump(mode="json")
    payload.pop("type", None)
    return f"event: {event.type}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
