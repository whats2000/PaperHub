"""Plan H2 feedback contract: reasons, stages and request/response models."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

REASONS: list[str] = [
    "wrong_route",
    "wrong_source",
    "citation",
    "factual",
    "off_topic",
    "language_format",
]
STAGES: list[str] = ["router", "retrieval", "citation", "answer"]
STAGE_OF_REASON: dict[str, str] = {
    "wrong_route": "router",
    "wrong_source": "retrieval",
    "citation": "citation",
    "factual": "answer",
    "off_topic": "answer",
    "language_format": "answer",
}

Polarity = Literal["up", "down"]
Stage = Literal["router", "retrieval", "citation", "answer"]


OTHER_TEXT_MAX = 500
CORRECTION_MAX = 8000


class FeedbackPut(BaseModel):
    polarity: Polarity | None
    reasons: list[str] = Field(default_factory=list)
    other_text: str | None = Field(default=None, max_length=OTHER_TEXT_MAX)
    correction: str | None = Field(default=None, max_length=CORRECTION_MAX)


class FeedbackGuess(BaseModel):
    reasons: list[str]


class RunFeedback(BaseModel):
    run_id: int
    polarity: Polarity
    reasons: list[str]
    other_text: str | None = None
    correction: str | None = None


class QueueTurn(BaseModel):
    role: str
    content: str


class QueueItem(BaseModel):
    feedback_id: int
    run_id: int
    session_id: int
    session_title: str
    papers: list[str]
    earlier: list[QueueTurn]
    question: str
    answer: str
    flagged: str
    next_reply: str | None
    reviewer_doubt: str
    guessed_reasons: list[str]


class QueueAnswer(BaseModel):
    feedback_id: int
    confirmed: bool
    reasons: list[str] = Field(default_factory=list)


class QueueSubmit(BaseModel):
    answers: list[QueueAnswer]


class QueueSubmitResult(BaseModel):
    confirmed: int
    rejected: int


class Kpis(BaseModel):
    answers_with_feedback: int
    approval_rate: float | None
    negatives_explicit: int
    negatives_implicit: int
    pending: int


class StageSummary(BaseModel):
    stage: Stage
    explicit: int
    implicit: int
    weekly: list[int] = Field(min_length=4, max_length=4)
    delta: int


class OtherReason(BaseModel):
    text: str
    count: int


class Unattributed(BaseModel):
    """Negative answers whose reasons map to no stage (only "other", or none):
    they belong in no stage bar, so they are counted apart."""

    explicit: int = 0
    implicit: int = 0


class FeedbackSummary(BaseModel):
    """``kpis.negatives_*`` count NEGATIVE ANSWERS (one per answer). A stage
    counts an answer once per stage its reasons point to, so the stages can
    add up to more than the negatives; ``unattributed`` holds the answers no
    stage claims."""

    kpis: Kpis
    stages: list[StageSummary]
    other_reasons: list[OtherReason]
    unattributed: Unattributed = Field(default_factory=Unattributed)


class FeedbackCase(BaseModel):
    feedback_id: int
    run_id: int
    session_id: int
    question: str
    kind: Literal["explicit", "implicit"]
    created_at: str
    in_g1_inbox: bool = False


class PresetCreate(BaseModel):
    label: str
    stage: Stage


class Preset(BaseModel):
    label: str
    stage: Stage


class PresetLabel(BaseModel):
    """``POST /feedback/presets`` response (contract: ``{label}``)."""

    label: str


class G1InboxRequest(BaseModel):
    feedback_ids: list[int]


class G1InboxResult(BaseModel):
    appended: int
    already: int = 0


class ExportPreview(BaseModel):
    sft: int
    dpo: int
    includes_private: bool


class ExportRequest(BaseModel):
    include_implicit: bool = False
    include_private: bool = True
    since: str | None = None
    until: str | None = None
    model: str | None = None
    cited_only: bool = False


class ExportResult(BaseModel):
    dir: str
    files: list[str]
    sft: int
    dpo: int


class ReviewVerdict(BaseModel):
    """A reviewer's (``feedback_review`` / ``feedback_review_fork``) verdict on a flagged
    answer. ``doubt`` is the one sentence shown to the user as the reviewer's
    concern. Unknown or repeated reasons are dropped, so ``reasons`` is always
    an ordered subset of ``REASONS``."""

    # All three fields are REQUIRED: with defaults, the JSON schema sent as
    # ``response_format`` marks them optional and the small tier omits ``doubt``
    # (47/48 empty in the first sweep).
    verdict: Literal["confirmed", "rejected", "uncertain"]
    reasons: list[str]
    doubt: str

    @field_validator("reasons")
    @classmethod
    def _known_reasons(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for r in value:
            if r in REASONS and r not in out:
                out.append(r)
        return out


REASON_DESCRIPTIONS: dict[str, str] = {
    "wrong_route": "handled as the wrong kind of request (e.g. searched for new papers "
                   "instead of answering about the attached one)",
    "wrong_source": "used the wrong paper, or the wrong part of a paper",
    "citation": "a cited passage does not support the sentence it is attached to",
    "factual": "states something false about the paper (a number, claim or method)",
    "off_topic": "accurate, but does not answer what was asked",
    "language_format": "wrong language, or a format the user did not ask for",
}


def format_reasons_block(reasons: list[str] | None = None) -> str:
    """The ``{reasons}`` slot of the ``feedback_review`` prompts: one ``- id: meaning``
    line per reason the reviewer may pick (defaults to every ``REASONS`` id)."""
    ids = REASONS if reasons is None else [r for r in reasons if r in REASON_DESCRIPTIONS]
    return "\n".join(f"- {r}: {REASON_DESCRIPTIONS[r]}" for r in ids)
