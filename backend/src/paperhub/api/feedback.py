"""Feedback REST surface (Plan H2, FR-18): explicit thumbs + reason guess."""
from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.feedback.export import EXPORT_FILES, build_export, utc_day, write_export
from paperhub.feedback.g1_inbox import (
    INBOX_NAME,
    NotANegative,
    append_inbox,
    build_inbox_lines,
    case_id,
    inbox_case_ids,
)
from paperhub.feedback.models import (
    ExportPreview,
    ExportRequest,
    ExportResult,
    FeedbackCase,
    FeedbackGuess,
    FeedbackPut,
    FeedbackSummary,
    G1InboxRequest,
    G1InboxResult,
    Preset,
    PresetCreate,
    PresetLabel,
    QueueItem,
    QueueSubmit,
    QueueSubmitResult,
    RunFeedback,
    Stage,
)
from paperhub.feedback.repo import (
    allowed_reasons,
    build_summary,
    create_preset,
    get_explicit,
    list_cases,
    list_explicit_for_session,
    list_presets,
    list_queue,
    rating_problem,
    run_exists,
    submit_queue,
    upsert_explicit,
)
from paperhub.feedback.signals import guess_reasons

router = APIRouter(prefix="/feedback", tags=["feedback"])

PRESET_LABEL_MAX = 60
_BAD_PATH_CHARS = ("/", "\\", ":", "\x00")


@router.put("/runs/{run_id}", response_model=RunFeedback | None)
async def put_run_feedback(run_id: int, body: FeedbackPut) -> RunFeedback | None:
    """Save the answer's explicit feedback; ``polarity: null`` clears it."""
    async with open_db(load_settings().db_path) as conn:
        if body.polarity is None:  # clearing needs no valid target
            if not await run_exists(conn, run_id):
                raise HTTPException(404, f"run {run_id} not found")
        elif (problem := await rating_problem(conn, run_id)) is not None:
            raise HTTPException(*problem)
        allowed = await allowed_reasons(conn)
        unknown = [r for r in body.reasons if r not in allowed]
        if unknown:
            raise HTTPException(422, f"unknown feedback reasons: {unknown}")
        await upsert_explicit(
            conn, run_id, body.polarity, body.reasons,
            body.other_text, body.correction,
        )
        return await get_explicit(conn, run_id)


@router.get("/runs/{run_id}/guess", response_model=FeedbackGuess)
async def get_run_guess(run_id: int) -> FeedbackGuess:
    async with open_db(load_settings().db_path) as conn:
        if not await run_exists(conn, run_id):
            raise HTTPException(404, f"run {run_id} not found")
        return FeedbackGuess(reasons=await guess_reasons(conn, run_id))


@router.get("/runs", response_model=list[RunFeedback])
async def list_session_feedback(session_id: int = Query(...)) -> list[RunFeedback]:
    async with open_db(load_settings().db_path) as conn:
        return await list_explicit_for_session(conn, session_id)


@router.get("/sessions/{session_id}/count")
async def get_session_feedback_count(session_id: int) -> dict[str, int]:
    """How many feedback rows (thumbs and reviewer rows) hang off the chat's
    runs: deleting the chat deletes them and any training data from them."""
    async with open_db(load_settings().db_path) as conn, conn.execute(
        "SELECT COUNT(*) FROM feedback f JOIN runs r ON r.id = f.run_id "
        "WHERE r.session_id = ?", (session_id,),
    ) as cur:
        return {"count": int((await cur.fetchone() or (0,))[0])}


@router.get("/queue", response_model=list[QueueItem])
async def get_queue() -> list[QueueItem]:
    async with open_db(load_settings().db_path) as conn:
        return await list_queue(conn)


@router.post("/queue/submit", response_model=QueueSubmitResult)
async def post_queue_submit(body: QueueSubmit) -> QueueSubmitResult:
    async with open_db(load_settings().db_path) as conn:
        ids = [a.feedback_id for a in body.answers]
        if len(ids) != len(set(ids)):
            raise HTTPException(422, "duplicate feedback_ids in answers")
        allowed = await allowed_reasons(conn)
        # Reasons are only meaningful (and stored) for confirmed items.
        unknown = [
            r for a in body.answers if a.confirmed for r in a.reasons
            if r not in allowed
        ]
        if unknown:
            raise HTTPException(422, f"unknown feedback reasons: {unknown}")
        result = await submit_queue(conn, body.answers)
        if result is None:
            raise HTTPException(404, "feedback item not in the queue")
        return QueueSubmitResult(confirmed=result[0], rejected=result[1])


@router.get("/summary", response_model=FeedbackSummary)
async def get_summary() -> FeedbackSummary:
    async with open_db(load_settings().db_path) as conn:
        return await build_summary(conn)


