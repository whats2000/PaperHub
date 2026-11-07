"""The recent conversation as the paper_qa subagents see it (run 892).

The subagents gather evidence for the finalizer, which is the agent actually
talking to the user. Given only the router's rewrite of the latest message, a
follow-up such as "give me a more detailed experimental method" read as "each
paper's own method" and the answer drifted off the discussed idea. So each
subagent also gets the last exchange: the previous user message and the head
of the previous answer (where the subject is stated), bounded to keep the
per-paper loop cheap. Citation markers are stripped: they point at chunks of
other papers and must never be copied into this paper's picks.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

RECENT_QUESTION_CHARS = 1000
"""Cap on the previous user message (an idea chat's opener is ~600 chars)."""
RECENT_ANSWER_CHARS = 1500
"""Cap on the previous answer's head: enough for its framing paragraphs."""

_CITATION = re.compile(r"\s*\[chunk:[^\]]*\](?:\s*\(p\.\s*\d+\))?|\s*\(p\.\s*\d+\)")


def _clean(text: str) -> str:
    return _CITATION.sub("", text).strip()


def recent_conversation(history: Sequence[dict[str, Any]] | None) -> str:
    """The last exchange of ``history`` as XML elements, or "" when there is
    none (a first message). The caller wraps it in ``<recent_conversation>``."""
    turns = [h for h in (history or [])
             if h.get("role") in ("user", "assistant") and str(h.get("content") or "").strip()]
    if not turns:
        return ""
    answer = ""
    if turns[-1]["role"] == "assistant":
        answer = _clean(str(turns[-1]["content"]))
        turns = turns[:-1]
    question = next((_clean(str(t["content"])) for t in reversed(turns)
                     if t["role"] == "user"), "")
    parts: list[str] = []
    if question:
        parts.append("<previous_user_message>"
                     f"{question[:RECENT_QUESTION_CHARS]}</previous_user_message>")
    if answer:
        cut = len(answer) > RECENT_ANSWER_CHARS
        attr = ' truncated="true"' if cut else ""
        parts.append(f"<previous_answer_excerpt{attr}>"
                     f"{answer[:RECENT_ANSWER_CHARS]}</previous_answer_excerpt>")
    return "\n".join(parts)
