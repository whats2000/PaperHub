"""Sessions REST surface — eager session creation.

Provides POST /sessions so the frontend can obtain a backend session_id
before the first chat turn, making the Reference Sources drawer and Library
Browser available from app load.
"""
from __future__ import annotations

import json
from typing import Any

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from paperhub.config import load_settings
from paperhub.db.connection import open_db, write_transaction
from paperhub.db.fork import fork_session
from paperhub.db.pending_actions import action_states
from paperhub.db.tool_calls import drain_tool_calls_since
from paperhub.feedback.review import maybe_review_fork
from paperhub.llm.litellm_adapter import LiteLlmAdapter
from paperhub.models.domain import ToolCallRecord
from paperhub.models.events import AgentSummary, SearchCandidateModel

router = APIRouter()


class CreateSessionResponse(BaseModel):
    session_id: int


class ForkSessionRequest(BaseModel):
    # The forked user message's run_id — the fork copies everything strictly
    # before that message. The frontend resolves it from the clicked user
    # message (or its paired assistant message).
    run_id: int
    # UI language code: the feedback reviewer writes its doubt in it.
    ui_language: str | None = None


class ForkSessionResponse(BaseModel):
    session_id: int
    forked_message: str
    title: str


class SessionSummary(BaseModel):
    id: int
    title: str
    created_at: str
    updated_at: str
    message_count: int
    # Fork lineage (SRS v2.30): the session this one was forked FROM, or None
    # for a normal session. Drives the sidebar's indented fork grouping.
    forked_from_session_id: int | None = None
    # Plan H3: the idea this chat was opened from (idea_sessions), else None.
    # Drives the sidebar's Lightbulb marker.
    idea_id: int | None = None
    # H4: this chat's auto-approve switch for confirm-tier agent actions.
    auto_approve: bool = False


class RoutingDecisionOut(BaseModel):
    intent: str
    confidence: float
    reasoning: str


class ContributingPaperOut(BaseModel):
    id: int


class DeckOut(BaseModel):
    """Replayed slide-deck payload. Matches the SSE `deck` event shape so the
    frontend reuses its `DeckEventData` type (BUG2 — deck chip survives
    refresh)."""
    deck_id: int
    session_id: int
    page_count: int
    title: str
    status: str
    contributing_papers: list[ContributingPaperOut] = []
    has_notes: bool = False
    # F4.5: the version snapshot THIS turn stamped (NULL on legacy rows
    # before runs.deck_version_id existed, or runs that didn't produce a
    # version). The per-turn DeckChip uses this to drive "Switch to this
    # version" against /deck/versions/{vid}/restore.
    version_id: str | None = None


def load_search_results(raw: str | None) -> list[list[dict[str, Any]]]:
    """Parse ``runs.search_results_json`` into a list of card batches.

    Accepts the legacy single-list shape ``[...]`` (-> one batch) and the
    agent shape ``{"batches": [[...], ...]}``. NULL/empty/invalid -> ``[]``.
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if isinstance(data, list):
        return [data] if data else []
    if isinstance(data, dict) and isinstance(data.get("batches"), list):
        return [b for b in data["batches"] if isinstance(b, list)]
    return []


def _agent_summary(raw: str | None) -> AgentSummary | None:
    """The turn summary of an agent run from ``runs.agent_record_json``."""
    if not raw:
        return None
    try:
        record = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return AgentSummary.from_record(record) if isinstance(record, dict) else None


def _ui_events(raw: str | None) -> list[dict[str, Any]]:
    """The tool UI events an agent run kept in its record (ruling R11)."""
    if not raw:
        return []
    try:
        record = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    events = record.get("ui_events") if isinstance(record, dict) else None
    if not isinstance(events, list):
        return []
    return [
        e for e in events
        if isinstance(e, dict) and isinstance(e.get("event"), str)
        and isinstance(e.get("data"), dict)
    ]


async def _id_rows(
    conn: aiosqlite.Connection, sql: str, ids: set[int],
) -> dict[int, tuple[Any, ...]]:
    """``{id: row}`` for ``sql`` (whose ``IN`` list is ``{ids}``), one query."""
    if not ids:
        return {}
    ordered = sorted(ids)
    async with conn.execute(
        sql.format(ids=",".join("?" * len(ordered))), ordered,
    ) as cur:
        return {int(r[0]): tuple(r) for r in await cur.fetchall()}


def _int_ids(events: list[dict[str, Any]], event: str, key: str) -> set[int]:
    return {
        int(e["data"][key]) for e in events
        if e["event"] == event and isinstance(e["data"].get(key), int)
    }


def _paper_chip(
    row: tuple[Any, ...] | None, session_id: int,
) -> dict[str, bool]:
    """Undo applies to an enabled reference of this chat; a disabled one was
    undone; another chat's row (a fork's copy) is not this chat's to undo."""
    if row is None or int(row[1]) != session_id:
        return {"undoable": False, "undone": False}
    enabled = bool(row[2])
    return {"undoable": enabled, "undone": not enabled}


