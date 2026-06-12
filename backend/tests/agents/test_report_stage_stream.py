"""Tests for _emit_stage and _stage_heartbeat (R5 — stage-progress events).

Covers:
  1. _emit_stage: records correct shape + no-op without writer/run_id.
  2. _stage_heartbeat: emits an initial beat, repeats on the interval, and the
     task is cancelled (no further events) after the block exits.
  3. Source-order guard: all four stage tools appear in _generate source in the
     expected order (reading → planning → drafting → compiling).
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest

import paperhub.agents.report_graph as rg
from paperhub.agents.report_graph import _emit_stage, _stage_heartbeat

# ──────────────────────── 1. _emit_stage ────────────────────────────


def test_emit_stage_appends_correct_record() -> None:
    """A recording writer receives one tool_step event with the right shape."""
    events: list[dict[str, Any]] = []

    def writer(payload: dict[str, Any]) -> None:
        events.append(payload)

    _emit_stage(writer, run_id=42, tool="report:reading", elapsed_s=3.5, step_index=-1)

    assert len(events) == 1
    ev = events[0]
    assert ev["event"] == "tool_step"
    rec = ev["record"]
    assert rec["tool"] == "report:reading"
    assert rec["run_id"] == 42
    assert rec["agent"] == "report"
    assert rec["result_summary_json"]["stage"] is True
    assert rec["result_summary_json"]["elapsed_s"] == 3.5
    assert rec["status"] == "ok"
    assert rec["error"] is None
    assert rec["branch"] == ""
    assert rec["step_index"] == -1
    assert rec["parent_step"] is None
    assert rec["model"] == ""
    assert rec["latency_ms"] == 0
    assert rec["token_in"] == 0
    assert rec["token_out"] == 0


def test_emit_stage_noop_without_writer() -> None:
    """None writer → no error, no side-effects."""
    # Should complete without raising.
    _emit_stage(None, run_id=1, tool="report:reading")


def test_emit_stage_noop_without_run_id() -> None:
    """None run_id → no-op even with a valid writer."""
    events: list[dict[str, Any]] = []

    def writer(payload: dict[str, Any]) -> None:
        events.append(payload)

    _emit_stage(writer, run_id=None, tool="report:reading")
    assert events == []


# ──────────────────────── 2. _stage_heartbeat ────────────────────────


@pytest.mark.asyncio
async def test_stage_heartbeat_emits_and_cancels() -> None:
    """Heartbeat fires immediately + on interval; stops after context exits."""
    events: list[dict[str, Any]] = []

    def writer(payload: dict[str, Any]) -> None:
        events.append(payload)

    async with _stage_heartbeat(writer, run_id=7, tool="report:planning", every=0.01):
        await asyncio.sleep(0.05)

    count_after_exit = len(events)
    # Must have fired at least 2 times (immediate + ≥1 repeat over 50 ms / 10 ms)
    assert count_after_exit >= 2, f"Expected ≥2 beats, got {count_after_exit}"

    # All events are tool_step with the right tool.
    for ev in events:
        assert ev["event"] == "tool_step"
        assert ev["record"]["tool"] == "report:planning"
        assert ev["record"]["run_id"] == 7

    # The elapsed counter increases monotonically (each beat uses n * every).
    elapsed_vals = [ev["record"]["result_summary_json"]["elapsed_s"] for ev in events]
    assert elapsed_vals == sorted(elapsed_vals), "elapsed_s should be non-decreasing"

    # After context exit the task is cancelled — no further events arrive.
    await asyncio.sleep(0.03)
    assert len(events) == count_after_exit, (
        "Events kept arriving after _stage_heartbeat context exited — task not cancelled"
    )


@pytest.mark.asyncio
async def test_stage_heartbeat_noop_with_none_run_id() -> None:
    """Heartbeat is a no-op when run_id is None (no events emitted)."""
    events: list[dict[str, Any]] = []

    def writer(payload: dict[str, Any]) -> None:
        events.append(payload)

    async with _stage_heartbeat(writer, run_id=None, tool="report:reading", every=0.01):
        await asyncio.sleep(0.03)

    assert events == []


# ──────────────────────── 3. Source-order guard ────────────────────────


def test_generate_wraps_four_stages_in_order() -> None:
    """_generate source must contain all four _stage_heartbeat calls in the
    correct order: reading → planning → drafting → compiling."""
    src = inspect.getsource(rg)

    tools_in_order = [
        "report:reading",
        "report:planning",
        "report:drafting",
        "report:compiling",
    ]

    positions: list[int] = []
    for tool in tools_in_order:
        pos = src.find(f'"{tool}"')
        assert pos != -1, f'"{tool}" not found in report_graph module source'
        positions.append(pos)

    # Each tool must appear after the previous one (order guard).
    for i in range(1, len(positions)):
        assert positions[i] > positions[i - 1], (
            f'"{tools_in_order[i]}" appears before "{tools_in_order[i-1]}" in source'
        )

    # All four must be wrapped via _stage_heartbeat (simple substring check).
    for tool in tools_in_order:
        # Confirm the tool string appears adjacent to _stage_heartbeat usage.
        heartbeat_usage = f'_stage_heartbeat(writer, run_id, "{tool}")'
        assert heartbeat_usage in src, (
            f'_stage_heartbeat wrapper for "{tool}" not found in source; '
            f'expected: {heartbeat_usage!r}'
        )
