"""Per-slide Beamer-frame renderer for the F4.4 slide pipeline (T3).

Consumes ONE :class:`PlannedSlide` + (for per-paper patterns) the relevant
:class:`PaperTalkBrief` and emits a single
``\\begin{frame}...\\end{frame}`` block wrapped in a
:class:`RenderedSlide`. SINGLE LLM call per slide, but tool-using: a
bounded callback budget lets the renderer fetch one or two
``read_section`` / ``read_figure_block`` reads when the brief's
pre-extracted summary is insufficient. Cross-paper patterns
(``paper_id is None``) have NO callback tools wired — the LLM gets a
direct render call.

Hard contracts (closes T5/T6's assemble + verify burden):

- The LLM's final no-tool-calls response is parsed via
  ``RenderedSlide.model_validate_json``. On failure the tracer step is
  flipped to ``status='error'`` with the canonical marker
  ``"render_parse_failed"`` and the ``ValidationError`` re-raised. Per
  the agent-flow observability iron rule a silent fallback emitting
  structurally-valid garbage downstream is exactly the failure mode we
  refuse to swallow.
- After parsing, deterministic sanity validation runs (``"render_validation_failed"``):
  exactly one ``\\begin{frame}`` / ``\\end{frame}``; every
  ``\\includegraphics`` key extracted from ``frame_tex`` is mirrored in
  ``figure_keys_used``; ``math_stack`` MUST contain at least one
  ``\\[...\\]`` display-math block; ``title`` MUST contain ``[plain]``
  and ``\\titlepage``; ``takeaway_closer`` MUST NOT contain
  ``\\frametitle``.

T3 ships the node + tests only; the subgraph wiring lands in T5.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import aiosqlite
import litellm
from pydantic import ValidationError

from paperhub.agents.sl_paper_brief import (
    _read_figure_block,
    _read_section,
    _resolve_source_dir,
)
from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.models.domain import (
    DeckOutline,
    PaperTalkBrief,
    PlannedSlide,
    RenderedSlide,
)
from paperhub.pipelines.paper_asset import PaperAsset, read_paper_asset
from paperhub.tracing.tracer import Tracer

__all__ = [
    "MAX_CALLBACK_CALLS",
    "PATTERN_TEMPLATES",
    "run_sl_render_slide",
]


# Bounded callback budget — sum of read_section + read_figure_block per slide.
MAX_CALLBACK_CALLS: int = 2

# Hard iteration cap on the agentic loop — callback budget + a margin for the
# final no-tool-calls turn. (Cross-paper patterns get _MAX_TURNS=2 since they
# have no tools — but the loop still uses this cap defensively.)
_MAX_TURNS: int = MAX_CALLBACK_CALLS + 3

# Strip a wrapping markdown code fence (```json ... ```) so a fenced JSON
# response still validates. Tolerates an optional language tag.
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n?|\n?```$")

# Regex to extract \includegraphics keys (bare stem, optionally with options).
# Examples matched: \includegraphics{p0-fig-001}, \includegraphics[width=...]{key}.
_INCLUDEGRAPHICS_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")

# Regex matching one \begin{frame}...\end{frame} environment (greedy is fine —
# we will count occurrences, not extract a specific instance).
_FRAME_BEGIN_RE = re.compile(r"\\begin\{frame\}")
_FRAME_END_RE = re.compile(r"\\end\{frame\}")

# Display-math detector for the math_stack sanity check. Matches \[...\]
# OR \begin{equation}...\end{equation} / \begin{align}...\end{align}, which
# are the project's accepted display-math forms on slides.
_DISPLAY_MATH_RE = re.compile(
    r"\\\[.*?\\\]"
    r"|\\begin\{equation\*?\}.*?\\end\{equation\*?\}"
    r"|\\begin\{align\*?\}.*?\\end\{align\*?\}",
    re.DOTALL,
)


# ────────────────────────── pattern templates ───────────────────────
# These templates are SHOWN to the LLM via the prompt user message. They are
# hand-authored design references (no comparison-target text leaked) encoding
# the layout discipline T2 plans against.
PATTERN_TEMPLATES: dict[str, str] = {
    "title": (
        r"""\begin{frame}[plain]
  \titlepage
