"""The outbound checkpoint: one review function every text-bearing outbound
request passes before it leaves (Plan H5a, SRS §III-10.5 H5a).

Stages, cheapest first: destination allowlist → the user's approval (a
held request the user sent, single use) → review switch → sensitive terms →
exact identifier → private-title guard → cache → LLM review →
verbatim-span and private-title guard. Failures and timeouts
follow the unsure policy; nothing is ever silently allowed on failure.
Every decision writes one ``outbound_log`` row (the allowlist stage logs its
own) and, with a tracer, one ``outbound:review`` trace step.
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any

import aiosqlite

from paperhub.db.connection import write_lock
from paperhub.llm.adapter import LlmAdapter
from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.outbound import held, log
from paperhub.outbound.context import current_private, material_incomplete, private_windows
from paperhub.outbound.destinations import PUBLIC_WEB_SEARCH, OutboundBlocked, check_destination
from paperhub.outbound.log import utc_now
from paperhub.outbound.models import (
    Action,
    Decision,
    OutboundRequest,
    PrivateItem,
    ReviewVerdict,
    Stage,
    parse_note_ref,
)
from paperhub.outbound.policy import OutboundPolicy, load_policy
from paperhub.outbound.spans import contains_title, guarded_title, windows
from paperhub.tracing.tracer import Tracer

logger = logging.getLogger(__name__)

REVIEW_SLOT = "outbound_review/v1"
CACHE_TTL = timedelta(days=30)

_EXACT_IDS = (
    re.compile(r"^(arXiv:)?\d{4}\.\d{4,5}(v\d+)?$", re.IGNORECASE),
    re.compile(r"^10\.\d{4,9}/\S+$"),
    re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE),  # Semantic Scholar paperId
)
_LOG_DECISION: dict[Action, str] = {"ask": "held", "hold": "held", "block": "blocked"}


def is_exact_id(text: str) -> bool:
    t = text.strip()
    return any(p.match(t) for p in _EXACT_IDS)


_ASCII_WORD = re.compile(r"[A-Za-z0-9_]")


def _term_pattern(term: str) -> re.Pattern[str]:
    """Case-insensitive and word-bounded on letters only: a trailing ``'s``,
    ``s``, ``-``, ``_`` or digit still matches ("Kestrel's", "Kestrels",
    "KESTREL-v3", "KESTREL_v3", "Kestrel3") but a longer word does not
    ("kestrelfish"). Words of a multi-word term may be joined by any run of
    spaces, ``-`` or ``_`` ("Acme Corp", "Acme-Corp", "Acme_Corp"). Edges that
    are not ASCII word characters (CJK, symbols) get no boundary, since such
    scripts have no word separators."""
    t = term.strip()
    body = r"[\s\-_]+".join(re.escape(w) for w in re.split(r"[\s\-_]+", t) if w)
    head = r"(?<![^\W\d_])(?<!\d)" if _ASCII_WORD.match(t[0]) else ""
    tail = r"s?(?![^\W\d_])" if _ASCII_WORD.match(t[-1]) else ""
    return re.compile(head + body + tail, re.IGNORECASE)


def find_term(text: str, terms: tuple[str, ...]) -> str | None:
    for term in terms:
        if re.sub(r"[\s\-_]+", "", term) and _term_pattern(term).search(text):
            return term.strip()
    return None


def format_private(items: list[PrivateItem]) -> str:
    """Wrap each item as data; ``<``/``>``/quotes are escaped so item text or
    titles can never close the tags."""
    body = "".join(
        f'<private_item kind="{escape(i.kind)}" ref="{escape(i.ref)}" '
        f'title="{escape(i.title)}">{escape(i.text, quote=False)}</private_item>\n'
        for i in items
    )
    return f"<private_material>\n{body}</private_material>"


def review_variables(
    req: OutboundRequest, items: list[PrivateItem], terms: tuple[str, ...],
) -> dict[str, str]:
    """The ``outbound_review/v1`` template inputs (the eval runner reuses this)."""
    return {
        "query": escape(req.text, quote=False),
        "destination": escape(f"{req.kind} ({req.destination})", quote=False),
        "private_material": format_private(items),
        "sensitive_terms": escape(", ".join(terms), quote=False) or "(none)",
    }


@functools.cache
def _review_prompt_sha() -> str:
    """The review prompt's text, hashed (a slot edited in place on a branch
    still gets new cache keys; the process reloads on a code change)."""
    slot = PromptRegistry().get(REVIEW_SLOT)
    return hashlib.sha256(f"{slot.system}|{slot.user_template}".encode()).hexdigest()[:16]


def _cache_key(kind: str, text: str, refs: list[str], model: str) -> str:
    """Per destination kind, text and private set; the prompt slot, its text
    and the review model are part of the key so a prompt or model change
    never reuses old decisions."""
    raw = (f"{REVIEW_SLOT}|{_review_prompt_sha()}|{model}|{kind}|{text}|"
           f"{','.join(sorted(refs))}")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _cache_get(conn: aiosqlite.Connection, key: str) -> dict[str, Any] | None:
    cutoff = (datetime.now(UTC) - CACHE_TTL).isoformat(timespec="seconds")
    async with conn.execute(
        "SELECT decision_json FROM outbound_review_cache WHERE key = ? AND created_at >= ?",
        (key, cutoff),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("action") not in ("send", "block"):
        return None
    return data


async def _cache_put(conn: aiosqlite.Connection, key: str, data: dict[str, Any]) -> None:
    async with write_lock(conn):
        await conn.execute(
            "INSERT OR REPLACE INTO outbound_review_cache (key, decision_json, created_at) "
            "VALUES (?, ?, ?)",
            (key, json.dumps(data), utc_now()),
        )
        await conn.commit()


@dataclass
class _Outcome:
    action: Action
    sent_text: str | None
    stage: Stage
    reason: str
    term: str | None = None
    #: The private items a span-stage hit copies (ids only, see ``_span_sources``).
    sources: list[dict[str, Any]] | None = None


_SPAN_REASON = "contains a passage from private material"
_TITLE_REASON = "contains the title of a private document"


def _echoes(text: str, items: list[PrivateItem]) -> bool:
    """``text`` copies a passage of this turn's private material (the window
    set is built once per turn, see ``context.private_windows``)."""
    return bool(items) and not private_windows().isdisjoint(windows(text))


def names_private_title(
    text: str, items: list[PrivateItem], *, any_order: bool = False,
) -> bool:
    """``text`` contains the title of a private document of this turn (its
    document item or a chunk read from it); short titles are not guarded.
    ``any_order``: also the title's words reordered (the review's rewrite)."""
    for item in items:
        if item.kind in ("document", "chunk"):
            title = guarded_title(item.title)
            if title is not None and contains_title(text, title, any_order=any_order):
                return True
    return False


async def _library_private_titles(conn: aiosqlite.Connection) -> list[PrivateItem]:
    """Every private document in the library as a title-only item (the
    title guard; its text is never read here)."""
    async with conn.execute(
        "SELECT id, title FROM paper_content WHERE is_private = 1"
    ) as cur:
        return [PrivateItem("document", f"doc:{r[0]}", str(r[1]), "")
                for r in await cur.fetchall()]


def _span_leak(
    text: str, items: list[PrivateItem], *, any_order: bool = False,
) -> str | None:
    """The span-stage reason ``text`` may not leave, or None. The reason is
    a constant: it never echoes the title or the passage."""
    if names_private_title(text, items, any_order=any_order):
        return _TITLE_REASON
    if _echoes(text, items):
        return _SPAN_REASON
    return None


def _span_sources(text: str, items: list[PrivateItem]) -> list[dict[str, Any]]:
    """Which items ``text`` copies a passage of, by id only (never the text):
    a note as ``{kind, note_id, block_ids, comment_ids}`` (empty lists left
    out), any other item as ``{kind, ref}``."""
    mine = set(windows(text))
    out: list[dict[str, Any]] = []
    notes: dict[int, dict[str, Any]] = {}
    for item in items:
        if mine.isdisjoint(windows(item.text)):
            continue
        parsed = parse_note_ref(item.ref) if item.kind == "note" else None
        if parsed is None:
            src = {"kind": item.kind, "ref": item.ref}
            if src not in out:
                out.append(src)
            continue
        note_id, part, item_id = parsed
        if note_id not in notes:
            notes[note_id] = {"kind": "note", "note_id": note_id}
            out.append(notes[note_id])
        ids = notes[note_id].setdefault(f"{part}_ids", [])
        if item_id and item_id not in ids:
            ids.append(item_id)
    return out


def _sources_text(sources: list[dict[str, Any]]) -> str:
    """``sources`` for the log's reason: ``note 3: block b_x, comment c_y``."""
    parts: list[str] = []
    for src in sources:
        if src["kind"] != "note":
            parts.append(f"{src['kind']} {src['ref']}")
            continue
        ids = [f"{part} {i}" for part in ("block", "comment", "title")
               for i in src.get(f"{part}_ids", [])]
        parts.append(f"note {src['note_id']}" + (f": {', '.join(ids)}" if ids else ""))
    return "; ".join(parts)


