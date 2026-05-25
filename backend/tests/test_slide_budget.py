from paperhub.agents.report_pipeline import parse_slide_budget
from paperhub.models.domain import SlideBudget


def test_default_is_15() -> None:
    b = parse_slide_budget("make slides comparing these papers")
    assert b == SlideBudget(target_slide_count=15, depth="standard")


def test_minutes_map_to_slides() -> None:
    assert parse_slide_budget("a 20 minute talk").target_slide_count == 15
    assert parse_slide_budget("an 8-minute talk").target_slide_count == 8  # clamp lo from round(6)


def test_explicit_slide_count_wins() -> None:
    assert parse_slide_budget("make a 25 slide deck").target_slide_count == 25


def test_clamped_range() -> None:
    assert parse_slide_budget("a 60 slide deck").target_slide_count == 30  # clamp hi
    assert parse_slide_budget("a 3 slide deck").target_slide_count == 8    # clamp lo
