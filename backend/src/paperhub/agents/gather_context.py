"""F4.5 per-paper subagent (stage 1).

For each paper in the deck's contributing set, this runs ONE small agentic
LLM call (with bounded callbacks to list_sections / read_section /
read_figure_block) and emits ONE :class:`PaperContextBundle`. Fan-out is
correct here because each paper's gather is independent IO + summarisation.

Mirrors the proven bounded-tool-loop pattern from R1's
``sl_paper_brief.run_sl_paper_brief``:

- tool palette (list_sections / read_section / read_figure_block) with a
  shared bounded read budget (``max_callback_calls``);
- one tracer ``report:gather_context`` step around the whole loop,
  recording the per-tool log + the parsed bundle's IDs;
- HARD CONTRACT #1 (no hallucinated figures) enforced at parse time: any
  ``key_figures[*].key`` not in the deck-prefixed inventory raises ``ValueError``
  BEFORE the bundle is returned. The LLM never gets to invent a figure key.

Schema deviation from the original F4.5 plan stub: the real ``PaperAsset``
(F2 ingestion dataclass at ``paperhub.pipelines.paper_asset``) has NO
``source_dir`` / ``metadata`` / ``additional_tex`` keys — figures expose
``image_path`` relative to ``source_dir/asset/`` via ``abs_image_path()``.
We therefore take ``source_dir`` + the paper-row metadata + the parsed
ADDITIONAL.tex lines as explicit kwargs (matching how ``_enabled_papers``
in ``report_graph.py`` already loads them).
"""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import aiosqlite

from paperhub.agents._paper_callbacks import (
    _read_figure_block,
    _read_section,
)
from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.models.slide_domain import (
    FigureDimensions,
    PaperContextBundle,
)
from paperhub.pipelines.paper_asset import PaperAsset
from paperhub.pipelines.slide_pipeline.figure_geometry import probe_figure_dimensions
from paperhub.tracing.tracer import Tracer

__all__ = ["MAX_CALLBACK_CALLS", "run_gather_context"]

LlmAcompletion = Callable[..., Awaitable[Any]]

