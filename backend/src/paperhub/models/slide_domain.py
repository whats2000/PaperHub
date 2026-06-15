"""F4.5 slide-pipeline schemas (replaces R1's PaperTalkBrief / PlannedSlide /
RenderedSlide / DeckOutline in models/domain.py).

Lives in a separate module so the R1 deletion in Phase 14 leaves these
untouched. KeyFigureBundle / KeyEquationBundle preserve the shape of R1's
KeyFigure / KeyEquation but add probed dimensions + drop F4.4-specific fields.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field


class FigureDimensions(BaseModel):
    """Pixel dimensions probed via PIL at gather_context time."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def aspect_ratio(self) -> float:
        """w/h. >1 = landscape; <1 = portrait; ~1 = square."""
        return self.width_px / self.height_px


KeyFigureRole = Literal[
    "overview", "method", "ablation", "result", "qualitative", "supporting"
]


class KeyFigureBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    role: KeyFigureRole
    one_line_interpretation: str
    dimensions: FigureDimensions


class KeyEquationBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latex: str
    role: str  # e.g. 'visual_token_importance_score' — used by math_auditor's
               # role-keyword overlap (so the role string should be descriptive
               # snake_case for token-overlap matching to work)
    notation_legend: str


class SectionExcerpt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_name: str
    text: str  # ≤ ~1000 chars; the agent quotes from this


class PaperContextBundle(BaseModel):
    """One per paper, produced by gather_context.

    Holds the narrative + grounded asset inventory the monolithic slide_agent
    consumes. NOT a structured plan — the agent decides layout in-loop.
    """

    model_config = ConfigDict(extra="forbid")

    paper_id: int  # paper_content.id
    paper_idx: int  # 0-based within the deck's contributing papers
    title: str
    authors: list[str]
    year: int | None
    narrative_summary: str  # contribution + method core + key results, prose
    key_figures: list[KeyFigureBundle]
    key_equations: list[KeyEquationBundle]
    section_excerpts: list[SectionExcerpt]
    paper_newcommands: list[str]  # raw \newcommand lines from ADDITIONAL.tex
    read_chunk_ids: list[int] = Field(default_factory=list)  # chunk IDs read during gather (grounding source)


# --- detector signals ----------------------------------------------------

OverflowRecommendation = Literal[
    "ok", "tighten", "shrink_figure", "relayout_figure", "split_frame"
]

SplitHint = Literal[
    "figure_to_own_frame_then_text",
    "halve_bullets_across_two_frames",
    "move_table_to_own_frame",
    "no_hint",
]


class FrameOverflowSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frame_index: int  # 0-based within the deck
    frame_title: str
    page_number: int  # 1-based PDF page
    matched_layout: str  # name from slide_canvas_budget.yaml, or 'unknown'
    body_token_count: int
    text_budget_tokens: int
    overage_tokens: int  # max(0, body_token_count - text_budget_tokens)
    figure_footprint_cm2: float
    layout_aspect_mismatch: bool
    exceeds_canvas_budget: bool
    pdflatex_overfull_pt: float  # 0.0 when no Overfull message attributed
    recommendation: OverflowRecommendation
    split_hint: SplitHint = "no_hint"


class UnrenderedMathFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frame_index: int
    frame_title: str
    matched_equation_role: str
    matched_equation_latex: str
    paper_idx: int
    recommendation: str  # human-readable hint for the agent


class CompileCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool  # True iff zero compile_errors AND zero unrendered_math_frames
    page_count: int
    compile_errors: list[str]
    frame_overflow: list[FrameOverflowSignal]
    unrendered_math_frames: list[UnrenderedMathFrame]


# --- F6.1: slide narrative outline (the sl_outline stage) ----------------

class ContextRequest(BaseModel):
    """One aimed gather request emitted by the orchestrator LLM."""

    model_config = ConfigDict(extra="forbid")

    aim: str  # the specific detail to fetch, e.g. "quantitative ablation results for encoder choice"
    paper_id: int


class OutlineSlideDraft(BaseModel):
    """One planned slide as authored by the sl_outline LLM (the draft).

    The LLM never emits raw chunk integers; it names the aims whose gathered
    context a slide draws on (`cites_aims`). sl_outline resolves those to
    `chunks.id` deterministically via the gathered PaperContextBundles
    (see :class:`OutlineSlide`).
    """

    # No extra="forbid": LLM structured output is not schema-strict; ignore unknown keys.

    goal: str  # one-line purpose of the slide
    key_message: str  # the single point it makes (may be "" for a title slide)
    content_form: str = "bullets"  # how to SHOW the slide — bullets/comparison_table/results/…
    transition_from_prev: str = ""  # the bridge from the previous slide
    speaker_note_hint: str = ""  # "SAY" content: explanations + transition bridge for the notes agent; NOT shown on slide
    paper_id: int | None = None  # paper_content.id this slide is about; None = synthesis/title
    figure_key: str | None = None  # inventory key, if the slide centres on a figure
    grounding_sections: list[str] = Field(default_factory=list)  # legacy: bundle section names (unused in F6.1+)
    cites_aims: list[str] = Field(default_factory=list)  # legacy (unused in F6.1-R): the aims whose gathered chunks ground this slide
    cites_reads: list[str] = Field(default_factory=list)  # read keys "<paper_id>:<section_name>" whose evidence grounds this slide


