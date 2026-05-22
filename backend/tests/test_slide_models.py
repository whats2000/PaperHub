from paperhub.models.domain import PlannedSection, SlidePlan  # noqa: F401


def test_slide_plan_parses() -> None:
    plan = SlidePlan.model_validate({
        "title": "MoE Routing",
        "sections": [
            {"title": "Motivation", "intent": "why MoE", "paper_content_ids": [1, 2]},
            {"title": "Comparison", "intent": "A vs B", "paper_content_ids": [1, 2]},
        ],
    })
    assert plan.title == "MoE Routing"
    assert len(plan.sections) == 2
    assert plan.sections[0].paper_content_ids == [1, 2]
