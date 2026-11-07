"""The ideation agent (Plan H3, FR-20).

One ``ideation:propose`` step reads every document's mechanism card in a
single context and proposes up to ``MAX_IDEAS`` ideas, best first:

- **problem mode** (``problem_id`` set): ``problem <- mechanism`` — the source
  document's cited section supplies a mechanism for the user's problem; the
  target is the problem itself (``target_doc`` must be null).
- **explore mode** (``problem_id`` None): ``document -> document`` — the source
  document's mechanism applied to a target document that shares no domain.

``generate_ideas`` steps:

1. Ensure a card for every library document (``cards.ensure_cards``; a cache
   hit makes no LLM call, digest builds are concurrency-bounded). A document
   whose card fails is skipped (its error is on its own ``ideation:digest`` /
   ``ideation:card`` step) and its id is listed in the step result as
   ``skipped_docs`` — it never aborts the run. Private documents
   are included: they are the user's own library and only reach the
   configured LLM, the same as ``paper_qa``.
2. Render the prompt variables — exactly the keys the G1 ``ideation``
   stage replays (``problem``, ``tried``, ``cards_xml``, ``rejected_xml``,
   ``mode``, ``response_language``) — and record them as the step args (never
   the rendered prompt).
   ``rejected_xml`` lists the scope's dismissed and failed ideas with their
   reasons, so the model is told what not to suggest again.
3. One bounded ``structured`` call (output cap, one retry on an invalid reply,
   an overall timeout); a failure records its kind, the attempts and the head
   of the bad output on the error step.
4. Validate: unknown documents, sections a card does not cite, a problem-mode
   ``target_doc``, a same-domain (or same-document) explore pair, a repeat of a
   dismissed/failed idea and a duplicate are dropped and listed as ``dropped``
   with a reason. A cited section that resolves to no chunks is dropped too, so
   a stored idea never carries an empty or fabricated citation.
5. Resolve chunk ids with ``read_section_chunks`` and insert the kept ideas as
   ``status='new'`` in rank order.

``run_problem`` / ``run_explore`` start the work as a background task (strong
refs in ``_live_tasks``) under a run of the hidden ideation session, set the
problem's ``status`` to ``ready`` or ``error``, and never raise. Card building
is single-flight across concurrent runs (``_card_lock``). An explore run is
marked by ``runs.routing_decision_json`` = ``EXPLORE_MODE_JSON`` so
``explore_status`` reads the latest outcome (``new_count``) back from the DB
(Ruling U3); ``reconcile_generating_problems`` is the startup sweep for problems
a killed process left ``generating``.

While ``generate_ideas`` runs, its :class:`CardProgress` sits in ``_progress``
(keyed by ``problem_id``, None = explore; in-process) so the polling endpoints
can show "Summarising your documents — ready/total" during the card phase. A
finished run's skipped count is read back from its ``ideation:propose`` step
(``last_skipped_docs``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

import aiosqlite
from pydantic import ValidationError

from paperhub.db.connection import open_db, write_transaction
from paperhub.ideation.cards import ensure_cards
from paperhub.ideation.internal_session import ideation_session_id
from paperhub.ideation.models import (
    CitedField,
    ExploreStatus,
    IdeationOutput,
    MechanismCard,
    ProposedIdea,
    ProposeDraft,
)
from paperhub.ideation.narrow import narrow, tokens
from paperhub.llm.adapter import LlmAdapter
from paperhub.tracing.tracer import Tracer

_LOG = logging.getLogger(__name__)

PROPOSE_SLOT = "ideation_propose/v3"

PROPOSE_MAX_TOKENS = 32768
"""Output cap. Eight ideas are ~1.5k tokens of JSON, but a thinking model
counts its reasoning against the cap: gemini-pro-latest spends 7–10k reasoning
tokens on the ~60-card explore board, so 8192 truncated the JSON (run 705).
The cap still stops a degenerate repetition loop."""
PROPOSE_ATTEMPTS = 2
"""One retry when the reply is not valid JSON / not a valid ``IdeationOutput``."""
PROPOSE_TIMEOUT_S = 300.0
"""Overall bound on the propose call (every attempt together). The context is
the whole library's cards (~60 documents)."""
_BAD_OUTPUT_HEAD = 300

_live_tasks: set[asyncio.Task[None]] = set()
"""Strong refs to in-flight ideation runs so they are not GC'd mid-run."""
_explore_task: asyncio.Task[None] | None = None

_CARD_LOCKS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)
"""One card-building lock per event loop (a module-level ``asyncio.Lock``
would bind to the first test's loop)."""

EXPLORE_MODE_JSON = '{"ideation_mode": "explore"}'
"""``runs.routing_decision_json`` of an explore run, so the explore status is
read back from the run row (the internal session's runs are never routed)."""


def run_scope_json(problem_id: int | None) -> str:
    """``runs.routing_decision_json`` of an ideation run for this scope."""
    if problem_id is None:
        return EXPLORE_MODE_JSON
    return json.dumps({"ideation_mode": "problem", "problem_id": problem_id})


@dataclass
class CardProgress:
    """Card-phase progress of an in-flight run (see ``ensure_cards``)."""

    ready: int = 0
    total: int = 0
    skipped: int = 0


_progress: dict[int | None, CardProgress] = {}


