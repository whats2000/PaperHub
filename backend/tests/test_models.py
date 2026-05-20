import json

import pytest
from pydantic import ValidationError

from paperhub.models.domain import RoutingDecision, ToolCallRecord
from paperhub.models.events import (
    RoutingDecisionEvent,
    sse_format,
)


def test_routing_decision_resolved_query_defaults_empty() -> None:
    # Legacy 4-field payload still validates; resolved_query defaults to "".
    d = RoutingDecision(intent="paper_search", model_tier="small", confidence=0.9, reasoning="r")
    assert d.resolved_query == ""


def test_routing_decision_accepts_clarify_intent_and_brief() -> None:
    d = RoutingDecision(
        intent="clarify", model_tier="small", confidence=0.5,
        reasoning="ambiguous follow-up",
        resolved_query="Which topic would you like papers on?",
    )
    assert d.intent == "clarify"
    assert d.resolved_query.startswith("Which topic")


def test_routing_decision_rejects_unknown_intent() -> None:
    with pytest.raises(ValidationError):
        RoutingDecision(intent="bogus", model_tier="small", confidence=0.9, reasoning="x")


def test_routing_decision_clamps_confidence() -> None:
    with pytest.raises(ValidationError):
        RoutingDecision(intent="chitchat", model_tier="small", confidence=1.5, reasoning="x")


def test_tool_call_record_round_trip() -> None:
    record = ToolCallRecord(
        run_id=1, branch="", step_index=0, parent_step=None,
        agent="router", tool="classify", model="gemini/x",
        args_redacted_json={"input": "hello"}, result_summary_json={"intent": "chitchat"},
        latency_ms=120, token_in=12, token_out=4, status="ok", error=None,
    )
    dumped = record.model_dump_json()
    assert json.loads(dumped)["status"] == "ok"


def test_sse_format_routing_decision() -> None:
    evt = RoutingDecisionEvent(
        run_id=7, branch="",
        decision=RoutingDecision(intent="chitchat", model_tier="small",
                                 confidence=0.92, reasoning="greeting"),
    )
    payload = sse_format(evt)
    assert payload.startswith("event: routing_decision\n")
    assert "chitchat" in payload
    assert payload.endswith("\n\n")