def _span_outcome(policy: OutboundPolicy, req: OutboundRequest, text: str,
                  reason: str, items: list[PrivateItem]) -> _Outcome:
    """The unsure policy at stage ``span``; a passage hit names its sources."""
    out = _unsure(policy, req, "span", reason)
    if reason == _SPAN_REASON:
        out.sources = _span_sources(text, items)
    return out


_WITHHELD_REASON = "reason withheld: it repeated private material"


def _safe_reason(reason: str, items: list[PrivateItem], terms: tuple[str, ...]) -> str:
    """The review's free-text reason, or a constant when it repeats a
    private title, a private passage or a sensitive term. The reason reaches
    the log, the held row and the chat card; the raw verdict stays only in
    the local trace (R50)."""
    if _span_leak(reason, items, any_order=True) is not None or find_term(reason, terms):
        return _WITHHELD_REASON
    return reason


def _unsure(policy: OutboundPolicy, req: OutboundRequest, stage: Stage, reason: str) -> _Outcome:
    if policy.unsure == "block":
        return _Outcome("block", None, stage, reason)
    return _Outcome("hold" if req.unattended else "ask", None, stage, reason)


def _allowlist_target(destination: str) -> str:
    """The URL to check against the allowlist. Anything that is not a URL (or
    the public-web-search pseudo-destination) is treated as a host, so an
    unknown label fails closed under an allowlist."""
    d = destination.strip()
    if d == PUBLIC_WEB_SEARCH or "://" in d:
        return d
    return f"https://{d}"


