"""Run 879: a runaway outline generation (65k tokens, 4 minutes, truncated JSON)
must fail fast, retry once and surface a localized message, not a raw dump."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import aiosqlite
import pytest
from pydantic import ValidationError

from paperhub.agents import sl_outline
from paperhub.agents.sl_outline import OutlineGenerationError, run_sl_outline
from paperhub.models.slide_domain import PaperDigest, RoundAction
from paperhub.tracing.tracer import Tracer


def _digest() -> PaperDigest:
    return PaperDigest(paper_id=1, title="T", abstract="a", sections=[], figures=[])


def _bad_json_error() -> ValidationError:
    try:
        RoundAction.model_validate_json('{"action": "finalize", "outline": {"talk_ti')
    except ValidationError as exc:
        return exc
    raise AssertionError


class _Adapter:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.kwargs: list[dict[str, Any]] = []

    async def structured(self, **kw: Any) -> Any:
        self.kwargs.append(kw)
        out = self.outcomes.pop(0)
        if isinstance(out, BaseException):
            raise out
        if out == "hang":
            await asyncio.sleep(5)
        return out


async def _run(adapter: _Adapter, tracer: Tracer, lang: str = "English") -> Any:
    return await run_sl_outline(
        digests=[_digest()], task_description="deck", response_language=lang,
        target_slides=8, adapter=adapter, tracer=tracer, model="m",  # type: ignore[arg-type]
        read_fn=None,  # type: ignore[arg-type]
    )


async def test_call_is_bounded_retried_once_then_fails_localized(
    fake_tracer: Tracer, migrated_db: aiosqlite.Connection,
) -> None:
    adapter = _Adapter([_bad_json_error(), _bad_json_error()])
    with pytest.raises(OutlineGenerationError) as ei:
        await _run(adapter, fake_tracer, "Traditional Chinese")
    assert len(adapter.kwargs) == 2  # one retry, no more
    assert all(k["max_tokens"] == sl_outline.OUTLINE_MAX_TOKENS for k in adapter.kwargs)
    assert "簡報" in ei.value.user_message
    assert "validation error" not in ei.value.user_message
    assert "validation error" in str(ei.value)  # the raw detail stays traceable
    async with migrated_db.execute(
        "SELECT status, error, result_summary_json FROM tool_calls "
        "WHERE tool = 'report:outline'",
    ) as cur:
        status, error, result = await cur.fetchone()  # type: ignore[misc]
    assert status == "error" and "validation error" in error
    assert json.loads(result)["attempts"] == 2


async def test_a_hung_call_times_out_and_retries(
    fake_tracer: Tracer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sl_outline, "OUTLINE_CALL_TIMEOUT_S", 0.05)
    adapter = _Adapter(["hang", "hang"])
    with pytest.raises(OutlineGenerationError):
        await _run(adapter, fake_tracer)
    assert len(adapter.kwargs) == 2


async def test_second_attempt_can_succeed(fake_tracer: Tracer) -> None:
    ok = RoundAction.model_validate({
        "action": "finalize", "narrative_pattern": "synthesis", "reads": [],
        "outline": {
            "talk_title": "t", "narrative_pattern": "synthesis", "audience_intent": "a",
            "narrative_arc": "x",
            "slides": [
                {"goal": "g1", "key_message": "m1", "paper_id": 1},
                {"goal": "g2", "key_message": "m2", "paper_id": 1},
            ],
        },
    })
    adapter = _Adapter([_bad_json_error(), ok])
    result = await _run(adapter, fake_tracer)
    assert result.outline.talk_title == "t"
    assert len(adapter.kwargs) == 2
