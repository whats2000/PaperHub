"""F4.5 geometry-grounded overflow detector.

For each \\begin{frame}…\\end{frame} in the deck:
  1. Classify layout (text_only / figure_left_half / figure_top_full / …) by
     parsing column structure + \\includegraphics + figure aspect.
  2. Compute text_budget_tokens from the layout's text_region_cm geometry
     and the script (en/cjk).
  3. Count body tokens (strip LaTeX, count words).
  4. Compute figure_footprint_cm2 via figure_geometry.resolve_includegraphics_geometry.
  5. Cross-reference with pdflatex's Overfull \\vbox messages (best-effort).
  6. Emit a FrameOverflowSignal with recommendation + split_hint.
"""
from __future__ import annotations

import re
from typing import Literal

from paperhub.agents._canvas_budget import (
    CanvasBudget,
    CanvasConstants,
    CanvasLayout,
    aspect_matches,
    compute_token_budget,
)
from paperhub.models.slide_domain import (
    FrameOverflowSignal,
    KeyFigureBundle,
    OverflowRecommendation,
    SplitHint,
)
from paperhub.pipelines.slide_pipeline.figure_geometry import (
    LINEWIDTH_CM_DEFAULT,
    TEXTHEIGHT_CM_DEFAULT,
    parse_includegraphics_options,
    resolve_includegraphics_geometry,
)

Script = Literal["en", "cjk"]

_FRAME_RE = re.compile(r"\\begin\{frame\}(?:\[[^\]]*\])?(?:\{([^}]*)\})?(.*?)\\end\{frame\}", re.DOTALL)
_INCLUDEGRAPHICS_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{[^}]+\}")
_OVERFULL_RE = re.compile(r"Overfull\s+\\vbox\s+\(([\d.]+)pt\s+too\s+high\)")
_COLUMNS_RE = re.compile(r"\\begin\{columns\}")
_TABULAR_RE = re.compile(r"\\begin\{tabular\}")
_MATH_RE = re.compile(r"\\\[|\\begin\{equation\}|\\begin\{align\}|\$[^$]+\$")
_COLUMN_WIDTH_RE = re.compile(r"\\begin\{column\}\{([^}]+)\}")

# Strip these so they don't pollute body word count. Order matters — the more
# specific brace-eating patterns must run BEFORE the bare-command sweep, and
# the bare-command sweep must NOT gobble trailing prose (so it has no `[^}]*`
# tail). For commands like \textbf{foo}, the bare-command sweep peels the
# command, then the next pass strips the leftover braces; the content `foo`
# survives and is counted.
_STRIP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\\begin\{frame\}(?:\[[^\]]*\])?\{[^}]*\}"),
    re.compile(r"\\end\{frame\}"),
    re.compile(r"\\begin\{[a-zA-Z*]+\}(?:\[[^\]]*\])?(?:\{[^}]*\})?"),
    re.compile(r"\\end\{[a-zA-Z*]+\}"),
    re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{[^}]+\}"),
    re.compile(r"\\label\{[^}]+\}"),
    re.compile(r"\\caption\{[^}]*\}"),
    # Beamer/preamble setters that take a single braced argument we want to drop.
    re.compile(r"\\(?:setlength|usefont|definecolor|setbeamertemplate|setbeamercolor|setbeamerfont|setbeamersize|usepackage|color|vspace|hspace)\s*\{[^}]*\}"),
    re.compile(r"\\[a-zA-Z]+\*?"),  # bare commands (no argument-gobble)
    re.compile(r"\{|\}|\[|\]"),     # leftover braces/brackets
    re.compile(r"%[^\n]*"),          # comments
)


def count_body_tokens(frame_tex: str) -> int:
    """Strip LaTeX from a frame body and count remaining whitespace-separated words.

    NOTE: this is a raw word count, useful for diagnostics. The budget-comparison
    path in ``detect_overflow`` uses :func:`_count_visual_tokens` instead, which
    accounts for Beamer's actual line-wrap behavior (each ``\\item`` consumes a
    whole visual line even when short; a long item wraps to multiple lines).
    """
    body = frame_tex
    # Remove the title arg from \begin{frame}{Title} so it doesn't count as body.
    body = re.sub(r"\\begin\{frame\}(?:\[[^\]]*\])?\{[^}]*\}", "", body)
    body = re.sub(r"\\end\{frame\}", "", body)
    for pat in _STRIP_PATTERNS:
        body = pat.sub(" ", body)
    return len([w for w in body.split() if w])


