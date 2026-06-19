"""Sessions REST surface — eager session creation.

Provides POST /sessions so the frontend can obtain a backend session_id
before the first chat turn, making the Reference Sources drawer and Library
Browser available from app load.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.db.fork import fork_session
from paperhub.db.tool_calls import drain_tool_calls_since
from paperhub.models.domain import ToolCallRecord
from paperhub.models.events import SearchCandidateModel

router = APIRouter()


class CreateSessionResponse(BaseModel):
    session_id: int


class ForkSessionRequest(BaseModel):
    # The forked user message's run_id — the fork copies everything strictly
    # before that message. The frontend resolves it from the clicked user
    # message (or its paired assistant message).
    run_id: int


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


class RoutingDecisionOut(BaseModel):
    intent: str
    model_tier: str
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


class MessageOut(BaseModel):
    role: str
    content: str
    run_id: int | None
    run_status: str | None = None
    created_at: str
    routing_decision: RoutingDecisionOut | None = None
    search_results: list[SearchCandidateModel] | None = None
    deck: DeckOut | None = None


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
                   s.forked_from_session_id
            FROM chat_sessions s
            LEFT JOIN messages m ON m.session_id = s.id
            WHERE s.deleted_at IS NULL
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
        )
        for r in rows
    ]


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
                   r.status
            FROM messages m
            LEFT JOIN runs r ON r.id = m.run_id
            LEFT JOIN decks d ON d.session_id = m.session_id
            WHERE m.session_id = ?
            ORDER BY m.id ASC
            """,
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()

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
    ) in rows:
        decision: RoutingDecisionOut | None = None
        cards: list[SearchCandidateModel] | None = None
        deck: DeckOut | None = None
        if role == "assistant":
            if routing_json:
                try:
                    decision = RoutingDecisionOut(**json.loads(routing_json))
                except (json.JSONDecodeError, TypeError, ValueError):
                    decision = None
            if cards_json:
                try:
                    cards = [
                        SearchCandidateModel(**c) for c in json.loads(cards_json)
                    ]
                except (json.JSONDecodeError, TypeError, ValueError):
                    cards = None
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
                search_results=cards,
                deck=deck,
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
            "SELECT title FROM chat_sessions WHERE id = ?", (session_id,)
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
            "UPDATE chat_sessions SET deleted_at = NULL WHERE id = ?",
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
            "SELECT 1 FROM chat_sessions WHERE id = ? AND deleted_at IS NULL",
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
    return ForkSessionResponse(
        session_id=result.new_session_id,
        forked_message=result.forked_message,
        title=result.title,
    )
