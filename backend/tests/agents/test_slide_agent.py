"""F4.5 Phase 8.2 — slide_agent tool-call dispatch loop tests."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from paperhub.agents.slide_agent import SlideAgentResult, run_slide_agent
from paperhub.models.slide_domain import (
    CompileCheckResult,
    FigureDimensions,
    FrameOverflowSignal,
    KeyFigureBundle,
    PaperContextBundle,
    UnrenderedMathFrame,
)


def _bundle() -> PaperContextBundle:
    return PaperContextBundle(
        paper_id=1,
        paper_idx=0,
        title="t",
        authors=[],
        year=2025,
        narrative_summary="x",
        key_figures=[
            KeyFigureBundle(
                key="p0-fig-001",
                role="overview",
                one_line_interpretation="x",
                dimensions=FigureDimensions(width_px=1000, height_px=1000),
            )
        ],
        key_equations=[],
        section_excerpts=[],
        paper_newcommands=[],
    )


def _tool_call_msg(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call_{tool_name}",
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(args),
                            },
                        }
                    ],
                }
            }
        ]
    }


def _final_msg() -> dict[str, Any]:
    return {"choices": [{"message": {"content": "", "tool_calls": []}}]}


_GOOD_DECK = (
    "\\documentclass{beamer}\n"
    "\\begin{document}\n"
    "\\begin{frame}{A}body\\end{frame}\n"
    "\\end{document}\n"
)


def test_default_tool_call_budget_is_30():
    from paperhub.agents.slide_agent import DEFAULT_MAX_TOOL_CALLS
    assert DEFAULT_MAX_TOOL_CALLS == 30


@pytest.mark.asyncio
async def test_happy_path_initial_draft_then_compile_then_done(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, fake_tracer: Any
) -> None:
    """Agent calls initial_draft → compile_check (ok=True) → done() → returns."""
    bundles = [_bundle()]
    workdir = tmp_path / "slides"
    workdir.mkdir()

    llm = AsyncMock()
    llm.side_effect = [
        _tool_call_msg("initial_draft", {"deck_tex": _GOOD_DECK}),
        _tool_call_msg("compile_check", {}),
        _tool_call_msg("done", {}),
    ]

    async def fake_compile_check(**kw: Any) -> CompileCheckResult:
        return CompileCheckResult(
            ok=True,
            page_count=1,
            compile_errors=[],
            frame_overflow=[],
            unrendered_math_frames=[],
        )

    monkeypatch.setattr(
        "paperhub.agents.slide_agent.run_compile_check", fake_compile_check
    )

    result = await run_slide_agent(
        bundles=bundles,
        task_description="Generate slides",
        response_language="English",
        resolved_preamble=r"\documentclass{beamer}",
        workdir=workdir,
        existing_deck_tex=None,
        figure_inventory={},
        memory_context="",
        tracer=fake_tracer,
        model="stub",
        llm_acompletion=llm,
    )
    assert isinstance(result, SlideAgentResult)
    assert result.deck_tex == _GOOD_DECK
    assert result.satisfied is True
    assert result.tool_calls_used == 3


@pytest.mark.asyncio
async def test_done_rejected_when_unrendered_math_frames_present(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, fake_tracer: Any
) -> None:
    """compile_check returns unrendered_math; done() is rejected; agent must
    keep going (here it gives up by emitting a no-tool-calls msg → returns
    satisfied=False)."""
    bundles = [_bundle()]
    workdir = tmp_path / "slides"
    workdir.mkdir()

    llm = AsyncMock()
    llm.side_effect = [
        _tool_call_msg("initial_draft", {"deck_tex": _GOOD_DECK}),
        _tool_call_msg("compile_check", {}),
        _tool_call_msg("done", {}),  # rejected
        _final_msg(),  # agent gives up
    ]

    async def fake_compile_check(**kw: Any) -> CompileCheckResult:
        return CompileCheckResult(
            ok=False,
            page_count=1,
            compile_errors=[],
            frame_overflow=[],
            unrendered_math_frames=[
                UnrenderedMathFrame(
                    frame_index=0,
                    frame_title="X",
                    matched_equation_role="r",
                    matched_equation_latex=r"\Phi = 1",
                    paper_idx=0,
                    recommendation="...",
                )
            ],
        )

    monkeypatch.setattr(
        "paperhub.agents.slide_agent.run_compile_check", fake_compile_check
    )

    result = await run_slide_agent(
        bundles=bundles,
        task_description="x",
        response_language="en",
        resolved_preamble=r"\documentclass{beamer}",
        workdir=workdir,
        existing_deck_tex=None,
        figure_inventory={},
        memory_context="",
        tracer=fake_tracer,
        model="stub",
        llm_acompletion=llm,
    )
    assert result.satisfied is False
    assert result.last_compile_check is not None
    assert len(result.last_compile_check.unrendered_math_frames) == 1


@pytest.mark.asyncio
async def test_replace_frame_then_done_resolves_math_violation(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, fake_tracer: Any
) -> None:
    """Realistic remediation: initial_draft → compile (math missing) →
    replace_frame → compile (clean) → done() (accepted)."""
    bundles = [_bundle()]
    workdir = tmp_path / "slides"
    workdir.mkdir()

    llm = AsyncMock()
    llm.side_effect = [
        _tool_call_msg("initial_draft", {"deck_tex": _GOOD_DECK}),
        _tool_call_msg("compile_check", {}),
        _tool_call_msg(
            "replace_frame",
            {
                "frame_index": 0,
                "new_frame_tex": r"\begin{frame}{A}\[ \Phi = 1 \]\end{frame}",
            },
        ),
        _tool_call_msg("compile_check", {}),
        _tool_call_msg("done", {}),
    ]

    call_count = {"n": 0}

    async def fake_compile_check(**kw: Any) -> CompileCheckResult:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return CompileCheckResult(
                ok=False,
                page_count=1,
                compile_errors=[],
                frame_overflow=[],
                unrendered_math_frames=[
                    UnrenderedMathFrame(
                        frame_index=0,
                        frame_title="A",
                        matched_equation_role="r",
                        matched_equation_latex=r"\Phi=1",
                        paper_idx=0,
                        recommendation="x",
                    )
                ],
            )
        return CompileCheckResult(
            ok=True,
            page_count=1,
            compile_errors=[],
            frame_overflow=[],
            unrendered_math_frames=[],
        )

    monkeypatch.setattr(
        "paperhub.agents.slide_agent.run_compile_check", fake_compile_check
    )

    result = await run_slide_agent(
        bundles=bundles,
        task_description="x",
        response_language="en",
        resolved_preamble=r"\documentclass{beamer}",
        workdir=workdir,
        existing_deck_tex=None,
        figure_inventory={},
        memory_context="",
        tracer=fake_tracer,
        model="stub",
        llm_acompletion=llm,
    )
    assert result.satisfied is True
    assert "\\Phi = 1" in result.deck_tex


@pytest.mark.asyncio
async def test_slide_agent_retries_on_transient_gemini_disconnect(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, fake_tracer: Any
) -> None:
    """First acompletion raises a transient ``Server disconnected`` error →
    the retry helper kicks in → next call succeeds. Real-API Run 341 / case
    5 (slides-multi-zh) hit ``litellm.APIConnectionError`` mid-loop; the
    global ``litellm.num_retries=3`` did not catch it.
    """
    bundles = [_bundle()]
    workdir = tmp_path / "slides"
    workdir.mkdir()

    class _Disconnect(Exception):
        pass

    call_count = {"n": 0}

    async def flaky_llm(**kwargs: Any) -> Any:
        call_count["n"] += 1
        # First call: simulate Gemini server disconnect.
        if call_count["n"] == 1:
            raise _Disconnect("Server disconnected")
        # Second call (the retry): drive a normal happy path.
        if call_count["n"] == 2:
            return _tool_call_msg("initial_draft", {"deck_tex": _GOOD_DECK})
        if call_count["n"] == 3:
            return _tool_call_msg("compile_check", {})
        return _tool_call_msg("done", {})

    async def fake_compile_check(**kw: Any) -> CompileCheckResult:
        return CompileCheckResult(
            ok=True,
            page_count=1,
            compile_errors=[],
            frame_overflow=[],
            unrendered_math_frames=[],
        )

    # Skip the real backoff so the test is fast.
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(
        "paperhub.agents.slide_agent.run_compile_check", fake_compile_check
    )
    monkeypatch.setattr("paperhub.agents.slide_agent.asyncio.sleep", no_sleep)

    result = await run_slide_agent(
        bundles=bundles,
        task_description="x",
        response_language="en",
        resolved_preamble=r"\documentclass{beamer}",
        workdir=workdir,
        existing_deck_tex=None,
        figure_inventory={},
        memory_context="",
        tracer=fake_tracer,
        model="stub",
        llm_acompletion=flaky_llm,
    )

    assert result.satisfied is True
    # At least one retry happened (first call raised, second succeeded).
    assert call_count["n"] >= 2


@pytest.mark.asyncio
async def test_tool_call_budget_exhaustion_ships_imperfect(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, fake_tracer: Any
) -> None:
    """If the agent burns through the tool-call budget without done(), ship
    whatever deck state we have."""
    bundles = [_bundle()]
    workdir = tmp_path / "slides"
    workdir.mkdir()

    overflow_signal = FrameOverflowSignal(
        frame_index=0,
        frame_title="A",
        page_number=1,
        matched_layout="text_only",
        body_token_count=300,
        text_budget_tokens=100,
        overage_tokens=200,
        figure_footprint_cm2=0,
        layout_aspect_mismatch=False,
        exceeds_canvas_budget=True,
        pdflatex_overfull_pt=0.0,
        recommendation="tighten",
        split_hint="no_hint",
    )

    # initial_draft once, then a long parade of compile_check calls.
    msgs = [_tool_call_msg("initial_draft", {"deck_tex": _GOOD_DECK})]
    msgs.extend([_tool_call_msg("compile_check", {})] * 20)
    llm = AsyncMock()
    llm.side_effect = msgs

    async def fake_compile_check(**kw: Any) -> CompileCheckResult:
        return CompileCheckResult(
            ok=True,
            page_count=1,
            compile_errors=[],
            frame_overflow=[overflow_signal],
            unrendered_math_frames=[],
        )

    monkeypatch.setattr(
        "paperhub.agents.slide_agent.run_compile_check", fake_compile_check
    )

    result = await run_slide_agent(
        bundles=bundles,
        task_description="x",
        response_language="en",
        resolved_preamble=r"\documentclass{beamer}",
        workdir=workdir,
        existing_deck_tex=None,
        figure_inventory={},
        memory_context="",
        tracer=fake_tracer,
        model="stub",
        llm_acompletion=llm,
        max_tool_calls=8,
    )
    # Imperfect-deck-ship at budget exhaustion (frame_overflow is advisory).
    assert result.satisfied is False
    assert result.deck_tex == _GOOD_DECK
    assert result.tool_calls_used == 8


def _make_fast_retry_helper(max_attempts: int) -> Any:
    """Bypass the backoff sleeps for fast test execution."""
    async def _retry(llm_acompletion: Any, **kwargs: Any) -> Any:
        from paperhub.agents.slide_agent import _is_transient
        last_exc: BaseException | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return await llm_acompletion(**kwargs)
            except Exception as exc:
                last_exc = exc
                if attempt >= max_attempts or not _is_transient(exc):
                    raise
                # NO sleep for tests
        if last_exc is not None:
            raise last_exc

    return _retry


@pytest.mark.asyncio
async def test_slide_agent_ships_imperfect_when_transient_retry_exhausts(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, fake_tracer: Any
) -> None:
    """All retry attempts fail with transient error AFTER a successful
    initial_draft → ship the imperfect deck (satisfied=False), don't raise.
    Real-API Run 351 / case 5 burned 183s on Gemini disconnects after a
    deck was already drafted; this verifies the ship-imperfect fallback.
    """
    bundles = [_bundle()]
    workdir = tmp_path / "slides"
    workdir.mkdir()

    class _Disconnect(Exception):
        pass

    call_count = {"n": 0}

    async def flaky_llm(**kwargs: Any) -> Any:
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First call: initial_draft succeeds.
            return _tool_call_msg("initial_draft", {"deck_tex": _GOOD_DECK})
        # Every subsequent call fails with a transient error — the retry
        # helper exhausts its budget and re-raises. The slide_agent's catch
        # should detect deck_tex != "" and ship imperfect.
        raise _Disconnect("Server disconnected")

    async def fake_compile_check(**kw: Any) -> CompileCheckResult:
        return CompileCheckResult(
            ok=True,
            page_count=1,
            compile_errors=[],
            frame_overflow=[],
            unrendered_math_frames=[],
        )

    monkeypatch.setattr(
        "paperhub.agents.slide_agent.run_compile_check", fake_compile_check
    )
    # Reduce retry budget for fast test execution.
    monkeypatch.setattr(
        "paperhub.agents.slide_agent._acompletion_with_retry",
        _make_fast_retry_helper(max_attempts=2),
    )

    result = await run_slide_agent(
        bundles=bundles,
        task_description="x",
        response_language="en",
        resolved_preamble=r"\documentclass{beamer}",
        workdir=workdir,
        existing_deck_tex=None,
        figure_inventory={},
        memory_context="",
        tracer=fake_tracer,
        model="stub",
        llm_acompletion=flaky_llm,
    )

    assert result.satisfied is False
    assert result.deck_tex == _GOOD_DECK  # The partial deck survived.
    assert result.tool_calls_used >= 1


@pytest.mark.asyncio
async def test_slide_agent_raises_when_transient_with_no_deck(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, fake_tracer: Any
) -> None:
    """Transient error BEFORE any deck state exists (initial_draft never
    landed) → re-raise (nothing to ship)."""
    bundles = [_bundle()]
    workdir = tmp_path / "slides"
    workdir.mkdir()

    class _Disconnect(Exception):
        pass

    async def always_fails_llm(**kwargs: Any) -> Any:
        raise _Disconnect("Server disconnected")

    monkeypatch.setattr(
        "paperhub.agents.slide_agent._acompletion_with_retry",
        _make_fast_retry_helper(max_attempts=2),
    )

    with pytest.raises(Exception, match="Server disconnected"):
        await run_slide_agent(
            bundles=bundles,
            task_description="x",
            response_language="en",
            resolved_preamble=r"\documentclass{beamer}",
            workdir=workdir,
            existing_deck_tex=None,
            figure_inventory={},
            memory_context="",
            tracer=fake_tracer,
            model="stub",
            llm_acompletion=always_fails_llm,
        )


def test_acompletion_retry_default_attempts_is_5() -> None:
    """Real Gemini outages can exceed 7s; bumped default from 3 to 5 attempts
    (1s+2s+4s+8s+16s = ~31s patience)."""
    import inspect

    from paperhub.agents.slide_agent import _acompletion_with_retry

    sig = inspect.signature(_acompletion_with_retry)
    assert sig.parameters["max_attempts"].default == 5


# ---------------------------------------------------------------------------
# read_section — agentic context gather (fetch verbatim section source)
# ---------------------------------------------------------------------------
async def test_read_section_fetches_verbatim_section_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """read_section returns the section's verbatim flattened-LaTeX text + chunk
    ids so the drafter can copy an exact table/equation."""
    from paperhub.agents import slide_agent as sa
    from paperhub.agents.sl_read import ReadResult
    from paperhub.agents.slide_agent_tools import DeckState

    async def fake_read(*, paper_content_id: int, section_name: str, conn: Any) -> ReadResult:
        assert paper_content_id == 1
        assert section_name == "Results"
        return ReadResult(text=r"\begin{tabular}{cc}a&b\\1&2\end{tabular}", chunk_ids=[7, 8])

    monkeypatch.setattr(sa, "read_section_chunks", fake_read)

    state = DeckState(deck_tex="", preamble="", workdir=None)
    _, result_str, check, done = await sa._dispatch_tool_call(
        name="read_section",
        args={"paper_id": 1, "section_name": "Results"},
        state=state, bundles=[_bundle()], figure_inventory={},
        workdir=tmp_path, session_id=None, conn=object(), script="",
        pending_done_check=None,
    )
    payload = json.loads(result_str)
    assert payload["paper_id"] == 1 and payload["section_name"] == "Results"
    assert "tabular" in payload["text"]
    assert payload["chunk_ids"] == [7, 8]
    assert payload["truncated"] is False
    assert check is None and done is False


async def test_read_section_rejects_paper_not_in_deck(tmp_path: Any) -> None:
    """A paper_id not among the deck's bundles is rejected with a clear error —
    no read is attempted."""
    from paperhub.agents import slide_agent as sa
    from paperhub.agents.slide_agent_tools import DeckState

    state = DeckState(deck_tex="", preamble="", workdir=None)
    _, result_str, check, done = await sa._dispatch_tool_call(
        name="read_section",
        args={"paper_id": 999, "section_name": "Results"},
        state=state, bundles=[_bundle()], figure_inventory={},
        workdir=tmp_path, session_id=None, conn=object(), script="",
        pending_done_check=None,
    )
    assert "not in this deck" in json.loads(result_str)["error"]
    assert check is None and done is False
