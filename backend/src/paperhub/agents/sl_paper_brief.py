"""Per-paper agentic brief subagent for the F4.4 slide pipeline (T1).

Runs a bounded tool-using LLM loop over ONE paper, picking the sections,
figures, and equations a presenter needs, and emits a structured
:class:`PaperTalkBrief` that downstream stages (T2 ``sl_plan_deck``,
T3 ``sl_render_slide``) consume.

Mirrors the proven Plan-C-v2.10 ``paper_qa_subagent`` pattern (tool palette
+ bounded read budget + force-stop fallback + single Tracer step around the
whole loop). The tool surface is augmented for slide-design needs:

- ``list_sections()`` — TOC (free).
- ``read_section(name)`` — every chunk in the named section.
- ``read_figure_block(figure_key)`` — caption + nearby paragraph context
  for ONE figure (looked up from PaperAsset).
- ``read_equations(section)`` — every ``\\begin{equation}`` /
  ``\\begin{align}`` block in the named section, verbatim.

The final no-tool-calls LLM response is parsed as JSON and validated
against :class:`PaperTalkBrief`. Tracing follows the project's agent-flow
observability policy: one ``report:paper_brief`` row per call, with a
full per-turn + per-tool-call log so a debugger can reconstruct the loop
from the DB alone.

T1 ships the node and tests; the subgraph wiring lands in T5.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import aiosqlite
import litellm
from pydantic import ValidationError

from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.models.domain import PaperTalkBrief
from paperhub.pipelines.paper_asset import PaperAsset, read_paper_asset
from paperhub.tracing.tracer import Tracer

__all__ = [
    "MAX_SECTION_READS",
    "MAX_FIGURE_READS",
    "MAX_EQUATION_READS",
    "run_sl_paper_brief",
]

# Read budgets — distinct per tool so a figure-heavy paper doesn't starve the
# section reads (and vice versa). Tuned for Round-1 baseline; revisit after
# the harness gates land in T6.
MAX_SECTION_READS: int = 5
MAX_FIGURE_READS: int = 5
MAX_EQUATION_READS: int = 2

# Hard iteration cap on the agentic loop — every legal call plus a margin
# for the final no-tool-calls turn.
_MAX_TURNS: int = MAX_SECTION_READS + MAX_FIGURE_READS + MAX_EQUATION_READS + 3

# Strip a wrapping markdown code fence (```json ... ```) so a fenced JSON
# response still validates. Tolerates an optional language tag.
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n?|\n?```$")

# ──────────────────────── tool schemas ──────────────────────────────

_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_sections",
            "description": (
                "Return the section table-of-contents for this paper "
                "(name, token count, chunk count per section). Free — "
                "does not count against any read budget. Always call "
                "this first."
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
                "Return every chunk in the named section, each wrapped in "
                "<chunk id=\"N\">…</chunk>. Counts against the section "
                "read budget. Favour Methods and Results sections."
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
            "name": "read_figure_block",
            "description": (
                "Return the caption and surrounding paragraph context for "
                "ONE figure, looked up by its inventory key "
                "(e.g. 'p0-fig-001'). Counts against the figure read "
                "budget. Use this to decide whether a figure is "
                "slide-worthy and to draft its one_line_interpretation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "figure_key": {
                        "type": "string",
                        "description": "Exact figure key from the FIGURE INVENTORY.",
                    },
                },
                "required": ["figure_key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_equations",
            "description": (
                "Return every \\begin{equation}/\\begin{align}/etc. block "
                "in the named section, verbatim. Counts against the "
                "equation read budget. Use to capture central math from "
                "Methods so key_equations can quote it verbatim."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "description": "Exact section name from list_sections().",
                    },
                },
                "required": ["section"],
            },
        },
    },
]


# ──────────────────────── DB / asset helpers ────────────────────────


async def _list_sections(
    *,
    paper_content_id: int,
    conn: aiosqlite.Connection,
) -> tuple[str, list[str]]:
    """Return the section TOC as a JSON string + the list of section names.

    The names are tracked so the trace records what TOC the LLM actually saw.
    """
    async with conn.execute(
        "SELECT sections_json FROM paper_content WHERE id = ?",
        (paper_content_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None or row[0] is None:
        return (
            json.dumps({"error": "no section TOC available for this paper"}),
            [],
        )
    try:
        sections = json.loads(row[0])
    except json.JSONDecodeError:
        return (
            json.dumps({"error": "section TOC is corrupted; re-ingest this paper"}),
            [],
        )
    names = [str(s["name"]) for s in sections if isinstance(s, dict) and "name" in s]
    payload = json.dumps([
        {"name": s["name"], "tokens": s["token_count"], "chunks": s["chunk_count"]}
        for s in sections
        if isinstance(s, dict)
    ])
    return payload, names


async def _read_section(
    *,
    paper_content_id: int,
    name: str,
    conn: aiosqlite.Connection,
) -> tuple[str, list[int]]:
    """Return every chunk in ``name`` as ``<chunk id="N">…</chunk>`` blocks
    + the list of chunk ids returned.
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
    chunk_ids: list[int] = []
    blocks: list[str] = []
    for cid, text, _section, page in rows:
        chunk_ids.append(int(cid))
        body = f'<chunk id="{int(cid)}">\n{text}\n</chunk>'
        if page is not None:
            body += f" (p.{int(page)})"
        blocks.append(body)
    return ("\n\n".join(blocks), chunk_ids)


