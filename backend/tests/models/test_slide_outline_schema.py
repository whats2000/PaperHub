from paperhub.models.slide_domain import (
    DeckOutline,
    DeckOutlineDraft,
    OutlineSlide,
    OutlineSlideDraft,
)


def test_draft_slide_defaults_are_empty() -> None:
    s = OutlineSlideDraft(goal="g", key_message="k")
    assert s.transition_from_prev == ""
    assert s.paper_id is None
    assert s.figure_key is None
    assert s.grounding_sections == []


def test_draft_outline_holds_slides() -> None:
    d = DeckOutlineDraft(
        talk_title="T",
        audience_intent="walk through the references",
        narrative_arc="problem -> method -> takeaway",
        slides=[OutlineSlideDraft(goal="title", key_message="")],
    )
    assert len(d.slides) == 1


def test_resolved_slide_carries_index_and_grounding() -> None:
    s = OutlineSlide(
        slide_index=2,
        goal="g",
        key_message="k",
        transition_from_prev="bridge",
        paper_id=73,
        figure_key="p0-fig-001",
        grounding_chunk_ids=[85229, 85230],
    )
    assert s.slide_index == 2
    assert s.grounding_chunk_ids == [85229, 85230]


def test_resolved_outline_roundtrips_json() -> None:
    d = DeckOutline(
        talk_title="T",
        audience_intent="ai",
        narrative_arc="arc",
        slides=[
            OutlineSlide(
                slide_index=0, goal="title", key_message="", transition_from_prev="",
                paper_id=None, figure_key=None, grounding_chunk_ids=[],
            )
        ],
    )
    assert DeckOutline.model_validate_json(d.model_dump_json()) == d
