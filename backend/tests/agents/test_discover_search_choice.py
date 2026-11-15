"""Discoverer search backend choice: trusted -> public web.search -> none."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from paperhub.agents.research_pipeline import ParsedRequest, discover_canonical
from paperhub.tracing.tracer import Tracer
from tests._research_stubs import (
    StubRegistry,
    async_completion_mock,
    dev_gate,
    msg,
    tool_call,
)

HIT = {"title": "Mamba", "url": "https://arxiv.org/abs/2312.00752", "snippet": "s"}
IDENT = json.dumps({"title": "Mamba", "author_surname": "Gu", "year": 2023,
                    "confidence": "high", "rationale": "r"})


def _seq() -> list[dict[str, Any]]:
    return [
        msg(tool_calls=[tool_call("c1", "paperhub.search_web",
                                  {"paper_hint": "mamba paper", "extra_terms": []})]),
        msg(content=IDENT),
    ]


async def _discover(fake_tracer: Tracer, reg: StubRegistry) -> Any:
    with patch("litellm.acompletion",
               new=async_completion_mock(_seq())):
        return await discover_canonical(
            ParsedRequest(hint="mamba paper", kind="natural_language"),
            tracer=fake_tracer, model="m", mcp_registry=reg, outbound=dev_gate(fake_tracer),  # type: ignore[arg-type]
        )


async def _backend(conn: aiosqlite.Connection) -> str:
    async with conn.execute(
        "SELECT result_summary_json FROM tool_calls WHERE tool=?",
        ("paper_search:paperhub.search_web",),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    return str(json.loads(row[0])["search_backend"])


async def test_discover_prefers_trusted(
    fake_tracer: Tracer, monkeypatch: pytest.MonkeyPatch,
    migrated_db: aiosqlite.Connection,
) -> None:
    monkeypatch.setenv("PAPERHUB_TRUSTED_SEARCH_URL", "https://search.example.org")
    reg = StubRegistry(web_hits=[HIT])
    fake = AsyncMock(return_value=[HIT])
    with patch("paperhub.agents.research_pipeline.trusted_search", new=fake):
        out = await _discover(fake_tracer, reg)
    assert out is not None and out.title == "Mamba"
    fake.assert_awaited_once()
    assert fake.await_args.kwargs["url"] == "https://search.example.org"
    assert [n for n, _ in reg.call_log if n == "web.search"] == []
    assert await _backend(migrated_db) == "trusted"


async def test_discover_falls_back_to_public(
    fake_tracer: Tracer, monkeypatch: pytest.MonkeyPatch,
    migrated_db: aiosqlite.Connection,
) -> None:
    monkeypatch.delenv("PAPERHUB_TRUSTED_SEARCH_URL", raising=False)
    monkeypatch.delenv("PAPERHUB_PROFILE", raising=False)
    reg = StubRegistry(web_hits=[HIT])
    out = await _discover(fake_tracer, reg)
    assert out is not None
    assert len([n for n, _ in reg.call_log if n == "web.search"]) == 1
    assert await _backend(migrated_db) == "public"


async def test_discover_without_any_search_still_resolves(
    fake_tracer: Tracer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PAPERHUB_TRUSTED_SEARCH_URL", raising=False)
    reg = StubRegistry(has_web_search=False)
    comp = AsyncMock()
    with patch("litellm.acompletion", new=comp):
        out = await discover_canonical(
            ParsedRequest(hint="mamba paper", kind="natural_language"),
            tracer=fake_tracer, model="m", mcp_registry=reg, outbound=dev_gate(fake_tracer),  # type: ignore[arg-type]
        )
    assert out is not None and out.confidence == "low" and out.title == "mamba paper"
    comp.assert_not_called()
