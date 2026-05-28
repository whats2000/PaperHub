"""Unit + integration tests for the v2.7 decomposed paper_search pipeline.

Each test patches ``litellm.acompletion`` and stubs ``MCPRegistry.call`` to
exercise the four stages (Parser / Discoverer / Resolver / Synthesizer)
independently and end-to-end through the subgraph.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from paperhub.agents.research_pipeline import (
    CanonicalIdentity,
    ParsedRequest,
    ResolvedPaper,
    discover_canonical,
    parse_user_message,
    resolve_via_ss,
    synthesize_prose,
)
from paperhub.pipelines.arxiv_client import ArxivResult
from paperhub.tracing.tracer import Tracer

# ───────────────────────────── helpers ─────────────────────────────


def _msg(content: str | None = None, tool_calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    m: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    return {"choices": [{"message": m}]}


def _tool_call(call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _async_completion_mock(seq: list[dict[str, Any]]) -> AsyncMock:
    return AsyncMock(side_effect=seq)


class _StubRegistry:
    """In-memory MCP registry: stub web.search + papers.search_semantic_scholar."""

    def __init__(
        self,
        web_hits: list[dict[str, Any]] | None = None,
        ss_hits: list[dict[str, Any]] | None = None,
        has_web_search: bool = True,
    ) -> None:
        self.web_hits = web_hits or []
        self.ss_hits = ss_hits or []
        self._has_web = has_web_search
        self.call_log: list[tuple[str, dict[str, Any]]] = []

    async def has_tool(self, name: str) -> bool:
        if name == "web.search":
            return self._has_web
        return True

    async def aggregate_tool_schemas(self) -> list[dict[str, Any]]:
        if self._has_web:
            return [{
                "type": "function",
                "function": {
                    "name": "web.search",
                    "description": "Web search",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            }]
        return []

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        self.call_log.append((name, args))
        if name == "web.search":
            return list(self.web_hits)
        if name == "papers.search_semantic_scholar":
            return list(self.ss_hits)
        raise RuntimeError(f"_StubRegistry: unknown tool {name!r}")


@pytest.fixture
async def migrated_db() -> aiosqlite.Connection:
    """In-memory SQLite with the project schema applied."""
    from paperhub.db.migrate import apply_schema

    conn = await aiosqlite.connect(":memory:")
    await apply_schema(conn)
    # Seed a chat_sessions + runs row so the tracer has a foreign key target.
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await conn.execute(
        "INSERT INTO runs (session_id, status) VALUES (1, 'running')",
    )
    await conn.commit()
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
def fake_tracer(migrated_db: aiosqlite.Connection) -> Tracer:
    return Tracer(conn=migrated_db, run_id=1, branch="")


# ───────────────────────────── Parser ─────────────────────────────


async def test_parse_arxiv_id_shortcircuits_without_llm(
    fake_tracer: Tracer,
) -> None:
    """A message that's PURELY a pasted ID (no significant natural-
    language words around it) short-circuits without an LLM call.
    Mixed-content messages with both IDs and natural language still
    go through the LLM so any non-ID paper hints get parsed too."""
    comp = AsyncMock()
    with patch("paperhub.agents.research_pipeline.litellm.acompletion", new=comp):
        out = await parse_user_message(
            "arxiv:1706.03762",
            tracer=fake_tracer, model="m",
        )
    # Short-circuit path: zero LLM calls.
    assert comp.await_count == 0
    assert len(out) == 1
    assert out[0].kind == "arxiv_id"
    assert out[0].hint == "1706.03762"


async def test_parse_natural_language_single_paper(
    fake_tracer: Tracer,
) -> None:
    """LLM returns one ParsedRequest for a single-paper natural-language query."""
    comp = _async_completion_mock([
        _msg(content='[{"hint": "mamba paper", "kind": "natural_language"}]'),
    ])
    with patch("paperhub.agents.research_pipeline.litellm.acompletion", new=comp):
        out = await parse_user_message(
            "find the mamba paper",
            tracer=fake_tracer, model="m",
        )
    assert len(out) == 1
    assert out[0] == ParsedRequest(hint="mamba paper", kind="natural_language")


async def test_parse_trace_records_raw_llm_content(
    fake_tracer: Tracer,
    migrated_db: aiosqlite.Connection,
) -> None:
    """Harness-eval observability: the paper_search:parse row must record
    the raw LLM content (the Parser's reasoning) alongside the parsed
    requests, so a post-hoc eval can see what the model actually emitted
    vs what was parsed out — not just the deduped result."""
    raw = '[{"hint": "mamba paper", "kind": "natural_language"}]'
    comp = _async_completion_mock([_msg(content=raw)])
    with patch("paperhub.agents.research_pipeline.litellm.acompletion", new=comp):
        await parse_user_message(
            "find the mamba paper", tracer=fake_tracer, model="m",
        )
    async with migrated_db.execute(
        "SELECT result_summary_json FROM tool_calls "
        "WHERE run_id=1 AND tool='paper_search:parse'",
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    result = json.loads(row[0] or "{}")
    assert result.get("requests") == [
        {"hint": "mamba paper", "kind": "natural_language"},
    ]
    assert result.get("llm_content") == raw, (
        "parse step must record the raw LLM content for eval reconstruction"
    )


async def test_synthesize_trace_records_resolved_and_not_found_ids(
    fake_tracer: Tracer,
    migrated_db: aiosqlite.Connection,
) -> None:
    """Harness-eval observability: the paper_search:synthesize row must
    record the actual resolved paper_ids + not_found hints (not just
    counts), so an eval can score resolve accuracy from the trace alone."""
    resolved = [
        ResolvedPaper(
            request=ParsedRequest(hint="mamba paper", kind="natural_language"),
            identity=CanonicalIdentity(
                title="Mamba", author_surname="Gu", year=2023,
                confidence="high", arxiv_id="2312.00752", rationale="top hit",
            ),
            paper_id="arxiv:2312.00752",
            meta={"title": "Mamba"},
        ),
    ]
    not_found = [ParsedRequest(hint="some imaginary paper", kind="natural_language")]
    comp = _async_completion_mock([_msg(content="Here is what I found...")])
    with patch("paperhub.agents.research_pipeline.litellm.acompletion", new=comp):
        await synthesize_prose(
            resolved, not_found,
            user_message="find mamba and an imaginary paper",
            tracer=fake_tracer, model="m",
        )
    async with migrated_db.execute(
        "SELECT result_summary_json FROM tool_calls "
        "WHERE run_id=1 AND tool='paper_search:synthesize'",
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    result = json.loads(row[0] or "{}")
    assert result.get("resolved") == [
        {"paper_id": "arxiv:2312.00752", "title": "Mamba"},
    ], "synthesize must record resolved paper_ids + titles, not just a count"
    assert result.get("not_found") == ["some imaginary paper"], (
        "synthesize must record the not_found hints for eval"
    )
    assert result.get("content") == "Here is what I found..."


async def test_parse_multi_paper_fanout(
    fake_tracer: Tracer,
) -> None:
    """LLM returns N ParsedRequests for a multi-paper query."""
    raw = [
        {"hint": "Mamba", "kind": "natural_language"},
        {"hint": "DDPM", "kind": "natural_language"},
        {"hint": "Vaswani 2017", "kind": "natural_language"},
    ]
    comp = _async_completion_mock([_msg(content=json.dumps(raw))])
    with patch("paperhub.agents.research_pipeline.litellm.acompletion", new=comp):
        out = await parse_user_message(
            "Mamba, DDPM, and Vaswani 2017",
            tracer=fake_tracer, model="m",
        )
    assert len(out) == 3
    assert {r.hint for r in out} == {"Mamba", "DDPM", "Vaswani 2017"}


async def test_parse_empty_for_non_paper_search(
    fake_tracer: Tracer,
) -> None:
    """Parser returns [] when the message isn't a paper-search query."""
    comp = _async_completion_mock([_msg(content="[]")])
    with patch("paperhub.agents.research_pipeline.litellm.acompletion", new=comp):
        out = await parse_user_message(
            "compare these two papers",
            tracer=fake_tracer, model="m",
        )
    assert out == []


async def test_parse_tolerates_prose_around_json(
    fake_tracer: Tracer,
) -> None:
    """Parser extracts the JSON array even if the LLM wraps it in prose."""
    comp = _async_completion_mock([_msg(
        content='Here are the requests: [{"hint": "DDPM", "kind": "natural_language"}]',
    )])
    with patch("paperhub.agents.research_pipeline.litellm.acompletion", new=comp):
        out = await parse_user_message(
            "DDPM",
            tracer=fake_tracer, model="m",
        )
    assert len(out) == 1
    assert out[0].hint == "DDPM"


async def test_parse_downgrades_bibtex_cite_key_to_natural_language(
    fake_tracer: Tracer,
) -> None:
    """Regression: run 280 in session 158. The user pasted a LaTeX
    bibliography snippet containing ``\\cite{pei2026actionaware}`` along
    with two full titles. The LLM Parser classified ``pei2026actionaware``
    as ``kind: quoted_title``. The Discoverer then SHORT-CIRCUITED web
    search ("user-supplied id/title") and the Resolver queried Semantic
    Scholar for a paper literally titled "pei2026actionaware" — 0 hits,
    pipeline reported the paper as not found.

    A BibTeX cite-key is NOT a title and MUST NOT short-circuit the web
    Discoverer. Detect the cite-key shape deterministically (no spaces,
    contains a 4-digit year, lowercase + digits only) and downgrade to
    ``natural_language`` so the Discoverer can web-search for the actual
    paper that key references."""
    raw = (
        '[{"hint": "pei2026actionaware", "kind": "quoted_title"}, '
        '{"hint": "li2026optimusvla", "kind": "quoted_title"}, '
        '{"hint": "Attention Is All You Need", "kind": "quoted_title"}]'
    )
    comp = _async_completion_mock([_msg(content=raw)])
    with patch("paperhub.agents.research_pipeline.litellm.acompletion", new=comp):
        out = await parse_user_message(
            r"\cite{pei2026actionaware} and \cite{li2026optimusvla} and the "
            r"paper titled \"Attention Is All You Need\"",
            tracer=fake_tracer, model="m",
        )
    by_hint = {r.hint: r.kind for r in out}
    # Cite-key shaped hints → natural_language (web-Discoverer eligible).
    assert by_hint["pei2026actionaware"] == "natural_language"
    assert by_hint["li2026optimusvla"] == "natural_language"
    # Real quoted titles must remain quoted_title (they're still trusted).
    assert by_hint["Attention Is All You Need"] == "quoted_title"


async def test_parse_does_not_downgrade_real_titles_just_because_year_appears() -> None:
    """Guard against false positives: a real paper title that happens to
    contain a year (``Mamba 2 (2024)``) must NOT be downgraded — the
    cite-key regex requires NO spaces."""
    from paperhub.agents.research_pipeline import _safe_parse_request_list

    raw = (
        '[{"hint": "Mamba 2 (2024)", "kind": "quoted_title"}, '
        '{"hint": "Attention 2017", "kind": "quoted_title"}]'
    )
    out = _safe_parse_request_list(raw)
    by_hint = {r.hint: r.kind for r in out}
    assert by_hint["Mamba 2 (2024)"] == "quoted_title"
    assert by_hint["Attention 2017"] == "quoted_title"


# ─────────────────────────── Discoverer ─────────────────────────────


async def test_discover_shortcircuits_for_arxiv_id(
    fake_tracer: Tracer,
) -> None:
    """arxiv_id / doi / quoted_title requests skip web.search entirely."""
    reg = _StubRegistry()
    out = await discover_canonical(
        ParsedRequest(hint="1706.03762", kind="arxiv_id"),
        tracer=fake_tracer, model="m", mcp_registry=reg,  # type: ignore[arg-type]
    )
    assert out is not None
    assert out.title == "1706.03762"
    assert out.confidence == "high"
    # No web.search invocations.
    assert all(name != "web.search" for name, _ in reg.call_log)


async def test_discover_natural_language_multi_angle_then_returns_identity(
    fake_tracer: Tracer,
) -> None:
    """Discoverer issues web.search, reads results, returns CanonicalIdentity."""
    reg = _StubRegistry(web_hits=[
        {"title": "Mamba: Linear-Time Sequence Modeling", "url": "https://arxiv.org/abs/2312.00752"},
    ])
    # 1) LLM responds with a tool call to the structured-output wrapper.
    # 2) After tool result, LLM emits canonical identity JSON.
    seq = [
        _msg(tool_calls=[_tool_call(
            "c1", "paperhub.search_web",
            {"paper_hint": "mamba paper", "extra_terms": ["foundational"]},
        )]),
        _msg(content=json.dumps({
            "title": "Mamba: Linear-Time Sequence Modeling with Selective State Spaces",
            "author_surname": "Gu",
            "year": 2023,
            "confidence": "high",
            "rationale": "Multiple hits pointed to Gu & Dao 2023.",
        })),
    ]
    comp = _async_completion_mock(seq)
    with patch("paperhub.agents.research_pipeline.litellm.acompletion", new=comp):
        out = await discover_canonical(
            ParsedRequest(hint="mamba paper", kind="natural_language"),
            tracer=fake_tracer, model="m", mcp_registry=reg,  # type: ignore[arg-type]
        )
    assert out is not None
    assert out.title.startswith("Mamba")
    assert out.year == 2023
    assert out.author_surname == "Gu"
    assert out.confidence == "high"
    # The wrapper dispatched to web.search server-side.
    web_calls = [n for n, _ in reg.call_log if n == "web.search"]
    assert len(web_calls) == 1


async def test_discover_returns_none_when_llm_says_not_found(
    fake_tracer: Tracer,
) -> None:
    """Title=null in the canonical identity payload → returns None."""
    reg = _StubRegistry(web_hits=[])
    seq = [
        _msg(tool_calls=[_tool_call(
            "c1", "paperhub.search_web",
            {"paper_hint": "foo", "extra_terms": []},
        )]),
        _msg(content='{"title": null, "reason": "no usable hits"}'),
    ]
    comp = _async_completion_mock(seq)
    with patch("paperhub.agents.research_pipeline.litellm.acompletion", new=comp):
        out = await discover_canonical(
            ParsedRequest(hint="obscure paper", kind="natural_language"),
            tracer=fake_tracer, model="m", mcp_registry=reg,  # type: ignore[arg-type]
        )
    assert out is None


async def test_discover_trace_records_full_context(
    fake_tracer: Tracer,
    migrated_db: aiosqlite.Connection,
) -> None:
    """The Discoverer's tracer rows must capture full LLM content +
    tool-call list, AND the web.search result must record the actual
    top-N hits (not just ``count``). Without this, post-hoc debugging
    of a discovery loop is blind to what the LLM actually saw and
    decided — which is exactly the regression run 65 surfaced."""
    web_hit = {
        "title": "Mamba: Linear-Time Sequence Modeling",
        "url": "https://arxiv.org/abs/2312.00752",
        "snippet": "Mamba is a state-space model with selective scans.",
    }
    reg = _StubRegistry(web_hits=[web_hit])
    identity_json = json.dumps({
        "title": "Mamba: Linear-Time Sequence Modeling with Selective State Spaces",
        "author_surname": "Gu",
        "year": 2023,
        "confidence": "high",
        "rationale": "Top hit names Gu & Dao 2023.",
    })
    seq = [
        _msg(tool_calls=[_tool_call(
            "c1", "paperhub.search_web",
            {"paper_hint": "mamba paper", "extra_terms": []},
        )]),
        _msg(content=identity_json),
    ]
    comp = _async_completion_mock(seq)
    with patch("paperhub.agents.research_pipeline.litellm.acompletion", new=comp):
        out = await discover_canonical(
            ParsedRequest(hint="mamba paper", kind="natural_language"),
            tracer=fake_tracer, model="m", mcp_registry=reg,  # type: ignore[arg-type]
        )
    assert out is not None

    rows: list[dict[str, Any]] = []
    async with migrated_db.execute(
        "SELECT tool, result_summary_json FROM tool_calls "
        "WHERE run_id=1 ORDER BY step_index",
    ) as cur:
        async for r in cur:
            rows.append({"tool": r[0], "result": json.loads(r[1] or "{}")})

    plan_rows = [r for r in rows if r["tool"] == "paper_search:discover_plan"]
    web_rows = [r for r in rows if r["tool"] == "paper_search:paperhub.search_web"]

    # discover_plan iteration 0: tool call recorded with its name + args.
    assert plan_rows[0]["result"].get("tool_calls"), (
        f"discover_plan must record the actual tool_calls; got {plan_rows[0]['result']!r}"
    )
    assert plan_rows[0]["result"]["tool_calls"][0]["name"] == "paperhub.search_web"
    # discover_plan iteration 1: full content from the LLM, not just length.
    assert "Linear-Time" in plan_rows[1]["result"].get("content", ""), (
        f"discover_plan finalize must record full content; got {plan_rows[1]['result']!r}"
    )
    # web-call row: top hits stored verbatim (not just count).
    assert web_rows[0]["result"].get("top"), (
        f"web.search must record top hits; got {web_rows[0]['result']!r}"
    )
    assert web_rows[0]["result"]["top"][0]["url"] == web_hit["url"]


async def test_discover_wrapper_strips_quotes_from_paper_hint(
    fake_tracer: Tracer,
) -> None:
    """The structured-output wrapper sanitises the LLM's paper_hint:
    even if Gemini sneaks quotes into the field value, the underlying
    web.search query has no quotes. This is the structural guarantee
    that replaces the prompt rule against quoting.

    Probing the open-websearch daemon empirically showed that DDG
    returns 0 hits for ``"MolmoACT2"`` and 10 hits for the bare token
    — so this sanitiser is the difference between finding the paper
    and a confabulated NotFound."""
    reg = _StubRegistry(web_hits=[
        {"title": "MolmoAct 2", "url": "https://arxiv.org/abs/2605.02881"},
    ])
    seq = [
        # Adversarial LLM: tries to inject quotes into paper_hint AND
        # uses boolean OR syntax in extra_terms.
        _msg(tool_calls=[_tool_call(
            "c1", "paperhub.search_web",
            {
                "paper_hint": '"MolmoACT2"',
                "extra_terms": ['"paper"', "OR", "arxiv"],
            },
        )]),
        _msg(content=json.dumps({
            "title": "MolmoAct 2: Action Reasoning Models",
            "arxiv_id": "2605.02881",
            "confidence": "high",
            "rationale": "arxiv hit",
        })),
    ]
    comp = _async_completion_mock(seq)
    with patch("paperhub.agents.research_pipeline.litellm.acompletion", new=comp):
        out = await discover_canonical(
            ParsedRequest(hint="MolmoACT2", kind="natural_language"),
            tracer=fake_tracer, model="m", mcp_registry=reg,  # type: ignore[arg-type]
        )
    assert out is not None
    # The underlying web.search must have been called with a query
    # that has NO quotes (the structural guarantee).
    web_calls = [args for name, args in reg.call_log if name == "web.search"]
    assert len(web_calls) == 1
    built_query = web_calls[0]["query"]
    assert '"' not in built_query, (
        f"quotes leaked through the sanitiser: {built_query!r}"
    )
    assert " OR " not in built_query, (
        f"boolean OR leaked through the sanitiser: {built_query!r}"
    )
    assert "MolmoACT2" in built_query, (
        f"paper_hint token must survive sanitisation: {built_query!r}"
    )


async def test_discover_rejects_off_palette_tool_calls(
    fake_tracer: Tracer,
) -> None:
    """If the LLM hallucinates a tool name other than the exposed
    wrapper (e.g. directly calls web.search by name), the orchestrator
    must NOT dispatch — that would bypass the query sanitiser. Return
    an error tool message so the LLM corrects on the next turn."""
    reg = _StubRegistry(web_hits=[
        {"title": "should not appear", "url": "https://example.com"},
    ])
    seq = [
        # Adversarial: LLM tries to call web.search directly with a
        # quoted query, hoping to bypass the wrapper.
        _msg(tool_calls=[_tool_call(
            "c1", "web.search", {"query": '"MolmoACT2"'},
        )]),
        _msg(content='{"title": null, "reason": "tool error"}'),
    ]
    comp = _async_completion_mock(seq)
    with patch("paperhub.agents.research_pipeline.litellm.acompletion", new=comp):
        await discover_canonical(
            ParsedRequest(hint="MolmoACT2", kind="natural_language"),
            tracer=fake_tracer, model="m", mcp_registry=reg,  # type: ignore[arg-type]
        )
    # web.search must NOT have been dispatched.
    assert not [n for n, _ in reg.call_log if n == "web.search"], (
        f"off-palette web.search call leaked through: {reg.call_log!r}"
    )


async def test_discover_falls_back_when_web_not_in_registry(
    fake_tracer: Tracer,
) -> None:
    """No web.search → discover skips the LLM and returns a low-confidence
    fallback CanonicalIdentity built from the raw hint, so the Resolver
    still gets a chance to land the paper via Semantic Scholar."""
    reg = _StubRegistry(has_web_search=False)
    comp = AsyncMock()
    with patch("paperhub.agents.research_pipeline.litellm.acompletion", new=comp):
        out = await discover_canonical(
            ParsedRequest(hint="mamba", kind="natural_language"),
            tracer=fake_tracer, model="m", mcp_registry=reg,  # type: ignore[arg-type]
        )
    assert out is not None
    assert out.title == "mamba"
    assert out.confidence == "low"
    assert comp.await_count == 0


# ───────────────────────────── Resolver ─────────────────────────────


async def test_resolver_calls_ss_exactly_once(
    fake_tracer: Tracer,
) -> None:
    """Resolver invokes papers.search_semantic_scholar ONCE per request.

    This is the architectural property the v2.7 refactor enforces:
    SS rate-limiting protection is structural, not a prompt rule.
    """
    reg = _StubRegistry(ss_hits=[
        {
            "paper_id": "arxiv:2312.00752",
            "title": "Mamba: Linear-Time Sequence Modeling with Selective State Spaces",
            "year": 2023,
            "authors": ["Albert Gu", "Tri Dao"],
            "arxiv_id": "2312.00752",
            "has_open_pdf": True,
        },
    ])
    identity = CanonicalIdentity(
        title="Mamba: Linear-Time Sequence Modeling with Selective State Spaces",
        author_surname="Gu", year=2023, confidence="high",
    )
    req = ParsedRequest(hint="mamba paper", kind="natural_language")
    out = await resolve_via_ss(
        req, identity, tracer=fake_tracer, mcp_registry=reg,  # type: ignore[arg-type]
    )
    assert out is not None
    assert out.paper_id == "arxiv:2312.00752"
    assert out.request is req
    assert out.identity is identity
    # Exactly one SS call — the structural invariant.
    ss_calls = [n for n, _ in reg.call_log if n == "papers.search_semantic_scholar"]
    assert len(ss_calls) == 1


async def test_resolver_returns_none_when_ss_empty(
    fake_tracer: Tracer,
) -> None:
    """SS empty → Resolver returns None; the subgraph treats this as
    'kick back to Discoverer' (or, after MAX_REFINEMENT_LOOPS, as
    NotFound)."""
    reg = _StubRegistry(ss_hits=[])
    out = await resolve_via_ss(
        ParsedRequest(hint="obscure", kind="natural_language"),
        CanonicalIdentity(title="some title", author_surname=None, year=None,
                          confidence="low"),
        tracer=fake_tracer, mcp_registry=reg,  # type: ignore[arg-type]
    )
    assert out is None
    # Still exactly one SS call attempted.
    ss_calls = [n for n, _ in reg.call_log if n == "papers.search_semantic_scholar"]
    assert len(ss_calls) == 1


async def test_resolver_uses_arxiv_id_when_present(
    fake_tracer: Tracer,
) -> None:
    """When the Discoverer extracted an arxiv ID, the Resolver queries
    SS with ``arXiv:<id>`` (much more reliable than title match) and
    returns the SS hit on success."""
    reg = _StubRegistry(ss_hits=[
        {"paper_id": "arxiv:2510.10274", "title": "X-VLA",
         "year": 2025, "arxiv_id": "2510.10274"},
    ])
    identity = CanonicalIdentity(
        title="X-VLA: Soft-Prompted Transformer …",
        author_surname="Zheng", year=2025, confidence="high",
        arxiv_id="2510.10274",
    )
    out = await resolve_via_ss(
        ParsedRequest(hint="X-VLA", kind="natural_language"),
        identity, tracer=fake_tracer, mcp_registry=reg,  # type: ignore[arg-type]
    )
    assert out is not None
    assert out.paper_id == "arxiv:2510.10274"
    # SS was called with the arxiv-id query shape.
    assert reg.call_log[0][1]["query"] == "arXiv:2510.10274"


async def test_resolver_verifies_arxiv_id_when_ss_misses(
    fake_tracer: Tracer,
) -> None:
    """When SS hasn't indexed an arxiv_id the Discoverer claims, the
    Resolver VERIFIES the id against the arXiv API rather than trusting
    the LLM's identity. On a hit it adopts arXiv's AUTHORITATIVE
    title/meta — the LLM's guessed title is discarded. This is the fix
    for the title-mismatch bug: SS/arXiv sources preserve the real
    paper name; the LLM never determines it."""
    reg = _StubRegistry(ss_hits=[])  # SS misses
    identity = CanonicalIdentity(
        # The LLM's guess — deliberately wrong to prove it's discarded.
        title="WRONG LLM-GUESSED TITLE",
        author_surname="Zheng", year=2025, confidence="high",
        arxiv_id="2510.10274",
    )

    async def fake_lookup(arxiv_id: str) -> ArxivResult | None:
        assert arxiv_id == "2510.10274"
        return ArxivResult(
            arxiv_id="2510.10274",
            title="X-VLA: Soft-Prompted Transformer as Scalable …",
            authors=["Jinliang Zheng"], year=2025, abstract="real abstract",
        )

    out = await resolve_via_ss(
        ParsedRequest(hint="X-VLA", kind="natural_language"),
        identity, tracer=fake_tracer, mcp_registry=reg,  # type: ignore[arg-type]
        arxiv_lookup=fake_lookup,
    )
    assert out is not None, "verified ResolvedPaper expected on arXiv hit"
    assert out.paper_id == "arxiv:2510.10274"
    assert out.meta["arxiv_id"] == "2510.10274"
    # Authoritative arXiv title wins; the LLM's guess is gone.
    assert out.meta["title"] == "X-VLA: Soft-Prompted Transformer as Scalable …"
    assert out.meta["title"] != identity.title
    assert out.meta["has_open_pdf"] is True


async def test_resolver_emits_unverified_arxiv_id_for_download_gate(
    fake_tracer: Tracer,
) -> None:
    """When SS misses AND arXiv can't confirm the id, verification is
    INCONCLUSIVE — overwhelmingly a transient 429/503 against
    export.arxiv.org, NOT proof the paper is nonexistent. Per
    'unverified ≠ nonexistent', the Resolver must NOT drop it: it emits
    the candidate (built from the Discoverer hint + claimed arxiv_id) and
    lets the auto-attach DOWNLOAD be the real validity gate. A genuinely
    bogus id simply fails to download and never lands; a real-but-unindexed
    paper gets through. Ingest replaces the hint title with arXiv's
    authoritative one, so any paraphrase is only ever transient."""
    reg = _StubRegistry(ss_hits=[])  # SS misses
    identity = CanonicalIdentity(
        title="Load Balancing MoE with Similarity Preserving Routers",
        author_surname="Omi", year=2025, confidence="high",
        arxiv_id="2506.14038",  # real, but SS unindexed + arXiv throttled
    )

    async def fake_lookup(arxiv_id: str) -> ArxivResult | None:
        return None  # arXiv couldn't confirm (e.g. 429) — inconclusive

    out = await resolve_via_ss(
        ParsedRequest(hint="load balancing moe", kind="natural_language"),
        identity, tracer=fake_tracer, mcp_registry=reg,  # type: ignore[arg-type]
        arxiv_lookup=fake_lookup,
    )
    assert out is not None, "unverified-but-plausible id must be EMITTED, not dropped"
    assert out.paper_id == "arxiv:2506.14038"
    assert out.meta["arxiv_id"] == "2506.14038"
    assert out.meta["verified"] is False
    # The hint title is carried through (ingest will overwrite with the
    # authoritative arXiv title once the download lands).
    assert out.meta["title"] == identity.title


async def test_resolver_extracts_arxiv_id_from_evidence_safety_net(
    fake_tracer: Tracer,
) -> None:
    """If the LLM forgets to emit ``arxiv_id`` but the tool messages
    contained an arxiv URL, the server-side parser must extract it
    anyway. This is the safety net that prevents an LLM lapse from
    costing us the arxiv-id resolution path."""
    reg = _StubRegistry(web_hits=[
        {"title": "X-VLA paper",
         "url": "https://arxiv.org/abs/2510.10274",
         "snippet": "X-VLA on arxiv"},
    ])
    # LLM emits identity JSON WITHOUT arxiv_id field.
    identity_json = json.dumps({
        "title": "X-VLA",
        "author_surname": "Zheng", "year": 2025, "confidence": "high",
        "rationale": "found on arxiv",
    })
    seq = [
        _msg(tool_calls=[_tool_call(
            "c1", "paperhub.search_web",
            {"paper_hint": "X-VLA", "extra_terms": ["arxiv"]},
        )]),
        _msg(content=identity_json),
    ]
    comp = _async_completion_mock(seq)
    with patch("paperhub.agents.research_pipeline.litellm.acompletion", new=comp):
        out = await discover_canonical(
            ParsedRequest(hint="X-VLA", kind="natural_language"),
            tracer=fake_tracer, model="m", mcp_registry=reg,  # type: ignore[arg-type]
        )
    assert out is not None
    assert out.arxiv_id == "2510.10274", (
        f"safety net must mine arxiv ID from tool-message URL; got {out!r}"
    )


# ─────────────────────────── Synthesizer ────────────────────────────


async def test_synthesizer_writes_prose_for_resolved_set(
    fake_tracer: Tracer,
) -> None:
    """Synthesizer is called with resolved + not_found context."""
    resolved = [
        ResolvedPaper(
            request=ParsedRequest(hint="mamba", kind="natural_language"),
            identity=CanonicalIdentity(
                title="Mamba", author_surname="Gu", year=2023, confidence="high"),
            paper_id="arxiv:2312.00752",
            meta={"title": "Mamba"},
        ),
    ]
    comp = _async_completion_mock([
        _msg(content="The Mamba paper by Gu & Dao (2023) introduced selective SSMs..."),
    ])
    with patch("paperhub.agents.research_pipeline.litellm.acompletion", new=comp):
        prose = await synthesize_prose(
            resolved, [],
            user_message="the mamba paper",
            tracer=fake_tracer, model="m",
        )
    assert "Mamba" in prose
    assert comp.await_count == 1


async def test_synthesizer_handles_all_not_found(
    fake_tracer: Tracer,
) -> None:
    """Empty resolved + non-empty not_found → honest 'I couldn't find'
    prose. The synthesizer prompt's contract is that it must say so AND
    ask one clarifying question."""
    not_found = [ParsedRequest(hint="quantum cucumber paper", kind="natural_language")]
    comp = _async_completion_mock([
        _msg(content=(
            "I couldn't find a clear match for 'quantum cucumber paper'. "
            "Do you have an arxiv ID or the lead author's name?"
        )),
    ])
    with patch("paperhub.agents.research_pipeline.litellm.acompletion", new=comp):
        prose = await synthesize_prose(
            [], not_found,
            user_message="find the quantum cucumber paper",
            tracer=fake_tracer, model="m",
        )
    assert "couldn't find" in prose.lower()
    assert "?" in prose  # clarifying question


# ──────────────────────── Finalize-step observability ───────────────────


async def test_paper_search_finalize_records_emitted_candidates(
    fake_tracer: Tracer,
    migrated_db: aiosqlite.Connection,
) -> None:
    """Harness-eval observability: running the full paper_search subgraph
    must leave a ``paper_search:finalize`` tracer row recording the
    candidates emitted to the user (paper_id + title + finalize) plus the
    resolved / not_found breakdown — so an eval can score the final
    output from the trace alone, not from the ephemeral SSE event."""
    from unittest.mock import MagicMock

    from paperhub.agents.research_graph import (
        ResearchDeps,
        build_paper_search_subgraph,
    )
    from paperhub.pipelines.paper_pipeline import PaperPipeline
    from paperhub.rag.retriever import Retriever

    # arxiv_id path: parse is deterministic (no LLM), discover short-
    # circuits (no LLM), resolve queries SS once, finalize builds the
    # candidate + calls synthesize (the single LLM turn).
    reg = _StubRegistry(ss_hits=[
        {"paper_id": "arxiv:2312.00752", "title": "Mamba",
         "year": 2023, "arxiv_id": "2312.00752"},
    ])
    comp = _async_completion_mock([_msg(content="Found Mamba.")])

    deps = ResearchDeps(
        adapter=MagicMock(),
        tracer=fake_tracer,
        paper_qa_model="m",
        conn=migrated_db,
        pipeline=MagicMock(spec=PaperPipeline),
        retriever=MagicMock(spec=Retriever),
        mcp_registry=reg,  # type: ignore[arg-type]
    )
    graph = build_paper_search_subgraph(deps)
    state: dict[str, Any] = {
        "run_id": 1,
        "branch": "",
        "session_id": 1,
        "user_message": "2312.00752",
        "history": [],
    }
    with patch("paperhub.agents.research_pipeline.litellm.acompletion", new=comp):
        async for _mode, _payload in graph.astream(
            state, stream_mode=["custom", "values"],
        ):
            pass

    async with migrated_db.execute(
        "SELECT result_summary_json FROM tool_calls "
        "WHERE run_id=1 AND tool='paper_search:finalize'",
    ) as cur:
        row = await cur.fetchone()
    assert row is not None, "paper_search:finalize must open a tracer step"
    result = json.loads(row[0] or "{}")
    assert result.get("emitted_candidates") == [
        {"paper_id": "arxiv:2312.00752", "title": "Mamba", "finalize": True},
    ], "finalize must record the emitted candidate paper_ids + titles + flag"
    assert result.get("not_found") == []
    assert result.get("resolved_count") == 1


# ──────────────────────── Public-API dataclass smoke ────────────────────


async def test_parse_resolves_topic_from_brief(
    fake_tracer: Tracer,
) -> None:
    """Contract test: a self-contained brief (resolved by the router) yields
    a non-empty request list from the Parser.  The behavioural change is at
    the call site in research_graph.py (effective_query → parse_user_message);
    this test pins the contract that the Parser handles a topical brief."""
    comp = _async_completion_mock([
        _msg(content='[{"hint":"discrete diffusion distillation","kind":"natural_language"}]'),
    ])
    brief = "recommend representative papers on discrete diffusion distillation"
    with patch("paperhub.agents.research_pipeline.litellm.acompletion", new=comp):
        reqs = await parse_user_message(
            brief, tracer=fake_tracer, model="gpt-4o-mini",
        )
    assert len(reqs) == 1
    assert reqs[0].kind == "natural_language"


async def test_paper_search_subgraph_uses_suggest_slots(
    fake_tracer: Tracer,
    migrated_db: aiosqlite.Connection,
) -> None:
    """ResearchDeps.parse_slot / synth_slot thread through to parse_user_message
    and synthesize_prose. With the suggest parse prompt the mock LLM returns
    2 angle hints → ≥2 candidates emitted via search_results SSE event."""
    from unittest.mock import MagicMock

    from paperhub.agents.research_graph import (
        ResearchDeps,
        build_paper_search_subgraph,
    )
    from paperhub.pipelines.paper_pipeline import PaperPipeline
    from paperhub.rag.retriever import Retriever

    # The suggest parse prompt returns 2 distinct angle hints; the SS stub
    # resolves each to a unique paper so both land as candidates.
    two_angle_parse = json.dumps([
        {"hint": "flow matching for discrete diffusion", "kind": "natural_language"},
        {"hint": "distillation for discrete diffusion", "kind": "natural_language"},
    ])

    # LLM call sequence:
    #  call 0 → parse (suggest slot) — returns 2-angle array
    #  call 1 → synthesize (suggest slot) — returns prose
    # Discover is bypassed (no web search) and Resolver calls the stub reg.
    # We give the stub 2 different SS hits (cycling) so both angles resolve
    # to distinct candidates.

    # Override the stub to return 2 different hits by cycling.
    call_idx: dict[str, int] = {"n": 0}

    ss_hits_list = [
        [{"paper_id": "arxiv:2501.00001", "title": "Flow Matching DiffLM",
          "year": 2025, "arxiv_id": "2501.00001",
          "authors": ["Smith"], "has_open_pdf": True, "abstract": "A."}],
        [{"paper_id": "arxiv:2501.00002", "title": "Distillation for Discrete Diffusion",
          "year": 2025, "arxiv_id": "2501.00002",
          "authors": ["Jones"], "has_open_pdf": True, "abstract": "B."}],
    ]

    class _CyclingRegistry(_StubRegistry):
        async def call(self, name: str, args: dict[str, Any]) -> Any:
            self.call_log.append((name, args))
            if name == "papers.search_semantic_scholar":
                idx = call_idx["n"] % len(ss_hits_list)
                call_idx["n"] += 1
                return list(ss_hits_list[idx])
            raise RuntimeError(f"_CyclingRegistry: unexpected tool {name!r}")

    cycling_reg = _CyclingRegistry(has_web_search=False)

    captured_candidates: list[Any] = []

    comp_responses = [
        _msg(content=two_angle_parse),           # call 0: parse (suggest slot)
        _msg(content="Here are suggested papers about discrete diffusion."),  # call 1: synthesize
    ]
    comp = _async_completion_mock(comp_responses)

    deps = ResearchDeps(
        adapter=MagicMock(),
        tracer=fake_tracer,
        paper_qa_model="m",
        conn=migrated_db,
        pipeline=MagicMock(spec=PaperPipeline),
        retriever=MagicMock(spec=Retriever),
        mcp_registry=cycling_reg,  # type: ignore[arg-type]
        parse_slot="paper_search_parse_suggest/v1",
        synth_slot="paper_search_synthesize_suggest/v1",
    )
    graph = build_paper_search_subgraph(deps)

    state: dict[str, Any] = {
        "run_id": fake_tracer._run_id,  # noqa: SLF001
        "branch": "",
        "session_id": 1,
        "user_message": "recommend papers on discrete diffusion",
        "history": [],
    }

    with patch("paperhub.agents.research_pipeline.litellm.acompletion", new=comp):
        async for mode, payload in graph.astream(
            state, stream_mode=["custom", "values"],
        ):
            if (
                mode == "custom"
                and isinstance(payload, dict)
                and payload.get("event") == "search_results"
            ):
                captured_candidates.extend(payload.get("candidates", []))

    assert len(captured_candidates) >= 2, (
        f"suggest slots must yield ≥2 candidates; got {captured_candidates!r}"
    )


async def test_parse_uses_provided_slot(
    fake_tracer: Tracer,
) -> None:
    """Passing slot='paper_search_parse_suggest/v1' routes to the suggest
    prompt. The suggest prompt contains 'angle' (search angles) but the
    default parse prompt does not — assert the system message seen by the
    LLM comes from the suggest slot."""
    seen: dict[str, Any] = {}

    async def fake_acompletion(*, model: Any, messages: Any, **kw: Any) -> Any:
        seen["messages"] = messages
        return {
            "choices": [{
                "message": {
                    "content": (
                        '[{"hint":"flow matching for discrete diffusion",'
                        '"kind":"natural_language"},'
                        '{"hint":"distillation for discrete diffusion",'
                        '"kind":"natural_language"}]'
                    ),
                },
            }],
        }

    with patch(
        "paperhub.agents.research_pipeline.litellm.acompletion",
        new=fake_acompletion,
    ):
        reqs = await parse_user_message(
            "recommend papers on flow matching and distillation for discrete diffusion",
            tracer=fake_tracer,
            model="gpt-4o-mini",
            slot="paper_search_parse_suggest/v1",
        )

    sys_msg = next(m["content"] for m in seen["messages"] if m["role"] == "system")
    # "angle" appears in paper_search_parse_suggest/v1 (search angles) but
    # NOT in the default paper_search_parse/v1.
    assert "angle" in sys_msg.lower(), (
        f"suggest parse slot must be used; system prompt was:\n{sys_msg[:300]}"
    )
    assert len(reqs) == 2


def test_dataclasses_serialise_via_asdict() -> None:
    """The chat layer / SSE wire shape depends on asdict() round-tripping
    cleanly for diagnostics — keep them dataclasses-compatible."""
    req = ParsedRequest(hint="mamba", kind="natural_language")
    identity = CanonicalIdentity(
        title="Mamba", author_surname="Gu", year=2023, confidence="high")
    resolved = ResolvedPaper(
        request=req, identity=identity, paper_id="arxiv:2312.00752", meta={},
    )
    d = asdict(resolved)
    assert d["paper_id"] == "arxiv:2312.00752"
    assert d["request"]["kind"] == "natural_language"
    assert d["identity"]["confidence"] == "high"
