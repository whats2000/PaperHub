"""Training trajectories of agent turns (H4, SRS §III-10.4).

An agent run (``runs.mode = 'agent'``) is rebuilt into the message list its
model worked through, from the run's trace alone; no prompt is stored:

* the system message is the ``core_agent`` template, filled with the
  recorded ``max_steps``; the ``<session_state>`` message is rendered from the
  first ``agent:step`` record (papers re-read by id; idea context, slide,
  memories and UI language as recorded);
* the conversation is rebuilt with ``load_conversation`` from the session's
  earlier messages and their runs' work records;
* each ``agent:step`` that called tools gives an assistant message with its
  calls (ids numbered here; provider ids are not training data), and each
  call's ``tool:<name>`` row gives its tool message: library chunks are
  re-read by id and formatted by the tool's own formatter, ephemeral results
  (SQL rows, search candidates, action outcomes) come from the record;
* the final assistant message is the stored answer.

Private documents fail closed. A turn is private when its session has a
private paper attached (its titles reach error text, confirm summaries and
the session state), when it read a chunk of a private paper or one that no
longer resolves, when a history turn or the answer cites one, or when it ran
a library-wide tool (``query_library``, a search with ``library:`` cards) or
carried idea context while the library holds a private paper. With
``include_private`` off such a turn is not rebuilt at all (``TrajectoryError``);
redacting piecemeal leaks titles through too many channels. Memories are
withheld (``MEMORIES_WITHHELD``) unless ``include_private``: a global memory
may come from any chat and its source is not tracked.

A turn whose recorded prompt fingerprint (``prompt_slot``, ``tools_sha256``,
``system_sha256``) no longer matches the current code raises
``FingerprintMismatch``; turns recorded before the fingerprint export as
rebuilt (ruling R18).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

from paperhub.agents.core.action_tools import (
    build_default_registry,
    render_action_content,
)
from paperhub.agents.core.agent import (
    AGENT_MAX_STEPS,
    BUDGET_INSTRUCTION,
    CORE_AGENT_SLOT,
    DUPLICATE_CALL_CONTENT,
    EMPTY_TURN_NUDGE,
    PENDING_RETRY_CONTENT,
    prompt_fingerprint,
)
from paperhub.agents.core.citations import cited_figures, cited_ids, cited_views
from paperhub.agents.core.context import (
    AGENT_KEEP_TURNS,
    EnabledPaper,
    SessionStateParts,
    enabled_paper_from_row,
    in_live_history,
    load_conversation,
    render_session_state,
)
from paperhub.agents.core.figures import (
    FigureImage,
    encode_figure,
    figure_content,
    figures_tables_text,
    images_message,
    load_figures,
    resolve_figure,
)
from paperhub.agents.core.gather import GATHER_EMPTY, format_gathered_chunk
from paperhub.agents.core.page_tools import PAGE_TOOLS, rerun_page_tool
from paperhub.agents.core.read_tools import (
    chunk_block,
    format_hit,
    format_read_chunks,
    format_section_page,
)
from paperhub.agents.core.tools import PENDING_CONTENT
from paperhub.agents.paper_qa_subagent import _list_figures_tables, _read_layout_object
from paperhub.feedback.citations import strip_unsourced
from paperhub.llm.prompts.registry import PromptRegistry

UNAVAILABLE = "[text unavailable: the paper was re-ingested]"
MEMORIES_WITHHELD = "[memories withheld: their source chat is not tracked]"
_FINGERPRINT_KEYS = ("prompt_slot", "tools_sha256", "system_sha256")

Message = dict[str, Any]


class TrajectoryError(ValueError):
    """The run cannot be rebuilt (not an agent run, no answer, a private turn
    without ``include_private``, a missing trace row)."""


class FingerprintMismatch(TrajectoryError):
    """The core agent prompt or tool schemas changed since the turn ran."""


class ConversationMismatch(TrajectoryError):
    """The stored chat no longer rebuilds the conversation the turn saw."""


@dataclass
class Trajectory:
    messages: list[Message]
    tools: list[dict[str, Any]]
    prompt_len: int  # messages up to and including this turn's user message
    private: bool = False
    chunk_ids: list[int] = field(default_factory=list)
    # The tool whose own answer ended the turn (query_library with
    # answer_user): the trajectory then ends at that tool call, because the
    # answer text was written by the tool's agent, not the core model.
    answer_from_tool: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"messages": self.messages, "tools": self.tools,
                               "private": self.private}
        if self.answer_from_tool:
            out["answer_from_tool"] = self.answer_from_tool
        return out


@dataclass
class _Chunk:
    paper: int
    section: str | None
    text: str
    page: int | None
    private: bool


class _Library:
    """Library reads for one trajectory; every read of a private (or vanished)
    paper or chunk marks the trajectory ``private``."""

    def __init__(self, conn: aiosqlite.Connection, include_private: bool) -> None:
        self.conn = conn
        self.include_private = include_private
        self.private = False
        self.chunk_ids: list[int] = []
        # Figure images of the step being rebuilt (view_figure), sent after
        # its tool messages like the live loop does.
        self.images: list[FigureImage] = []
        self._papers: dict[int, tuple[Any, ...] | None] = {}
        self._any_private: bool | None = None

    async def chunks(self, ids: list[int]) -> dict[int, _Chunk]:
        ids = [i for i in ids if isinstance(i, int)]
        self.chunk_ids += [i for i in ids if i not in self.chunk_ids]
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        async with self.conn.execute(
            "SELECT c.id, c.paper_content_id, c.section, c.text, c.page, "
            "pc.is_private FROM chunks c JOIN paper_content pc "
            f"ON pc.id = c.paper_content_id WHERE c.id IN ({marks})", tuple(ids),
        ) as cur:
            return {
                int(r[0]): _Chunk(int(r[1]), r[2], str(r[3]), r[4], bool(r[5]))
                for r in await cur.fetchall()
            }

    def text(self, chunk: _Chunk | None) -> str:
        """A chunk's text (``UNAVAILABLE`` when it no longer resolves)."""
        if chunk is None:
            self.private = True
            return UNAVAILABLE
        self.private = self.private or chunk.private
        return chunk.text

    async def paper(self, pid: Any) -> tuple[Any, ...] | None:
        """(id, title, source_path, sections_json, is_private) or None."""
        if not isinstance(pid, int):
            return None
        if pid not in self._papers:
            async with self.conn.execute(
                "SELECT id, title, source_path, sections_json, is_private "
                "FROM paper_content WHERE id = ?", (pid,),
            ) as cur:
                row = await cur.fetchone()
            self._papers[pid] = tuple(row) if row is not None else None
        return self._papers[pid]

    async def mark_paper(self, pid: Any) -> tuple[Any, ...] | None:
        """The paper's row; a private or vanished paper marks the trajectory."""
        row = await self.paper(pid)
        self.private = self.private or row is None or bool(row[4])
        return row

    async def mark_library(self) -> None:
        """A library-wide read: private when the library holds a private paper."""
        if self._any_private is None:
            async with self.conn.execute(
                "SELECT 1 FROM paper_content WHERE is_private = 1 LIMIT 1",
            ) as cur:
                self._any_private = await cur.fetchone() is not None
        self.private = self.private or self._any_private

    async def mark_session(self, session_id: int) -> None:
        """Any private paper attached to the session (enabled or not)."""
        async with self.conn.execute(
            "SELECT 1 FROM papers p JOIN paper_content pc "
            "ON pc.id = p.paper_content_id "
            "WHERE p.session_id = ? AND pc.is_private = 1 LIMIT 1", (session_id,),
        ) as cur:
            self.private = self.private or await cur.fetchone() is not None

    async def mark_cited(self, texts: list[str]) -> None:
        """Chunks cited in ``texts`` (history answers, the final answer)."""
        ids = list(dict.fromkeys(i for t in texts for i in cited_ids(t)))
        rows = await self.chunks(ids)
        for i in ids:
            self.text(rows.get(i))
        # A cited figure shows its paper's content too.
        for key in dict.fromkeys(k for t in texts for k in cited_figures(t)):
            await self.mark_paper(int(key.split("/", 1)[0]))
        for key in dict.fromkeys(k for t in texts for k in cited_views(t)):
            await self.mark_paper(int(key.split("/", 1)[0]))


