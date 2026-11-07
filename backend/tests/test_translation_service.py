"""TranslationService: batch, validate placeholders, escalate, cache (Plan H1)."""

import asyncio
import hashlib
import re
from typing import Any

import aiosqlite
import pytest

from paperhub.agents.memory_recall import build_active_memory_block
from paperhub.db.connection import write_transaction
from paperhub.translation.cache import get_cached, put_cached
from paperhub.translation.models import (
    TranslateBlock,
    TranslatedBlocks,
    TranslatedItem,
    TranslateRequest,
)
from paperhub.translation.placeholders import source_hash
from paperhub.translation.service import TranslationService

SMALL = "small-model"
FLAG = "flagship-model"
_BLOCK_RE = re.compile(r'<block key="([^"]+)">(.*?)</block>', re.S)


class StubLlm:
    """Echo-translates each block as ``T(<text>)`` unless a hook overrides."""

    def __init__(self, override: Any = None, delay: float = 0.0) -> None:
        self.calls: list[dict[str, Any]] = []
        self.override = override
        self.delay = delay
        self.in_flight = 0
        self.max_in_flight = 0

    async def structured(self, *, slot: str, variables: dict[str, Any],
                         response_model: type, model: str, **_: Any) -> Any:
        self.calls.append({"slot": slot, "variables": variables, "model": model})
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            blocks = [(k, t) for k, t in _BLOCK_RE.findall(variables["blocks_xml"])]
            items = []
            for key, text in blocks:
                out = self.override(key, text, model) if self.override else f"T({text})"
                if out is not None:
                    items.append(TranslatedItem(key=key, text=out))
            assert response_model is TranslatedBlocks
            return TranslatedBlocks(items=items)
        finally:
            self.in_flight -= 1

    def stream(self, **_: Any) -> Any:  # pragma: no cover — unused
        raise NotImplementedError


async def _seed(conn: aiosqlite.Connection) -> int:
    cur = await conn.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, source_path,"
        " source_dir_path, html_path) VALUES ('k','arxiv','1.1','T','s','d','h')"
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


async def _memory_hash(conn: aiosqlite.Connection) -> str:
    block = await build_active_memory_block(conn, session_id=None)
    return hashlib.sha256(block.encode("utf-8")).hexdigest()


async def _put(conn: aiosqlite.Connection, *args: Any) -> None:
    async with write_transaction(conn):
        await put_cached(conn, *args)


def _svc(llm: StubLlm) -> TranslationService:
    return TranslationService(llm, model=SMALL, flagship_model=FLAG)


def _req(pid: int, *blocks: tuple[str, str], language: str = "zh-TW",
         force: bool = False) -> TranslateRequest:
    return TranslateRequest(
        paper_content_id=pid, language=language, force=force,
        blocks=[TranslateBlock(key=k, text=t) for k, t in blocks],
    )


@pytest.mark.asyncio
async def test_cache_hit_skips_llm(migrated_db: aiosqlite.Connection) -> None:
    pid = await _seed(migrated_db)
    mh = await _memory_hash(migrated_db)
    await _put(migrated_db, pid, "h:0", "zh-TW", source_hash("Hello ⟦0⟧"),
                     mh, "你好 ⟦0⟧", SMALL)
    llm = StubLlm()
    resp = await _svc(llm).translate(migrated_db, _req(pid, ("h:0", "Hello ⟦0⟧")))
    assert llm.calls == []
    [r] = resp.results
    assert (r.key, r.status, r.text, r.model, r.stale_terminology) == (
        "h:0", "ok", "你好 ⟦0⟧", SMALL, False,
    )


