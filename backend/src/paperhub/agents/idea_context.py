"""Idea context for chats opened from an idea (Plan H3, QA I4).

``POST /ideation/ideas/{id}/chat`` links the new session in ``idea_sessions``
and returns a composer prefill — but the prefill is only the first message. If
the user replaces it with their own question, or comes back later through
"Continue chat" (empty composer), the answer would know nothing about the
idea. So every turn of a linked session gets the idea server-side:
``build_idea_context`` renders the problem, the idea's title and rationale and
its source / target papers and sections as one XML block, which reaches the
answering agents the way the active-slide block does — ``slide_aware_query``
prepends it to the paper_qa query (subagent + finalizer), and the chitchat and
paper_search synthesizers get it with their memory block (``with_idea``).
Slides and library_stats do not take it: a deck is built from the attached
papers and a library count has no use for it.

The chat layer records the block's resolved IDs on an
``ideation:idea_context`` trace step (never the rendered text).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape, quoteattr

import aiosqlite


@dataclass(frozen=True)
class IdeaContext:
    idea_id: int
    block: str
    record: dict[str, Any] = field(default_factory=dict)
    """IDs the block was built from (the trace payload)."""


def _ids(raw: Any) -> list[int]:
    try:
        return [int(x) for x in json.loads(raw or "[]")]
    except (ValueError, TypeError):
        return []


async def build_idea_context(
    conn: aiosqlite.Connection, *, session_id: int,
) -> IdeaContext | None:
    """The idea the session was opened from, as an ``<idea_context>`` block;
    None for a session no idea links (the most recent link wins if several)."""
    async with conn.execute(
        "SELECT i.id, i.problem_id, i.source_doc, i.source_section, "
        "i.source_chunk_ids_json, i.target_doc, i.target_section, "
        "i.target_chunk_ids_json, i.title, i.rationale, p.text, "
        "sp.title, tp.title "
        "FROM idea_sessions s JOIN ideas i ON i.id = s.idea_id "
        "LEFT JOIN problems p ON p.id = i.problem_id "
        "LEFT JOIN paper_content sp ON sp.id = i.source_doc "
        "LEFT JOIN paper_content tp ON tp.id = i.target_doc "
        "WHERE s.session_id = ? ORDER BY i.id DESC LIMIT 1",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    (iid, pid, sdoc, ssec, schunks, tdoc, tsec, tchunks, title, rationale,
     problem, stitle, ttitle) = row
    lines = ["<idea_context>",
             "This chat was opened to explore the idea below from the user's Ideas page."]
    if problem:
        lines.append(f"<problem>{escape(str(problem))}</problem>")
    lines.append(f"<idea_title>{escape(str(title))}</idea_title>")
    if rationale:
        lines.append(f"<rationale>{escape(str(rationale))}</rationale>")
    lines.append(f"<source_paper title={quoteattr(str(stitle or ''))} "
                 f"section={quoteattr(str(ssec))} />")
    if tdoc is not None:
        lines.append(f"<target_paper title={quoteattr(str(ttitle or ''))} "
                     f"section={quoteattr(str(tsec or ''))} />")
    lines.append("</idea_context>")
    record = {
        "idea_id": int(iid), "problem_id": int(pid) if pid is not None else None,
        "source_doc": int(sdoc), "source_section": str(ssec),
        "source_chunk_ids": _ids(schunks),
        "target_doc": int(tdoc) if tdoc is not None else None,
        "target_section": str(tsec) if tsec is not None else None,
        "target_chunk_ids": _ids(tchunks), "title": str(title),
    }
    return IdeaContext(idea_id=int(iid), block="\n".join(lines), record=record)


def with_idea(memory_block: str, idea_block: str | None) -> str:
    """A memory-context value carrying the idea block too (either may be empty)."""
    return "\n\n".join(b for b in (idea_block or "", memory_block) if b)
