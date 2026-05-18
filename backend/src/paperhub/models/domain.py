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
    # paper_search loop:
    #   - ps_messages: the running LLM message list (system + history +
    #     user + assistant/tool turns) that ps_plan and ps_dispatch_tools
    #     mutate across iterations.
    #   - ps_iter: iteration counter, capped by MAX_TOOL_ITERATIONS.
    #   - ps_pending_tool_calls: tool_calls returned by the last ps_plan
    #     call; consumed (drained) by ps_dispatch_tools.
    #   - ps_external_search_calls: external search call counter (cap).
    #   - ps_recent_results: paper_id → metadata, populated by
    #     ps_dispatch_tools so ps_finalize can resolve candidates.
    #   - ps_final_text: assistant content from the terminating ps_plan
    #     call (no tool_calls); consumed by ps_finalize.
    #   - ps_last_step_index: latest tool_calls step_index the subgraph
    #     has already emitted via stream_writer; drained at every step.
    ps_messages: list[dict[str, Any]]
    ps_iter: int
    ps_pending_tool_calls: list[dict[str, Any]]
    ps_external_search_calls: int
    ps_recent_results: dict[str, dict[str, Any]]
    ps_final_text: str
    ps_last_step_index: int
    # paper_qa branch:
    #   - pq_papers: enabled (paper_content_id, title) pairs resolved by
    #     pq_resolve and consumed by the count branch.
    #   - pq_per_paper: per-paper analyses emitted by pq_map and consumed
    #     by pq_synthesize. Each entry is
    #     (paper_content_id, title, retrieved_chunks, analysis_text).
    pq_papers: list[tuple[int, str]]
    pq_per_paper: list[tuple[int, str, list[Any], str]]
