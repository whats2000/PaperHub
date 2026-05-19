"""Per-paper paper_qa subagent (Plan C v2.10-3).

Runs a bounded tool-calling LLM loop over a single paper's sections,
picking the chunks that contain evidence for a user question. Returns
a ``PerPaperPicks`` with the cited chunks and a 2-3 sentence rationale.

The subagent exposes two tools to the LLM:
- ``list_sections()`` — returns the section TOC (free; no budget cost).
- ``read_section(name)`` — returns every chunk in that section
  (counts against ``max_section_reads``).

Loop exit conditions:
- LLM responds without ``tool_calls`` → treat as final summary, extract
  ``[chunk:<id>]`` markers → return PerPaperPicks.
- ``read_count >= max_section_reads`` AND LLM still calls ``read_section``
  → force-stop; return all chunks read so far as best-effort fallback.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import aiosqlite
import litellm

from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.tracing.tracer import Tracer

__all__ = [
    "MAX_SECTION_READS",
    "PickedChunk",
    "PerPaperPicks",
    "run_paper_qa_subagent",
]

MAX_SECTION_READS: int = 5

# ──────────────────────── chunk-marker regex ─────────────────────────

_CHUNK_MARKER_RE = re.compile(r"\[chunk:(\d+(?:,\d+)*)\]")

# ──────────────────────── tool schemas ──────────────────────────────

_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_sections",
            "description": (
                "Return the section table-of-contents for this paper "
                "(name, token count, chunk count per section). "
                "Call this first to understand the paper structure. "
                "Free — does not count against the read budget."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_section",
            "description": (
                "Return every chunk in the named section, each prefixed "
                "with [chunk:<id>]. Counts against the read budget. "
                "Use list_sections() first to discover valid section names."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exact section name from list_sections().",
                    },
                },
                "required": ["name"],
            },
        },
    },
]


# ──────────────────────── domain types ──────────────────────────────


@dataclass(frozen=True)
class PickedChunk:
    chunk_id: int
    text: str
    section: str | None


@dataclass(frozen=True)
class PerPaperPicks:
    paper_content_id: int
    title: str
    picked_chunks: list[PickedChunk]
    rationale: str  # the subagent's 1-3 sentence summary


# ──────────────────────── DB helpers ────────────────────────────────


async def _list_sections(
    *,
    paper_content_id: int,
    conn: aiosqlite.Connection,
) -> str:
    """Return the section TOC for the paper as a JSON string.

    Returns an error dict if ``sections_json`` is NULL (pre-re-ingest row).
    """
    async with conn.execute(
        "SELECT sections_json FROM paper_content WHERE id = ?",
        (paper_content_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None or row[0] is None:
        return json.dumps({"error": "no section TOC available for this paper"})
    try:
        sections = json.loads(row[0])
    except json.JSONDecodeError:
        return json.dumps({"error": "section TOC is corrupted; re-ingest this paper"})
    return json.dumps([
        {"name": s["name"], "tokens": s["token_count"], "chunks": s["chunk_count"]}
        for s in sections
    ])


async def _read_section(
    *,
    paper_content_id: int,
    name: str,
    conn: aiosqlite.Connection,
) -> tuple[str, list[PickedChunk]]:
    """Return all chunks in the named section.

    Returns an error string and empty list when the section doesn't exist.
    """
    async with conn.execute(
        "SELECT id, text, section FROM chunks "
        "WHERE paper_content_id = ? AND section = ? "
        "ORDER BY char_start",
        (paper_content_id, name),
    ) as cur:
        rows = await cur.fetchall()
    if not rows:
        return (
            json.dumps({
                "error": f"unknown section: {name!r}. Call list_sections() first.",
            }),
            [],
        )
    picks = [PickedChunk(chunk_id=r[0], text=r[1], section=r[2]) for r in rows]
    body = "\n\n".join(f"[chunk:{p.chunk_id}]\n{p.text}" for p in picks)
    return (body, picks)


# ──────────────────────── extraction helper ──────────────────────────


def _extract_cited_chunk_ids(summary: str) -> list[int]:
    """Parse ``[chunk:N]`` and ``[chunk:N,M,...]`` markers from summary."""
    out: list[int] = []
    for m in _CHUNK_MARKER_RE.finditer(summary):
        out.extend(int(x) for x in m.group(1).split(","))
    return out


# ──────────────────────── main entry point ──────────────────────────


async def run_paper_qa_subagent(
    *,
    paper_content_id: int,
    title: str,
    user_message: str,
    tracer: Tracer,
    model: str,
    conn: aiosqlite.Connection,
    max_section_reads: int = MAX_SECTION_READS,
    registry: PromptRegistry | None = None,
    **litellm_kwargs: Any,
) -> PerPaperPicks:
    """Run the per-paper paper_qa subagent loop.

    Returns a ``PerPaperPicks`` with the chunks the LLM cited in its
    final summary, plus a rationale string (the full final message).

    When the LLM is force-stopped (budget exhausted without a final
    no-tool-calls response), returns ALL chunks read as a best-effort
    fallback — every chunk the subagent ever loaded is handed to the
    finalizer.
    """
    reg = registry or PromptRegistry()
    prompt = reg.get("paper_qa_subagent/v1")
    system = prompt.system.format(max_section_reads=max_section_reads)
    user = prompt.user_template.format(title=title, user_message=user_message)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    # Tracks every chunk the subagent has ever read (deduped by chunk_id).
    seen_chunks: dict[int, PickedChunk] = {}
    read_count: int = 0
    final_summary: str = ""

    # ONE tracer step around the entire loop — one paper_qa:subagent row
    # per run_paper_qa_subagent call, summarising the whole loop at exit.
    async with tracer.step(
        agent="research",
        tool="paper_qa:subagent",
        model=model,
    ) as step:
        step.record_args({
            "paper_content_id": paper_content_id,
            "title": title,
        })

        # +2: one for a free list_sections turn (before any reads), one safety
        # margin. Force-stop fires after dispatch in the iteration where read_count
        # hits max — the LLM doesn't get an extra response turn after exhaustion.
        for _iteration in range(max_section_reads + 2):
            response = await litellm.acompletion(
                model=model,
                messages=messages,
                tools=_TOOL_SCHEMAS,
                tool_choice="auto",
                **litellm_kwargs,
            )
            msg = response["choices"][0]["message"]
            tool_calls = msg.get("tool_calls") or []

            if not tool_calls:
                # Final summary — parse cites and exit loop.
                final_summary = str(msg.get("content") or "").strip()
                break

            # Append assistant turn.
            messages.append({
                "role": "assistant",
                "content": msg.get("content"),
                "tool_calls": tool_calls,
            })

            # Execute each tool call.
            for call in tool_calls:
                name = call["function"]["name"]
                try:
                    raw_args = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    raw_args = {}

                if name == "list_sections":
                    result_str = await _list_sections(
                        paper_content_id=paper_content_id, conn=conn,
                    )
                elif name == "read_section":
                    section_name = raw_args.get("name", "")
                    if read_count >= max_section_reads:
                        result_str = json.dumps({
                            "error": (
                                f"read_section budget exhausted ({max_section_reads}). "
                                "Stop calling tools and write your final summary now."
                            ),
                        })
                    else:
                        result_str, new_picks = await _read_section(
                            paper_content_id=paper_content_id,
                            name=section_name,
                            conn=conn,
                        )
                        for p in new_picks:
                            seen_chunks[p.chunk_id] = p
                        if new_picks:  # only count a successful read
                            read_count += 1
                else:
                    # Off-palette tool call — return a clear error.
                    result_str = json.dumps({
                        "error": f"unknown tool {name!r}. Use list_sections or read_section.",
                    })

                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": name,
                    "content": result_str,
                })

            # Force-stop: budget exhausted AND the last LLM call still had
            # tool_calls (meaning it didn't self-terminate after receiving
            # the exhaustion error). Break out and use the fallback.
            if read_count >= max_section_reads and all(
                c["function"]["name"] == "read_section" for c in tool_calls
            ):
                break  # every read_section call received the exhaustion error; force-stop

        # Compute picks from whatever the loop produced.
        cited_ids = _extract_cited_chunk_ids(final_summary)
        if cited_ids:
            picked = [seen_chunks[cid] for cid in cited_ids if cid in seen_chunks]
        else:
            # No citations in summary (LLM forgot, or force-stopped without summary):
            # hand everything read to the finalizer as best-effort fallback.
            picked = list(seen_chunks.values())

        step.record_result({
            "reads_used": read_count,
            "chunks_read": len(seen_chunks),
            "chunks_cited": len(picked),
            "summary_len": len(final_summary),
        })

    rationale = (
        final_summary
        if final_summary
        else "[force-stopped: read budget exhausted without final summary]"
    )
    return PerPaperPicks(
        paper_content_id=paper_content_id,
        title=title,
        picked_chunks=picked,
        rationale=rationale,
    )
