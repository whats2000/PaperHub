"""Decomposed paper_search pipeline (v2.7).

Replaces the v2 mega-agent (one LLM turn with 5 tools + ~200 lines of
HARD REQUIREMENT blocks) with four single-responsibility stages:

  A. Parser       — split user_message into N distinct ParsedRequests.
  B. Discoverer   — per-request multi-angle web.search → CanonicalIdentity.
  C. Resolver     — per-request exactly-one papers.search_semantic_scholar
                    → ResolvedPaper | NotFound. Kick-back-with-bounded-loop
                    to Discoverer when Resolver finds nothing.
  D. Synthesizer  — prose summary only. The json:candidates block is
                    *not* an LLM responsibility — the chat layer builds
                    SearchResultsYield from the resolved set in Python,
                    so block emission is architecturally guaranteed.

Each stage has its own 20-30 line prompt (in
``paperhub.llm.prompts.paper_search_*``) and a focused tool palette.
The orchestration lives in ``research_graph.build_paper_search_subgraph``
(LangGraph fan-out per ParsedRequest + per-request kick-back loop).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import litellm

from paperhub.agents._mcp_result import normalize_mcp_result
from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.mcp.errors import MCPToolError, MCPUnavailableError
from paperhub.mcp.registry import MCPRegistry
from paperhub.pipelines.arxiv_client import ArxivResult, fetch_arxiv_by_id
from paperhub.tracing.tracer import Tracer

# Verifies an LLM-claimed arxiv_id against the arXiv API. Injected into
# ``resolve_via_ss`` for testability; the prod default wraps the sync
# ``fetch_arxiv_by_id`` in a thread so it doesn't block the event loop.
ArxivLookup = Callable[[str], Awaitable["ArxivResult | None"]]


async def _default_arxiv_lookup(arxiv_id: str) -> ArxivResult | None:
    return await asyncio.to_thread(fetch_arxiv_by_id, arxiv_id)

__all__ = [
    "MAX_REFINEMENT_LOOPS",
    "MAX_WEB_SEARCHES_PER_DISCOVER",
    "CanonicalIdentity",
    "ParsedRequest",
    "ResolvedPaper",
    "discover_canonical",
    "parse_user_message",
    "resolve_via_ss",
    "synthesize_prose",
]

_LOG = logging.getLogger(__name__)

# Per-request outer loop: how many Discover→Resolve attempts we make
# per request. Set to 1 — web.search is keyword lookup, not iterative
# research; kick-back loops just churn out near-duplicate titles with
# tweaked years (observed in prod traces) without adding signal. When
# the first attempt misses, surface NotFound to the user and let them
# disambiguate with an arxiv ID or DOI instead.
MAX_REFINEMENT_LOOPS = 1
# Cap on web.search calls within ONE discover stage invocation. The
# Discoverer's job is to keyword-match likely paper titles, not to
# do open-ended research — 2 is enough for one canonical-title query
# plus one alternate phrasing. After this cap, the inner loop forces
# the LLM to commit to a canonical identity.
MAX_WEB_SEARCHES_PER_DISCOVER = 2

RequestKind = Literal["arxiv_id", "doi", "quoted_title", "natural_language"]


# ────────────────── Structured-output web-search wrapper ──────────────────
#
# Empirical finding (probing the daemon at :3000): DuckDuckGo treats a
# double-quoted token as a strict-substring match. The LLM has a strong
# habit of wrapping single-token paper hints in quotes ("MolmoACT2"),
# which kills recall — bare `MolmoACT2` returns 10 hits including arxiv
# URLs, while `"MolmoACT2"` returns 0. Prompt rules against quoting are
# unreliable (Gemini ignores them under pressure), so we hide raw
# web.search from the LLM and expose a typed wrapper instead. The
# wrapper's schema literally has no field that accepts a free-form
# query string; the LLM passes a paper_hint + bag of extra_terms, and
# we build the underlying query deterministically — quotes that sneak
# into field values are stripped server-side.

_DISCOVER_TOOL_NAME = "paperhub.search_web"

_DISCOVER_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": _DISCOVER_TOOL_NAME,
        "description": (
            "Web-search for a paper by hint. Pass the user's paper name "
            "VERBATIM in paper_hint — do NOT add quotes or normalise "
            "capitalisation. Extra bare keywords are appended unquoted to "
            "bias toward paper pages (arxiv, github, openreview)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "paper_hint": {
                    "type": "string",
                    "description": (
                        "The user's name for the paper, verbatim "
                        "(e.g. 'MolmoACT2' or 'mamba paper'). No quotes, "
                        "no boolean operators."
                    ),
                },
                "extra_terms": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Additional bare keywords (e.g. ['arxiv'], "
                        "['paper', 'author', 'year']). Avoid quotes "
                        "and OR operators."
                    ),
                },
            },
            "required": ["paper_hint"],
        },
    },
}


_BOOLEAN_OPERATORS = {"OR", "AND", "NOT"}


def _build_safe_web_query(paper_hint: str, extra_terms: list[str]) -> str:
    """Deterministic query builder. Strips quotes, boolean operators,
    and any other syntax the LLM may have snuck through the typed
    schema. The output is a bare bag-of-keywords query that DDG handles
    via case-folded fuzzy match."""
    def _scrub(s: str) -> str:
        return (
            s.replace('"', "").replace("'", "").replace("“", "")
            .replace("”", "").replace(" OR ", " ").replace(" AND ", " ")
            .replace(" NOT ", " ").strip()
        )
    cleaned_hint = _scrub(paper_hint)
    cleaned_terms: list[str] = []
    for raw in extra_terms:
        scrubbed = _scrub(raw)
        # Drop bare boolean-operator terms (the LLM sometimes splits
        # `"a OR b"` into ["a", "OR", "b"] under the typed schema).
        if not scrubbed or scrubbed.upper() in _BOOLEAN_OPERATORS:
            continue
        cleaned_terms.append(scrubbed)
    parts = [cleaned_hint, *cleaned_terms]
    return " ".join(p for p in parts if p)


# ─────────────────────────────── A. Parser ───────────────────────────────


@dataclass(frozen=True)
class ParsedRequest:
    """One distinct paper request inside a single user turn.

    ``hint`` is the chunk of the user's text that names this paper.
    ``kind`` controls whether the Discoverer stage runs (skipped for
    arxiv_id / doi / quoted_title — those go straight to Resolver).
    """

    hint: str
    kind: RequestKind


_PARSER_RE_ARXIV = re.compile(r"\b(?:arxiv[:\s/]+)?(\d{4}\.\d{4,5})(?:v\d+)?\b", re.IGNORECASE)
_PARSER_RE_DOI = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Z0-9]+)\b", re.IGNORECASE)
# BibTeX cite-key shape (``pei2026actionaware``, ``fedus2022switch``,
# ``vaswani2017attention``): two-or-more lowercase letters, a 4-digit year,
# then a topic token. NO spaces — a real paper title always has them. Used
# in _safe_parse_request_list to DOWNGRADE such hints from ``quoted_title``
# to ``natural_language`` so the web Discoverer handles them (instead of
# Semantic Scholar searching for a paper titled "pei2026actionaware" and
# returning 0 hits, which is what the user's run 280 hit).
_PARSER_RE_BIBTEX_CITE_KEY = re.compile(r"^[a-z]{2,}\d{4}[a-z][a-z0-9]{2,}$")


def _scan_structured_ids(user_message: str) -> list[ParsedRequest]:
    """Cheap deterministic pass — pulls out arxiv IDs + DOIs the user
    pasted literally. Saves an LLM call when the input is already
    structured, and prevents the parser from mangling them."""
    out: list[ParsedRequest] = []
    for m in _PARSER_RE_ARXIV.finditer(user_message):
        out.append(ParsedRequest(hint=m.group(1), kind="arxiv_id"))
    for m in _PARSER_RE_DOI.finditer(user_message):
        out.append(ParsedRequest(hint=m.group(1), kind="doi"))
    return out


async def parse_user_message(
    user_message: str,
    *,
    tracer: Tracer,
    model: str,
    slot: str = "paper_search_parse/v1",
    registry: PromptRegistry | None = None,
    **litellm_kwargs: Any,
) -> list[ParsedRequest]:
    """Split ``user_message`` into N distinct paper requests.

    Returns an empty list when the message isn't a paper-search query —
    the chat layer treats that as a clarifying-question stop.
    """
    # Deterministic fast path: pasted arxiv IDs / DOIs.
    direct = _scan_structured_ids(user_message)
    if direct and len(direct) >= 1:
        # If the user pasted ONLY IDs (no extra natural-language paper
        # references), short-circuit and skip the LLM call. Strip the
        # IDs themselves AND the common identifier prefixes (``arxiv``,
        # ``doi``) before checking for prose — otherwise "arxiv:1706.03762"
        # leaves "arxiv:" which trips the [A-Za-z]{3,} filter.
        stripped = user_message
        for r in direct:
            stripped = stripped.replace(r.hint, "")
        stripped = re.sub(r"\b(?:arxiv|doi)\b", "", stripped, flags=re.IGNORECASE)
        if not re.search(r"[A-Za-z]{3,}", stripped):
            _LOG.info("paper_search.parse short-circuit: only structured IDs")
            return direct

    reg = registry or PromptRegistry()
    prompt = reg.get(slot)
    system = prompt.system
    user = prompt.user_template.format(user_message=user_message)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    async with tracer.step(
        agent="research", tool="paper_search:parse", model=model,
    ) as step:
        step.record_args({"user_message": user_message})
        response = await litellm.acompletion(
            model=model, messages=messages, **litellm_kwargs,
        )
        content = str(response["choices"][0]["message"].get("content") or "").strip()
        parsed = _safe_parse_request_list(content)
        # Merge deterministic + LLM parses; dedupe by (kind, hint).
        merged: list[ParsedRequest] = []
        seen: set[tuple[str, str]] = set()
        for r in [*direct, *parsed]:
            key = (r.kind, r.hint.lower())
            if key in seen:
                continue
            seen.add(key)
            merged.append(r)
        step.record_result(
            {
                "requests": [{"hint": r.hint, "kind": r.kind} for r in merged],
                # Raw LLM output (the Parser's reasoning) — recorded for
                # harness-eval reconstruction so a post-hoc reviewer sees
                # what the model emitted vs what was parsed/deduped out.
                "llm_content": content,
            },
        )
    return merged


def _safe_parse_request_list(content: str) -> list[ParsedRequest]:
    """Tolerate the LLM emitting prose around the JSON array."""
    m = re.search(r"\[.*\]", content, re.DOTALL)
    raw = m.group(0) if m else content
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[ParsedRequest] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        hint = entry.get("hint")
        kind = entry.get("kind")
        if not isinstance(hint, str) or not hint.strip():
            continue
        resolved_kind: RequestKind = "natural_language"
        if kind == "arxiv_id":
            resolved_kind = "arxiv_id"
        elif kind == "doi":
            resolved_kind = "doi"
        elif kind == "quoted_title":
            resolved_kind = "quoted_title"
        clean_hint = hint.strip()
        # Deterministic downgrade: a "quoted_title" whose hint is a bare
        # BibTeX cite-key (no spaces, contains a year) is NOT a real title
        # — pasting \cite{pei2026actionaware} from a survey's bibliography
        # is what surfaced this. Send it through the web Discoverer
        # (natural_language path) instead of dead-ending at Semantic
        # Scholar by-title with the cite-key as the query.
        if (
            resolved_kind == "quoted_title"
            and _PARSER_RE_BIBTEX_CITE_KEY.match(clean_hint)
        ):
            resolved_kind = "natural_language"
        out.append(ParsedRequest(hint=clean_hint, kind=resolved_kind))
    return out


# ───────────────────────────── B. Discoverer ─────────────────────────────


@dataclass(frozen=True)
class CanonicalIdentity:
    """What the Discoverer produces after multi-angle web.search.

    ``title`` is the canonical paper title as the community writes it
    (good input for SS). ``author_surname`` + ``year`` are extra
    signal the Resolver can use to disambiguate. ``confidence`` lets
    the Resolver decide how aggressively to try variants.

    ``arxiv_id`` is populated when the Discoverer found an
    ``arxiv.org/abs/<id>`` URL among its web hits. The Resolver uses
    this to query SS by arxiv-id directly (much more reliable than
    title match) AND, when SS still misses (e.g. paper not yet
    indexed), to synthesize a ResolvedPaper from the identity alone
    so the downstream arxiv-ingest path can land the paper anyway.
    """

    title: str
    author_surname: str | None
    year: int | None
    confidence: Literal["high", "medium", "low"]
    # Optional: a short justification we surface in the trace.
    rationale: str = ""
    arxiv_id: str | None = None


async def discover_canonical(
    request: ParsedRequest,
    *,
    tracer: Tracer,
    model: str,
    mcp_registry: MCPRegistry,
    prior_attempt_feedback: str = "",
    registry: PromptRegistry | None = None,
    **litellm_kwargs: Any,
) -> CanonicalIdentity | None:
    """Run a discover stage on a single ParsedRequest.

    For ``kind in {"arxiv_id", "doi", "quoted_title"}`` this short-
    circuits without calling web.search — the hint is already the
    canonical identifier.

    When ``web.search`` is NOT in the registry palette (operator hasn't
    installed open-webSearch, or its daemon is down), this falls back
    to a low-confidence CanonicalIdentity built from the raw hint —
    the Resolver will still get a chance to land the paper directly
    via Semantic Scholar. A tracer step is always opened so operators
    can see *why* the discovery stage produced what it did.

    Returns ``None`` only when web.search IS available but the multi-
    angle exploration could not produce a canonical identity.
    """
    if request.kind in {"arxiv_id", "doi", "quoted_title"}:
        # Always open a trace row so the stage is visible in the panel.
        async with tracer.step(
            agent="research", tool="paper_search:discover_shortcircuit", model=None,
        ) as step:
            step.record_args({"hint": request.hint, "kind": request.kind})
            step.record_result({"shortcircuit": "user-supplied id/title"})
        return CanonicalIdentity(
            title=request.hint,
            author_surname=None,
            year=None,
            confidence="high",
            rationale=f"user-supplied {request.kind}; no discovery needed",
        )

    if not await mcp_registry.has_tool("web.search"):
        # Web search is unavailable. Don't silently bail — open a
        # tracer step so the operator sees the fallback, and return a
        # low-confidence identity built from the raw hint so the
        # Resolver still gets to try SS with it.
        _LOG.warning(
            "paper_search.discover: web.search not in registry; falling "
            "back to direct-SS path for hint=%r", request.hint,
        )
        async with tracer.step(
            agent="research", tool="paper_search:discover_fallback", model=None,
        ) as step:
            step.record_args({"hint": request.hint})
            step.record_result(
                {
                    "fallback": "web.search not available — passing raw hint "
                                "to Resolver as a quoted_title-equivalent. "
                                "Operator: start open-webSearch daemon for "
                                "better recall on vague queries.",
                },
            )
        return CanonicalIdentity(
            title=request.hint,
            author_surname=None,
            year=None,
            confidence="low",
            rationale="web.search unavailable; trying raw hint via SS",
        )

    reg = registry or PromptRegistry()
    prompt = reg.get("paper_search_discover/v1")
    system = prompt.system
    user = prompt.user_template.format(
        hint=request.hint,
        prior_feedback=prior_attempt_feedback or "(none — first attempt)",
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    # Hide raw web.search from the LLM. Expose only the typed wrapper
    # so the LLM cannot pass a quoted query string. The wrapper
    # synthesises the underlying web.search query server-side.
    web_tools = [_DISCOVER_TOOL_SCHEMA]

    web_calls = 0
    for iteration in range(MAX_WEB_SEARCHES_PER_DISCOVER + 1):
        async with tracer.step(
            agent="research", tool="paper_search:discover_plan", model=model,
        ) as step:
            step.record_args({
                "hint": request.hint,
                "iteration": iteration,
                "web_calls_so_far": web_calls,
            })
            response = await litellm.acompletion(
                model=model, messages=messages, tools=web_tools,
                tool_choice="auto", **litellm_kwargs,
            )
            msg = response["choices"][0]["message"]
            tool_calls = msg.get("tool_calls") or []
            step.record_result(
                {
                    "content": msg.get("content") or "",
                    "tool_calls": [
                        {
                            "name": c["function"]["name"],
                            "arguments": c["function"]["arguments"],
                        }
                        for c in tool_calls
                    ],
                },
            )

        if not tool_calls:
            content = str(msg.get("content") or "").strip()
            evidence = "\n".join(
                str(m.get("content") or "")
                for m in messages
                if m.get("role") == "tool"
            )
            return _parse_canonical_identity(content, evidence_text=evidence)

        messages.append({
            "role": "assistant",
            "content": msg.get("content"),
            "tool_calls": tool_calls,
        })

        for call in tool_calls:
            name = call["function"]["name"]
            if web_calls >= MAX_WEB_SEARCHES_PER_DISCOVER:
                result: Any = {"error": "web_search_cap_reached"}
            else:
                raw_args = json.loads(call["function"]["arguments"] or "{}")
                # Route paperhub.search_web through the safe query builder
                # → underlying web.search. Anything else is forbidden (LLM
                # got the schema; off-palette calls are a bug, so surface
                # them as a tool error rather than silently passing through).
                if name != _DISCOVER_TOOL_NAME:
                    # Off-palette tool call. We never expose anything
                    # other than the wrapper, so this is either an LLM
                    # hallucination or an attempt to bypass the query
                    # sanitiser. Don't dispatch; tell the LLM what's
                    # allowed.
                    result = {
                        "error": "off_palette_tool",
                        "received": name,
                        "allowed": [_DISCOVER_TOOL_NAME],
                        "hint": (
                            f"Only {_DISCOVER_TOOL_NAME} is exposed. "
                            "Call it with paper_hint + extra_terms."
                        ),
                    }
                    async with tracer.step(
                        agent="research", tool=f"paper_search:{name}", model=None,
                    ) as step2:
                        step2.record_args(raw_args if isinstance(raw_args, dict) else {})
                        step2.record_result({"summary": result})
                        step2.mark_error("off_palette_tool")
                else:
                    paper_hint = (
                        raw_args.get("paper_hint") if isinstance(raw_args, dict) else None
                    ) or request.hint
                    extra_terms = (
                        raw_args.get("extra_terms") if isinstance(raw_args, dict) else None
                    ) or []
                    if not isinstance(extra_terms, list):
                        extra_terms = []
                    safe_query = _build_safe_web_query(str(paper_hint), list(extra_terms))
                    async with tracer.step(
                        agent="research", tool=f"paper_search:{name}", model=None,
                    ) as step2:
                        step2.record_args(
                            {
                                "paper_hint": paper_hint,
                                "extra_terms": extra_terms,
                                "built_query": safe_query,
                            },
                        )
                        try:
                            result = normalize_mcp_result(
                                await mcp_registry.call(
                                    "web.search", {"query": safe_query},
                                )
                            )
                        except (MCPUnavailableError, MCPToolError) as exc:
                            result = {"error": str(exc), "tool": "web.search"}
                            step2.mark_error(str(exc))
                        if isinstance(result, dict):
                            step2.record_result({"summary": result})
                        elif isinstance(result, list):
                            step2.record_result(
                                {"count": len(result), "top": result[:5]},
                            )
                        else:
                            step2.record_result({"value": result})
                if name == _DISCOVER_TOOL_NAME:
                    web_calls += 1
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "name": name,
                "content": json.dumps(result, default=str),
            })

    # Cap exhausted; final attempt at canonical extraction.
    messages.append({
        "role": "user",
        "content": (
            "You've reached the web.search cap for this discovery stage. "
            "Based on what you've seen so far, return the canonical "
            "identity as JSON now (or {\"title\": null, \"reason\": "
            "\"...\"} if you couldn't determine it)."
        ),
    })
    async with tracer.step(
        agent="research", tool="paper_search:discover_finalise", model=model,
    ) as step:
        step.record_args({"hint": request.hint, "web_calls": web_calls})
        response = await litellm.acompletion(model=model, messages=messages, **litellm_kwargs)
        content = str(response["choices"][0]["message"].get("content") or "").strip()
        step.record_result({"content": content})
    evidence = "\n".join(
        str(m.get("content") or "")
        for m in messages
        if m.get("role") == "tool"
    )
    return _parse_canonical_identity(content, evidence_text=evidence)


_ARXIV_URL_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE,
)


def _extract_arxiv_id_from_text(text: str) -> str | None:
    """Server-side safety net: pull an arxiv ID out of any arxiv URL
    present in ``text``. Used when the LLM forgot to include
    ``arxiv_id`` in its JSON output even though the web hits contained
    one."""
    m = _ARXIV_URL_RE.search(text)
    return m.group(1) if m else None


def _parse_canonical_identity(
    content: str, *, evidence_text: str = "",
) -> CanonicalIdentity | None:
    """Pull a JSON object out of the Discoverer's final message.

    ``evidence_text`` is the concatenation of tool-message contents the
    Discoverer saw — we mine it for an arxiv ID as a server-side
    safety net so the LLM forgetting to include ``arxiv_id`` doesn't
    cost us a lookup-by-id path.
    """
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    confidence = data.get("confidence", "medium")
    if confidence not in ("high", "medium", "low"):
        confidence = "medium"
    # arxiv_id resolution: prefer the LLM's explicit field, fall back
    # to scanning the evidence text for an arxiv URL.
    arxiv_id: str | None = None
    raw_arxiv = data.get("arxiv_id")
    if isinstance(raw_arxiv, str) and raw_arxiv.strip():
        # Accept either "2510.10274" or a full arxiv URL.
        m_url = _ARXIV_URL_RE.search(raw_arxiv)
        if m_url:
            arxiv_id = m_url.group(1)
        elif re.fullmatch(r"\d{4}\.\d{4,5}", raw_arxiv.strip()):
            arxiv_id = raw_arxiv.strip()
    if arxiv_id is None and evidence_text:
        arxiv_id = _extract_arxiv_id_from_text(evidence_text)
    return CanonicalIdentity(
        title=title.strip(),
        author_surname=str(data["author_surname"]).strip()
            if isinstance(data.get("author_surname"), str) else None,
        year=int(data["year"])
            if isinstance(data.get("year"), int) else None,
        confidence=confidence,
        rationale=str(data.get("rationale", "") or ""),
        arxiv_id=arxiv_id,
    )


# ───────────────────────────── C. Resolver ─────────────────────────────


@dataclass(frozen=True)
class ResolvedPaper:
    """SS hit linked to a ParsedRequest. ``paper_id`` is the prefixed
    id (``arxiv:`` / ``ss:``) ready to feed into ``json:candidates``."""

    request: ParsedRequest
    identity: CanonicalIdentity
    paper_id: str
    meta: dict[str, Any] = field(default_factory=dict)


async def resolve_via_ss(
    request: ParsedRequest,
    identity: CanonicalIdentity,
    *,
    tracer: Tracer,
    mcp_registry: MCPRegistry,
    arxiv_lookup: ArxivLookup | None = None,
) -> ResolvedPaper | None:
    """Resolve ``identity`` to a ResolvedPaper, ideally via Semantic
    Scholar but falling back to arxiv when SS misses.

    Strategy (deterministic — no LLM):

    1. If ``identity.arxiv_id`` is set, query SS with ``arXiv:<id>``
       (much more reliable than title match). If SS hits → use SS meta.
       If SS misses → VERIFY the id against the arXiv API and adopt
       arXiv's AUTHORITATIVE metadata (title/authors/year/abstract).
       The LLM's ``identity.title`` is a discovery hint only and is
       NEVER stored as the paper title — the Discoverer routinely
       paraphrases ("Distilled Diffusion Language Models" for what is
       really "Turning the TIDE: Cross-Architecture Distillation …"),
       so trusting it produced the title-mismatch bug. If arXiv can't
       confirm the id, it's bogus → return None (NotFound).

    2. Else (title-only), query SS with the canonical title. On miss,
       return None (NotFound).

    No outer loop, no LLM. ``arxiv_lookup`` is injected for tests; prod
    uses the arXiv ``id_list`` API via :data:`_default_arxiv_lookup`.
    """
    arxiv_lookup = arxiv_lookup or _default_arxiv_lookup
    # Trace args + identity snapshot.
    async with tracer.step(
        agent="research", tool="paper_search:resolve", model=None,
    ) as step:
        identity_snapshot = {
            "title": identity.title,
            "author_surname": identity.author_surname,
            "year": identity.year,
            "confidence": identity.confidence,
            "rationale": identity.rationale,
            "arxiv_id": identity.arxiv_id,
        }

        # Path 1: arxiv_id known → try SS by arxiv-id, then synthesise on miss.
        if identity.arxiv_id:
            query = f"arXiv:{identity.arxiv_id}"
            step.record_args(
                {"query": query, "request_kind": request.kind, "identity": identity_snapshot},
            )
            try:
                hits = normalize_mcp_result(
                    await mcp_registry.call(
                        "papers.search_semantic_scholar",
                        {"query": query, "max_results": 5},
                    )
                )
            except (MCPUnavailableError, MCPToolError) as exc:
                step.mark_error(str(exc))
                hits = []
            # SS's `arXiv:<id>` query hits the FREE-TEXT /paper/search endpoint,
            # which keyword-matches "arXiv" and returns junk first hits (e.g. a
            # paper literally titled "arXiv") — NOT an exact id lookup. So we
            # accept a hit ONLY when its arxiv_id actually equals the claimed
            # id (version suffix ignored); otherwise treat it as an SS miss and
            # fall through to the authoritative arXiv-API verification below.
            def _aid(v: object) -> str:
                return str(v or "").split("v")[0].strip().lower()

            want = _aid(identity.arxiv_id)
            matched = next(
                (
                    h for h in hits
                    if isinstance(h, dict)
                    and _aid(h.get("arxiv_id")) == want
                    and isinstance(h.get("paper_id"), str)
                ),
                None,
            ) if isinstance(hits, list) else None
            if matched is not None:
                pid = str(matched["paper_id"])
                step.record_result(
                    {"hits": len(hits), "picked": pid, "top": hits[:5], "source": "ss_by_arxiv_id"},
                )
                return ResolvedPaper(
                    request=request, identity=identity, paper_id=pid, meta=matched,
                )
            # SS missed — verify the id against arXiv and adopt arXiv's
            # AUTHORITATIVE metadata when it confirms (the LLM's guessed
            # title paraphrases, so a CONFIRMED id always wins the title).
            verified = await arxiv_lookup(identity.arxiv_id)
            if verified is None:
                # Verification was INCONCLUSIVE — SS missed AND arXiv couldn't
                # confirm the id. In prod this is overwhelmingly a transient
                # 429/503 against export.arxiv.org, NOT proof the paper is
                # nonexistent. Per "unverified ≠ nonexistent": do NOT drop.
                # Emit the candidate from the Discoverer's hint + claimed
                # arxiv_id with finalize=True and let the auto-attach DOWNLOAD
                # be the real validity gate (it has its own export→arxiv.org
                # fallback + retry). A genuinely-bogus id simply fails to
                # download and never lands; a real-but-unindexed paper gets
                # through. Ingest replaces the hint title with arXiv's
                # authoritative one, so the paraphrase is only ever transient.
                unverified_meta: dict[str, Any] = {
                    "title": identity.title or "",
                    "arxiv_id": identity.arxiv_id,
                    "year": identity.year,
                    "authors": [],
                    "abstract": None,
                    "has_open_pdf": True,  # a real arxiv id resolves to a PDF
                    "verified": False,
                }
                step.record_result(
                    {
                        "hits": 0,
                        "top": [],
                        "source": "arxiv_id_unverified_emitted",
                        "unverified_arxiv_id": identity.arxiv_id,
                        "llm_hint_title": identity.title,
                    },
                )
                return ResolvedPaper(
                    request=request, identity=identity,
                    paper_id=f"arxiv:{identity.arxiv_id}", meta=unverified_meta,
                )
            verified_pid = f"arxiv:{verified.arxiv_id}"
            verified_meta: dict[str, Any] = {
                "title": verified.title,
                "arxiv_id": verified.arxiv_id,
                "year": verified.year,
                "authors": verified.authors,
                "abstract": verified.abstract,
                "has_open_pdf": True,  # arxiv URLs always have an open PDF
            }
            step.record_result(
                {
                    "hits": 0,
                    "top": [],
                    "source": "verified_via_arxiv_api",
                    "verified_arxiv_id": verified.arxiv_id,
                    "title": verified.title,
                    "llm_hint_title": identity.title,
                },
            )
            return ResolvedPaper(
                request=request, identity=identity,
                paper_id=verified_pid, meta=verified_meta,
            )

        # Path 2: no arxiv_id → title-only SS search.
        query_parts: list[str] = []
        if identity.author_surname:
            query_parts.append(identity.author_surname)
        if identity.year:
            query_parts.append(str(identity.year))
        query_parts.append(identity.title)
        query = " ".join(query_parts)
        step.record_args(
            {"query": query, "request_kind": request.kind, "identity": identity_snapshot},
        )
        try:
            hits = normalize_mcp_result(
                await mcp_registry.call(
                    "papers.search_semantic_scholar",
                    {"query": query, "max_results": 5},
                )
            )
        except (MCPUnavailableError, MCPToolError) as exc:
            step.mark_error(str(exc))
            return None
        if not isinstance(hits, list) or not hits:
            step.record_result({"hits": 0, "top": [], "source": "ss_by_title"})
            return None
        top = hits[0]
        pid = top.get("paper_id")
        if not isinstance(pid, str):
            step.record_result(
                {"hits": len(hits), "error": "missing_paper_id", "top": hits[:5]},
            )
            return None
        step.record_result(
            {"hits": len(hits), "picked": pid, "top": hits[:5], "source": "ss_by_title"},
        )
    return ResolvedPaper(
        request=request, identity=identity, paper_id=pid, meta=top,
    )


# ─────────────────────────── D. Synthesizer ─────────────────────────────


async def synthesize_prose(
    resolved: list[ResolvedPaper],
    not_found: list[ParsedRequest],
    *,
    user_message: str,
    tracer: Tracer,
    model: str,
    slot: str = "paper_search_synthesize/v1",
    registry: PromptRegistry | None = None,
    response_language: str = "the user's language",
    memory_context: str = "",
    **litellm_kwargs: Any,
) -> str:
    """Write the prose summary the user sees in the chat bubble.

    Does NOT emit ``json:candidates`` — the chat layer builds the
    ``SearchResultsYield`` from ``resolved`` in pure Python (block
    emission is architectural, not LLM-driven).
    """
    reg = registry or PromptRegistry()
    prompt = reg.get(slot)
    system = prompt.system
    resolved_block = "\n".join(
        f"  - {r.identity.title} "
        f"({r.identity.year or '?'}) → {r.paper_id}"
        for r in resolved
    ) or "  (none)"
    not_found_block = "\n".join(
        f"  - {req.hint}" for req in not_found
    ) or "  (none)"
    user = prompt.user_template.format(
        user_message=user_message,
        resolved_block=resolved_block,
        not_found_block=not_found_block,
        response_language=response_language,
        memory_context=memory_context,
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    async with tracer.step(
        agent="research", tool="paper_search:synthesize", model=model,
    ) as step:
        step.record_args({
            "resolved_count": len(resolved),
            "not_found_count": len(not_found),
        })
        response = await litellm.acompletion(
            model=model, messages=messages, **litellm_kwargs,
        )
        content = str(response["choices"][0]["message"].get("content") or "").strip()
        # Record the actual resolved paper_ids + titles and not_found
        # hints (not just counts) so a harness eval can score resolve
        # accuracy from the trace alone.
        step.record_result({
            "resolved": [
                {
                    "paper_id": r.paper_id,
                    "title": str(
                        (r.meta.get("title") if isinstance(r.meta, dict) else None)
                        or r.identity.title
                        or "",
                    ),
                }
                for r in resolved
            ],
            "not_found": [req.hint for req in not_found],
            "content": content,
            "recall_hit": bool(memory_context),
        })
    return content
