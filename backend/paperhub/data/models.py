"""Pydantic data models for every persisted entity.

These are the SHAPES — converting to/from SQL rows is the data-access
layer's job, added in Phase A Task 6. Per NFR-11, no Any, no untyped
dict in public field types. The two dict[str, object] fields on ToolCall
are JSON columns whose schemas vary by call site — the tracer enforces
they were Pydantic-validated upstream.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ReadingStatus = Literal["unread", "skimmed", "deep"]
MessageRole = Literal["user", "assistant", "system"]
RunStatus = Literal["running", "ok", "failed"]
ToolCallStatus = Literal["ok", "error", "rejected"]
ModelTier = Literal["small", "flagship"]
Intent = Literal["paper_qa", "library_stats", "research_suggest", "slides", "mcp_tool", "chitchat"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Project(_Frozen):
    id: UUID
    name: str
    created_at: datetime


class Paper(_Frozen):
    """A paper imported into PaperHub.

    ``pdf_path`` is the path to the **primary artifact** relative to the
    workspace root.  The artifact format depends on the extraction tier:
      - Tier 1 (``extraction_tier='latex'``) → ``.tex`` file
        (``papers/<arxiv_id>/source.tex``)
      - Tier 2 (``extraction_tier='marker'``) → ``.md`` file (Phase B)
      - Tier 3 (``extraction_tier='raw'``) → ``.md`` file
        (``papers/<arxiv_id>/fallback.md``)
    The column name ``pdf_path`` is kept as-is to avoid breaking existing
    tests and migrations; its semantics have been broadened.

    ``notes_md`` carries import-time annotations.  When ``'low_fidelity_extraction'``
    the artifact is a lossy Tier-3 raw-markdown extract; downstream consumers
    (e.g. the Phase B slide pipeline) should decline to use it.
    """

    id: UUID
    arxiv_id: str | None
    doi: str | None
    title: str
    authors: list[str]
    year: int | None
    abstract: str | None
    pdf_path: str
    sha256: str
    primary_topic: str | None
    added_at: datetime
    extraction_tier: Literal["latex", "marker", "raw"] | None = None
    notes_md: str | None = None


class ProjectPaper(_Frozen):
    project_id: UUID
    paper_id: UUID
    reading_status: ReadingStatus | None


class Tag(_Frozen):
    paper_id: UUID
    tag: str


class Note(_Frozen):
    id: UUID
    paper_id: UUID
    body_md: str
    created_at: datetime


class Chunk(_Frozen):
    id: UUID
    paper_id: UUID
    section: str | None
    page: int | None
    char_start: int | None
    char_end: int | None
    text: str


class Citation(_Frozen):
    src_paper_id: UUID
    dst_paper_id: UUID
    source: str


class ChatSession(_Frozen):
    id: UUID
    project_id: UUID | None
    title: str | None
    created_at: datetime


class Message(_Frozen):
    id: UUID
    session_id: UUID
    role: MessageRole
    content: str
    run_id: UUID | None
    created_at: datetime


class RoutingDecision(_Frozen):
    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    model_tier: ModelTier
    reasoning: str
    fallback_to_user: bool = False


class RunMetadata(_Frozen):
    id: UUID
    session_id: UUID | None
    routing_decision: RoutingDecision | None
    started_at: datetime
    finished_at: datetime | None
    status: RunStatus


class ToolCall(_Frozen):
    run_id: UUID
    step_index: int
    parent_step: int | None
    agent: str
    tool: str
    model: str | None
    args_redacted: dict[str, object]
    result_summary: dict[str, object] | None
    latency_ms: int = Field(ge=0)
    token_in: int | None = Field(default=None, ge=0)
    token_out: int | None = Field(default=None, ge=0)
    status: ToolCallStatus
    error: str | None
