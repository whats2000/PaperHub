"""Background ``feedback:review`` run (Plan H2, FR-19).

A correction-like user message (keyword trigger) or a fork flags the previous
answer. ``schedule_review`` starts a detached task that asks a reviewer
prompt whether the answer was wrong and stores an implicit ``down`` feedback
row. A keyword review uses ``REVIEW_SLOT``; a fork, which has no follow-up
text, uses ``FORK_REVIEW_SLOT`` (``REVIEW_SLOTS``). A keyword review is ``needs_confirmation``
when the verdict is ``uncertain`` (the user is asked on the Feedback page) and
``final`` otherwise; rejected verdicts are kept, so they are never re-asked,
and the summary counts only confirmed implicit rows. A fork review is ALWAYS
``needs_confirmation`` (Ruling K2): a fork says only that the user went back,
so its verdict is a guess shown in the queue, never final.
One implicit row per ``(run, trigger)`` (partial unique index); a duplicate
review records ``skipped: duplicate``. A target with a blank answer or a run
that did not finish ``ok`` is skipped without an LLM call.

The review must never delay or break the chat turn: it runs in its own task on
its own DB connection, every failure is recorded as an ``error`` step + an
``error`` run, and nothing is re-raised. The reviewer call is bounded
(``_ask_reviewer``): an output cap, one retry on an invalid verdict and an
overall timeout; a failure records its kind, the attempts and the head of the
bad output on the error step. The review run has no messages, so it
never shows in the chat; its ``routing_decision_json`` is ``REVIEW_RUN_JSON``.

The prompt variables are built exactly like the eval stage
(``benchmark/agent/stages.py`` ``feedback_review``): ``question``, ``answer``,
``user_followup`` (``""`` for a fork, which has no follow-up message),
``trace_summary`` in the corpus format, and ``reasons`` from
``format_reasons_block()``. Every cited chunk is shown in full (whitespace
collapsed) until ``CITED_TEXT_BUDGET`` characters of passage text are spent:
the passage that crosses it is cut with ``...`` and later ones read
``[text not shown: passage budget spent]``. ``ReviewInputs.cited_passages``
(recorded on the step) says what was shown or cut. One rare trace line goes
beyond the corpus: ``failed step: agent/tool`` (an error step that was not a
user cancellation).

An agent run (``runs.mode = 'agent'``, H4) is summarised from its own rows
(``_agent_trace``): ``route: agent`` with the turn's enabled papers as refs,
``read:`` lines from its read/gather tools (chunks read by id are named by
their section), ``search:`` lines from ``search_papers``, ``sql:`` lines from
``query_library`` records, and one ``citation removed: chunk N (never read)``
line per marker ``validate_citations`` dropped.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import aiosqlite
from pydantic import ValidationError

from paperhub.config import Settings
from paperhub.db.connection import open_db, write_transaction
from paperhub.feedback.citations import cited_chunk_ids
from paperhub.feedback.forks import copied_in_session
from paperhub.feedback.models import ReviewVerdict, format_reasons_block
from paperhub.feedback.signals import guess_reasons
from paperhub.feedback.trigger import keywords_from_env, matches_trigger
from paperhub.llm.adapter import LlmAdapter
from paperhub.tracing.tracer import Tracer

_LOG = logging.getLogger(__name__)

Trigger = Literal["keyword", "fork"]
REVIEW_SLOT = "feedback_review/v4"
FORK_REVIEW_SLOT = "feedback_review_fork/v4"
REVIEW_SLOTS: dict[Trigger, str] = {"keyword": REVIEW_SLOT, "fork": FORK_REVIEW_SLOT}
"""The reviewer prompt per trigger. The keyword prompt sends every empty
follow-up to ``rejected`` (benchmark/agent/prompts/feedback_review_fork/README.md),
so a fork has its own prompt; it takes the same variables and ignores
``user_followup``."""
_UI_LANGUAGES = {
    "en": "English", "zh-TW": "Traditional Chinese", "zh-CN": "Simplified Chinese",
    "ja": "Japanese",
}
_DEFAULT_DOUBT_LANGUAGE = "the main language of the user's question"


def doubt_language(ui_language: str | None) -> str:
    """The language the reviewer writes its ``doubt`` in: the client's UI
    language (``en``, ``zh-TW``, ``zh-CN``, ``ja``) by name, else the question's."""
    return _UI_LANGUAGES.get((ui_language or "").strip(), _DEFAULT_DOUBT_LANGUAGE)


