"""Precise context for the core agent (SRS §III-10.4).

Blocks, in prompt order: instructions, tool definitions (both fixed, so the
prefix is cacheable), session state (rebuilt each turn), the conversation
(earlier turns as text plus a one-line work record each; their tool results
are dropped), and this turn's messages. Very old turns fold into one
deterministic ``<earlier_summary>`` (no LLM call).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any
from xml.sax.saxutils import escape

import aiosqlite
import litellm

from paperhub.agents.core.citations import cited_figures, cited_ids, cited_views
from paperhub.agents.idea_context import build_idea_context
from paperhub.agents.memory_recall import active_memories

AGENT_KEEP_TURNS = 12
SUMMARY_QUESTION_CHARS = 160
_COUNT_MODEL = "gpt-4o"  # tokenizer used for budget accounting only


@dataclass(frozen=True)
class EnabledPaper:
    id: int
    title: str
    format: str  # "pdf" | "latex"
    sections: int


async def load_enabled_papers(
    conn: aiosqlite.Connection, session_id: int
) -> list[EnabledPaper]:
    async with conn.execute(
        "SELECT pc.id, pc.title, pc.source_path, pc.sections_json FROM papers p "
        "JOIN paper_content pc ON pc.id = p.paper_content_id "
        "WHERE p.session_id = ? AND p.enabled = 1 ORDER BY p.added_at, p.id",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [enabled_paper_from_row(*r) for r in rows]


def enabled_paper_from_row(
    pid: Any, title: Any, source_path: Any, sections_json: Any,
) -> EnabledPaper:
    """One ``paper_content`` row (id, title, source_path, sections_json)."""
    try:
        sections = len(json.loads(sections_json or "[]"))
    except (TypeError, ValueError):
        sections = 0
    fmt = "pdf" if PurePath(str(source_path or "")).suffix.lower() == ".pdf" \
        else "latex"
    return EnabledPaper(int(pid), str(title or f"Paper {pid}"), fmt, sections)


def render_enabled_papers(papers: list[EnabledPaper]) -> str:
    if not papers:
        return "<enabled_papers>\n(none)\n</enabled_papers>"
    lines = [
        f'<paper id="{p.id}" format="{p.format}" sections="{p.sections}">'
        f"{escape(p.title)}</paper>"
        for p in papers
    ]
    return "<enabled_papers>\n" + "\n".join(lines) + "\n</enabled_papers>"


def render_memories(rows: list[tuple[int, str, str]]) -> str:
    """Active memories, one tag each: the id is what ``forget`` takes; the
    user-sourced text is escaped."""
    return "\n".join(
        f'<memory id="{mid}" scope="{scope}">{escape(content)}</memory>'
        for mid, scope, content in rows)


@dataclass(frozen=True)
class SessionStateParts:
    """The inputs of the ``<session_state>`` block. ``papers`` is library
    state (re-readable by id); the rest is mutable, so the agent records it."""

    papers: list[EnabledPaper]
    idea_context: str | None = None
    slide_context: str | None = None
    memories: str | None = None
    ui_language: str | None = None


def render_session_state(parts: SessionStateParts) -> str:
    out = [render_enabled_papers(parts.papers)]
    if parts.idea_context:
        out.append(parts.idea_context)
    if parts.slide_context:
        out.append(f"<slide_context>\n{parts.slide_context}\n</slide_context>")
    if parts.memories:
        out.append(f"<memories>\n{parts.memories}\n</memories>")
    if parts.ui_language:
        out.append(f"<ui_language>{escape(parts.ui_language)}</ui_language>")
    return "\n\n".join(out)


async def session_state_parts(
    conn: aiosqlite.Connection, session_id: int, state: dict[str, Any] | Any,
    *, papers: list[EnabledPaper] | None = None,
) -> SessionStateParts:
    """See :func:`build_session_state`."""
    if papers is None:
        papers = await load_enabled_papers(conn, session_id)
    idea = state.get("idea_context")
    if idea is None and "idea_context" not in state:
        ctx = await build_idea_context(conn, session_id=session_id)
        idea = ctx.block if ctx is not None else None
    slide = state.get("slide_context")
    memories = render_memories(await active_memories(conn, session_id=session_id))
    ui = state.get("ui_language")
    return SessionStateParts(
        papers=papers,
        idea_context=str(idea) if idea else None,
        slide_context=str(slide) if slide else None,
        memories=memories or None,
        ui_language=str(ui) if ui else None,
    )


async def build_session_state(
    conn: aiosqlite.Connection, session_id: int, state: dict[str, Any] | Any,
    *, papers: list[EnabledPaper] | None = None,
) -> str:
    """Session state as descriptive tags: enabled papers (no content), the idea
    context of an idea chat, the attached slide, active memories, UI language.
    ``state['idea_context']`` / ``state['slide_context']`` are used when the
    caller already built them; the idea context is built here otherwise."""
    return render_session_state(
        await session_state_parts(conn, session_id, state, papers=papers))


# ------------------------------------------------------------ conversation


@dataclass
class Conversation:
    messages: list[dict[str, Any]]
    kept_ids: set[int] = field(default_factory=set)
    """Chunk ids read or cited by the verbatim (kept) turns: citable without a
    re-read."""
    folded_turns: int = 0
    kept_figures: set[str] = field(default_factory=set)
    """Figures (``"<paper>/<figure id>"``) the kept turns looked at or cited."""
    kept_views: set[str] = field(default_factory=set)
    """Page regions (``"<paper>/<page>/<box>"``) the kept turns viewed or cited."""


def _ints(raw: Any) -> list[int]:
    if not isinstance(raw, list):
        return []
    return [i for i in raw if isinstance(i, int) and not isinstance(i, bool)]


def _strs(raw: Any) -> list[str]:
    return [str(x) for x in raw] if isinstance(raw, list) else []


def _work_record(
    answer: str, raw_record: str | None,
) -> tuple[str, set[int], set[str], set[str]]:
    """The one-line ``<work_record>`` of an earlier assistant turn and the ids
    it makes citable (chunk ids, figure keys). Runs from before the agent flow have no record: their
    cited ids come from the answer's own markers."""
    record: dict[str, Any] | None = None
    if raw_record:
        try:
            loaded = json.loads(raw_record)
            record = loaded if isinstance(loaded, dict) else None
        except ValueError:
            record = None
    if record is None:
        cited = cited_ids(answer)
        line = f"tools: (earlier flow); cited: {', '.join(map(str, cited)) or '-'}"
        return f"<work_record>{line}</work_record>", set(cited), set(), set()
    tools = [str(t) for t in record.get("tools") or []]
    read, cited = _ints(record.get("read")), _ints(record.get("cited"))
    line = (f"tools: {', '.join(tools) or '-'}; "
            f"read: {', '.join(map(str, read)) or '-'}; "
            f"cited: {', '.join(map(str, cited)) or '-'}")
    figures = list(dict.fromkeys(
        _strs(record.get("figures")) + _strs(record.get("cited_figures"))
        + cited_figures(answer)))
    if figures:
        line += f"; figures: {', '.join(figures)}"
    views = list(dict.fromkeys(
        _strs(record.get("views")) + _strs(record.get("cited_views"))
        + cited_views(answer)))
    if views:
        line += f"; views: {', '.join(views)}"
    return (f"<work_record>{line}</work_record>", set(read) | set(cited), set(figures),
            set(views))


