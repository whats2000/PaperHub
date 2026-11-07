import pytest

from paperhub.llm.prompts.registry import PromptRegistry


@pytest.mark.parametrize("version", ["v1", "v2", "v3"])
def test_sql_agent_loads_and_covers_react_round_protocol(version: str) -> None:
    reg = PromptRegistry()
    slot = reg.get(f"sql_agent/{version}")
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


@pytest.mark.parametrize("version", ["v1", "v2", "v3"])
def test_sql_agent_user_template_formats_cleanly(version: str) -> None:
    reg = PromptRegistry()
    slot = reg.get(f"sql_agent/{version}")
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


def test_sql_agent_v2_counts_only_visible_chat_turns() -> None:
    """Background runs (feedback reviews, ideation) are not chat turns: v2
    counts user messages in visible sessions, and v1's text is unchanged."""
    reg = PromptRegistry()
    v1, v2 = reg.get("sql_agent/v1"), reg.get("sql_agent/v2")
    assert v2.user_template == v1.user_template
    assert "role = 'user'" in v2.system
    assert "chat_sessions.internal = 0" in v2.system
    assert "deleted_at IS NULL" in v2.system
    assert "chat_sessions.internal" not in v1.system



def test_sql_agent_v3_filters_every_history_count() -> None:
    """Live run 787: v2 filtered the session count but counted every user
    message. v3 says every chat_sessions / messages / runs count joins the
    visible-session filter."""
    reg = PromptRegistry()
    v2, v3 = reg.get("sql_agent/v2"), reg.get("sql_agent/v3")
    assert v3.user_template == v2.user_template
    assert "every count over chat_sessions" in v3.system
    assert "every count over" not in v2.system
