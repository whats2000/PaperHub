"""Action tools for the core agent: library, discovery, memory, session, ideation.

Each tool wraps an existing capability. Tools never emit SSE themselves: UI
events travel in ``ToolOutcome.events`` and the loop emits them.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any
from xml.sax.saxutils import escape

from paperhub.agents._mcp_result import normalize_mcp_result
from paperhub.agents.core.gather import register_gather_tool
from paperhub.agents.core.language import direct_answer_language
from paperhub.agents.core.permissions import memory_listing, visible_memories
from paperhub.agents.core.read_tools import register_read_tools
from paperhub.agents.core.tools import (
    Tier,
    ToolContext,
    ToolOutcome,
    ToolRegistry,
    ToolSpec,
    error_outcome,
)
from paperhub.agents.memory_gate import MemoryGateRefusal, classify_memory_scope
from paperhub.agents.memory_tools import (
    MemoryScopeError,
    add_memory_with_supersede,
    forget_memory,
)
from paperhub.agents.research import (
    FinalOnlyMessage,
    SearchCandidate,
    SearchResultsYield,
    ToolStepYield,
)
from paperhub.agents.research_tools import (
    NoIngestibleSourceError,
    add_paper_to_session_dispatch,
)
from paperhub.agents.sql_agent import sql_agent_stream
from paperhub.agents.state import AgentState
from paperhub.config import Settings, load_settings
from paperhub.db.connection import write_transaction
from paperhub.db.decks import get_deck
from paperhub.ideation.problems import insert_problem, start_problem_run
from paperhub.models.domain import DeckCommand
from paperhub.models.events import SearchCandidateModel
from paperhub.pipelines.paper_pipeline import PaperPipeline

QUERY_ROWS_CAP = 200
QUERY_ROWS_SHOWN = 20
IDEAS_LIST_CAP = 100
# Tools that rebuild or edit the chat's one deck (read-modify-write).
DECK_TOOLS = frozenset({"create_deck", "edit_slides", "write_notes"})
IDEA_STATUSES = ("new", "saved", "trying", "worked", "failed", "dismissed")


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


def _card(c: SearchCandidate) -> dict[str, Any]:
    return SearchCandidateModel(**asdict(c)).model_dump()


_DECK_SUMMARY_KEYS = ("title", "page_count", "status", "version_id", "has_notes")


def _deck_content(record: dict[str, Any]) -> str:
    final = str(record.get("final") or "")
    deck = record.get("deck")
    if not isinstance(deck, dict):
        return final or "No deck was produced."
    summary = (
        f"Deck {deck.get('title')!r}: {deck.get('page_count')} pages, "
        f"status {deck.get('status')}, version {deck.get('version_id')}"
        f"{', with speaker notes' if deck.get('has_notes') else ''}."
    )
    return f"{summary}\n{final}".strip()


def render_action_content(name: str, record: dict[str, Any]) -> str:
    """The text the model sees for a successful action tool call, built from
    the call's trace record alone. The handlers use it too, so the feedback
    export rebuilds exactly what the model saw."""
    if name == "query_library":
        parts = [str(record.get("answer") or "")]
        queries = record.get("queries") or []
        last = queries[-1] if queries else None
        if isinstance(last, dict) and "error" not in last and last.get("rows"):
            total = int(last.get("total_rows") or 0)
            shown = list(last["rows"])[:QUERY_ROWS_SHOWN]
            body = "\n".join(" | ".join(str(c) for c in r) for r in shown)
            more = f"\n... ({total - len(shown)} more rows)" if total > len(shown) else ""
            parts.append(f"Rows ({total}) columns {last.get('columns')}:\n{body}{more}")
        ids = record.get("candidates") or []
        titles = record.get("candidate_titles") or []
        if ids:
            parts.append("Library papers: " + "; ".join(
                f"{pid} {title}" for pid, title in zip(ids, titles, strict=False)))
        return "\n\n".join(p for p in parts if p)
    if name == "search_papers":
        lines = [
            f"- {c['paper_id']} | {c['title']} | {c['year'] or 'n/a'} | {c['reason']}"
            + (" | already in this chat" if c["already_in_session"] else "")
            for c in record.get("candidates") or []
        ]
        return ("Candidates (not added; call add_paper to attach one):\n"
                + "\n".join(lines)) if lines else "No candidate papers were found."
    if name == "add_paper":
        if not record.get("added"):
            tried_urls = record.get("tried_urls") or []
            tried = ", ".join(tried_urls) if tried_urls else "none"
            return (f"Could not add {record.get('paper_id')}: no downloadable "
                    f"source was found (tried: {tried}). The user can upload the "
                    "PDF manually.")
        if record.get("prior_state") == "enabled":
            return (f"{record.get('title')!r} is already in this chat (paper id "
                    f"{record.get('paper_content_id')}); nothing changed.")
        return (f"Added {record.get('title')!r} to this chat (paper id "
                f"{record.get('paper_content_id')}).")
    if name == "remember":
        if not record.get("saved"):
            return f"Memory not saved: {record.get('refused')}"
        return (f"Saved to memory (id {record.get('memory_id')}, scope "
                f"{record.get('scope')}).")
    if name == "forget":
        if record.get("content"):
            return (f"Forgot memory {record.get('memory_id')} "
                    f"({record.get('content')!r}).")
        return f"Forgot memory {record.get('memory_id')}."
    if name == "remove_paper":
        return f"Removed {record.get('title')!r} from this chat."
    if name == "list_ideas":
        lines = [
            f"- #{i['id']} [{i['status']}] {i['title']}"
            + (f" - {i['rationale']}" if i.get("rationale") else "")
            for i in record.get("ideas") or []
        ]
        if not lines:
            return "No ideas found."
        return f"{record.get('total')} idea(s):\n" + "\n".join(lines)
    if name == "propose_ideas":
        return (f"Started idea generation for problem {record.get('problem_id')}. "
                "The ideas will appear on the Ideas page in a few minutes.")
    if name in ("create_deck", "write_notes"):
        return _deck_content(record)
    if name == "edit_slides":
        pages = record.get("pages") or []
        content = _deck_content(record)
        if not pages or pages[-1].get("ok"):
            return content
        done = [r["page"] for r in pages if r.get("ok")]
        return (f"Edited pages {done or 'none'}; page {pages[-1]['page']} was not "
                f"edited: {content}")
    raise KeyError(name)


# ------------------------------------------------------------ query_library


class _RecordingRegistry:
    """Proxy that keeps EVERY ``sql.query`` (sql + result) the agent ran."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.queries: list[tuple[str, Any]] = []

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        raw = await self._inner.call(name, args)
        if name == "sql.query":
            self.queries.append(
                (str(args.get("sql", "")), normalize_mcp_result(raw)))
        return raw