MAX_CALLBACK_CALLS = 3
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n?|\n?```$")


def _format_figure_inventory_block(
    asset: PaperAsset, *, paper_idx: int, source_dir: Path | None
) -> tuple[str, dict[str, FigureDimensions]]:
    """Render the figure inventory for the prompt AND return the key→dimensions map.

    Figure key scheme: ``p{paper_idx}-{figure_id}``. Matches
    :func:`figure_inventory.build_inventory` so downstream stages stay
    consistent. The probe falls back to a neutral 1000x1000 when the file is
    missing — same posture as ``probe_figure_dimensions`` itself.
    """
    lines: list[str] = []
    dims_map: dict[str, FigureDimensions] = {}
    for fig in asset.figures:
        key = f"p{paper_idx}-{fig.id}"
        if source_dir is not None:
            abs_path = fig.abs_image_path(source_dir)
            dims = (
                probe_figure_dimensions(abs_path)
                if abs_path.exists()
                else FigureDimensions(width_px=1000, height_px=1000)
            )
        else:
            dims = FigureDimensions(width_px=1000, height_px=1000)
        dims_map[key] = dims
        caption = (fig.caption or "").strip()[:200]
        lines.append(
            f"- key={key} width_px={dims.width_px} height_px={dims.height_px} "
            f"aspect={dims.aspect_ratio:.2f} caption={caption!r}"
        )
    if not lines:
        return "(no figures in this paper)", dims_map
    return "\n".join(lines), dims_map


def _format_equations_block(asset: PaperAsset) -> str:
    if not asset.equations:
        return "(no equations extracted)"
    lines = []
    for i, eq in enumerate(asset.equations):
        latex = (eq.latex or "").strip()[:300]
        section = (eq.section or "").strip()[:80]
        lines.append(f"[{i}] section={section!r} latex={latex!r}")
    return "\n".join(lines)


def _format_sections_toc(asset: PaperAsset) -> str:
    if not asset.sections:
        return "(no sections extracted)"
    return "\n".join(f"- {s.name}" for s in asset.sections)


def _format_newcommands(paper_newcommands: list[str]) -> str:
    if not paper_newcommands:
        return "(none)"
    return "\n".join(paper_newcommands[:50])


def _parse_bundle(
    raw: str,
    *,
    paper_id: int,
    paper_idx: int,
    valid_figure_keys: set[str],
    dims_map: dict[str, FigureDimensions],
) -> PaperContextBundle:
    """Validate + override figure dimensions + reject unknown figure keys.

    HARD CONTRACT #1 lives here: a figure key the LLM emitted that isn't in
    the inventory raises ``ValueError`` BEFORE the bundle ever leaves the
    function. Dimensions are force-overridden from the PIL probe so the LLM
    can never silently lie about a figure's size.
    """
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = _FENCE_RE.sub("", cleaned)
        cleaned = _FENCE_RE.sub("", cleaned).strip()
    raw_obj = json.loads(cleaned)

    for kf in raw_obj.get("key_figures", []):
        key = kf.get("key", "")
        if key not in valid_figure_keys:
            raise ValueError(
                f"unknown figure key {key!r} (not in inventory; "
                f"valid keys: {sorted(valid_figure_keys)})"
            )
        dims = dims_map.get(key)
        if dims is not None:
            kf["dimensions"] = {
                "width_px": dims.width_px,
                "height_px": dims.height_px,
            }

    # Force-override the paper_id / paper_idx echo — the LLM is told the
    # values in the prompt but a wrong echo would confuse downstream stages.
    raw_obj["paper_id"] = paper_id
    raw_obj["paper_idx"] = paper_idx

    return PaperContextBundle.model_validate(raw_obj)


def _callback_tools(*, has_conn: bool) -> list[dict[str, Any]]:
    """Return the tool palette for the LLM. Empty when no DB is wired (the
    LLM is then told to emit the bundle from the prompt material alone)."""
    if not has_conn:
        return []
    return [
        {
            "type": "function",
            "function": {
                "name": "list_sections",
                "description": "Return the section TOC of this paper.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_section",
                "description": (
                    "Fetch one section's chunks. Counts against the "
                    "callback budget."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_figure_block",
                "description": (
                    "Fetch one figure's caption + surrounding context. "
                    "Counts against the callback budget."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"figure_key": {"type": "string"}},
                    "required": ["figure_key"],
                },
            },
        },
    ]


async def run_gather_context(
    *,
    paper_id: int,
    paper_idx: int,
    asset: PaperAsset,
    source_dir: Path | None,
    paper_title: str,
    paper_authors: list[str],
    paper_year: int | None,
    paper_abstract: str | None,
    paper_newcommands: list[str],
    conn: aiosqlite.Connection | None,
    tracer: Tracer,
    model: str,
    response_language: str = "the user's language",
    registry: PromptRegistry | None = None,
    llm_acompletion: LlmAcompletion | None = None,
    max_callback_calls: int = MAX_CALLBACK_CALLS,
) -> PaperContextBundle:
    """Run the gather-context subagent for ONE paper and return ONE bundle.

    Args:
        paper_id: ``paper_content.id`` (canonical paper row).
        paper_idx: 0-based position in the deck's contributing-papers list.
            Used to namespace figure keys (``p{paper_idx}-{figure_id}``).
        asset: F2 ingested :class:`PaperAsset` (figures + equations + sections).
        source_dir: paper's ``paper_content.source_dir_path`` — needed to
            resolve ``FigureAsset.abs_image_path`` for PIL probing. ``None``
            falls back to the neutral 1000x1000 default per probe.
        paper_title/_authors/_year/_abstract: paper-row metadata (the asset
            dataclass doesn't carry these — they come from ``paper_content``).
        paper_newcommands: raw ``\\newcommand`` lines pre-parsed from
            ``ADDITIONAL.tex`` (or empty for non-LaTeX-source papers).
        conn: if ``None``, the LLM is given NO callback tools and must emit
            the bundle from the prompt material alone (this is the
            test/smoke posture).
        tracer: open Tracer bound to the run.
        model: litellm model id.
        response_language: passed through for any narrative-language steering
            the prompt may add later — currently unused by the schema (the
            bundle is structured fields, not free prose).
        registry: optional prompt registry (defaults to a fresh one).
        llm_acompletion: optional injection for tests; defaults to
            ``litellm.acompletion``.
        max_callback_calls: shared budget across read_section /
            read_figure_block calls. ``list_sections`` is free.

    Raises:
        ValueError: a ``key_figures[*].key`` was not in the inventory
            (HARD CONTRACT #1).
        RuntimeError: the LLM never emitted a no-tool-calls response.
    """
    reg = registry or PromptRegistry()
    prompt = reg.get("slides_gather_context/v1")

    figure_inventory_block, dims_map = _format_figure_inventory_block(
        asset, paper_idx=paper_idx, source_dir=source_dir
    )
    valid_keys = set(dims_map.keys())

    user = prompt.user_template.format(
        paper_id=paper_id,
        paper_idx=paper_idx,
        paper_title=paper_title,
        paper_authors_json=json.dumps(paper_authors, ensure_ascii=False),
        paper_year="null" if paper_year is None else paper_year,
        figure_inventory_block=figure_inventory_block,
        equations_block=_format_equations_block(asset),
        sections_toc_block=_format_sections_toc(asset),
        abstract_block=(paper_abstract or "")[:1200],
        paper_newcommands_block=_format_newcommands(paper_newcommands),
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": prompt.system},
        {"role": "user", "content": user},
    ]

    tools_schema = _callback_tools(has_conn=conn is not None)

    callback_log: list[dict[str, str]] = []
    callback_reads_used = 0

    if llm_acompletion is None:
        import litellm

        llm_acompletion = litellm.acompletion

    async with tracer.step(
        agent="report", tool="report:gather_context", model=model
    ) as step:
        step.record_args(
            {
                "paper_id": paper_id,
                "paper_idx": paper_idx,
                "n_figures_in_inventory": len(valid_keys),
                "callback_budget": max_callback_calls,
            }
        )
        final_text = ""
        for _turn in range(max_callback_calls + 3):
            kwargs: dict[str, Any] = {"model": model, "messages": messages}
            if tools_schema:
                kwargs["tools"] = tools_schema
                kwargs["tool_choice"] = "auto"
            response = await llm_acompletion(**kwargs)
            msg = response["choices"][0]["message"]
            tool_calls = msg.get("tool_calls") or []
            content = str(msg.get("content") or "")
            if not tool_calls:
                final_text = content.strip()
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.get("content"),
                    "tool_calls": tool_calls,
                }
            )
            for call in tool_calls:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                if callback_reads_used >= max_callback_calls:
                    result_str = json.dumps(
                        {
                            "error": (
                                "callback budget exhausted; "
                                "emit final JSON now"
                            )
                        }
                    )
                elif name == "list_sections":
                    result_str = json.dumps([s.name for s in asset.sections])
                    callback_reads_used += 1
                elif name == "read_section" and conn is not None:
                    result_str, _ = await _read_section(
                        paper_content_id=paper_id,
                        name=str(args.get("name", "")),
                        conn=conn,
                    )
                    callback_reads_used += 1
                elif name == "read_figure_block" and conn is not None:
                    result_str = await _read_figure_block(
                        paper_content_id=paper_id,
                        figure_key=str(args.get("figure_key", "")),
                        asset=asset,
                        paper_idx=paper_idx,
                        conn=conn,
                    )
                    callback_reads_used += 1
                else:
                    result_str = json.dumps(
                        {"error": f"tool {name!r} not available here"}
                    )
                callback_log.append(
                    {
                        "tool": name,
                        "args": json.dumps(args),
                        "result_excerpt": result_str[:200],
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "name": name,
                        "content": result_str,
                    }
                )

        if not final_text:
            step.mark_error("gather_context_no_final_response")
            raise RuntimeError(
                "gather_context: LLM never emitted a no-tool-calls response"
            )

        try:
            bundle = _parse_bundle(
                final_text,
                paper_id=paper_id,
                paper_idx=paper_idx,
                valid_figure_keys=valid_keys,
                dims_map=dims_map,
            )
        except Exception as exc:
            step.record_result(
                {
                    "final_text": final_text,
                    "callback_reads": callback_log,
                    "parse_error": f"{type(exc).__name__}: {exc}",
                }
            )
            step.mark_error("gather_context_parse_failed")
            raise

        step.record_result(
            {
                "paper_id": paper_id,
                "n_key_figures": len(bundle.key_figures),
                "n_key_equations": len(bundle.key_equations),
                "n_section_excerpts": len(bundle.section_excerpts),
                "callback_reads": callback_log,
                "final_text_len": len(final_text),
            }
        )
        return bundle
