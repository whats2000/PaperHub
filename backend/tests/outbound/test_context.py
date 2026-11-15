"""Turn-level private material for the outbound checkpoint."""
from __future__ import annotations

import contextvars
import logging

import pytest

from paperhub.outbound.context import (
    MAX_PRIVATE_CHARS,
    PrivateItem,
    add_private,
    begin_turn,
    current_private,
)


def _run(fn):  # type: ignore[no-untyped-def]
    # A fresh, empty context (nothing inherited from other tests) + a turn.
    def turn():  # type: ignore[no-untyped-def]
        begin_turn()
        return fn()

    return contextvars.Context().run(turn)


def test_empty_by_default() -> None:
    assert _run(current_private) == []


def test_private_material_cap_and_dedup() -> None:
    def body() -> list[PrivateItem]:
        add_private(PrivateItem("document", "p1", "Report", "d" * 3000))
        add_private(PrivateItem("chunk", "c1", "Report", "a" * 5000))
        add_private(PrivateItem("chunk", "c1", "Report", "dup" * 10))  # dedup
        add_private(PrivateItem("chunk", "c2", "Report", "b" * 5000))
        add_private(PrivateItem("problem", "i9", "Idea", "p" * 2000))
        return current_private()

    items = _run(body)
    assert [(i.kind, i.ref) for i in items] == [
        ("document", "p1"), ("chunk", "c1"), ("chunk", "c2"), ("problem", "i9")]
    assert sum(len(i.text) for i in items) == MAX_PRIVATE_CHARS
    # 15 000 chars total: the oldest chunk lost the 3 000 excess, nothing else did.
    assert items[1].text == "a" * 2000
    assert items[0].text == "d" * 3000 and items[2].text == "b" * 5000
    assert items[3].text == "p" * 2000


def test_cap_trims_other_kinds_after_chunks() -> None:
    def body() -> list[PrivateItem]:
        add_private(PrivateItem("chunk", "c1", "R", "a" * 1000))
        add_private(PrivateItem("document", "p1", "R", "d" * 13000))
        return current_private()

    items = _run(body)
    assert items[0].text == ""
    assert len(items[1].text) == MAX_PRIVATE_CHARS


def test_child_context_shares_the_turn_list() -> None:
    def body() -> int:
        add_private(PrivateItem("chunk", "c1", "R", "x"))
        contextvars.copy_context().run(
            add_private, PrivateItem("chunk", "c2", "R", "y"))
        return len(current_private())

    assert _run(body) == 2


def test_current_private_returns_a_copy() -> None:
    def body() -> int:
        add_private(PrivateItem("chunk", "c1", "R", "x"))
        current_private().clear()
        return len(current_private())

    assert _run(body) == 1


def test_begin_turn_starts_empty() -> None:
    def body() -> int:
        add_private(PrivateItem("chunk", "c1", "R", "x"))
        begin_turn()
        return len(current_private())

    assert _run(body) == 0


def test_add_private_without_turn_warns(caplog: pytest.LogCaptureFixture) -> None:
    def body() -> int:
        add_private(PrivateItem("chunk", "c1", "R", "x"))
        return len(current_private())

    with caplog.at_level(logging.WARNING, logger="paperhub.outbound.context"):
        assert contextvars.Context().run(body) == 1
    assert any("begin_turn" in r.getMessage() for r in caplog.records)


def test_add_private_inside_turn_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="paperhub.outbound.context"):
        _run(lambda: add_private(PrivateItem("chunk", "c1", "R", "x")))
    assert caplog.records == []