REVIEW_RUN_JSON = '{"intent": "feedback_review"}'
"""``runs.routing_decision_json`` of a review run. A review run is never
routed; the marker lets a ``runs`` count (``library_stats``) tell it from a
chat turn, as the ideation runs' ``{"ideation_mode": ...}`` marker does."""

_live_tasks: set[asyncio.Task[None]] = set()
"""Strong refs to in-flight review tasks so they are not GC'd mid-run."""

REVIEW_MAX_TOKENS = 4096
"""Output cap for the reviewer call. The cap stops a degenerate repetition loop
(run 694: 65 535 chars of one repeated combining accent in 113 s) early. Gemini
counts thinking tokens against it: the flagship reviewer thinks for 140-960
tokens (959 of 1024 on ``fork-ja-thin-evidence``), and 1024 truncated 2 of 135
verdicts, so the cap leaves room for that plus the JSON."""
REVIEW_FALLBACK_ENV = "PAPERHUB_FEEDBACK_REVIEW_FALLBACK_MODEL"


def review_fallback_model() -> str:
    """The model a failing reviewer call downgrades to; ``""`` (the default)
    means none. Not the usual small tier: it garbles zh/ja doubts (~12 % in the
    eval) and scores fork cases worse, and a failed background review is only
    recorded, while a garbled verdict would be kept."""
    return os.environ.get(REVIEW_FALLBACK_ENV, "").strip()


REVIEW_ATTEMPTS = 2
"""One retry when the verdict is not valid JSON / not a valid ``ReviewVerdict``."""
REVIEW_TIMEOUT_S = 90.0
"""Overall bound on the review's LLM work (every attempt together)."""
_BAD_OUTPUT_HEAD = 300

CITED_TEXT_BUDGET = 16_000
"""Characters of cited passage text (all citations together) in the summary.
Runs 825/827 showed a 200-char head per chunk hides the fact that supports the
answer; chunks are at most 5 000 chars, so this fits three whole chunks
(~4k tokens) before cutting."""
SQL_TEXT_MAX = 600
SQL_ROWS_MAX = 10
SQL_RESULT_MAX = 800
SQL_QUERIES_MAX = 4
"""Bounds on the ``library_stats`` evidence lines: the query, the rows shown and
the rendered result, per query; at most ``SQL_QUERIES_MAX`` queries."""
_SECTION_NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)(?=\s|$|\.)")


def section_label(section: str) -> str:
    """``5.1 Training Data`` -> ``5.1`` (the corpus's ``§5.1`` form); a heading
    without a leading number is kept as-is."""
    m = _SECTION_NUM_RE.match(section.strip())
    return m.group(1) if m else section.strip()


# ---------------------------------------------------------------- inputs


@dataclass
class ReviewInputs:
    question: str
    answer: str
    answer_message_id: int
    trace_summary: str
    # ``tool_calls.step_index`` of every target-run step read (branch '').
    step_ids_read: list[int] = field(default_factory=list)
    cited_chunk_ids: list[int] = field(default_factory=list)
    # One ``{chunk_id, chars, shown_chars, cut}`` per cited chunk found.
    cited_passages: list[dict[str, Any]] = field(default_factory=list)