async def _query_library(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    question = str(args["question"])
    if ctx.mcp_registry is None:
        return error_outcome("The library database is not available right now.")
    rec = _RecordingRegistry(ctx.mcp_registry)
    answer_user = args.get("answer_user") is True
    state: AgentState = {**ctx.state, "effective_query": question}
    direct: dict[str, Any] = {}
    if answer_user:
        # The SQL agent's answer goes to the user as written, but it never
        # sees the conversation: give it the user's own words and the
        # language to answer in (a remembered language still wins there).
        user_message = str(ctx.state.get("user_message") or "")
        language = direct_answer_language(
            user_message, ctx.state.get("ui_language"))
        if user_message.strip() and user_message.strip() != question.strip():
            state["effective_query"] = (
                f"{question}\n<user_message>{escape(user_message)}"
                "</user_message>")
        state["response_language"] = language
        direct = {"response_language": language,
                  "effective_query": state["effective_query"]}
    answer_parts: list[str] = []
    cards: list[dict[str, Any]] = []
    async for item in sql_agent_stream(
        state,
        adapter=ctx.adapter, tracer=ctx.tracer, registry=rec,
        planner_model=ctx.settings.sql_agent_model, conn=ctx.conn,
        recall_enabled=ctx.settings.memory_recall_enabled,
        emit_tool_steps=False, overlap_describe=True,
    ):
        if isinstance(item, SearchResultsYield):
            cards.extend(_card(c) for c in item.candidates)
        elif isinstance(item, str):
            answer_parts.append(item)
    answer = "".join(answer_parts)
    queries: list[dict[str, Any]] = []
    budget = QUERY_ROWS_CAP
    for n, (q_sql, q_res) in enumerate(rec.queries, 1):
        if not isinstance(q_res, dict) or "error" in q_res:
            queries.append({"sql": q_sql, "columns": [], "rows": [], "total_rows": 0,
                            "error": str(q_res.get("error")) if isinstance(q_res, dict) else "failed"})
            continue
        q_rows = list(q_res.get("rows") or [])
        # The last query's shown rows are always kept, so the content the
        # model saw can be rebuilt from the record (feedback export).
        cap = max(budget, QUERY_ROWS_SHOWN) if n == len(rec.queries) else budget
        kept = q_rows[:cap]
        budget = max(budget - len(kept), 0)
        queries.append({"sql": q_sql, "columns": list(q_res.get("columns") or []),
                        "rows": kept, "total_rows": len(q_rows)})
    last = queries[-1] if queries else None
    events: list[tuple[str, dict[str, Any]]] = (
        [("search_results", {"candidates": cards})] if cards else []
    )
    record = {
        "question": question, "answer_user": answer_user, **direct,
        "queries": queries,
        "sql": last["sql"] if last else None, "answer": answer,
        "candidates": [c["paper_id"] for c in cards],
        "candidate_titles": [c["title"] for c in cards],
    }
    return ToolOutcome(
        content=render_action_content("query_library", record),
        record=record, events=events,
        final_answer=answer if answer_user and answer.strip() else None,
    )


# ------------------------------------------------------------ search_papers


async def _search_papers(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    from paperhub.api import chat as chat_module  # lazy: chat imports the core

    query = str(args["query"])
    mode = str(args.get("mode", "search"))
    if mode not in ("search", "suggest"):
        return error_outcome("mode must be 'search' or 'suggest'.")
    if ctx.mcp_registry is None:
        return error_outcome("Paper search is not available right now.")
    pipeline = PaperPipeline(ctx.conn, papers_cache_dir=ctx.settings.papers_cache_dir)
    state: AgentState = {**ctx.state, "effective_query": query}
    candidates: list[SearchCandidate] = []
    events: list[tuple[str, dict[str, Any]]] = []
    # Look the wrapper up at call time (tests patch it); it never attaches.
    async for item in chat_module.paper_search(
        state, adapter=ctx.adapter, tracer=ctx.tracer,
        model=ctx.settings.paper_qa_model, conn=ctx.conn, pipeline=pipeline,
        mcp_registry=ctx.mcp_registry, suggest=(mode == "suggest"),
    ):
        if isinstance(item, ToolStepYield):
            # Live progress (ruling R10): the pipeline's trace rows stream now.
            await ctx.emit("tool_step", {"record": item.record})
        elif isinstance(item, SearchResultsYield):
            candidates.extend(item.candidates)
    in_session: set[str] = set()
    async with ctx.conn.execute(
        "SELECT paper_content_id FROM papers WHERE session_id = ? AND enabled = 1",
        (ctx.session_id,),
    ) as cur:
        in_session = {f"library:{r[0]}" for r in await cur.fetchall()}
    cards = []
    for c in candidates:
        card = _card(c)
        card["already_in_session"] = bool(
            c.already_in_session or c.paper_id in in_session)
        cards.append(card)
    if cards:
        events.append(("search_results", {"candidates": cards}))
    record = {"query": query, "mode": mode, "candidates": cards}
    return ToolOutcome(
        content=render_action_content("search_papers", record), record=record,
        events=events,
    )


# ---------------------------------------------------------------- add_paper


def mark_paper_enabled(ctx: ToolContext, paper: int, title: str) -> None:
    """Make a paper attached during the turn readable by the read tools and
    ``gather`` in the turn's later steps."""
    if all(p != paper for p, _ in ctx.enabled_papers):
        ctx.enabled_papers.append((paper, title))


def mark_paper_disabled(ctx: ToolContext, paper: int) -> None:
    ctx.enabled_papers[:] = [(p, t) for p, t in ctx.enabled_papers if p != paper]


async def _session_paper_states(ctx: ToolContext) -> dict[int, bool]:
    """``{paper_content_id: enabled}`` of this chat's ``papers`` rows."""
    async with ctx.conn.execute(
        "SELECT paper_content_id, enabled FROM papers WHERE session_id = ?",
        (ctx.session_id,),
    ) as cur:
        return {int(r[0]): bool(r[1]) for r in await cur.fetchall()}


async def _add_paper(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    paper_id = str(args["paper_id"])
    before = await _session_paper_states(ctx)
    pipeline = PaperPipeline(ctx.conn, papers_cache_dir=ctx.settings.papers_cache_dir)
    try:
        result = await add_paper_to_session_dispatch(
            paper_id, pipeline=pipeline, conn=ctx.conn,
            session_id=ctx.session_id, metadata_override=None,
            unpaywall_email=ctx.settings.unpaywall_email,
        )
    except NoIngestibleSourceError as exc:
        failed = {"paper_id": paper_id, "added": False,
                  "reason": "no_ingestible_source", "tried_urls": exc.tried_urls}
        return ToolOutcome(
            content=render_action_content("add_paper", failed), record=failed)
    # The link helpers re-enable a removed row; a row that was already
    # enabled is unchanged, so it gets no chip (its Undo would remove a
    # paper the user had before this turn).
    prior = before.get(result.paper_content_id)
    prior_state = "absent" if prior is None else ("enabled" if prior else "disabled")
    record = {"paper_id": paper_id, "added": True,
              "paper_content_id": result.paper_content_id,
              "papers_id": result.papers_id, "cache_hit": result.cache_hit,
              "title": result.title, "prior_state": prior_state}
    mark_paper_enabled(ctx, result.paper_content_id, result.title)
    if prior_state == "enabled":
        return ToolOutcome(
            content=render_action_content("add_paper", record), record=record)
    return ToolOutcome(
        content=render_action_content("add_paper", record), record=record,
        events=[("paper_added", {
            "papers_id": result.papers_id,
            "paper_content_id": result.paper_content_id,
            "title": result.title,
        })],
    )


# ------------------------------------------------------------------- memory


async def _remember(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    fact = str(args["fact"]).strip()
    scope = args.get("scope") or classify_memory_scope(fact)
    if scope not in ("session", "global"):
        return error_outcome("scope must be 'session' or 'global'.")
    try:
        memory_id = await add_memory_with_supersede(
            ctx.conn, session_id=ctx.session_id, content=fact, scope=scope,
            adapter=ctx.adapter, model=ctx.settings.memory_conflict_model,
        )
    except MemoryGateRefusal as exc:
        refused = {"saved": False, "refused": str(exc), "fact_chars": len(fact)}
        return ToolOutcome(
            content=render_action_content("remember", refused), record=refused)
    record = {"saved": True, "memory_id": memory_id, "scope": scope,
              "content": fact}
    return ToolOutcome(
        content=render_action_content("remember", record), record=record,
        events=[("memory_saved", {
            "memory_id": memory_id, "content": fact, "scope": scope})],
    )


async def _forget(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    memory_id = args["memory_id"]
    if not isinstance(memory_id, int) or isinstance(memory_id, bool):
        return error_outcome("memory_id must be an integer.")
    # Only an active memory this chat sees (its <memories> block) can be
    # forgotten: a guessed id must never delete another note.
    rows = await visible_memories(ctx)
    found = next((r for r in rows if r[0] == memory_id), None)
    if found is None:
        return error_outcome(
            f"Memory {memory_id} is not an active memory of this chat. "
            f"Active memories: {memory_listing(rows)}.")
    try:
        await forget_memory(ctx.conn, session_id=ctx.session_id, memory_id=memory_id)
    except MemoryScopeError as exc:
        return error_outcome(str(exc))
    record = {"forgotten": True, "memory_id": memory_id,
              "content": found[2], "scope": found[1]}
    return ToolOutcome(
        content=render_action_content("forget", record), record=record)


async def _remove_paper(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    paper = args["paper"]
    titles = {p: t for p, t in ctx.enabled_papers}
    if not isinstance(paper, int) or isinstance(paper, bool) or paper not in titles:
        listing = "; ".join(f"{p}: {t}" for p, t in ctx.enabled_papers) or "none"
        return error_outcome(
            f"Paper {paper!r} is not enabled in this chat. Enabled papers: {listing}.")
    async with write_transaction(ctx.conn):
        await ctx.conn.execute(
            "UPDATE papers SET enabled = 0 WHERE session_id = ? "
            "AND paper_content_id = ?", (ctx.session_id, paper))
    mark_paper_disabled(ctx, paper)
    record = {"removed": True, "paper_content_id": paper, "title": titles[paper]}
    return ToolOutcome(
        content=render_action_content("remove_paper", record), record=record)


# ----------------------------------------------------------------- ideation


async def _list_ideas(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    from paperhub.api.ideation import _ideas  # shared query helper

    flt = str(args.get("filter") or "active")
    if flt not in ("active", "all", *IDEA_STATUSES):
        return error_outcome(
            f"filter must be 'active', 'all' or one of {', '.join(IDEA_STATUSES)}.")
    clauses: list[str] = []
    params: list[Any] = []
    if flt == "active":
        clauses.append("status != 'dismissed'")
    elif flt != "all":
        clauses.append("status = ?")
        params.append(flt)
    problem_id = args.get("problem_id")
    if problem_id is not None:
        clauses.append("problem_id = ?")
        params.append(int(problem_id))
    ideas = await _ideas(ctx.conn, " AND ".join(clauses) or "1=1", tuple(params))
    total = len(ideas)
    ideas = ideas[:IDEAS_LIST_CAP]
    rows = [i.model_dump() for i in ideas]
    record = {"filter": flt, "problem_id": problem_id, "total": total, "ideas": rows}
    return ToolOutcome(
        content=render_action_content("list_ideas", record), record=record)


async def _propose_ideas(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    from paperhub.ideation import agent as ideation_agent
    from paperhub.ideation.models import PROBLEM_MAX_CHARS

    text = str(args["problem"]).strip()
    if not text:
        return error_outcome("describe the problem to find ideas for.")
    if len(text) > PROBLEM_MAX_CHARS:
        return error_outcome(f"the problem is longer than {PROBLEM_MAX_CHARS} characters.")
    pid = await insert_problem(ctx.conn, text=text)
    s = ctx.settings
    await start_problem_run(
        ctx.conn, pid,
        lambda: ideation_agent.run_problem(
            pid, db_path=s.db_path, adapter=ctx.adapter, model=s.ideation_model,
            card_model=s.ideation_card_model,
            thinking_budget=s.ideation_problem_thinking_budget),
    )
    record = {"problem_id": pid, "text": text}
    return ToolOutcome(
        content=render_action_content("propose_ideas", record), record=record)


# ------------------------------------------------------------------- slides


async def _run_slide_command(
    ctx: ToolContext, command: DeckCommand | None, query: str,
    record: dict[str, Any],
) -> ToolOutcome:
    """Drive the unchanged Report subgraph; ``command`` None = plain generate."""
    from paperhub.api import chat as chat_module  # lazy: chat imports the core
    DeckYield = chat_module.DeckYield

    state: AgentState = {**ctx.state, "effective_query": query}
    if command is not None:
        state["report_forced_command"] = command
    events: list[tuple[str, dict[str, Any]]] = []
    deck: dict[str, Any] | None = None
    final = ""
    async for item in chat_module.report_stream(
        state, adapter=ctx.adapter, tracer=ctx.tracer, conn=ctx.conn,
        settings=ctx.settings,
    ):
        if isinstance(item, ToolStepYield):
            # Live progress (ruling R10): stage heartbeats and the report
            # pipeline's trace rows stream while the deck builds.
            await ctx.emit("tool_step", {"record": item.record})
        elif isinstance(item, DeckYield):
            deck = item.deck
            events.append(("deck", item.deck))
        elif isinstance(item, FinalOnlyMessage):
            final = item.content
    rec = {
        "command": command.action if command else "generate", **record,
        "deck_id": deck.get("deck_id") if deck else None,
        "version_id": deck.get("version_id") if deck else None,
        # What the model is told (rebuilt by the feedback export). No deck:
        # no papers, no LaTeX, or a failure; the pipeline's message says why.
        "final": final,
        "deck": {k: deck.get(k) for k in _DECK_SUMMARY_KEYS} if deck else None,
    }
    return ToolOutcome(
        content=render_action_content("create_deck", rec), record=rec, events=events)


async def _create_deck(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    brief = str(args["brief"]).strip()
    slides = args.get("slides")
    language = args.get("language")
    query = brief
    if slides:
        query += f"\nTarget length: about {int(slides)} slides."
    if language:
        query += f"\nWrite the slide content in {language}."
    has_deck = await get_deck(ctx.conn, session_id=ctx.session_id) is not None
    command = (
        DeckCommand(action="regenerate", target_scope="all",
                    target_page=None, note_language=None)
        if has_deck else None  # no deck: _resolve never classifies
    )
    return await _run_slide_command(
        ctx, command, query,
        {"brief": brief, "slides": slides, "language": language})


_NO_DECK = "There is no deck in this chat yet; call create_deck first."


async def _edit_slides(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    instruction = str(args["instruction"]).strip()
    pages = [int(p) for p in (args.get("pages") or [])]
    if await get_deck(ctx.conn, session_id=ctx.session_id) is None:
        return error_outcome(_NO_DECK)
    if not pages:
        command = DeckCommand(action="edit_slides", target_scope="all",
                              target_page=None, note_language=None)
        return await _run_slide_command(
            ctx, command, instruction, {"brief": instruction, "pages": []})
    # One forced single-page edit per requested page, in order, so only the
    # named pages change. Stop at the first failure.
    events: list[tuple[str, dict[str, Any]]] = []
    results: list[dict[str, Any]] = []
    last: ToolOutcome | None = None
    for page in pages:
        command = DeckCommand(action="edit_slides", target_scope="page",
                              target_page=page, note_language=None)
        last = await _run_slide_command(
            ctx, command, instruction, {"brief": instruction})
        events.extend(last.events)
        decks = [p for n, p in last.events if n == "deck"]
        ok = bool(decks) and decks[-1].get("status") == "ok"
        results.append({"page": page, "version_id": last.record.get("version_id"),
                        "ok": ok})
        if not ok:
            break
    assert last is not None
    record = {"command": "edit_slides", "brief": instruction, "pages": results,
              "deck_id": last.record.get("deck_id"),
              "version_id": last.record.get("version_id"),
              "final": last.record.get("final"), "deck": last.record.get("deck")}
    return ToolOutcome(
        content=render_action_content("edit_slides", record), record=record,
        events=events)


async def _write_notes(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    language = args.get("language")
    if await get_deck(ctx.conn, session_id=ctx.session_id) is None:
        return error_outcome(_NO_DECK)
    command = DeckCommand(action="generate_notes", target_scope="all",
                          target_page=None, note_language=language)
    query = "Write the speaker notes for this deck." + (
        f" Write them in {language}." if language else "")
    return await _run_slide_command(
        ctx, command, query, {"brief": query, "language": language})


# ----------------------------------------------------------------- registry


def register_action_tools(registry: ToolRegistry) -> None:
    specs = [
        ToolSpec(
            "query_library",
            "Ask a question about the user's whole paper library (counts, "
            "filters by year/author/topic, which papers they have). Answers "
            "from the library database and may return library:<id> paper cards. "
            "Set answer_user=true when that answer is all the user asked for "
            "(a count, a list): it goes to the user verbatim and you get no "
            "further step; ask `question` in the user's language then. Leave it "
            "false when you still need to read, compare or act.",
            _obj({"question": {"type": "string"},
                  "answer_user": {"type": "boolean"}}, ["question"]),
            Tier.FREE, _query_library),
        ToolSpec(
            "search_papers",
            "Find papers outside the chat: mode 'search' resolves a named "
            "paper, 'suggest' recommends papers on a topic. Returns candidates "
            "only; nothing is added until you call add_paper.",
            _obj({"query": {"type": "string"},
                  "mode": {"type": "string", "enum": ["search", "suggest"]}},
                 ["query"]),
            Tier.FREE, _search_papers),
        ToolSpec(
            "add_paper",
            "Add a candidate paper to this chat so it can be read. paper_id is "
            "a candidate id such as arxiv:2401.00001, ss:<id> or library:<id>.",
            _obj({"paper_id": {"type": "string"}}, ["paper_id"]),
            Tier.SHOW_UNDO, _add_paper),
        ToolSpec(
            "remember",
            "Save a fact or preference the user asked you to remember. scope "
            "'global' = about the user, 'session' = about this project; omit "
            "to classify automatically.",
            _obj({"fact": {"type": "string"},
                  "scope": {"type": "string", "enum": ["global", "session"]}},
                 ["fact"]),
            Tier.SHOW_UNDO, _remember),
        ToolSpec(
            "forget",
            "Delete one saved memory by its id from <memories>. Needs the "
            "user's confirmation.",
            _obj({"memory_id": {"type": "integer"}}, ["memory_id"]),
            Tier.CONFIRM, _forget),
        ToolSpec(
            "remove_paper",
            "Remove a paper from this chat (paper = its id among the chat's "
            "papers). Needs the user's confirmation.",
            _obj({"paper": {"type": "integer"}}, ["paper"]),
            Tier.CONFIRM, _remove_paper),
        ToolSpec(
            "list_ideas",
            "List research ideas from the Ideas page. filter: 'active' "
            "(default, not dismissed), 'all', or one status "
            f"({', '.join(IDEA_STATUSES)}); optionally for one problem_id.",
            _obj({"filter": {"type": "string"}, "problem_id": {"type": "integer"}},
                 []),
            Tier.FREE, _list_ideas),
        ToolSpec(
            "propose_ideas",
            "Start generating cross-domain research ideas for a problem. Only "
            "when the user asks for ideas. Runs in the background; results "
            "appear on the Ideas page.",
            _obj({"problem": {"type": "string"}}, ["problem"]),
            Tier.IF_ASKED, _propose_ideas),
        ToolSpec(
            "create_deck",
            "Build a Beamer slide deck from the papers in this chat (a new "
            "deck, or a rebuild when one exists). brief steers the story and "
            "audience; the content comes from the papers. Only when the user "
            "asks for slides.",
            _obj({"brief": {"type": "string"}, "slides": {"type": "integer"},
                  "language": {"type": "string"}}, ["brief"]),
            Tier.IF_ASKED, _create_deck),
        ToolSpec(
            "edit_slides",
            "Edit slides of the existing deck as instructed; pages = 1-based "
            "page numbers (omit for the whole deck). Only when the user asks.",
            _obj({"instruction": {"type": "string"},
                  "pages": {"type": "array", "items": {"type": "integer"}}},
                 ["instruction"]),
            Tier.IF_ASKED, _edit_slides),
        ToolSpec(
            "write_notes",
            "Write speaker notes for the existing deck, optionally in a given "
            "language. Only when the user asks.",
            _obj({"language": {"type": "string"}}, []),
            Tier.IF_ASKED, _write_notes),
    ]
    for s in specs:
        s.serial = s.name in DECK_TOOLS
        registry.register(s)


def build_default_registry(settings: Settings | None = None) -> ToolRegistry:
    """The core agent's full tool set (read, gather, action tools). Tools
    read settings from the ToolContext at call time; only whether the model
    may look at images changes the set."""
    if settings is None:
        settings = load_settings()
    registry = ToolRegistry()
    register_read_tools(registry, vision=settings.agent_vision)
    register_gather_tool(registry)
    register_action_tools(registry)
    return registry

