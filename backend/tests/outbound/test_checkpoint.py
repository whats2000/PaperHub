"""The outbound checkpoint: stages, decisions, log/held rows, trace step."""
from __future__ import annotations

import asyncio
import contextvars
import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio

from paperhub.db.migrate import apply_schema
from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.outbound import checkpoint
from paperhub.outbound.context import PrivateItem, add_private, private_material
from paperhub.outbound.destinations import pending_log_writes
from paperhub.outbound.models import OutboundRequest, ReviewVerdict
from paperhub.tracing.tracer import Tracer

pytestmark = pytest.mark.asyncio

_ENV = ("PAPERHUB_PROFILE", "PAPERHUB_OUTBOUND_REVIEW", "PAPERHUB_OUTBOUND_UNSURE",
        "PAPERHUB_SENSITIVE_TERMS", "PAPERHUB_OUTBOUND_ALLOWLIST",
        "PAPERHUB_OUTBOUND_REVIEW_TIMEOUT", "PAPERHUB_TRUSTED_SEARCH_URL",
        "PAPERHUB_PUBLIC_WEB_SEARCH")


class StubAdapter:
    """Returns queued verdicts (or raises / sleeps) and records each call."""

    def __init__(self, *results: Any, delay: float = 0.0) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []
        self.delay = delay

    async def structured(self, **kwargs: Any) -> ReviewVerdict:
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        r = self.results.pop(0)
        if isinstance(r, BaseException):
            raise r
        assert isinstance(r, ReviewVerdict)
        return r


def _v(action: str, reason: str = "r", rewritten: str | None = None) -> ReviewVerdict:
    return ReviewVerdict(action=action, reason=reason, rewritten=rewritten)  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[aiosqlite.Connection]:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PAPERHUB_OUTBOUND_REVIEW", "1")
    monkeypatch.setenv("PAPERHUB_OUTBOUND_REVIEW_MODEL", "ollama/review")
    async with aiosqlite.connect(tmp_path / "paperhub.db") as c:
        await apply_schema(c)
        yield c


@pytest.fixture(autouse=True)
def _fresh_private() -> Iterator[None]:
    token = private_material.set([])
    yield
    private_material.reset(token)


def _req(text: str, **kw: Any) -> OutboundRequest:
    base: dict[str, Any] = {"caller": "research.search", "kind": "s2_search",
                            "destination": "api.semanticscholar.org", "text": text}
    base.update(kw)
    return OutboundRequest(**base)


def _private() -> None:
    add_private(PrivateItem("chunk", "c7", "Wafer report", "Our yield on line 3 dropped 4%."))


async def _log(conn: aiosqlite.Connection) -> list[tuple[Any, ...]]:
    async with conn.execute(
        "SELECT actor, decision, stage, sent_text, original_text, reason, run_id "
        "FROM outbound_log ORDER BY id"
    ) as cur:
        return [tuple(r) for r in await cur.fetchall()]


