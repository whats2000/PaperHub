"""Typed SSE event discriminated union (design §8).

Every event the /chat endpoint emits is one of the six types below.
Each is a frozen Pydantic model so the SSE boundary can call
``event.model_dump(mode='json')`` and get JSON-serialisable dicts
(UUID → str, datetime → ISO-8601 string, etc.).

Emitting:
    import json
    from paperhub.api.sse import RoutingDecisionEvent
    payload = RoutingDecisionEvent(data=...).model_dump(mode='json')
    yield json.dumps(payload)
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from paperhub.data.models import RoutingDecision, ToolCall

# ---------------------------------------------------------------------------
# Individual event payloads
# ---------------------------------------------------------------------------


class _FrozenEvent(BaseModel):
    model_config = ConfigDict(frozen=True)


class RoutingDecisionEvent(_FrozenEvent):
    type: Literal["routing_decision"] = "routing_decision"
    data: RoutingDecision


class ToolStepEvent(_FrozenEvent):
    """One recorded tool call (or LLM step) in the trace."""

    type: Literal["tool_step"] = "tool_step"
    data: ToolCall


class TokenEvent(_FrozenEvent):
    """A fragment of the generated answer text.

    Phase A: the full answer arrives as a single token event.
    Phase B: stream individual LLM token deltas.
    """

    type: Literal["token"] = "token"
    data: str


class CitationEvent(_FrozenEvent):
    """A citation reference from the generated answer."""

    type: Literal["citation"] = "citation"
    chunk_id: UUID
    section: str | None
    page: int | None


class FinalEvent(_FrozenEvent):
    """Signals end-of-stream with the complete answer."""

    type: Literal["final"] = "final"
    run_id: UUID
    answer: str


class ErrorEvent(_FrozenEvent):
    """Signals a non-recoverable error."""

    type: Literal["error"] = "error"
    message: str


# ---------------------------------------------------------------------------
# Discriminated union
# ---------------------------------------------------------------------------

SseEvent = Annotated[
    RoutingDecisionEvent | ToolStepEvent | TokenEvent | CitationEvent | FinalEvent | ErrorEvent,
    Field(discriminator="type"),
]