def card_progress(problem_id: int | None) -> CardProgress | None:
    """The scope's progress while ``generate_ideas`` runs for it, else None."""
    return _progress.get(problem_id)


async def last_skipped_docs(conn: aiosqlite.Connection, problem_id: int | None) -> int:
    """How many documents the scope's latest run could not analyse."""
    return (await last_outcome(conn, problem_id)).skipped


@dataclass(frozen=True)
class RunOutcome:
    """What the scope's latest ``ideation:propose`` step recorded."""

    skipped: int = 0
    nothing_relevant: bool = False
    reason: str = ""


def run_outcome(raw: Any) -> RunOutcome:
    """:class:`RunOutcome` of a recorded ``ideation:propose`` result."""
    try:
        result = json.loads(raw) if isinstance(raw, str) and raw else raw
    except ValueError:
        return RunOutcome()
    if not isinstance(result, dict):
        return RunOutcome()
    nothing = result.get("outcome") == "nothing_relevant"
    return RunOutcome(skipped=skipped_count(result), nothing_relevant=nothing,
                      reason=str(result.get("no_fit_reason") or "") if nothing else "")


async def last_outcome(conn: aiosqlite.Connection, problem_id: int | None) -> RunOutcome:
    """The scope's latest run outcome (skipped documents, nothing relevant)."""
    async with conn.execute(
        "SELECT tc.result_summary_json FROM runs r "
        "JOIN tool_calls tc ON tc.run_id = r.id "
        "WHERE r.routing_decision_json = ? AND tc.tool = 'ideation:propose' "
        "ORDER BY r.id DESC, tc.step_index DESC LIMIT 1",
        (run_scope_json(problem_id),),
    ) as cur:
        row = await cur.fetchone()
    return run_outcome(row[0] if row is not None else None)


def skipped_count(raw: Any) -> int:
    """``len(skipped_docs)`` of a recorded ``ideation:propose`` result."""
    try:
        result = json.loads(raw) if isinstance(raw, str) and raw else raw
    except ValueError:
        return 0
    skipped = result.get("skipped_docs") if isinstance(result, dict) else None
    return len(skipped) if isinstance(skipped, list) else 0


def _card_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _CARD_LOCKS.get(loop)
    if lock is None:
        lock = _CARD_LOCKS[loop] = asyncio.Lock()
    return lock


# ---------------------------------------------------------------- rendering


@dataclass(frozen=True)
class BoardCard:
    """One document as the proposer sees it."""

    doc: int
    title: str
    domains: list[str]
    card: MechanismCard


def _norm(name: str) -> str:
    return " ".join(name.split()).casefold()


def card_sections(card: MechanismCard) -> list[str]:
    """Every section the card cites, in field order, de-duplicated."""
    fields = [card.problem, card.mechanism, *card.assumptions, *card.limitations]
    out: list[str] = []
    for f in fields:
        for s in f.sections:
            if s not in out:
                out.append(s)
    return out


def _field_xml(tag: str, f: CitedField) -> str:
    cites = quoteattr("; ".join(f.sections))
    return f"<{tag} cites={cites}>{escape(f.text)}</{tag}>"


def render_card(c: BoardCard) -> str:
    lines = [
        f'<card doc="{c.doc}" title={quoteattr(c.title)} '
        f"domains={quoteattr('; '.join(c.domains))}>",
        _field_xml("problem", c.card.problem),
        _field_xml("mechanism", c.card.mechanism),
        *(_field_xml("assumption", f) for f in c.card.assumptions),
        *(_field_xml("limitation", f) for f in c.card.limitations),
        "</card>",
    ]
    return "\n".join(lines)


def render_cards_xml(cards: list[BoardCard]) -> str:
    """One ``<card doc="id">`` per document — shared verbatim with the eval
    corpus builder (``benchmark/agent/ideation_cards.py``)."""
    return "\n".join(render_card(c) for c in cards)


@dataclass(frozen=True)
class RejectedIdea:
    status: str
    source_doc: int
    source_section: str
    target_doc: int | None
    target_section: str | None
    title: str
    reasons: list[str]
    note: str


def render_rejected_xml(rejected: list[RejectedIdea]) -> str:
    """The scope's dismissed and failed ideas with their reasons; ``""`` when
    there are none (the eval corpus's default)."""
    lines: list[str] = []
    for r in rejected:
        attrs = (
            f"status={quoteattr(r.status)} source_doc=\"{r.source_doc}\" "
            f"source_section={quoteattr(r.source_section)}"
        )
        if r.target_doc is not None:
            attrs += (f' target_doc="{r.target_doc}" '
                      f"target_section={quoteattr(r.target_section or '')}")
        if r.reasons:
            attrs += f" reasons={quoteattr(', '.join(r.reasons))}"
        body = escape(r.title)
        if r.note.strip():
            body += f"<note>{escape(r.note.strip())}</note>"
        lines.append(f"<idea {attrs}>{body}</idea>")
    return "\n".join(lines)


_LANGUAGE_NAMES = {
    "en": "English",
    "zh-tw": "Traditional Chinese (zh-TW)",
    "zh-hant": "Traditional Chinese (zh-TW)",
    "zh-cn": "Simplified Chinese (zh-CN)",
    "zh-hans": "Simplified Chinese (zh-CN)",
    "zh": "Simplified Chinese (zh-CN)",
    "ja": "Japanese",
}


