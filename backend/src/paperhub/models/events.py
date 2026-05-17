import json
from typing import Literal

from pydantic import BaseModel

from paperhub.models.domain import Branch, RoutingDecision, ToolCallRecord


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


SseEvent = RoutingDecisionEvent | ToolStepEvent | TokenEvent | FinalEvent | ErrorEvent


def sse_format(event: SseEvent) -> str:
    payload = event.model_dump(mode="json")
    payload.pop("type", None)
    return f"event: {event.type}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
