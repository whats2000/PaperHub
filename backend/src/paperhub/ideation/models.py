"""Plan H3 ideation contract — mirrors frontend/src/types/ideation.ts."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

IdeaStatus = Literal["new", "saved", "trying", "worked", "failed", "dismissed"]
ProblemStatus = Literal["generating", "ready", "error"]

IDEA_STATUSES: tuple[str, ...] = (
    "new", "saved", "trying", "worked", "failed", "dismissed",
)
DISMISS_REASONS: tuple[str, ...] = (
    "already_tried", "not_feasible", "analogy_fails", "not_novel", "other",
)


class CitedField(BaseModel):
    text: str
    sections: list[str] = Field(default_factory=list)


class MechanismCard(BaseModel):
    problem: CitedField
    mechanism: CitedField
    assumptions: list[CitedField] = Field(default_factory=list)
    limitations: list[CitedField] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)


class Idea(BaseModel):
    id: int
    problem_id: int | None = None
    source_doc: int
    source_section: str
    source_chunk_ids: list[int] = Field(default_factory=list)
    target_doc: int | None = None
    target_section: str | None = None
    target_chunk_ids: list[int] = Field(default_factory=list)
    title: str
    rationale: str = ""
    status: IdeaStatus = "new"
    prev_status: IdeaStatus | None = None
    note: str = ""
    dismiss_reasons: list[str] = Field(default_factory=list)
    session_ids: list[int] = Field(default_factory=list)
    created_at: str


class Problem(BaseModel):
    """``cards_ready``/``cards_total``: while a run is in flight, the library
    documents whose mechanism card is processed (built, cached or skipped) out
    of all of them; None when no run is in flight (``cards_total`` 0 = the run
    is waiting for another run's card phase). ``skipped_docs``: documents the
    latest run could not analyse (no card)."""

    id: int
    text: str
    tried: str = ""
    created_at: str
    idea_count: int = 0
    status: ProblemStatus = "generating"
    cards_ready: int | None = None
    cards_total: int | None = None
    skipped_docs: int = 0
    # QA I2: the latest finished run found nothing in the library that applies
    # (no ideas); ``nothing_relevant_reason`` is the proposer's one-sentence why,
    # in the problem's language. False while generating or when ideas exist.
    nothing_relevant: bool = False
    nothing_relevant_reason: str = ""


class GraphDocument(BaseModel):
    """A graph node. ``idea_count`` counts the scope's non-dismissed ideas
    touching it; in problem scope a document touched only by dismissed ideas
    is still returned (``idea_count`` 0) so Undo needs no refetch."""

    id: int
    title: str
    domains: list[int] = Field(default_factory=list)
    idea_count: int = 0
    is_private: bool = False


class GraphDomain(BaseModel):
    id: int
    name: str
    color_index: int | None = None  # None = gray "Other"


class GraphCitation(BaseModel):
    source_id: int
    target_id: int


class GraphResponse(BaseModel):
    """``library_count`` / ``private_count`` count the whole library
    (``paper_content``) in every scope; ``documents`` is only the scope's."""

    documents: list[GraphDocument]
    domains: list[GraphDomain]
    citations: list[GraphCitation]
    ideas: list[Idea]
    problem: Problem | None = None
    library_count: int = 0
    private_count: int = 0


PROBLEM_MAX_CHARS = 2000
"""Server-side cap on a problem's text (after trimming); a longer text is 422."""
TRIED_MAX_CHARS = 2000
"""Same cap for "what I already tried"."""


class ProblemCreate(BaseModel):
    text: str
    tried: str = ""


class ExploreRequest(BaseModel):
    """``POST /ideation/explore`` body (optional). Free exploration has no
    problem text to take the language from, so the client sends its UI
    language (a BCP-47 tag such as ``zh-TW``); English without one."""

    language: str = "en"


class IdeaPatch(BaseModel):
    status: IdeaStatus | None = None
    note: str | None = None
    dismiss_reasons: list[str] | None = None
    dismiss_note: str | None = None


class IdeaChatResponse(BaseModel):
    session_id: int
    title: str
    prefill: str
    paper_content_ids: list[int]


class ExploreResponse(BaseModel):
    started: bool = True


class DocumentDomainsPut(BaseModel):
    domain_ids: list[int] = Field(default_factory=list)
    new_names: list[str] = Field(default_factory=list)


class DocumentOut(BaseModel):
    id: int
    title: str
    domains: list[int] = Field(default_factory=list)
    is_private: bool = False


class DomainPatch(BaseModel):
    name: str | None = None
    merge_into: int | None = None


class DismissReasonCount(BaseModel):
    reason: str
    count: int


class FeedbackSummary(BaseModel):
    """``GET /ideation/feedback-summary``."""

    save_rate: float
    worked_rate: float | None = None  # null when no idea has worked/failed yet
    dismiss_reasons: list[DismissReasonCount] = Field(default_factory=list)


class FeedbackToG1Response(BaseModel):
    appended: int


MAX_IDEAS = 8


class ProposedIdea(BaseModel):
    """One idea as the ideation model emits it, before section → chunk-id
    resolution. Problem mode leaves ``target_doc``/``target_section`` null (the
    target is the problem); free exploration names both ends."""

    source_doc: int
    source_section: str
    target_doc: int | None = None
    target_section: str | None = None
    title: str
    rationale: str = ""


class IdeationOutput(BaseModel):
    """The ideation step's structured reply: at most ``MAX_IDEAS`` ideas, best
    first (rank order is what recall@5 grades). A longer list is truncated to
    the first ``MAX_IDEAS`` (the best ones), not rejected — failing a whole
    reply over a ninth idea would waste the call."""

    ideas: list[ProposedIdea] = Field(default_factory=list, max_length=MAX_IDEAS)
    no_fit_reason: str = ""
    """Set (with no ideas) when nothing in the library applies to the problem:
    one sentence on why, in the response language (QA I2)."""

    @field_validator("ideas", mode="before")
    @classmethod
    def _keep_best(cls, value: Any) -> Any:
        return value[:MAX_IDEAS] if isinstance(value, list) else value


class ProposeDraft(IdeationOutput):
    """The propose call's response schema: every field REQUIRED, because a
    native ``response_format`` lets the model omit a defaulted field (see
    ``cards.CardDraft``). Parsed back into :class:`IdeationOutput`."""

    ideas: list[ProposedIdea] = Field(max_length=MAX_IDEAS)
    no_fit_reason: str


class ExploreStatus(BaseModel):
    """``GET /ideation/explore/status``: whether an explore run is in flight
    and the latest run's outcome (Ruling U3: tell the user when a re-run kept
    no new ideas)."""

    running: bool = False
    last_run_at: str | None = None
    last_new_count: int | None = None
    last_error: str | None = None
    # Same meaning as on ``Problem``.
    cards_ready: int | None = None
    cards_total: int | None = None
    skipped_docs: int = 0
    nothing_relevant: bool = False
    nothing_relevant_reason: str = ""