def language_name(tag: str) -> str:
    """The prompt's name for a BCP-47 UI language tag (unknown tags pass
    through, trimmed; blank = English)."""
    clean = " ".join(tag.split())[:40]
    if not clean:
        return "English"
    key = clean.replace("_", "-").casefold()
    return _LANGUAGE_NAMES.get(key) or _LANGUAGE_NAMES.get(key.split("-")[0]) or clean


# ---------------------------------------------------------------- loading


async def _library_ids(conn: aiosqlite.Connection) -> list[int]:
    async with conn.execute("SELECT id FROM paper_content ORDER BY id") as cur:
        return [int(r[0]) for r in await cur.fetchall()]


async def _titles(conn: aiosqlite.Connection) -> dict[int, str]:
    async with conn.execute("SELECT id, title FROM paper_content") as cur:
        return {int(r[0]): str(r[1] or "") for r in await cur.fetchall()}


async def document_domains(conn: aiosqlite.Connection) -> dict[int, list[str]]:
    """Each document's current domain names (manual and auto tags), in the
    order they were assigned: the first is the document's primary field."""
    out: dict[int, list[str]] = {}
    async with conn.execute(
        "SELECT dd.paper_content_id, d.name FROM document_domains dd "
        "JOIN domains d ON d.id = dd.domain_id ORDER BY dd.paper_content_id, dd.rowid",
    ) as cur:
        for pid, name in await cur.fetchall():
            out.setdefault(int(pid), []).append(str(name))
    return out


async def _rejected(
    conn: aiosqlite.Connection, problem_id: int | None,
) -> list[RejectedIdea]:
    scope = "problem_id = ?" if problem_id is not None else "problem_id IS NULL"
    params: tuple[Any, ...] = (problem_id,) if problem_id is not None else ()
    async with conn.execute(
        "SELECT status, source_doc, source_section, target_doc, target_section, "
        "title, dismiss_reasons_json, dismiss_note, note FROM ideas "
        f"WHERE {scope} AND status IN ('dismissed', 'failed') ORDER BY id",
        params,
    ) as cur:
        rows = await cur.fetchall()
    out: list[RejectedIdea] = []
    for (status, sdoc, ssec, tdoc, tsec, title, reasons_raw, dnote, note) in rows:
        try:
            reasons = [str(x) for x in json.loads(reasons_raw or "[]")]
        except ValueError:
            reasons = []
        text = str(dnote or "") if status == "dismissed" else ""
        if note:
            text = f"{text} {note}".strip()
        out.append(RejectedIdea(
            status=str(status), source_doc=int(sdoc), source_section=str(ssec),
            target_doc=int(tdoc) if tdoc is not None else None,
            target_section=str(tsec) if tsec is not None else None,
            title=str(title), reasons=reasons, note=text,
        ))
    return out


async def _existing_keys(
    conn: aiosqlite.Connection, problem_id: int | None,
) -> dict[tuple[int, str, int | None, str], str]:
    """(source, section, target, target section) -> status of every idea
    already in the scope."""
    scope = "problem_id = ?" if problem_id is not None else "problem_id IS NULL"
    params: tuple[Any, ...] = (problem_id,) if problem_id is not None else ()
    async with conn.execute(
        "SELECT source_doc, source_section, target_doc, target_section, status "
        f"FROM ideas WHERE {scope}", params,
    ) as cur:
        rows = await cur.fetchall()
    return {
        (int(s), _norm(str(ss)), int(t) if t is not None else None,
         _norm(str(ts or ""))): str(st)
        for s, ss, t, ts, st in rows
    }


# ---------------------------------------------------------------- the call


class IdeationCallFailed(Exception):
    """The proposer produced no usable reply: ``kind`` is ``invalid_json``,
    ``invalid_output``, ``timeout`` or ``provider_error``; ``output_head`` is
    the start of the last bad output (the model's text, never the prompt)."""

    def __init__(self, kind: str, attempts: int, output_head: str | None) -> None:
        super().__init__(f"{kind} after {attempts} attempt(s)")
        self.kind = kind
        self.attempts = attempts
        self.output_head = output_head

    def summary(self) -> dict[str, Any]:
        return {"failure": self.kind, "attempts": self.attempts,
                "output_head": self.output_head}


def _validation_failure(exc: ValidationError) -> tuple[str, str]:
    errors = exc.errors()
    kind = ("invalid_json" if any(e.get("type") == "json_invalid" for e in errors)
            else "invalid_output")
    raw = errors[0].get("input") if errors else None
    if not isinstance(raw, str):
        raw = json.dumps(raw, ensure_ascii=False, default=str)
    return kind, raw[:_BAD_OUTPUT_HEAD]


def thinking_kwargs(budget: int) -> dict[str, Any]:
    """Provider kwargs that cap a thinking model's reasoning at ``budget``
    tokens (LiteLLM ``thinking`` → Gemini ``thinkingBudget``); ``{}`` for 0
    (the provider's default, unbounded). ``drop_params`` keeps a model
    without the setting (e.g. a fallback tier) from rejecting the call."""
    if budget <= 0:
        return {}
    return {"thinking": {"type": "enabled", "budget_tokens": budget}, "drop_params": True}