\end{frame}"""
    ),
    "references": (
        r"""\begin{frame}{<frametitle>}
  \begin{columns}[T]
    % One column per paper (3 expected). Each column an [s]-stretch minipage
    % so heights match. Replace <N>/<short_name>/<title>/<authors>/<venue>/<url>
    % from ALL BRIEFS. Skip <venue> line if missing.
    \begin{column}{0.32\textwidth}
      \begin{minipage}[t][0.78\textheight][s]{\linewidth}
        \textbf{[<N>] <short_name>}\\[0.25em]
        {\scriptsize\itshape <title>}\\[0.25em]
        {\tiny <authors>}\\[0.25em]
        \textcolor{accent}{\textbf{<venue>}}
        \vfill
        {\tiny\ttfamily <url>}
      \end{minipage}
    \end{column}
    % ... repeat for each paper
  \end{columns}
\end{frame}"""
    ),
    "motivation_figure": (
        r"""\begin{frame}{<frametitle>}
  \centering
  \includegraphics[width=0.65\linewidth,height=0.6\textheight,keepaspectratio]{<figure_key>}\\
  \textbf{<one-sentence motivation under the figure>}
\end{frame}"""
    ),
    "bottlenecks_table": (
        r"""\begin{frame}{<frametitle>}
  \begin{tabular}{l l l}
    \toprule
    Bottleneck & Paper & Speedup / Result \\
    \midrule
    <bottleneck 1> & [<N1>] <short_name_1> & <number_1> \\
    <bottleneck 2> & [<N2>] <short_name_2> & <number_2> \\
    <bottleneck 3> & [<N3>] <short_name_3> & <number_3> \\
    \bottomrule
  \end{tabular}
  \vspace{0.5em}
  \begin{block}{<goal as tagline>}
    <one-sentence framing of the three together>
  \end{block}
\end{frame}"""
    ),
    "concept_2col": (
        r"""\begin{frame}{<frametitle>}
  \begin{columns}[T]
    \begin{column}{0.55\textwidth}
      \includegraphics[width=\linewidth]{<figure_key>}
    \end{column}
    \begin{column}{0.45\textwidth}
      \begin{itemize}
        \item <key_point 1, <=15 words>
        \item <key_point 2, <=15 words>
        \item <key_point 3, optional>
        \item <key_point 4, optional>
      \end{itemize}
      \begin{block}{Result}
        <headline number from brief.key_results, or empty block if none fits>
      \end{block}
    \end{column}
  \end{columns}
\end{frame}"""
    ),
    "math_stack": (
        r"""\begin{frame}{<frametitle>}
  % For EACH equation (max 2): a section-style label followed by a display-math
  % block followed by a one-line italic notation explanation. NO bullets on
  % math slides. Use the assigned equation from PaperTalkBrief.key_equations.
  \textbf{<role label, e.g. "Loss:" or "Update rule:">}
  \[
    <equation_latex verbatim from brief>
  \]
  {\small\itshape notation: <notation_explanation from brief>}
\end{frame}"""
    ),
    "results_table": (
        r"""\begin{frame}{<frametitle>}
  \begin{tabular}{l c c c}
    \toprule
    Metric & Baseline & Ours & Delta \\
    \midrule
    <metric 1> & <baseline> & <ours> & <delta> \\
    <metric 2> & <baseline> & <ours> & <delta> \\
    \bottomrule
  \end{tabular}
\end{frame}"""
    ),
    "proposed_direction_placeholder": (
        r"""\begin{frame}{<frametitle>}
  \textbf{[Proposed direction --- fill with your own contribution here]}\\[0.5em]
  <one-line restatement of the slide goal as the synthesis prompt>\\[0.5em]
  \begin{tikzpicture}
    \node[draw, dashed, minimum width=6cm, minimum height=2cm, align=center]
      {your synthesis here};
  \end{tikzpicture}
