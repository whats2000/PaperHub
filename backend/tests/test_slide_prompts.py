from paperhub.llm.prompts.registry import PromptRegistry


def test_revise_slot_loads_and_formats() -> None:
    reg = PromptRegistry()
    slot = reg.get("slides_revise/v1")
    rendered = slot.user_template.format(
        pdflatex_log="LOG", tex="TEXSOURCE"
    )
    assert "LOG" in rendered
    assert "TEXSOURCE" in rendered