async def _assistant_rows(
    conn: aiosqlite.Connection, session_id: int
) -> list[tuple[str, int | None, str | None]]:
    async with conn.execute(
        "SELECT m.content, m.run_id, r.agent_record_json FROM messages m "
        "LEFT JOIN runs r ON r.id = m.run_id "
        "WHERE m.session_id = ? AND m.role = 'assistant' ORDER BY m.id",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [(str(r[0]), r[1], r[2]) for r in rows]


def in_live_history(role: str, content: str, run_status: str | None) -> bool:
    """Whether a stored message is part of the history a turn is given: the
    client leaves out empty messages (a stopped or interrupted turn's stub)
    and an errored turn's answer (the error text). The server-side history
    fallback and the feedback export use the same rule."""
    if not content:
        return False
    return not (role == "assistant" and run_status == "error")


def _turns(history: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any] | None]]:
    """Group history into (user question, assistant entry or None) turns."""
    turns: list[tuple[str, dict[str, Any] | None]] = []
    for h in history:
        role, content = h.get("role"), str(h.get("content") or "")
        if role == "user":
            turns.append((content, None))
        elif role == "assistant":
            if turns and turns[-1][1] is None:
                turns[-1] = (turns[-1][0], h)
            else:
                turns.append(("", h))
    return turns


