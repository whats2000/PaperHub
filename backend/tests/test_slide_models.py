import pytest

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


def test_phd_models_parse() -> None:
    from paperhub.models.domain import (  # noqa: F401
        OutlineSlide,
        PaperBrief,
        TalkOutline,
    )

    brief = PaperBrief.model_validate(
        {
            "paper_id": 1,
            "contribution": "x",
            "method": "y",
            "key_results": ["r1"],
            "key_figure_keys": ["p0-fig-000"],
            "key_equations": ["E=mc^2"],
        }
    )
    assert brief.key_figure_keys == ["p0-fig-000"]

    outline = TalkOutline.model_validate(
        {
            "title": "T",
            "slides": [
                {
                    "title": "Motivation",
                    "goal": "why",
                    "key_points": ["a", "b"],
                    "figure_key": "p0-fig-000",
                    "equation": None,
                    "chunk_ids": [3],
                    "paper_ids": [1],
                }
            ],
        }
    )
    assert outline.slides[0].figure_key == "p0-fig-000"


def test_paper_brief_extra_forbidden() -> None:
    from pydantic import ValidationError

    from paperhub.models.domain import PaperBrief

    with pytest.raises(ValidationError):
        PaperBrief.model_validate(
            {
                "paper_id": 1,
                "contribution": "x",
                "method": "y",
                "key_results": [],
                "key_figure_keys": [],
                "key_equations": [],
                "unexpected_field": "oops",
            }
        )


def test_outline_slide_defaults() -> None:
    from paperhub.models.domain import OutlineSlide

    slide = OutlineSlide.model_validate(
        {"title": "Intro", "goal": "set the scene", "key_points": ["p1"]}
    )
    assert slide.figure_key is None
    assert slide.equation is None
    assert slide.chunk_ids == []
    assert slide.paper_ids == []


def test_talk_outline_extra_forbidden() -> None:
    from pydantic import ValidationError

    from paperhub.models.domain import TalkOutline

    with pytest.raises(ValidationError):
        TalkOutline.model_validate({"title": "T", "slides": [], "extra": "bad"})
