"""Cited, domain-free mechanism cards (Plan H3, FR-20).

Each library document gets one :class:`MechanismCard` built by a single
``structured`` call on ``ideation_card/v2`` from the document's cached
:class:`PaperDigest` (the slides digest cache — ``get_or_build_digest``). Every
card field cites digest section names; a cited name the digest does not have
is dropped (a spacing/case variant maps onto the canonical name), so a section
always resolves to real chunks via ``read_section_chunks``. A document with no
sections (every chunk's section NULL) cites ``FULL_TEXT_SECTION`` instead.

The card is cached in ``mechanism_cards`` keyed by a hash of the digest, the
card prompt slot (``CARD_SLOT``) and the model, and rebuilt when any of them
changes. The card upsert, the document's AUTO
domain re-tag (``assign_domains_tx`` never touches manual tags) and the colour
pass commit in ONE ``write_transaction`` — a card is never cached untagged. A
cache hit on a document with no domain rows re-tags it from the cached card.
The ``ideation:card`` trace step records the paper id, the digest section
names + hash, the card and the dropped citations — never the prompt.

A missing digest is built inside its own ``ideation:digest`` step, and only
when the document has a PaperAsset, like the slides pipeline: building without
one would cache an equation-less ``digest.json`` that slides then reuse. Such a
document raises :class:`CardUnavailable` (recorded on the step).
``ensure_cards`` builds many cards with bounded concurrency and skips failures.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import aiosqlite
from pydantic import BaseModel, Field, field_validator

from paperhub.agents.paper_digest import get_or_build_digest
from paperhub.agents.sl_read import FULL_TEXT_SECTION, is_sectionless
from paperhub.config import flagship_tier_model
from paperhub.db.connection import open_db, write_transaction
from paperhub.ideation.domains import (
    assign_color_indices_tx,
    assign_domains_tx,
    has_domains,
)
from paperhub.ideation.models import CitedField, MechanismCard
from paperhub.llm.adapter import LlmAdapter
from paperhub.models.slide_domain import PaperDigest
from paperhub.pipelines.paper_asset import PaperAsset, read_paper_asset
from paperhub.tracing.tracer import Tracer

CARD_SLOT = "ideation_card/v2"
MAX_DOMAINS = 3

_LOG = logging.getLogger(__name__)


class CardUnavailable(Exception):
    """The document cannot get a card (no digest and no PaperAsset to build one)."""


class CardDraft(BaseModel):
    """The LLM response schema. Every field is REQUIRED: with a native
    ``response_format`` an optional field (MechanismCard's defaulted
    ``domains``) is simply omitted by the model — the card sweep's first round
    got no domains on 47 of 48 cards."""

    problem: CitedField
    mechanism: CitedField
    assumptions: list[CitedField]
    limitations: list[CitedField]
    domains: list[str] = Field(min_length=1)

    @field_validator("domains")
    @classmethod
    def _cap_domains(cls, v: list[str]) -> list[str]:
        """Keep the first ``MAX_DOMAINS`` instead of failing a 4-domain reply."""
        return v[:MAX_DOMAINS]


def _digest_model() -> str:
    """``settings.report_section_model`` without loading Settings (which
    creates the workspace dir) — the slides digest model, so the shared
    digest cache is built the same way from both callers. MUST mirror
    ``config.load_settings``'s ``report_section_model`` line (pinned by
    ``test_digest_model_matches_settings``)."""
    return os.environ.get("PAPERHUB_REPORT_SECTION_MODEL", flagship_tier_model())


def digest_hash(digest: PaperDigest) -> str:
    """Hash of what the card prompt sees (title, abstract, section insights)."""
    payload = {
        "title": digest.title, "abstract": digest.abstract,
        "sections": [[s.name, s.insight] for s in digest.sections],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sections_xml(digest: PaperDigest) -> str:
    """One ``<section name="…">insight</section>`` per digest section."""
    lines = [
        f"<section name={quoteattr(s.name)}>{escape(s.insight)}</section>"
        for s in digest.sections
    ]
    return "<sections>\n" + "\n".join(lines) + "\n</sections>"


def card_variables(digest: PaperDigest) -> dict[str, str]:
    """The prompt variables — shared verbatim with the eval stage."""
    return {
        "title": escape(digest.title),
        "abstract": escape(digest.abstract),
        "sections_xml": sections_xml(digest),
    }


def _norm(name: str) -> str:
    return " ".join(name.split()).casefold()


def clean_card(card: MechanismCard, names: list[str]) -> tuple[MechanismCard, list[str]]:
    """Keep only cited sections the digest has (canonical spelling); return the
    cleaned card and the dropped names."""
    canonical = {_norm(n): n for n in names}
    dropped: list[str] = []

    def fix(f: CitedField) -> CitedField:
        kept: list[str] = []
        for s in f.sections:
            c = canonical.get(_norm(s))
            if c is None:
                dropped.append(s)
            elif c not in kept:
                kept.append(c)
        return CitedField(text=f.text, sections=kept)

    cleaned = MechanismCard(
        problem=fix(card.problem),
        mechanism=fix(card.mechanism),
        assumptions=[fix(f) for f in card.assumptions],
        limitations=[fix(f) for f in card.limitations],
        domains=[d for d in card.domains if d.strip()],
    )
    return cleaned, dropped


def cite_full_text(card: MechanismCard) -> MechanismCard:
    """A sectionless document's card: every field cites
    :data:`FULL_TEXT_SECTION` (the whole body), its only citable section — the
    text can only come from there, and an uncitable card could never back an
    idea."""

    def fix(f: CitedField) -> CitedField:
        return CitedField(text=f.text, sections=[FULL_TEXT_SECTION])

    return card.model_copy(update={
        "problem": fix(card.problem), "mechanism": fix(card.mechanism),
        "assumptions": [fix(f) for f in card.assumptions],
        "limitations": [fix(f) for f in card.limitations],
    })


async def _cites_whole_document(
    conn: aiosqlite.Connection, paper_content_id: int, digest: PaperDigest,
) -> bool:
    return not digest.sections and await is_sectionless(conn, paper_content_id)


async def _source_dir(conn: aiosqlite.Connection, paper_content_id: int) -> str:
    async with conn.execute(
        "SELECT source_dir_path FROM paper_content WHERE id = ?", (paper_content_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise LookupError(f"paper_content {paper_content_id} not found")
    return str(row[0] or "")


async def load_digest(
    conn: aiosqlite.Connection, paper_content_id: int, *,
    adapter: LlmAdapter, model: str, tracer: Tracer | None = None,
) -> PaperDigest:
    """The document's cached digest. A miss is built (traced as
    ``ideation:digest`` when a tracer is given) only when the document has a
    PaperAsset, else :class:`CardUnavailable`."""
    raw_dir = await _source_dir(conn, paper_content_id)
    source_dir = Path(raw_dir) if raw_dir else None
    cache = source_dir / "digest.json" if source_dir is not None else None
    if cache is not None and cache.is_file():
        try:
            text = await asyncio.to_thread(cache.read_text, encoding="utf-8")
            return PaperDigest.model_validate_json(text)
        except ValueError:
            pass  # corrupt: rebuilt below, like get_or_build_digest

    async def build() -> PaperDigest:
        asset: PaperAsset | None = None
        if source_dir is not None:
            asset = await asyncio.to_thread(read_paper_asset, source_dir)
        if asset is None:
            raise CardUnavailable(
                f"paper_content {paper_content_id}: no PaperAsset to build a digest")
        return await get_or_build_digest(
            paper_id=paper_content_id, conn=conn, asset=asset, adapter=adapter,
            model=model,
        )

    if tracer is None:
        return await build()
    async with tracer.step(agent="ideation", tool="ideation:digest", model=model) as step:
        step.record_args({"paper_content_id": paper_content_id, "source_dir": raw_dir})
        digest = await build()
        step.record_result({
            "paper_content_id": paper_content_id,
            "sections": [x.name for x in digest.sections],
            "empty_sections": [x.name for x in digest.sections if not x.insight],
        })
        return digest


async def cached_card(
    conn: aiosqlite.Connection, paper_content_id: int,
) -> tuple[MechanismCard, tuple[str, str, str]] | None:
    """The cached card and its cache key ``(digest_hash, slot, model)``."""
    async with conn.execute(
        "SELECT card_json, digest_hash, slot, model FROM mechanism_cards "
        "WHERE paper_content_id = ?",
        (paper_content_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    try:
        card = MechanismCard.model_validate_json(str(row[0]))
    except ValueError:
        return None
    return card, (str(row[1]), str(row[2]), str(row[3]))


async def _store_and_tag(
    conn: aiosqlite.Connection, paper_content_id: int, card: MechanismCard,
    *, digest_hash_: str | None, model: str,
) -> None:
    """One transaction: upsert the card (unless ``digest_hash_`` is None),
    re-tag the document's auto domains, colour new domains.

    Runs on its OWN connection: the caller's connection is shared with the
    Tracer, whose ``INSERT``+``commit`` is not in a managed transaction. On a
    shared connection a concurrent build (``ensure_cards``) would either hit
    "cannot start a transaction within a transaction" or have the tracer's
    commit land mid-transaction and break its atomicity."""
    path = await _db_file(conn)
    if path is None:  # in-memory DB: no second connection possible
        await _write_card_tx_on(conn, paper_content_id, card,
                                digest_hash_=digest_hash_, model=model)
        return
    async with open_db(path) as own:
        await _write_card_tx_on(own, paper_content_id, card,
                                digest_hash_=digest_hash_, model=model)


async def _db_file(conn: aiosqlite.Connection) -> Path | None:
    async with conn.execute("PRAGMA database_list") as cur:
        for row in await cur.fetchall():
            if row[1] == "main" and row[2]:
                return Path(str(row[2]))
    return None


async def _write_card_tx_on(
    conn: aiosqlite.Connection, paper_content_id: int, card: MechanismCard,
    *, digest_hash_: str | None, model: str,
) -> None:
    async with write_transaction(conn):
        if digest_hash_ is not None:
            await conn.execute(
                "INSERT INTO mechanism_cards "
                "(paper_content_id, card_json, digest_hash, model, slot) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(paper_content_id) DO UPDATE SET "
                "card_json = excluded.card_json, digest_hash = excluded.digest_hash, "
                "model = excluded.model, slot = excluded.slot, "
                "created_at = datetime('now')",
                (paper_content_id, card.model_dump_json(), digest_hash_, model,
                 CARD_SLOT),
            )
        await assign_domains_tx(conn, paper_content_id, card.domains)
        await assign_color_indices_tx(conn)


async def build_card(
    conn: aiosqlite.Connection,
    paper_content_id: int,
    *,
    adapter: LlmAdapter,
    model: str,
    tracer: Tracer,
    digest_model: str | None = None,
) -> MechanismCard:
    """The document's card — cached; rebuilt when its digest, the card prompt
    slot or the model changed.

    ``digest_model`` builds a missing digest (default: the slides digest model,
    ``settings.report_section_model``, so the shared cache stays consistent).
    """
    digest = await load_digest(
        conn, paper_content_id, adapter=adapter,
        model=digest_model or _digest_model(), tracer=tracer,
    )
    h = digest_hash(digest)
    hit = await cached_card(conn, paper_content_id)
    if hit is not None and hit[1] == (h, CARD_SLOT, model):
        if await _cites_whole_document(conn, paper_content_id, digest):
            upgraded = cite_full_text(hit[0])
            if upgraded != hit[0]:  # cached before sectionless docs were citable
                await _store_and_tag(conn, paper_content_id, upgraded,
                                     digest_hash_=h, model=model)
                return upgraded
        if hit[0].domains and not await has_domains(conn, paper_content_id):
            await _store_and_tag(conn, paper_content_id, hit[0],
                                 digest_hash_=None, model=model)
        return hit[0]

    names = [s.name for s in digest.sections]
    async with tracer.step(agent="ideation", tool="ideation:card", model=model) as step:
        step.record_args({
            "paper_content_id": paper_content_id,
            "digest_sections": names,
            "digest_hash": h,
        })
        raw = await adapter.structured(
            slot=CARD_SLOT, variables=card_variables(digest),
            response_model=CardDraft, model=model,
        )
        draft = CardDraft.model_validate(raw, from_attributes=True)
        card, dropped = clean_card(MechanismCard(**draft.model_dump()), names)
        if await _cites_whole_document(conn, paper_content_id, digest):
            card = cite_full_text(card)
        step.record_result({
            "paper_content_id": paper_content_id,
            "card": card.model_dump(),
            "dropped_sections": dropped,
        })

    await _store_and_tag(conn, paper_content_id, card, digest_hash_=h, model=model)
    return card


async def ensure_cards(
    conn: aiosqlite.Connection,
    paper_content_ids: list[int],
    *,
    adapter: LlmAdapter,
    model: str,
    tracer: Tracer,
    concurrency: int = 2,
    digest_model: str | None = None,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> dict[int, MechanismCard]:
    """Cards for many documents, at most ``concurrency`` building at once
    (a digest build fans out one LLM call per section). A document whose card
    fails (:class:`CardUnavailable`, an LLM or validation error) is skipped:
    logged, its error recorded on its ``ideation:digest`` / ``ideation:card``
    step, and absent from the result. On cancellation every child build is
    cancelled and awaited before ``CancelledError`` propagates.

    ``on_progress(done, total, skipped)`` is called once before any build and
    after each document finishes; ``done`` counts processed documents (built,
    cached or skipped), so it ends at ``total``."""
    sem = asyncio.Semaphore(max(1, concurrency))
    unique = list(dict.fromkeys(paper_content_ids))
    done = skipped = 0

    def report(card: MechanismCard | None) -> None:
        nonlocal done, skipped
        done += 1
        skipped += card is None
        if on_progress is not None:
            on_progress(done, len(unique), skipped)

    async def one(pid: int) -> tuple[int, MechanismCard | None]:
        async with sem:
            try:
                card = await build_card(conn, pid, adapter=adapter, model=model,
                                        tracer=tracer, digest_model=digest_model)
            except Exception as exc:  # noqa: BLE001 - one bad doc never aborts the run
                _LOG.warning("ideation: no card for paper_content %d: %s", pid, exc)
                report(None)
                return pid, None
            report(card)
            return pid, card

    if not unique:
        return {}
    if on_progress is not None:
        on_progress(0, len(unique), 0)
    tasks = [asyncio.ensure_future(one(p)) for p in unique]
    try:
        # ``wait`` (not ``gather``): a cancel of the caller is NOT forwarded to
        # the children, so we cancel them exactly once below.
        await asyncio.wait(tasks)
    except asyncio.CancelledError:
        # Cancel the children once and wait until every one has unwound (its
        # tracer error step written on the shared connection) before the
        # caller resumes. A repeated cancel must not cut that cleanup short:
        # a cancelled aiosqlite await still runs its SQL later, mid-caller.
        for t in tasks:
            t.cancel()
        pending = set(tasks)
        while pending:
            try:
                _, pending = await asyncio.wait(pending)
            except asyncio.CancelledError:
                continue
        raise
    results = [t.result() for t in tasks]
    return {pid: card for pid, card in results if card is not None}
