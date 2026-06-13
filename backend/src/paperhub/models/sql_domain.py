"""Structured-output schemas for the agentic ReAct SQL agent (library_stats).

Each round the orchestrator LLM returns a :class:`SqlRoundAction`: it either
runs another read-only SELECT (``action="query"``) or finalizes the turn
(``action="finalize"``) with the answer prose + a curated paper shortlist.

Lives in its own module (sibling to ``slide_domain``/``domain``) — these are the
SQL-agent's structured-output types, distinct from the slide pipeline's.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SqlPaperPick(BaseModel):
    """One paper the SQL agent chose to surface, with why it's relevant."""

    model_config = ConfigDict(extra="forbid")

    paper_content_id: int = Field(
        description="paper_content.id of the genuinely-relevant paper to surface."
    )
    reason: str = Field(
        description="One-line reason this paper answers the user's question."
    )


class SqlRoundAction(BaseModel):
    """The SQL agent's per-round ReAct decision: run another query, or finalize.

    ``action="query"`` carries the next read-only SELECT in ``sql``; ``answer``
    is null and ``papers`` is empty. ``action="finalize"`` carries the final
    prose in ``answer`` and the curated shortlist in ``papers``; ``sql`` is null.

    CRITICAL — schema-required discipline (load-bearing): these are emitted via
    Gemini native structured output (``adapter.structured``). Gemini OMITS
    optional fields that carry a default from its responseSchema entirely — see
    commit 72c31a5 / ``DeckCommand.target_page`` where ``int | None = None`` made
    the model DROP the field. So ``sql``/``answer``/``papers`` carry NO default;
    they're schema-REQUIRED and the model emits ``null``/``[]`` for the branch it
    isn't using rather than dropping them. No ``extra="forbid"`` so an LLM that
    adds a stray reasoning key doesn't fail validation (matches the file's other
    LLM-output models like ``RoundAction``).
    """

    action: Literal["query", "finalize"] = Field(
        description=(
            "'query' to run another read-only SELECT (set sql, leave answer "
            "null and papers []); 'finalize' to answer the user (set answer + "
            "papers, leave sql null)."
        )
    )
    sql: str | None = Field(
        description=(
            "The next read-only SELECT to run when action='query'; null when "
            "action='finalize'."
        )
    )
    answer: str | None = Field(
        description=(
            "The final user-facing prose answer when action='finalize'; null "
            "when action='query'."
        )
    )
    papers: list[SqlPaperPick] = Field(
        description=(
            "When action='finalize': the curated, genuinely-relevant subset "
            "(NOT every SQL row), each with a one-line reason; empty for "
            "aggregate/stat answers. Empty when action='query'."
        )
    )
