"""Tests for Pydantic data models — the typed shape every persisted entity must take."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from paperhub.data.models import (
    Chunk,
    Intent,
    Note,
    Paper,
    Project,
    ReadingStatus,
    RoutingDecision,
    RunStatus,
    ToolCall,
    ToolCallStatus,
)


def test_paper_round_trip() -> None:
    pid = uuid4()
    p = Paper(
        id=pid,
        arxiv_id="2401.00001",
        doi=None,
        title="A Paper",
        authors=["Alice", "Bob"],
        year=2024,
        abstract="Abstract.",
        pdf_path="papers/abc.pdf",
        sha256="0" * 64,
        primary_topic=None,
        added_at=datetime(2026, 5, 17, 12, 0, 0),
    )
    dumped = p.model_dump()
    assert dumped["authors"] == ["Alice", "Bob"]
    revived = Paper.model_validate(dumped)
    assert revived == p
    # JSON-mode round-trip (used by DAOs persisting to JSON columns in Task 6)
    assert Paper.model_validate(p.model_dump(mode="json")) == p


def test_routing_decision_intent_literal_is_enforced() -> None:
    with pytest.raises(ValueError):
        RoutingDecision(
            intent="nonsense",  # intentional bad value
            confidence=0.5,
            model_tier="small",
            reasoning="x",
        )


def test_routing_decision_confidence_bounds() -> None:
    with pytest.raises(ValueError):
        RoutingDecision(intent="paper_qa", confidence=1.5, model_tier="small", reasoning="x")


def test_tool_call_status_is_constrained() -> None:
    tc = ToolCall(
        run_id=uuid4(),
        step_index=0,
        parent_step=None,
        agent="router",
        tool="llm",
        model="claude-haiku-4-5",
        args_redacted={"prompt": "<REDACTED>"},
        result_summary={"intent": "paper_qa"},
        latency_ms=120,
        token_in=42,
        token_out=10,
        status="ok",
        error=None,
    )
    assert tc.status == "ok"
    with pytest.raises(ValueError):
        ToolCall(
            run_id=uuid4(),
            step_index=0,
            parent_step=None,
            agent="router",
            tool="llm",
            model=None,
            args_redacted={},
            result_summary=None,
            latency_ms=1,
            token_in=None,
            token_out=None,
            status="weird",  # intentional bad value
            error=None,
        )


def test_chunk_project_note_validate() -> None:
    Project(id=uuid4(), name="Thesis", created_at=datetime.now(UTC))
    Note(id=uuid4(), paper_id=uuid4(), body_md="note", created_at=datetime.now(UTC))
    Chunk(
        id=uuid4(),
        paper_id=uuid4(),
        section="intro",
        page=1,
        char_start=0,
        char_end=100,
        text="hello",
    )


def test_literal_aliases_export_string_constants() -> None:
    # Literal type aliases used widely — smoke check the expected string members exist
    assert "deep" in ReadingStatus.__args__  # type: ignore[attr-defined]
    assert "running" in RunStatus.__args__  # type: ignore[attr-defined]
    assert "rejected" in ToolCallStatus.__args__  # type: ignore[attr-defined]
    assert "paper_qa" in Intent.__args__  # type: ignore[attr-defined]
