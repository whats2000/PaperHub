from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

Intent = Literal[
    "paper_search", "paper_suggest", "paper_qa", "slides", "library_stats", "memory", "chitchat", "clarify",
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
    # Human-readable name of the language the user wrote their latest turn in
    # (e.g. "Traditional Chinese", "English", "Japanese"), detected by the
    # router. Every downstream agent writes its FINAL response in this language
    # so a Chinese question isn't answered in English. Empty => agents fall
    # back to "the user's language".
    response_language: str = ""


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


class PlannedSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    intent: str
    paper_content_ids: list[int]


class SlidePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    sections: list[PlannedSection]


class PaperBrief(BaseModel):
    """Per-paper understanding produced by the F3 'understand' stage.
    Carries the contribution, method, key results, figure keys, and
    equations that the PhD-grade slide agent uses when drafting slides."""

    model_config = ConfigDict(extra="forbid")

    paper_id: int
    contribution: str
    method: str
    key_results: list[str]
    key_figure_keys: list[str]
    key_equations: list[str]


class OutlineSlide(BaseModel):
    """One slide entry in a TalkOutline — title, narrative goal, key points,
    and optional pointers to a figure, equation, chunks, and papers."""

    model_config = ConfigDict(extra="forbid")

    title: str
    goal: str
    key_points: list[str]
    figure_key: str | None = None
    equation: str | None = None
    chunk_ids: list[int] = []
    paper_ids: list[int] = []


class TalkOutline(BaseModel):
    """Structured talk outline produced by the F3 'narrate' stage — a title
    and an ordered list of OutlineSlide entries."""

    model_config = ConfigDict(extra="forbid")

    title: str
    slides: list[OutlineSlide]


class FrameDraft(BaseModel):
    """A single CONCISE Beamer frame, produced by the F4 frame-only draft
    stage. Speaker notes are authored separately by the opt-in NOTES flow."""

    model_config = ConfigDict(extra="forbid")

    frame: str


class SlideBudget(BaseModel):
    """Deck length budget (F4 — SRS v2.21). Default 20 min ≈ 15 slides."""

    model_config = ConfigDict(extra="forbid")

    target_slide_count: int = 15
    depth: str = "standard"  # 'overview' | 'standard' | 'deep'


class DeckCommand(BaseModel):
    """How to interpret a slides turn when a deck already exists (F4, v2.21)."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["generate_notes", "edit_notes", "edit_slides", "regenerate"]
    target_scope: Literal["current", "page", "all"] = "all"
    target_page: int | None = None
    note_language: str | None = None  # for generate_notes / edit_notes


class TargetLanguage(BaseModel):
    """The language the user EXPLICITLY asked the slide CONTENT to be written in
    (v2.22). ``None`` when no language was named — callers fall back to the
    router's ``response_language``. Distinct from the chat-reply language: the
    user may write in Chinese yet ask for an English deck ("把簡報換成英文")."""

    model_config = ConfigDict(extra="forbid")

    language: str | None = None


class AgentState(TypedDict, total=False):
    run_id: int
    branch: Branch
    session_id: int
    user_message: str
    # v2.11: the router's anaphora-resolved, self-contained rewrite of
    # user_message. Downstream agents read this (falling back to
    # user_message) so a bare follow-up like "推薦幾篇" carries its topic.
    effective_query: str
    # v2.13: human-readable language of the user's latest turn (router-set,
    # from RoutingDecision.response_language). Final-response agents read this
    # (fallback "the user's language") so they answer in the user's language.
    response_language: str
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
    # ------------------------------------------------------------------
    # Report (slides) subgraph fields (Plan F v2.18):
    # ------------------------------------------------------------------
    current_view_page: int       # v2.18: slide on screen (frontend-supplied; Phase 2 uses it)
    report_deck_id: int          # v2.18: set by sl_emit
    report_papers: list[dict[str, Any]]  # v2.18: enabled papers loaded by sl_resolve
    report_budget: SlideBudget   # v2.21 (F4): GENERATE length budget
    report_command: DeckCommand  # v2.21 (F4): deck-scoped follow-up action
    # v2.22: TASK target language for the SLIDE CONTENT, detected from the
    # instruction (e.g. "把簡報換成英文" → "English"), independent of the
    # router's response_language (which is the chat-REPLY language). Empty/unset
    # → fall back to response_language. Consumed by _generate + _edit_slides.
    report_slide_language: str
