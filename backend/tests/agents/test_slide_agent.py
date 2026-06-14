"""F6.2 slide_agent — REVISE-ONLY tool-call dispatch loop tests.

The agent palette is EDIT-only + ``submit``. The pipeline (this loop) runs the
deterministic checks: density after every edit turn, compile on submit. The
agent never elects a check tool — the loop feeds it.
"""
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


def _clean_check() -> CompileCheckResult:
    return CompileCheckResult(
        ok=True,
        page_count=1,
        compile_errors=[],
        frame_overflow=[],
        unrendered_math_frames=[],
    )


def test_default_tool_call_budget_is_30():
    from paperhub.agents.slide_agent import DEFAULT_MAX_TOOL_CALLS
    assert DEFAULT_MAX_TOOL_CALLS == 30


def test_tool_palette_is_edit_only_plus_submit() -> None:
    """The palette must NOT contain the removed must-do step tools; it has the
    EDIT tools + read_section + submit."""
    from paperhub.agents.slide_agent import _tool_schemas

    names = {t["function"]["name"] for t in _tool_schemas()}
    assert names == {
        "read_section",
        "replace_frame",
        "insert_frame_after",
        "delete_frame",
        "replace_preamble",
        "submit",
    }
    # The removed must-do steps are guards now, never tools.
    assert not (
        names & {"initial_draft", "compile_check", "density_check", "done"}
    )


@pytest.mark.asyncio
async def test_requires_starting_deck() -> None:
    """Revise-only: an empty / None starting deck is a programmer error."""
    bundles = [_bundle()]
    with pytest.raises(ValueError, match="revise-only"):
        await run_slide_agent(
            bundles=bundles,
            task_description="x",
            response_language="en",
            resolved_preamble=r"\documentclass{beamer}",
            workdir=None,  # type: ignore[arg-type]
            existing_deck_tex=None,
            figure_inventory={},
            memory_context="",
            tracer=None,  # type: ignore[arg-type]
            model="stub",
            llm_acompletion=AsyncMock(),
        )

    with pytest.raises(ValueError, match="revise-only"):
        await run_slide_agent(
            bundles=bundles,
            task_description="x",
            response_language="en",
            resolved_preamble=r"\documentclass{beamer}",
            workdir=None,  # type: ignore[arg-type]
            existing_deck_tex="   ",
            figure_inventory={},
            memory_context="",
            tracer=None,  # type: ignore[arg-type]
            model="stub",
            llm_acompletion=AsyncMock(),
        )


@pytest.mark.asyncio
async def test_submit_triggers_compile_and_accepts_when_clean(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, fake_tracer: Any
) -> None:
    """Agent issues submit → the pipeline compiles → clean → satisfied=True."""
    bundles = [_bundle()]
    workdir = tmp_path / "slides"
    workdir.mkdir()

    llm = AsyncMock()
    llm.side_effect = [_tool_call_msg("submit", {})]

    compile_spy = AsyncMock(return_value=_clean_check())
    density_spy = AsyncMock(return_value=_clean_check())
    monkeypatch.setattr("paperhub.agents.slide_agent.run_compile_check", compile_spy)
    monkeypatch.setattr("paperhub.agents.slide_agent.run_density_check", density_spy)

    result = await run_slide_agent(
        bundles=bundles,
        task_description="Generate slides",
        response_language="English",
        resolved_preamble=r"\documentclass{beamer}",
        workdir=workdir,
        existing_deck_tex=_GOOD_DECK,
        figure_inventory={},
        memory_context="",
        tracer=fake_tracer,
        model="stub",
        llm_acompletion=llm,
    )
    assert isinstance(result, SlideAgentResult)
    assert result.deck_tex == _GOOD_DECK
    assert result.satisfied is True
    # Compile ran exactly once (on submit); density never ran (no edit turn).
    assert compile_spy.await_count == 1
    assert density_spy.await_count == 0
    assert result.last_compile_check is not None and result.last_compile_check.ok