async def _resolve_source_dir(
    *,
    paper_content_id: int,
    conn: aiosqlite.Connection,
) -> Path | None:
    """Return the paper's ``source_dir_path`` as a ``Path``, or ``None`` if
    the row is missing / has no source dir."""
    async with conn.execute(
        "SELECT source_dir_path FROM paper_content WHERE id = ?",
        (paper_content_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None or row[0] is None:
        return None
    return Path(str(row[0]))


async def _read_figure_block(
    *,
    paper_content_id: int,
    figure_key: str,
    asset: PaperAsset | None,
    paper_idx: int,
    conn: aiosqlite.Connection,
) -> str:
    """Return a JSON block with the figure's caption + nearby chunk text.

    ``figure_key`` is a deck-inventory key (``p{idx}-{figure_id}``); we strip
    the ``p{idx}-`` prefix to look up the figure inside this paper's
    PaperAsset. The "nearby paragraph context" is one chunk from the figure's
    section, taken as a usable proxy without requiring per-figure paragraph
    extraction at ingest time.
    """
    if asset is None:
        return json.dumps({"error": "this paper has no PaperAsset (no figures available)"})

    expected_prefix = f"p{paper_idx}-"
    if figure_key.startswith(expected_prefix):
        local_id = figure_key[len(expected_prefix) :]
    else:
        # Tolerate the LLM dropping the deck-prefix when the brief is for a
        # single paper — fall back to a raw id match.
        local_id = figure_key

    match = next((f for f in asset.figures if f.id == local_id), None)
    if match is None:
        valid_keys = [f"{expected_prefix}{f.id}" for f in asset.figures]
        return json.dumps({
            "error": f"unknown figure_key: {figure_key!r}",
            "valid_keys": valid_keys,
        })

    context_chunk: str | None = None
    if match.section:
        async with conn.execute(
            "SELECT text FROM chunks "
            "WHERE paper_content_id = ? AND section = ? "
            "ORDER BY char_start LIMIT 1",
            (paper_content_id, match.section),
        ) as cur:
            row = await cur.fetchone()
        if row is not None:
            context_chunk = str(row[0])

    payload: dict[str, Any] = {
        "figure_key": figure_key,
        "caption": match.caption,
        "page": match.page,
        "section": match.section,
    }
    if context_chunk is not None:
        payload["context"] = context_chunk
    return json.dumps(payload, ensure_ascii=False)


async def _read_equations(
    *,
    section: str,
    asset: PaperAsset | None,
    valid_section_names: list[str] | None,
) -> tuple[str, int]:
    """Return every equation in ``section`` (verbatim LaTeX) + the count.

    Filters the PaperAsset's ``equations`` index directly by
    ``EquationAsset.section`` — both ingestion paths (``latex_to_asset`` and
    ``marker_to_asset``) populate this field, so the asset is the canonical
    source. Aligning with how :func:`_read_figure_block` consumes structured
    asset entries (rather than re-scanning chunks) avoids a divergence risk
    between chunk text and asset LaTeX and removes a regex from the hot path.

    ``valid_section_names`` (the most recent ``list_sections`` result, if
    any) is used to disambiguate "unknown section" from "known section with
    no equations" so the LLM gets a clear remediation hint.
    """
    if asset is None or not asset.equations:
        return (
            json.dumps({"error": "this paper has no equation index in its PaperAsset"}),
            0,
        )

    if valid_section_names is not None and section not in valid_section_names:
        return (
            json.dumps({
                "error": f"unknown section: {section!r}. Call list_sections() first.",
            }),
            0,
        )

    matched: list[str] = []
    for eq in asset.equations:
        if eq.section != section:
            continue
        latex = eq.latex.strip()
        if latex and latex not in matched:
            matched.append(latex)

    if not matched:
        return (
            json.dumps({"info": f"no equations indexed in {section!r}"}),
            0,
        )
    return (json.dumps({"equations": matched}, ensure_ascii=False), len(matched))


# ──────────────────────── output parsing ────────────────────────────


def _parse_brief(raw: str) -> PaperTalkBrief:
    """Strip optional fence and validate against PaperTalkBrief."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = _FENCE_RE.sub("", cleaned)
        cleaned = _FENCE_RE.sub("", cleaned).strip()
    return PaperTalkBrief.model_validate_json(cleaned)


# ──────────────────────── main entry point ──────────────────────────


def _paper_block(
    *,
    paper_id: int,
    title: str,
    asset: PaperAsset | None,
    paper_idx: int,
) -> str:
    """Render the user-prompt paper block: id, title, figure inventory."""
    inv_lines: list[str] = []
    if asset is not None:
        for f in asset.figures:
            inv_lines.append(f"- p{paper_idx}-{f.id}: {f.caption}")
    inv = "\n".join(inv_lines) if inv_lines else "(no figures indexed)"
    return (
        f"paper_id={paper_id}\n"
        f"title: {title}\n"
        f"FIGURE INVENTORY:\n{inv}"
    )


async def run_sl_paper_brief(
    *,
    paper_content_id: int,
    paper_idx: int,
    title: str,
    tracer: Tracer,
    model: str,
    conn: aiosqlite.Connection,
    response_language: str = "the user's language",
    max_section_reads: int = MAX_SECTION_READS,
    max_figure_reads: int = MAX_FIGURE_READS,
    max_equation_reads: int = MAX_EQUATION_READS,
    registry: PromptRegistry | None = None,
    **litellm_kwargs: Any,
) -> PaperTalkBrief:
    """Run the per-paper agentic-brief loop.

    Returns a :class:`PaperTalkBrief`. The loop force-stops when every read
    budget is exhausted and the LLM has not emitted a final response yet —
    in that case the loop falls back to a brief synthesised from whatever
    has been read so far (the LLM is told via tool-error messages so it can
    self-correct; if it still does not emit JSON, an empty-but-valid brief
    is returned so the deck pipeline never crashes on this stage).
    """
    reg = registry or PromptRegistry()
    prompt = reg.get("slides_paper_brief/v1")
    system = prompt.system.format(
        max_section_reads=max_section_reads,
        max_figure_reads=max_figure_reads,
        max_equation_reads=max_equation_reads,
    )

    # Resolve the paper's source dir so read_figure_block can hit PaperAsset.
    source_dir = await _resolve_source_dir(
        paper_content_id=paper_content_id, conn=conn,
    )
    asset = read_paper_asset(source_dir) if source_dir is not None else None

    user = prompt.user_template.format(
        paper_block=_paper_block(
            paper_id=paper_content_id,
            title=title,
            asset=asset,
            paper_idx=paper_idx,
        ),
        max_section_reads=max_section_reads,
        response_language=response_language or "the user's language",
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    section_reads: int = 0
    figure_reads: int = 0
    equation_reads: int = 0
    final_text: str = ""
    listed_sections: list[str] | None = None
    sections_read: list[str] = []
    figures_read: list[str] = []
    equations_read_sections: list[str] = []
    chunk_ids_seen: list[int] = []
    tool_call_log: list[dict[str, Any]] = []
    llm_turn_log: list[dict[str, Any]] = []
    parse_error: str | None = None

    async with tracer.step(
        agent="report",
        tool="report:paper_brief",
        model=model,
    ) as step:
        step.record_args({
            "paper_content_id": paper_content_id,
            "paper_idx": paper_idx,
            "title": title,
            "max_section_reads": max_section_reads,
            "max_figure_reads": max_figure_reads,
            "max_equation_reads": max_equation_reads,
        })

        for iteration in range(_MAX_TURNS):
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
                final_text = assistant_content.strip()
                break

            messages.append({
                "role": "assistant",
                "content": msg.get("content"),
                "tool_calls": tool_calls,
            })

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
                    result_str, sec_names = await _list_sections(
                        paper_content_id=paper_content_id, conn=conn,
                    )
                    if listed_sections is None and sec_names:
                        listed_sections = sec_names
                    tool_log_entry["sections_listed"] = sec_names
                elif name == "read_section":
                    section_name = str(raw_args.get("name", ""))
                    if section_reads >= max_section_reads:
                        result_str = json.dumps({
                            "error": (
                                f"read_section budget exhausted "
                                f"({max_section_reads}). Stop calling read_section "
                                "and emit the final brief JSON now."
                            ),
                        })
                        tool_log_entry["error"] = "budget_exhausted"
                    else:
                        result_str, cids = await _read_section(
                            paper_content_id=paper_content_id,
                            name=section_name,
                            conn=conn,
                        )
                        if cids:  # only count a successful read
                            section_reads += 1
                            sections_read.append(section_name)
                            chunk_ids_seen.extend(cids)
                            tool_log_entry["chunk_ids_returned"] = cids
                elif name == "read_figure_block":
                    figure_key = str(raw_args.get("figure_key", ""))
                    if figure_reads >= max_figure_reads:
                        result_str = json.dumps({
                            "error": (
                                f"read_figure_block budget exhausted "
                                f"({max_figure_reads}). Stop calling "
                                "read_figure_block and emit the final brief."
                            ),
                        })
                        tool_log_entry["error"] = "budget_exhausted"
                    else:
                        result_str = await _read_figure_block(
                            paper_content_id=paper_content_id,
                            figure_key=figure_key,
                            asset=asset,
                            paper_idx=paper_idx,
                            conn=conn,
                        )
                        # Count even an "unknown key" attempt — burning the
                        # budget on bad lookups is on the LLM, not the user.
                        figure_reads += 1
                        figures_read.append(figure_key)
                        tool_log_entry["figure_key"] = figure_key
                elif name == "read_equations":
                    section_name = str(raw_args.get("section", ""))
                    if equation_reads >= max_equation_reads:
                        result_str = json.dumps({
                            "error": (
                                f"read_equations budget exhausted "
                                f"({max_equation_reads}). Stop calling "
                                "read_equations and emit the final brief."
                            ),
                        })
                        tool_log_entry["error"] = "budget_exhausted"
                    else:
                        result_str, eq_count = await _read_equations(
                            section=section_name,
                            asset=asset,
                            valid_section_names=listed_sections,
                        )
                        # A successful section lookup (even one with zero
                        # equations) burns the budget — it's still a
                        # deliberate read.
                        equation_reads += 1
                        equations_read_sections.append(section_name)
                        tool_log_entry["equations_returned"] = eq_count
                else:
                    result_str = json.dumps({
                        "error": (
                            f"unknown tool {name!r}. Use list_sections, "
                            "read_section, read_figure_block, or read_equations."
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

        # Parse the final JSON, with a defensive fallback if the LLM never
        # produced a clean schema-conformant response. The fallback brief is
        # still returned (so the deck pipeline degrades gracefully) but the
        # tracer step is flipped to status='error' via mark_error — per the
        # agent-flow observability iron rule, a silent fallback emitting
        # structurally-valid garbage downstream is precisely the failure
        # mode we refuse to swallow.
        brief: PaperTalkBrief
        if final_text:
            try:
                brief = _parse_brief(final_text)
                # Override paper_id with the canonical value (the LLM is told
                # the id in the prompt but a wrong echo would confuse T2).
                brief = brief.model_copy(update={"paper_id": paper_content_id})
            except (ValidationError, ValueError) as exc:
                parse_error = f"{type(exc).__name__}: {exc}"
                brief = _empty_brief(paper_content_id)
                step.mark_error("brief_parse_failed")
        else:
            parse_error = "no final no-tool-calls response from LLM"
            brief = _empty_brief(paper_content_id)
            step.mark_error("brief_parse_failed")

        step.record_result({
            "paper_id": brief.paper_id,
            "contribution": brief.contribution,
            "method_core": brief.method_core,
            "key_results": [kr.model_dump() for kr in brief.key_results],
            "key_figures": [kf.model_dump() for kf in brief.key_figures],
            "key_equations": [ke.model_dump() for ke in brief.key_equations],
            "paper_newcommands": brief.paper_newcommands,
            "talk_shape_hint": brief.talk_shape_hint,
            "section_reads_used": section_reads,
            "figure_reads_used": figure_reads,
            "equation_reads_used": equation_reads,
            "listed_sections": listed_sections,
            "sections_read": sections_read,
            "figures_read": figures_read,
            "equations_read_sections": equations_read_sections,
            "chunk_ids_seen": chunk_ids_seen,
            "final_text_len": len(final_text),
            "final_text": final_text,
            "parse_error": parse_error,
            "llm_turns": llm_turn_log,
            "tool_call_log": tool_call_log,
        })

    return brief


def _empty_brief(paper_id: int) -> PaperTalkBrief:
    """Defensive fallback brief — schema-valid but explicitly empty.

    Used when the LLM never returns a final no-tool-calls JSON or returns
    text that doesn't validate. The planner (T2) is responsible for handling
    a content-poor brief gracefully (e.g. allocating fewer slides). The
    parse error is recorded in the tracer step for diagnosis.
    """
    return PaperTalkBrief(
        paper_id=paper_id,
        contribution="",
        method_core="",
        key_results=[],
        key_figures=[],
        key_equations=[],
        paper_newcommands="",
        talk_shape_hint="concept_only",
    )
