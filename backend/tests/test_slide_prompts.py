from paperhub.llm.prompts.registry import PromptRegistry


def test_slide_slots_load_and_format() -> None:
    reg = PromptRegistry()
    plan = reg.get("slides_plan/v1")
    assert "{papers_block}" in plan.user_template
    plan.user_template.format(papers_block="...", response_language="English", memory_context="")

    sec = reg.get("slides_section/v1")
    sec.user_template.format(
        section_title="Motivation", section_intent="why", chunks_block="...",
        deck_title="X", response_language="English", memory_context="",
        available_figures="model\nattn",
    )

    notes = reg.get("slides_notes/v1")
    notes.user_template.format(beamer_code="...", response_language="English")
