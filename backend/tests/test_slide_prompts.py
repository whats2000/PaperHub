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
