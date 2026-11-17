"""Turn-level private material for the outbound checkpoint."""
from __future__ import annotations

import contextvars
import logging

import pytest

from paperhub.outbound import context
from paperhub.outbound.context import (
    MAX_PRIVATE_CHARS,
    PrivateItem,
    add_private,
    begin_turn,
    current_private,
    echoes_private,
    guard_sources,
    material_incomplete,
    private_material,
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


_SECRET = "micro cracks start at the scribe edge during the reflow step"


def test_text_trimmed_for_the_review_stays_guarded() -> None:
    """The 12k cap limits only the review's text; the span guard keeps the
    windows of every item's full text."""
    def body() -> tuple[str, bool, list[tuple[str, str]]]:
        add_private(PrivateItem("document", "doc:1", "R", f"Intro. {_SECRET}."))
        add_private(PrivateItem("document", "doc:2", "R", "x " * 7000))
        first = current_private()[0].text
        return first, echoes_private(_SECRET.upper()), guard_sources(_SECRET)

    first, hit, sources = _run(body)
    assert first == ""  # trimmed out of the review's material
    assert hit and sources == [("document", "doc:1")]


def test_a_reread_after_trimming_still_guards() -> None:
    def body() -> bool:
        add_private(PrivateItem("chunk", "chunk:1", "R", _SECRET))
        add_private(PrivateItem("document", "doc:2", "R", "x " * 7000))
        add_private(PrivateItem("chunk", "chunk:1", "R", _SECRET))  # dedup
        return echoes_private(_SECRET)

    assert _run(body)


def test_seven_words_do_not_echo() -> None:
    def body() -> bool:
        add_private(PrivateItem("chunk", "chunk:1", "R", _SECRET))
        return echoes_private(" ".join(_SECRET.split()[:7]) + " papers")

    assert not _run(body)


def test_guard_ceiling_marks_the_turn_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(context, "MAX_GUARD_WINDOWS", 5)

    def body() -> tuple[str | None, str | None]:
        add_private(PrivateItem("chunk", "chunk:1", "R", "a b c d e f g h i j"))  # 3
        before = material_incomplete()
        add_private(PrivateItem("chunk", "chunk:2", "R", "k l m n o p q r s t"))  # 6 > 5
        return before, material_incomplete()

    before, after = _run(body)
    assert before is None and after == "private material too large to guard"


def test_guard_follows_the_turn_list() -> None:
    """A context that sets a fresh material list starts with a fresh guard."""
    def body() -> bool:
        add_private(PrivateItem("chunk", "chunk:1", "R", _SECRET))
        private_material.set([])
        return echoes_private(_SECRET)

    assert not _run(body)