async def test_disabled_sends_and_logs(conn: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERHUB_OUTBOUND_REVIEW", "0")
    monkeypatch.setenv("PAPERHUB_SENSITIVE_TERMS", "Kestrel")
    _private()
    ad = StubAdapter()
    d = await checkpoint.check(_req("Kestrel defects"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage, d.sent_text) == ("send", "disabled", "Kestrel defects")
    assert ad.calls == []
    rows = await _log(conn)
    assert rows == [("system", "sent", "disabled", "Kestrel defects", None, d.reason, None)]
    assert d.log_id is not None


@pytest.mark.parametrize("text", [
    "2401.12345", "arXiv:2401.12345v2", "10.1145/3292500.3330701",
    "649def34f8be52c8b66281af98ae884c09aef38b",
])
async def test_exact_ids_skip_review(conn: aiosqlite.Connection, text: str) -> None:
    _private()
    ad = StubAdapter()
    d = await checkpoint.check(_req(text), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage, d.sent_text) == ("send", "exact_id", text)
    assert ad.calls == []
    assert (await _log(conn))[0][1:3] == ("sent", "exact_id")


@pytest.mark.parametrize("text,blocked", [
    ("Kestrel's defects", True), ("KESTREL-v3 benchmarks", True),
    ("about kestrel", True), ("Kestrel3 results", True),
    ("KESTREL_v3 logs", True), ("Kestrels in the fab", True),
    ("Acme Corp suppliers", True), ("Acme-Corp", True), ("acme  corp", True),
    ("Acme_Corp data", True),
    ("kestrelfish habitats", False), ("Acmes Corporation", False),
])
async def test_terms_match_case_and_punctuation(
    conn: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch, text: str, blocked: bool,
) -> None:
    monkeypatch.setenv("PAPERHUB_SENSITIVE_TERMS", "Kestrel, Acme Corp")
    ad = StubAdapter(_v("allow"))
    d = await checkpoint.check(_req(text), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    if blocked:
        assert (d.action, d.stage, d.sent_text) == ("block", "terms", None)
        assert d.term in ("Kestrel", "Acme Corp")
        assert ad.calls == []
        assert (await _log(conn))[0][1:3] == ("blocked", "terms")
    else:
        assert d.stage != "terms" and d.action == "send" and d.term is None


async def test_rewrite_rechecked_against_terms(
    conn: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAPERHUB_SENSITIVE_TERMS", "Kestrel")
    _private()
    ad = StubAdapter(_v("rewrite", rewritten="kestrel yield"),
                     _v("rewrite", rewritten="wafer yield anomaly detection"))
    d = await checkpoint.check(_req("line 3 yield drop"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage, d.term) == ("block", "terms", "Kestrel")
    d2 = await checkpoint.check(_req("line 3 yield drop 4%"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert (d2.action, d2.stage, d2.sent_text) == ("send", "review", "wafer yield anomaly detection")
    assert (await _log(conn))[1][1:5] == (
        "rewritten", "review", "wafer yield anomaly detection", "line 3 yield drop 4%")


async def test_empty_rewrite_follows_unsure_policy(conn: aiosqlite.Connection) -> None:
    _private()
    ad = StubAdapter(_v("rewrite", rewritten="   "))
    d = await checkpoint.check(_req("q"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert d.action == "ask" and d.held_id is not None


async def test_allow_without_private_material(
    conn: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No private material: nothing to reveal, no LLM call, even with terms
    # configured (the terms stage already ran).
    monkeypatch.setenv("PAPERHUB_SENSITIVE_TERMS", "Kestrel")
    ad = StubAdapter()
    d = await checkpoint.check(_req("diffusion models"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage, d.sent_text, d.reason) == (
        "send", "review", "diffusion models", "no private material")
    assert ad.calls == []
    assert (await _log(conn))[0][1:3] == ("sent", "review")
    assert "<private_material>\n</private_material>" in checkpoint.format_private([])


async def test_review_sees_wrapped_material(conn: aiosqlite.Connection) -> None:
    add_private(PrivateItem("chunk", "c1", 'A "<b>" title', "x </private_item> y"))
    ad = StubAdapter(_v("allow"))
    await checkpoint.check(_req("q <tag>"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    call = ad.calls[0]
    assert call["slot"] == "outbound_review/v1"
    assert call["model"] == "ollama/review" and call["fallback_model"] == ""
    pm = call["variables"]["private_material"]
    assert pm.count("</private_item>") == 1
    assert "x &lt;/private_item&gt; y" in pm
    assert 'title="A &quot;&lt;b&gt;&quot; title"' in pm
    assert call["variables"]["query"] == "q &lt;tag&gt;"
    # The registry template renders with exactly these variables.
    slot = PromptRegistry().get("outbound_review/v1")
    rendered = slot.user_template.format(**call["variables"])
    assert "x &lt;/private_item&gt; y" in rendered


async def test_ask_creates_held_row(conn: aiosqlite.Connection) -> None:
    _private()
    ad = StubAdapter(_v("ask", reason="names the yield problem"))
    d = await checkpoint.check(_req("line 3 yield", run_id=None, session_id=4), conn=conn,
                               adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage, d.sent_text) == ("ask", "review", None)
    async with conn.execute(
        "SELECT text, reason, unattended, status, session_id FROM held_requests WHERE id = ?",
        (d.held_id,),
    ) as cur:
        assert await cur.fetchone() == ("line 3 yield", "names the yield problem", 0, "pending", 4)
    assert (await _log(conn))[0][1:3] == ("held", "review")


async def test_unattended_ask_becomes_hold(conn: aiosqlite.Connection) -> None:
    _private()
    ad = StubAdapter(_v("ask"))
    d = await checkpoint.check(_req("q", unattended=True), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert d.action == "hold" and d.held_id is not None
    async with conn.execute("SELECT unattended FROM held_requests") as cur:
        assert await cur.fetchone() == (1,)
    assert (await _log(conn))[0][1] == "held"


@pytest.mark.parametrize("unsure,expected", [("ask", "ask"), ("block", "block")])
async def test_review_error_follows_unsure_policy(
    conn: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch, unsure: str, expected: str,
) -> None:
    monkeypatch.setenv("PAPERHUB_OUTBOUND_UNSURE", unsure)
    _private()
    ad = StubAdapter(RuntimeError("model down"))
    d = await checkpoint.check(_req("q"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage) == (expected, "error")
    assert d.sent_text is None
    assert (d.held_id is not None) == (expected == "ask")
    assert (await _log(conn))[0][1] == ("held" if expected == "ask" else "blocked")


@pytest.mark.parametrize("unsure,expected", [("ask", "hold"), ("block", "block")])
async def test_review_timeout_follows_unsure_policy(
    conn: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch, unsure: str, expected: str,
) -> None:
    monkeypatch.setenv("PAPERHUB_OUTBOUND_UNSURE", unsure)
    monkeypatch.setenv("PAPERHUB_OUTBOUND_REVIEW_TIMEOUT", "0.05")
    _private()
    ad = StubAdapter(_v("allow"), delay=1.0)
    d = await checkpoint.check(_req("q", unattended=True), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage) == (expected, "timeout")


async def test_cache_hit_skips_review(conn: aiosqlite.Connection) -> None:
    _private()
    ad = StubAdapter(_v("rewrite", rewritten="wafer yield"), _v("ask"), _v("ask"))
    d1 = await checkpoint.check(_req("line 3"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    d2 = await checkpoint.check(_req("line 3"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert len(ad.calls) == 1
    assert (d2.action, d2.stage, d2.sent_text) == ("send", "cache", d1.sent_text)
    assert (await _log(conn))[1][1:3] == ("rewritten", "cache")
    # Another destination kind, or other private material, is a different key.
    await checkpoint.check(_req("line 3", kind="web_search"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    add_private(PrivateItem("chunk", "c8", "R", "more"))
    await checkpoint.check(_req("line 3"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert len(ad.calls) == 3
    # ask is never cached: only the first send row exists in the cache.
    async with conn.execute("SELECT decision_json FROM outbound_review_cache") as cur:
        rows = [json.loads(r[0]) for r in await cur.fetchall()]
    assert [r["action"] for r in rows] == ["send"]


async def test_expired_cache_is_ignored(conn: aiosqlite.Connection) -> None:
    _private()
    ad = StubAdapter(_v("allow"), _v("allow"))
    await checkpoint.check(_req("q"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    await conn.execute("UPDATE outbound_review_cache SET created_at = '2000-01-01T00:00:00+00:00'")
    await conn.commit()
    d = await checkpoint.check(_req("q"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert d.stage == "review" and len(ad.calls) == 2


async def test_trace_step_records_decision(conn: aiosqlite.Connection) -> None:
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await conn.execute("INSERT INTO runs (session_id) VALUES (1)")
    await conn.commit()
    tracer = Tracer(conn, run_id=1, branch="")
    _private()
    ad = StubAdapter(_v("rewrite", reason="names line 3", rewritten="wafer yield"))
    d = await checkpoint.check(_req("line 3 yield", run_id=1, session_id=1), conn=conn,
                               adapter=ad, tracer=tracer)  # type: ignore[arg-type]
    async with conn.execute(
        "SELECT agent, tool, model, status, args_redacted_json, result_summary_json "
        "FROM tool_calls WHERE run_id = 1"
    ) as cur:
        rows = await cur.fetchall()
    assert len(rows) == 1
    agent, tool, model, status, args_json, result_json = rows[0]
    assert (agent, tool, model, status) == ("outbound", "outbound:review", "ollama/review", "ok")
    args = json.loads(args_json)
    assert args["request"]["text"] == "line 3 yield"
    res = json.loads(result_json)
    assert res["stage"] == "review" and res["decision"] == "send"
    assert res["sent_text"] == "wafer yield" and res["reason"] == "names line 3"
    assert res["private_refs"] == ["chunk:c7"]
    assert res["log_id"] == d.log_id and res["held_id"] is None
    assert res["verdict"]["action"] == "rewrite"
    assert (await _log(conn))[0][0] == "agent"  # inside a run


async def test_allowlist_block_logs_once(
    conn: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAPERHUB_PROFILE", "deployment")
    ad = StubAdapter()
    d = await checkpoint.check(_req("q", destination="evil.example"), conn=conn,
                               adapter=ad, tracer=None)  # type: ignore[arg-type]
    await asyncio.gather(*pending_log_writes())
    rows = await _log(conn)
    assert len(rows) == 1 and rows[0][1:3] == ("blocked", "allowlist")
    # The Decision carries the id of the one row written (fix round 1).
    async with conn.execute("SELECT id FROM outbound_log") as cur:
        (row_id,) = [r[0] for r in await cur.fetchall()]
    assert (d.action, d.stage, d.log_id) == ("block", "allowlist", row_id)
    assert rows[0][4] == "q"  # the refused query is recorded like any block


async def test_private_material_isolated_per_context(conn: aiosqlite.Connection) -> None:
    # A request with no private material in its own context reviews nothing.
    _private()
    ad = StubAdapter()
    ctx = contextvars.Context()
    d = await asyncio.create_task(
        checkpoint.check(_req("q"), conn=conn, adapter=ad, tracer=None),  # type: ignore[arg-type]
        context=ctx,
    )
    assert d.action == "send" and ad.calls == []


async def test_terms_run_before_exact_id(
    conn: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAPERHUB_SENSITIVE_TERMS", "Kestrel")
    _private()
    ad = StubAdapter()
    d = await checkpoint.check(_req("10.1234/kestrel-line3-yield"), conn=conn,
                               adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage) == ("block", "terms")
    d2 = await checkpoint.check(_req("10.1145/3292500.3330701"), conn=conn,
                                adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert (d2.action, d2.stage) == ("send", "exact_id")
    assert ad.calls == []


@pytest.mark.parametrize("destination", ["web", "", "s2 api"])
async def test_unknown_destination_label_fails_closed(
    conn: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch, destination: str,
) -> None:
    monkeypatch.setenv("PAPERHUB_PROFILE", "deployment")
    ad = StubAdapter()
    d = await checkpoint.check(_req("q", destination=destination), conn=conn,
                               adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage) == ("block", "allowlist")
    await asyncio.gather(*pending_log_writes())


async def test_unknown_label_allowed_in_dev(conn: aiosqlite.Connection) -> None:
    d = await checkpoint.check(_req("q", destination="web"), conn=conn,
                               adapter=StubAdapter(), tracer=None)  # type: ignore[arg-type]
    assert d.action == "send"


async def test_review_error_reason_does_not_echo_exception(conn: aiosqlite.Connection) -> None:
    _private()
    ad = StubAdapter(ValueError("invalid output: 'Our yield on line 3 dropped 4%'"))
    d = await checkpoint.check(_req("q"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert d.stage == "error" and d.reason == "review failed (ValueError)"
    assert "line 3" not in ((await _log(conn))[0][5] or "")


async def test_cache_write_failure_keeps_decision(
    conn: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(*_a: Any, **_k: Any) -> None:
        raise aiosqlite.OperationalError("disk full")

    monkeypatch.setattr(checkpoint, "_cache_put", broken)
    _private()
    ad = StubAdapter(_v("allow"))
    d = await checkpoint.check(_req("q"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage, d.sent_text) == ("send", "review", "q")
    assert d.log_id is not None


async def test_cache_key_includes_slot_and_model() -> None:
    a = checkpoint._cache_key("s2_search", "q", ["chunk:1"], "ollama/a")
    assert a != checkpoint._cache_key("s2_search", "q", ["chunk:1"], "ollama/b")
    assert a == checkpoint._cache_key("s2_search", "q", ["chunk:1"], "ollama/a")


@pytest.mark.parametrize("unsure,expected", [("ask", "ask"), ("block", "block")])
async def test_incomplete_material_follows_unsure_policy(
    conn: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch, unsure: str, expected: str,
) -> None:
    from paperhub.outbound.context import begin_turn, mark_incomplete

    monkeypatch.setenv("PAPERHUB_OUTBOUND_UNSURE", unsure)
    monkeypatch.setenv("PAPERHUB_SENSITIVE_TERMS", "Kestrel")
    begin_turn()
    mark_incomplete("chunk collection failed (RuntimeError)")
    ad = StubAdapter(_v("allow"))
    d = await checkpoint.check(_req("diffusion models"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage, d.sent_text) == (expected, "review", None)
    assert d.reason == "private material could not be collected"
    assert ad.calls == []  # the review is skipped
    # Terms still block and exact ids still pass first.
    t = await checkpoint.check(_req("Kestrel yield"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert t.stage == "terms" and t.action == "block"
    e = await checkpoint.check(_req("2401.01234"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert (e.action, e.stage) == ("send", "exact_id")
    begin_turn()


async def test_allowlist_block_stands_when_its_log_write_fails(
    conn: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAPERHUB_PROFILE", "deployment")

    async def broken(*_a: Any, **_k: Any) -> int:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(checkpoint.log, "append", broken)
    d = await checkpoint.check(_req("q", destination="evil.example"), conn=conn,
                               adapter=StubAdapter(), tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage, d.log_id) == ("block", "allowlist", None)


_PASSAGE = "micro-cracks start at the scribe edge and propagate during the reflow step"


def _private_passage() -> None:
    add_private(PrivateItem("document", "41", "Yield review", f"Internal. {_PASSAGE}. Next step."))


async def test_span_guard_holds_a_verbatim_echo_on_allow(conn: aiosqlite.Connection) -> None:
    _private_passage()
    ad = StubAdapter(_v("allow"))
    d = await checkpoint.check(_req(f"why do {_PASSAGE.upper()}"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage, d.sent_text) == ("ask", "span", None)
    assert d.reason == "contains a passage from private material"
    assert d.held_id is not None
    rows = await _log(conn)
    assert all("scribe" not in str(r[5]) for r in rows)


async def test_span_guard_blocks_under_block_policy_and_holds_unattended(
    conn: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _private_passage()
    monkeypatch.setenv("PAPERHUB_OUTBOUND_UNSURE", "block")
    d = await checkpoint.check(_req(_PASSAGE), conn=conn, adapter=StubAdapter(_v("allow")), tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage) == ("block", "span")
    monkeypatch.setenv("PAPERHUB_OUTBOUND_UNSURE", "ask")
    d2 = await checkpoint.check(_req(_PASSAGE + " x", unattended=True), conn=conn,
                                adapter=StubAdapter(_v("allow")), tracer=None)  # type: ignore[arg-type]
    assert (d2.action, d2.stage) == ("hold", "span")


async def test_span_guard_catches_a_rewrite_that_echoes_and_does_not_cache(
    conn: aiosqlite.Connection,
) -> None:
    _private_passage()
    ad = StubAdapter(_v("rewrite", rewritten=f"{_PASSAGE} semiconductor"), _v("allow"))
    d = await checkpoint.check(_req("a query about cracks"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage) == ("ask", "span")
    d2 = await checkpoint.check(_req("a query about cracks"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert d2.stage == "review" and len(ad.calls) == 2  # the span hold was not cached


async def test_span_guard_checks_cached_sends(conn: aiosqlite.Connection) -> None:
    _private()
    ad = StubAdapter(_v("allow"))
    q = "scribe edge cracks propagate during the reflow step of dicing today"
    d = await checkpoint.check(_req(q), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert d.action == "send"
    # same ref, text now holds the query: the cached send echoes it
    # (through add_private: the span guard is built when material is added)
    private_material.set([])
    add_private(PrivateItem("chunk", "c7", "Wafer report", f"x {q} y"))
    d2 = await checkpoint.check(_req(q), conn=conn, adapter=StubAdapter(), tracer=None)  # type: ignore[arg-type]
    assert (d2.action, d2.stage) == ("ask", "span")


async def test_span_guard_seven_word_overlap_passes(conn: aiosqlite.Connection) -> None:
    _private_passage()
    seven = "micro-cracks start at the scribe edge and"[: len("micro-cracks start at the scribe edge")]
    d = await checkpoint.check(_req(f"{seven} detection"), conn=conn, adapter=StubAdapter(_v("allow")), tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage) == ("send", "review")


async def test_span_guard_cjk_12_char_echo(conn: aiosqlite.Connection) -> None:
    add_private(PrivateItem("document", "63", "報告", "社内報告：NMC811セルを四十五度で三百サイクル運転した後、容量が低下した。"))
    d = await checkpoint.check(_req("四十五度で三百サイクル運転した後 論文"), conn=conn,
                               adapter=StubAdapter(_v("allow")), tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage) == ("ask", "span")
    d2 = await checkpoint.check(_req("三百サイクル運転 論文"), conn=conn,
                                adapter=StubAdapter(_v("allow")), tracer=None)  # type: ignore[arg-type]
    assert d2.action == "send"


async def test_span_guard_is_a_noop_without_private_material(conn: aiosqlite.Connection) -> None:
    d = await checkpoint.check(_req(_PASSAGE), conn=conn, adapter=StubAdapter(), tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage) == ("send", "review") and d.reason == "no private material"


def test_windows_generator_is_shared_with_the_eval() -> None:
    from benchmark.agent import outbound_eval
    from paperhub.outbound import spans
    assert outbound_eval.windows is spans.windows


_TITLE = "Sparse Routing for Efficient Sequence Models"
_TITLE_REASON = "contains the title of a private document"


def _private_doc(title: str = _TITLE) -> None:
    add_private(PrivateItem("document", "doc:91", title, "The sparse model reaches 12.4."))


async def test_title_guard_holds_a_rewrite_that_inserts_the_private_title(
    conn: aiosqlite.Connection,
) -> None:
    """Run 912: the review dropped the figures but put the title in."""
    _private_doc()
    ad = StubAdapter(_v("rewrite", rewritten="sparse routing for efficient sequence models arxiv"))
    d = await checkpoint.check(_req("the sparse model reaches a perplexity of 12.4"),
                               conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage, d.sent_text, d.reason) == ("ask", "span", None, _TITLE_REASON)
    rows = await _log(conn)
    assert all("sparse routing" not in str(r[3]).lower() + str(r[5]).lower() for r in rows)


async def test_title_guard_blocks_under_block_policy(
    conn: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _private_doc()
    monkeypatch.setenv("PAPERHUB_OUTBOUND_UNSURE", "block")
    ad = StubAdapter(_v("rewrite", rewritten=f"{_TITLE} github"))
    d = await checkpoint.check(_req("perplexity 12.4"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage, d.reason) == ("block", "span", _TITLE_REASON)


async def test_title_guard_catches_the_query_before_review(conn: aiosqlite.Connection) -> None:
    _private_doc()
    ad = StubAdapter()
    d = await checkpoint.check(_req(f"papers like {_TITLE.upper()}"), conn=conn,
                               adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage, d.reason) == ("ask", "span", _TITLE_REASON)
    assert ad.calls == []


async def test_title_guard_checks_cached_sends(conn: aiosqlite.Connection) -> None:
    _private_doc("Draft notes")  # 2 words: not guarded, the rewrite is cached
    ad = StubAdapter(_v("rewrite", rewritten=f"{_TITLE} survey"))
    assert (await checkpoint.check(_req("q12"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
            ).action == "send"
    # Same ref, now titled: the cached rewrite carries the title.
    private_material.set([PrivateItem("document", "doc:91", _TITLE, "x")])
    d = await checkpoint.check(_req("q12"), conn=conn, adapter=StubAdapter(), tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage, d.reason) == ("ask", "span", _TITLE_REASON)


async def test_title_guard_ignores_short_titles(conn: aiosqlite.Connection) -> None:
    _private_doc("Sparse Routing")
    d = await checkpoint.check(_req("sparse routing survey"), conn=conn,
                               adapter=StubAdapter(_v("allow")), tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage) == ("send", "review")


async def test_title_guard_is_a_noop_without_private_material(conn: aiosqlite.Connection) -> None:
    d = await checkpoint.check(_req(_TITLE), conn=conn, adapter=StubAdapter(), tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage) == ("send", "review") and d.reason == "no private material"


async def _library_doc(conn: aiosqlite.Connection, title: str, *, private: bool = True) -> None:
    await conn.execute(
        "INSERT INTO paper_content (content_key, kind, sha256, title, source_path, "
        "source_dir_path, html_path, is_private) VALUES (?, 'pdf_upload', ?, ?, '', '', '', ?)",
        (f"pdf:{title}", f"sha-{title}", title, int(private)))
    await conn.commit()


async def test_title_guard_covers_private_documents_not_in_this_chat(
    conn: aiosqlite.Connection,
) -> None:
    """A private document's title is private wherever it is typed: a chat
    without that document attached still asks before sending it."""
    await _library_doc(conn, _TITLE)
    ad = StubAdapter()
    d = await checkpoint.check(_req(f"find {_TITLE} and similar work"), conn=conn,
                               adapter=ad, tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage, d.reason) == ("ask", "span", _TITLE_REASON)
    assert ad.calls == []


async def test_library_title_guard_ignores_public_and_short_titles(
    conn: aiosqlite.Connection,
) -> None:
    await _library_doc(conn, _TITLE, private=False)
    await _library_doc(conn, "Sparse Routing")
    d = await checkpoint.check(_req(f"{_TITLE} sparse routing"), conn=conn,
                               adapter=StubAdapter(), tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage, d.reason) == ("send", "review", "no private material")


async def test_library_title_guard_checks_the_reviews_rewrite(
    conn: aiosqlite.Connection,
) -> None:
    _private()  # this chat's own material sends the query to review
    await _library_doc(conn, _TITLE)
    d = await checkpoint.check(_req("routing survey"), conn=conn,
                               adapter=StubAdapter(_v("rewrite", rewritten=f"{_TITLE} survey")),
                               tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage, d.reason) == ("ask", "span", _TITLE_REASON)


async def test_cache_key_changes_with_the_review_prompt_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The slot can be edited in place on a branch: a cached decision of the
    old prompt text must not be reused."""
    a = checkpoint._cache_key("s2_search", "q", ["chunk:1"], "ollama/a")
    monkeypatch.setattr(checkpoint, "_review_prompt_sha", lambda: "other")
    assert a != checkpoint._cache_key("s2_search", "q", ["chunk:1"], "ollama/a")


async def test_title_guard_any_order_only_on_the_reviews_rewrite(
    conn: aiosqlite.Connection,
) -> None:
    _private_doc("Gated Memory Replay for Streaming Video Captioning")
    words = "video captioning gated memory replay streaming"
    # The user's query with the title words reordered: a topic, reviewed as usual.
    d = await checkpoint.check(_req(words), conn=conn,
                               adapter=StubAdapter(_v("allow")), tracer=None)  # type: ignore[arg-type]
    assert (d.action, d.stage) == ("send", "review")
    # The review's rewrite reordering the title: held.
    d2 = await checkpoint.check(_req("captioner reaches 131.7"), conn=conn,
                                adapter=StubAdapter(_v("rewrite", rewritten=words)),
                                tracer=None)  # type: ignore[arg-type]
    assert (d2.action, d2.stage, d2.reason) == ("ask", "span", _TITLE_REASON)


async def test_cache_key_includes_the_review_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    a = checkpoint._cache_key("s2_search", "q", ["chunk:1"], "ollama/a")
    monkeypatch.setattr(checkpoint, "REVIEW_SLOT", "outbound_review/v2")
    monkeypatch.setattr(checkpoint, "_review_prompt_sha", lambda: "same")
    b = checkpoint._cache_key("s2_search", "q", ["chunk:1"], "ollama/a")
    monkeypatch.setattr(checkpoint, "REVIEW_SLOT", "outbound_review/v1")
    assert b != checkpoint._cache_key("s2_search", "q", ["chunk:1"], "ollama/a")
    assert a != b


_WITHHELD = "reason withheld: it repeated private material"


@pytest.mark.parametrize("action,rewritten", [
    ("allow", None), ("rewrite", "sequence model sparsity"), ("block", None), ("ask", None)])
async def test_review_reason_naming_the_private_title_is_withheld(
    conn: aiosqlite.Connection, action: str, rewritten: str | None,
) -> None:
    """The verdict's free-text reason reaches the log, the held row and the
    card: it must not carry a private title or passage (the trace keeps it)."""
    _private_doc()
    reason = f"the query is about {_TITLE.lower()}"
    tracer_conn = conn
    await conn.execute("INSERT INTO chat_sessions (id, title) VALUES (1, 'c')")
    await conn.execute("INSERT INTO runs (id, session_id) VALUES (1, 1)")
    await conn.commit()
    tracer = Tracer(tracer_conn, run_id=1, branch="")
    d = await checkpoint.check(
        _req("perplexity 12.4"), conn=conn,
        adapter=StubAdapter(_v(action, reason=reason, rewritten=rewritten)),
        tracer=tracer)  # type: ignore[arg-type]
    assert d.reason == _WITHHELD
    for r in await _log(conn):
        assert "sparse routing" not in str(r[5]).lower()
    async with conn.execute("SELECT reason FROM held_requests") as cur:
        assert all("sparse routing" not in str(r[0]).lower() for r in await cur.fetchall())
    async with conn.execute(
        "SELECT result_summary_json FROM tool_calls WHERE tool = 'outbound:review'") as cur:
        (raw,) = await cur.fetchone()  # type: ignore[misc]
    assert json.loads(raw)["verdict"]["reason"] == reason  # R50: raw in the local trace


async def test_review_reason_with_a_passage_or_term_is_withheld(
    conn: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _private_passage()
    d = await checkpoint.check(
        _req("q1"), conn=conn,
        adapter=StubAdapter(_v("block", reason=f"it says {_PASSAGE}")), tracer=None)  # type: ignore[arg-type]
    assert d.reason == _WITHHELD
    monkeypatch.setenv("PAPERHUB_SENSITIVE_TERMS", "Kestrel")
    d2 = await checkpoint.check(
        _req("q2"), conn=conn,
        adapter=StubAdapter(_v("block", reason="mentions project kestrel")), tracer=None)  # type: ignore[arg-type]
    assert d2.reason == _WITHHELD
    d3 = await checkpoint.check(
        _req("q3"), conn=conn,
        adapter=StubAdapter(_v("block", reason="internal figures")), tracer=None)  # type: ignore[arg-type]
    assert d3.reason == "internal figures"


async def test_cached_reason_is_guarded(
    conn: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reason cached before a term was added (or stored by an older build)
    is guarded when it is reused."""
    _private()
    ad = StubAdapter(_v("allow", reason="fine, nothing about kestrel"))
    await checkpoint.check(_req("q"), conn=conn, adapter=ad, tracer=None)  # type: ignore[arg-type]
    monkeypatch.setenv("PAPERHUB_SENSITIVE_TERMS", "kestrel")
    d = await checkpoint.check(_req("q"), conn=conn, adapter=StubAdapter(), tracer=None)  # type: ignore[arg-type]
    assert (d.stage, d.reason) == ("cache", _WITHHELD)
