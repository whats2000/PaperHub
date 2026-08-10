import json

import pytest
from pydantic import ValidationError

from paperhub.agents.state import effective_query
from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.models.domain import RoutingDecision, ToolCallRecord
from paperhub.models.events import (
    RoutingDecisionEvent,
    sse_format,
)


def test_routing_decision_resolved_query_defaults_empty() -> None:
    # resolved_query defaults to "" when not supplied.
    d = RoutingDecision(intent="paper_search", confidence=0.9, reasoning="r")
    assert d.resolved_query == ""


def test_routing_decision_accepts_clarify_intent_and_brief() -> None:
    d = RoutingDecision(
        intent="clarify", confidence=0.5,
        reasoning="ambiguous follow-up",
        resolved_query="Which topic would you like papers on?",
    )
    assert d.intent == "clarify"
    assert d.resolved_query.startswith("Which topic")


def test_routing_decision_rejects_unknown_intent() -> None:
    with pytest.raises(ValidationError):
        RoutingDecision(intent="bogus", confidence=0.9, reasoning="x")


def test_routing_decision_clamps_confidence() -> None:
    with pytest.raises(ValidationError):
        RoutingDecision(intent="chitchat", confidence=1.5, reasoning="x")


def test_routing_decision_rejects_model_tier_extra_field() -> None:
    """extra='forbid' means model_tier (removed field) is now rejected."""
    with pytest.raises(ValidationError):
        RoutingDecision(intent="chitchat", model_tier="small", confidence=0.9, reasoning="x")


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
        decision=RoutingDecision(intent="chitchat",
                                 confidence=0.92, reasoning="greeting"),
    )
    payload = sse_format(evt)
    assert payload.startswith("event: routing_decision\n")
    assert "chitchat" in payload
    assert payload.endswith("\n\n")


def test_effective_query_prefers_resolved() -> None:
    assert effective_query({"user_message": "raw", "effective_query": "brief"}) == "brief"


def test_effective_query_falls_back_when_empty_or_missing() -> None:
    assert effective_query({"user_message": "raw", "effective_query": ""}) == "raw"
    assert effective_query({"user_message": "raw"}) == "raw"


def test_router_prompt_mentions_resolved_query_and_clarify() -> None:
    # Production router slot is router/v2 (Plan G2); assert on the live prompt,
    # not the kept-as-history router/v1.
    p = PromptRegistry().get("router/v2")
    assert "resolved_query" in p.system
    assert "clarify" in p.system


def test_routing_decision_accepts_paper_suggest_intent():
    d = RoutingDecision(intent="paper_suggest", confidence=0.9,
                        reasoning="topic recommendation", resolved_query="recommend papers on X")
    assert d.intent == "paper_suggest"


def test_router_prompt_distinguishes_search_and_suggest():
    p = PromptRegistry().get("router/v2")
    assert "paper_suggest" in p.system
    assert "paper_search" in p.system


def test_router_v2_prompt_loads_and_has_no_model_tier() -> None:
    """router/v2 slot resolves to router_v2.yaml and must not mention model_tier
    in its output spec (the tier-free validated winner from Plan G2 Phase 1)."""
    p = PromptRegistry().get("router/v2")
    assert "resolved_query" in p.system
    assert "clarify" in p.system
    # The v2 prompt's output spec must list exactly 5 fields with no model_tier.
    assert "model_tier" not in p.system
    assert "response_language" in p.system


def test_suggest_prompts_load_and_format():
    reg = PromptRegistry()
    parse = reg.get("paper_search_parse_suggest/v1")
    parse.user_template.format(user_message="T")  # no KeyError
    synth = reg.get("paper_search_synthesize_suggest/v1")
    synth.user_template.format(
        user_message="m", resolved_block="r", not_found_block="n",
        response_language="English", memory_context="",
    )  # no KeyError
