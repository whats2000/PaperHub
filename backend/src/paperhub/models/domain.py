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


class KeyResult(BaseModel):
    """A single quantified empirical result a talk should mention (F4.4 T1).

    The pairing of ``number`` + ``benchmark`` is load-bearing: a talk that
    says "better accuracy" is forgettable; "14% higher accuracy on LIBERO"
    is the line the audience remembers. The Round-0 scorecards explicitly
    called out un-quantified results as a recurring failure mode.
    """

    model_config = ConfigDict(extra="forbid")

    description: str
    # number + benchmark are load-bearing: the prompt mandates "Every
    # key_result MUST include the benchmark name AND the number"; without
    # min_length=1 a lazy LLM emit of "" would still validate and record a
    # passing brief that silently drops the quantification at slide time.
    number: str = Field(min_length=1)
    benchmark: str = Field(min_length=1)


KeyFigureRole = Literal[
    "motivation",
    "overview",
    "method_diagram",
    "results_chart",
    "qualitative_example",
]


class KeyFigure(BaseModel):
    """A figure the talk should actually use (F4.4 T1).

    ``key`` matches the F2 figure inventory key scheme (``p{idx}-{figure_id}``)
    so the planner can reference real, ingested figures. ``role`` is what slot
    the figure plays in the narrative; ``one_line_interpretation`` is what
    the slide should say about it (audiences don't read figures on their own).
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    role: KeyFigureRole
    one_line_interpretation: str


KeyEquationRole = Literal[
    "objective",
    "loss",
    "update_rule",
    "model_definition",
    "auxiliary",
]


class KeyEquation(BaseModel):
    """A central equation a talk should display verbatim (F4.4 T1).

    The ``notation_explanation`` field is load-bearing — it closes the
    equation-without-symbol-definition gap from the Round-0 scorecards.
    Audiences cannot parse math on a slide unless every symbol used is
    named on the same slide. Brief content, one symbol per phrase.
    """

    model_config = ConfigDict(extra="forbid")

    latex: str
    role: KeyEquationRole
    # Load-bearing: enforced non-empty so the "every symbol named on the
    # same slide" contract from the prompt can't be silently dropped by an
    # empty LLM emit. Closes the equation-without-symbol-definition gap.
    notation_explanation: str = Field(min_length=1)


TalkShapeHint = Literal[
    "concept_only",
    "concept+math",
    "concept+math+results",
    "deep_dive",
]


class PaperTalkBrief(BaseModel):
    """Per-paper agentic brief produced by ``sl_paper_brief`` (F4.4 T1).

    Replaces today's ``PaperBrief`` as input to the future ``sl_plan_deck``
    stage (T2). The brief is a dense, structured summary of everything the
    deck planner needs to allocate slides to this paper:

    - ``contribution`` / ``method_core`` / ``key_results`` carry the
      paper's narrative substance.
    - ``key_figures`` names the 3-5 figures the talk should actually use,
      with a one-line interpretation each so the slide rendering stage
      doesn't have to re-invent what the figure means.
    - ``key_equations`` carries up to 3 central equations paired with a
      ``notation_explanation`` so the audience can parse them on a slide.
    - ``paper_newcommands`` is the raw ``\\newcommand`` / ``\\renewcommand``
      / ``\\DeclareMathOperator`` block from the paper's preamble, plumbed
      into the deck preamble by a future ``sl_assemble`` change (T4) so
      paper-specific math macros resolve in the slides.
    - ``talk_shape_hint`` gives the planner a starting allocation
      (1 / 2 / 3 / 4-5 slides) for this paper.
    """

    model_config = ConfigDict(extra="forbid")

    paper_id: int
    contribution: str
    method_core: str
    key_results: list[KeyResult]
    key_figures: list[KeyFigure]
    key_equations: list[KeyEquation]
    paper_newcommands: str = ""
    talk_shape_hint: TalkShapeHint


PatternKind = Literal[
    "title",
    "references",
    "motivation_figure",
    "bottlenecks_table",
    "concept_2col",
    "math_stack",
    "results_table",
    "proposed_direction_placeholder",
    "plan_numbered",
    "takeaway_closer",
]


class PlannedSlide(BaseModel):
    """One planned slide in a :class:`DeckOutline` (F4.4 T2).

    Names the layout ``pattern_kind`` the renderer (T3) will branch on and the
    paper-/figure-/equation- attribution the planner chose. The validation pass
    in :func:`paperhub.agents.sl_plan_deck.run_sl_plan_deck` rejects any planned
    slide whose ``paper_id`` / ``figure_key`` / ``equation_index`` does not
    correspond to an actual brief input — so the renderer can assume every
    attribution on a PlannedSlide is internally consistent.

    Note: ``title`` may be an empty string for ``title`` and ``takeaway_closer``
    patterns whose layouts do not use a ``\\frametitle``. Every other pattern
    must carry a non-empty title; the planner prompt instructs this and
    :func:`paperhub.agents.sl_plan_deck._validate_attributions` rejects empty
    title on content patterns (raises ``plan_validation_failed``).
    """

    model_config = ConfigDict(extra="forbid")

    pattern_kind: PatternKind
    title: str
    # ``goal`` is what the slide should land with the audience — T3 reads it
    # to keep the rendered bullets / caption on-message. Non-empty mandatory.
    goal: str = Field(min_length=1)
    paper_id: int | None = None
    figure_key: str | None = None
    # Index into the assigned paper's ``key_equations`` (NOT the LaTeX itself,
    # so T3 can pull the equation + its ``notation_explanation`` together).
    equation_index: int | None = None
    # 2-4 short hints for T3's renderer. NOT the final bullet text — T3 may
    # rephrase to honor the density / specificity contracts.
    key_points: list[str] = []
    # Chunks the slide draws from (collected from the brief's underlying reads
    # for traceability). May be empty for cross-paper slides.
    chunk_ids: list[int] = []


class DeckOutline(BaseModel):
    """Cross-paper deck plan produced by ``sl_plan_deck`` (F4.4 T2).

    Consumes N :class:`PaperTalkBrief` inputs and emits an ordered sequence of
    :class:`PlannedSlide` entries naming the layout patterns the renderer (T3)
    will materialise. Decoupled from rendering: the planner picks the talk
    shape + per-slide attribution; T3 turns each plan into LaTeX.

    For multi-paper (N>=2) the planner emits the academic-talk-deck skeleton
    (title → references → motivation_figure → bottlenecks_table →
    per-paper concept_2col + math_stack → proposed_direction_placeholder →
    plan_numbered → takeaway_closer). For single-paper (N==1) it degenerates
    to a focused skeleton (no bottlenecks_table, no references, no
    proposed_direction_placeholder).
    """

    model_config = ConfigDict(extra="forbid")

    talk_title: str = Field(min_length=1)
    talk_subtitle: str | None = None
    slides: list[PlannedSlide]
    # Which :class:`SlideStyleProfile` was applied. Hardcoded ``"default"``
    # for T2 (the gold methodology profile); the profile-lookup surface lands
    # in a later round.
    style_profile_name: str = "default"


class RenderedSlide(BaseModel):
    """One rendered Beamer frame produced by ``sl_render_slide`` (F4.4 T3).

    Consumes a single :class:`PlannedSlide` (+ the relevant
    :class:`PaperTalkBrief`) and emits exactly one ``\\begin{frame}...
    \\end{frame}`` block. The renderer does NOT emit a preamble or
    ``\\documentclass`` — assemble (T4) concatenates these frames into the
    final deck. ``figure_keys_used`` lets the existing ``sl_verify_figures``
    step trust each frame is internally consistent before concatenation;
    ``callback_reads`` records which bounded ``read_section`` /
    ``read_figure_block`` calls the renderer made when the brief's
    pre-extracted summary was insufficient (per the agent-flow observability
    iron rule — every step records enough state to reconstruct the loop from
    the DB alone).
    """

    model_config = ConfigDict(extra="forbid")

    slide_index: int
    pattern_kind: PatternKind
    paper_id: int | None = None
    frame_tex: str
    figure_keys_used: list[str] = []
    callback_reads: list[dict[str, str]] = []


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

    action: Literal[
        "generate_notes", "edit_notes", "edit_slides",
        "edit_title", "edit_preamble", "regenerate",
    ]
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
    # F4.4 T4: list of PaperTalkBrief, one per enabled paper, populated by the
    # future sl_paper_brief stage (T5 wires it). Consumed by sl_assemble to
    # plumb each brief's ``paper_newcommands`` into the deck preamble between
    # ``% BEGIN/END paperhub:paper_newcommands`` markers. Until T5 lands the
    # list is empty and the assemble step emits the marker-only block.
    report_paper_briefs: list[Any]  # list[PaperTalkBrief]