def _loads(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _sql_lines(args: Any, result: Any, total: int | None = None) -> list[str]:
    """``sql: <query>`` + ``result: columns=... rows=...`` for one recorded
    ``sql.query`` step, bounded; a failed query shows the error. ``total`` is
    the full row count when ``rows`` holds only the recorded head."""
    sql = args.get("sql") if isinstance(args, dict) else None
    out = [f"sql: {' '.join(str(sql).split())[:SQL_TEXT_MAX]}"] if sql else []
    if not isinstance(result, dict):
        return out
    payload = result.get("payload")
    if result.get("ok") is False or not isinstance(payload, dict):
        out.append(f"result: error {str(result.get('error'))[:200]}")
        return out
    rows = payload.get("rows")
    rows = rows if isinstance(rows, list) else []
    n_rows = max(total or 0, len(rows))
    body = f"columns={payload.get('columns')} rows={json.dumps(rows[:SQL_ROWS_MAX], ensure_ascii=False)}"
    if len(body) > SQL_RESULT_MAX:
        body = body[:SQL_RESULT_MAX] + "..."
    if n_rows > SQL_ROWS_MAX or body.endswith("..."):
        body += f" ({n_rows} rows in all)"
    out.append("result: " + body)
    return out


@dataclass
class _AgentTrace:
    """What an agent run's ``agent:*`` / ``tool:*`` rows show the reviewer."""

    papers: list[int] | None  # the turn's enabled papers (step 1 record)
    reads: list[tuple[int | None, list[str]]]
    searches: list[str]
    sql: list[str]
    citations_removed: list[int]


async def _agent_trace(
    conn: aiosqlite.Connection, steps: list[Any], record: Any,
) -> _AgentTrace:
    """Reads (sections, figures and chunks by paper), paper searches, SQL
    queries and dropped citations of an agent run, from its own rows."""
    papers: list[int] | None = None
    by_paper: dict[int | None, list[str]] = {}
    searches: list[str] = []
    sql: list[str] = []
    removed: list[int] = [
        i for i in (record.get("citations_removed") or [])
        if isinstance(i, int)
    ] if isinstance(record, dict) else []
    sql_seen = 0
    chunk_reads: list[int] = []

    def read(paper: Any, name: Any) -> None:
        key = paper if isinstance(paper, int) else None
        names = by_paper.setdefault(key, [])
        if name and str(name) not in names:
            names.append(str(name))

    for _sid, agent, tool, status, _error, args_raw, result_raw in steps:
        if agent != "agent":
            continue
        args, result = _loads(args_raw), _loads(result_raw)
        args = args if isinstance(args, dict) else {}
        result = result if isinstance(result, dict) else {}
        if tool == "agent:step":
            state = result.get("session_state")
            if isinstance(state, dict) and isinstance(state.get("papers"), list):
                papers = [p for p in state["papers"] if isinstance(p, int)]
            for i in result.get("citations_removed") or []:
                if isinstance(i, int) and i not in removed:
                    removed.append(i)
            continue
        if status != "ok":
            continue
        if tool == "tool:read_section":
            read(result.get("paper"), result.get("section"))
        elif tool == "tool:read_layout_object":
            read(result.get("paper"), result.get("label"))
        elif tool == "tool:view_figure":
            read(result.get("paper"), result.get("figure"))
        elif tool == "tool:gather":
            for entry in result.get("sections_read") or []:
                if isinstance(entry, dict):
                    read(entry.get("paper"), entry.get("section"))
            chunk_reads += [i for i in result.get("chunk_ids_selected") or []
                            if isinstance(i, int)]
        elif tool == "tool:read_chunks":
            chunk_reads += [i for i in result.get("chunk_ids") or []
                            if isinstance(i, int)]
        elif tool == "tool:search_papers":
            cards = result.get("candidates")
            n = len(cards) if isinstance(cards, list) else 0
            searches.append(f'search: "{result.get("query")}" -> {n} results')
        elif tool == "tool:query_library":
            for q in result.get("queries") or []:
                if not isinstance(q, dict):
                    continue
                sql_seen += 1
                if sql_seen > SQL_QUERIES_MAX:
                    break
                failed = "error" in q
                sql.extend(_sql_lines(
                    {"sql": q.get("sql")},
                    {"ok": not failed, "error": q.get("error"),
                     "payload": None if failed else {
                         "columns": q.get("columns"), "rows": q.get("rows")}},
                    total=q.get("total_rows") if isinstance(q.get("total_rows"), int)
                    else None,
                ))
    if chunk_reads:  # chunks read by id: name their paper and section
        marks = ",".join("?" * len(chunk_reads))
        async with conn.execute(
            f"SELECT id, paper_content_id, section FROM chunks WHERE id IN ({marks})",
            tuple(chunk_reads),
        ) as cur:
            where = {int(r[0]): (int(r[1]), r[2]) for r in await cur.fetchall()}
        for cid in chunk_reads:
            if cid in where:
                read(where[cid][0], where[cid][1])
    return _AgentTrace(
        papers=papers, reads=list(by_paper.items()), searches=searches, sql=sql,
        citations_removed=removed,
    )


async def _paper_titles(
    conn: aiosqlite.Connection, ids: list[int],
) -> list[tuple[int, str]]:
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    async with conn.execute(
        f"SELECT id, title FROM paper_content WHERE id IN ({marks})", tuple(ids),
    ) as cur:
        titles = {int(r[0]): r[1] for r in await cur.fetchall()}
    return [(i, str(titles.get(i) or f"Paper {i}")) for i in ids]


async def load_review_inputs(
    conn: aiosqlite.Connection, run_id: int,
) -> ReviewInputs | None:
    """Read the answer under review and summarise its trace. ``None`` when the
    run has no assistant answer (nothing to review)."""
    async with conn.execute(
        "SELECT id, content FROM messages WHERE run_id = ? AND role = 'assistant' "
        "ORDER BY id DESC LIMIT 1", (run_id,),
    ) as cur:
        ans = await cur.fetchone()
    if ans is None:
        return None
    async with conn.execute(
        "SELECT content FROM messages WHERE run_id = ? AND role = 'user' "
        "ORDER BY id LIMIT 1", (run_id,),
    ) as cur:
        q = await cur.fetchone()
    async with conn.execute(
        "SELECT session_id, routing_decision_json, search_results_json, mode, "
        "agent_record_json FROM runs WHERE id = ?", (run_id,),
    ) as cur:
        run_row = await cur.fetchone()
    async with conn.execute(
        "SELECT step_index, agent, tool, status, error, args_redacted_json, "
        "result_summary_json FROM tool_calls WHERE run_id = ? "
        "ORDER BY branch, step_index", (run_id,),
    ) as cur:
        steps = list(await cur.fetchall())

    question = str(q[0]) if q is not None else ""
    answer = str(ans[1])
    session_id = int(run_row[0]) if run_row is not None else None
    agent_mode = run_row is not None and run_row[3] == "agent"

    router: dict[str, Any] | None = None
    refs: list[tuple[int, str]] | None = None
    reads: list[tuple[int | None, list[str]]] = []
    failed: list[str] = []
    evidence: list[str] = []  # non-cited intents: what the run recorded
    sql_seen = 0
    for _sid, agent, tool, status, error, args_raw, result_raw in steps:
        result = _loads(result_raw)
        if agent == "router" and tool == "classify" and isinstance(result, dict):
            router = result
        elif tool == "paper_qa:resolve" and isinstance(result, dict):
            enabled = result.get("enabled")
            if isinstance(enabled, list):
                refs = [
                    (int(e["id"]), str(e.get("title") or f"Paper {e['id']}"))
                    for e in enabled if isinstance(e, dict) and "id" in e
                ]
        elif tool == "paper_qa:subagent":
            args = _loads(args_raw)
            pcid = args.get("paper_content_id") if isinstance(args, dict) else None
            names: list[str] = []
            log = result.get("tool_call_log") if isinstance(result, dict) else None
            for entry in log if isinstance(log, list) else []:
                if not isinstance(entry, dict) or not entry.get("chunk_ids_returned"):
                    continue
                eargs = entry.get("args")
                if not isinstance(eargs, dict):
                    eargs = {}
                name = eargs.get("name") or eargs.get("label")
                if name and str(name) not in names:
                    names.append(str(name))
            reads.append((int(pcid) if isinstance(pcid, int) else None, names))
        if tool == "sql.query" and status == "ok" and not agent_mode:
            # (an agent run's queries come from its query_library records)
            sql_seen += 1
            if sql_seen <= SQL_QUERIES_MAX:
                evidence.extend(_sql_lines(_loads(args_raw), result))
        elif tool == "report:outline" and isinstance(result, dict):
            title = result.get("talk_title")
            if title:
                evidence.append(f'deck: "{title}"')
        elif tool == "report:compile" and isinstance(result, dict):
            evidence.append(
                "compiled: ok, " + f"{result.get('page_count')} pages"
                if result.get("ok") else "compiled: failed",
            )
        elif tool == "paper_search:finalize" and isinstance(result, dict):
            titles = [
                str(c.get("title")) for c in result.get("emitted_candidates") or []
                if isinstance(c, dict) and c.get("title")
            ]
            if titles:
                evidence.append("found: " + "; ".join(f'"{t}"' for t in titles[:8]))
            missing = result.get("not_found")
            if isinstance(missing, list) and missing:
                evidence.append("not found: " + "; ".join(str(m) for m in missing[:8]))
        if status == "error" and error != "cancelled":
            failed.append(f"{agent}/{tool}")
    agent = (await _agent_trace(conn, steps, _loads(run_row[4]))
             if agent_mode and run_row is not None else None)
    if agent is not None:
        router = {"intent": "agent"}
        if agent.papers is not None:
            refs = await _paper_titles(conn, agent.papers)
        reads = agent.reads
        evidence = agent.sql + evidence
    if router is None and run_row is not None:
        rd = _loads(run_row[1])
        router = rd if isinstance(rd, dict) else None
    if refs is None and session_id is not None:
        async with conn.execute(
            "SELECT pc.id, pc.title FROM papers p "
            "JOIN paper_content pc ON pc.id = p.paper_content_id "
            "WHERE p.session_id = ? AND p.enabled = 1 ORDER BY p.added_at, p.id",
            (session_id,),
        ) as cur:
            refs = [
                (int(r[0]), str(r[1]) if r[1] else f"Paper {r[0]}")
                for r in await cur.fetchall()
            ]
    label = {pcid: f"P{i + 1}" for i, (pcid, _t) in enumerate(refs or [])}

    intent = str((router or {}).get("intent") or "unknown")
    lines: list[str] = []
    route = f"route: {intent}"
    if refs:
        route += " (refs: " + "; ".join(
            f'[{label[pcid]}] "{title}"' for pcid, title in refs
        ) + ")"
    lang = (router or {}).get("response_language")
    if lang:
        route += f" response_language={lang}"
    lines.append(route)

    for pcid, names in reads:
        who = label.get(pcid, f"paper {pcid}") if pcid is not None else "paper ?"
        lines.append(
            f"read: {who} " + ("; ".join(f"§{n}" for n in names) if names else "none"),
        )
    if intent in ("paper_search", "paper_suggest"):
        cards = _loads(run_row[2]) if run_row is not None else None
        n = len(cards) if isinstance(cards, list) else 0
        query = (router or {}).get("resolved_query") or question
        lines.append(f'search: "{query}" -> {n} results')

    if agent is not None:
        lines.extend(agent.searches)
    lines.extend(evidence)

    cited = cited_chunk_ids(answer)
    chunk_rows: dict[int, tuple[int, str | None, str]] = {}
    if cited:
        marks = ",".join("?" * len(cited))
        async with conn.execute(
            f"SELECT id, paper_content_id, section, text FROM chunks "
            f"WHERE id IN ({marks})", cited,
        ) as cur:
            for cid, pcid, section, text in await cur.fetchall():
                chunk_rows[int(cid)] = (int(pcid), section, str(text))
    if not cited:
        lines.append("cited: none")
    passages: list[dict[str, Any]] = []
    budget = CITED_TEXT_BUDGET
    for cid in cited:
        row = chunk_rows.get(cid)
        if row is None:
            lines.append(f"cited: chunk {cid} (not found)")
            continue
        pcid, section, text = row
        where = label.get(pcid, f"paper {pcid}")
        if section:
            where += f" §{section_label(section)}"
        body = " ".join(text.split())
        shown = body[:budget]
        budget -= len(shown)
        passages.append({"chunk_id": cid, "chars": len(body),
                         "shown_chars": len(shown), "cut": len(shown) < len(body)})
        if not shown and body:
            lines.append(f"cited: chunk {cid} ({where}) "
                         "[text not shown: passage budget spent]")
            continue
        if len(shown) < len(body):
            shown = shown.rstrip() + "..."
        lines.append(f'cited: chunk {cid} ({where}) "{shown}"')

    if agent is not None:
        lines.extend(f"citation removed: chunk {i} (never read)"
                     for i in agent.citations_removed)
    if intent == "paper_qa" and refs:
        read_ids = {pcid for pcid, _n in reads}
        missing = [label[pcid] for pcid, _t in refs if pcid not in read_ids]
        if missing:
            lines.append("not read: " + ", ".join(missing))
    for f in failed:
        lines.append(f"failed step: {f}")

    return ReviewInputs(
        question=question, answer=answer, answer_message_id=int(ans[0]),
        trace_summary="\n".join(lines),
        step_ids_read=[int(s[0]) for s in steps],
        cited_chunk_ids=cited,
        cited_passages=passages,
    )


# ---------------------------------------------------------------- run


class ReviewCallFailed(Exception):
    """The reviewer produced no usable verdict: ``kind`` is ``invalid_json``,
    ``invalid_verdict`` or ``timeout``; ``output_head`` is the start of the
    last bad output (the model's text, never the prompt)."""

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
            else "invalid_verdict")
    raw = errors[0].get("input") if errors else None
    if not isinstance(raw, str):
        raw = json.dumps(raw, ensure_ascii=False, default=str)
    return kind, raw[:_BAD_OUTPUT_HEAD]