async def _ask(
    adapter: LlmAdapter, model: str, variables: dict[str, Any], thinking_budget: int = 0,
) -> tuple[IdeationOutput, int]:
    attempts = 0
    last: tuple[str, str] | None = None
    try:
        async with asyncio.timeout(PROPOSE_TIMEOUT_S):
            while attempts < PROPOSE_ATTEMPTS:
                attempts += 1
                try:
                    raw = await adapter.structured(
                        slot=PROPOSE_SLOT, variables=variables,
                        response_model=ProposeDraft, model=model,
                        max_tokens=PROPOSE_MAX_TOKENS, **thinking_kwargs(thinking_budget),
                    )
                    out = IdeationOutput.model_validate(raw, from_attributes=True)
                    return out, attempts
                except ValidationError as exc:
                    last = _validation_failure(exc)
                    _LOG.warning("ideation propose attempt %d: %s", attempts, last[0])
    except TimeoutError:
        raise IdeationCallFailed("timeout", attempts, last[1] if last else None) from None
    except IdeationCallFailed:
        raise
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — recorded on the step, re-raised
        raise IdeationCallFailed(
            "provider_error", attempts, f"{type(exc).__name__}: {exc}"[:_BAD_OUTPUT_HEAD],
        ) from exc
    assert last is not None
    raise IdeationCallFailed(last[0], attempts, last[1])


# ---------------------------------------------------------------- validation


def _canonical_section(name: str | None, card: MechanismCard) -> str | None:
    """The card's spelling of ``name``. The model sometimes copies a whole
    ``cites`` list ("A; B"): the first listed name the card cites is used —
    still a real citation, never a guess."""
    if not name:
        return None
    cited = {_norm(s): s for s in card_sections(card)}
    for part in (name, *name.split(";")):
        hit = cited.get(_norm(part))
        if hit is not None:
            return hit
    return None


def _check(
    idea: ProposedIdea, *, mode: str, cards: dict[int, MechanismCard],
    domains: dict[int, list[str]],
) -> tuple[str | None, str | None, str | None]:
    """``(reason, source_section, target_section)``: reason is None when the
    idea is valid; the sections come back in their canonical spelling."""
    if not idea.title.strip():
        return "empty title", None, None
    src = cards.get(idea.source_doc)
    if src is None:
        return f"unknown source_doc {idea.source_doc}", None, None
    ssec = _canonical_section(idea.source_section, src)
    if ssec is None:
        return f"source_section {idea.source_section!r} not cited by its card", None, None
    if mode == "problem":
        if idea.target_doc is not None:
            return "target_doc set in problem mode", None, None
        return None, ssec, None
    if idea.target_doc is None:
        return "explore idea without a target_doc", None, None
    tgt = cards.get(idea.target_doc)
    if tgt is None:
        return f"unknown target_doc {idea.target_doc}", None, None
    if idea.target_doc == idea.source_doc:
        return "source and target are the same document", None, None
    tsec = _canonical_section(idea.target_section, tgt)
    if tsec is None:
        return f"target_section {idea.target_section!r} not cited by its card", None, None
    reason = cross_domain_reason(domains.get(idea.source_doc, []),
                                 domains.get(idea.target_doc, []))
    if reason is not None:
        return reason, None, None
    return None, ssec, tsec


def cross_domain_reason(source: list[str], target: list[str]) -> str | None:
    """Why an explore pair is not cross-domain, or None when it is: the
    target's primary field (its first domain) must be one the source is not
    in. Domain tags are multi-label and coarse — a VLA paper is robotics,
    vision and language at once — so "share no domain" would rule out most of
    an AI-heavy library; a language mechanism into a robotics paper still
    counts. An untagged target cannot be judged and is kept."""
    if not target:
        return None
    primary = _norm(target[0])
    if primary in {_norm(d) for d in source}:
        return f"same domain: the target's primary domain ({primary}) is also the source's"
    return None


# ---------------------------------------------------------------- generate


