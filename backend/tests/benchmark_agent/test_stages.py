from benchmark.agent.stages import STAGE_REGISTRY, RouterEvalOutput, get_stage


def test_router_spec_registered():
    spec = get_stage("router")
    assert spec.key == "router"
    assert spec.trace_agent == "router" and spec.trace_tool == "classify"
    # Decoupled from the production RoutingDecision (which still carries
    # model_tier) so variants across the G2 model_tier-removal boundary
    # co-parse — see RouterEvalOutput docstring.
    assert spec.response_model is RouterEvalOutput
    assert "router" in STAGE_REGISTRY


def test_router_variables_from_args():
    spec = get_stage("router")
    args = {"user_message": "compare these", "enabled_refs_count": 2, "slide_attached": False, "$x": "ignored"}
    assert spec.variables_from_args(args) == {
        "user_message": "compare these", "enabled_refs_count": 2, "slide_attached": False}


def test_router_output_and_score():
    spec = get_stage("router")
    d = RouterEvalOutput(intent="paper_qa", confidence=0.9,
                         reasoning="x", resolved_query="q", response_language="English")
    out = spec.output_summary(d)
    assert out["intent"] == "paper_qa" and out["resolved_query"] == "q"
    assert spec.deterministic_score({"intent": "paper_qa"}, out) == 1.0
    assert spec.deterministic_score({"intent": "slides"}, out) == 0.0
    assert spec.deterministic_score({}, out) is None


def test_router_output_tolerates_model_tier_presence_or_absence():
    """The eval grades intent only; a tier-emitting variant (v1/v2a) and a
    tier-free variant (v3) must summarize identically — the property that
    makes the v3-vs-v2a sweep comparable across the model_tier boundary."""
    spec = get_stage("router")
    with_tier = {"intent": "slides", "model_tier": "flagship", "confidence": 0.8,
                 "reasoning": "r", "resolved_query": "q", "response_language": "English"}
    without_tier = {"intent": "slides", "confidence": 0.8,
                    "reasoning": "r", "resolved_query": "q", "response_language": "English"}
    assert spec.output_summary(with_tier) == spec.output_summary(without_tier)
    assert spec.output_summary(with_tier)["intent"] == "slides"
