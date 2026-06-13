from paperhub.llm.prompts.registry import PromptRegistry


def test_sql_agent_v1_loads_and_covers_react_round_protocol() -> None:
    reg = PromptRegistry()
    slot = reg.get("sql_agent/v1")
    system = slot.system
    lower = system.lower()

    # 1. Round protocol: each turn returns ONE SqlRoundAction; query vs finalize.
    assert "SqlRoundAction" in system
    assert 'action="query"' in system
    assert 'action="finalize"' in system

    # 2. Bounded loop: at most max_rounds; must_finalize forces finalize.
    assert "max_rounds" in system
    assert "must_finalize" in system

    # 3. CURATION — the load-bearing point: review rows, surface only the
    # genuinely-relevant subset with a reason, NOT every row.
    assert "curat" in lower  # curate/curation
    assert "not every row" in lower
    assert "reason" in lower

    # 4. Listing alias so picks resolve.
    assert "AS paper_content_id" in system

    # 5. Two-layer scoping: library (paper_content) vs this-session.
    assert "paper_content" in system
    assert "session_id" in system

    # 6. Aggregate path renders a markdown table; papers empty.
    assert "markdown table" in lower

    # 7. Answer rules: response language + embed executed SQL as a ```sql block.
    assert "response language" in lower
    assert "```sql" in system

    # 8. Read-only, single-SELECT, real columns only.
    assert "single" in lower and "select" in lower
    assert "never invent" in lower


def test_sql_agent_v1_user_template_formats_cleanly() -> None:
    reg = PromptRegistry()
    slot = reg.get("sql_agent/v1")
    # The agent loop (Task 3) passes exactly these placeholders.
    rendered = slot.user_template.format(
        question="list my papers about diffusion models",
        table_schemas="paper_content(id, title, abstract, year)",
        session_id=42,
        round_number=1,
        max_rounds=4,
        must_finalize=False,
        query_results="(no queries run yet)",
        response_language="English",
    )
    assert "diffusion models" in rendered
    assert "42" in rendered