def _memory_chip(
    row: tuple[Any, ...] | None, session_id: int,
) -> dict[str, bool]:
    """Undo applies to an active memory of this chat (or a global one); a
    deleted or no-longer-active one was undone; another chat's session
    memory (a fork's copy) is not this chat's to undo."""
    if row is None:
        return {"undoable": False, "undone": True}
    scope, owner, status = str(row[1]), row[2], str(row[3])
    if scope == "session" and owner != session_id:
        return {"undoable": False, "undone": False}
    active = status == "active"
    return {"undoable": active, "undone": not active}


async def _replay_agent_events(
    conn: aiosqlite.Connection, session_id: int, raws: list[str | None],
) -> list[list[dict[str, Any]] | None]:
    """Each agent record's UI events joined with the live rows they point
    at, with one query per table for the whole chat."""
    per_run = [_ui_events(raw) for raw in raws]
    flat = [e for events in per_run for e in events]
    actions = await action_states(
        conn,
        [str(e["data"].get("action_id")) for e in flat
         if e["event"] == "confirm_request"],
        session_id=session_id,
    )
    papers = await _id_rows(
        conn, "SELECT id, session_id, enabled FROM papers WHERE id IN ({ids})",
        _int_ids(flat, "paper_added", "papers_id"))
    memories = await _id_rows(
        conn,
        "SELECT id, scope, session_id, status FROM memories WHERE id IN ({ids})",
        _int_ids(flat, "memory_saved", "memory_id"))

    out: list[list[dict[str, Any]] | None] = []
    for events in per_run:
        replayed: list[dict[str, Any]] = []
        for e in events:
            name, data = str(e["event"]), dict(e["data"])
            if name == "confirm_request":
                data.update(actions.get(
                    str(data.get("action_id")), {"status": "unavailable"}))
            elif name == "paper_added":
                pid = data.get("papers_id")
                data.update(_paper_chip(
                    papers.get(pid) if isinstance(pid, int) else None, session_id))
            elif name == "memory_saved":
                mid = data.get("memory_id")
                data.update(_memory_chip(
                    memories.get(mid) if isinstance(mid, int) else None, session_id))
            replayed.append({"event": name, "data": data})
        out.append(replayed or None)
    return out


class MessageOut(BaseModel):
    role: str
    content: str
    run_id: int | None
    run_status: str | None = None
    created_at: str
    # Pipeline (pre-H4) runs replay their routing badge; agent runs replay
    # their turn summary instead (SRS §III-10.4).
    routing_decision: RoutingDecisionOut | None = None
    agent_summary: AgentSummary | None = None
    # Every card flattened (the pre-H4 shape) plus the per-call batches an
    # agent turn may produce (one list per search).
    search_results: list[SearchCandidateModel] | None = None
    search_result_batches: list[list[SearchCandidateModel]] | None = None
    deck: DeckOut | None = None
    # Agent runs: the tool UI events (paper/memory chips, confirm cards) in
    # stream order, each confirm card joined with its action's current state.
    agent_events: list[dict[str, Any]] | None = None


@router.post("/sessions", response_model=CreateSessionResponse, status_code=201)
async def create_session() -> CreateSessionResponse:
    """Create an empty chat_sessions row.

    Used by the frontend to eagerly obtain a backend session_id before the
    first chat turn, so the Reference Sources drawer and Library Browser are
    usable from app load.
    """
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        cur = await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
        await conn.commit()
        session_id = cur.lastrowid
        if session_id is None:
            raise HTTPException(status_code=500, detail="session creation failed")
    return CreateSessionResponse(session_id=session_id)