@pytest.mark.asyncio
async def test_misses_go_in_one_batch_with_preceding_context(
    migrated_db: aiosqlite.Connection,
) -> None:
    pid = await _seed(migrated_db)
    mh = await _memory_hash(migrated_db)
    await _put(migrated_db, pid, "h:4", "zh-TW", "other", mh, "前一段", SMALL)
    llm = StubLlm()
    resp = await _svc(llm).translate(
        migrated_db, _req(pid, ("h:5", "a <b> & c"), ("h:6", "second")),
    )
    assert len(llm.calls) == 1
    v = llm.calls[0]["variables"]
    assert llm.calls[0]["slot"] == "translate_blocks/v1"
    assert llm.calls[0]["model"] == SMALL
    assert v["context"] == "前一段"
    assert v["language"] == "繁體中文"  # "zh-TW" is a known alias
    assert '<block key="h:5">a &lt;b&gt; &amp; c</block>' in v["blocks_xml"]
    assert [r.status for r in resp.results] == ["ok", "ok"]
    cached = await get_cached(migrated_db, pid, ["h:5", "h:6"], "zh-TW")
    assert set(cached) == {"h:5", "h:6"}
    assert cached["h:5"].source_hash == source_hash("a <b> & c")


@pytest.mark.asyncio
async def test_dropped_placeholder_escalates_to_flagship(
    migrated_db: aiosqlite.Connection,
) -> None:
    pid = await _seed(migrated_db)

    def override(key: str, text: str, model: str) -> str:
        return "x" if model == SMALL else "好 ⟦0⟧"

    llm = StubLlm(override)
    resp = await _svc(llm).translate(migrated_db, _req(pid, ("h:0", "Good ⟦0⟧")))
    assert [c["model"] for c in llm.calls] == [SMALL, SMALL, FLAG]
    [r] = resp.results
    assert (r.status, r.text, r.model) == ("ok", "好 ⟦0⟧", FLAG)
    cached = await get_cached(migrated_db, pid, ["h:0"], "zh-TW")
    assert cached["h:0"].model == FLAG


@pytest.mark.asyncio
async def test_br_moved_to_an_edge_escalates_like_a_placeholder_failure(
    migrated_db: aiosqlite.Connection,
) -> None:
    pid = await _seed(migrated_db)

    def override(key: str, text: str, model: str) -> str:
        # Small parks the break at the end; the flagship keeps it mid-paragraph.
        return "前半。後半。⟦BR⟧" if model == SMALL else "前半。⟦BR⟧後半。"

    llm = StubLlm(override)
    resp = await _svc(llm).translate(
        migrated_db, _req(pid, ("p:/page/0/Text/1", "First half.⟦BR⟧Second half.")),
    )
    assert [c["model"] for c in llm.calls] == [SMALL, SMALL, FLAG]
    [r] = resp.results
    assert (r.status, r.text, r.model) == ("ok", "前半。⟦BR⟧後半。", FLAG)


@pytest.mark.asyncio
async def test_all_attempts_fail_returns_unavailable_and_does_not_cache(
    migrated_db: aiosqlite.Connection, caplog: pytest.LogCaptureFixture,
) -> None:
    pid = await _seed(migrated_db)

    def override(key: str, text: str, model: str) -> str | None:
        if key == "h:1":
            return None  # missing from every reply
        return f"T({text})"

    llm = StubLlm(override)
    with caplog.at_level("WARNING", logger="paperhub.translation.service"):
        resp = await _svc(llm).translate(
            migrated_db, _req(pid, ("h:0", "ok ⟦0⟧"), ("h:1", "bad")),
        )
    by_key = {r.key: r for r in resp.results}
    assert by_key["h:0"].status == "ok"
    assert (by_key["h:1"].status, by_key["h:1"].text, by_key["h:1"].model) == (
        "unavailable", None, None,
    )
    assert [c["model"] for c in llm.calls] == [SMALL, SMALL, FLAG]
    cached = await get_cached(migrated_db, pid, ["h:0", "h:1"], "zh-TW")
    assert set(cached) == {"h:0"}
    msg = "\n".join(r.getMessage() for r in caplog.records)
    assert f"paper={pid}" in msg and "h:1" in msg and "繁體中文" in msg


@pytest.mark.asyncio
async def test_provider_error_escalates_and_is_logged(
    migrated_db: aiosqlite.Connection, caplog: pytest.LogCaptureFixture,
) -> None:
    pid = await _seed(migrated_db)

    def override(key: str, text: str, model: str) -> str:
        raise RuntimeError("provider 503")

    llm = StubLlm(override)
    with caplog.at_level("WARNING", logger="paperhub.translation.service"):
        resp = await _svc(llm).translate(migrated_db, _req(pid, ("h:0", "hi")))
    assert resp.results[0].status == "unavailable"
    assert [c["model"] for c in llm.calls] == [SMALL, FLAG]
    assert "provider 503" in caplog.text