@router.get("/cases", response_model=list[FeedbackCase])
async def get_cases(stage: Annotated[Stage | None, Query()] = None) -> list[FeedbackCase]:
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        cases = await list_cases(conn, stage)
    sent = await asyncio.to_thread(inbox_case_ids, settings.exports_dir / INBOX_NAME)
    for c in cases:
        c.in_g1_inbox = case_id(c.feedback_id, c.run_id) in sent
    return cases


@router.get("/presets", response_model=list[Preset])
async def get_presets() -> list[Preset]:
    async with open_db(load_settings().db_path) as conn:
        return await list_presets(conn)


@router.post("/presets", response_model=PresetLabel)
async def post_preset(body: PresetCreate) -> PresetLabel:
    """Promote an "other" text to a preset reason (the popover then offers it)."""
    label = body.label.strip()
    if not label:
        raise HTTPException(422, "label must not be empty")
    if len(label) > PRESET_LABEL_MAX:
        raise HTTPException(422, f"label longer than {PRESET_LABEL_MAX} characters")
    async with open_db(load_settings().db_path) as conn:
        if not await create_preset(conn, label, body.stage):
            raise HTTPException(409, f"preset {label!r} already exists")
        return PresetLabel(label=label)


@router.post("/g1-inbox", response_model=G1InboxResult)
async def post_g1_inbox(body: G1InboxRequest) -> G1InboxResult:
    """Append failed answers to ``exports/g1-inbox.jsonl`` for human labelling.
    Idempotent per case id. All or nothing: an unknown feedback id is a 404,
    an id that is not a counted negative (explicit down, or implicit confirmed
    and final) is a 422, and neither writes anything."""
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        try:
            lines = await build_inbox_lines(conn, body.feedback_ids)
        except NotANegative as exc:
            raise HTTPException(
                422, f"not a counted negative feedback: {exc.ids}",
            ) from exc
    if lines is None:
        raise HTTPException(404, "feedback item not found")
    appended = await asyncio.to_thread(
        append_inbox, settings.exports_dir / INBOX_NAME, lines,
    )
    return G1InboxResult(appended=appended, already=len(lines) - appended)


def _export_query(
    cited_only: bool = False,
    include_implicit: bool = False,
    include_private: bool = True,
    since: str | None = None,
    until: str | None = None,
    model: str | None = None,
) -> ExportRequest:
    return ExportRequest(
        cited_only=cited_only, include_implicit=include_implicit,
        include_private=include_private, since=since, until=until, model=model,
    )


def _validate_days(opts: ExportRequest) -> None:
    """``since``/``until`` must be ISO dates/datetimes (inclusive UTC days)."""
    for name, value in (("since", opts.since), ("until", opts.until)):
        if value is None:
            continue
        try:
            utc_day(value)
        except ValueError:
            raise HTTPException(
                422, f"{name} must be an ISO date (YYYY-MM-DD, UTC day)",
            ) from None
    if opts.since and opts.until and utc_day(opts.since) > utc_day(opts.until):
        raise HTTPException(422, "since must not be after until")


@router.get("/export/preview", response_model=ExportPreview)
async def get_export_preview(
    opts: Annotated[ExportRequest, Depends(_export_query)],
) -> ExportPreview:
    """The counts ``POST /feedback/export`` would write, writing nothing.
    ``since``/``until`` are inclusive UTC days of the feedback's date."""
    _validate_days(opts)
    async with open_db(load_settings().db_path) as conn:
        plan = await build_export(conn, opts)
    return ExportPreview(
        sft=len(plan.sft), dpo=len(plan.dpo), includes_private=plan.includes_private,
    )


@router.post("/export", response_model=ExportResult)
async def post_export(body: ExportRequest) -> ExportResult:
    """Write the export under ``exports_dir/<UTC stamp>/``. ``since``/``until``
    are inclusive UTC days of the feedback's date (422 when not ISO)."""
    _validate_days(body)
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        plan = await build_export(conn, body)
    if not plan.sft and not plan.dpo:
        raise HTTPException(422, "nothing to export: no sample matches these options")
    out = await asyncio.to_thread(write_export, plan, settings.exports_dir)
    return ExportResult(
        dir=out.name, files=list(EXPORT_FILES), sft=len(plan.sft), dpo=len(plan.dpo),
    )


@router.get("/exports/{dir_name}/{file_name}")
async def get_export_file(dir_name: str, file_name: str) -> FileResponse:
    """Serve one export file. Only plain names inside ``exports_dir``: any
    ``..``, separator or path that resolves outside it is a 400."""
    root = load_settings().exports_dir.resolve()
    for part in (dir_name, file_name):
        if part in ("", ".", "..") or any(c in part for c in _BAD_PATH_CHARS):
            raise HTTPException(400, "invalid export path")
    target = (root / dir_name / file_name).resolve()
    if not target.is_relative_to(root) or target.parent.parent != root:
        raise HTTPException(400, "invalid export path")
    if not target.is_file():
        raise HTTPException(404, "export file not found")
    return FileResponse(target, filename=file_name)
