"""Plan H3 Task 5: the ideation agent (problem <- mechanism, document -> document)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from paperhub.db.connection import open_db
from paperhub.ideation.agent import (
    PROPOSE_MAX_TOKENS,
    PROPOSE_SLOT,
    card_progress,
    cross_domain_reason,
    document_domains,
    explore_status,
    generate_ideas,
    last_skipped_docs,
    reconcile_generating_problems,
    run_explore,
    run_problem,
)
from paperhub.ideation.cards import CARD_SLOT, CardDraft
from paperhub.ideation.domains import assign_domains_tx
from paperhub.ideation.models import CitedField, IdeationOutput, ProposedIdea
from paperhub.models.slide_domain import DigestSection, PaperDigest
from paperhub.tracing.tracer import Tracer

# doc title -> (sections, domain)
DOCS: dict[str, tuple[list[str], str]] = {
    "Anchors": (["1 Intro", "3 Anchor Boxes"], "Computer Vision"),
    "Masking": (["1 Intro", "3 Masked Prediction"], "Natural Language Processing"),
    "Detector": (["1 Intro", "2 Method"], "Computer Vision"),
}


class _Stub:
    """Card calls answer from the document title; propose calls return
    ``ideas`` (or raise ``propose_error``)."""

    def __init__(self, ideas: list[ProposedIdea] | None = None,
                 propose_error: Exception | None = None,
                 fail_cards: set[str] | None = None) -> None:
        self.ideas = ideas or []
        self.propose_error = propose_error
        self.fail_cards = fail_cards or set()
        self.calls: list[dict[str, Any]] = []

    async def structured(self, **kw: Any) -> Any:
        self.calls.append(kw)
        if kw["slot"] == CARD_SLOT:
            title = kw["variables"]["title"]
            if title in self.fail_cards:
                raise ValueError("domains: list should have at least 1 item")
            sections, domain = DOCS[title]
            return CardDraft(
                problem=CitedField(text=f"{title} problem", sections=sections[:1]),
                mechanism=CitedField(text=f"{title} mechanism", sections=sections[1:2]),
                assumptions=[], limitations=[], domains=[domain],
            )
        assert kw["slot"] == PROPOSE_SLOT
        if self.propose_error is not None:
            raise self.propose_error
        return IdeationOutput(ideas=self.ideas)

    def stream(self, **kw: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


async def _library(conn: aiosqlite.Connection, root: Path) -> dict[str, int]:
    ids: dict[str, int] = {}
    for n, (title, (sections, _d)) in enumerate(DOCS.items()):
        src = root / f"doc{n}"
        src.mkdir(parents=True)
        cur = await conn.execute(
            "INSERT INTO paper_content (content_key, kind, arxiv_id, title, "
            "source_path, source_dir_path, html_path) VALUES (?, 'arxiv', ?, ?, '', ?, '')",
            (f"arxiv:{n}", f"{n}", title, str(src)),
        )
        assert cur.lastrowid is not None
        pid = int(cur.lastrowid)
        ids[title] = pid
        digest = PaperDigest(
            paper_id=pid, title=title, abstract="a",
            sections=[DigestSection(name=s, insight="i") for s in sections], figures=[],
        )
        (src / "digest.json").write_text(digest.model_dump_json(), encoding="utf-8")
        for k, s in enumerate(sections):
            await conn.execute(
                "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text) "
                "VALUES (?, ?, ?, ?, ?)", (pid, s, k * 10, k * 10 + 9, f"{title} {s}"),
            )
    await conn.commit()
    return ids


async def _problem(conn: aiosqlite.Connection) -> int:
    cur = await conn.execute(
        "INSERT INTO problems (text, tried) VALUES ('slow reranker', 'bigger model')",
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


async def _propose_step(conn: aiosqlite.Connection) -> tuple[str, dict[str, Any], Any]:
    async with conn.execute(
        "SELECT status, args_redacted_json, result_summary_json FROM tool_calls "
        "WHERE tool = 'ideation:propose' ORDER BY rowid DESC LIMIT 1",
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    return str(row[0]), json.loads(row[1]), json.loads(row[2]) if row[2] else None


def _idea(src: int, section: str, tgt: int | None = None,
          tsec: str | None = None, title: str = "idea") -> ProposedIdea:
    return ProposedIdea(source_doc=src, source_section=section, target_doc=tgt,
                        target_section=tsec, title=title, rationale="because")


async def _ideas(conn: aiosqlite.Connection) -> list[tuple[Any, ...]]:
    async with conn.execute(
        "SELECT id, problem_id, source_doc, source_section, source_chunk_ids_json, "
        "target_doc, target_section, target_chunk_ids_json, status FROM ideas ORDER BY id",
    ) as cur:
        return list(await cur.fetchall())


async def test_unknown_documents_and_sections_are_dropped_and_recorded(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    ids = await _library(migrated_db, tmp_path)
    pid = await _problem(migrated_db)
    a = ids["Anchors"]
    stub = _Stub([
        _idea(9999, "3 Anchor Boxes", title="ghost doc"),
        _idea(a, "7 Made Up", title="ghost section"),
        _idea(a, "3 anchor  boxes", title="good"),  # spacing/case variant is fine
        _idea(ids["Masking"], "9 Nope; 3 Masked Prediction", title="copied list"),  # a cites list
    ])
    kept = await generate_ideas(migrated_db, problem_id=pid, adapter=stub,
                                model="big", card_model="small", tracer=fake_tracer)
    assert len(kept) == 2
    rows = await _ideas(migrated_db)
    assert [r[0] for r in rows] == kept
    assert rows[0][3] == "3 Anchor Boxes"  # canonical name stored
    assert rows[1][3] == "3 Masked Prediction"  # first cited name of the copied list
    status, args, result = await _propose_step(migrated_db)
    assert status == "ok"
    assert set(args) >= {"problem", "tried", "cards_xml", "rejected_xml", "mode"}
    assert args["problem"] == "slow reranker" and args["tried"] == "bigger model"
    assert args["mode"] == "problem"
    for doc in ids.values():
        assert f'<card doc="{doc}"' in args["cards_xml"]
    reasons = {d["title"]: d["reason"] for d in result["dropped"]}
    assert set(reasons) == {"ghost doc", "ghost section"}
    assert "unknown" in reasons["ghost doc"] and "section" in reasons["ghost section"]
    assert result["kept_ids"] == kept
    assert sorted(result["card_ids_read"]) == sorted(ids.values())
    assert len(result["proposed"]) == 4
    # the card model built the cards, the flagship proposed
    models = {c["slot"]: c["model"] for c in stub.calls}
    assert models == {CARD_SLOT: "small", PROPOSE_SLOT: "big"}


async def test_problem_mode_idea_with_a_target_doc_is_dropped(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    ids = await _library(migrated_db, tmp_path)
    pid = await _problem(migrated_db)
    stub = _Stub([_idea(ids["Anchors"], "3 Anchor Boxes", ids["Masking"], "1 Intro")])
    kept = await generate_ideas(migrated_db, problem_id=pid, adapter=stub,
                                model="big", card_model="small", tracer=fake_tracer)
    assert kept == []
    _s, _a, result = await _propose_step(migrated_db)
    assert len(result["dropped"]) == 1 and "target" in result["dropped"][0]["reason"]


async def test_same_domain_explore_pair_is_dropped(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    ids = await _library(migrated_db, tmp_path)
    stub = _Stub([
        _idea(ids["Anchors"], "3 Anchor Boxes", ids["Detector"], "2 Method", title="same"),
        _idea(ids["Masking"], "3 Masked Prediction", ids["Detector"], "2 Method",
              title="cross"),
    ])
    kept = await generate_ideas(migrated_db, problem_id=None, adapter=stub,
                                model="big", card_model="small", tracer=fake_tracer)
    assert len(kept) == 1
    rows = await _ideas(migrated_db)
    assert rows[0][1] is None and rows[0][2] == ids["Masking"] and rows[0][5] == ids["Detector"]
    _s, args, result = await _propose_step(migrated_db)
    assert args["mode"] == "explore" and args["problem"] == ""
    assert [d["title"] for d in result["dropped"]] == ["same"]
    assert "domain" in result["dropped"][0]["reason"]


async def test_kept_ideas_carry_the_resolved_chunk_ids(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    ids = await _library(migrated_db, tmp_path)
    stub = _Stub([_idea(ids["Masking"], "3 Masked Prediction", ids["Anchors"], "1 Intro")])
    kept = await generate_ideas(migrated_db, problem_id=None, adapter=stub,
                                model="big", card_model="small", tracer=fake_tracer)
    async with migrated_db.execute(
        "SELECT id, paper_content_id, section FROM chunks",
    ) as cur:
        chunk = {(int(r[1]), str(r[2])): int(r[0]) for r in await cur.fetchall()}
    src_ids = [chunk[(ids["Masking"], "3 Masked Prediction")]]
    tgt_ids = [chunk[(ids["Anchors"], "1 Intro")]]
    row = (await _ideas(migrated_db))[0]
    assert json.loads(row[4]) == src_ids and json.loads(row[7]) == tgt_ids
    assert row[8] == "new"
    _s, _a, result = await _propose_step(migrated_db)
    assert sorted(result["evidence_chunk_ids"]) == sorted(src_ids + tgt_ids)
    assert result["kept_ids"] == kept


async def test_dismissed_ideas_appear_in_rejected_xml_and_are_not_resuggested(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    ids = await _library(migrated_db, tmp_path)
    pid = await _problem(migrated_db)
    m = ids["Masking"]
    await migrated_db.execute(
        "INSERT INTO ideas (problem_id, source_doc, source_section, title, status, "
        "dismiss_reasons_json, dismiss_note) VALUES (?, ?, '3 Masked Prediction', "
        "'mask the queries', 'dismissed', '[\"already_tried\"]', 'we did this in 2024')",
        (pid, m),
    )
    await migrated_db.execute(
        "INSERT INTO ideas (problem_id, source_doc, source_section, title, status, note) "
        "VALUES (?, ?, '3 Anchor Boxes', 'anchor the scores', 'failed', 'too slow')",
        (pid, ids["Anchors"]),
    )
    await migrated_db.commit()
    stub = _Stub([_idea(m, "3 Masked Prediction", title="mask again")])
    kept = await generate_ideas(migrated_db, problem_id=pid, adapter=stub,
                                model="big", card_model="small", tracer=fake_tracer)
    assert kept == []
    _s, args, result = await _propose_step(migrated_db)
    rejected = args["rejected_xml"]
    assert "mask the queries" in rejected and "already_tried" in rejected
    assert "we did this in 2024" in rejected
    assert "anchor the scores" in rejected and "too slow" in rejected
    assert "dismissed" in result["dropped"][0]["reason"]


async def test_failed_card_build_skips_the_document(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    ids = await _library(migrated_db, tmp_path)
    stub = _Stub([_idea(ids["Detector"], "2 Method", ids["Masking"], "1 Intro")],
                 fail_cards={"Detector"})
    kept = await generate_ideas(migrated_db, problem_id=None, adapter=stub,
                                model="big", card_model="small", tracer=fake_tracer)
    assert kept == []
    _s, args, result = await _propose_step(migrated_db)
    assert f'<card doc="{ids["Detector"]}"' not in args["cards_xml"]
    assert result["skipped_docs"] == [ids["Detector"]]
    assert "unknown" in result["dropped"][0]["reason"]


@pytest.mark.parametrize("error", [RuntimeError("provider down")])
async def test_llm_error_sets_problem_error_and_records_error_step(
    migrated_db: aiosqlite.Connection, tmp_path: Path, error: Exception,
) -> None:
    await _library(migrated_db, tmp_path / "lib")
    pid = await _problem(migrated_db)
    db_path = tmp_path / "paperhub.db"
    task = run_problem(pid, db_path=db_path, adapter=_Stub(propose_error=error),
                       model="big", card_model="small")
    await task
    async with migrated_db.execute("SELECT status FROM problems WHERE id = ?", (pid,)) as cur:
        assert (await cur.fetchone() or [None])[0] == "error"
    async with migrated_db.execute(
        "SELECT r.status, c.internal FROM runs r JOIN chat_sessions c ON c.id = r.session_id",
    ) as cur:
        assert await cur.fetchall() == [("error", 1)]
    status, _args, result = await _propose_step(migrated_db)
    assert status == "error"
    assert result is not None and result["failure"] == "provider_error"


async def test_invalid_reply_is_retried_once_then_recorded(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    await _library(migrated_db, tmp_path / "lib")
    pid = await _problem(migrated_db)
    try:
        IdeationOutput.model_validate_json('{"ideas": [ {"source_doc": "x"')
    except Exception as exc:  # noqa: BLE001
        bad = exc
    stub = _Stub(propose_error=bad)
    await run_problem(pid, db_path=tmp_path / "paperhub.db", adapter=stub,
                      model="big", card_model="small")
    status, _args, result = await _propose_step(migrated_db)
    assert status == "error"
    assert result["failure"] == "invalid_json" and result["attempts"] == 2
    assert result["output_head"]
    assert sum(1 for c in stub.calls if c["slot"] == PROPOSE_SLOT) == 2


async def test_successful_problem_run_marks_ready(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    ids = await _library(migrated_db, tmp_path / "lib")
    pid = await _problem(migrated_db)
    stub = _Stub([_idea(ids["Anchors"], "3 Anchor Boxes")])
    await run_problem(pid, db_path=tmp_path / "paperhub.db", adapter=stub,
                      model="big", card_model="small")
    async with migrated_db.execute("SELECT status FROM problems WHERE id = ?", (pid,)) as cur:
        assert (await cur.fetchone() or [None])[0] == "ready"
    rows = await _ideas(migrated_db)
    assert len(rows) == 1 and rows[0][1] == pid
    propose = [c for c in stub.calls if c["slot"] == PROPOSE_SLOT][0]
    assert propose["max_tokens"] > 0


async def test_cited_section_without_chunks_is_dropped(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    ids = await _library(migrated_db, tmp_path)
    pid = await _problem(migrated_db)
    await migrated_db.execute(
        "DELETE FROM chunks WHERE paper_content_id = ? AND section = '3 Anchor Boxes'",
        (ids["Anchors"],),
    )
    await migrated_db.commit()
    stub = _Stub([_idea(ids["Anchors"], "3 Anchor Boxes", title="no evidence")])
    kept = await generate_ideas(migrated_db, problem_id=pid, adapter=stub,
                                model="big", card_model="small", tracer=fake_tracer)
    assert kept == [] and await _ideas(migrated_db) == []
    _s, _a, result = await _propose_step(migrated_db)
    assert result["dropped"][0]["reason"] == "cited section has no chunks"
    assert result["new_count"] == 0


class _SlowCards(_Stub):
    """Card calls yield to the loop so two runs really overlap."""

    async def structured(self, **kw: Any) -> Any:
        if kw["slot"] == CARD_SLOT:
            await asyncio.sleep(0.05)
        return await super().structured(**kw)


async def _tracer(conn: aiosqlite.Connection) -> Tracer:
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    cur = await conn.execute(
        "INSERT INTO runs (session_id) VALUES ((SELECT MAX(id) FROM chat_sessions))")
    await conn.commit()
    assert cur.lastrowid is not None
    return Tracer(conn, run_id=int(cur.lastrowid), branch="")


async def test_concurrent_runs_build_each_card_once(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    ids = await _library(migrated_db, tmp_path / "lib")
    stub = _SlowCards([_idea(ids["Masking"], "3 Masked Prediction", ids["Anchors"], "1 Intro")])
    db = tmp_path / "paperhub.db"
    async with open_db(db) as c1, open_db(db) as c2:
        t1, t2 = await _tracer(c1), await _tracer(c2)
        await asyncio.gather(
            generate_ideas(c1, problem_id=None, adapter=stub, model="big",
                           card_model="small", tracer=t1),
            generate_ideas(c2, problem_id=None, adapter=stub, model="big",
                           card_model="small", tracer=t2),
        )
    built = [c["variables"]["title"] for c in stub.calls if c["slot"] == CARD_SLOT]
    assert sorted(built) == sorted(DOCS)  # each document's card built once
    assert len(await _ideas(migrated_db)) == 1  # the second run's copy is a duplicate


class _Blocking(_Stub):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def structured(self, **kw: Any) -> Any:
        self.started.set()
        await asyncio.Event().wait()  # never returns


async def test_cancelled_run_records_cancelled_and_problem_error(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    await _library(migrated_db, tmp_path / "lib")
    pid = await _problem(migrated_db)
    stub = _Blocking()
    task = run_problem(pid, db_path=tmp_path / "paperhub.db", adapter=stub,
                       model="big", card_model="small")
    await asyncio.wait_for(stub.started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with migrated_db.execute("SELECT status FROM problems WHERE id = ?", (pid,)) as cur:
        assert (await cur.fetchone() or [None])[0] == "error"
    async with migrated_db.execute("SELECT status FROM runs") as cur:
        assert await cur.fetchall() == [("cancelled",)]


async def test_run_explore_is_single_flight_and_reports_new_count(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    ids = await _library(migrated_db, tmp_path / "lib")
    db = tmp_path / "paperhub.db"
    idea = _idea(ids["Masking"], "3 Masked Prediction", ids["Anchors"], "1 Intro")
    assert (await explore_status(migrated_db)).last_run_at is None
    kw: dict[str, Any] = {"db_path": db, "adapter": _SlowCards([idea]), "model": "big",
                          "card_model": "small"}
    first = run_explore(**kw)
    assert run_explore(**kw) is first  # no duplicate while in flight
    assert (await explore_status(migrated_db)).running
    await first
    status = await explore_status(migrated_db)
    assert not status.running and status.last_new_count == 1
    assert status.last_error is None and status.last_run_at

    await run_explore(**kw)  # same idea again: a duplicate, nothing new
    status = await explore_status(migrated_db)
    assert status.last_new_count == 0 and status.last_error is None

    await run_explore(db_path=db, adapter=_Stub(propose_error=RuntimeError("down")),
                      model="big", card_model="small")
    status = await explore_status(migrated_db)
    assert status.last_error == "provider_error" and status.last_new_count is None


async def test_startup_sweep_moves_stuck_problems_to_error(
    migrated_db: aiosqlite.Connection,
) -> None:
    stuck = await _problem(migrated_db)  # default status 'generating'
    await migrated_db.execute(
        "INSERT INTO problems (text, status) VALUES ('done', 'ready')")
    await migrated_db.commit()
    assert await reconcile_generating_problems(migrated_db) == 1
    async with migrated_db.execute("SELECT id, status FROM problems ORDER BY id") as cur:
        rows = await cur.fetchall()
    assert rows[0] == (stuck, "error") and rows[1][1] == "ready"


# --- final fix wave: card progress + skipped documents ----------------------


class _ProgressProbe(_Stub):
    """Snapshots the scope's card progress when the propose call starts."""

    def __init__(self, scope: int | None, **kw: Any) -> None:
        super().__init__(**kw)
        self.scope = scope
        self.snapshot: Any = "unset"

    async def structured(self, **kw: Any) -> Any:
        if kw["slot"] == PROPOSE_SLOT:
            p = card_progress(self.scope)
            self.snapshot = None if p is None else (p.ready, p.total, p.skipped)
        return await super().structured(**kw)


