"""F4.4 T4 — plumb per-paper ``\\newcommand`` blocks into the deck preamble.

The OLD ``reference/paper2slides-plus`` pipeline preserved the paper's own
custom math macros via ``\\input{ADDITIONAL.tex}``. T1 now collects each
paper's ``\\newcommand`` / ``\\renewcommand`` / ``\\DeclareMathOperator``
block into :class:`PaperTalkBrief.paper_newcommands`; this module
deduplicates them across N papers and renders the block that ``sl_assemble``
splices into the deck preamble between deterministic comment markers.

Dedup contract (per the F4.4 T4 plan):

- Skip blank lines and pure-comment lines.
- Extract the macro name via regex on the four supported patterns
  (``\\newcommand``, ``\\renewcommand``, ``\\DeclareMathOperator``,
  ``\\DeclareMathOperator*``). Lines that match none are emitted with a
  ``% SKIPPED: <line>`` comment so the trace + the .tex preserve the
  rejection signal (no silent drop).
- Identical definition strings across papers emit once.
- Collision (same macro NAME, different definitions): keep the FIRST
  paper's version + emit a ``% NOTE: paper M also defined \\NAME
  differently — using paper N's version`` comment.
- Stable order: paper 1's macros in original order, then paper 2's NEW
  (non-colliding) macros, etc.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from paperhub.models.domain import PaperTalkBrief

# Each pattern captures the macro name. ``\DeclareMathOperator*`` is its own
# pattern (the trailing ``*`` matters); the other three share the same shape.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("newcommand", re.compile(r"\\newcommand\s*\*?\s*\{?\\([A-Za-z@]+)\}?")),
    ("renewcommand", re.compile(r"\\renewcommand\s*\*?\s*\{?\\([A-Za-z@]+)\}?")),
    (
        "DeclareMathOperator*",
        re.compile(r"\\DeclareMathOperator\*\s*\{?\\([A-Za-z@]+)\}?"),
    ),
    (
        "DeclareMathOperator",
        re.compile(r"\\DeclareMathOperator(?!\*)\s*\{?\\([A-Za-z@]+)\}?"),
    ),
)


@dataclass
class NewcommandsSummary:
    """Per-call summary for the tracer (record principle)."""

    unique_count: int = 0
    collisions: list[str] = field(default_factory=list)
    skipped_count: int = 0
    contributing_papers: int = 0


def _extract_macro_name(line: str) -> str | None:
    """Return the macro name for ``line`` if it matches a supported pattern,
    else ``None``. ``\\DeclareMathOperator*`` is checked before
    ``\\DeclareMathOperator`` so the star variant wins."""
    for _kind, pat in _PATTERNS:
        m = pat.search(line)
        if m:
            return m.group(1)
    return None


def build_newcommands_block(
    briefs: list[PaperTalkBrief],
) -> tuple[str, NewcommandsSummary]:
    """Build the comment-marker-wrapped newcommands block for the deck preamble.

    Returns ``(block_text, summary)``. ``block_text`` always carries the
    BEGIN/END markers so the location stays consistent even when every brief
    is empty (a ``% (no paper-defined macros to plumb)`` note appears inside).

    The dedup walks briefs in order: paper 1's macros enter first (in their
    original line order), then paper 2 contributes only NAMES paper 1 didn't
    already claim, etc. Identical-definition collisions are silent (one row
    emitted); different-definition collisions emit a NOTE comment near the
    surviving definition naming the divergent later paper.
    """
    # Order-preserving: name -> (definition_line, paper_index)
    by_name: dict[str, tuple[str, int]] = {}
    order: list[str] = []
    # Lines that fail the four-pattern regex: emitted as % SKIPPED comments
    # in the block so the trace preserves the rejection signal.
    skipped: list[tuple[int, str]] = []
    # Collision NOTEs: (winning_name, note_text) — attached after the winner.
    notes_for: dict[str, list[str]] = {}
    contributing = 0
    collisions: list[str] = []

    for paper_idx, brief in enumerate(briefs, start=1):
        raw = brief.paper_newcommands or ""
        if raw.strip():
            contributing += 1
        for raw_line in raw.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            # Pure-comment line (no LaTeX command on it) is informational; skip.
            if line.startswith("%"):
                continue
            name = _extract_macro_name(line)
            if name is None:
                skipped.append((paper_idx, line))
                continue
            if name in by_name:
                existing_line, existing_paper = by_name[name]
                if existing_line == line:
                    # Identical re-emit — drop silently.
                    continue
                # Same name, different definition → keep first, note divergence.
                notes_for.setdefault(name, []).append(
                    f"% NOTE: paper {paper_idx} also defined \\{name} "
                    f"differently — using paper {existing_paper}'s version"
                )
                collisions.append(name)
                continue
            by_name[name] = (line, paper_idx)
            order.append(name)

    body_lines: list[str] = []
    for name in order:
        line, _paper = by_name[name]
        body_lines.append(line)
        for note in notes_for.get(name, []):
            body_lines.append(note)
    for paper_idx, line in skipped:
        body_lines.append(f"% SKIPPED (paper {paper_idx}): {line}")

    header = (
        "% BEGIN paperhub:paper_newcommands "
        "(F4.4 T4 — paper-defined macros plumbed from PaperTalkBrief)"
    )
    footer = "% END paperhub:paper_newcommands"
    if not body_lines:
        body_lines = ["% (no paper-defined macros to plumb)"]

    block = "\n".join([header, *body_lines, footer])
    summary = NewcommandsSummary(
        unique_count=len(order),
        collisions=collisions,
        skipped_count=len(skipped),
        contributing_papers=contributing,
    )
    return block, summary
