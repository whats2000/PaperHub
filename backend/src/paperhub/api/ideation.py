"""Ideation REST surface (Plan H3, FR-20): problems, the idea graph, the idea
lifecycle, idea chat sessions and manual domain edits.

- **Graph scope.** ``problem_id`` → that problem's ideas, the documents they
  touch and those documents' domains; ``explore=1`` → the free-exploration
  ideas and every library document. Every idea in the scope is returned
  (dismissed ones too, so the client can Undo); a document's ``idea_count`` and
  ``Problem.idea_count`` exclude dismissed ideas (Ruling R3). So in problem
  scope a document touched only by dismissed ideas is still a node, with
  ``idea_count`` 0 — Undo brings its idea back without a refetch.
- **Citations.** Only library-internal edges between the scope's documents.
  A private document stays a node (drawn with a lock) but its edges are hidden:
  they came from public Semantic Scholar data.
- **Colours** are assigned server-side (Ruling O3) by every write that changes
  a domain's document set (card build, the domain edits below, a library
  delete); ``GET /graph`` is read-only and just returns them.
- **Lifecycle.** Every status change stores the old status in
  ``prev_status``; re-sending the current status is a no-op for it (else Undo
  would restore ``dismissed``). Dismissing needs ≥1 known reason (422).
  ``undo-dismiss`` restores ``prev_status`` exactly (e.g. ``trying``). Leaving
  ``dismissed`` clears its reasons and note.
- **Idea chat** opens a NEW session per call (titled with the idea), attaches
  the source (+ target) document and links it in ``idea_sessions``; the
  returned ``prefill`` is the opening message the user sends (visible and
  editable), not a hidden prompt.
- **Manual domains.** ``PUT /documents/{id}/domains`` replaces the document's
  tags with ``manual = 1`` rows (re-analysis never overwrites them); typed new
  names become user-curated domains. ``PATCH /domains/{id}`` renames
  (curated) or merges into another domain (tags move with their manual flag,
  the old domain is deleted, the target becomes curated); either way the old
  name is aliased (``domain_aliases``) so re-analysis keeps the edit.

Generation runs in the background through ``paperhub.ideation.agent``'s
``run_problem`` / ``run_explore`` (looked up at call time). A problem whose
run failed (``status = 'error'``) is restarted by ``POST
/problems/{id}/retry`` (409 for any other status). If starting the background
run raises after the problem row committed, the problem is marked ``error``
(so the UI offers Retry instead of polling forever) and the error re-raised.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import aiosqlite
from fastapi import APIRouter, HTTPException, Query, Request

from paperhub.api.deps import get_llm
from paperhub.config import load_settings
from paperhub.db.connection import open_db, write_transaction
from paperhub.feedback.g1_inbox import INBOX_NAME, append_inbox
from paperhub.ideation.domains import (
    assign_color_indices_tx,
    drop_orphan_domains_tx,
    manual_domain_id_tx,
    merge_domain_tx,
    normalize_domain,
    rename_domain_tx,
)
from paperhub.ideation.models import (
    DISMISS_REASONS,
    PROBLEM_MAX_CHARS,
    TRIED_MAX_CHARS,
    DismissReasonCount,
    DocumentDomainsPut,
    DocumentOut,
    DomainPatch,
    ExploreRequest,
    ExploreResponse,
    ExploreStatus,
    FeedbackSummary,
    FeedbackToG1Response,
    GraphCitation,
    GraphDocument,
    GraphDomain,
    GraphResponse,
    Idea,
    IdeaChatResponse,
    IdeaPatch,
    Problem,
    ProblemCreate,
)

router = APIRouter(prefix="/ideation", tags=["ideation"])

# Statuses that mean the user kept the idea; reaching one sets the sticky
# ``ideas.ever_saved`` flag (the save_rate numerator).
_EVER_SAVED = ("saved", "trying", "worked", "failed")

_IDEA_COLS = (
    "id, problem_id, source_doc, source_section, source_chunk_ids_json, "
    "target_doc, target_section, target_chunk_ids_json, title, rationale, "
    "status, prev_status, note, dismiss_reasons_json, created_at"
)
_PROBLEM_SELECT = (
    "SELECT p.id, p.text, p.tried, p.created_at, p.status, "
    "(SELECT COUNT(*) FROM ideas i WHERE i.problem_id = p.id "
    "AND i.status != 'dismissed') FROM problems p"
)


def _int_list(raw: Any) -> list[int]:
    try:
        return [int(x) for x in json.loads(raw or "[]")]
    except (ValueError, TypeError):
        return []


def _str_list(raw: Any) -> list[str]:
    try:
        return [str(x) for x in json.loads(raw or "[]")]
    except (ValueError, TypeError):
        return []


async def _problem(conn: aiosqlite.Connection, row: Any) -> Problem:
    """A problem row plus its run's card progress (in flight) or the latest
    run's skipped-document count (finished)."""
    from paperhub.ideation import agent as ideation_agent

    pid, text, tried, created_at, status, count = row
    p = Problem(id=int(pid), text=str(text), tried=str(tried or ""),
                created_at=str(created_at), status=status, idea_count=int(count))
    progress = ideation_agent.card_progress(p.id)
    if progress is not None:
        p.cards_ready, p.cards_total = progress.ready, progress.total
        p.skipped_docs = progress.skipped
    elif p.status != "generating":
        outcome = await ideation_agent.last_outcome(conn, p.id)
        p.skipped_docs = outcome.skipped
        p.nothing_relevant = outcome.nothing_relevant and count == 0
        p.nothing_relevant_reason = outcome.reason if p.nothing_relevant else ""
    return p