class DeckOutlineDraft(BaseModel):
    """The whole talk plan, as authored by the sl_outline LLM."""

    # No extra="forbid": LLM structured output is not schema-strict; ignore unknown keys.

    talk_title: str
    narrative_pattern: str = "synthesis"  # chosen talk archetype — single_paper/comparison/…
    audience_intent: str  # what the talk should accomplish; e.g. "walk through the references"
    narrative_arc: str  # the throughline: problem framing -> bridges -> synthesis takeaway
    slides: list[OutlineSlideDraft]


class ReadRequest(BaseModel):
    """A deterministic read_section fetch request emitted by the R-path orchestrator."""

    model_config = ConfigDict(extra="forbid")

    paper_id: int
    section_name: str


class RoundAction(BaseModel):
    """The orchestrator LLM's per-round decision: gather more context or finalize.

    Placed after DeckOutlineDraft to allow the forward reference to resolve.

    action values:
      "dispatch" — (legacy) gather via aimed PaperContextBundle fetches
      "read"     — (F6.1-R) fetch specific sections via ReadRequest list
      "finalize" — emit the DeckOutlineDraft
    """

    # No extra="forbid": LLM structured output is not schema-strict; ignore unknown keys.

    action: Literal["dispatch", "read", "finalize"]
    narrative_pattern: str = "synthesis"  # chosen round 1, echoed thereafter
    requests: list[ContextRequest] = Field(default_factory=list)  # when action == "dispatch"
    reads: list[ReadRequest] = Field(default_factory=list)  # when action == "read"
    outline: DeckOutlineDraft | None = None  # when action == "finalize"


class SourceSection(BaseModel):
    """One (paper, section) a slide was grounded in, with the chunk ids read.

    Persisted per slide into ``deck_slides.source_sections_json`` — the
    traceability north star for slides: every page records the paper
    section(s) it was written from. ``chunk_ids`` is empty when the frame's
    ``% cite:`` marker named a section that does not resolve to evidence
    (the non-blocking "unsourced" signal surfaced to the Sources panel).
    """

    model_config = ConfigDict(extra="forbid")

    paper_id: int
    section_name: str
    chunk_ids: list[int]


class OutlineSlide(BaseModel):
    """A planned slide after deterministic resolution (grounding + index)."""

    model_config = ConfigDict(extra="forbid")

    slide_index: int  # 0-based; matches the final deck_slides.slide_index (1:1 contract)
    goal: str
    key_message: str
    content_form: str = "bullets"  # how to SHOW the slide — bullets/comparison_table/results/…
    transition_from_prev: str
    speaker_note_hint: str = ""  # "SAY" content: explanations + transition bridge for the notes agent; NOT shown on slide
    paper_id: int | None
    figure_key: str | None
    grounding_chunk_ids: list[int]  # union of read_chunk_ids from each cited aim's PaperContextBundle
    support_excerpts: list[str] = Field(default_factory=list)  # gathered evidence for the drafter


class DeckOutline(BaseModel):
    """The resolved talk plan handed to the slide_agent (rendered 1:1)."""

    model_config = ConfigDict(extra="forbid")

    talk_title: str
    narrative_pattern: str = "synthesis"  # chosen talk archetype — single_paper/comparison/…
    audience_intent: str
    narrative_arc: str
    slides: list[OutlineSlide]


class SeedFigure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    caption: str = ""


class DigestSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    insight: str  # 1-2 line compression of what this section says


class DigestEquation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latex: str
    role: str = ""


class PaperDigest(BaseModel):
    """Cheap cached per-section summary of one paper (F6.1-R gather rework).

    Produced once per paper and reused by the outline orchestrator to structure
    the deck without fetching full section text upfront.
    """

    model_config = ConfigDict(extra="forbid")

    paper_id: int
    title: str
    abstract: str
    sections: list[DigestSection]
    figures: list[SeedFigure]  # reuse the existing SeedFigure
    key_equations: list[DigestEquation] = Field(default_factory=list)


class SeedPaper(BaseModel):
    """Deterministic high-level map of one paper — the orchestrator's dispatch menu."""

    model_config = ConfigDict(extra="forbid")

    paper_id: int
    title: str
    abstract: str
    is_survey: bool          # a survey is internally multi-work -> decompose into its branches
    sections: list[str]      # section names = the menu of what a detail gather can aim at
    figures: list[SeedFigure]


class OutlineResult(BaseModel):
    """What the orchestrator returns: the outline + how many refine rounds it took."""

    model_config = ConfigDict(extra="forbid")

    outline: DeckOutline
    rounds_used: int