async def _ask_reviewer(
    llm: LlmAdapter, model: str, variables: dict[str, Any], *, slot: str = REVIEW_SLOT,
) -> tuple[ReviewVerdict, int]:
    """The bounded reviewer call: capped output, one retry on an invalid
    verdict, all attempts under ``REVIEW_TIMEOUT_S``. Returns the verdict and
    the attempts used; raises ``ReviewCallFailed`` (provider errors propagate
    unchanged)."""
    attempts = 0
    last: tuple[str, str] | None = None
    try:
        async with asyncio.timeout(REVIEW_TIMEOUT_S):
            while attempts < REVIEW_ATTEMPTS:
                attempts += 1
                try:
                    verdict = await llm.structured(
                        slot=slot, variables=variables,
                        response_model=ReviewVerdict, model=model,
                        max_tokens=REVIEW_MAX_TOKENS,
                        fallback_model=review_fallback_model(),
                    )
                    return verdict, attempts
                except ValidationError as exc:
                    last = _validation_failure(exc)
                    _LOG.warning("feedback reviewer attempt %d: %s", attempts, last[0])
    except TimeoutError:
        raise ReviewCallFailed("timeout", attempts, last[1] if last else None) from None
    assert last is not None
    raise ReviewCallFailed(last[0], attempts, last[1])