async def _get_problem(conn: aiosqlite.Connection, problem_id: int) -> Problem:
    async with conn.execute(f"{_PROBLEM_SELECT} WHERE p.id = ?", (problem_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(404, f"problem {problem_id} not found")
    return await _problem(conn, row)


async def _ideas(
    conn: aiosqlite.Connection, where: str, params: tuple[Any, ...],
) -> list[Idea]:
    async with conn.execute(
        f"SELECT {_IDEA_COLS} FROM ideas WHERE {where} ORDER BY id", params,
    ) as cur:
        rows = await cur.fetchall()
    if not rows:
        return []
    ids = [int(r[0]) for r in rows]
    sessions: dict[int, list[int]] = {}
    marks = ",".join("?" * len(ids))
    async with conn.execute(
        # A soft-deleted chat is gone for the user: never link it (QA I6).
        f"SELECT i.idea_id, i.session_id FROM idea_sessions i "
        f"JOIN chat_sessions s ON s.id = i.session_id "
        f"WHERE i.idea_id IN ({marks}) AND s.deleted_at IS NULL "
        "ORDER BY i.session_id", ids,
    ) as cur:
        for iid, sid in await cur.fetchall():
            sessions.setdefault(int(iid), []).append(int(sid))
    out: list[Idea] = []
    for (iid, prob, sdoc, ssec, schunks, tdoc, tsec, tchunks, title, rationale,
         status, prev, note, reasons, created_at) in rows:
        out.append(Idea(
            id=int(iid), problem_id=int(prob) if prob is not None else None,
            source_doc=int(sdoc), source_section=str(ssec),
            source_chunk_ids=_int_list(schunks),
            target_doc=int(tdoc) if tdoc is not None else None,
            target_section=str(tsec) if tsec is not None else None,
            target_chunk_ids=_int_list(tchunks), title=str(title),
            rationale=str(rationale or ""), status=status, prev_status=prev,
            note=str(note or ""), dismiss_reasons=_str_list(reasons),
            session_ids=sessions.get(int(iid), []), created_at=str(created_at),
        ))
    return out


async def _get_idea(conn: aiosqlite.Connection, idea_id: int) -> Idea:
    ideas = await _ideas(conn, "id = ?", (idea_id,))
    if not ideas:
        raise HTTPException(404, f"idea {idea_id} not found")
    return ideas[0]


async def _doc_domains(conn: aiosqlite.Connection) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    async with conn.execute(
        "SELECT paper_content_id, domain_id FROM document_domains "
        "ORDER BY paper_content_id, domain_id"
    ) as cur:
        for pid, did in await cur.fetchall():
            out.setdefault(int(pid), []).append(int(did))
    return out


async def _domains(
    conn: aiosqlite.Connection, ids: set[int] | None = None,
) -> list[GraphDomain]:
    async with conn.execute("SELECT id, name, color_index FROM domains ORDER BY id") as cur:
        rows = await cur.fetchall()
    return [
        GraphDomain(id=int(d), name=str(n), color_index=int(c) if c is not None else None)
        for d, n, c in rows if ids is None or int(d) in ids
    ]


# ---------------------------------------------------------------- graph


@router.get("/graph", response_model=GraphResponse)
async def graph(
    problem_id: int | None = Query(default=None),
    explore: int = Query(default=0),
) -> GraphResponse:
    if (problem_id is None) == (not explore):
        raise HTTPException(422, "pass exactly one of problem_id or explore=1")
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        problem: Problem | None = None
        if problem_id is not None:
            problem = await _get_problem(conn, problem_id)
        if problem is not None:
            ideas = await _ideas(conn, "problem_id = ?", (problem.id,))
        else:
            ideas = await _ideas(conn, "problem_id IS NULL", ())
        async with conn.execute(
            "SELECT id, title, is_private FROM paper_content ORDER BY id"
        ) as cur:
            library = [(int(r[0]), str(r[1] or ""), bool(r[2]))
                       for r in await cur.fetchall()]
        library_count = len(library)
        private_count = sum(1 for d in library if d[2])
        if problem is not None:
            touched = {i.source_doc for i in ideas} | {
                i.target_doc for i in ideas if i.target_doc is not None}
            library = [d for d in library if d[0] in touched]
        counts: dict[int, int] = {}
        for i in ideas:
            if i.status == "dismissed":
                continue
            for d in {i.source_doc, i.target_doc}:
                if d is not None:
                    counts[d] = counts.get(d, 0) + 1
        tags = await _doc_domains(conn)
        documents = [
            GraphDocument(id=d, title=t, domains=tags.get(d, []),
                          idea_count=counts.get(d, 0), is_private=p)
            for d, t, p in library
        ]
        public = {d.id for d in documents if not d.is_private}
        async with conn.execute(
            "SELECT source_id, target_id FROM citation_edges "
            "ORDER BY source_id, target_id"
        ) as cur:
            citations = [
                GraphCitation(source_id=int(s), target_id=int(t))
                for s, t in await cur.fetchall()
                if int(s) in public and int(t) in public
            ]
        used = {did for d in documents for did in d.domains}
        domains = await _domains(conn, used)
    return GraphResponse(documents=documents, domains=domains, citations=citations,
                         ideas=ideas, problem=problem, library_count=library_count,
                         private_count=private_count)


# ---------------------------------------------------------------- problems


def _start(
    kind: str, request: Request, problem_id: int | None = None, language: str = "en",
) -> None:
    # Late lookup: the agent module is resolved at call time (tests patch it).
    from paperhub.ideation import agent as ideation_agent

    settings = load_settings()
    kw: dict[str, Any] = {
        "db_path": settings.db_path, "adapter": get_llm(request),
        "model": settings.ideation_model, "card_model": settings.ideation_card_model,
    }
    if kind == "problem":
        assert problem_id is not None
        ideation_agent.run_problem(
            problem_id, **kw, thinking_budget=settings.ideation_problem_thinking_budget)
    else:
        ideation_agent.run_explore(**kw, language=language)


async def _start_problem(request: Request, conn: aiosqlite.Connection, pid: int) -> None:
    """Start the problem's run; on failure mark it ``error`` and re-raise."""
    try:
        _start("problem", request, pid)
    except Exception:
        async with write_transaction(conn):
            await conn.execute(
                "UPDATE problems SET status = 'error' WHERE id = ?", (pid,))
        raise


@router.get("/problems", response_model=list[Problem])
async def list_problems() -> list[Problem]:
    settings = load_settings()
    async with (
        open_db(settings.db_path) as conn,
        conn.execute(f"{_PROBLEM_SELECT} ORDER BY p.id DESC") as cur,
    ):
        return [await _problem(conn, r) for r in await cur.fetchall()]


@router.post("/problems", response_model=Problem)
async def create_problem(body: ProblemCreate, request: Request) -> Problem:
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "describe the problem")
    if len(text) > PROBLEM_MAX_CHARS:
        raise HTTPException(422, f"the problem is longer than {PROBLEM_MAX_CHARS} characters")
    if len(body.tried.strip()) > TRIED_MAX_CHARS:
        raise HTTPException(
            422, f"what you tried is longer than {TRIED_MAX_CHARS} characters")
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        async with write_transaction(conn):
            cur = await conn.execute(
                "INSERT INTO problems (text, tried, status) VALUES (?, ?, 'generating')",
                (text, body.tried.strip()),
            )
            assert cur.lastrowid is not None
            pid = int(cur.lastrowid)
        problem = await _get_problem(conn, pid)
        await _start_problem(request, conn, pid)
    return problem


@router.post("/problems/{problem_id}/retry", response_model=Problem)
async def retry_problem(problem_id: int, request: Request) -> Problem:
    """Re-run a failed problem's generation (409 unless it is ``error``)."""
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        async with write_transaction(conn):
            async with conn.execute(
                "SELECT status FROM problems WHERE id = ?", (problem_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                raise HTTPException(404, f"problem {problem_id} not found")
            if row[0] != "error":
                raise HTTPException(409, f"problem {problem_id} has not failed")
            await conn.execute(
                "UPDATE problems SET status = 'generating' WHERE id = ?", (problem_id,))
        problem = await _get_problem(conn, problem_id)
        await _start_problem(request, conn, problem_id)
    return problem


@router.get("/problems/{problem_id}", response_model=Problem)
async def get_problem(problem_id: int) -> Problem:
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        return await _get_problem(conn, problem_id)


@router.post("/explore", response_model=ExploreResponse)
async def explore(request: Request, body: ExploreRequest | None = None) -> ExploreResponse:
    _start("explore", request, language=(body or ExploreRequest()).language)
    return ExploreResponse(started=True)


@router.get("/explore/status", response_model=ExploreStatus)
async def explore_status() -> ExploreStatus:
    """Whether an explore run is in flight and the latest run's outcome
    (``last_new_count`` 0 = the re-run found nothing new; Ruling U3)."""
    from paperhub.ideation import agent as ideation_agent

    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        return await ideation_agent.explore_status(conn)


# ---------------------------------------------------------------- lifecycle


@router.patch("/ideas/{idea_id}", response_model=Idea)
async def patch_idea(idea_id: int, body: IdeaPatch) -> Idea:
    reasons: list[str] | None = None
    if body.dismiss_reasons is not None:
        reasons = list(dict.fromkeys(body.dismiss_reasons))
        unknown = [r for r in reasons if r not in DISMISS_REASONS]
        if unknown:
            raise HTTPException(422, f"unknown dismiss reasons: {unknown}")
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        # Read, validate and write in ONE transaction: a racing status change
        # must not compute ``prev_status`` from a stale read.
        async with write_transaction(conn):
            current = await _get_idea(conn, idea_id)
            target = body.status or current.status
            if target == "dismissed":
                if reasons == [] or (reasons is None and current.status != "dismissed"):
                    raise HTTPException(422, "dismissing needs at least one reason")
            elif reasons or (body.dismiss_note or "").strip():
                raise HTTPException(422, "dismiss reasons only describe a dismissed idea")
            sets: list[str] = []
            params: list[Any] = []
            if target != current.status:
                sets += ["status = ?", "prev_status = ?"]
                params += [target, current.status]
                if target in _EVER_SAVED:
                    sets.append("ever_saved = 1")
                if current.status == "dismissed":
                    sets += ["dismiss_reasons_json = '[]'", "dismiss_note = ''"]
            if target == "dismissed":
                if reasons is not None:
                    sets.append("dismiss_reasons_json = ?")
                    params.append(json.dumps(reasons))
                if body.dismiss_note is not None:
                    sets.append("dismiss_note = ?")
                    params.append(body.dismiss_note.strip())
            if body.note is not None:
                sets.append("note = ?")
                params.append(body.note)
            if sets:
                sets.append("updated_at = datetime('now')")
                await conn.execute(
                    f"UPDATE ideas SET {', '.join(sets)} WHERE id = ?",
                    (*params, idea_id),
                )
        return await _get_idea(conn, idea_id)


@router.post("/ideas/{idea_id}/undo-dismiss", response_model=Idea)
async def undo_dismiss(idea_id: int) -> Idea:
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        async with write_transaction(conn):
            cur = await conn.execute(
                "UPDATE ideas SET status = COALESCE(prev_status, 'new'), "
                "prev_status = 'dismissed', dismiss_reasons_json = '[]', "
                "dismiss_note = '', updated_at = datetime('now') "
                "WHERE id = ? AND status = 'dismissed'", (idea_id,),
            )
            changed = cur.rowcount
        idea = await _get_idea(conn, idea_id)
        if not changed:
            raise HTTPException(409, f"idea {idea_id} is not dismissed")
        return idea


# ---------------------------------------------------------------- idea chat


def _prefill(idea: Idea, titles: dict[int, str], problem_text: str | None) -> str:
    source = f'"{idea.source_section}" of {titles.get(idea.source_doc, "")}'.strip()
    if idea.target_doc is None:
        ask = (f'Let\'s dig into the idea "{idea.title}": apply the mechanism in '
               f"{source} to my problem: {problem_text or ''}".rstrip())
    else:
        target = (f'"{idea.target_section or ""}" of '
                  f'{titles.get(idea.target_doc, "")}')
        ask = (f'Let\'s dig into the idea "{idea.title}": borrow the mechanism in '
               f"{source} for {target}.")
    if idea.rationale.strip():
        ask += f"\n\nWhy it might work: {idea.rationale.strip()}"
    return ask + "\n\nHow would this work in practice, and what could break it?"


@router.post("/ideas/{idea_id}/chat", response_model=IdeaChatResponse)
async def idea_chat(idea_id: int) -> IdeaChatResponse:
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        idea = await _get_idea(conn, idea_id)
        docs = [idea.source_doc]
        if idea.target_doc is not None and idea.target_doc != idea.source_doc:
            docs.append(idea.target_doc)
        marks = ",".join("?" * len(docs))
        async with conn.execute(
            f"SELECT id, title FROM paper_content WHERE id IN ({marks})", docs,
        ) as cur:
            titles = {int(r[0]): str(r[1] or "") for r in await cur.fetchall()}
        problem_text: str | None = None
        if idea.problem_id is not None:
            async with conn.execute(
                "SELECT text FROM problems WHERE id = ?", (idea.problem_id,),
            ) as cur:
                row = await cur.fetchone()
            problem_text = str(row[0]) if row is not None else None
        async with write_transaction(conn):
            cur2 = await conn.execute(
                "INSERT INTO chat_sessions (title) VALUES (?)", (idea.title,),
            )
            assert cur2.lastrowid is not None
            session_id = int(cur2.lastrowid)
            await conn.executemany(
                "INSERT OR IGNORE INTO papers (session_id, paper_content_id) "
                "VALUES (?, ?)", [(session_id, d) for d in docs],
            )
            await conn.execute(
                "INSERT INTO idea_sessions (idea_id, session_id) VALUES (?, ?)",
                (idea_id, session_id),
            )
    return IdeaChatResponse(
        session_id=session_id, title=idea.title,
        prefill=_prefill(idea, titles, problem_text), paper_content_ids=docs,
    )


# ---------------------------------------------------------------- domains


@router.put("/documents/{doc_id}/domains", response_model=DocumentOut)
async def put_document_domains(doc_id: int, body: DocumentDomainsPut) -> DocumentOut:
    names = [n for n in body.new_names if n.strip()]
    if not body.domain_ids and not names:
        raise HTTPException(422, "a document needs at least one domain")
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        async with conn.execute(
            "SELECT title, is_private FROM paper_content WHERE id = ?", (doc_id,),
        ) as cur:
            doc = await cur.fetchone()
        if doc is None:
            raise HTTPException(404, f"document {doc_id} not found")
        async with write_transaction(conn):
            wanted = list(dict.fromkeys(body.domain_ids))
            if wanted:
                marks = ",".join("?" * len(wanted))
                async with conn.execute(
                    f"SELECT id FROM domains WHERE id IN ({marks})", wanted,
                ) as cur:
                    found = {int(r[0]) for r in await cur.fetchall()}
                missing = [d for d in wanted if d not in found]
                if missing:
                    raise HTTPException(422, f"unknown domains: {missing}")
            for name in names:
                did = await manual_domain_id_tx(conn, name)
                if did not in wanted:
                    wanted.append(did)
            async with conn.execute(
                "SELECT domain_id FROM document_domains WHERE paper_content_id = ?",
                (doc_id,),
            ) as cur:
                previous = {int(r[0]) for r in await cur.fetchall()}
            await conn.execute(
                "DELETE FROM document_domains WHERE paper_content_id = ?", (doc_id,))
            await conn.executemany(
                "INSERT INTO document_domains (paper_content_id, domain_id, manual) "
                "VALUES (?, ?, 1)", [(doc_id, d) for d in wanted],
            )
            await drop_orphan_domains_tx(conn, previous - set(wanted))
            await assign_color_indices_tx(conn)
        tags = (await _doc_domains(conn)).get(doc_id, [])
    return DocumentOut(id=doc_id, title=str(doc[0] or ""), domains=tags,
                       is_private=bool(doc[1]))


@router.patch("/domains/{domain_id}", response_model=list[GraphDomain])
async def patch_domain(domain_id: int, body: DomainPatch) -> list[GraphDomain]:
    if (body.name is None) == (body.merge_into is None):
        raise HTTPException(422, "pass exactly one of name or merge_into")
    name = " ".join((body.name or "").split())
    if body.name is not None and not name:
        raise HTTPException(422, "a domain needs a name")
    if body.merge_into == domain_id:
        raise HTTPException(422, "a domain cannot merge into itself")
    settings = load_settings()
    async with open_db(settings.db_path) as conn, write_transaction(conn):
        # Checks run inside the transaction so concurrent edits serialise
        # (a racing rename gets 409, not a UNIQUE-constraint 500).
        async with conn.execute("SELECT id, name FROM domains") as cur:
            existing = {int(r[0]): str(r[1]) for r in await cur.fetchall()}
        if domain_id not in existing:
            raise HTTPException(404, f"domain {domain_id} not found")
        if body.merge_into is not None:
            if body.merge_into not in existing:
                raise HTTPException(404, f"domain {body.merge_into} not found")
            await merge_domain_tx(conn, domain_id, existing[domain_id], body.merge_into)
        else:
            key = normalize_domain(name)
            if any(normalize_domain(n) == key for d, n in existing.items()
                   if d != domain_id):
                raise HTTPException(409, f"a domain named {name!r} already exists")
            await rename_domain_tx(conn, domain_id, existing[domain_id], name)
        await assign_color_indices_tx(conn)
        return await _domains(conn)


# ---------------------------------------------------------------- feedback


@router.get("/feedback-summary", response_model=FeedbackSummary)
async def feedback_summary() -> FeedbackSummary:
    async with (
        open_db(load_settings().db_path) as conn,
        conn.execute(
            "SELECT status, ever_saved, dismiss_reasons_json FROM ideas",
        ) as cur,
    ):
        rows = list(await cur.fetchall())
    ever = sum(int(ev) for _, ev, _ in rows)
    worked = sum(1 for st, _, _ in rows if st == "worked")
    failed = sum(1 for st, _, _ in rows if st == "failed")
    counts: dict[str, int] = {}
    for st, _, raw in rows:
        if st == "dismissed":
            for reason in _str_list(raw):
                counts[reason] = counts.get(reason, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return FeedbackSummary(
        save_rate=ever / len(rows) if rows else 0.0,
        worked_rate=worked / (worked + failed) if worked + failed else None,
        dismiss_reasons=[DismissReasonCount(reason=r, count=n) for r, n in ranked],
    )


@router.post("/feedback-to-g1", response_model=FeedbackToG1Response)
async def feedback_to_g1() -> FeedbackToG1Response:
    """Append saved / worked / failed / dismissed ideas to the G1 inbox.

    ``trying`` and ``new`` carry no verdict yet, so they are not exported.
    ``saved`` and ``worked`` are positives (``expect = {source_doc}``);
    ``failed`` and ``dismissed`` are negatives (``expect = {}`` plus
    ``negative_reasons``, for a human to label). ``variables`` are the recorded
    ``ideation:propose`` args of the run whose ``kept_ids`` produced the idea
    (``{}`` if that trace is gone). Idempotent per idea AND status
    (``case_id = idea<id>-<status>``): a later outcome appends a new line
    instead of being frozen behind the first export. The LATEST line of an idea
    wins: ``import-inbox --stage ideation`` skips an idea's earlier lines (a
    ``saved`` line superseded by a later ``failed`` one is not a positive).
    """
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        producers: dict[int, tuple[int, dict[str, Any]]] = {}
        async with conn.execute(
            "SELECT run_id, args_redacted_json, result_summary_json FROM tool_calls "
            "WHERE tool = 'ideation:propose' ORDER BY run_id, step_index",
        ) as cur:
            for run_id, args_raw, res_raw in await cur.fetchall():
                try:
                    kept = json.loads(res_raw or "{}").get("kept_ids") or []
                    args = json.loads(args_raw or "{}")
                except (ValueError, AttributeError):
                    continue
                if not isinstance(args, dict):
                    continue
                for iid in kept:
                    producers[int(iid)] = (int(run_id), args)
        ideas = await _ideas(
            conn, "status IN (?, ?, ?, ?)", ("saved", "worked", "failed", "dismissed"))
    lines: list[dict[str, Any]] = []
    for idea in ideas:
        run_id, args = producers.get(idea.id, (None, {}))
        positive = idea.status in ("saved", "worked")
        line: dict[str, Any] = {
            "case_id": f"idea{idea.id}-{idea.status}",
            "stage_hint": "ideation",
            "source_run_id": run_id,
            "variables": args,
            "observed": idea.model_dump(),
            "expect": {"source_doc": idea.source_doc} if positive else {},
        }
        if not positive:
            line["negative_reasons"] = idea.dismiss_reasons
        lines.append(line)
    appended = await asyncio.to_thread(
        append_inbox, settings.exports_dir / INBOX_NAME, lines)
    return FeedbackToG1Response(appended=appended)
