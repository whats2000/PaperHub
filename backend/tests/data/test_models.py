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


def test_paper_extraction_tier_latex() -> None:
    """Paper with extraction_tier='latex' (Tier 1 path) has no low-fidelity annotation."""
    pid = uuid4()
    p = Paper(
        id=pid,
        arxiv_id="1706.03762",
        doi=None,
        title="Attention Is All You Need",
        authors=["Vaswani et al."],
        year=2017,
        abstract="Transformer architecture.",
        pdf_path="papers/1706.03762/source.tex",
        sha256="a" * 64,
        primary_topic=None,
        added_at=datetime(2026, 5, 17, 12, 0, 0),
        extraction_tier="latex",
        notes_md=None,
    )
    assert p.extraction_tier == "latex"
    assert p.notes_md is None
    # Round-trip
    assert Paper.model_validate(p.model_dump()) == p


def test_paper_extraction_tier_raw() -> None:
    """Paper with extraction_tier='raw' (Tier 3 fallback) is annotated as low-fidelity."""
    pid = uuid4()
    p = Paper(
        id=pid,
        arxiv_id="2301.07041",
        doi=None,
        title="Test Paper",
        authors=["Alice"],
        year=2023,
        abstract="Abstract.",
        pdf_path="papers/2301.07041/fallback.md",
        sha256="b" * 64,
        primary_topic=None,
        added_at=datetime(2026, 5, 17, 12, 0, 0),
        extraction_tier="raw",
        notes_md="low_fidelity_extraction",
    )
    assert p.extraction_tier == "raw"
    assert p.notes_md == "low_fidelity_extraction"
    # Round-trip
    assert Paper.model_validate(p.model_dump()) == p


def test_paper_extraction_tier_none_backward_compat() -> None:
    """Pre-migration Paper rows with no extraction_tier are valid (None = unknown tier)."""
    pid = uuid4()
    p = Paper(
        id=pid,
        arxiv_id="old-paper",
        doi=None,
        title="Old Paper",
        authors=[],
        year=None,
        abstract=None,
        pdf_path="papers/old-paper.md",
        sha256="c" * 64,
        primary_topic=None,
        added_at=datetime(2026, 5, 17, 12, 0, 0),
        # extraction_tier and notes_md intentionally omitted → default None
    )
    assert p.extraction_tier is None
    assert p.notes_md is None


def test_paper_source_dir_path_for_tier1() -> None:
    """Tier 1 Paper with source_dir_path set round-trips cleanly."""
    pid = uuid4()
    p = Paper(
        id=pid,
        arxiv_id="1706.03762",
        doi=None,
        title="Attention Is All You Need",
        authors=["Vaswani et al."],
        year=2017,
        abstract="Transformer architecture.",
        pdf_path="papers/1706.03762/source/main.tex",
        sha256="a" * 64,
        primary_topic=None,
        added_at=datetime(2026, 5, 17, 12, 0, 0),
        extraction_tier="latex",
        notes_md=None,
        source_dir_path="papers/1706.03762/source",
    )
    assert p.source_dir_path == "papers/1706.03762/source"
    assert p.extraction_tier == "latex"
    # Round-trip
    assert Paper.model_validate(p.model_dump()) == p


def test_paper_source_dir_path_none_for_tier3() -> None:
    """Tier 3 Paper has source_dir_path=None (no unpacked archive)."""
    pid = uuid4()
    p = Paper(
        id=pid,
        arxiv_id="2301.07041",
        doi=None,
        title="Test Paper",
        authors=["Alice"],
        year=2023,
        abstract="Abstract.",
        pdf_path="papers/2301.07041/fallback.md",
        sha256="b" * 64,
        primary_topic=None,
        added_at=datetime(2026, 5, 17, 12, 0, 0),
        extraction_tier="raw",
        notes_md="low_fidelity_extraction",
        source_dir_path=None,
    )
    assert p.source_dir_path is None
    assert Paper.model_validate(p.model_dump()) == p


def test_paper_extraction_tier_invalid_value() -> None:
    """extraction_tier must be one of 'latex', 'marker', 'raw', or None."""
    pid = uuid4()
    with pytest.raises(ValueError):
        Paper(
            id=pid,
            arxiv_id="x",
            doi=None,
            title="X",
            authors=[],
            year=None,
            abstract=None,
            pdf_path="x.pdf",
            sha256="d" * 64,
            primary_topic=None,
            added_at=datetime(2026, 5, 17, 12, 0, 0),
            extraction_tier="unknown_tier",  # pydantic validates at runtime; mypy accepts str
        )


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
