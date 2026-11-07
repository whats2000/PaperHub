"""``paperhub-backfill-cards``: every library document gets a mechanism card
(the per-paper data H3's ideation needs), idempotently."""
from __future__ import annotations

from pathlib import Path

import aiosqlite

from paperhub.cli.backfill_cards import backfill
from tests.test_ideation_agent import _library, _Stub


async def test_builds_missing_cards_then_reports_them_ready(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    ids = await _library(migrated_db, tmp_path / "lib")
    db = tmp_path / "paperhub.db"

    dry = await backfill(db, adapter=_Stub(), card_model="small", dry_run=True)
    assert {r["paper_content_id"]: r["status"] for r in dry} == dict.fromkeys(
        ids.values(), "missing")

    stub = _Stub(fail_cards={"Detector"})
    rows = await backfill(db, adapter=stub, card_model="small", dry_run=False)
    status = {r["paper_content_id"]: r for r in rows}
    assert status[ids["Anchors"]]["status"] == "built"
    assert status[ids["Detector"]]["status"] == "error"
    assert "at least 1 item" in status[ids["Detector"]]["reason"]

    again = await backfill(db, adapter=_Stub(), card_model="small", dry_run=False)
    status = {r["paper_content_id"]: r["status"] for r in again}
    assert status[ids["Anchors"]] == "ready" and status[ids["Masking"]] == "ready"
    assert status[ids["Detector"]] == "built"

    stale = await backfill(db, adapter=_Stub(), card_model="other", dry_run=True)
    assert {r["status"] for r in stale} == {"stale"}


async def test_ids_limit_the_run_and_an_uncitable_card_is_reported_then_upgraded(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    """The migration for sectionless documents runs on the affected ids only:
    a dry run flags the cached card that cites nothing as ``uncitable``, the
    run upgrades it to cite "Full text" (``built``) without an LLM call."""
    from paperhub.agents.sl_read import FULL_TEXT_SECTION
    from paperhub.ideation import cards
    from paperhub.ideation.models import CitedField, MechanismCard
    from paperhub.models.slide_domain import PaperDigest
    from tests.test_ideation_cards import _letter

    ids = await _library(migrated_db, tmp_path / "lib")
    letter = await _letter(migrated_db, tmp_path)
    digest = PaperDigest.model_validate_json(
        (tmp_path / "letter" / "digest.json").read_text(encoding="utf-8"))
    uncited = MechanismCard(problem=CitedField(text="p", sections=[]),
                            mechanism=CitedField(text="m", sections=[]),
                            assumptions=[], limitations=[], domains=["Remote Sensing"])
    await cards._write_card_tx_on(migrated_db, letter, uncited,
                                  digest_hash_=cards.digest_hash(digest), model="small")
    db = tmp_path / "paperhub.db"

    dry = await backfill(db, adapter=_Stub(), card_model="small", dry_run=True,
                         ids=[letter])
    assert [(r["paper_content_id"], r["status"]) for r in dry] == [(letter, "uncitable")]

    stub = _Stub()
    rows = await backfill(db, adapter=stub, card_model="small", dry_run=False, ids=[letter])
    assert [(r["paper_content_id"], r["status"]) for r in rows] == [(letter, "built")]
    assert stub.calls == []
    after = await cards.cached_card(migrated_db, letter)
    assert after is not None and after[0].mechanism.sections == [FULL_TEXT_SECTION]
    assert ids["Anchors"] not in {r["paper_content_id"] for r in rows}