\end{frame}"""
    ),
    "plan_numbered": (
        r"""\begin{frame}{<frametitle>}
  \small
  \begin{enumerate}
    \item <step 1>
    \item <step 2>
    \item <step 3>
    \item <step 4>
    % optional steps 5 / 6
  \end{enumerate}
\end{frame}"""
    ),
    "takeaway_closer": (
        r"""\begin{frame}[plain]
  \centering
  \rule{0.6\linewidth}{0.4pt}\\[1em]
  {\Large\bfseries Take-away}\\[0.75em]
  <one-sentence take-away derived from slide goal>\\[1em]
  \rule{0.6\linewidth}{0.4pt}\\[1em]
  \begin{block}{Open Question}
    \itshape <one-sentence open question for the audience>
  \end{block}
  \vspace{1em}
  \textbf{\Large Thank you. Questions?}
\end{frame}"""
    ),
}


# ────────────────────────── tool schemas ────────────────────────────


def _callback_tool_schemas(*, has_paper: bool) -> list[dict[str, Any]]:
    """Return the callback tool palette for the LLM.

    For cross-paper patterns (``has_paper=False``) returns ``[]`` — the
    LLM has no callback tools wired, which both prunes the schema and
    makes "the renderer is unable to call back-into-paper tools when no
    paper is in scope" a structural property rather than a runtime check.
    """
    if not has_paper:
        return []
    return [
        {
            "type": "function",
            "function": {
                "name": "read_section",
                "description": (
                    "Fetch every chunk in the named section of the paper "
                    "this slide attributes to. Counts against the callback "
                    "budget. Use ONLY when the brief's pre-extracted "
                    "summary is insufficient to render the slide accurately."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Exact section name from the brief.",
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
                    "Fetch the caption + surrounding paragraph context for "
                    "ONE figure on this paper, looked up by its inventory "
                    "key (e.g. 'p0-fig-001'). Counts against the callback "
                    "budget."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "figure_key": {
                            "type": "string",
                            "description": "Exact key from the brief's key_figures.",
                        },
                    },
                    "required": ["figure_key"],
                },
            },
        },
    ]


# ────────────────────────── prompt rendering ────────────────────────


def _format_key_points(points: list[str]) -> str:
    if not points:
        return "(no hints — write from goal + brief)"
    return "\n".join(f"- {p}" for p in points)


def _format_sibling_block(outline: DeckOutline, current_index: int) -> str:
    lines: list[str] = []
    for idx, s in enumerate(outline.slides):
        marker = " <-- current" if idx == current_index else ""
        title = s.title or "(no title — pattern-specific framing)"
        lines.append(f"{idx}. [{s.pattern_kind}] {title}{marker}")
    return "\n".join(lines)


def _format_brief_block(brief: PaperTalkBrief | None) -> str:
    if brief is None:
        return "(none — this is a cross-paper pattern)"
    payload = {
        "paper_id": brief.paper_id,
        "contribution": brief.contribution,
        "method_core": brief.method_core,
        "key_results": [kr.model_dump() for kr in brief.key_results],
        "key_figures": [kf.model_dump() for kf in brief.key_figures],
        "key_equations": [
            {
                "index": idx,
                "latex": ke.latex,
                "role": ke.role,
                "notation_explanation": ke.notation_explanation,
            }
            for idx, ke in enumerate(brief.key_equations)
        ],
        "paper_newcommands": brief.paper_newcommands,
        "talk_shape_hint": brief.talk_shape_hint,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _format_all_briefs_block(all_briefs: list[PaperTalkBrief]) -> str:
    if not all_briefs:
        return "(no briefs in scope)"
    rows: list[dict[str, Any]] = []
    for idx, brief in enumerate(all_briefs):
        rows.append(
            {
                "paper_idx": idx,
                "paper_id": brief.paper_id,
                "contribution": brief.contribution,
                "key_figure_keys": [kf.key for kf in brief.key_figures],
                "key_result_headlines": [
                    f"{kr.number} on {kr.benchmark}: {kr.description}"
                    for kr in brief.key_results
                ],
            }
        )
    return json.dumps(rows, ensure_ascii=False, indent=2)


def _format_assigned_equation_block(
    planned: PlannedSlide, brief: PaperTalkBrief | None
) -> str:
    if planned.equation_index is None or brief is None:
        return "(none — this slide carries no assigned equation)"
    n = len(brief.key_equations)
    if not (0 <= planned.equation_index < n):
        # T2 validates this, so reaching here would be a planner bug — surface
        # it to the LLM so it does not silently emit a wrong equation.
        return (
            f"(error: equation_index={planned.equation_index} is out of range "
            f"for brief.key_equations [0..{n - 1}])"
        )
    eq = brief.key_equations[planned.equation_index]
    return json.dumps(
        {
            "latex": eq.latex,
            "role": eq.role,
            "notation_explanation": eq.notation_explanation,
        },
        ensure_ascii=False,
        indent=2,
    )


# ────────────────────────── output parsing + validation ─────────────


def _parse_rendered_slide(raw: str) -> RenderedSlide:
    """Strip optional fence and validate against RenderedSlide."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        # The regex alternation matches both the opening fence (`^```\w*\n?`)
        # and the closing fence (`\n?```$`), so one .sub() pass strips both.
        cleaned = _FENCE_RE.sub("", cleaned).strip()
    return RenderedSlide.model_validate_json(cleaned)


