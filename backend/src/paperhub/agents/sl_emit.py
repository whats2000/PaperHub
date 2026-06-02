"""F4.5 sl_emit - deterministic finalize stage (3rd and last).

Runs AFTER the slide_agent returns satisfied=True (or budget-exhausted with
deck content). Responsibilities:
  1. Contract #1 enforcement - ``verify_and_fix_graphics`` audits every
     ``\\includegraphics`` key against the inventory; unknown keys become
     ``\\textit{[figure omitted]}``. NEVER prompts the LLM (deterministic).
  2. Persist decks + deck_slides rows (one current deck per session per the
     ``UNIQUE(session_id)`` constraint; ``deck_slides`` rebuilt from the
     post-audit frames).
  3. Snapshot the new (tex, speaker_notes) under
     ``edit_history/version_<ts>.json``.
  4. Update ``decks.current_version_id`` to point at the new snapshot.
  5. The caller (report_graph) emits the ``deck`` SSE event from the
     ``EmitResult``.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aiosqlite

from paperhub.models.slide_domain import KeyFigureBundle
from paperhub.pipelines.slide_pipeline.beamer_helpers import (
    extract_frames_from_beamer,
)
from paperhub.pipelines.slide_pipeline.figure_inventory import (
    verify_and_fix_graphics,
)

# F4.5: defensive post-process — see ``enforce_figure_paragraph_break``.
_INCLUDEGRAPHICS_RE = re.compile(
    r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{[^}]*\}"
)
# A LaTeX command followed by a braced argument; matches ``\vspace{0.3em}``,
# ``\hspace{1pt}``, ``\vspace*{...}`` etc. Used to skip trailing spacing
# directives between the figure and the next real content.
_SPACING_CMD_RE = re.compile(r"\\[hv]space\*?\s*\{[^}]*\}")
# An environment whose internal layout we should NOT touch — columns place
# children side-by-side by design, ``figure`` floats have their own caption
# discipline.
_LAYOUT_ENV_NAMES = ("column", "columns", "figure", "wrapfigure")


def _is_inside_layout_env(tex: str, pos: int) -> bool:
    """Return True if ``pos`` lies inside an unclosed ``column``/``columns``/
    ``figure``/``wrapfigure`` environment.

    Scans ``tex[:pos]`` and counts ``\\begin{env}`` vs ``\\end{env}`` for each
    layout-aware env. If any of them has more opens than closes at ``pos``,
    we're inside one and must NOT inject a ``\\par``.
    """
    head = tex[:pos]
    for env in _LAYOUT_ENV_NAMES:
        opens = len(re.findall(r"\\begin\{" + re.escape(env) + r"\}", head))
        closes = len(re.findall(r"\\end\{" + re.escape(env) + r"\}", head))
        if opens > closes:
            return True
    return False


def _is_already_wrapped_in_center(tex: str, fig_start: int, fig_end: int) -> bool:
    """Return True if the ``\\includegraphics`` is already wrapped in a
    ``\\begin{center}...\\end{center}`` environment on the surrounding lines.

    Concretely: the previous non-whitespace line (within the same frame body)
    must be ``\\begin{center}`` and the next non-whitespace line must be
    ``\\end{center}``. We don't try to handle the figure being part of a
    multi-line ``center`` block with other content — that's a structure the
    LLM never produces; we only need to detect our own previous injection.
    """
    # Look backwards for the previous non-whitespace line.
    prev_nl = tex.rfind("\n", 0, fig_start)
    if prev_nl == -1:
        return False
    # Walk back over blank lines to the first non-empty line.
    scan = prev_nl
    while scan > 0:
        line_end = scan
        line_start = tex.rfind("\n", 0, scan) + 1
        prev_line = tex[line_start:line_end].strip()
        if prev_line:
            if prev_line != "\\begin{center}":
                return False
            break
        scan = line_start - 1
    else:
        return False

    # Look forward for the next non-whitespace line.
    next_nl = tex.find("\n", fig_end)
    if next_nl == -1:
        return False
    scan = next_nl
    while scan < len(tex):
        line_start = scan + 1
        line_end_idx = tex.find("\n", line_start)
        if line_end_idx == -1:
            line_end_idx = len(tex)
        next_line = tex[line_start:line_end_idx].strip()
        if next_line:
            return next_line == "\\end{center}"
        scan = line_end_idx
    return False


def enforce_figure_paragraph_break(tex: str) -> str:
    """Make ``\\includegraphics`` in a frame body unambiguously stand alone.

    Why: with ``keepaspectratio`` + height-bound includegraphics, the rendered
    image is narrower than ``\\linewidth``; without a hard paragraph break,
    LaTeX (and especially Beamer + xeCJK) flows the following text to the
    RIGHT of the image (inline box behavior). An earlier fix injected
    ``\\centering`` before the figure, but ``\\centering`` is a DECLARATION
    that stays in effect for the rest of the surrounding group (the entire
    frame body), so caption text below still inherited centered-paragraph
    state and Beamer+xeCJK still flowed it inline next to the image.

    The fix uses the ``center`` ENVIRONMENT instead — its scope ends with
    ``\\end{center}``, so the caption text below is in a fresh paragraph
    context with no scope bleed. The environment also inserts vertical space
    before/after and forces a paragraph break.

    For each ``\\includegraphics`` not inside a column/figure env, this
    function:

      1. Wraps the ``\\includegraphics`` line in
         ``\\begin{center}\\n<figure>\\n\\end{center}`` (preserving the
         figure's original indent on the figure line; the wrapper lines
         carry the same indent).
      2. Injects a literal blank line (``\\n\\n``) between the end of the
         figure block (the ``\\end{center}`` + any trailing
         ``\\vspace``/``\\hspace``) and the next non-whitespace content.

    The function is idempotent and conservative:
      - Skips wrapping when the figure is already inside an explicit
        ``\\begin{center}``/``\\end{center}`` pair on adjacent lines.
      - Skips the blank-line injection when one is already present.
      - Skips everything when the figure is inside ``\\begin{column}`` /
        ``\\begin{columns}`` / ``\\begin{figure}`` / ``\\begin{wrapfigure}``.
      - Skips everything when the next non-whitespace token is ``\\end{...}``.
    """
    out_parts: list[str] = []
    cursor = 0
    for m in _INCLUDEGRAPHICS_RE.finditer(tex):
        # Skip when inside a layout-managing environment — this single early
        # return guards both the wrap and blank-line injections.
        if _is_inside_layout_env(tex, m.start()):
            continue

        # Find the end of the includegraphics "block" — the figure call
        # itself plus any immediately-following \v/hspace commands and
        # whitespace (including a single newline, but NOT a blank line which
        # already terminates the block correctly).
        block_end = m.end()
        scan = block_end
        while scan < len(tex):
            # Try to consume a spacing cmd that's either right at scan or
            # preceded only by horizontal whitespace + a single newline
            # (i.e. on the next line, indented).
            ws_then_cmd = re.match(
                r"([ \t]*\n)?[ \t]*(\\[hv]space\*?\s*\{[^}]*\})",
                tex[scan:],
            )
            if ws_then_cmd:
                scan += ws_then_cmd.end()
                # Consume trailing horizontal whitespace after the cmd.
                tail = re.match(r"[ \t]*", tex[scan:])
                if tail:
                    scan += tail.end()
                continue
            break

        # ``scan`` is now after the figure + trailing spacing cmds, sitting on
        # whatever comes next (possibly a newline starting a blank line, or
        # the next content token).
        remainder = tex[scan:]

        # Look ahead at the first non-whitespace token. If it's an \end{...}
        # (frame, column, etc.) → no text follows → no injection at all
        # (neither wrap nor blank line — a standalone figure that ends
        # the frame doesn't need either).
        nonspace = re.match(r"\s*", remainder)
        next_pos = scan + (nonspace.end() if nonspace else 0)
        if next_pos >= len(tex):
            continue
        if tex[next_pos:].startswith("\\end{"):
            continue

        # Decide what already exists.
        already_wrapped = _is_already_wrapped_in_center(tex, m.start(), m.end())
        has_paragraph_break = bool(
            re.match(r"\s*\n\s*\n", remainder)
            or re.match(r"\s*\\par\b", remainder)
            or re.match(r"\s*\\\\", remainder)
        )

        # Nothing to do if BOTH are already present.
        if already_wrapped and has_paragraph_break:
            continue

        # Find the start of the figure's line so we can replace it (and its
        # leading indent) with the wrapped version.
        line_start = tex.rfind("\n", 0, m.start()) + 1
        fig_line_prefix = tex[line_start:m.start()]
        indent_match = re.match(r"[ \t]*", fig_line_prefix)
        indent = indent_match.group(0) if indent_match else ""
        # The figure line is allowed to have ONLY whitespace before
        # \includegraphics; if there's any non-whitespace there, fall back to
        # the safe behaviour of just inserting before \includegraphics (which
        # would be unusual LLM output).
        prefix_is_indent_only = fig_line_prefix.strip() == ""

        if already_wrapped:
            # Just emit the figure region verbatim; only the blank-line
            # injection (below) may still need to run.
            out_parts.append(tex[cursor:scan])
            cursor = scan
        else:
            # Emit everything up to (but not including) the figure line's
            # leading indent — we replace that line with our wrapped form.
            if prefix_is_indent_only:
                out_parts.append(tex[cursor:line_start])
                # Wrapped block: \begin{center} / figure (with original
                # indent) / \end{center}, each on its own line.
                wrapped = (
                    f"{indent}\\begin{{center}}\n"
                    f"{indent}{tex[m.start():m.end()]}\n"
                    f"{indent}\\end{{center}}"
                )
                out_parts.append(wrapped)
                # Continue emitting from immediately after the figure call;
                # the original trailing newline + spacing cmds are preserved.
                cursor = m.end()
                # Re-emit any trailing spacing cmds verbatim (they are in
                # tex[m.end():scan]).
                out_parts.append(tex[m.end():scan])
                cursor = scan
            else:
                # Fallback: figure is not on its own line. Emit up to the
                # figure, prepend a \begin{center}+newline, emit the figure,
                # append a newline+\end{center}. Indent is best-effort.
                out_parts.append(tex[cursor:m.start()])
                out_parts.append(
                    f"\\begin{{center}}\n{indent}{tex[m.start():m.end()]}\n"
                    f"{indent}\\end{{center}}"
                )
                cursor = m.end()
                out_parts.append(tex[m.end():scan])
                cursor = scan

        if not has_paragraph_break:
            # Inject a blank line between the figure block and the next
            # content. We want exactly ``\n\n`` separating them.
            leading_ws = re.match(r"[ \t]*\n", remainder)
            if leading_ws:
                out_parts.append("\n\n")
                cursor = scan + leading_ws.end()
            else:
                # No newline before the next content — inject ``\n\n`` plus
                # the deduced indent of the next non-empty content.
                line_match = re.match(r"([ \t]*)\S", remainder)
                next_indent = line_match.group(1) if line_match else ""
                out_parts.append(f"\n\n{next_indent}")
                ws = re.match(r"[ \t]*", remainder)
                if ws:
                    cursor = scan + ws.end()

    out_parts.append(tex[cursor:])
    return "".join(out_parts)


@dataclass(frozen=True)
class EmitResult:
    deck_id: int
    deck_tex: str  # post-audit (may differ from input on unknown-key replacements)
    page_count: int
    current_version_id: str
    figure_audit_replacements: int  # how many \includegraphics were replaced


def _frame_spans(deck_tex: str) -> list[tuple[str, int, int]]:
    """Return ``[(frame_tex, page_start, page_end), ...]`` in source order.

    ``extract_frames_from_beamer`` already duplicates each frame across its
    overlay pages (page numbers align with the rendered PDF), so collapsing
    by frame body gives ``(content, first_page, last_page)`` per logical
    frame.
    """
    raw = extract_frames_from_beamer(deck_tex)
    if not raw:
        return []
    spans: list[tuple[str, int, int]] = []
    cur_content = raw[0][1]
    cur_start = raw[0][0]
    cur_end = raw[0][0]
    for page_num, content, _s, _e in raw[1:]:
        if content == cur_content and page_num == cur_end + 1:
            cur_end = page_num
            continue
        spans.append((cur_content, cur_start, cur_end))
        cur_content = content
        cur_start = page_num
        cur_end = page_num
    spans.append((cur_content, cur_start, cur_end))
    return spans


async def run_sl_emit(
    *,
    session_id: int,
    run_id: int,
    deck_tex: str,
    workdir: Path,
    page_count: int,
    status: str,  # 'ok' | 'error'
    contributing_paper_ids: list[int],
    figure_inventory: dict[str, KeyFigureBundle],
    conn: aiosqlite.Connection,
    speaker_notes: dict[int, str] | None = None,  # opt-in NOTES path
) -> EmitResult:
    # 1. Contract #1: figure-key audit.
    inventory_keys: set[str] = set(figure_inventory.keys())
    audited_tex, rejected = verify_and_fix_graphics(
        deck_tex, allowed_keys=inventory_keys
    )
    n_replacements = len(rejected)
    # F4.5: defensive — wrap \includegraphics in \begin{center}...\end{center}
    # and inject a blank line after, so the caption text below renders as a
    # standalone paragraph instead of flowing inline beside the figure
    # (observed on Chinese decks in real-API gate; \centering's scope leaked).
    audited_tex = enforce_figure_paragraph_break(audited_tex)

    # 2. + 3. Filesystem work off the event loop (write audited deck.tex,
    # write the version snapshot under edit_history/).
    deck_path = workdir / "deck.tex"
    pdf_path = workdir / "deck.pdf"
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    version_id = f"version_{ts}"
    snapshot = {
        "tex_content": audited_tex,
        "speaker_notes": {str(k): v for k, v in (speaker_notes or {}).items()},
        "description": "F4.5 sl_emit snapshot",
        "timestamp": ts,
    }

    def _persist_files() -> bool:
        workdir.mkdir(parents=True, exist_ok=True)
        deck_path.write_text(audited_tex, encoding="utf-8")
        edit_history = workdir / "edit_history"
        edit_history.mkdir(exist_ok=True)
        (edit_history / f"{version_id}.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return pdf_path.exists()

    pdf_exists = await asyncio.to_thread(_persist_files)

    # 4. Upsert the decks row.
    speaker_notes_json = (
        json.dumps(
            {str(k): v for k, v in (speaker_notes or {}).items()},
            ensure_ascii=False,
        )
        if speaker_notes
        else None
    )

    await conn.execute(
        """
        INSERT INTO decks (
            session_id, run_id, tex_path, pdf_path, speaker_notes_json,
            plan_json, page_count, current_version_id,
            contributing_paper_ids_json, status, created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, datetime('now'), datetime('now')
        )
        ON CONFLICT(session_id) DO UPDATE SET
            run_id = excluded.run_id,
            tex_path = excluded.tex_path,
            pdf_path = excluded.pdf_path,
            speaker_notes_json = excluded.speaker_notes_json,
            page_count = excluded.page_count,
            current_version_id = excluded.current_version_id,
            contributing_paper_ids_json = excluded.contributing_paper_ids_json,
            status = excluded.status,
            updated_at = datetime('now')
        """,
        (
            session_id,
            run_id,
            str(deck_path),
            str(pdf_path) if pdf_exists else None,
            speaker_notes_json,
            page_count,
            version_id,
            json.dumps(contributing_paper_ids),
            status,
        ),
    )

    async with conn.execute(
        "SELECT id FROM decks WHERE session_id = ?", (session_id,)
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise RuntimeError(
            f"sl_emit: decks row not found for session_id={session_id} after upsert"
        )
    deck_id = int(row[0])

    # 5. Rebuild deck_slides rows. Earlier rows (if any) are cleared because
    # frame_count likely changed; notes are reapplied by index from `speaker_notes`.
    await conn.execute("DELETE FROM deck_slides WHERE deck_id = ?", (deck_id,))
    spans = _frame_spans(audited_tex)
    for idx, (frame_tex, page_start, page_end) in enumerate(spans):
        note_text = (speaker_notes or {}).get(idx)
        await conn.execute(
            """
            INSERT INTO deck_slides (
                deck_id, slide_index, frame_tex, note_text, note_language,
                page_start, page_end
            ) VALUES (?, ?, ?, ?, NULL, ?, ?)
            """,
            (deck_id, idx, frame_tex, note_text, page_start, page_end),
        )
    await conn.commit()

    return EmitResult(
        deck_id=deck_id,
        deck_tex=audited_tex,
        page_count=page_count,
        current_version_id=version_id,
        figure_audit_replacements=n_replacements,
    )
