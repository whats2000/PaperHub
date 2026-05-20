from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

Intent = Literal[
    "paper_search", "paper_qa", "slides", "library_stats", "chitchat", "clarify",
]
ModelTier = Literal["small", "flagship"]
ToolStatus = Literal["ok", "error", "rejected"]
Branch = Literal["", "A", "B"]
PaperQaStrategy = Literal["compare", "find"]


class SectionEntry(BaseModel):
    """One entry in paper_content.sections_json — a section's name and
    physical extents within source.flattened.tex. Used by the per-paper
    paper_qa subagent's list_sections() tool (Plan C v2.10-3)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    char_start: int
    char_end: int
    token_count: int
    chunk_count: int


class RoutingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Intent
    model_tier: ModelTier
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    # v2.11: self-contained, anaphora-free rewrite of the user's latest
    # turn (resolved against history by the router). For actionable
    # intents this is the task brief downstream agents act on; for
    # intent="clarify" it carries the clarifying question to show the
    # user. Empty string => downstream falls back to the raw user_message.
    resolved_query: str = ""


class PaperQaPlan(BaseModel):
    """Structured output of paper_qa:plan — classifies the user's
    question into a retrieval/generation strategy."""

    model_config = ConfigDict(extra="forbid")
    strategy: PaperQaStrategy
    reasoning: str


class ToolCallRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: int
    branch: Branch = ""
    step_index: int
    parent_step: int | None
    agent: str
    tool: str
    model: str | None
    args_redacted_json: dict[str, Any] | None
    result_summary_json: dict[str, Any] | None
    latency_ms: int
    token_in: int | None
    token_out: int | None
    status: ToolStatus
    error: str | None


class AgentState(TypedDict, total=False):
    run_id: int
    branch: Branch
    session_id: int
    user_message: str
    # v2.11: the router's anaphora-resolved, self-contained rewrite of
    # user_message. Downstream agents read this (falling back to
    # user_message) so a bare follow-up like "推薦幾篇" carries its topic.
    effective_query: str
    routing_decision: RoutingDecision
    final_response: str
    history: list[dict[str, str]]
    # ------------------------------------------------------------------
    # Research subgraph control-flow fields (Plan C v4).
    #
    # The Research Agent is a LangGraph of multiple nodes; these slots
    # carry the per-iteration state between nodes. Everything here is
    # ``NotRequired`` (the TypedDict is ``total=False``) — the dispatcher
    # node initialises the fields its branch needs, leaving the rest unset.
    # ------------------------------------------------------------------
    # paper_search subgraph (v2.7 — decomposed pipeline):
    #   - ps_parsed_requests: output of the Parser stage; list of
    #     ParsedRequest dataclasses (one per distinct paper request the
    #     user named). Empty list means "not a paper-search query" → the
    #     synthesizer emits a clarifying question.
    #   - ps_resolved: list of ResolvedPaper from successful
    #     Discover→Resolve cycles (one per ParsedRequest that landed).
    #   - ps_not_found: list of ParsedRequest entries whose
    #     Discover→Resolve cycle exhausted MAX_REFINEMENT_LOOPS without
    #     a SS hit. The Synthesizer mentions these explicitly so the
    #     user knows what failed.
    #   - ps_last_step_index: latest tool_calls.step_index the subgraph
    #     has already emitted via stream_writer; drained between stages
    #     so the chat layer sees rows in tracer-write order.
    ps_parsed_requests: list[Any]   # list[ParsedRequest]
    ps_resolved: list[Any]          # list[ResolvedPaper]
    ps_not_found: list[Any]         # list[ParsedRequest]
    ps_last_step_index: int
    # paper_qa branch (v2.10 — agentic hierarchical):
    #   - pq_papers: enabled (paper_content_id, title) pairs resolved by
    #     pq_resolve and fanned-out by pq_dispatch.
    #   - pq_per_paper_picks: PerPaperPicks objects collected by pq_dispatch
    #     via asyncio.gather over run_paper_qa_subagent. Consumed by
    #     pq_finalize which streams the user-facing synthesis over raw chunks
    #     rather than analyst-prose summaries.
    pq_papers: list[tuple[int, str]]
    pq_per_paper_picks: list[Any]  # list[PerPaperPicks]
