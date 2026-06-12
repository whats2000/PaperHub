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

class OutlineSlideDraft(BaseModel):
    """One planned slide as authored by the sl_outline LLM (the draft).

    The LLM never emits raw chunk integers; it names the bundle SECTIONS a
    slide draws on (`grounding_sections`). sl_outline resolves those to
    `chunks.id` deterministically (see :class:`OutlineSlide`).
    """

    # No extra="forbid": LLM structured output is not schema-strict; ignore unknown keys.

    goal: str  # one-line purpose of the slide
    key_message: str  # the single point it makes (may be "" for a title slide)
    transition_from_prev: str = ""  # the bridge from the previous slide
    paper_id: int | None = None  # paper_content.id this slide is about; None = synthesis/title
    figure_key: str | None = None  # inventory key, if the slide centres on a figure
    grounding_sections: list[str] = Field(default_factory=list)  # bundle section names this slide draws on


class DeckOutlineDraft(BaseModel):
    """The whole talk plan, as authored by the sl_outline LLM."""

    # No extra="forbid": LLM structured output is not schema-strict; ignore unknown keys.

    talk_title: str
    audience_intent: str  # what the talk should accomplish; e.g. "walk through the references"
    narrative_arc: str  # the throughline: problem framing -> bridges -> synthesis takeaway
    slides: list[OutlineSlideDraft]


class OutlineSlide(BaseModel):
    """A planned slide after deterministic resolution (grounding + index)."""

    model_config = ConfigDict(extra="forbid")

    slide_index: int  # 0-based; matches the final deck_slides.slide_index (1:1 contract)
    goal: str
    key_message: str
    transition_from_prev: str
    paper_id: int | None
    figure_key: str | None
    grounding_chunk_ids: list[int]  # resolved from grounding_sections via SQL


class DeckOutline(BaseModel):
    """The resolved talk plan handed to the slide_agent (rendered 1:1)."""

    model_config = ConfigDict(extra="forbid")

    talk_title: str
    audience_intent: str
    narrative_arc: str
    slides: list[OutlineSlide]