def _validate_rendered_slide(rendered: RenderedSlide) -> None:
    """Deterministic post-parse checks. Raises ValueError on violation.

    Closes T5's assemble burden — after this pass the frame may be
    concatenated as-is with no per-pattern re-inspection.
    """
    tex = rendered.frame_tex

    begin_count = len(_FRAME_BEGIN_RE.findall(tex))
    end_count = len(_FRAME_END_RE.findall(tex))
    if begin_count != 1 or end_count != 1:
        raise ValueError(
            f"render emitted {begin_count} \\begin{{frame}} and "
            f"{end_count} \\end{{frame}} envs, expected exactly 1 of each "
            f"(slide_index={rendered.slide_index}, "
            f"pattern_kind={rendered.pattern_kind!r})"
        )

    stripped = tex.strip()
    if not stripped.startswith("\\begin{frame}"):
        raise ValueError(
            f"render frame_tex must start with \\begin{{frame}}; got "
            f"{stripped[:60]!r} (slide_index={rendered.slide_index})"
        )
    if not stripped.endswith("\\end{frame}"):
        raise ValueError(
            f"render frame_tex must end with \\end{{frame}}; got "
            f"{stripped[-60:]!r} (slide_index={rendered.slide_index})"
        )

    # Every \includegraphics key must appear in figure_keys_used. Wrong-direction
    # check (figure_keys_used can list keys not in the tex, but every tex key
    # MUST be tracked) would let the verify-figures step miss un-recorded
    # citations; bidirectional consistency closes that gap.
    tex_keys = set(_INCLUDEGRAPHICS_RE.findall(tex))
    tracked_keys = set(rendered.figure_keys_used)
    missing = sorted(tex_keys - tracked_keys)
    if missing:
        raise ValueError(
            f"render cited figure_keys not tracked in figure_keys_used: "
            f"{missing} (slide_index={rendered.slide_index}); the renderer "
            "must record every \\includegraphics key it emits so "
            "sl_verify_figures can audit it."
        )

    if rendered.pattern_kind == "math_stack" and not _DISPLAY_MATH_RE.search(tex):
        raise ValueError(
            f"render emitted a math_stack frame with no \\[...\\] / "
            f"\\begin{{equation}} block (slide_index={rendered.slide_index}); "
            "math_stack must carry at least one display-math equation."
        )

    if rendered.pattern_kind == "title":
        if "[plain]" not in tex:
            raise ValueError(
                f"render emitted a title frame without [plain] "
                f"(slide_index={rendered.slide_index}); title pages must "
                "use \\begin{frame}[plain] to suppress the frame chrome."
            )
        if "\\titlepage" not in tex:
            raise ValueError(
                f"render emitted a title frame without \\titlepage "
                f"(slide_index={rendered.slide_index}); the title page "
                "must render the deck's \\titlepage block."
            )

    if rendered.pattern_kind == "takeaway_closer" and "\\frametitle" in tex:
        raise ValueError(
            f"render emitted a takeaway_closer frame with \\frametitle "
            f"(slide_index={rendered.slide_index}); the closer uses "
            "[plain] + \\rule framing and MUST NOT carry a \\frametitle."
        )


