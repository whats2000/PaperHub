from pathlib import Path

import aiosqlite
import pytest

from paperhub.db.migrate import apply_schema
from paperhub.llm import litellm_adapter as la
from paperhub.outbound import llm_destinations as ld
from paperhub.outbound import log
from paperhub.outbound.context import begin_scope, outbound_scope


async def _rows(db: Path) -> list[log.LogRow]:
    async with aiosqlite.connect(db) as c:
        return await log.query(c)


@pytest.fixture
async def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_OUTBOUND_REVIEW", "1")
    monkeypatch.setenv("PAPERHUB_PROFILE", "dev")
    ld.reset_for_tests()

    async def fake(**_k: object) -> dict[str, object]:
        return {"choices": [], "usage": None}

    monkeypatch.setattr(la.litellm, "acompletion", fake)
    async with aiosqlite.connect(tmp_path / "paperhub.db") as c:
        await apply_schema(c)
    return tmp_path / "paperhub.db"


async def test_llm_destination_logged_once_per_run(db: Path) -> None:
    tok = outbound_scope.set(None)
    try:
        begin_scope(7, 3)
        for _ in range(3):
            await la._acompletion_noted(model="gemini/gemini-2.5-pro", messages=[])
        await la._acompletion_noted(model="anthropic/claude-x", messages=[])
        await la._acompletion_noted(model="ollama/llama3", messages=[])  # local
        begin_scope(8, 3)
        await la._acompletion_noted(model="gemini/gemini-2.5-pro", messages=[])
    finally:
        outbound_scope.reset(tok)
    rows = await _rows(db)
    got = sorted((r.run_id, r.destination) for r in rows)
    assert got == [(7, "anthropic"), (7, "gemini"), (8, "gemini")]
    r = rows[0]
    assert (r.kind, r.caller, r.decision, r.stage, r.actor) == (
        "llm", "llm", "sent", "llm", "agent")
    assert r.sent_text == "(content not logged)"


async def test_no_run_logs_once_per_window_and_off_when_review_disabled(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for _ in range(3):
        await la._acompletion_noted(model="gemini/x", messages=[])
    rows = await _rows(db)
    assert len(rows) == 1 and rows[0].actor == "system" and rows[0].run_id is None
    monkeypatch.setenv("PAPERHUB_OUTBOUND_REVIEW", "0")
    await la._acompletion_noted(model="gemini/other", messages=[])
    assert len(await _rows(db)) == 1


async def test_logging_failure_does_not_fail_the_call(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_a: object, **_k: object) -> int:
        raise RuntimeError("db down")

    monkeypatch.setattr(ld.log, "append", boom)
    out = await la._acompletion_noted(model="gemini/x", messages=[])
    assert out == {"choices": [], "usage": None}


async def test_acompletion_noted_wrapper_logs_and_passes_through(db: Path) -> None:
    out = await ld.acompletion_noted(model="gemini/x", messages=[])
    assert out == {"choices": [], "usage": None}
    rows = await _rows(db)
    assert [(r.destination, r.kind) for r in rows] == [("gemini", "llm")]


async def test_destination_logged_even_when_the_call_raises(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(**_k: object) -> object:
        raise TimeoutError("slow")

    monkeypatch.setattr(la.litellm, "acompletion", boom)
    with pytest.raises(TimeoutError):
        await ld.acompletion_noted(model="gemini/x", messages=[])
    assert len(await _rows(db)) == 1


async def test_failed_append_is_retried_and_seen_runs_is_bounded(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = ld.log.append
    calls = {"n": 0}

    async def flaky(*a: object, **k: object) -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("locked")
        return await real(*a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(ld.log, "append", flaky)
    begin_scope(1, 1)
    await ld.note_llm_destination("gemini/x")  # fails, swallowed
    await ld.note_llm_destination("gemini/x")  # retried
    assert len(await _rows(db)) == 1
    monkeypatch.setattr(ld, "_MAX_SEEN", 5)
    monkeypatch.setattr(ld.log, "append", real)
    for rid in range(10, 30):
        begin_scope(rid, 1)
        await ld.note_llm_destination("gemini/x")
    assert len(ld._seen_runs) <= 5


def test_no_call_site_bypasses_the_noted_wrapper() -> None:
    root = Path(ld.__file__).resolve().parents[1]
    allowed = {"llm/litellm_adapter.py", "outbound/llm_destinations.py",
               "settings_readiness.py"}  # readiness pings: fixed "ping", unlogged
    offenders = [str(p.relative_to(root)) for p in root.rglob("*.py")
                 if p.relative_to(root).as_posix() not in allowed
                 and ("litellm.acompletion(" in p.read_text(encoding="utf-8")
                      or "= litellm.acompletion" in p.read_text(encoding="utf-8"))]
    assert offenders == []


async def test_stream_and_tool_turn_note_destination(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake(**_k: object) -> object:
        async def gen() -> object:
            yield {"choices": [{"delta": {"content": "hi"}}]}
        return gen()

    monkeypatch.setattr(la.litellm, "acompletion", fake)
    ad = la.LiteLlmAdapter(fallback_model="")
    begin_scope(40, 1)
    await ad.tool_turn(messages=[{"role": "user", "content": "x"}], tools=[],
                       model="gemini/tt")
    ad2 = la.LiteLlmAdapter(fallback_model="")
    monkeypatch.setattr(ad2, "_messages", lambda *_a, **_k: [{"role": "user", "content": "x"}])
    async for _ in ad2.stream(slot="s/v1", variables={}, model="anthropic/ss"):
        pass
    got = sorted((r.run_id, r.destination) for r in await _rows(db))
    assert got == [(40, "anthropic"), (40, "gemini")]


async def test_stream_and_tool_turn_note_destination_when_connect_raises(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A call that fails before the stream opens still contacted the
    provider: noted, like ``_acompletion_noted``."""
    async def refused(**_k: object) -> object:
        raise ValueError("bad request")  # permanent: no retry, no fallback

    monkeypatch.setattr(la.litellm, "acompletion", refused)
    begin_scope(41, 1)
    ad = la.LiteLlmAdapter(fallback_model="")
    with pytest.raises(ValueError):
        await ad.tool_turn(messages=[{"role": "user", "content": "x"}], tools=[],
                           model="gemini/tt")
    ad2 = la.LiteLlmAdapter(fallback_model="")
    monkeypatch.setattr(ad2, "_messages", lambda *_a, **_k: [{"role": "user", "content": "x"}])
    with pytest.raises(ValueError):
        async for _ in ad2.stream(slot="s/v1", variables={}, model="anthropic/ss"):
            pass
    got = sorted((r.run_id, r.destination) for r in await _rows(db))
    assert got == [(41, "anthropic"), (41, "gemini")]