def _loads(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _call_key(name: str, args: Any) -> str:
    return name + ":" + json.dumps(args, sort_keys=True, default=str)


def _pending_summary(name: str, args: dict[str, Any], result: dict[str, Any]) -> str:
    """The confirm summary the model was shown. Rows recorded before it was
    kept fall back to ``summarize_args``'s form without paper titles."""
    return str(result.get("args_summary") or (
        f"{name}({', '.join(f'{k}={v!r}' for k, v in args.items())})"))


async def _tool_content(
    lib: _Library, name: str, args: dict[str, Any], result: dict[str, Any],
    status: str,
) -> str:
    """The tool message the model read, from the call's ``tool:<name>`` row."""
    if result.get("pending_action_id"):
        return PENDING_CONTENT.format(summary=_pending_summary(name, args, result))
    if status == "error" or result.get("error"):
        return f"Error: {result.get('error')}"
    if name in ("read_section", "read_chunks"):
        ids = [i for i in result.get("chunk_ids") or [] if isinstance(i, int)]
        rows = await lib.chunks(ids)
        blocks = [chunk_block(i, lib.text(rows.get(i)),
                              rows[i].page if i in rows else None) for i in ids]
        if name == "read_chunks":
            return format_read_chunks(blocks, list(result.get("unknown_ids") or []))
        return format_section_page(
            blocks, int(result.get("page") or 1), int(result.get("pages") or 1))
    if name == "search_chunks":
        ids = [i for i in result.get("hit_ids") or [] if isinstance(i, int)]
        if not ids:
            return "No matching chunks."
        rows = await lib.chunks(ids)
        return "\n\n".join(
            format_hit(i, rows[i].paper, rows[i].section, lib.text(rows[i]),
                       rows[i].page)
            if i in rows else f'<hit chunk="{i}">\n{lib.text(None)}\n</hit>'
            for i in ids)
    if name == "gather":
        ids = [i for i in result.get("chunk_ids_selected") or [] if isinstance(i, int)]
        if not ids:
            return GATHER_EMPTY
        rows = await lib.chunks(ids)
        reasons = result.get("reasons") or {}
        parts = []
        for i in ids:
            c = rows.get(i)
            prow = await lib.paper(c.paper) if c is not None else None
            title = str(prow[1] or f"Paper {prow[0]}") if prow else str(
                c.paper if c else "?")
            parts.append(format_gathered_chunk(
                i, title, (c.section if c else None) or "", lib.text(c),
                str(reasons.get(str(i), ""))))
        return "\n\n".join(parts)
    if name in PAGE_TOOLS or (name == "view_figure" and "box" in result):
        # A page image: run the tool again and require the same image.
        row = await lib.mark_paper(result.get("paper", args.get("paper")))
        if row is None:
            return UNAVAILABLE
        out = await rerun_page_tool(lib.conn, name, args, int(row[0]))
        sent = getattr(out.images[0], "sha256", None) if out.images else None
        if sent is None or sent != result.get("sha256"):
            lib.private = True  # the page PDF changed or vanished
            return UNAVAILABLE
        lib.images.extend(out.images)
        return out.content
    if name in ("list_sections", "list_figures_tables", "read_layout_object",
                "view_figure"):
        row = await lib.mark_paper(result.get("paper", args.get("paper")))
        if row is None:
            return UNAVAILABLE  # the paper is gone: its text is unknown
        paper = int(row[0])
        if name == "list_sections":
            return str(result.get("sections") or "")
        if name == "list_figures_tables":
            text, labels, _ids = await figures_tables_text(
                paper_content_id=paper, conn=lib.conn)
            # Turns before view_figure saw the layout index alone.
            older, _ = await _list_figures_tables(paper_content_id=paper, conn=lib.conn)
            recorded = result.get("content_sha256")
            for candidate in (text, older):
                same = (hashlib.sha256(candidate.encode("utf-8")).hexdigest() == recorded
                        if recorded else labels == result.get("labels"))
                if same:
                    return candidate
            lib.private = True  # re-ingested since the turn: not what the model read
            return UNAVAILABLE
        if name == "view_figure":
            src, figures = await load_figures(lib.conn, paper)
            fig = resolve_figure(figures, str(result.get("figure", "")))
            image = (await asyncio.to_thread(encode_figure, paper, src, fig)
                     if src is not None and fig is not None else None)
            if image is None or image.sha256 != result.get("sha256"):
                lib.private = True  # the image changed or vanished
                return UNAVAILABLE
            lib.images.append(image)
            assert fig is not None
            return figure_content(paper, fig)
        body, pick = await _read_layout_object(
            paper_content_id=paper, label=str(result.get("label")),
            conn=lib.conn)
        recorded_ids = result.get("chunk_ids") or []
        if pick is None or not recorded_ids or pick.chunk_id != recorded_ids[0]:
            lib.private = True  # the figure moved or vanished: same as a lost chunk
            return UNAVAILABLE
        lib.chunk_ids += [pick.chunk_id]
        return body
    if name == "query_library" or (name == "search_papers" and any(
            str(c.get("paper_id", "")).startswith("library:")
            for c in result.get("candidates") or [] if isinstance(c, dict))):
        await lib.mark_library()
    try:
        return render_action_content(name, result)
    except (KeyError, TypeError, ValueError) as exc:
        raise TrajectoryError(f"cannot rebuild the {name} result: {exc!r}") from exc


def _check_fingerprint(run_id: int, state: dict[str, Any], tools: list[Any]) -> None:
    """Ruling R18: a turn without a fingerprint is rebuilt as is."""
    if not any(k in state for k in _FINGERPRINT_KEYS):
        return
    slot = PromptRegistry().get(CORE_AGENT_SLOT)
    now = prompt_fingerprint(tools, slot.system)
    changed = [k for k in _FINGERPRINT_KEYS if state.get(k) != now[k]]
    if changed:
        raise FingerprintMismatch(
            f"run {run_id}: {', '.join(changed)} changed since the turn ran")


async def _system_messages(
    lib: _Library, state: dict[str, Any],
) -> list[Message]:
    """The ``core_agent`` system message and the ``<session_state>`` message."""
    slot = PromptRegistry().get(CORE_AGENT_SLOT)
    max_steps = state.get("max_steps")
    system = slot.system.format(
        max_steps=max_steps if isinstance(max_steps, int) else AGENT_MAX_STEPS)
    papers: list[EnabledPaper] = []
    for pid in state.get("papers") or []:
        row = await lib.mark_paper(pid)
        if row is not None:  # a paper that no longer resolves is marked private
            papers.append(enabled_paper_from_row(*row[:4]))

    def text(key: str) -> str | None:
        value = state.get(key)
        return str(value) if value else None

    if text("idea_context"):
        await lib.mark_library()  # idea cards come from the whole library
    memories = text("memories")
    if memories and not lib.include_private:
        memories = MEMORIES_WITHHELD
    parts = SessionStateParts(
        papers=papers, idea_context=text("idea_context"),
        slide_context=text("slide_context"), memories=memories,
        ui_language=text("ui_language"),
    )
    return [
        {"role": "system", "content": system},
        {"role": "user",
         "content": slot.user_template.format(session_state=render_session_state(parts))},
    ]


async def _turn_texts(
    conn: aiosqlite.Connection, run_id: int, session_id: int,
) -> tuple[list[dict[str, Any]], str, str]:
    """The history before this run's question (with run ids), the question
    and the stored answer."""
    async with conn.execute(
        "SELECT m.role, m.content, m.run_id, r.status FROM messages m "
        "LEFT JOIN runs r ON r.id = m.run_id WHERE m.session_id = ? "
        "ORDER BY m.id", (session_id,),
    ) as cur:
        turns = [(str(r[0]), str(r[1]), r[2], r[3]) for r in await cur.fetchall()]
    q_idx = next((i for i, t in enumerate(turns)
                  if t[0] == "user" and t[2] == run_id), None)
    a_idx = next((i for i in range(len(turns) - 1, -1, -1)
                  if turns[i][0] == "assistant" and turns[i][2] == run_id), None)
    if q_idx is None or a_idx is None or q_idx > a_idx:
        raise TrajectoryError(f"run {run_id} has no question and answer")
    # The history the live turn was given (the client's filter).
    history = [{"role": r, "content": c, "run_id": rid}
               for r, c, rid, status in turns[:q_idx]
               if r in ("user", "assistant") and in_live_history(r, c, status)]
    # The answer is a training target: the validator's marks are not model text.
    return history, turns[q_idx][1], strip_unsourced(turns[a_idx][1])


def _check_conversation(run_id: int, state: dict[str, Any], conv: Any) -> None:
    """The rebuilt conversation must be the one the turn recorded seeing
    (message count, folded turns, citable ids); else the sample would train
    on a context that never existed."""
    recorded = {k: state.get(k) for k in
                ("conversation_messages", "folded_turns", "kept_ids")}
    if any(v is None for v in recorded.values()):
        return  # recorded before these fields existed
    rebuilt = {"conversation_messages": len(conv.messages),
               "folded_turns": conv.folded_turns,
               "kept_ids": sorted(conv.kept_ids)}
    if rebuilt != recorded:
        raise ConversationMismatch(
            f"run {run_id}: rebuilt conversation {rebuilt} differs from the "
            f"recorded {recorded}")


async def agent_trajectory(
    conn: aiosqlite.Connection, run_id: int, *, include_private: bool = False,
) -> Trajectory:
    """Rebuild agent run ``run_id`` as a training trajectory (see module doc).
    Raises ``TrajectoryError`` for a run that is not an answered agent run."""
    async with conn.execute(
        "SELECT session_id, mode FROM runs WHERE id = ?", (run_id,),
    ) as cur:
        run = await cur.fetchone()
    if run is None or run[1] != "agent":
        raise TrajectoryError(f"run {run_id} is not an agent run")
    session_id = int(run[0])
    history, question, answer = await _turn_texts(conn, run_id, session_id)

    async with conn.execute(
        "SELECT step_index, tool, status, args_redacted_json, result_summary_json "
        "FROM tool_calls WHERE run_id = ? AND branch = '' AND agent = 'agent' "
        "AND (tool = 'agent:step' OR tool LIKE 'tool:%') ORDER BY step_index",
        (run_id,),
    ) as cur:
        rows = [(int(r[0]), str(r[1]), str(r[2]), _loads(r[3]), _loads(r[4]))
                for r in await cur.fetchall()]
    steps = [(i, r) for i, (_s, tool, _st, _a, r) in enumerate(rows)
             if tool == "agent:step" and isinstance(r, dict)]
    if not steps:
        raise TrajectoryError(f"run {run_id} recorded no agent step")

    lib = _Library(conn, include_private)
    state = steps[0][1].get("session_state")
    state = state if isinstance(state, dict) else {}
    tools = build_default_registry().schemas()
    _check_fingerprint(run_id, state, tools)
    await lib.mark_session(session_id)
    await lib.mark_cited([str(h["content"]) for h in history] + [answer])
    messages = await _system_messages(lib, state)
    keep = state.get("keep_turns")
    conv = await load_conversation(
        conn, session_id, history,
        keep_turns=keep if isinstance(keep, int) else AGENT_KEEP_TURNS)
    _check_conversation(run_id, state, conv)
    messages += conv.messages
    messages.append({"role": "user", "content": question})
    prompt_len = len(messages)

    pending: dict[str, str] = {}  # call key -> confirm summary
    used: set[int] = set()
    nudge = False
    from_tool: str | None = None
    for n, (pos, record) in enumerate(steps):
        end = steps[n + 1][0] if n + 1 < len(steps) else len(rows)
        window = [k for k in range(pos + 1, end) if rows[k][1].startswith("tool:")]
        calls = record.get("tool_calls")
        if isinstance(calls, list) and calls:
            results = record.get("tool_results") or []
            ids = [f"call_{record.get('step', n + 1)}_{i}" for i in range(len(calls))]
            call_at = len(messages)
            messages.append({
                "role": "assistant", "content": record.get("preamble") or None,
                "tool_calls": [
                    {"id": cid, "type": "function", "function": {
                        "name": c.get("name"),
                        "arguments": json.dumps(c.get("args") or {}, ensure_ascii=False),
                    }}
                    for cid, c in zip(ids, calls, strict=True)
                ],
            })
            for i, (cid, c) in enumerate(zip(ids, calls, strict=True)):
                name, args = str(c.get("name")), c.get("args") or {}
                key = _call_key(name, args)
                entry = results[i] if i < len(results) and isinstance(results[i], dict) \
                    else {}
                if entry.get("skipped_pending_retry"):
                    # Not run: a repeat of a call waiting for confirmation (an
                    # earlier step's, or one earlier in this step), else a
                    # same-step duplicate.
                    content = (PENDING_RETRY_CONTENT.format(summary=pending[key])
                               if key in pending else DUPLICATE_CALL_CONTENT)
                else:
                    k = next((k for k in window if k not in used
                              and rows[k][1] == f"tool:{name}"
                              and _strip_call_id(rows[k][3]) == args), None)
                    if k is None:
                        raise TrajectoryError(
                            f"run {run_id}: no trace row for the {name} call")
                    used.add(k)
                    result = rows[k][4] if isinstance(rows[k][4], dict) else {}
                    content = await _tool_content(lib, name, args, result, rows[k][2])
                    if result.get("pending_action_id"):
                        pending[key] = _pending_summary(name, args, result)
                messages.append({"role": "tool", "tool_call_id": cid, "content": content})
            if lib.images:
                messages.append(images_message(lib.images))
                lib.images = []
            nudge = False
            if record.get("answer_from_tool"):
                # The tool's own answer ended the turn: the core model's last
                # output is this call, so the sample ends here (the results
                # were still rebuilt above, for the privacy marks).
                from_tool = str(record["answer_from_tool"])
                del messages[call_at + 1:]
                break
        elif record.get("empty_response"):
            nudge = True
        elif "answer" in record:
            if record.get("budget_reached"):
                messages.append({"role": "user", "content": BUDGET_INSTRUCTION})
            elif nudge:
                messages.append({"role": "user", "content": EMPTY_TURN_NUDGE})
            messages.append({"role": "assistant", "content": answer})
            break
    else:
        raise TrajectoryError(f"run {run_id} recorded no answering step")

    if lib.private and not include_private:
        raise TrajectoryError(
            f"run {run_id} touches private documents (include_private is off)")
    return Trajectory(
        messages=messages, tools=tools,
        prompt_len=prompt_len, private=lib.private, chunk_ids=lib.chunk_ids,
        answer_from_tool=from_tool,
    )


def _strip_call_id(args: Any) -> Any:
    if not isinstance(args, dict):
        return args
    return {k: v for k, v in args.items() if k != "call_id"}