@pytest.mark.asyncio
async def test_density_runs_automatically_after_edit_turn(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, fake_tracer: Any
) -> None:
    """After an edit turn the pipeline runs density (no agent tool call) and
    feeds the signals back to the model as a follow-up user message."""
    bundles = [_bundle()]
    workdir = tmp_path / "slides"
    workdir.mkdir()

    new_frame = r"\begin{frame}{A}edited\end{frame}"
    llm = AsyncMock()
    llm.side_effect = [
        _tool_call_msg("replace_frame", {"frame_index": 0, "new_frame_tex": new_frame}),
        _tool_call_msg("submit", {}),
    ]

    density_spy = AsyncMock(return_value=_clean_check())
    compile_spy = AsyncMock(return_value=_clean_check())
    monkeypatch.setattr("paperhub.agents.slide_agent.run_density_check", density_spy)
    monkeypatch.setattr("paperhub.agents.slide_agent.run_compile_check", compile_spy)

    result = await run_slide_agent(
        bundles=bundles,
        task_description="x",
        response_language="en",
        resolved_preamble=r"\documentclass{beamer}",
        workdir=workdir,
        existing_deck_tex=_GOOD_DECK,
        figure_inventory={},
        memory_context="",
        tracer=fake_tracer,
        model="stub",
        llm_acompletion=llm,
    )
    # Density ran once (after the edit turn), compile once (on submit).
    assert density_spy.await_count == 1
    assert compile_spy.await_count == 1
    assert result.satisfied is True
    assert new_frame in result.deck_tex

    # The density signals appeared as a user message between the two LLM calls.
    second_call_messages = llm.call_args_list[1].kwargs["messages"]
    assert any(
        m["role"] == "user" and "density" in (m.get("content") or "")
        for m in second_call_messages
    )


@pytest.mark.asyncio
async def test_failing_compile_pushes_errors_back_and_continues(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, fake_tracer: Any
) -> None:
    """submit → compile FAILS (compile_errors) → errors are pushed back as a
    message, the loop continues (agent gets another turn), satisfied stays
    False on that submit."""
    bundles = [_bundle()]
    workdir = tmp_path / "slides"
    workdir.mkdir()

    llm = AsyncMock()
    llm.side_effect = [
        _tool_call_msg("submit", {}),  # first submit → rejected
        _tool_call_msg(
            "replace_frame",
            {"frame_index": 0, "new_frame_tex": r"\begin{frame}{A}fixed\end{frame}"},
        ),
        _tool_call_msg("submit", {}),  # second submit → clean
    ]

    call_count = {"n": 0}

    async def fake_compile(**kw: Any) -> CompileCheckResult:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return CompileCheckResult(
                ok=False,
                page_count=0,
                compile_errors=["! Undefined control sequence."],
                frame_overflow=[],
                unrendered_math_frames=[],
            )
        return _clean_check()

    density_spy = AsyncMock(return_value=_clean_check())
    monkeypatch.setattr("paperhub.agents.slide_agent.run_compile_check", fake_compile)
    monkeypatch.setattr("paperhub.agents.slide_agent.run_density_check", density_spy)

    result = await run_slide_agent(
        bundles=bundles,
        task_description="x",
        response_language="en",
        resolved_preamble=r"\documentclass{beamer}",
        workdir=workdir,
        existing_deck_tex=_GOOD_DECK,
        figure_inventory={},
        memory_context="",
        tracer=fake_tracer,
        model="stub",
        llm_acompletion=llm,
    )
    # The agent made a FURTHER LLM call after the failing submit.
    assert llm.await_count == 3
    assert result.satisfied is True  # second submit was clean
    assert "fixed" in result.deck_tex

    # The compile errors were pushed back as a user message after the 1st submit.
    second_call_messages = llm.call_args_list[1].kwargs["messages"]
    assert any(
        m["role"] == "user" and "Undefined control sequence" in (m.get("content") or "")
        for m in second_call_messages
    )