# ────────────────────────── main entry point ────────────────────────


async def run_sl_render_slide(
    *,
    planned_slide: PlannedSlide,
    deck_outline: DeckOutline,
    paper_brief: PaperTalkBrief | None,
    all_briefs: list[PaperTalkBrief],
    tracer: Tracer,
    model: str,
    response_language: str = "the user's language",
    memory_context: str = "",
    paper_asset: PaperAsset | None = None,
    conn: aiosqlite.Connection | None = None,
    max_callback_calls: int = MAX_CALLBACK_CALLS,
    registry: PromptRegistry | None = None,
    **litellm_kwargs: Any,
) -> RenderedSlide:
    """Render ONE PlannedSlide into a RenderedSlide (single LLM call + bounded callbacks).

    Cross-paper patterns (``planned_slide.paper_id is None``) bypass the
    callback wiring — the LLM gets a direct render call with no tools in
    scope.

    ``planned_slide`` MUST be one of the slides in ``deck_outline.slides``
    (identity-match preferred; equality on ``(pattern_kind, title, goal)``
    is the soft-match fallback for callers that passed a copy). If neither
    locates it, ``ValueError`` is raised — silently defaulting the
    ``slide_index`` to 0 would mis-attribute the rendered frame in the
    trace + downstream emit. This is a programmer bug (callers must pass a
    slide that exists in the outline), not a runtime condition.
    """
    reg = registry or PromptRegistry()
    prompt = reg.get("slides_render_slide/v1")

    has_paper = planned_slide.paper_id is not None

    # Locate index of this slide within the outline (the renderer's
    # ``slide_index`` is its 0-based position in DeckOutline.slides).
    try:
        slide_index = next(
            i for i, s in enumerate(deck_outline.slides) if s is planned_slide
        )
    except StopIteration:
        # Fallback: equality-by-attributes when the caller passed a copy.
        soft_match = next(
            (
                i
                for i, s in enumerate(deck_outline.slides)
                if s.pattern_kind == planned_slide.pattern_kind
                and s.title == planned_slide.title
                and s.goal == planned_slide.goal
            ),
            None,
        )
        if soft_match is None:
            raise ValueError(
                f"sl_render_slide could not resolve slide_index for "
                f"planned_slide pattern_kind={planned_slide.pattern_kind!r} "
                f"title={planned_slide.title!r}; not present in "
                "deck_outline.slides by identity or "
                "(pattern_kind, title, goal) match. This is a programmer "
                "bug — callers must pass a slide that exists in the outline."
            ) from None
        slide_index = soft_match

    pattern_template = PATTERN_TEMPLATES.get(
        planned_slide.pattern_kind,
        "(no template defined for this pattern_kind)",
    )

    system = prompt.system.format(
        max_callback_calls=max_callback_calls,
        response_language=response_language or "the user's language",
    )
    user = prompt.user_template.format(
        slide_index=slide_index,
        pattern_kind=planned_slide.pattern_kind,
        slide_title=planned_slide.title or "(none — pattern uses alternative framing)",
        slide_goal=planned_slide.goal,
        paper_id=planned_slide.paper_id,
        figure_key=planned_slide.figure_key,
        equation_index=planned_slide.equation_index,
        key_points_block=_format_key_points(planned_slide.key_points),
        talk_title=deck_outline.talk_title,
        sibling_block=_format_sibling_block(deck_outline, slide_index),
        brief_block=_format_brief_block(paper_brief),
        all_briefs_block=_format_all_briefs_block(all_briefs),
        pattern_template=pattern_template,
        assigned_equation_block=_format_assigned_equation_block(
            planned_slide, paper_brief
        ),
        response_language=response_language or "the user's language",
        memory_context=memory_context,
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    tools_schema = _callback_tool_schemas(has_paper=has_paper)
    # When a per-paper pattern has no paper_asset/conn (test-mode without real
    # ingest), the callback tools still surface; the helpers below return JSON
    # error markers when asset/conn are missing so the LLM can choose to
    # proceed without callback.
    asset = paper_asset
    if (
        asset is None
        and has_paper
        and conn is not None
        and planned_slide.paper_id is not None
    ):
        # Resolve from DB if caller didn't pre-load the asset.
        source_dir: Path | None = await _resolve_source_dir(
            paper_content_id=planned_slide.paper_id, conn=conn,
        )
        if source_dir is not None:
            asset = read_paper_asset(source_dir)

    # The figure-key prefix the brief uses (matches T1's _paper_block scheme).
    paper_idx_in_briefs = next(
        (
            i
            for i, b in enumerate(all_briefs)
            if planned_slide.paper_id is not None
            and b.paper_id == planned_slide.paper_id
        ),
        0,
    )

    callback_reads_used: int = 0
    callback_log: list[dict[str, str]] = []
    final_text: str = ""
    llm_turn_log: list[dict[str, Any]] = []
    parse_error: str | None = None
    rendered: RenderedSlide | None = None
    pending_exc: Exception | None = None

    async with tracer.step(
        agent="report",
        tool="report:render_slide",
        model=model,
    ) as step:
        step.record_args(
            {
                "slide_index": slide_index,
                "pattern_kind": planned_slide.pattern_kind,
                "paper_id": planned_slide.paper_id,
                "figure_key": planned_slide.figure_key,
                "equation_index": planned_slide.equation_index,
                "key_points_len": len(planned_slide.key_points),
                "callback_budget": max_callback_calls,
                "has_callback_tools": has_paper,
            }
        )

        for iteration in range(_MAX_TURNS):
            acompletion_kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                **litellm_kwargs,
            }
            if tools_schema:
                acompletion_kwargs["tools"] = tools_schema
                acompletion_kwargs["tool_choice"] = "auto"

            response = await litellm.acompletion(**acompletion_kwargs)
            msg = response["choices"][0]["message"]
            tool_calls = msg.get("tool_calls") or []
            assistant_content = str(msg.get("content") or "")

            llm_turn_log.append(
                {
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
                }
            )

            if not tool_calls:
                final_text = assistant_content.strip()
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
                    raw_args = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    raw_args = {}

                if not has_paper:
                    # The LLM somehow synthesised a callback name even
                    # though no tool schema was wired (off-palette / OpenAI
                    # SDK quirk). Surface a deterministic error so the
                    # next turn can recover by emitting the final JSON.
                    result_str = json.dumps(
                        {
                            "error": (
                                f"tool {name!r} is not available on "
                                "cross-paper slides (no paper_id in scope)."
                            ),
                        }
                    )
                elif callback_reads_used >= max_callback_calls:
                    result_str = json.dumps(
                        {
                            "error": (
                                f"callback budget exhausted "
                                f"({max_callback_calls}). Stop calling "
                                "read_section / read_figure_block and "
                                "emit the final RenderedSlide JSON now."
                            ),
                        }
                    )
                elif name == "read_section" and conn is not None:
                    section_name = str(raw_args.get("name", ""))
                    assert planned_slide.paper_id is not None
                    result_str, _cids = await _read_section(
                        paper_content_id=planned_slide.paper_id,
                        name=section_name,
                        conn=conn,
                    )
                    callback_reads_used += 1
                    callback_log.append(
                        {
                            "tool": "read_section",
                            "args": json.dumps(raw_args, ensure_ascii=False),
                            "result_excerpt": result_str[:200],
                        }
                    )
                elif name == "read_figure_block" and conn is not None:
                    figure_key = str(raw_args.get("figure_key", ""))
                    assert planned_slide.paper_id is not None
                    result_str = await _read_figure_block(
                        paper_content_id=planned_slide.paper_id,
                        figure_key=figure_key,
                        asset=asset,
                        paper_idx=paper_idx_in_briefs,
                        conn=conn,
                    )
                    callback_reads_used += 1
                    callback_log.append(
                        {
                            "tool": "read_figure_block",
                            "args": json.dumps(raw_args, ensure_ascii=False),
                            "result_excerpt": result_str[:200],
                        }
                    )
                elif name in {"read_section", "read_figure_block"} and conn is None:
                    # Per-paper pattern with callback tools advertised but
                    # no DB connection wired — emit a clear remediation
                    # marker so the LLM stops calling.
                    result_str = json.dumps(
                        {
                            "error": (
                                f"tool {name!r} requires a DB connection "
                                "(none wired in this call); emit the "
                                "final RenderedSlide JSON from the brief."
                            ),
                        }
                    )
                else:
                    result_str = json.dumps(
                        {
                            "error": (
                                f"unknown tool {name!r}. Use "
                                "read_section or read_figure_block."
                            ),
                        }
                    )

                # For free reads (list_sections style) we'd also count
                # nothing, but T3 deliberately omits list_sections — the
                # brief already carried the TOC implicitly via
                # key_figures + key_equations.
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "name": name,
                        "content": result_str,
                    }
                )

        # Parse the final JSON (no silent fallback — per the iron rule).
        if final_text:
            try:
                rendered = _parse_rendered_slide(final_text)
                # Echo-correctness: force the canonical values for
                # slide_index/pattern_kind/paper_id so a sloppy LLM echo
                # cannot break downstream attribution. (Wrong echo would
                # be a planner inconsistency, not a render bug; we still
                # validate the LATEX content the LLM produced.)
                rendered = rendered.model_copy(
                    update={
                        "slide_index": slide_index,
                        "pattern_kind": planned_slide.pattern_kind,
                        "paper_id": planned_slide.paper_id,
                        "callback_reads": list(callback_log),
                    }
                )
            except (ValidationError, ValueError) as exc:
                parse_error = f"{type(exc).__name__}: {exc}"
                step.record_result(
                    {
                        "final_text": final_text,
                        "final_text_len": len(final_text),
                        "parse_error": parse_error,
                        "callback_reads_count": callback_reads_used,
                        "callback_reads_summary": callback_log,
                        "llm_turns": llm_turn_log,
                    }
                )
                step.mark_error("render_parse_failed")
                pending_exc = exc
        else:
            parse_error = "no final no-tool-calls response from LLM"
            step.record_result(
                {
                    "final_text": final_text,
                    "final_text_len": 0,
                    "parse_error": parse_error,
                    "callback_reads_count": callback_reads_used,
                    "callback_reads_summary": callback_log,
                    "llm_turns": llm_turn_log,
                }
            )
            step.mark_error("render_parse_failed")
            pending_exc = RuntimeError(parse_error)

        if rendered is not None:
            try:
                _validate_rendered_slide(rendered)
            except ValueError as exc:
                parse_error = f"{type(exc).__name__}: {exc}"
                step.record_result(
                    {
                        "final_text": final_text,
                        "final_text_len": len(final_text),
                        "frame_tex_first_200_chars": rendered.frame_tex[:200],
                        "figure_keys_used": rendered.figure_keys_used,
                        "validation_failed": True,
                        "validation_error": parse_error,
                        "callback_reads_count": callback_reads_used,
                        "callback_reads_summary": callback_log,
                        "llm_turns": llm_turn_log,
                    }
                )
                step.mark_error("render_validation_failed")
                pending_exc = exc
                rendered = None

        if rendered is not None:
            step.record_result(
                {
                    "frame_tex_first_200_chars": rendered.frame_tex[:200],
                    "figure_keys_used": rendered.figure_keys_used,
                    "callback_reads_count": callback_reads_used,
                    "callback_reads_summary": callback_log,
                    "parse_status": "ok",
                    "llm_turns": llm_turn_log,
                }
            )

    if pending_exc is not None:
        raise pending_exc
    assert rendered is not None  # control-flow invariant
    return rendered