class _NullStep:
    def record_args(self, args: dict[str, Any]) -> None: ...
    def record_result(self, result: dict[str, Any]) -> None: ...


@asynccontextmanager
async def _step(tracer: Tracer | None, model: str | None) -> AsyncIterator[Any]:
    if tracer is None:
        yield _NullStep()
        return
    async with tracer.step(agent="outbound", tool="outbound:review", model=model) as step:
        yield step


async def check(
    req: OutboundRequest,
    *,
    conn: aiosqlite.Connection,
    adapter: LlmAdapter,
    tracer: Tracer | None,
    approved_held_id: str | None = None,
) -> Decision:
    """Decide whether ``req`` may leave; see the module docstring.

    ``approved_held_id`` is the follow-up turn's approval token: when a row
    of that held card was sent by the user with exactly ``req.text`` for this
    destination and chat and is not spent yet, the request is sent (stage
    ``user``) and the approval is spent."""
    policy = load_policy()
    items = current_private()
    refs = [f"{i.kind}:{i.ref}" for i in items]
    model = policy.review_model if policy.review_enabled else None
    async with _step(tracer, model) as step:
        step.record_args({
            "request": {
                "caller": req.caller, "kind": req.kind, "destination": req.destination,
                "text": req.text, "unattended": req.unattended,
            },
            "review_enabled": policy.review_enabled,
            "unsure": policy.unsure,
            "approved_held_id": approved_held_id,
        })
        verdict: dict[str, Any] | None = None
        try:
            outcome, verdict = await _decide(req, conn, adapter, policy, items, refs,
                                             approved_held_id)
        except OutboundBlocked as exc:
            # check_destination left the row to us so the Decision carries
            # its id (a write it pushes off the loop has none).
            try:
                exc.log_id = await log.append(
                    conn, actor="agent" if req.run_id is not None else "system",
                    caller=req.caller, kind=req.kind, destination=exc.destination,
                    decision="blocked", stage="allowlist", original_text=req.text,
                    reason=exc.reason, run_id=req.run_id, session_id=req.session_id,
                )
            except Exception as log_exc:  # noqa: BLE001 - the block stands regardless
                logger.warning("outbound: could not log an allowlist block: %s",
                               type(log_exc).__name__)
            decision = Decision("block", None, req.text, "allowlist", exc.reason,
                                exc.log_id, None)
            step.record_result(_trace_result(decision, refs, None))
            return decision
        held_id: str | None = None
        if outcome.action in ("ask", "hold"):
            held_id = await held.create(
                conn, caller=req.caller, kind=req.kind, destination=req.destination,
                text=req.text, reason=outcome.reason, unattended=req.unattended,
                run_id=req.run_id, session_id=req.session_id,
            )
        rewritten = outcome.action == "send" and outcome.sent_text != req.text
        log_id = await log.append(
            conn,
            actor="agent" if req.run_id is not None else "system",
            caller=req.caller, kind=req.kind, destination=req.destination,
            decision="rewritten" if rewritten else _LOG_DECISION.get(outcome.action, "sent"),
            stage=outcome.stage,
            sent_text=outcome.sent_text,
            original_text=req.text if (rewritten or outcome.action != "send") else None,
            # The log names a span hit's sources (ids only); the held row and
            # the chat card keep the plain reason.
            reason=(f"{outcome.reason} ({_sources_text(outcome.sources)})"
                    if outcome.sources else outcome.reason),
            run_id=req.run_id, session_id=req.session_id,
        )
        decision = Decision(outcome.action, outcome.sent_text, req.text, outcome.stage,
                            outcome.reason, log_id, held_id, outcome.term)
        step.record_result({**_trace_result(decision, refs, verdict),
                            **({"span_sources": outcome.sources}
                               if outcome.sources is not None else {})})
        return decision


