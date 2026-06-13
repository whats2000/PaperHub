from paperhub.models.sql_domain import SqlPaperPick, SqlRoundAction


def test_finalize_action_roundtrips_with_papers() -> None:
    action = SqlRoundAction(
        action="finalize",
        sql=None,
        answer="You have 3 papers on diffusion models.",
        papers=[
            SqlPaperPick(paper_content_id=12, reason="core diffusion-model paper"),
            SqlPaperPick(paper_content_id=34, reason="latent diffusion follow-up"),
        ],
    )
    assert action.action == "finalize"
    assert action.sql is None
    assert action.answer == "You have 3 papers on diffusion models."
    assert len(action.papers) == 2
    assert action.papers[0].paper_content_id == 12
    assert action.papers[0].reason == "core diffusion-model paper"
    # round-trips through JSON
    assert SqlRoundAction.model_validate_json(action.model_dump_json()) == action


def test_query_action_carries_sql() -> None:
    action = SqlRoundAction(
        action="query",
        sql="SELECT id, title FROM paper_content WHERE title LIKE '%diffusion%'",
        answer=None,
        papers=[],
    )
    assert action.action == "query"
    assert action.sql is not None and action.sql.startswith("SELECT")
    assert action.answer is None
    assert action.papers == []


def test_decision_fields_are_schema_required() -> None:
    # CRITICAL: these are emitted via Gemini native structured output
    # (adapter.structured). Gemini OMITS optional fields that carry a default
    # from its responseSchema entirely — see commit 72c31a5 / DeckCommand
    # .target_page, where `int | None = None` made the model DROP the field.
    # So the decision-carrying fields MUST be schema-required (no default) so the
    # model always emits them (it emits null/[] for the branch it isn't using).
    required = SqlRoundAction.model_json_schema()["required"]
    for field in ("action", "sql", "answer", "papers"):
        assert field in required, f"{field} must be schema-required so Gemini emits it"


def test_sql_paper_pick_fields_required() -> None:
    required = SqlPaperPick.model_json_schema()["required"]
    assert "paper_content_id" in required
    assert "reason" in required