@router.get("/sessions", response_model=list[SessionSummary])
async def list_sessions() -> list[SessionSummary]:
    """List chat sessions that have at least one message, most-recently-active
    first.

    This is the cross-device source of truth: the frontend fetches it on load
    so a session started in one browser appears in another. Empty sessions
    (eagerly created but never used) are excluded so they don't clutter the
    list on every device.
    """
    settings = load_settings()
    async with (
        open_db(settings.db_path) as conn,
        conn.execute(
            """
            SELECT s.id, s.title, s.created_at,
                   COALESCE(MAX(m.created_at), s.created_at) AS updated_at,
                   COUNT(m.id) AS message_count,
                   s.forked_from_session_id,
                   -- A subquery (not a join) so COUNT(m.id) isn't multiplied;
                   -- a session belongs to at most one idea in practice.
                   (SELECT MIN(i.idea_id) FROM idea_sessions i
                    WHERE i.session_id = s.id) AS idea_id,
                   s.auto_approve
            FROM chat_sessions s
            LEFT JOIN messages m ON m.session_id = s.id
            WHERE s.deleted_at IS NULL AND s.internal = 0
            GROUP BY s.id
            -- "Meaningful" = has messages OR a non-default title. A named chat
            -- carries intent even with no persisted messages yet; only the
            -- untouched 'New chat' empties are hidden as clutter.
            HAVING message_count > 0 OR s.title <> 'New chat'
            ORDER BY updated_at DESC, s.id DESC
            """,
        ) as cur,
    ):
        rows = await cur.fetchall()
    return [
        SessionSummary(
            id=int(r[0]),
            title=str(r[1]),
            created_at=str(r[2]),
            updated_at=str(r[3]),
            message_count=int(r[4]),
            forked_from_session_id=int(r[5]) if r[5] is not None else None,
            idea_id=int(r[6]) if r[6] is not None else None,
            auto_approve=bool(r[7]),
        )
        for r in rows
    ]


class PatchSessionRequest(BaseModel):
    auto_approve: bool | None = None


class PatchSessionResponse(BaseModel):
    id: int
    auto_approve: bool


