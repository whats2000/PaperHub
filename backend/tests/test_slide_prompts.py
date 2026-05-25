from paperhub.llm.prompts.registry import PromptRegistry


def test_understand_slot_loads_and_formats() -> None:
    reg = PromptRegistry()
    slot = reg.get("slides_understand/v1")
    assert "{paper_block}" in slot.user_template
    rendered = slot.user_template.format(
        paper_block="PAPER-A-BODY", response_language="English"
    )
    assert "PAPER-A-BODY" in rendered
    assert "English" in rendered


def test_narrate_slot_loads_and_formats() -> None:
    reg = PromptRegistry()
    slot = reg.get("slides_narrate/v1")
    rendered = slot.user_template.format(
        briefs_block="BRIEFS",
        figure_inventory="model\nattn",
        response_language="English",
        memory_context="MEM",
    )
    assert "BRIEFS" in rendered
    assert "model" in rendered
    assert "English" in rendered
    assert "MEM" in rendered


def test_draft_slot_loads_and_formats() -> None:
    reg = PromptRegistry()
    slot = reg.get("slides_draft/v1")
    rendered = slot.user_template.format(
        deck_title="DECK",
        slide_goal="GOAL",
        slide_title="TITLE",
        key_points="KP",
        assigned_figure="model",
        assigned_equation="E=mc^2",
        chunks_block="CHUNKS",
        response_language="English",
        memory_context="MEM",
    )
    for needle in ("DECK", "GOAL", "TITLE", "KP", "model", "E=mc^2", "CHUNKS", "English", "MEM"):
        assert needle in rendered


def test_coherence_slot_loads_and_formats() -> None:
    reg = PromptRegistry()
    slot = reg.get("slides_coherence/v1")
    rendered = slot.user_template.format(
        frames_block="FRAMES", response_language="English"
    )
    assert "FRAMES" in rendered
    assert "English" in rendered


def test_revise_slot_loads_and_formats() -> None:
    reg = PromptRegistry()
    slot = reg.get("slides_revise/v1")
    rendered = slot.user_template.format(
        pdflatex_log="LOG", tex="TEXSOURCE"
    )
    assert "LOG" in rendered
    assert "TEXSOURCE" in rendered


def test_note_split_slot_loads_and_formats() -> None:
    """Regression for the brace-escaping bug: the user_template must not
    contain a literal ``{segments}`` replacement field (pre-fix it raised
    ``KeyError('segments')`` here)."""
    reg = PromptRegistry()
    slot = reg.get("slides_note_split/v1")
    rendered = slot.user_template.format(
        slide_title="TITLE",
        page_count=3,
        full_note="The full speaker note for the slide.",
        response_language="English",
    )
    assert "TITLE" in rendered
    assert "3" in rendered
    assert "The full speaker note" in rendered
    assert "English" in rendered
