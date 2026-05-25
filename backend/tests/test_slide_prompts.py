from paperhub.llm.prompts.registry import PromptRegistry


def test_understand_slot_loads_and_formats() -> None:
    reg = PromptRegistry()
    slot = reg.get("slides_understand/v1")
    assert "{paper_block}" in slot.user_template
    rendered = slot.user_template.format(
        paper_block="PAPER-A-BODY", response_language="Traditional Chinese"
    )
    assert "PAPER-A-BODY" in rendered
    assert "Traditional Chinese" in rendered
    # LANGUAGE enforcement must be present in the user block (substituted value visible)
    assert "LANGUAGE" in rendered


def test_narrate_slot_loads_and_formats() -> None:
    reg = PromptRegistry()
    slot = reg.get("slides_narrate/v1")
    rendered = slot.user_template.format(
        briefs_block="BRIEFS",
        figure_inventory="model\nattn",
        response_language="Traditional Chinese",
        memory_context="MEM",
        target_slide_count=15,
        depth="standard",
    )
    assert "BRIEFS" in rendered
    assert "model" in rendered
    assert "Traditional Chinese" in rendered
    assert "MEM" in rendered
    assert "LANGUAGE" in rendered


def test_coherence_slot_loads_and_formats() -> None:
    reg = PromptRegistry()
    slot = reg.get("slides_coherence/v1")
    rendered = slot.user_template.format(
        frames_block="FRAMES", response_language="Traditional Chinese"
    )
    assert "FRAMES" in rendered
    assert "Traditional Chinese" in rendered
    assert "LANGUAGE" in rendered


def test_revise_slot_loads_and_formats() -> None:
    reg = PromptRegistry()
    slot = reg.get("slides_revise/v1")
    rendered = slot.user_template.format(
        pdflatex_log="LOG", tex="TEXSOURCE"
    )
    assert "LOG" in rendered
    assert "TEXSOURCE" in rendered