async def _skip_reason(
    conn: aiosqlite.Connection, run_id: int, inputs: ReviewInputs | None,
) -> str | None:
    """Why ``run_id`` is not worth reviewing, or ``None``."""
    if inputs is None:
        return "no assistant answer"
    if not inputs.answer.strip():
        return "blank answer"
    async with conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)) as cur:
        row = await cur.fetchone()
    if row is None or row[0] != "ok":
        return f"target run status {row[0] if row else 'missing'}"
    return None


async def _finish_run(conn: aiosqlite.Connection, run_id: int, status: str) -> None:
    async with write_transaction(conn):
        await conn.execute(
            "UPDATE runs SET status = ?, finished_at = datetime('now') WHERE id = ?",
            (status, run_id),
        )


async def run_review(
    *,
    db_path: Path,
    session_id: int,
    target_run_id: int,
    followup: str | None,
    trigger: Trigger,
    llm: LlmAdapter,
    model: str,
    ui_language: str | None = None,
) -> None:
    """The review task body. Never raises (except a cancellation)."""
    # Cheap pre-check; the partial unique index is the real guard (below).
    review_run_id: int | None = None
    try:
        async with open_db(db_path) as conn:
            async with conn.execute(
                "SELECT 1 FROM feedback WHERE run_id = ? AND kind = 'implicit' "
                "AND trigger = ?", (target_run_id, trigger),
            ) as cur:
                if await cur.fetchone() is not None:
                    return  # already reviewed for this trigger
            async with write_transaction(conn):
                ins = await conn.execute(
                    "INSERT INTO runs (session_id, status, routing_decision_json) "
                    "VALUES (?, 'running', ?)",
                    (session_id, REVIEW_RUN_JSON),
                )
                assert ins.lastrowid is not None
                review_run_id = int(ins.lastrowid)
            tracer = Tracer(conn, run_id=review_run_id, branch="")
            status = "ok"
            try:
                async with tracer.step(
                    agent="feedback", tool="feedback:review", model=model,
                ) as step:
                    inputs = await load_review_inputs(conn, target_run_id)
                    # The reviewer's input state (not the rendered prompt): the
                    # G1 ``feedback_review`` stage rebuilds its variables from
                    # these keys (``reasons`` is the static block).
                    step.record_args({
                        "target_run_id": target_run_id,
                        "trigger": trigger,
                        "answer_message_id": (
                            inputs.answer_message_id if inputs is not None else None
                        ),
                        "question": inputs.question if inputs is not None else "",
                        "answer": inputs.answer if inputs is not None else "",
                        "user_followup": followup or "",
                        "trace_summary": (
                            inputs.trace_summary if inputs is not None else ""
                        ),
                        "doubt_language": doubt_language(ui_language),
                    })
                    skip = await _skip_reason(conn, target_run_id, inputs)
                    if skip is not None:
                        step.record_result({"skipped": skip})
                    else:
                        assert inputs is not None
                        try:
                            verdict, attempts = await _ask_reviewer(llm, model, {
                                "question": inputs.question,
                                "answer": inputs.answer,
                                "user_followup": followup or "",
                                "trace_summary": inputs.trace_summary,
                                "reasons": format_reasons_block(),
                                "doubt_language": doubt_language(ui_language),
                            }, slot=REVIEW_SLOTS[trigger])
                        except ReviewCallFailed as exc:
                            step.record_result(exc.summary())
                            raise
                        guessed = await guess_reasons(conn, target_run_id)
                        result: dict[str, Any] = {
                            "verdict": verdict.verdict,
                            "reasons": list(verdict.reasons),
                            "doubt": verdict.doubt,
                            "guessed_reasons": guessed,
                            "step_ids_read": inputs.step_ids_read,
                            "cited_chunk_ids": inputs.cited_chunk_ids,
                            "cited_passages": inputs.cited_passages,
                            "attempts": attempts,
                            "slot": REVIEW_SLOTS[trigger],
                        }
                        needs_user = trigger == "fork" or verdict.verdict == "uncertain"
                        stored = "needs_confirmation" if needs_user else "final"
                        stored_verdict = verdict.verdict
                        async with write_transaction(conn):
                            async with conn.execute(
                                "SELECT 1 FROM feedback WHERE run_id = ? "
                                "AND kind = 'explicit'", (target_run_id,),
                            ) as rated:
                                if await rated.fetchone() is not None:
                                    # The user already rated this answer: that
                                    # is their answer, nothing left to ask.
                                    stored, stored_verdict = "final", "rejected"
                            ins = await conn.execute(
                                "INSERT INTO feedback (run_id, message_id, kind, "
                                "polarity, reasons_json, trigger, verdict, status, "
                                "reviewer_rationale) "
                                "VALUES (?, ?, 'implicit', 'down', ?, ?, ?, ?, ?) "
                                "ON CONFLICT(run_id, trigger) WHERE kind = 'implicit' "
                                "DO NOTHING",
                                (
                                    target_run_id, inputs.answer_message_id,
                                    json.dumps(list(verdict.reasons), ensure_ascii=False),
                                    trigger, stored_verdict, stored, verdict.doubt,
                                ),
                            )
                        if ins.rowcount == 0:  # a concurrent review won the race
                            result.update(skipped="duplicate", feedback_id=None)
                        else:
                            result.update(status=stored, feedback_id=ins.lastrowid)
                        step.record_result(result)
            except asyncio.CancelledError:
                await _finish_run(conn, review_run_id, "cancelled")
                raise
            except Exception:  # noqa: BLE001 — the tracer recorded the error step
                _LOG.warning(
                    "feedback review of run %s failed", target_run_id, exc_info=True,
                )
                status = "error"
            await _finish_run(conn, review_run_id, status)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a review must never surface
        _LOG.warning(
            "feedback review of run %s could not run (review run %s)",
            target_run_id, review_run_id, exc_info=True,
        )
        if review_run_id is not None:
            try:
                async with open_db(db_path) as conn:
                    await _finish_run(conn, review_run_id, "error")
            except Exception:  # noqa: BLE001
                _LOG.warning("could not mark review run %s error", review_run_id)