@pytest.mark.asyncio
async def test_batch_provider_error_goes_straight_to_one_flagship_call(
    migrated_db: aiosqlite.Connection,
) -> None:
    pid = await _seed(migrated_db)

    def override(key: str, text: str, model: str) -> str | None:
        if model == SMALL:
            raise RuntimeError("small down")
        return None if key == "h:2" else f"F({text})"

    llm = StubLlm(override)
    resp = await _svc(llm).translate(
        migrated_db, _req(pid, ("h:0", "a"), ("h:1", "b"), ("h:2", "c")),
    )
    assert [c["model"] for c in llm.calls] == [SMALL, FLAG]
    assert [(r.status, r.model) for r in resp.results] == [
        ("ok", FLAG), ("ok", FLAG), ("unavailable", None),
    ]


@pytest.mark.asyncio
async def test_small_retry_error_skips_small_for_remaining_blocks(
    migrated_db: aiosqlite.Connection,
) -> None:
    pid = await _seed(migrated_db)

    def override(key: str, text: str, model: str) -> str | None:
        if model == FLAG:
            return f"F({text})"
        n = len(llm.calls[-1]["variables"]["blocks_xml"].splitlines())
        if n > 1:
            return None  # content failure: dropped from the batch reply
        raise RuntimeError("small down")

    llm = StubLlm(override)
    resp = await _svc(llm).translate(migrated_db, _req(pid, ("h:0", "a"), ("h:1", "b")))
    assert [c["model"] for c in llm.calls] == [SMALL, SMALL, FLAG, FLAG]
    assert [r.status for r in resp.results] == ["ok", "ok"]


@pytest.mark.asyncio
async def test_reply_entities_are_unescaped_and_block_tag_round_trips(
    migrated_db: aiosqlite.Connection,
) -> None:
    pid = await _seed(migrated_db)

    def override(key: str, text: str, model: str) -> str:
        return "A &amp; B &lt;x&gt;" if key == "h:0" else text

    llm = StubLlm(override)
    resp = await _svc(llm).translate(
        migrated_db, _req(pid, ("h:0", "A & B <x>"), ("h:1", "see </block> here")),
    )
    xml = llm.calls[0]["variables"]["blocks_xml"]
    assert xml.count("</block>") == 2 and "&lt;/block&gt;" in xml
    assert [r.text for r in resp.results] == ["A & B <x>", "see </block> here"]
    cached = await get_cached(migrated_db, pid, ["h:0", "h:1"], "zh-TW")
    assert cached["h:0"].text == "A & B <x>"
    assert cached["h:1"].text == "see </block> here"


@pytest.mark.asyncio
async def test_failed_force_keeps_cached_text_marked_stale(
    migrated_db: aiosqlite.Connection,
) -> None:
    pid = await _seed(migrated_db)
    await _put(migrated_db, pid, "h:0", "zh-TW", source_hash("token ⟦0⟧"),
                     "old-memory-hash", "符記 ⟦0⟧", SMALL)
    llm = StubLlm(lambda k, t, m: "broken")
    resp = await _svc(llm).translate(
        migrated_db, _req(pid, ("h:0", "token ⟦0⟧"), force=True),
    )
    [r] = resp.results
    assert (r.status, r.text, r.model, r.stale_terminology) == (
        "ok", "符記 ⟦0⟧", SMALL, True,
    )
    cached = await get_cached(migrated_db, pid, ["h:0"], "zh-TW")
    assert cached["h:0"].memory_hash == "old-memory-hash"