@pytest.mark.asyncio
async def test_failing_compile_unrendered_math_pushes_back_and_continues(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, fake_tracer: Any
) -> None:
    """submit → compile reports unrendered_math_frames → pushed back, loop
    continues (no done acceptance on that submit)."""
    bundles = [_bundle()]
    workdir = tmp_path / "slides"
    workdir.mkdir()

    llm = AsyncMock()
    llm.side_effect = [
        _tool_call_msg("submit", {}),  # rejected (math)
        _final_msg(),  # agent gives up → ships imperfect
    ]

    async def fake_compile(**kw: Any) -> CompileCheckResult:
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

    monkeypatch.setattr("paperhub.agents.slide_agent.run_compile_check", fake_compile)
    monkeypatch.setattr(
        "paperhub.agents.slide_agent.run_density_check",
        AsyncMock(return_value=_clean_check()),
    )

    result = await run_slide_agent(
        bundles=bundles,
        task_description="x",
        response_language="en",
        resolved_preamble=r"\documentclass{beamer}",
        workdir=workdir,
        existing_deck_tex=_GOOD_DECK,
        figure_inventory={},
        memory_context="",
        tracer=fake_tracer,
        model="stub",
        llm_acompletion=llm,
    )
    assert result.satisfied is False
    assert result.last_compile_check is not None
    assert len(result.last_compile_check.unrendered_math_frames) == 1

    second_call_messages = llm.call_args_list[1].kwargs["messages"]
    assert any(
        m["role"] == "user" and "unrendered_math" in (m.get("content") or "")
        for m in second_call_messages
    )


@pytest.mark.asyncio
async def test_slide_agent_retries_on_transient_gemini_disconnect(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, fake_tracer: Any
) -> None:
    """First acompletion raises a transient ``Server disconnected`` error →
    the retry helper kicks in → next call succeeds (submit, clean compile)."""
    bundles = [_bundle()]
    workdir = tmp_path / "slides"
    workdir.mkdir()

    class _Disconnect(Exception):
        pass

    call_count = {"n": 0}

    async def flaky_llm(**kwargs: Any) -> Any:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise _Disconnect("Server disconnected")
        return _tool_call_msg("submit", {})

    monkeypatch.setattr(
        "paperhub.agents.slide_agent.run_compile_check",
        AsyncMock(return_value=_clean_check()),
    )
    monkeypatch.setattr(
        "paperhub.agents.slide_agent.run_density_check",
        AsyncMock(return_value=_clean_check()),
    )

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("paperhub.agents.slide_agent.asyncio.sleep", no_sleep)

    result = await run_slide_agent(
        bundles=bundles,
        task_description="x",
        response_language="en",
        resolved_preamble=r"\documentclass{beamer}",
        workdir=workdir,
        existing_deck_tex=_GOOD_DECK,
        figure_inventory={},
        memory_context="",
        tracer=fake_tracer,
        model="stub",
        llm_acompletion=flaky_llm,
    )

    assert result.satisfied is True
    assert call_count["n"] >= 2


