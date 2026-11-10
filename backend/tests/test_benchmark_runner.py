"""The live benchmark runner deletes every chat session it created."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from benchmark import driver, runner
from benchmark.scorer import Grounding

CONFIG = """
[benchmark]
name = "cleanup-test"
db_path = "unused.db"

[[cases]]
id = "a"
prompt = "hello"

[[cases]]
id = "b"
prompt = "same chat as c"
session_group = "g"

[[cases]]
id = "c"
prompt = "still the same chat"
session_group = "g"

[[cases]]
id = "d"
prompt = "reuses an existing deck session"
session_id = 999
"""


class _Health:
    def raise_for_status(self) -> None:
        return None


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    cfg = tmp_path / "cases.toml"
    cfg.write_text(CONFIG, encoding="utf-8")
    state: dict[str, Any] = {"next": 100, "deleted": [], "fail_on": None}

    def create_session() -> int:
        state["next"] += 1
        return int(state["next"])

    def chat(session_id: int, prompt: str, **_: Any) -> driver.ChatResult:
        if state["fail_on"] == prompt:
            raise RuntimeError("backend dropped the stream")
        return driver.ChatResult(run_id=1, session_id=session_id, intent="chitchat")

    def delete_session(session_id: int, **_: Any) -> None:
        state["deleted"].append(session_id)

    monkeypatch.setattr(driver, "create_session", create_session)
    monkeypatch.setattr(driver, "chat", chat)
    monkeypatch.setattr(driver, "delete_session", delete_session)
    monkeypatch.setattr(runner, "grounding_for", lambda *a, **k: Grounding(run_id=1))
    monkeypatch.setattr(runner.httpx, "get", lambda *a, **k: _Health())
    state["argv"] = ["runner", "--config", str(cfg), "--out", str(tmp_path / "out")]
    return state


def test_deletes_every_created_session(harness: dict[str, Any],
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", harness["argv"])
    runner.main()
    # 101 for case a, 102 shared by b + c; the reused session 999 is not ours.
    assert sorted(harness["deleted"]) == [101, 102]


def test_deletes_sessions_when_a_case_fails(harness: dict[str, Any],
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    harness["fail_on"] = "same chat as c"
    monkeypatch.setattr(sys, "argv", harness["argv"])
    with pytest.raises(RuntimeError):
        runner.main()
    assert sorted(harness["deleted"]) == [101, 102]


def test_deletes_sessions_on_interrupt(harness: dict[str, Any],
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted(session_id: int, prompt: str, **_: Any) -> driver.ChatResult:
        raise KeyboardInterrupt

    monkeypatch.setattr(driver, "chat", interrupted)
    monkeypatch.setattr(sys, "argv", harness["argv"])
    with pytest.raises(KeyboardInterrupt):
        runner.main()
    assert harness["deleted"] == [101]


def test_one_failed_delete_does_not_skip_the_rest(harness: dict[str, Any],
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    deleted: list[int] = []

    def flaky_delete(session_id: int, **_: Any) -> None:
        if session_id == 101:
            raise RuntimeError("404")
        deleted.append(session_id)

    monkeypatch.setattr(driver, "delete_session", flaky_delete)
    monkeypatch.setattr(sys, "argv", harness["argv"])
    runner.main()
    assert deleted == [102]


def test_keep_sessions_skips_cleanup(harness: dict[str, Any],
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", [*harness["argv"], "--keep-sessions"])
    runner.main()
    assert harness["deleted"] == []
