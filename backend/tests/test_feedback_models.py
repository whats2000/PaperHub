import pytest
from pydantic import ValidationError

from paperhub.feedback.models import (
    REASONS,
    STAGE_OF_REASON,
    STAGES,
    ExportRequest,
    FeedbackPut,
    QueueSubmit,
)


def test_reason_stage_mapping_complete() -> None:
    assert STAGES == ["router", "retrieval", "citation", "answer"]
    assert set(STAGE_OF_REASON) == set(REASONS)
    assert set(STAGE_OF_REASON.values()) <= set(STAGES)
    assert STAGE_OF_REASON["wrong_route"] == "router"


def test_put_rejects_unknown_polarity() -> None:
    with pytest.raises(ValidationError):
        FeedbackPut(polarity="sideways", reasons=[])  # type: ignore[arg-type]
    assert FeedbackPut(polarity=None, reasons=[]).polarity is None
    assert FeedbackPut(polarity="down", reasons=["factual"]).polarity == "down"


def test_defaults() -> None:
    assert ExportRequest(cited_only=False).include_private is True
    assert QueueSubmit(answers=[]).answers == []