@pytest.mark.asyncio
async def test_tool_call_budget_exhaustion_ships_imperfect(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, fake_tracer: Any
) -> None:
    """If the agent burns through the tool-call budget without a clean submit,
    ship whatever deck state we have (the starting deck)."""
    bundles = [_bundle()]
    workdir = tmp_path / "slides"
    workdir.mkdir()

    new_frame = r"\begin{frame}{A}edited\end{frame}"
    msgs = [
        _tool_call_msg("replace_frame", {"frame_index": 0, "new_frame_tex": new_frame})
    ] * 20
    llm = AsyncMock()
    llm.side_effect = msgs

    monkeypatch.setattr(
        "paperhub.agents.slide_agent.run_density_check",
        AsyncMock(return_value=_clean_check()),
    )
    monkeypatch.setattr(
        "paperhub.agents.slide_agent.run_compile_check",
        AsyncMock(return_value=_clean_check()),
    )

    result = await run_slide_agent(
        bundles=bundles,
        task_description="x",
        response_language="en",
        resolved_preamble=r"\documentclass{beamer}",
        workdir=workdir,
        existing_deck_tex=_GOOD_DECK,
        figure_inventory={},
        memory_context="",
        tracer=fake_tracer,
        model="stub",
        llm_acompletion=llm,
        max_tool_calls=8,
    )
    # No submit → never satisfied; ships the (edited) deck at budget exhaustion.
    assert result.satisfied is False
    assert new_frame in result.deck_tex
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
    """All retry attempts fail with transient error → ship the starting deck
    (satisfied=False), don't raise. The deck always starts non-empty now."""
    bundles = [_bundle()]
    workdir = tmp_path / "slides"
    workdir.mkdir()

    class _Disconnect(Exception):
        pass

    async def flaky_llm(**kwargs: Any) -> Any:
        raise _Disconnect("Server disconnected")

    monkeypatch.setattr(
        "paperhub.agents.slide_agent.run_compile_check",
        AsyncMock(return_value=_clean_check()),
    )
    monkeypatch.setattr(
        "paperhub.agents.slide_agent.run_density_check",
        AsyncMock(return_value=_clean_check()),
    )
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
        existing_deck_tex=_GOOD_DECK,
        figure_inventory={},
        memory_context="",
        tracer=fake_tracer,
        model="stub",
        llm_acompletion=flaky_llm,
    )

    assert result.satisfied is False
    assert result.deck_tex == _GOOD_DECK  # the starting deck survived


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
    ids so the agent can copy an exact table/equation."""
    from paperhub.agents import slide_agent as sa
    from paperhub.agents.sl_read import ReadResult
    from paperhub.agents.slide_agent_tools import DeckState

    async def fake_read(*, paper_content_id: int, section_name: str, conn: Any) -> ReadResult:
        assert paper_content_id == 1
        assert section_name == "Results"
        return ReadResult(text=r"\begin{tabular}{cc}a&b\\1&2\end{tabular}", chunk_ids=[7, 8])

    monkeypatch.setattr(sa, "read_section_chunks", fake_read)

    state = DeckState(deck_tex="", preamble="", workdir=None)
    _, result_str, check = await sa._dispatch_tool_call(
        name="read_section",
        args={"paper_id": 1, "section_name": "Results"},
        state=state, bundles=[_bundle()], figure_inventory={},
        workdir=tmp_path, session_id=None, conn=object(), script="",
    )
    payload = json.loads(result_str)
    assert payload["paper_id"] == 1 and payload["section_name"] == "Results"
    assert "tabular" in payload["text"]
    assert payload["chunk_ids"] == [7, 8]
    assert payload["truncated"] is False
    assert check is None


async def test_read_section_rejects_paper_not_in_deck(tmp_path: Any) -> None:
    """A paper_id not among the deck's bundles is rejected with a clear error —
    no read is attempted."""
    from paperhub.agents import slide_agent as sa
    from paperhub.agents.slide_agent_tools import DeckState

    state = DeckState(deck_tex="", preamble="", workdir=None)
    _, result_str, check = await sa._dispatch_tool_call(
        name="read_section",
        args={"paper_id": 999, "section_name": "Results"},
        state=state, bundles=[_bundle()], figure_inventory={},
        workdir=tmp_path, session_id=None, conn=object(), script="",
    )
    assert "not in this deck" in json.loads(result_str)["error"]
    assert check is None


async def test_submit_dispatch_returns_neutral_placeholder(tmp_path: Any) -> None:
    """submit is handled by the loop, not dispatch — dispatch returns a neutral
    placeholder so the tool-call protocol stays valid."""
    from paperhub.agents import slide_agent as sa
    from paperhub.agents.slide_agent_tools import DeckState

    state = DeckState(deck_tex=_GOOD_DECK, preamble="", workdir=tmp_path)
    new_state, result_str, check = await sa._dispatch_tool_call(
        name="submit",
        args={},
        state=state, bundles=[_bundle()], figure_inventory={},
        workdir=tmp_path, session_id=None, conn=None, script="",
    )
    payload = json.loads(result_str)
    assert payload.get("submitted") is True
    assert check is None
    assert new_state.deck_tex == _GOOD_DECK  # dispatch did NOT compile/mutate


# Suppress unused-import lint for FrameOverflowSignal kept for parity helpers.
_ = FrameOverflowSignal