async def _problem_text(
    conn: aiosqlite.Connection, problem_id: int,
) -> tuple[str, str]:
    async with conn.execute(
        "SELECT text, tried FROM problems WHERE id = ?", (problem_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise LookupError(f"problem {problem_id} not found")
    return str(row[0]), str(row[1] or "")


async def generate_ideas(
    conn: aiosqlite.Connection,
    *,
    problem_id: int | None,
    adapter: LlmAdapter,
    model: str,
    card_model: str,
    tracer: Tracer,
    thinking_budget: int = 0,
    language: str = "en",
) -> list[int]:
    """Propose, validate and store ideas for a problem (or free exploration);
    returns the kept idea ids in rank order. Raises on an LLM failure (after
    recording it on the ``ideation:propose`` step). Publishes its card
    progress in ``_progress`` while it runs."""
    progress = CardProgress()
    _progress[problem_id] = progress
    try:
        return await _generate_ideas(
            conn, problem_id=problem_id, adapter=adapter, model=model,
            card_model=card_model, tracer=tracer, progress=progress,
            thinking_budget=thinking_budget, language=language,
        )
    finally:
        if _progress.get(problem_id) is progress:
            del _progress[problem_id]


async def _generate_ideas(
    conn: aiosqlite.Connection,
    *,
    problem_id: int | None,
    adapter: LlmAdapter,
    model: str,
    card_model: str,
    tracer: Tracer,
    progress: CardProgress,
    thinking_budget: int = 0,
    language: str = "en",
) -> list[int]:
    def on_progress(done: int, total: int, skipped_n: int) -> None:
        progress.ready, progress.total, progress.skipped = done, total, skipped_n

    mode = "problem" if problem_id is not None else "explore"
    problem, tried = ("", "")
    if problem_id is not None:
        problem, tried = await _problem_text(conn, problem_id)

    ids = await _library_ids(conn)
    # Single flight: a concurrent run (two problems, or a problem + explore)
    # waits here, then reuses the first run's digests and cards (cache hits)
    # instead of building every missing digest a second time.
    async with _card_lock():
        cards = await ensure_cards(conn, ids, adapter=adapter, model=card_model,
                                   tracer=tracer, on_progress=on_progress)
    # A card that cites no section (its paper has none, e.g. a short letter)
    # can never back a kept idea, so it is left off the board.
    uncitable = [pid for pid in ids if pid in cards and not card_sections(cards[pid])]
    skipped = [pid for pid in ids if pid not in cards or pid in uncitable]
    titles = await _titles(conn)
    domains = await document_domains(conn)
    board = [BoardCard(doc=pid, title=titles.get(pid, ""), domains=domains.get(pid, []),
                       card=cards[pid]) for pid in ids if pid in cards and pid not in uncitable]
    rejected = await _rejected(conn, problem_id)
    prior_ideas = await _existing_ideas(conn, problem_id)
    variables = {
        "problem": problem,
        "tried": tried,
        "cards_xml": render_cards_xml(board),
        "rejected_xml": render_rejected_xml(rejected),
        # The scope's live ideas, so a re-run does not restate them (QA I9).
        "existing_xml": render_existing_xml(prior_ideas),
        "mode": mode,
        # Problem mode answers in the problem's own language (empty = "the
        # language <problem> is written in"); explore has no problem text, so
        # it uses the client's UI language.
        "response_language": "" if mode == "problem" else language_name(language),
    }

    async with tracer.step(agent="ideation", tool="ideation:propose", model=model) as step:
        step.record_args({**variables, "problem_id": problem_id,
                          "thinking_budget": thinking_budget})
        try:
            output, attempts = await _ask(adapter, model, variables, thinking_budget)
        except IdeationCallFailed as exc:
            step.record_result({**exc.summary(), "card_ids_read": [c.doc for c in board],
                                "skipped_docs": skipped})
            raise

        existing = await _existing_keys(conn, problem_id)
        valid, dropped = await _validate(conn, output, mode=mode, cards=cards,
                                         domains=domains, existing=existing,
                                         existing_titles=prior_ideas)
        repair: dict[str, Any] | None = None
        if output.ideas and not valid:
            # Every proposal was dropped: re-ask once, showing the dropped
            # ideas and why in <rejected_ideas> so the model can correct them.
            retry_xml = render_rejected_xml([*rejected, *_as_rejected(dropped)])
            try:
                retry, more = await _ask(adapter, model, {**variables,
                                                          "rejected_xml": retry_xml},
                                         thinking_budget)
            except IdeationCallFailed as exc:
                repair = {"rejected_xml": retry_xml, **exc.summary()}
            else:
                attempts += more
                valid, retry_dropped = await _validate(
                    conn, retry, mode=mode, cards=cards, domains=domains, existing=existing,
                    existing_titles=prior_ideas)
                repair = {"rejected_xml": retry_xml,
                          "proposed": [i.model_dump() for i in retry.ideas],
                          "dropped": retry_dropped}

        kept: list[dict[str, Any]] = []
        async with write_transaction(conn):
            # Re-check inside the serialised write: a concurrent run may have
            # stored the same idea since ``existing`` was read (that read and
            # the LLM calls happen outside any write lock).
            stored = await _existing_keys(conn, problem_id)
            fresh = [e for e in await _existing_ideas(conn, problem_id)
                     if e.ref not in {p.ref for p in prior_ideas}]
            for rank, (idea, ssec, tsec, src_chunks, tgt_chunks) in enumerate(valid):
                key = (idea.source_doc, _norm(ssec), idea.target_doc
                       if mode == "explore" else None, _norm(tsec or ""))
                near = near_duplicate(idea, fresh, mode=mode)
                if key in stored or near is not None:
                    dropped.append({"rank": rank, **idea.model_dump(),
                                    "reason": "duplicate" if key in stored
                                    else f"near-duplicate of idea {near}"})
                    continue
                stored[key] = "new"
                cur = await conn.execute(
                    "INSERT INTO ideas (problem_id, source_doc, source_section, "
                    "source_chunk_ids_json, target_doc, target_section, "
                    "target_chunk_ids_json, title, rationale, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')",
                    (
                        problem_id, idea.source_doc, ssec, json.dumps(src_chunks),
                        idea.target_doc if mode == "explore" else None, tsec,
                        json.dumps(tgt_chunks), idea.title.strip(), idea.rationale.strip(),
                    ),
                )
                assert cur.lastrowid is not None
                kept.append({"id": int(cur.lastrowid), "source_section": ssec,
                             "source_chunk_ids": src_chunks, "target_section": tsec,
                             "target_chunk_ids": tgt_chunks})
                fresh.append(ExistingIdea(
                    ref=str(int(cur.lastrowid)), source_doc=idea.source_doc,
                    target_doc=idea.target_doc if mode == "explore" else None,
                    title=idea.title))

        evidence = sorted({c for k in kept
                           for c in (*k["source_chunk_ids"], *k["target_chunk_ids"])})
        # QA I2: the proposer says when nothing in the library applies (no
        # ideas + a reason); that outcome is shown to the user.
        no_fit = output.no_fit_reason.strip() if not output.ideas else ""
        outcome = ("ideas" if kept else "nothing_relevant" if no_fit else "nothing_new")
        step.record_result({
            "outcome": outcome,
            "no_fit_reason": no_fit,
            "proposed": [i.model_dump() for i in output.ideas],
            "kept_ids": [k["id"] for k in kept],
            "kept": kept,
            "dropped": dropped,
            "card_ids_read": [c.doc for c in board],
            "skipped_docs": skipped,
            "uncitable_docs": uncitable,
            "evidence_chunk_ids": evidence,
            "new_count": len(kept),
            "attempts": attempts,
            **({"repair": repair} if repair is not None else {}),
        })
    return [k["id"] for k in kept]


_Valid = tuple[ProposedIdea, str, "str | None", list[int], list[int]]

NEAR_DUP_OVERLAP = 0.5
"""Title-token Jaccard at or above which two problem-mode ideas from the same
source document restate each other."""


@dataclass(frozen=True)
class ExistingIdea:
    """An idea already in the scope (or kept earlier in this run), as the
    near-duplicate check and ``<existing_ideas>`` see it."""

    ref: str
    source_doc: int
    target_doc: int | None
    title: str
    source_section: str = ""
    target_section: str | None = None
    status: str = "new"


def near_duplicate(
    idea: ProposedIdea, existing: list[ExistingIdea], *, mode: str,
) -> str | None:
    """The ``ref`` of an idea ``idea`` restates, else None (QA I9). Explore:
    the same ordered document pair (a second pair idea from other sections
    retells the first). Problem mode: the same source document and a title
    whose tokens overlap by ``NEAR_DUP_OVERLAP`` or more."""
    words = set(tokens(idea.title))
    for e in existing:
        if e.source_doc != idea.source_doc:
            continue
        if mode == "explore":
            if e.target_doc is not None and e.target_doc == idea.target_doc:
                return e.ref
            continue
        other = set(tokens(e.title))
        if words and other and len(words & other) / len(words | other) >= NEAR_DUP_OVERLAP:
            return e.ref
    return None


async def _existing_ideas(
    conn: aiosqlite.Connection, problem_id: int | None,
) -> list[ExistingIdea]:
    """Every idea already in the scope, any status, in id order."""
    scope = "problem_id = ?" if problem_id is not None else "problem_id IS NULL"
    params: tuple[Any, ...] = (problem_id,) if problem_id is not None else ()
    async with conn.execute(
        "SELECT id, source_doc, target_doc, title, source_section, target_section, "
        f"status FROM ideas WHERE {scope} ORDER BY id", params,
    ) as cur:
        rows = await cur.fetchall()
    return [ExistingIdea(ref=str(int(r[0])), source_doc=int(r[1]),
                         target_doc=int(r[2]) if r[2] is not None else None,
                         title=str(r[3]), source_section=str(r[4]),
                         target_section=str(r[5]) if r[5] is not None else None,
                         status=str(r[6]))
            for r in rows]


def render_existing_xml(existing: list[ExistingIdea]) -> str:
    """The scope's live (not dismissed / failed) ideas, so the proposer does
    not restate them; ``""`` when there are none."""
    lines: list[str] = []
    for e in existing:
        if e.status in ("dismissed", "failed"):
            continue  # already in <rejected_ideas>
        attrs = (f'source_doc="{e.source_doc}" '
                 f"source_section={quoteattr(e.source_section)}")
        if e.target_doc is not None:
            attrs += (f' target_doc="{e.target_doc}" '
                      f"target_section={quoteattr(e.target_section or '')}")
        lines.append(f"<idea {attrs}>{escape(e.title)}</idea>")
    return "\n".join(lines)


def cited_parts(name: str | None, card: MechanismCard) -> list[str]:
    """Every section of ``name`` the card cites, canonical spelling, in order:
    one name, or each part of a copied ``"A; B"`` cites list."""
    if not name:
        return []
    cited = {_norm(s): s for s in card_sections(card)}
    whole = cited.get(_norm(name))
    if whole is not None:
        return [whole]
    out: list[str] = []
    for part in name.split(";"):
        hit = cited.get(_norm(part))
        if hit is not None and hit not in out:
            out.append(hit)
    return out


def field_text(card: MechanismCard, sections: list[str]) -> str:
    """The card's field texts that cite any of ``sections`` (the words the
    citation narrowing scores the section's chunks against)."""
    want = {_norm(s) for s in sections}
    fields = [card.problem, card.mechanism, *card.assumptions, *card.limitations]
    return " ".join(f.text for f in fields if want & {_norm(s) for s in f.sections})


async def _cite(
    conn: aiosqlite.Connection, doc: int, card: MechanismCard, proposed: str | None,
    canonical: str, idea: ProposedIdea,
) -> tuple[str, list[int], bool]:
    """``(section, chunk_ids, narrowed)`` of one end of an idea: the chunks of
    the cited section(s) that carry the mechanism (``narrow``), else the whole
    section; ``([], False)`` chunks when the section has none."""
    parts = cited_parts(proposed, card) or [canonical]
    query = f"{field_text(card, parts)} {idea.title} {idea.rationale}"
    got = await narrow(conn, paper_content_id=doc, sections=parts, query_text=query)
    if got is None:
        return canonical, [], False
    return got.section, got.chunk_ids, got.narrowed


async def _validate(
    conn: aiosqlite.Connection, output: IdeationOutput, *, mode: str,
    cards: dict[int, MechanismCard], domains: dict[int, list[str]],
    existing: dict[tuple[int, str, int | None, str], str],
    existing_titles: list[ExistingIdea] | None = None,
) -> tuple[list[_Valid], list[dict[str, Any]]]:
    """``(valid, dropped)``: the proposals that pass ``_check``, are new to the
    scope (no exact or near duplicate) and cite sections with chunks (narrowed
    to the chunks that carry the mechanism), and the rest with their drop
    reason."""
    dropped: list[dict[str, Any]] = []
    valid: list[_Valid] = []
    seen: set[tuple[int, str, int | None, str]] = set()
    titles: list[ExistingIdea] = list(existing_titles or [])
    for rank, idea in enumerate(output.ideas):
        reason, ssec, tsec = _check(idea, mode=mode, cards=cards, domains=domains)
        key: tuple[int, str, int | None, str] | None = None
        src_chunks: list[int] = []
        tgt_chunks: list[int] = []
        if reason is None:
            assert ssec is not None
            key = (idea.source_doc, _norm(ssec), idea.target_doc
                   if mode == "explore" else None, _norm(tsec or ""))
            prior = existing.get(key)
            if prior in ("dismissed", "failed"):
                reason = f"repeats a {prior} idea"
            elif prior is not None or key in seen:
                reason = "duplicate"
            else:
                near = near_duplicate(idea, titles, mode=mode)
                if near is not None:
                    reason = f"near-duplicate of idea {near}"
        if reason is None:
            assert ssec is not None
            ssec, src_chunks, _n = await _cite(
                conn, idea.source_doc, cards[idea.source_doc], idea.source_section,
                ssec, idea)
            if tsec is not None and idea.target_doc is not None:
                tsec, tgt_chunks, _n = await _cite(
                    conn, idea.target_doc, cards[idea.target_doc], idea.target_section,
                    tsec, idea)
            if not src_chunks or (tsec is not None and not tgt_chunks):
                reason = "cited section has no chunks"
        if reason is not None:
            dropped.append({"rank": rank, **idea.model_dump(), "reason": reason})
            continue
        assert ssec is not None and key is not None
        seen.add(key)
        titles.append(ExistingIdea(
            ref=f"#{rank + 1} of this run", source_doc=idea.source_doc,
            target_doc=idea.target_doc if mode == "explore" else None,
            title=idea.title))
        valid.append((idea, ssec, tsec, src_chunks, tgt_chunks))
    return valid, dropped


def _as_rejected(dropped: list[dict[str, Any]]) -> list[RejectedIdea]:
    """Dropped proposals as ``status="invalid"`` entries of <rejected_ideas>."""
    return [RejectedIdea(
        status="invalid", source_doc=int(d["source_doc"]),
        source_section=str(d.get("source_section") or ""),
        target_doc=d.get("target_doc"), target_section=d.get("target_section"),
        title=str(d.get("title") or ""), reasons=[str(d["reason"])], note="",
    ) for d in dropped]


# ---------------------------------------------------------------- background


async def _finish(
    conn: aiosqlite.Connection, run_id: int, status: str,
    problem_id: int | None, problem_status: str | None,
) -> None:
    async with write_transaction(conn):
        await conn.execute(
            "UPDATE runs SET status = ?, finished_at = datetime('now') WHERE id = ?",
            (status, run_id),
        )
        if problem_id is not None and problem_status is not None:
            await conn.execute(
                "UPDATE problems SET status = ? WHERE id = ?", (problem_status, problem_id),
            )


async def run_ideation(
    *, db_path: Path, problem_id: int | None, adapter: LlmAdapter,
    model: str, card_model: str, thinking_budget: int = 0, language: str = "en",
) -> None:
    """The background task body. Never raises (except a cancellation)."""
    run_id: int | None = None
    try:
        async with open_db(db_path) as conn:
            session_id = await ideation_session_id(conn)
            async with write_transaction(conn):
                ins = await conn.execute(
                    "INSERT INTO runs (session_id, status, routing_decision_json) "
                    "VALUES (?, 'running', ?)",
                    (session_id, run_scope_json(problem_id)),
                )
                assert ins.lastrowid is not None
                run_id = int(ins.lastrowid)
            tracer = Tracer(conn, run_id=run_id, branch="")
            try:
                await generate_ideas(
                    conn, problem_id=problem_id, adapter=adapter, model=model,
                    card_model=card_model, tracer=tracer, thinking_budget=thinking_budget,
                    language=language,
                )
            except asyncio.CancelledError:
                # A fresh connection: card builds cancelled with us may still be
                # writing their error steps on ``conn`` (gather returns before
                # its children finish cleaning up), so ``conn`` can be mid-
                # transaction here.
                async with open_db(db_path) as fresh:
                    await _finish(fresh, run_id, "cancelled", problem_id, "error")
                raise
            except Exception:  # noqa: BLE001 — the tracer recorded the error step
                _LOG.warning("ideation run %s (problem %s) failed", run_id, problem_id,
                             exc_info=True)
                await _finish(conn, run_id, "error", problem_id, "error")
                return
            await _finish(conn, run_id, "ok", problem_id, "ready")
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a background run must never surface
        _LOG.warning("ideation run for problem %s could not run", problem_id,
                     exc_info=True)
        try:
            async with open_db(db_path) as conn:
                if run_id is not None:
                    await _finish(conn, run_id, "error", problem_id, "error")
                elif problem_id is not None:
                    async with write_transaction(conn):
                        await conn.execute(
                            "UPDATE problems SET status = 'error' WHERE id = ?",
                            (problem_id,),
                        )
        except Exception:  # noqa: BLE001
            _LOG.warning("could not mark ideation run %s error", run_id)


def _spawn(coro: Any) -> asyncio.Task[None]:
    task: asyncio.Task[None] = asyncio.create_task(coro)
    _live_tasks.add(task)
    task.add_done_callback(_live_tasks.discard)
    return task


def run_problem(
    problem_id: int, *, db_path: Path, adapter: LlmAdapter, model: str, card_model: str,
    thinking_budget: int = 0,
) -> asyncio.Task[None]:
    """Start a problem's idea generation in the background; returns the task
    at once (awaiting it is optional)."""
    return _spawn(run_ideation(
        db_path=db_path, problem_id=problem_id, adapter=adapter, model=model,
        card_model=card_model, thinking_budget=thinking_budget,
    ))


def run_explore(
    *, db_path: Path, adapter: LlmAdapter, model: str, card_model: str,
    thinking_budget: int = 0, language: str = "en",
) -> asyncio.Task[None]:
    """Start free-exploration generation in the background. A second call
    while one is in flight returns the running task instead of a duplicate.
    ``language`` is the client's UI language (BCP-47): explore ideas have no
    problem text to take their language from."""
    global _explore_task
    if _explore_task is not None and not _explore_task.done():
        return _explore_task
    _explore_task = _spawn(run_ideation(
        db_path=db_path, problem_id=None, adapter=adapter, model=model,
        card_model=card_model, thinking_budget=thinking_budget, language=language,
    ))
    return _explore_task


def explore_running() -> bool:
    """An explore run is in flight in this process."""
    return _explore_task is not None and not _explore_task.done()


async def explore_status(conn: aiosqlite.Connection) -> ExploreStatus:
    """The latest explore run's outcome: when it ran, how many new ideas it
    kept (``new_count`` of its ``ideation:propose`` step) and why it failed."""
    async with conn.execute(
        "SELECT id, status, started_at, finished_at FROM runs "
        "WHERE routing_decision_json = ? ORDER BY id DESC LIMIT 1",
        (EXPLORE_MODE_JSON,),
    ) as cur:
        run = await cur.fetchone()
    if run is None:
        progress = card_progress(None)
        return ExploreStatus(
            running=explore_running(),
            cards_ready=progress.ready if progress else None,
            cards_total=progress.total if progress else None,
            skipped_docs=progress.skipped if progress else 0,
        )
    run_id, status, started_at, finished_at = int(run[0]), str(run[1]), run[2], run[3]
    async with conn.execute(
        "SELECT status, error, result_summary_json FROM tool_calls "
        "WHERE run_id = ? AND tool = 'ideation:propose' ORDER BY step_index DESC LIMIT 1",
        (run_id,),
    ) as cur:
        step = await cur.fetchone()
    new_count: int | None = None
    error: str | None = None
    skipped = skipped_count(step[2]) if step is not None else 0
    if step is not None:
        try:
            result = json.loads(step[2]) if step[2] else {}
        except ValueError:
            result = {}
        if isinstance(result, dict):
            n = result.get("new_count")
            new_count = n if isinstance(n, int) else None
            if step[0] == "error":
                error = str(result.get("failure") or step[1] or "error")
    if error is None and status in ("error", "cancelled", "interrupted"):
        error = status
    running = status == "running" or explore_running()
    progress = card_progress(None) if running else None
    outcome = run_outcome(step[2]) if step is not None and not running else RunOutcome()
    return ExploreStatus(
        nothing_relevant=outcome.nothing_relevant,
        nothing_relevant_reason=outcome.reason,
        running=running,
        last_run_at=str(finished_at or started_at) if not running else str(started_at),
        last_new_count=None if running else new_count,
        last_error=None if running else error,
        cards_ready=progress.ready if progress else None,
        cards_total=progress.total if progress else None,
        skipped_docs=(progress.skipped if progress else 0) if running else skipped,
    )


async def reconcile_generating_problems(conn: aiosqlite.Connection) -> int:
    """Startup sweep: at boot no generation is in flight, so a problem still
    ``generating`` was left by a killed process — mark it ``error`` (the user
    can re-run it). Returns how many were moved."""
    async with write_transaction(conn):
        cur = await conn.execute(
            "UPDATE problems SET status = 'error' WHERE status = 'generating'",
        )
    return int(cur.rowcount or 0)
