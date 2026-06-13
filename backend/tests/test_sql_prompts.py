from paperhub.llm.prompts.registry import PromptRegistry


def test_sql_planner_nudges_paper_content_id_and_title_for_listing() -> None:
    reg = PromptRegistry()
    slot = reg.get("sql_planner/v1")
    system = slot.system
    # Listing/finding queries must select attachable identity columns.
    assert "paper_content_id" in system
    assert "title" in system
    assert "listing" in system.lower() or "finding" in system.lower()


def test_sql_answer_nudges_markdown_table_for_aggregate_results() -> None:
    reg = PromptRegistry()
    slot = reg.get("sql_answer/v1")
    system = slot.system
    # Non-paper-shaped (aggregate/statistic) results render as a markdown table.
    assert "markdown table" in system.lower()
    assert "paper_content_id" in system

