from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

Intent = Literal["paper_search", "paper_qa", "slides", "library_stats", "chitchat"]
ModelTier = Literal["small", "flagship"]
ToolStatus = Literal["ok", "error", "rejected"]
Branch = Literal["", "A", "B"]
PaperQaStrategy = Literal["compare", "find"]


class RoutingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Intent
    model_tier: ModelTier
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


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
    # paper_qa branch:
    #   - pq_papers: enabled (paper_content_id, title) pairs resolved by
    #     pq_resolve and consumed by the count branch.
    #   - pq_per_paper: per-paper analyses emitted by pq_map and consumed
    #     by pq_synthesize. Each entry is
    #     (paper_content_id, title, retrieved_chunks, analysis_text).
    pq_papers: list[tuple[int, str]]
    pq_per_paper: list[tuple[int, str, list[Any], str]]