@router.patch("/sessions/{session_id}", response_model=PatchSessionResponse)
async def patch_session(
    session_id: int, req: PatchSessionRequest,
) -> PatchSessionResponse:
    """Update per-chat settings (currently the agent auto-approve switch)."""
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        async with conn.execute(
            "SELECT auto_approve FROM chat_sessions "
            "WHERE id = ? AND deleted_at IS NULL", (session_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        value = bool(row[0])
        if req.auto_approve is not None:
            value = req.auto_approve
            async with write_transaction(conn):
                await conn.execute(
                    "UPDATE chat_sessions SET auto_approve = ? WHERE id = ?",
                    (int(value), session_id),
                )
    return PatchSessionResponse(id=session_id, auto_approve=value)


@router.get("/sessions/{session_id}/messages", response_model=list[MessageOut])
async def get_session_messages(session_id: int) -> list[MessageOut]:
    """Replay a session's message history in chronological order.

    Assistant messages carry their run's routing decision (when present) so the
    frontend can restore the routing pill on reload. The live trace panel and
    inline search-result cards are NOT reconstructed here — they remain
    derivable from `tool_calls` if a future feature needs them.
    """
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        async with conn.execute(
            "SELECT 1 FROM chat_sessions WHERE id = ?", (session_id,)
        ) as cur:
            if await cur.fetchone() is None:
                raise HTTPException(404, f"chat_sessions row {session_id} not found")
        # F4.5: join the session's deck row (one per session) so every
        # turn that stamped a version_id can carry its own DeckChip. We
        # ALSO read d.run_id so legacy runs (no deck_version_id) keep
        # the pre-F4.5 single-card behavior — gated below.
        async with conn.execute(
            """
            SELECT m.role, m.content, m.run_id, m.created_at,
                   r.routing_decision_json, r.search_results_json,
                   r.deck_version_id,
                   d.id, d.run_id, d.page_count, d.plan_json, d.status,
                   d.speaker_notes_json, d.contributing_paper_ids_json,
                   r.status, r.mode, r.agent_record_json
            FROM messages m
            LEFT JOIN runs r ON r.id = m.run_id
            LEFT JOIN decks d ON d.session_id = m.session_id
            WHERE m.session_id = ?
            ORDER BY m.id ASC
            """,
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        # Agent runs' UI events need live lookups (action status, undo).
        agent_runs: dict[int, str | None] = {}
        for (role_, _c, run_id_, *_rest, run_mode_, agent_record_) in rows:
            if role_ == "assistant" and run_mode_ == "agent" and run_id_ is not None:
                agent_runs.setdefault(int(run_id_), agent_record_)
        replayed = await _replay_agent_events(
            conn, session_id, list(agent_runs.values()))
        events_by_run = dict(zip(agent_runs, replayed, strict=True))

    out: list[MessageOut] = []
    for (
        role,
        content,
        run_id,
        created_at,
        routing_json,
        cards_json,
        deck_version_id,
        deck_id,
        deck_run_id,
        deck_page_count,
        deck_plan_json,
        deck_status,
        deck_notes_json,
        deck_paper_ids_json,
        run_status,
        run_mode,
        agent_record_json,
    ) in rows:
        decision: RoutingDecisionOut | None = None
        summary: AgentSummary | None = None
        cards: list[SearchCandidateModel] | None = None
        batches: list[list[SearchCandidateModel]] | None = None
        deck: DeckOut | None = None
        if role == "assistant":
            if run_mode == "agent":
                summary = _agent_summary(agent_record_json)
            elif routing_json:
                try:
                    decision = RoutingDecisionOut(**json.loads(routing_json))
                except (json.JSONDecodeError, TypeError, ValueError):
                    decision = None
            if cards_json:
                try:
                    batches = [
                        [SearchCandidateModel(**c) for c in batch]
                        for batch in load_search_results(cards_json)
                    ] or None
                except (json.JSONDecodeError, TypeError, ValueError):
                    batches = None
                if batches:
                    cards = [c for b in batches for c in b] or None
            # Surface a per-turn DeckChip when EITHER:
            #   (a) F4.5+: this run stamped a version snapshot
            #       (``runs.deck_version_id IS NOT NULL``), OR
            #   (b) legacy: the deck row's run_id matches this message's
            #       run_id (pre-F4.5 behavior, preserved so old DBs with
            #       no per-run version still surface a single card).
            attach = deck_id is not None and (
                deck_version_id is not None
                or (deck_run_id is not None and run_id is not None
                    and int(deck_run_id) == int(run_id))
            )
            if attach:
                deck = _build_deck_out(
                    deck_id=int(deck_id),
                    session_id=session_id,
                    page_count=int(deck_page_count or 0),
                    plan_json=deck_plan_json,
                    status=str(deck_status),
                    notes_json=deck_notes_json,
                    paper_ids_json=deck_paper_ids_json,
                    version_id=str(deck_version_id) if deck_version_id else None,
                )
        out.append(
            MessageOut(
                role=str(role),
                content=str(content),
                run_id=int(run_id) if run_id is not None else None,
                run_status=str(run_status) if run_status is not None else None,
                created_at=str(created_at),
                routing_decision=decision,
                agent_summary=summary,
                search_results=cards,
                search_result_batches=batches,
                deck=deck,
                agent_events=(
                    events_by_run.get(int(run_id))
                    if role == "assistant" and run_id is not None else None
                ),
            )
        )
    return out


@router.get(
    "/sessions/{session_id}/runs/{run_id}/trace",
    response_model=list[ToolCallRecord],
)
async def get_run_trace(session_id: int, run_id: int) -> list[ToolCallRecord]:
    """Lazily fetch a run's recorded tool_calls (the agent trace).

    The trace streams over `tool_step` SSE during a turn and is NOT replayed by
    GET /sessions/{id}/messages, so it vanishes on refresh. This reconstructs it
    on demand from `tool_calls` so the Trace panel can hydrate on click.
    Authorized by the run→session FK — a run is only readable through the
    session that owns it (else 404).
    """
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        async with conn.execute(
            "SELECT 1 FROM runs WHERE id = ? AND session_id = ?",
            (run_id, session_id),
        ) as cur:
            if await cur.fetchone() is None:
                raise HTTPException(
                    404, f"run {run_id} not found in session {session_id}"
                )
        # after_step=-1 so step_index 0 (the first step) is included.
        rows = await drain_tool_calls_since(conn, run_id, -1)
    return [ToolCallRecord(**row) for row in rows]


def _build_deck_out(
    *,
    deck_id: int,
    session_id: int,
    page_count: int,
    plan_json: str | None,
    status: str,
    notes_json: str | None,
    paper_ids_json: str | None,
    version_id: str | None = None,
) -> DeckOut:
    """Assemble a DeckOut from the joined `decks` columns, matching the SSE
    `deck` event shape. `title` comes from the plan (default 'Slides'),
    `has_notes` from non-empty speaker notes, `contributing_papers` from the
    persisted id list."""
    try:
        plan = json.loads(plan_json) if plan_json else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        plan = {}
    title = str(plan.get("title") or "Slides") if isinstance(plan, dict) else "Slides"

    try:
        notes = json.loads(notes_json) if notes_json else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        notes = {}
    has_notes = bool(notes)

    try:
        paper_ids = json.loads(paper_ids_json) if paper_ids_json else []
    except (json.JSONDecodeError, TypeError, ValueError):
        paper_ids = []
    contributing = [
        ContributingPaperOut(id=int(pid))
        for pid in paper_ids
        if isinstance(pid, int)
    ]

    return DeckOut(
        deck_id=deck_id,
        session_id=session_id,
        page_count=page_count,
        title=title,
        status=status,
        contributing_papers=contributing,
        has_notes=has_notes,
        version_id=version_id,
    )


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: int) -> None:
    """Delete a chat session.

    Two paths, chosen by whether the session is *meaningful* — it has messages
    OR a non-default title (a named chat carries intent even with no messages):

    * **Empty AND unnamed ('New chat', no messages) → hard delete.** There's
      nothing to undo, so the row is removed immediately. The FK cascade still
      applies:
        chat_sessions ─CASCADE→ papers / messages / runs ─CASCADE→ tool_calls
      (`paper_content` is never touched — papers are deduplicated and may be
      referenced by other sessions; only this session's membership rows go.)

    * **Meaningful (has messages or a name) → soft delete.** Set the tombstone
      so it disappears from GET /sessions on every device immediately, while
      Undo (POST /sessions/{id}/restore) can bring it back with full history.
      Tombstoned rows are purged after the retention window at startup
      (`purge_deleted_sessions`), reclaiming their storage.

    Idempotent on an already-soft-deleted session (204, no change). 404 only
    when the id never existed.
    """
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        async with conn.execute(
            "SELECT title FROM chat_sessions WHERE id = ? AND internal = 0",
            (session_id,),
        ) as cur:
            srow = await cur.fetchone()
        if srow is None:
            raise HTTPException(404, f"chat_sessions row {session_id} not found")
        title = str(srow[0])
        async with conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
        has_messages = row is not None and int(row[0]) > 0
        meaningful = has_messages or title != "New chat"

        if meaningful:
            await conn.execute(
                "UPDATE chat_sessions SET deleted_at = datetime('now') "
                "WHERE id = ? AND deleted_at IS NULL",
                (session_id,),
            )
        else:
            await conn.execute(
                "DELETE FROM chat_sessions WHERE id = ?", (session_id,),
            )
        await conn.commit()


@router.post("/sessions/{session_id}/restore", status_code=204)
async def restore_session(session_id: int) -> None:
    """Undo a soft delete — clear the tombstone so the session is live again
    on every device. 404 if the id never existed (e.g. it was an empty session
    that was hard-deleted, or already purged)."""
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        cur = await conn.execute(
            "UPDATE chat_sessions SET deleted_at = NULL "
            "WHERE id = ? AND internal = 0",
            (session_id,),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, f"chat_sessions row {session_id} not found")
        await conn.commit()


@router.post(
    "/sessions/{session_id}/fork",
    response_model=ForkSessionResponse,
    status_code=201,
)
async def fork_session_endpoint(
    session_id: int, req: ForkSessionRequest
) -> ForkSessionResponse:
    """Branch a NEW session from the point ABOVE a chosen user message.

    Copies every message strictly before the forked message (remapped runs),
    the session's enabled references, active session memories, and the deck
    (best-effort). The original session is untouched; the forked message text
    is returned so the frontend can prefill the composer (editable, not sent).
    """
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        # 404 (not 400) when the session is gone/tombstoned: fork_session raises
        # ValueError for BOTH "missing session" and "bad run_id", but only the
        # latter is a client 400. A soft-deleted session is invisible in
        # GET /sessions, so forking it must 404 too (not resurrect its history).
        async with conn.execute(
            "SELECT 1 FROM chat_sessions "
            "WHERE id = ? AND deleted_at IS NULL AND internal = 0",
            (session_id,),
        ) as cur:
            if await cur.fetchone() is None:
                raise HTTPException(404, f"chat_sessions row {session_id} not found")
        try:
            result = await fork_session(
                conn,
                source_session_id=session_id,
                fork_run_id=req.run_id,
                workspace_dir=settings.workspace_dir,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    # Plan H2: forking at a turn flags that turn's answer for a background
    # review (detached; never raises). No adapter is built when it is off.
    if settings.feedback_review_enabled:
        maybe_review_fork(
            settings=settings, session_id=session_id, target_run_id=req.run_id,
            llm=LiteLlmAdapter(), ui_language=req.ui_language,
        )
    return ForkSessionResponse(
        session_id=result.new_session_id,
        forked_message=result.forked_message,
        title=result.title,
    )