def schedule_review(
    *,
    db_path: Path,
    session_id: int,
    target_run_id: int,
    followup: str | None,
    trigger: Trigger,
    llm: LlmAdapter,
    model: str,
    ui_language: str | None = None,
) -> None:
    """Start the background review; returns at once."""
    task = asyncio.create_task(run_review(
        db_path=db_path, session_id=session_id, target_run_id=target_run_id,
        followup=followup, trigger=trigger, llm=llm, model=model,
        ui_language=ui_language,
    ))
    _live_tasks.add(task)
    task.add_done_callback(_live_tasks.discard)


# ---------------------------------------------------------------- hooks


async def maybe_review_keyword(
    conn: aiosqlite.Connection,
    *,
    settings: Settings,
    session_id: int,
    run_id: int,
    user_message: str,
    llm: LlmAdapter,
    ui_language: str | None = None,
) -> None:
    """Chat hook: a correction keyword in the user's new message flags the
    latest previous answer in the session, unless that answer is a fork's copy
    of an earlier one. Never raises."""
    try:
        if not settings.feedback_review_enabled:
            return
        if not matches_trigger(user_message, keywords_from_env()):
            return
        async with conn.execute(
            "SELECT run_id FROM messages WHERE session_id = ? AND role = 'assistant' "
            "AND run_id IS NOT NULL AND run_id < ? ORDER BY id DESC LIMIT 1",
            (session_id, run_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return
        if int(row[0]) in await copied_in_session(conn, session_id):
            return  # a fork's copy of an earlier answer, not this chat's answer
        schedule_review(
            db_path=settings.db_path, session_id=session_id,
            target_run_id=int(row[0]), followup=user_message, trigger="keyword",
            llm=llm, model=settings.feedback_review_model, ui_language=ui_language,
        )
    except Exception:  # noqa: BLE001 — the chat turn must not notice
        _LOG.warning("feedback keyword hook failed", exc_info=True)


def maybe_review_fork(
    *, settings: Settings, session_id: int, target_run_id: int, llm: LlmAdapter,
    ui_language: str | None = None,
) -> None:
    """Fork hook: forking at a turn flags that turn's answer. Never raises."""
    try:
        if not settings.feedback_review_enabled:
            return
        schedule_review(
            db_path=settings.db_path, session_id=session_id,
            target_run_id=target_run_id, followup=None, trigger="fork",
            llm=llm, model=settings.feedback_review_model, ui_language=ui_language,
        )
    except Exception:  # noqa: BLE001 — the fork must not notice
        _LOG.warning("feedback fork hook failed", exc_info=True)