def _trace_result(
    d: Decision, refs: list[str], verdict: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "stage": d.stage, "decision": d.action, "sent_text": d.sent_text,
        "reason": d.reason, "private_refs": refs, "held_id": d.held_id,
        "log_id": d.log_id, "term": d.term, "verdict": verdict,
    }


async def _decide(
    req: OutboundRequest,
    conn: aiosqlite.Connection,
    adapter: LlmAdapter,
    policy: OutboundPolicy,
    items: list[PrivateItem],
    refs: list[str],
    approved_held_id: str | None = None,
) -> tuple[_Outcome, dict[str, Any] | None]:
    check_destination(_allowlist_target(req.destination), caller=req.caller,
                      kind=req.kind, log_blocked=False)  # may raise OutboundBlocked
    # A term added since the user's approval still stops the text (the
    # normal stages below then block it); the approval stays unspent.
    if approved_held_id is not None and find_term(req.text, policy.terms) is None:
        spent = await held.consume_approval(
            conn, approved_held_id, kind=req.kind, destination=req.destination,
            text=req.text, session_id=req.session_id)
        if spent is not None:
            return _Outcome("send", req.text, "user",
                            f"approved by the user (held request {spent})"), None
    if not policy.review_enabled:
        return _Outcome("send", req.text, "disabled", "outbound review is off"), None
    # Terms before the exact-id bypass: a DOI-shaped string can carry a term.
    hit = find_term(req.text, policy.terms)
    if hit is not None:
        return _Outcome("block", None, "terms", f"contains the sensitive term {hit!r}", hit), None
    if is_exact_id(req.text):
        return _Outcome("send", req.text, "exact_id", "exact identifier lookup"), None
    # A private document's title is private wherever it is typed, so the
    # title guard covers every private document in the library, not only
    # this chat's material (a chat without the document still asks).
    try:
        titled = [*items, *await _library_private_titles(conn)]
    except Exception as exc:  # noqa: BLE001 - an unreadable list proves nothing
        logger.warning("outbound: private titles could not be read: %s",
                       type(exc).__name__)
        return _unsure(policy, req, "review",
                       "private titles could not be read"), None
    if names_private_title(req.text, titled):
        # Before the review: a rewrite of a titled query rarely drops it all.
        return _unsure(policy, req, "span", _TITLE_REASON), None
    if material_incomplete() is not None:
        # Collection failed this turn: an empty or partial list proves nothing.
        return _unsure(policy, req, "review",
                       "private material could not be collected"), None
    if not items:
        return _Outcome("send", req.text, "review", "no private material"), None

    key = _cache_key(req.kind, req.text, refs, policy.review_model)
    cached = await _cache_get(conn, key)
    if cached is not None:
        sent = cached.get("sent_text")
        if cached["action"] == "send" and isinstance(sent, str):
            hit = find_term(sent, policy.terms)
            if hit is not None:
                return _Outcome("block", None, "terms",
                                f"cached rewrite contains the sensitive term {hit!r}", hit), None
            leak = _span_leak(sent, titled)
            if leak is not None:
                return _span_outcome(policy, req, sent, leak, items), None
            return _Outcome("send", sent, "cache", _safe_reason(
                str(cached.get("reason", "")), titled, policy.terms)), None
        if cached["action"] == "block":
            return _Outcome("block", None, "cache", _safe_reason(
                str(cached.get("reason", "")), titled, policy.terms)), None

    outcome, verdict = await _review(req, adapter, policy, items)
    # Any-order title matching only for a rewrite: the user's own query (an
    # allow sends it unchanged) and cached sends need the title as a phrase.
    sent = outcome.sent_text if outcome.action == "send" else None
    leak = (_span_leak(sent, titled, any_order=sent != req.text)
            if sent is not None else None)
    if sent is not None and leak is not None:
        # Deterministic backstop: a model-only review cannot guarantee that a
        # passage or a title of the private material never leaves (run 912:
        # a rewrite swapped the figures for the document's title). Not cached.
        # The trace's `verdict` keeps the raw rewrite (local record principle,
        # R50); the log reason and the held row never carry it.
        return _span_outcome(policy, req, sent, leak, items), verdict
    if outcome.stage == "review" and outcome.action in ("send", "block"):
        try:
            await _cache_put(conn, key, {"action": outcome.action,
                                         "sent_text": outcome.sent_text,
                                         "reason": outcome.reason})
        except Exception as exc:  # noqa: BLE001 - a cache miss later is harmless
            logger.warning("outbound review cache write failed: %s", type(exc).__name__)
    return outcome, verdict


