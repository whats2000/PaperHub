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


def test_router_finalize_applies_the_deploy_priority():
    """The sweep grades what the deploy router node returns, not the raw LLM
    pick: a paper_qa that also asks for slides becomes slides (refs present),
    and the judged query is the one downstream reads (the raw message)."""
    spec = get_stage("router")
    assert spec.finalize is not None
    raw = spec.output_summary({"intent": "paper_qa", "also_requested": ["slides"],
                               "confidence": 0.8, "reasoning": "r",
                               "resolved_query": "Overview.", "response_language": "English"})
    msg = "give me an overview, then build a deck"
    out = spec.finalize({"user_message": msg, "enabled_refs_count": 1,
                         "slide_attached": False}, raw)
    assert out["intent"] == "slides" and out["llm_intent"] == "paper_qa"
    assert out["resolved_query"] == msg
    kept = spec.finalize({"user_message": msg, "enabled_refs_count": 0,
                          "slide_attached": False}, raw)
    assert kept["intent"] == "paper_qa" and kept["resolved_query"] == "Overview."


def test_router_finalize_keeps_a_translated_query_and_flags_it():
    spec = get_stage("router")
    assert spec.finalize is not None
    raw = spec.output_summary({"intent": "chitchat", "confidence": 0.9, "reasoning": "r",
                               "resolved_query": "Thanks!", "response_language": "Japanese"})
    out = spec.finalize({"user_message": "ありがとう", "enabled_refs_count": 0,
                         "slide_attached": False}, raw)
    # kept (SRS v2.11: a rewrite carries resolved context); the drift is flagged
    assert out["resolved_query"] == "Thanks!"
    assert out["query_language_drift"] == "translated"


def test_router_finalize_asks_which_paper_when_nothing_is_named():
    spec = get_stage("router")
    assert spec.finalize is not None
    raw = spec.output_summary({"intent": "paper_search", "subject": "", "confidence": 0.8,
                               "reasoning": "r", "resolved_query": "Find this paper.",
                               "response_language": "English"})
    out = spec.finalize({"user_message": "summarize this paper", "enabled_refs_count": 0,
                         "slide_attached": False}, raw)
    assert out["intent"] == "clarify" and out["llm_intent"] == "paper_search"
    assert out["subject_rule"]["demoted_to"] == "clarify"