async def test_problem_run_exposes_card_progress_then_skipped_count(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    await _library(migrated_db, tmp_path / "lib")
    pid = await _problem(migrated_db)
    stub = _ProgressProbe(pid, fail_cards={"Detector"})
    assert card_progress(pid) is None
    await run_problem(pid, db_path=tmp_path / "paperhub.db", adapter=stub,
                      model="big", card_model="small")
    assert stub.snapshot == (3, 3, 1)  # all processed, one skipped
    assert card_progress(pid) is None  # cleared once the run ends
    assert await last_skipped_docs(migrated_db, pid) == 1


async def test_explore_status_reports_progress_and_skipped(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    await _library(migrated_db, tmp_path / "lib")
    stub = _ProgressProbe(None, fail_cards={"Masking"})
    await run_explore(db_path=tmp_path / "paperhub.db", adapter=stub,
                      model="big", card_model="small")
    assert stub.snapshot == (3, 3, 1)
    status = await explore_status(migrated_db)
    assert status.skipped_docs == 1
    assert status.cards_ready is None and status.cards_total is None


ROB, CV, NLP, ML = "Robotics", "Computer Vision", "Natural Language Processing", "Machine Learning"


@pytest.mark.parametrize(("source", "target", "ok"), [
    # a language mechanism into a VLA paper, which also carries an NLP tag
    ([NLP], [ROB, CV, NLP], True),
    ([NLP, ML], [ROB, CV], True),
    # the target's primary (first) field is one the source is already in
    ([CV, "Representation Learning"], [CV, ML], False),
    ([NLP, "Genomics"], [NLP, "Machine Translation"], False),
    ([ML, NLP], [NLP, ML], False),
    ([ROB, CV, NLP], [CV, NLP], False),
    (["computer vision"], [CV], False),  # names compare normalized
    ([CV], [], True),  # an untagged target cannot be judged: keep
])
def test_cross_domain_means_the_target_primary_field_is_new_to_the_source(
    source: list[str], target: list[str], ok: bool,
) -> None:
    reason = cross_domain_reason(source, target)
    assert (reason is None) is ok
    if not ok:
        assert reason is not None and target[0].casefold() in reason


async def test_document_domains_lists_the_primary_field_first(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    ids = await _library(migrated_db, tmp_path)
    await assign_domains_tx(migrated_db, ids["Anchors"], [CV])  # CV gets the lower id
    await assign_domains_tx(migrated_db, ids["Detector"], [ROB, CV, NLP])
    await migrated_db.commit()
    # at most MAX_DOMAINS (2) tags, in the order the card named them
    assert (await document_domains(migrated_db))[ids["Detector"]] == [ROB, CV]


async def test_explore_keeps_a_pair_whose_target_field_is_new_to_the_source(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    """A multi-label target (a VLA paper: robotics, vision, language) still
    takes a mechanism from a language paper."""
    ids = await _library(migrated_db, tmp_path)
    stub = _Stub([_idea(ids["Masking"], "3 Masked Prediction", ids["Detector"], "2 Method")])
    await generate_ideas(migrated_db, problem_id=None, adapter=stub,
                         model="big", card_model="small", tracer=fake_tracer)
    await assign_domains_tx(migrated_db, ids["Detector"], [ROB, CV, NLP])
    await migrated_db.commit()
    await migrated_db.execute("DELETE FROM ideas")
    await migrated_db.commit()
    kept = await generate_ideas(migrated_db, problem_id=None, adapter=stub,
                                model="big", card_model="small", tracer=fake_tracer)
    assert len(kept) == 1


def test_propose_cap_leaves_room_for_a_thinking_model() -> None:
    """Run 705: gemini-pro-latest spent the 8192-token cap on reasoning over the
    60-card explore board and returned truncated JSON."""
    assert PROPOSE_MAX_TOKENS >= 32768


class _RepairStub(_Stub):
    """Propose calls answer from ``rounds`` in order."""

    def __init__(self, rounds: list[list[ProposedIdea]]) -> None:
        super().__init__()
        self.rounds = rounds

    async def structured(self, **kw: Any) -> Any:
        if kw["slot"] == PROPOSE_SLOT:
            self.calls.append(kw)
            return IdeationOutput(ideas=self.rounds.pop(0))
        return await super().structured(**kw)


async def test_all_dropped_proposals_get_one_repair_round_with_the_reasons(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    ids = await _library(migrated_db, tmp_path)
    bad = _idea(ids["Anchors"], "3 Anchor Boxes", ids["Detector"], "2 Method", title="same")
    good = _idea(ids["Masking"], "3 Masked Prediction", ids["Detector"], "2 Method",
                 title="cross")
    stub = _RepairStub([[bad], [good]])
    kept = await generate_ideas(migrated_db, problem_id=None, adapter=stub,
                                model="big", card_model="small", tracer=fake_tracer)
    assert len(kept) == 1
    proposes = [c for c in stub.calls if c["slot"] == PROPOSE_SLOT]
    assert len(proposes) == 2
    retry_rejected = proposes[1]["variables"]["rejected_xml"]
    assert 'status="invalid"' in retry_rejected and "same" in retry_rejected
    assert "primary domain" in retry_rejected  # the drop reason is shown
    _s, _a, result = await _propose_step(migrated_db)
    assert [d["title"] for d in result["dropped"]] == ["same"]
    assert result["repair"]["rejected_xml"] == retry_rejected
    assert [i["title"] for i in result["repair"]["proposed"]] == ["cross"]
    assert result["repair"]["dropped"] == []
    assert result["new_count"] == 1


async def test_no_repair_round_when_an_idea_survives_or_none_were_proposed(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    ids = await _library(migrated_db, tmp_path)
    bad = _idea(ids["Anchors"], "3 Anchor Boxes", ids["Detector"], "2 Method", title="same")
    good = _idea(ids["Masking"], "3 Masked Prediction", ids["Detector"], "2 Method",
                 title="cross")
    for rounds in ([[bad, good]], [[]]):
        stub = _RepairStub(rounds)
        await generate_ideas(migrated_db, problem_id=None, adapter=stub,
                             model="big", card_model="small", tracer=fake_tracer)
        assert sum(1 for c in stub.calls if c["slot"] == PROPOSE_SLOT) == 1
        _s, _a, result = await _propose_step(migrated_db)
        assert "repair" not in result


class _UncitableStub(_Stub):
    """The "Detector" card cites no section (its paper has none: a short
    letter whose digest has no sections, like paper 88)."""

    async def structured(self, **kw: Any) -> Any:
        out = await super().structured(**kw)
        if kw["slot"] == CARD_SLOT and kw["variables"]["title"] == "Detector":
            out.problem.sections = []
            out.mechanism.sections = []
        return out


async def test_a_card_that_cites_no_section_is_left_off_the_board(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    """No idea from it could be kept (every idea must cite a section), so it
    would only waste proposal slots."""
    ids = await _library(migrated_db, tmp_path)
    stub = _UncitableStub([_idea(ids["Masking"], "3 Masked Prediction",
                                 ids["Anchors"], "3 Anchor Boxes")])
    await generate_ideas(migrated_db, problem_id=None, adapter=stub,
                         model="big", card_model="small", tracer=fake_tracer)
    _s, args, result = await _propose_step(migrated_db)
    assert f'<card doc="{ids["Detector"]}"' not in args["cards_xml"]
    assert result["uncitable_docs"] == [ids["Detector"]]
    assert ids["Detector"] in result["skipped_docs"]
    assert ids["Detector"] not in result["card_ids_read"]


async def test_a_sectionless_document_is_cited_as_its_full_text(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    """Paper 88's shape (a letter with no sections) is on the board citing
    "Full text", and an idea from it keeps the body's chunk ids."""
    from paperhub.agents.sl_read import FULL_TEXT_SECTION

    ids = await _library(migrated_db, tmp_path)
    src = tmp_path / "letter"
    src.mkdir()
    cur = await migrated_db.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, "
        "source_path, source_dir_path, html_path) "
        "VALUES ('arxiv:9', 'arxiv', '9', 'Letter', '', ?, '')", (str(src),))
    assert cur.lastrowid is not None
    letter = int(cur.lastrowid)
    (src / "digest.json").write_text(PaperDigest(
        paper_id=letter, title="Letter", abstract="a", sections=[], figures=[],
    ).model_dump_json(), encoding="utf-8")
    for k in range(2):
        await migrated_db.execute(
            "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text, "
            "dom_id) VALUES (?, NULL, ?, ?, 'body', ?)", (letter, k * 10, k * 10 + 9, f"d{k}"))
    await migrated_db.commit()
    DOCS["Letter"] = ([], "Remote Sensing")
    try:
        stub = _Stub([_idea(letter, FULL_TEXT_SECTION, ids["Anchors"], "3 Anchor Boxes")])
        await generate_ideas(migrated_db, problem_id=None, adapter=stub,
                             model="big", card_model="small", tracer=fake_tracer)
    finally:
        del DOCS["Letter"]
    _s, args, result = await _propose_step(migrated_db)
    assert f'<card doc="{letter}"' in args["cards_xml"]
    assert f'cites="{FULL_TEXT_SECTION}"' in args["cards_xml"]
    assert result["uncitable_docs"] == []
    rows = await _ideas(migrated_db)
    assert len(rows) == 1 and rows[0][3] == FULL_TEXT_SECTION
    assert len(json.loads(rows[0][4])) == 2


async def test_a_thinking_budget_bounds_every_propose_call(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    """``thinking_budget`` caps the proposer's reasoning on each call (the
    repair call too) and is recorded on the step; 0 leaves it unbounded."""
    ids = await _library(migrated_db, tmp_path)
    bad = _idea(ids["Anchors"], "3 Anchor Boxes", ids["Detector"], "2 Method")
    stub = _RepairStub([[bad], [bad]])
    await generate_ideas(migrated_db, problem_id=None, adapter=stub, model="big",
                         card_model="small", tracer=fake_tracer, thinking_budget=2048)
    propose = [c for c in stub.calls if c["slot"] == PROPOSE_SLOT]
    assert len(propose) == 2
    for c in propose:
        assert c["thinking"] == {"type": "enabled", "budget_tokens": 2048}
        assert c["drop_params"] is True
    _s, args, _r = await _propose_step(migrated_db)
    assert args["thinking_budget"] == 2048

    stub = _Stub([_idea(ids["Masking"], "3 Masked Prediction", ids["Anchors"], "3 Anchor Boxes")])
    await generate_ideas(migrated_db, problem_id=None, adapter=stub, model="big",
                         card_model="small", tracer=fake_tracer)
    call = next(c for c in stub.calls if c["slot"] == PROPOSE_SLOT)
    assert "thinking" not in call


# ---------------------------------------------------------------- QA fixes


class _NoFit(_Stub):
    """The proposer finds nothing relevant: no ideas plus a reason."""

    async def structured(self, **kw: Any) -> Any:
        if kw["slot"] == PROPOSE_SLOT:
            self.calls.append(kw)
            return IdeationOutput(ideas=[], no_fit_reason="Nothing here is about baking.")
        return await super().structured(**kw)


async def test_nothing_relevant_is_recorded_and_reported(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    """QA I2: no applicable document = no ideas + a recorded outcome."""
    from paperhub.ideation.agent import last_outcome

    await _library(migrated_db, tmp_path)
    pid = await _problem(migrated_db)
    await migrated_db.execute(
        "INSERT INTO chat_sessions (title, internal) VALUES ('i', 1)")
    cur = await migrated_db.execute(
        "INSERT INTO runs (session_id, status, routing_decision_json) VALUES "
        "((SELECT MAX(id) FROM chat_sessions), 'ok', ?)",
        (json.dumps({"ideation_mode": "problem", "problem_id": pid}),))
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=int(cur.lastrowid or 0), branch="")
    kept = await generate_ideas(migrated_db, problem_id=pid, adapter=_NoFit(),
                                model="big", card_model="small", tracer=tracer)
    assert kept == []
    _s, _a, result = await _propose_step(migrated_db)
    assert result["outcome"] == "nothing_relevant"
    assert result["no_fit_reason"] == "Nothing here is about baking."
    got = await last_outcome(migrated_db, pid)
    assert got.nothing_relevant and got.reason == "Nothing here is about baking."


async def test_problem_mode_records_language_and_existing_ideas(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    """QA I1/I9: problem mode answers in the problem's language (empty
    response_language); live ideas reach the proposer as existing_xml."""
    ids = await _library(migrated_db, tmp_path)
    pid = await _problem(migrated_db)
    await migrated_db.execute(
        "INSERT INTO ideas (problem_id, source_doc, source_section, title, status) "
        "VALUES (?, ?, '3 Anchor Boxes', 'Rank by anchor overlap', 'saved')",
        (pid, ids["Anchors"]))
    await migrated_db.commit()
    stub = _Stub([_idea(ids["Masking"], "3 Masked Prediction")])
    await generate_ideas(migrated_db, problem_id=pid, adapter=stub,
                         model="big", card_model="small", tracer=fake_tracer)
    _s, args, _r = await _propose_step(migrated_db)
    assert args["response_language"] == ""
    assert "Rank by anchor overlap" in args["existing_xml"]
    variables = [c for c in stub.calls if c["slot"] == PROPOSE_SLOT][0]["variables"]
    assert set(variables) == {"problem", "tried", "cards_xml", "rejected_xml",
                              "existing_xml", "mode", "response_language"}


async def test_explore_takes_the_ui_language(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    await _library(migrated_db, tmp_path)
    await generate_ideas(migrated_db, problem_id=None, adapter=_Stub([]), model="big",
                         card_model="small", tracer=fake_tracer, language="zh-TW")
    _s, args, _r = await _propose_step(migrated_db)
    assert args["response_language"] == "Traditional Chinese (zh-TW)"


async def test_near_duplicates_are_dropped_with_a_reason(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    """QA I9/I11: a restatement of an existing idea (explore: the same doc
    pair from other sections) is dropped and listed with its reason."""
    ids = await _library(migrated_db, tmp_path)
    m, a = ids["Masking"], ids["Anchors"]
    first = _Stub([_idea(m, "3 Masked Prediction", a, "1 Intro", title="Mask anchors")])
    await generate_ideas(migrated_db, problem_id=None, adapter=first, model="big",
                         card_model="small", tracer=fake_tracer)
    again = _Stub([_idea(m, "1 Intro", a, "3 Anchor Boxes", title="Hide boxes"),
                   _idea(m, "1 Intro", ids["Detector"], "2 Method", title="x")])
    await generate_ideas(migrated_db, problem_id=None, adapter=again, model="big",
                         card_model="small", tracer=fake_tracer)
    _s, _a, result = await _propose_step(migrated_db)
    assert [d["reason"] for d in result["dropped"]] == ["near-duplicate of idea 1"]
    assert result["new_count"] == 1  # the new Masking -> Detector pair is kept
