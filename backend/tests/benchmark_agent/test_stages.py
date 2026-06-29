from benchmark.agent.stages import STAGE_REGISTRY, get_stage
from paperhub.models.domain import RoutingDecision


def test_router_spec_registered():
    spec = get_stage("router")
    assert spec.key == "router"
    assert spec.trace_agent == "router" and spec.trace_tool == "classify"
    assert spec.response_model is RoutingDecision
    assert "router" in STAGE_REGISTRY


def test_router_variables_from_args():
    spec = get_stage("router")
    args = {"user_message": "compare these", "enabled_refs_count": 2, "slide_attached": False, "$x": "ignored"}
    assert spec.variables_from_args(args) == {
        "user_message": "compare these", "enabled_refs_count": 2, "slide_attached": False}


def test_router_output_and_score():
    spec = get_stage("router")
    d = RoutingDecision(intent="paper_qa", model_tier="small", confidence=0.9,
                        reasoning="x", resolved_query="q", response_language="English")
    out = spec.output_summary(d)
    assert out["intent"] == "paper_qa" and out["resolved_query"] == "q"
    assert spec.deterministic_score({"intent": "paper_qa"}, out) == 1.0
    assert spec.deterministic_score({"intent": "slides"}, out) == 0.0
    assert spec.deterministic_score({}, out) is None
