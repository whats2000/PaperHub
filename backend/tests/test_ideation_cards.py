"""Plan H3 Task 3: cited mechanism cards built from the cached PaperDigest."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from paperhub.config import load_settings
from paperhub.ideation import cards
from paperhub.ideation.cards import CARD_SLOT, CardDraft, build_card, ensure_cards
from paperhub.ideation.models import CitedField, MechanismCard
from paperhub.models.slide_domain import DigestSection, PaperDigest
from paperhub.tracing.tracer import Tracer


class _StubAdapter:
    def __init__(self, card: MechanismCard) -> None:
        self.card = card
        self.calls: list[dict[str, Any]] = []

    async def structured(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.card

    def stream(self, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


def _write_digest(source_dir: Path, paper_id: int, insight: str = "uses anchors") -> None:
    digest = PaperDigest(
        paper_id=paper_id, title="Faster R-CNN", abstract="Region proposals.",
        sections=[
            DigestSection(name="1 Introduction", insight="detection is slow"),
            DigestSection(name="3.1 Region Proposal Networks", insight=insight),
        ],
        figures=[],
    )
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "digest.json").write_text(digest.model_dump_json(), encoding="utf-8")


async def _doc(conn: aiosqlite.Connection, source_dir: Path) -> int:
    cur = await conn.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, "
        "source_path, source_dir_path, html_path) "
        "VALUES ('arxiv:1506.01497', 'arxiv', '1506.01497', 'Faster R-CNN', '', ?, '')",
        (str(source_dir),),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def _card() -> MechanismCard:
    return MechanismCard(
        problem=CitedField(text="Candidate generation is slow.",
                           sections=["1 introduction", "9 Made Up"]),
        mechanism=CitedField(text="Score a fixed set of reference shapes per location.",
                             sections=["3.1 Region Proposal Networks"]),
        assumptions=[CitedField(text="Objects fit a few shapes.", sections=["Nope"])],
        limitations=[],
        domains=["Computer Vision"],
    )


async def test_unknown_sections_dropped_and_traced(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    src = tmp_path / "paper"
    pid = await _doc(migrated_db, src)
    _write_digest(src, pid)
    adapter = _StubAdapter(_card())

    card = await build_card(migrated_db, pid, adapter=adapter, model="m",
                            tracer=fake_tracer)

    # Spacing/case-tolerant match maps onto the canonical digest name.
    assert card.problem.sections == ["1 Introduction"]
    assert card.mechanism.sections == ["3.1 Region Proposal Networks"]
    assert card.assumptions[0].sections == []
    call = adapter.calls[0]
    assert call["slot"] == CARD_SLOT
    assert call["model"] == "m"
    # Required-field schema: an optional field is omitted by native structured output.
    assert call["response_model"] is CardDraft
    assert '<section name="3.1 Region Proposal Networks">' in call["variables"]["sections_xml"]
    assert call["variables"]["title"] == "Faster R-CNN"
    async with migrated_db.execute(
        "SELECT tool, args_redacted_json, result_summary_json FROM tool_calls"
    ) as cur:
        rows = list(await cur.fetchall())
    assert [r[0] for r in rows] == ["ideation:card"]
    args = json.loads(rows[0][1])
    result = json.loads(rows[0][2])
    assert args["paper_content_id"] == pid
    assert args["digest_sections"] == ["1 Introduction", "3.1 Region Proposal Networks"]
    assert result["card"]["mechanism"]["sections"] == ["3.1 Region Proposal Networks"]
    assert sorted(result["dropped_sections"]) == ["9 Made Up", "Nope"]


async def test_cache_hit_skips_llm(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    src = tmp_path / "paper"
    pid = await _doc(migrated_db, src)
    _write_digest(src, pid)
    adapter = _StubAdapter(_card())
    first = await build_card(migrated_db, pid, adapter=adapter, model="m",
                             tracer=fake_tracer)
    second = await build_card(migrated_db, pid, adapter=adapter, model="m",
                              tracer=fake_tracer)
    assert len(adapter.calls) == 1
    assert second == first


async def test_changed_digest_rebuilds(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    src = tmp_path / "paper"
    pid = await _doc(migrated_db, src)
    _write_digest(src, pid)
    adapter = _StubAdapter(_card())
    await build_card(migrated_db, pid, adapter=adapter, model="m", tracer=fake_tracer)
    async with migrated_db.execute(
        "SELECT digest_hash FROM mechanism_cards WHERE paper_content_id = ?", (pid,),
    ) as cur:
        h1 = (await cur.fetchone() or [None])[0]

    _write_digest(src, pid, insight="a different insight")
    await build_card(migrated_db, pid, adapter=adapter, model="m", tracer=fake_tracer)
    assert len(adapter.calls) == 2
    async with migrated_db.execute(
        "SELECT digest_hash, model FROM mechanism_cards WHERE paper_content_id = ?",
        (pid,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] != h1 and row[1] == "m"


async def test_build_assigns_domains_keeping_manual(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    src = tmp_path / "paper"
    pid = await _doc(migrated_db, src)
    _write_digest(src, pid)
    await build_card(migrated_db, pid, adapter=_StubAdapter(_card()), model="m",
                     tracer=fake_tracer)
    async with migrated_db.execute(
        "SELECT d.name, dd.manual FROM document_domains dd "
        "JOIN domains d ON d.id = dd.domain_id WHERE dd.paper_content_id = ?", (pid,),
    ) as cur:
        assert list(await cur.fetchall()) == [("Computer Vision", 0)]


# --- fix round 1 -----------------------------------------------------------


async def _doc_n(conn: aiosqlite.Connection, root: Path, n: int, *, digest: bool = True) -> int:
    src = root / f"paper{n}"
    cur = await conn.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, "
        "source_path, source_dir_path, html_path) VALUES (?, 'arxiv', ?, ?, '', ?, '')",
        (f"arxiv:{n}", f"{n}", f"T{n}", str(src)),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    if digest:
        _write_digest(src, cur.lastrowid)
    else:
        src.mkdir(parents=True, exist_ok=True)
    return cur.lastrowid


async def _count(conn: aiosqlite.Connection, sql: str, *args: Any) -> int:
    async with conn.execute(sql, args) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row is not None else 0


async def test_tagging_failure_leaves_no_cached_card(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = await _doc_n(migrated_db, tmp_path, 1)

    async def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("tagging failed")

    monkeypatch.setattr(cards, "assign_domains_tx", boom)
    with pytest.raises(RuntimeError):
        await build_card(migrated_db, pid, adapter=_StubAdapter(_card()), model="m",
                         tracer=fake_tracer)
    assert await _count(migrated_db, "SELECT COUNT(*) FROM mechanism_cards") == 0


async def test_cache_hit_retags_an_untagged_document(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    pid = await _doc_n(migrated_db, tmp_path, 1)
    adapter = _StubAdapter(_card())
    await build_card(migrated_db, pid, adapter=adapter, model="m", tracer=fake_tracer)
    await migrated_db.execute("DELETE FROM document_domains")
    await migrated_db.commit()
    await build_card(migrated_db, pid, adapter=adapter, model="m", tracer=fake_tracer)
    assert len(adapter.calls) == 1
    assert await _count(migrated_db, "SELECT COUNT(*) FROM document_domains "
                        "WHERE paper_content_id = ?", pid) == 1


async def test_interleaved_builds_share_one_new_domain(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    a = await _doc_n(migrated_db, tmp_path, 1)
    b = await _doc_n(migrated_db, tmp_path, 2)
    adapter = _StubAdapter(_card())
    await asyncio.gather(
        build_card(migrated_db, a, adapter=adapter, model="m", tracer=fake_tracer),
        build_card(migrated_db, b, adapter=adapter, model="m", tracer=fake_tracer),
    )
    assert await _count(migrated_db, "SELECT COUNT(*) FROM domains") == 1
    assert await _count(migrated_db, "SELECT COUNT(*) FROM document_domains") == 2


async def test_ensure_cards_skips_a_document_without_asset(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    good = await _doc_n(migrated_db, tmp_path, 1)
    bare = await _doc_n(migrated_db, tmp_path, 2, digest=False)
    adapter = _StubAdapter(_card())
    out = await ensure_cards(migrated_db, [good, bare, good], adapter=adapter,
                             model="m", tracer=fake_tracer)
    assert set(out) == {good}
    async with migrated_db.execute(
        "SELECT status, args_redacted_json FROM tool_calls WHERE tool = 'ideation:digest'"
    ) as cur:
        rows = list(await cur.fetchall())
    assert len(rows) == 1 and rows[0][0] == "error"
    assert json.loads(rows[0][1])["paper_content_id"] == bare
    assert not (tmp_path / "paper2" / "digest.json").exists()


async def test_ensure_cards_bounds_concurrency(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    ids = [await _doc_n(migrated_db, tmp_path, n) for n in range(1, 6)]
    state = {"now": 0, "peak": 0}

    class _Slow(_StubAdapter):
        async def structured(self, **kwargs: Any) -> Any:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
            await asyncio.sleep(0.02)
            state["now"] -= 1
            return await super().structured(**kwargs)

    out = await ensure_cards(migrated_db, ids, adapter=_Slow(_card()), model="m",
                             tracer=fake_tracer, concurrency=2)
    assert set(out) == set(ids)
    assert state["peak"] == 2


def test_card_draft_caps_domains() -> None:
    draft = CardDraft.model_validate({
        "problem": {"text": "p", "sections": []}, "mechanism": {"text": "m", "sections": []},
        "assumptions": [], "limitations": [], "domains": ["A", "B", "C", "D"],
    })
    assert draft.domains == ["A", "B", "C"]


def test_card_variables_escape_title_and_abstract() -> None:
    d = PaperDigest(paper_id=1, title="A <b> & C", abstract="x < y", sections=[],
                    figures=[])
    v = cards.card_variables(d)
    assert v["title"] == "A &lt;b&gt; &amp; C" and v["abstract"] == "x &lt; y"


@pytest.mark.parametrize("override", [None, "openai/some-model"])
def test_digest_model_matches_settings(
    override: str | None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    if override is None:
        monkeypatch.delenv("PAPERHUB_REPORT_SECTION_MODEL", raising=False)
    else:
        monkeypatch.setenv("PAPERHUB_REPORT_SECTION_MODEL", override)
    assert cards._digest_model() == load_settings().report_section_model


async def test_ensure_cards_cancelled_waits_for_every_child(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    """A cancelled run (even cancelled twice) returns only after every child
    build has unwound and written its error step — no child writes on the
    shared connection after the caller resumes."""
    ids = [await _doc_n(migrated_db, tmp_path, n) for n in range(1, 3)]
    started: list[int] = []
    both_started = asyncio.Event()
    unwound: list[int] = []

    class _Hang(_StubAdapter):
        async def structured(self, **kwargs: Any) -> Any:
            started.append(1)
            if len(started) == 2:
                both_started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                await asyncio.sleep(0.05)  # cleanup that itself awaits
                unwound.append(1)
                raise
            return await super().structured(**kwargs)  # pragma: no cover

    task = asyncio.create_task(ensure_cards(
        migrated_db, ids, adapter=_Hang(_card()), model="m", tracer=fake_tracer,
        concurrency=2))
    await asyncio.wait_for(both_started.wait(), 5)
    task.cancel()
    await asyncio.sleep(0.01)
    task.cancel()  # a second cancel must not cut the children's cleanup short
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(unwound) == 2
    assert await _count(
        migrated_db,
        "SELECT COUNT(*) FROM tool_calls WHERE tool = 'ideation:card' AND error = 'cancelled'",
    ) == 2


# --- final fix wave: cache key covers the prompt slot and the model ---------


async def test_model_change_rebuilds_the_card(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    src = tmp_path / "paper"
    pid = await _doc(migrated_db, src)
    _write_digest(src, pid)
    adapter = _StubAdapter(_card())
    await build_card(migrated_db, pid, adapter=adapter, model="m1", tracer=fake_tracer)
    await build_card(migrated_db, pid, adapter=adapter, model="m1", tracer=fake_tracer)
    assert len(adapter.calls) == 1
    await build_card(migrated_db, pid, adapter=adapter, model="m2", tracer=fake_tracer)
    assert len(adapter.calls) == 2
    async with migrated_db.execute(
        "SELECT model, slot FROM mechanism_cards WHERE paper_content_id = ?", (pid,),
    ) as cur:
        assert tuple(await cur.fetchone() or ()) == ("m2", CARD_SLOT)


async def test_slot_change_rebuilds_the_card(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = tmp_path / "paper"
    pid = await _doc(migrated_db, src)
    _write_digest(src, pid)
    adapter = _StubAdapter(_card())
    await build_card(migrated_db, pid, adapter=adapter, model="m", tracer=fake_tracer)
    monkeypatch.setattr(cards, "CARD_SLOT", "ideation_card/v3")
    await build_card(migrated_db, pid, adapter=adapter, model="m", tracer=fake_tracer)
    assert len(adapter.calls) == 2
    assert adapter.calls[1]["slot"] == "ideation_card/v3"
    async with migrated_db.execute(
        "SELECT slot FROM mechanism_cards WHERE paper_content_id = ?", (pid,),
    ) as cur:
        assert (await cur.fetchone() or [None])[0] == "ideation_card/v3"


async def test_ensure_cards_reports_progress(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    """``on_progress(done, total, skipped)``: starts at 0, ends with every
    document processed; a skipped document still counts as processed."""
    good = await _doc_n(migrated_db, tmp_path, 1)
    bare = await _doc_n(migrated_db, tmp_path, 2, digest=False)
    seen: list[tuple[int, int, int]] = []
    await ensure_cards(migrated_db, [good, bare, good], adapter=_StubAdapter(_card()),
                       model="m", tracer=fake_tracer,
                       on_progress=lambda *a: seen.append(a))
    assert seen[0] == (0, 2, 0)
    assert seen[-1] == (2, 2, 1)
    assert [s[0] for s in seen] == sorted(s[0] for s in seen)


async def _letter(conn: aiosqlite.Connection, root: Path) -> int:
    """A sectionless document (paper 88's shape): a digest with no sections
    and chunks whose ``section`` is NULL."""
    src = root / "letter"
    cur = await conn.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, "
        "source_path, source_dir_path, html_path) "
        "VALUES ('arxiv:1203.3835', 'arxiv', '1203.3835', 'Ghost lidar', '', ?, '')",
        (str(src),),
    )
    assert cur.lastrowid is not None
    pid = int(cur.lastrowid)
    src.mkdir(parents=True)
    digest = PaperDigest(paper_id=pid, title="Ghost lidar", abstract="Sparse imaging.",
                         sections=[], figures=[])
    (src / "digest.json").write_text(digest.model_dump_json(), encoding="utf-8")
    for k in range(2):
        await conn.execute(
            "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text, "
            "dom_id) VALUES (?, NULL, ?, ?, 'body', ?)", (pid, k * 10, k * 10 + 9, f"d{k}"))
    await conn.commit()
    return pid


def _uncited_card() -> MechanismCard:
    return MechanismCard(
        problem=CitedField(text="Long-range imaging is blurry.", sections=[]),
        mechanism=CitedField(text="Reconstruct with sparsity constraints.", sections=["Nope"]),
        assumptions=[CitedField(text="The scene is sparse.", sections=[])],
        limitations=[], domains=["Remote Sensing"],
    )


async def test_a_sectionless_document_cites_its_full_text(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    """A document without sections is cited as a whole, so its card can back
    an idea (it was left off every board as uncitable)."""
    from paperhub.agents.sl_read import FULL_TEXT_SECTION

    pid = await _letter(migrated_db, tmp_path)
    card = await build_card(migrated_db, pid, adapter=_StubAdapter(_uncited_card()),
                            model="m", tracer=fake_tracer)
    assert card.problem.sections == [FULL_TEXT_SECTION]
    assert card.mechanism.sections == [FULL_TEXT_SECTION]
    assert card.assumptions[0].sections == [FULL_TEXT_SECTION]
    cached = await cards.cached_card(migrated_db, pid)
    assert cached is not None and cached[0] == card


async def test_a_cached_uncited_card_of_a_sectionless_document_is_upgraded(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    """The migration path (``paperhub-backfill-cards --ids``): a card cached
    before the fix cites nothing; the cache hit upgrades it, no LLM call."""
    from paperhub.agents.sl_read import FULL_TEXT_SECTION

    pid = await _letter(migrated_db, tmp_path)
    digest = PaperDigest.model_validate_json(
        (tmp_path / "letter" / "digest.json").read_text(encoding="utf-8"))
    await cards._write_card_tx_on(migrated_db, pid, _uncited_card(),
                                  digest_hash_=cards.digest_hash(digest), model="m")
    adapter = _StubAdapter(_card())
    card = await build_card(migrated_db, pid, adapter=adapter, model="m", tracer=fake_tracer)
    assert adapter.calls == []
    assert card.mechanism.sections == [FULL_TEXT_SECTION]
    cached = await cards.cached_card(migrated_db, pid)
    assert cached is not None and cached[0].problem.sections == [FULL_TEXT_SECTION]


async def test_a_sectioned_document_never_cites_full_text(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    src = tmp_path / "paper"
    pid = await _doc(migrated_db, src)
    _write_digest(src, pid)
    card = await build_card(migrated_db, pid, adapter=_StubAdapter(_card()), model="m",
                            tracer=fake_tracer)
    assert card.assumptions[0].sections == []
