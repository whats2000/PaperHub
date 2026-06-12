from paperhub.agents.slide_agent import _format_outline_block
from paperhub.models.slide_domain import DeckOutline, OutlineSlide


def test_format_outline_block_none_is_empty() -> None:
    assert _format_outline_block(None) == ""


def test_format_outline_block_lists_slides_in_order() -> None:
    outline = DeckOutline(
        talk_title="VLM Talk", audience_intent="walk the references",
        narrative_arc="problem -> method -> takeaway",
        slides=[
            OutlineSlide(slide_index=0, goal="title page", key_message="",
                         transition_from_prev="", paper_id=None, figure_key=None,
                         grounding_chunk_ids=[]),
            OutlineSlide(slide_index=1, goal="motivate the problem", key_message="VLMs hallucinate",
                         transition_from_prev="", paper_id=73, figure_key="p0-fig-001",
                         grounding_chunk_ids=[101]),
        ],
    )
    block = _format_outline_block(outline)
    assert "VLM Talk" in block
    assert "problem -> method -> takeaway" in block
    assert "1." in block and "2." in block
    assert "motivate the problem" in block
    assert "p0-fig-001" in block
    assert "exactly one frame" in block.lower()
