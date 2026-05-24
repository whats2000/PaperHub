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

# Matches any ``chunk:<digits>`` occurrence regardless of bracket structure.
# Real LLM responses mix three citation formats interchangeably and the old
# bracket-bounded pattern silently dropped the multi-format ones:
#
#   [chunk:101]               ✓ matched by old + new
#   [chunk:101,102]           ✓ matched by old + new
#   [chunk:101, 102]          ✗ old missed (space after comma)
#   [chunk:101, chunk:102]    ✗ old missed (repeated `chunk:` prefix)
#   (chunk:101) or chunk:101  ✗ old missed (non-bracketed)
#
# Empirical: gemini-3.1-flash-lite uses the third + fourth form heavily.
# Scanning for the token directly catches every variant and is robust to
# future format drift.
_CHUNK_MARKER_RE = re.compile(r"chunk:(\d+)")

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
    {
        "type": "function",
        "function": {
            "name": "list_figures_tables",
            "description": (
                "Return the index of this paper's figures and tables "
                "(label, kind, caption, page). Use this for a question about "
                "a SPECIFIC table or figure by number — a floated table may "
                "not appear in any section's prose. "
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
            "name": "read_layout_object",
            "description": (
                "Return the chunk for the figure/table with the given label "
                "(e.g. 'Table 1', 'Figure 3'), prefixed with [chunk:<id>]. "
                "Call list_figures_tables() first to discover valid labels. "
                "Counts against the read budget."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Exact label from list_figures_tables().",
                    },
                },
                "required": ["label"],
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
    page: int | None = None  # PDF page from Marker; None for LaTeX/PyMuPDF chunks


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
    The ``page`` column is included so the LLM can refer to PDF page positions
    in its cited summary (Marker chunks carry a page; LaTeX/PyMuPDF chunks have
    page=NULL and the annotation is omitted).
    """
    async with conn.execute(
        "SELECT id, text, section, page FROM chunks "
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
    picks = [
        PickedChunk(chunk_id=r[0], text=r[1], section=r[2], page=r[3])
        for r in rows
    ]
    # Wrap each chunk in an explicit <chunk id="N">…</chunk> container so the
    # boundary + id binding is unambiguous, and the input label is NOT the same
    # token (`[chunk:N]`) the model must EMIT as a citation — keeping "here is a
    # chunk" distinct from "cite it" reduces mis-attribution on dense text.
    # When page is non-NULL (Marker chunks), append " (p.N)" after the closing
    # tag so the LLM can mention the PDF location in its prose summary.
    def _chunk_block(p: PickedChunk) -> str:
        block = f'<chunk id="{p.chunk_id}">\n{p.text}\n</chunk>'
        if p.page is not None:
            block += f" (p.{p.page})"
        return block

    body = "\n\n".join(_chunk_block(p) for p in picks)
    return (body, picks)


async def _load_layout(
    *,
    paper_content_id: int,
    conn: aiosqlite.Connection,
) -> list[dict[str, Any]]:
    """Return the paper's layout_json as a list of dicts (empty when NULL/invalid)."""
    async with conn.execute(
        "SELECT layout_json FROM paper_content WHERE id = ?",
        (paper_content_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None or row[0] is None:
        return []
    try:
        data = json.loads(row[0])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [e for e in data if isinstance(e, dict)]


async def _list_figures_tables(
    *,
    paper_content_id: int,
    conn: aiosqlite.Connection,
) -> tuple[str, list[str]]:
    """Return the LLM-facing figure/table index + the list of labels surfaced.

    Each entry exposes ``{label, kind, caption, page}`` — the internal
    ``chunk_id`` is deliberately omitted from the LLM-facing text. Returns
    ``"(none)"`` when ``layout_json`` is NULL/empty.
    """
    layout = await _load_layout(paper_content_id=paper_content_id, conn=conn)
    if not layout:
        return ("(none)", [])
    surfaced = [
        {
            "label": e.get("label"),
            "kind": e.get("kind"),
            "caption": e.get("caption"),
            "page": e.get("page"),
        }
        for e in layout
    ]
    labels = [str(e["label"]) for e in surfaced if e["label"] is not None]
    return (json.dumps(surfaced), labels)


async def _read_layout_object(
    *,
    paper_content_id: int,
    label: str,
    conn: aiosqlite.Connection,
) -> tuple[str, PickedChunk | None]:
    """Return the chunk for the figure/table with ``label`` (case-insensitive).

    Returns an error string and ``None`` when the label isn't in the layout
    index or its chunk row is missing. The chunk text is headed
    ``[chunk:<id>] (p.<page>)`` exactly like ``read_section`` formats chunks.
    """
    layout = await _load_layout(paper_content_id=paper_content_id, conn=conn)
    target = label.strip().casefold()
    match: dict[str, Any] | None = None
    for e in layout:
        lbl = e.get("label")
        if isinstance(lbl, str) and lbl.strip().casefold() == target:
            match = e
            break
    if match is None:
        return (
            json.dumps({
                "error": (
                    f"no such table/figure: {label!r}. "
                    "Call list_figures_tables() to see valid labels."
                ),
            }),
            None,
        )
    chunk_id = match.get("chunk_id")
    if not isinstance(chunk_id, int):
        return (
            json.dumps({"error": f"layout object {label!r} has no chunk to read."}),
            None,
        )
    async with conn.execute(
        "SELECT id, text, section, page FROM chunks WHERE id = ?",
        (chunk_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return (
            json.dumps({"error": f"chunk for {label!r} is missing; re-ingest this paper."}),
            None,
        )
    pick = PickedChunk(chunk_id=row[0], text=row[1], section=row[2], page=row[3])
    block = f'<chunk id="{pick.chunk_id}">\n{pick.text}\n</chunk>'
    if pick.page is not None:
        block += f" (p.{pick.page})"
    return (block, pick)


# ──────────────────────── extraction helper ──────────────────────────


def _extract_cited_chunk_ids(summary: str) -> list[int]:
    """Return every distinct ``chunk:<id>`` referenced in ``summary``, in
    first-occurrence order. Tolerates every citation format real LLMs emit
    (single, comma-list, repeated-prefix, with or without spaces, bracketed
    or not — see ``_CHUNK_MARKER_RE`` for the matrix). Duplicates collapse
    so the finalizer doesn't see the same chunk twice."""
    seen: set[int] = set()
    out: list[int] = []
    for m in _CHUNK_MARKER_RE.finditer(summary):
        cid = int(m.group(1))
        if cid not in seen:
            seen.add(cid)
            out.append(cid)
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

    # Per-CLAUDE.md "agent-flow observability policy": record enough state
    # at each step that a future debugger can reconstruct the full agent
    # context from the tool_calls row alone — no one-off instrumentation
    # script should be needed to diagnose subagent failures.
    #
    # Captured BUT NOT in tracer-args (those are bounded): a per-tool-call
    # log of {turn, tool, args, result_len, chunk_ids_returned, sections_listed}
    # accumulated below and flushed into record_result on step exit.
    tool_call_log: list[dict[str, Any]] = []
    listed_sections: list[str] | None = None  # captured first time list_sections is called
    listed_layout: list[str] | None = None  # labels surfaced by list_figures_tables
    layout_read_ids: list[int] = []  # chunk ids fetched via read_layout_object
    llm_turn_log: list[dict[str, Any]] = []  # one entry per LLM turn

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
        for iteration in range(max_section_reads + 2):
            response = await litellm.acompletion(
                model=model,
                messages=messages,
                tools=_TOOL_SCHEMAS,
                tool_choice="auto",
                **litellm_kwargs,
            )
            msg = response["choices"][0]["message"]
            tool_calls = msg.get("tool_calls") or []
            assistant_content = str(msg.get("content") or "")
            # Observability: log every LLM turn — what came back, did the LLM
            # call tools, what did it choose, what content did it emit. This
            # is the recorded evidence a debugger uses to determine whether
            # the LLM gave up empty, looped on the wrong section, etc.
            llm_turn_log.append({
                "turn": iteration,
                "content_len": len(assistant_content),
                "content_preview": assistant_content[:200],
                "tool_calls": [
                    {
                        "name": tc["function"]["name"],
                        "args": tc["function"]["arguments"],
                    }
                    for tc in tool_calls
                ],
            })

            if not tool_calls:
                # Final summary — parse cites and exit loop.
                final_summary = assistant_content.strip()
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

                tool_log_entry: dict[str, Any] = {
                    "turn": iteration,
                    "tool": name,
                    "args": raw_args,
                }

                if name == "list_sections":
                    result_str = await _list_sections(
                        paper_content_id=paper_content_id, conn=conn,
                    )
                    # Capture the section names the LLM saw so a post-mortem
                    # can answer "did the LLM have a usable TOC to navigate?"
                    try:
                        parsed_sections = json.loads(result_str)
                        if isinstance(parsed_sections, list):
                            sec_names = [
                                str(s["name"]) for s in parsed_sections
                                if isinstance(s, dict) and "name" in s
                            ]
                            tool_log_entry["sections_listed"] = sec_names
                            if listed_sections is None:
                                listed_sections = sec_names
                        elif (
                            isinstance(parsed_sections, dict)
                            and "error" in parsed_sections
                        ):
                            tool_log_entry["error"] = parsed_sections["error"]
                    except json.JSONDecodeError:
                        pass
                elif name == "read_section":
                    section_name = raw_args.get("name", "")
                    if read_count >= max_section_reads:
                        result_str = json.dumps({
                            "error": (
                                f"read_section budget exhausted ({max_section_reads}). "
                                "Stop calling tools and write your final summary now."
                            ),
                        })
                        tool_log_entry["error"] = "budget_exhausted"
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
                        tool_log_entry["chunk_ids_returned"] = [
                            p.chunk_id for p in new_picks
                        ]
                elif name == "list_figures_tables":
                    result_str, labels = await _list_figures_tables(
                        paper_content_id=paper_content_id, conn=conn,
                    )
                    tool_log_entry["layout_listed"] = labels
                    if listed_layout is None:
                        listed_layout = labels
                elif name == "read_layout_object":
                    label = str(raw_args.get("label", ""))
                    if read_count >= max_section_reads:
                        result_str = json.dumps({
                            "error": (
                                f"read budget exhausted ({max_section_reads}). "
                                "Stop calling tools and write your final summary now."
                            ),
                        })
                        tool_log_entry["error"] = "budget_exhausted"
                    else:
                        result_str, pick = await _read_layout_object(
                            paper_content_id=paper_content_id,
                            label=label,
                            conn=conn,
                        )
                        if pick is not None:
                            seen_chunks[pick.chunk_id] = pick
                            layout_read_ids.append(pick.chunk_id)
                            read_count += 1
                            tool_log_entry["chunk_ids_returned"] = [pick.chunk_id]
                        else:
                            tool_log_entry["error"] = "layout_not_found"
                else:
                    # Off-palette tool call — return a clear error.
                    result_str = json.dumps({
                        "error": (
                            f"unknown tool {name!r}. Use list_sections, read_section, "
                            "list_figures_tables, or read_layout_object."
                        ),
                    })
                    tool_log_entry["error"] = "off_palette"

                tool_log_entry["result_len"] = len(result_str)
                tool_call_log.append(tool_log_entry)

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
                c["function"]["name"] in ("read_section", "read_layout_object")
                for c in tool_calls
            ):
                break  # every read call received the exhaustion error; force-stop

        # Compute picks from whatever the loop produced.
        cited_ids = _extract_cited_chunk_ids(final_summary)
        if cited_ids:
            picked = [seen_chunks[cid] for cid in cited_ids if cid in seen_chunks]
        else:
            # No citations in summary (LLM forgot, or force-stopped without summary):
            # hand everything read to the finalizer as best-effort fallback.
            picked = list(seen_chunks.values())

        # Observability payload — see CLAUDE.md "agent-flow observability
        # policy". A future debugger reconstructs the full subagent flow
        # from this single record_result without needing one-off
        # instrumentation:
        #   - llm_turns: per-turn LLM choices (tool calls + content preview)
        #   - tool_call_log: per-tool-call args + outputs + chunk_ids returned
        #   - chunks_read_ids / chunks_cited_ids: the actual IDs (not just counts)
        #   - final_summary: the LLM's complete output text (cite source-of-truth)
        step.record_result({
            "reads_used": read_count,
            "chunks_read": len(seen_chunks),
            "chunks_read_ids": sorted(seen_chunks.keys()),
            "chunks_cited": len(picked),
            "chunks_cited_ids": [p.chunk_id for p in picked],
            "summary_len": len(final_summary),
            "final_summary": final_summary,
            "listed_sections": listed_sections,
            "listed_layout": listed_layout,
            "layout_read_ids": layout_read_ids,
            "llm_turns": llm_turn_log,
            "tool_call_log": tool_call_log,
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
