import pytest

from paperhub.mcp.sql_safety import (
    ALLOWED_TABLES,
    SqlValidationError,
    validate_read_only_sql,
)


def test_plain_select_on_allowlisted_table_passes() -> None:
    sql = "SELECT count(*) FROM papers WHERE session_id = 1"
    assert validate_read_only_sql(sql) == sql


def test_with_cte_passes() -> None:
    sql = "WITH t AS (SELECT id FROM paper_content) SELECT count(*) FROM t"
    assert validate_read_only_sql(sql)


def test_join_across_allowlisted_tables_passes() -> None:
    validate_read_only_sql(
        "SELECT s.id FROM papers p JOIN paper_content pc ON p.paper_content_id = pc.id "
        "JOIN chat_sessions s ON p.session_id = s.id"
    )


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE papers",
        "DELETE FROM papers",
        "UPDATE papers SET enabled = 0",
        "INSERT INTO papers (session_id) VALUES (1)",
        "SELECT 1; DROP TABLE papers",
        "PRAGMA table_info(papers)",
    ],
)
def test_non_select_verbs_rejected(sql: str) -> None:
    with pytest.raises(SqlValidationError):
        validate_read_only_sql(sql)


def test_query_against_non_allowlisted_table_rejected() -> None:
    with pytest.raises(SqlValidationError, match="memories"):
        validate_read_only_sql("SELECT * FROM memories")


def test_unknown_table_rejected() -> None:
    with pytest.raises(SqlValidationError):
        validate_read_only_sql("SELECT * FROM secrets")


def test_memories_excluded_from_allowlist() -> None:
    assert "memories" not in ALLOWED_TABLES
    assert "papers" in ALLOWED_TABLES