async def load_conversation(
    conn: aiosqlite.Connection, session_id: int, history: list[dict[str, Any]],
    *, keep_turns: int = AGENT_KEEP_TURNS,
) -> Conversation:
    """Earlier turns as messages; each assistant answer is followed by its
    work record. Assistant entries are matched to their run by ``run_id`` when
    the entry has one, else by identical content (first unused match)."""
    rows = await _assistant_rows(conn, session_id)
    by_run = {rid: rec for _, rid, rec in rows if rid is not None}
    unused = list(range(len(rows)))

    def record_for(entry: dict[str, Any]) -> str | None:
        rid = entry.get("run_id")
        if isinstance(rid, int) and rid in by_run:
            return by_run[rid]
        content = str(entry.get("content") or "")
        for pos in unused:
            if rows[pos][0] == content:
                unused.remove(pos)
                return rows[pos][2]
        return None

    turns = _turns(history)
    keep = max(keep_turns, 0)
    if len(turns) <= keep:
        folded, kept = [], turns
    elif keep:
        folded, kept = turns[:-keep], turns[-keep:]
    else:
        folded, kept = turns, []
    messages: list[dict[str, Any]] = []
    if folded:
        lines = []
        for i, (q, a) in enumerate(folded, 1):
            question = " ".join(q.split())[:SUMMARY_QUESTION_CHARS] or "(no question)"
            if a is not None:
                record_for(a)  # consume its match so later identical answers align
                cited = cited_ids(str(a.get("content") or ""))
            else:
                cited = []
            lines.append(f"{i}. {question} (cited: "
                         f"{', '.join(map(str, cited)) or '-'})")
        messages.append({
            "role": "user",
            "content": "<earlier_summary>\nEarlier questions in this chat "
                       "(answers dropped; re-read cited chunks with read_chunks "
                       "before citing them):\n" + "\n".join(lines)
                       + "\n</earlier_summary>",
        })
    kept_ids: set[int] = set()
    kept_figures: set[str] = set()
    kept_views: set[str] = set()
    for q, a in kept:
        if q:
            messages.append({"role": "user", "content": q})
        if a is not None:
            answer = str(a.get("content") or "")
            line, ids, figs, views = _work_record(answer, record_for(a))
            kept_ids |= ids
            kept_figures |= figs
            kept_views |= views
            messages.append({"role": "assistant", "content": f"{answer}\n\n{line}"})
    return Conversation(messages=messages, kept_ids=kept_ids,
                        folded_turns=len(folded), kept_figures=kept_figures,
                        kept_views=kept_views)


async def build_conversation(
    conn: aiosqlite.Connection, session_id: int, history: list[dict[str, Any]],
    *, keep_turns: int = AGENT_KEEP_TURNS,
) -> list[dict[str, Any]]:
    conv = await load_conversation(conn, session_id, history, keep_turns=keep_turns)
    return conv.messages


# ------------------------------------------------------------ token accounting


def count_tokens(text: str) -> int:
    if not text:
        return 0
    return int(litellm.token_counter(model=_COUNT_MODEL, text=text))


def _messages_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            # Multimodal: count the text parts; images are not text tokens.
            parts += [str(p.get("text", "")) for p in content
                      if isinstance(p, dict) and p.get("type") == "text"]
        elif content:
            parts.append(str(content))
        if m.get("tool_calls"):
            parts.append(json.dumps(m["tool_calls"], ensure_ascii=False))
    return "\n".join(parts)


@dataclass
class ContextBlocks:
    instructions: str
    tools: list[dict[str, Any]]
    session_state: str
    conversation: list[dict[str, Any]]
    this_turn: list[dict[str, Any]]

    def token_counts(self) -> dict[str, int]:
        return {
            "instructions": count_tokens(self.instructions),
            "tools": count_tokens(json.dumps(self.tools, ensure_ascii=False)),
            "session_state": count_tokens(self.session_state),
            "conversation": count_tokens(_messages_text(self.conversation)),
            "this_turn": count_tokens(_messages_text(self.this_turn)),
        }


class TurnTokenCounter:
    """Per-turn token accounting without re-tokenising the context each step:
    the fixed blocks (instructions, tools, session state, conversation) are
    counted once; this turn's messages are append-only, so each is counted
    once, when it first appears."""

    def __init__(self, blocks: ContextBlocks) -> None:
        self._fixed = {
            "instructions": count_tokens(blocks.instructions),
            "tools": count_tokens(json.dumps(blocks.tools, ensure_ascii=False)),
            "session_state": count_tokens(blocks.session_state),
            "conversation": count_tokens(_messages_text(blocks.conversation)),
        }
        self._turn: list[int] = []

    def counts(self, this_turn: list[dict[str, Any]]) -> dict[str, int]:
        while len(self._turn) < len(this_turn):
            msg = this_turn[len(self._turn)]
            self._turn.append(count_tokens(_messages_text([msg])))
        return {**self._fixed, "this_turn": sum(self._turn)}