async def _review(
    req: OutboundRequest,
    adapter: LlmAdapter,
    policy: OutboundPolicy,
    items: list[PrivateItem],
) -> tuple[_Outcome, dict[str, Any] | None]:
    try:
        v = await asyncio.wait_for(
            adapter.structured(
                slot=REVIEW_SLOT,
                variables=review_variables(req, items, policy.terms),
                response_model=ReviewVerdict,
                model=policy.review_model,
                fallback_model="",  # never fall back to another (maybe non-local) model
            ),
            timeout=policy.review_timeout_s,
        )
    except TimeoutError:
        return _unsure(policy, req, "timeout",
                       f"review timed out after {policy.review_timeout_s:g}s"), None
    except Exception as exc:  # noqa: BLE001 - any failure follows the unsure policy
        # Only the type: the message (e.g. a ValidationError) can echo the raw
        # model output, which may quote private material.
        name = type(exc).__name__
        logger.warning("outbound review failed: %s", name)
        return _unsure(policy, req, "error", f"review failed ({name})"), None
    verdict = v.model_dump()
    reason = _safe_reason(v.reason, items, policy.terms)
    if v.action == "allow":
        return _Outcome("send", req.text, "review", reason), verdict
    if v.action == "rewrite":
        new = (v.rewritten or "").strip()
        if not new:
            return _unsure(policy, req, "review", "review returned an empty rewrite"), verdict
        hit = find_term(new, policy.terms)
        if hit is not None:
            return _Outcome("block", None, "terms",
                            f"rewrite contains the sensitive term {hit!r}", hit), verdict
        return _Outcome("send", new, "review", reason), verdict
    if v.action == "block":
        return _Outcome("block", None, "review", reason), verdict
    return _unsure(policy, req, "review", reason), verdict