def _count_visual_tokens(
    frame_tex: str,
    *,
    layout: CanvasLayout,
    constants: CanvasConstants,
    script: Script,
) -> int:
    """Count tokens with bullet-aware visual-line accounting.

    Each \\item starts a new visual line; a long item that wraps consumes
    multiple lines, so its effective token cost is rounded UP to the nearest
    full line. Non-bullet prose is counted as raw words. This matches how
    Beamer actually lays out a frame — a 28-word bullet on a 13-word-wide
    column eats 3 lines, not 28 words of column space.

    The ``script`` argument selects the chars-per-cm density (en vs cjk) so
    the per-line capacity matches the one used by :func:`compute_token_budget`
    — without this, a CJK deck would compare CJK budgets against en-density
    counts and produce wrong overflow signals.
    """
    # Detect explicit bullet structure.
    items = re.split(r"\\item\b", frame_tex)
    if len(items) <= 1:
        # No \item — fall back to raw word count.
        return count_body_tokens(frame_tex)

    # Words before the first \item (intro prose).
    intro_tokens = count_body_tokens(items[0])
    width_cm, _ = layout.text_region_cm
    if width_cm <= 0:
        return intro_tokens + sum(count_body_tokens(it) for it in items[1:])

    # Tokens per visual line for this layout / script.
    chars_per_cm = constants.chars_per_cm[script]
    tokens_per_line = max(
        1, int((width_cm * chars_per_cm) / constants.chars_per_word)
    )

    total = intro_tokens
    for item_body in items[1:]:
        item_tokens = count_body_tokens(item_body)
        if item_tokens == 0:
            continue
        # Round up to the next full visual line — a short bullet still eats one line.
        lines = max(1, -(-item_tokens // tokens_per_line))
        total += lines * tokens_per_line
    return total


def _column_width_fraction(spec: str) -> float | None:
    """Extract the fraction from '0.5\\textwidth' / '0.5\\linewidth' / etc."""
    m = re.match(r"\s*([\d.]+)\s*(?:\\textwidth|\\linewidth|\\paperwidth)?", spec)
    if m is None:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def classify_layout(
    *,
    frame_tex: str,
    figure_inventory: dict[str, KeyFigureBundle],
    canvas_budget: CanvasBudget,
) -> CanvasLayout:
    """Pick the best-matching layout from canvas_budget for this frame.

    Heuristic, deterministic — looks at \\includegraphics + \\columns + tabular
    + math structure. Falls back to text_only when no figure is present.
    """
    layouts_by_name = {layout.name: layout for layout in canvas_budget.layouts}
    has_figure = bool(_INCLUDEGRAPHICS_RE.search(frame_tex))
    has_columns = bool(_COLUMNS_RE.search(frame_tex))
    has_tabular = bool(_TABULAR_RE.search(frame_tex))
    has_math_only = (not has_figure) and bool(_MATH_RE.search(frame_tex))

    if has_tabular and not has_figure:
        return layouts_by_name["table_only"]
    if has_math_only:
        return layouts_by_name["equation_centered"]
    if not has_figure:
        return layouts_by_name["text_only"]

    # Figure present — get aspect.
    opts = parse_includegraphics_options(frame_tex)
    fig = figure_inventory.get(str(opts["key"])) if opts["key"] is not None else None
    aspect = fig.dimensions.aspect_ratio if fig is not None else 1.0  # default neutral

    if has_columns:
        # Look at first column's width to pick half vs third vs inset.
        col_match = _COLUMN_WIDTH_RE.search(frame_tex)
        frac = _column_width_fraction(col_match.group(1)) if col_match else 0.5
        frac = frac or 0.5
        if frac <= 0.4:
            return layouts_by_name["figure_left_third_portrait"]
        if frac <= 0.6:
            # Portrait vs landscape determines layout choice
            if aspect_matches("0.5..1.3", aspect):
                return layouts_by_name["figure_left_half_portrait"]
            # Landscape figure in a half-column → mismatch but still pick this layout
            return layouts_by_name["figure_left_half_portrait"]
        return layouts_by_name["figure_inset_small_text_around"]

    # No columns → top/bottom/full layout based on aspect.
    if aspect >= 1.5:
        return layouts_by_name["figure_top_full_landscape"]
    return layouts_by_name["figure_only_full_width"]


def _figure_footprint_cm2(
    frame_tex: str, figure_inventory: dict[str, KeyFigureBundle]
) -> float:
    """Sum the cm² of every \\includegraphics in this frame."""
    total = 0.0
    for m in re.finditer(
        r"\\includegraphics(?:\[[^\]]*\])?\{[^}]+\}", frame_tex
    ):
        opts = parse_includegraphics_options(m.group(0))
        fig = figure_inventory.get(str(opts["key"])) if opts["key"] is not None else None
        aspect = fig.dimensions.aspect_ratio if fig is not None else 1.0
        w_cm, h_cm = resolve_includegraphics_geometry(
            width_spec=opts["width_spec"] if isinstance(opts["width_spec"], str) else None,
            height_spec=opts["height_spec"] if isinstance(opts["height_spec"], str) else None,
            keepaspectratio=bool(opts["keepaspectratio"]),
            aspect_ratio=aspect,
            linewidth_cm=LINEWIDTH_CM_DEFAULT,
            textheight_cm=TEXTHEIGHT_CM_DEFAULT,
        )
        total += w_cm * h_cm
    return total


def _pick_recommendation(
    *,
    overage: int,
    figure_footprint_cm2: float,
    layout: CanvasLayout,
    aspect_mismatch: bool,
) -> tuple[OverflowRecommendation, SplitHint]:
    if overage <= 0 and not aspect_mismatch:
        return "ok", "no_hint"
    if aspect_mismatch and overage <= 0:
        return "relayout_figure", "no_hint"
    # Significant overage.
    if figure_footprint_cm2 > 25.0 and overage < 60:
        return "shrink_figure", "no_hint"
    if overage >= 60:
        if figure_footprint_cm2 > 15.0:
            return "split_frame", "figure_to_own_frame_then_text"
        return "split_frame", "halve_bullets_across_two_frames"
    return "tighten", "no_hint"


def detect_overflow(
    *,
    deck_tex: str,
    figure_inventory: dict[str, KeyFigureBundle],
    canvas_budget: CanvasBudget,
    pdflatex_log: str,
    script: Script,
) -> list[FrameOverflowSignal]:
    """Per-frame overflow analysis — returns one signal per frame, in order."""
    # Map Overfull \vbox warnings to a single aggregate value (we can't reliably
    # attribute per-frame from the log without page-number probing; pick the max
    # observed pt and surface it on every frame's signal as a coarse hint).
    overfull_pts = [float(m.group(1)) for m in _OVERFULL_RE.finditer(pdflatex_log)]
    max_overfull_pt = max(overfull_pts) if overfull_pts else 0.0

    signals: list[FrameOverflowSignal] = []
    for idx, m in enumerate(_FRAME_RE.finditer(deck_tex)):
        title = (m.group(1) or "").strip()
        frame_tex = m.group(0)
        layout = classify_layout(
            frame_tex=frame_tex,
            figure_inventory=figure_inventory,
            canvas_budget=canvas_budget,
        )
        # Use the visual-line-aware count for budget comparison (bullets eat
        # whole lines even when short); surface the raw word count in the
        # recorded signal for diagnostics.
        body_tokens = _count_visual_tokens(
            frame_tex,
            layout=layout,
            constants=canvas_budget.constants,
            script=script,
        )
        budget = compute_token_budget(layout, canvas_budget.constants, script=script)
        overage = max(0, body_tokens - budget)
        fig_footprint = _figure_footprint_cm2(frame_tex, figure_inventory)

        # Aspect mismatch: was this layout's matches_aspect satisfied by the
        # actual figure aspect (if any)?
        opts = parse_includegraphics_options(frame_tex)
        fig = figure_inventory.get(str(opts["key"])) if opts["key"] else None
        aspect = fig.dimensions.aspect_ratio if fig is not None else None
        aspect_mismatch = not aspect_matches(layout.matches_aspect, aspect)

        rec, hint = _pick_recommendation(
            overage=overage,
            figure_footprint_cm2=fig_footprint,
            layout=layout,
            aspect_mismatch=aspect_mismatch,
        )
        signals.append(
            FrameOverflowSignal(
                frame_index=idx,
                frame_title=title,
                page_number=idx + 1,  # 1-based; overlay frames not handled (no \pause)
                matched_layout=layout.name,
                body_token_count=body_tokens,
                text_budget_tokens=budget,
                overage_tokens=overage,
                figure_footprint_cm2=round(fig_footprint, 2),
                layout_aspect_mismatch=aspect_mismatch,
                exceeds_canvas_budget=overage > 0,
                pdflatex_overfull_pt=max_overfull_pt,
                recommendation=rec,
                split_hint=hint,
            )
        )
    return signals
