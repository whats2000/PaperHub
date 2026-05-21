"""Deterministic read-only SQL gate for the `sql` MCP (SRS §III-6, II-1 #3).

LLM-authored SQL is parsed with sqlglot and accepted ONLY if it is a single
SELECT/WITH statement referencing tables in ALLOWED_TABLES. Everything else
(writes, DDL, PRAGMA, multi-statement, unknown/disallowed tables) raises
SqlValidationError — the caller turns that into a status='rejected' tool row.
The `memories` table is intentionally absent: it is owned by the `memory` MCP.
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp

ALLOWED_TABLES: frozenset[str] = frozenset(
    {
        "paper_content",
        "papers",
        "chunks",
        "chat_sessions",
        "messages",
        "runs",
        "tool_calls",
    }
)

_ALLOWED_FTS: frozenset[str] = frozenset({"paper_content_fts"})
_ALLOWED_ROOTS: frozenset[str] = ALLOWED_TABLES | _ALLOWED_FTS


class SqlValidationError(ValueError):
    """Raised when LLM-authored SQL violates the read-only/allowlist policy."""


def validate_read_only_sql(sql: str) -> str:
    """Parse *sql* and return it unchanged if it is safe; raise SqlValidationError otherwise.

    Acceptance criteria:
    - Exactly one statement.
    - The statement must be SELECT (covers bare SELECT and WITH...SELECT).
    - Every real table reference must be in _ALLOWED_ROOTS.  CTE alias names
      that are used as FROM targets are skipped — they are virtual, not physical
      tables.  In sqlglot 30.x, WITH...SELECT parses to an exp.Select with a
      ``with_`` arg carrying exp.CTE nodes whose ``.alias`` is the alias name.
    """
    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except Exception as exc:
        raise SqlValidationError(f"unparseable SQL: {exc}") from exc

    real = [s for s in statements if s is not None]
    if len(real) != 1:
        raise SqlValidationError(
            f"expected exactly one statement, got {len(real)}"
            " (multi-statement SQL is rejected)"
        )
    stmt = real[0]

    if not isinstance(stmt, exp.Select):
        raise SqlValidationError(
            f"only SELECT / WITH...SELECT allowed,"
            f" got {type(stmt).__name__.upper()}"
        )

    # Collect CTE alias names so we can skip them during the table allowlist
    # check.  In sqlglot 30.x, WITH...SELECT parses to exp.Select; the CTE
    # definitions live in stmt.args['with_'] (an exp.With node whose
    # .expressions are exp.CTE nodes).  The alias is a virtual table name —
    # not a physical DB table — so it must NOT be checked against the
    # allowlist.
    cte_aliases: set[str] = set()
    with_node = stmt.args.get("with_")
    if with_node is not None:
        for cte in with_node.expressions:
            if cte.alias:
                cte_aliases.add(cte.alias.lower())

    for table in stmt.find_all(exp.Table):
        name = table.name.lower()
        if name in cte_aliases:
            continue
        if name not in _ALLOWED_ROOTS:
            raise SqlValidationError(
                f"table {name!r} is not allowlisted"
                f" (allowed: {sorted(_ALLOWED_ROOTS)})"
            )
    return sql