@pytest.mark.asyncio
async def test_concurrent_requests_for_one_block_share_one_call(
    migrated_db: aiosqlite.Connection,
) -> None:
    pid = await _seed(migrated_db)
    llm = StubLlm(delay=0.05)
    svc = _svc(llm)
    a, b = await asyncio.gather(
        svc.translate(migrated_db, _req(pid, ("h:3", "same"))),
        svc.translate(migrated_db, _req(pid, ("h:3", "same"))),
    )
    assert len(llm.calls) == 1
    assert a.results[0].text == b.results[0].text == "T(same)"
    assert svc._inflight == {}


@pytest.mark.asyncio
async def test_pdf_key_gets_no_context(migrated_db: aiosqlite.Connection) -> None:
    pid = await _seed(migrated_db)
    mh = await _memory_hash(migrated_db)
    await _put(migrated_db, pid, "p:/page/0/Text/1", "zh-TW", "x", mh, "前", SMALL)
    llm = StubLlm()
    await _svc(llm).translate(migrated_db, _req(pid, ("p:/page/0/Text/2", "next")))
    assert llm.calls[0]["variables"]["context"] == ""


@pytest.mark.asyncio
async def test_terminology_change_marks_stale_not_retranslated(
    migrated_db: aiosqlite.Connection,
) -> None:
    pid = await _seed(migrated_db)
    await _put(migrated_db, pid, "h:0", "zh-TW", source_hash("token"),
                     "old-memory-hash", "符記", SMALL)
    llm = StubLlm()
    resp = await _svc(llm).translate(migrated_db, _req(pid, ("h:0", "token")))
    assert llm.calls == []
    [r] = resp.results
    assert (r.status, r.text, r.stale_terminology) == ("ok", "符記", True)


@pytest.mark.asyncio
async def test_force_retranslates_and_clears_stale(
    migrated_db: aiosqlite.Connection,
) -> None:
    pid = await _seed(migrated_db)
    await migrated_db.execute(
        "INSERT INTO memories (scope, content, status) VALUES "
        "('global', 'Translate token as 詞元', 'active')"
    )
    await migrated_db.commit()
    mh = await _memory_hash(migrated_db)
    await _put(migrated_db, pid, "h:0", "zh-TW", source_hash("token"),
                     "old-memory-hash", "符記", SMALL)
    llm = StubLlm(lambda k, t, m: "詞元")
    resp = await _svc(llm).translate(migrated_db, _req(pid, ("h:0", "token"), force=True))
    assert len(llm.calls) == 1
    assert "詞元" in llm.calls[0]["variables"]["terminology"]
    [r] = resp.results
    assert (r.text, r.stale_terminology) == ("詞元", False)
    cached = await get_cached(migrated_db, pid, ["h:0"], "zh-TW")
    assert (cached["h:0"].text, cached["h:0"].memory_hash) == ("詞元", mh)


@pytest.mark.asyncio
async def test_language_whitespace_shares_cache(
    migrated_db: aiosqlite.Connection,
) -> None:
    pid = await _seed(migrated_db)
    llm = StubLlm()
    svc = _svc(llm)
    await svc.translate(migrated_db, _req(pid, ("h:0", "hi"),
                                          language="  繁體中文（香港） "))
    assert llm.calls[0]["variables"]["language"] == "繁體中文（香港）"
    resp = await svc.translate(migrated_db, _req(pid, ("h:0", "hi"),
                                                 language="繁體中文（香港）"))
    assert len(llm.calls) == 1
    assert resp.results[0].text == "T(hi)"


@pytest.mark.asyncio
async def test_source_change_is_a_miss(migrated_db: aiosqlite.Connection) -> None:
    pid = await _seed(migrated_db)
    mh = await _memory_hash(migrated_db)
    await _put(migrated_db, pid, "h:0", "zh-TW", source_hash("old"), mh, "舊", SMALL)
    llm = StubLlm()
    resp = await _svc(llm).translate(migrated_db, _req(pid, ("h:0", "new")))
    assert len(llm.calls) == 1
    assert resp.results[0].text == "T(new)"


@pytest.mark.asyncio
async def test_per_paper_concurrency_capped_at_two(
    migrated_db: aiosqlite.Connection,
) -> None:
    pid = await _seed(migrated_db)
    llm = StubLlm(delay=0.05)
    svc = _svc(llm)
    await asyncio.gather(*(
        svc.translate(migrated_db, _req(pid, (f"h:{i * 10}", f"text {i}")))
        for i in range(4)
    ))
    assert len(llm.calls) == 4
    assert llm.max_in_flight <= 2
    assert llm.max_in_flight == 2


