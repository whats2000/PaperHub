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

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

import litellm

from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.mcp.errors import MCPToolError, MCPUnavailableError
from paperhub.mcp.registry import MCPRegistry
from paperhub.tracing.tracer import Tracer

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

# Per-request inner loop: how many times we kick back from Resolver to
# Discoverer when SS returns nothing. 2 means: initial attempt + one
# refinement.
MAX_REFINEMENT_LOOPS = 2
# Cap on web.search calls within ONE discover stage invocation. The
# prompt asks for 2-3 distinct query angles; this is a hard ceiling.
MAX_WEB_SEARCHES_PER_DISCOVER = 4

RequestKind = Literal["arxiv_id", "doi", "quoted_title", "natural_language"]


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
        # references), short-circuit and skip the LLM call.
        stripped = user_message
        for r in direct:
            stripped = stripped.replace(r.hint, "")
        if not re.search(r"[A-Za-z]{3,}", stripped):
            _LOG.info("paper_search.parse short-circuit: only structured IDs")
            return direct

    reg = registry or PromptRegistry()
    prompt = reg.get("paper_search_parse/v1")
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
            {"requests": [{"hint": r.hint, "kind": r.kind} for r in merged]},
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
        out.append(ParsedRequest(hint=hint.strip(), kind=resolved_kind))
    return out


# ───────────────────────────── B. Discoverer ─────────────────────────────


@dataclass(frozen=True)
class CanonicalIdentity:
    """What the Discoverer produces after multi-angle web.search.

    ``title`` is the canonical paper title as the community writes it
    (good input for SS). ``author_surname`` + ``year`` are extra
    signal the Resolver can use to disambiguate. ``confidence`` lets
    the Resolver decide how aggressively to try variants.
    """

    title: str
    author_surname: str | None
    year: int | None
    confidence: Literal["high", "medium", "low"]
    # Optional: a short justification we surface in the trace.
    rationale: str = ""


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
    canonical identifier. Returns ``None`` only when discovery
    genuinely fails (web.search returned nothing across all angles).
    """
    if request.kind in {"arxiv_id", "doi", "quoted_title"}:
        return CanonicalIdentity(
            title=request.hint,
            author_surname=None,
            year=None,
            confidence="high",
            rationale=f"user-supplied {request.kind}; no discovery needed",
        )

    if not await mcp_registry.has_tool("web.search"):
        _LOG.warning(
            "paper_search.discover: web.search not in registry; cannot run "
            "discover stage for hint=%r", request.hint,
        )
        return None

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
    web_tools = [
        s for s in await mcp_registry.aggregate_tool_schemas()
        if s["function"]["name"].startswith("web.")
    ]

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
                    "had_tool_calls": bool(tool_calls),
                    "content_len": len(msg.get("content") or ""),
                },
            )

        if not tool_calls:
            content = str(msg.get("content") or "").strip()
            return _parse_canonical_identity(content)

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
                args = json.loads(call["function"]["arguments"] or "{}")
                async with tracer.step(
                    agent="research", tool=f"paper_search:{name}", model=None,
                ) as step2:
                    step2.record_args(args)
                    try:
                        result = await mcp_registry.call(name, args)
                    except (MCPUnavailableError, MCPToolError) as exc:
                        result = {"error": str(exc), "tool": name}
                        step2.mark_error(str(exc))
                    step2.record_result(
                        {"summary": result}
                        if isinstance(result, dict)
                        else {"count": len(result)},
                    )
                if name.startswith("web."):
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
        step.record_result({"content_len": len(content)})
    return _parse_canonical_identity(content)


def _parse_canonical_identity(content: str) -> CanonicalIdentity | None:
    """Pull a JSON object out of the Discoverer's final message."""
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
    return CanonicalIdentity(
        title=title.strip(),
        author_surname=str(data["author_surname"]).strip()
            if isinstance(data.get("author_surname"), str) else None,
        year=int(data["year"])
            if isinstance(data.get("year"), int) else None,
        confidence=confidence,
        rationale=str(data.get("rationale", "") or ""),
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
) -> ResolvedPaper | None:
    """Call ``papers.search_semantic_scholar`` EXACTLY ONCE with the
    canonical title and return the first usable hit.

    The "exactly once" property is architectural (this function makes
    one call), not a prompt rule. No LLM is involved at this stage —
    the query is deterministic from the identity.
    """
    # Build the SS query. Prefer "<author surname> <year> <title>" when
    # we have all three (matches how SS indexes); fall back to bare title.
    query_parts: list[str] = []
    if identity.author_surname:
        query_parts.append(identity.author_surname)
    if identity.year:
        query_parts.append(str(identity.year))
    query_parts.append(identity.title)
    query = " ".join(query_parts)

    # ArXiv IDs / DOIs skip Discover and pass through to here with the
    # raw id as `identity.title`. SS accepts the raw id as a query.
    async with tracer.step(
        agent="research", tool="paper_search:resolve", model=None,
    ) as step:
        step.record_args({"query": query, "request_kind": request.kind})
        try:
            hits = await mcp_registry.call(
                "papers.search_semantic_scholar",
                {"query": query, "max_results": 5},
            )
        except (MCPUnavailableError, MCPToolError) as exc:
            step.mark_error(str(exc))
            return None
        if not isinstance(hits, list) or not hits:
            step.record_result({"hits": 0})
            return None
        # Pick the top hit. SS's first result is usually the canonical
        # paper when the query is built from a canonical title.
        top = hits[0]
        pid = top.get("paper_id")
        if not isinstance(pid, str):
            step.record_result({"hits": len(hits), "error": "missing_paper_id"})
            return None
        step.record_result({"hits": len(hits), "picked": pid})
    return ResolvedPaper(
        request=request,
        identity=identity,
        paper_id=pid,
        meta=top,
    )


# ─────────────────────────── D. Synthesizer ─────────────────────────────


async def synthesize_prose(
    resolved: list[ResolvedPaper],
    not_found: list[ParsedRequest],
    *,
    user_message: str,
    tracer: Tracer,
    model: str,
    registry: PromptRegistry | None = None,
    **litellm_kwargs: Any,
) -> str:
    """Write the prose summary the user sees in the chat bubble.

    Does NOT emit ``json:candidates`` — the chat layer builds the
    ``SearchResultsYield`` from ``resolved`` in pure Python (block
    emission is architectural, not LLM-driven).
    """
    reg = registry or PromptRegistry()
    prompt = reg.get("paper_search_synthesize/v1")
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
        step.record_result({"content_len": len(content)})
    return content
