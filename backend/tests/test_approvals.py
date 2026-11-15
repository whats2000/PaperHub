"""Approval waiters: a live turn pauses on its cards (Plan H5a)."""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest

from paperhub import approvals
from paperhub.approvals import ApprovalDecision

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    yield
    for run_id in list(approvals._scopes):
        approvals.close_run(run_id)


def _events() -> tuple[list[tuple[str, dict[str, Any]]], Any]:
    seen: list[tuple[str, dict[str, Any]]] = []
    return seen, lambda name, data: seen.append((name, data))


async def test_a_wait_blocks_until_delivered_with_no_timeout() -> None:
    approvals.open_run(1)
    task = asyncio.create_task(approvals.wait(
        run_id=1, kind="held", row_id="h1", tracer=None))
    await asyncio.sleep(0.05)
    assert not task.done()
    assert approvals.has_waiter("held:h1")
    assert approvals.deliver("held:h1", ApprovalDecision(True, "q", edited=True))
    assert await task == ApprovalDecision(True, "q", edited=True)
    assert not approvals.has_waiter("held:h1")
    assert approvals.deliver("held:h1", ApprovalDecision(False)) is None


async def test_no_waiter_means_no_delivery() -> None:
    assert approvals.deliver("action:x", ApprovalDecision(True)) is None
    assert not approvals.is_open(5)


async def test_closing_the_run_ends_the_wait() -> None:
    approvals.open_run(2)
    task = asyncio.create_task(approvals.wait(
        run_id=2, kind="action", row_id="a1", tracer=None))
    await asyncio.sleep(0)
    approvals.close_run(2)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not approvals.has_waiter("action:a1")


async def test_paused_only_when_every_running_call_waits() -> None:
    seen, emit = _events()
    approvals.open_run(3, emit)
    other_done = asyncio.Event()

    async def waiting_call() -> ApprovalDecision:
        with approvals.call_scope(3, "c1", "remove_paper"):
            return await approvals.wait(run_id=3, kind="action", row_id="a1",
                                        tracer=None)

    async def other_call() -> None:
        with approvals.call_scope(3, "c2", "read_section"):
            await other_done.wait()

    t2 = asyncio.create_task(other_call())
    t1 = asyncio.create_task(waiting_call())
    await asyncio.sleep(0.01)
    assert seen == []  # read_section is still running
    other_done.set()
    await t2
    assert seen == [("paused", {"run_id": 3, "cards": [
        {"kind": "action", "id": "a1", "tool": "remove_paper", "call_id": "c1"}]})]
    outcome = approvals.deliver("action:a1", ApprovalDecision(False))
    assert outcome is not None
    await t1
    assert seen[-1] == ("resumed", {"run_id": 3})


async def test_a_held_card_lists_once_and_updates_while_paused() -> None:
    seen, emit = _events()
    approvals.open_run(4, emit)

    async def call() -> None:
        with approvals.call_scope(4, "c1", "search_papers"):
            await asyncio.gather(
                approvals.wait(run_id=4, kind="held", row_id="r1", card_id="r1",
                               tracer=None),
                approvals.wait(run_id=4, kind="held", row_id="r2", card_id="r1",
                               tracer=None),
                approvals.wait(run_id=4, kind="held", row_id="r3", card_id="r3",
                               tracer=None),
            )

    task = asyncio.create_task(call())
    await asyncio.sleep(0.01)
    assert seen[-1][1]["cards"] == [
        {"kind": "held", "id": "r1", "tool": "search_papers", "call_id": "c1"},
        {"kind": "held", "id": "r3", "tool": "search_papers", "call_id": "c1"}]
    approvals.deliver("held:r1", ApprovalDecision(True, "q"))
    approvals.deliver("held:r2", ApprovalDecision(True, "q"))
    await asyncio.sleep(0.01)
    assert seen[-1] == ("paused", {"run_id": 4, "cards": [
        {"kind": "held", "id": "r3", "tool": "search_papers", "call_id": "c1"}]})
    approvals.deliver("held:r3", ApprovalDecision(False))
    await task
    assert seen[-1] == ("resumed", {"run_id": 4})


async def test_an_action_outcome_reaches_the_endpoint() -> None:
    approvals.open_run(6)
    task = asyncio.create_task(approvals.wait(
        run_id=6, kind="action", row_id="a9", tracer=None))
    await asyncio.sleep(0)
    outcome = approvals.deliver("action:a9", ApprovalDecision(True))
    assert outcome is not None
    assert (await task).approved
    approvals.settle("action:a9", {"status": "done", "result": {"content": "ok"}})
    assert await outcome == {"status": "done", "result": {"content": "ok"}}