@pytest.mark.asyncio
async def test_cache_writes_share_one_write_transaction(
    migrated_db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All of one request's cache writes go through a single write_transaction
    (the process-wide write lock), not bare per-row commits."""
    import paperhub.translation.service as service_mod

    entered: list[int] = []
    real = service_mod.write_transaction

    def spy(conn: aiosqlite.Connection) -> Any:
        entered.append(1)
        return real(conn)

    monkeypatch.setattr(service_mod, "write_transaction", spy)
    pid = await _seed(migrated_db)
    resp = await _svc(StubLlm()).translate(
        migrated_db, _req(pid, ("h:0", "one"), ("h:1", "two")),
    )
    assert [r.status for r in resp.results] == ["ok", "ok"]
    assert entered == [1]
    assert not migrated_db.in_transaction
    assert set(await get_cached(migrated_db, pid, ["h:0", "h:1"], "zh-TW")) == {
        "h:0", "h:1",
    }


@pytest.mark.asyncio
async def test_failed_cache_write_still_returns_translations(
    migrated_db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import paperhub.translation.service as service_mod

    async def boom(*_: Any) -> None:
        raise aiosqlite.OperationalError("database is locked")

    monkeypatch.setattr(service_mod, "put_cached", boom)
    pid = await _seed(migrated_db)
    with caplog.at_level("WARNING", logger="paperhub.translation.service"):
        resp = await _svc(StubLlm()).translate(migrated_db, _req(pid, ("h:0", "one")))
    [r] = resp.results
    assert (r.status, r.text) == ("ok", "T(one)")
    assert any("cache write failed" in m for m in caplog.messages)
    assert not migrated_db.in_transaction


PROSE = ("With the concept of photon introduced by Albert Einstein in 1905, the "
         "statistical description of light is necessary for the study of ⟦0⟧.")
PROSE_ZH = "隨著愛因斯坦於 1905 年提出光子的概念，光的統計描述對於研究 ⟦0⟧ 是必要的。"


@pytest.mark.asyncio
async def test_echo_in_a_batch_is_retried_alone_and_never_cached(
    migrated_db: aiosqlite.Connection, caplog: pytest.LogCaptureFixture,
) -> None:
    """QA B1: a block the model sent back in English inside a batch passed the
    placeholder check and was cached as ``ok``. It must be retried alone."""
    pid = await _seed(migrated_db)
    calls: list[int] = []

    def override(key: str, text: str, model: str) -> str:
        calls.append(1)
        if key == "p:/page/0/Text/11" and len(calls) <= 2:
            return text  # echoed inside the batch
        return PROSE_ZH if key == "p:/page/0/Text/11" else f"譯({text})"

    llm = StubLlm(override)
    with caplog.at_level("WARNING", logger="paperhub.translation.service"):
        resp = await _svc(llm).translate(migrated_db, _req(
            pid, ("p:/page/0/Text/9", "Keywords ⟦0⟧"), ("p:/page/0/Text/11", PROSE),
        ))
    by_key = {r.key: r for r in resp.results}
    assert by_key["p:/page/0/Text/11"].status == "ok"
    assert by_key["p:/page/0/Text/11"].text == PROSE_ZH
    assert [c["model"] for c in llm.calls] == [SMALL, SMALL]
    assert "echo" in caplog.text and "p:/page/0/Text/11" in caplog.text
    cached = await get_cached(migrated_db, pid, ["p:/page/0/Text/11"], "zh-TW")
    assert cached["p:/page/0/Text/11"].text == PROSE_ZH


@pytest.mark.asyncio
async def test_persistent_echo_ends_unavailable_and_is_not_cached(
    migrated_db: aiosqlite.Connection,
) -> None:
    pid = await _seed(migrated_db)
    llm = StubLlm(lambda key, text, model: text)
    resp = await _svc(llm).translate(migrated_db, _req(pid, ("h:3", PROSE)))
    assert resp.results[0].status == "unavailable"
    assert [c["model"] for c in llm.calls] == [SMALL, SMALL, FLAG]
    assert await get_cached(migrated_db, pid, ["h:3"], "zh-TW") == {}


@pytest.mark.asyncio
async def test_english_target_keeps_an_english_copy(
    migrated_db: aiosqlite.Connection,
) -> None:
    pid = await _seed(migrated_db)
    llm = StubLlm(lambda key, text, model: text)
    resp = await _svc(llm).translate(migrated_db, _req(pid, ("h:3", PROSE),
                                                        language="English"))
    assert resp.results[0].status == "ok"
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_cached_echo_is_treated_as_a_miss(migrated_db: aiosqlite.Connection) -> None:
    pid = await _seed(migrated_db)
    mh = await _memory_hash(migrated_db)
    await _put(migrated_db, pid, "h:3", "繁體中文", source_hash(PROSE), mh, PROSE, SMALL)
    llm = StubLlm(lambda key, text, model: PROSE_ZH)
    resp = await _svc(llm).translate(migrated_db, _req(pid, ("h:3", PROSE)))
    assert resp.results[0].text == PROSE_ZH
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_fullwidth_spacing_is_tidied_before_caching(
    migrated_db: aiosqlite.Connection,
) -> None:
    pid = await _seed(migrated_db)
    llm = StubLlm(lambda key, text, model: "任務（ 例如 ⟦0⟧ ）。")
    resp = await _svc(llm).translate(migrated_db, _req(pid, ("h:0", "tasks (e.g. ⟦0⟧).")))
    assert resp.results[0].text == "任務（例如 ⟦0⟧）。"
    cached = await get_cached(migrated_db, pid, ["h:0"], "zh-TW")
    assert cached["h:0"].text == "任務（例如 ⟦0⟧）。"


@pytest.mark.asyncio
async def test_cached_rows_are_served_tidied(migrated_db: aiosqlite.Connection) -> None:
    pid = await _seed(migrated_db)
    mh = await _memory_hash(migrated_db)
    await _put(migrated_db, pid, "h:0", "zh-TW", source_hash("x"), mh, "甲（ 乙 ）", SMALL)
    resp = await _svc(StubLlm()).translate(migrated_db, _req(pid, ("h:0", "x")))
    assert resp.results[0].text == "甲（乙）"


@pytest.mark.asyncio
async def test_language_aliases_share_one_cache(migrated_db: aiosqlite.Connection) -> None:
    pid = await _seed(migrated_db)
    llm = StubLlm(lambda key, text, model: "こんにちは")
    svc = _svc(llm)
    await svc.translate(migrated_db, _req(pid, ("h:0", "hi"), language="Japanese"))
    await svc.translate(migrated_db, _req(pid, ("h:0", "hi"), language="日本語"))
    assert len(llm.calls) == 1
    assert llm.calls[0]["variables"]["language"] == "日本語"


@pytest.mark.asyncio
async def test_request_time_is_bounded(migrated_db: aiosqlite.Connection) -> None:
    """QA nitpick: one block held a call for 117 s through primary, retry and
    flagship. The whole request has one time budget."""
    pid = await _seed(migrated_db)
    llm = StubLlm(delay=0.3)
    svc = TranslationService(llm, model=SMALL, flagship_model=FLAG, budget_s=0.5)
    loop = asyncio.get_running_loop()
    start = loop.time()
    resp = await svc.translate(migrated_db, _req(pid, ("h:0", "x"), ("h:1", "y")))
    # The first call (0.3 s) succeeds; nothing waits past the budget.
    assert loop.time() - start < 0.8
    assert [r.status for r in resp.results] == ["ok", "ok"]

    slow = StubLlm(delay=5)
    svc = TranslationService(slow, model=SMALL, flagship_model=FLAG, budget_s=0.3)
    start = loop.time()
    resp = await svc.translate(migrated_db, _req(pid, ("h:5", "z")))
    assert loop.time() - start < 1.0
    assert resp.results[0].status == "unavailable"
