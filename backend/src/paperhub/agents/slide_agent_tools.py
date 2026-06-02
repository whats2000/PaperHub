# F4.5 slide-agent deck-mutation tools (deterministic — no LLM in here).
"""Deck-mutation tools invoked as tool calls by the slide_agent.

Each ``apply_*`` function is a pure transform over :class:`DeckState`. The
tool-call dispatcher (Phase 8) wraps these with tracer ``record_args`` /
``record_result`` and persists the new ``DeckState``.

We REUSE ``beamer_helpers`` from the surviving slide_pipeline (vetted by F4)
for the load-bearing frame-position math. Preamble replacement is done
directly here so that we always preserve ``\\begin{document}`` and the body
frames — ``beamer_helpers.replace_preamble`` operates on the broader
"page-1 source block" which can include the first frame, and the slide_agent
distinguishes preamble edits from title-frame edits explicitly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from paperhub.pipelines.slide_pipeline.beamer_helpers import (
    extract_frames_from_beamer,
    replace_frame_in_beamer,
)

_FRAME_RE = re.compile(r"\\begin\{frame\}.*?\\end\{frame\}", re.DOTALL)
_BEGIN_DOC_RE = re.compile(r"\\begin\{document\}")


@dataclass(frozen=True)
class DeckState:
    """Immutable working state the slide_agent's tools transform.

    ``workdir`` is the per-session ``slides/`` directory under workspace —
    ``compile_check`` writes ``deck.tex`` / runs ``pdflatex`` there. ``None``
    during early tool calls before the workdir is created. ``dirty`` is
    ``True`` after any mutation; ``compile_check`` flips it back to ``False``
    once the new tex has been written + ``pdflatex`` run.
    """

    deck_tex: str
    preamble: str
    workdir: Path | None
    dirty: bool = True


def _strip_single_frame_env(tex: str) -> str:
    """Validate ``tex`` contains exactly one ``\\begin{frame}…\\end{frame}`` env."""
    matches: list[str] = _FRAME_RE.findall(tex)
    if len(matches) != 1:
        raise ValueError(
            f"frame_tex must be a single \\begin{{frame}}…\\end{{frame}} frame env; "
            f"found {len(matches)}"
        )
    return matches[0]


def _unique_frames(deck_tex: str) -> list[tuple[int, str, int, int]]:
    """De-duplicate ``extract_frames_from_beamer`` entries.

    ``extract_frames_from_beamer`` repeats one tuple per PDF page for frames
    with overlay specs (``\\only<N>`` etc.). F4.5 forbids ``\\pause`` /
    overlays, but we de-dup defensively by ``start_pos`` so frame_index is a
    stable 0-based source-order index.
    """
    seen: set[int] = set()
    out: list[tuple[int, str, int, int]] = []
    for entry in extract_frames_from_beamer(deck_tex):
        start = entry[2]
        if start in seen:
            continue
        seen.add(start)
        out.append(entry)
    return out


def _extract_preamble_block(deck_tex: str) -> tuple[str, int] | None:
    """Return ``(preamble_text, cut_pos)`` where ``cut_pos`` is the start of
    ``\\begin{document}``. Returns ``None`` if ``\\begin{document}`` is absent.
    """
    match = _BEGIN_DOC_RE.search(deck_tex)
    if match is None:
        return None
    return deck_tex[: match.start()], match.start()


def apply_initial_draft(state: DeckState, *, deck_tex: str) -> DeckState:
    """Set the whole ``deck.tex`` from the agent's first cut.

    The agent's ``initial_draft(deck_tex)`` tool call hands us the full deck
    string — preamble + every frame. We cache the preamble block (everything
    before ``\\begin{document}``) so later ``replace_preamble`` calls can
    splice cleanly.

    F4.5: validate structural minimums so a malformed first cut surfaces as a
    tool-call error the LLM sees on the next turn, rather than silently
    becoming a "successful" deck downstream. Real-API benchmark seventh round
    (run 362, slides-multi-zh) caught the LLM emitting a deck_tex with NO
    preamble (no ``\\documentclass``, no ``\\usepackage``, no theme — just
    ``\\title{...}\\begin{document}...``); pdflatex's nonstopmode error
    recovery silently produced a broken 1-page PDF and the agent called
    ``done()`` thinking the deck was clean. Reject at the tool boundary so the
    LLM gets an explicit, actionable error message.
    """
    if "\\documentclass" not in deck_tex:
        raise ValueError(
            "deck_tex is missing \\documentclass — emit a COMPLETE deck "
            "starting with `\\documentclass{beamer}` (or use the "
            "resolved_preamble we passed you verbatim). Do NOT emit only "
            "the document body; the preamble is REQUIRED."
        )
    if "\\begin{document}" not in deck_tex:
        raise ValueError(
            "deck_tex is missing \\begin{document} — the preamble must be "
            "followed by \\begin{document} ... \\end{document}."
        )
    if "\\end{document}" not in deck_tex:
        raise ValueError(
            "deck_tex is missing \\end{document} — close the document body "
            "with \\end{document} after the last frame."
        )
    extracted = _extract_preamble_block(deck_tex)
    preamble = extracted[0] if extracted is not None else ""
    return replace(state, deck_tex=deck_tex, preamble=preamble, dirty=True)


def apply_replace_frame(
    state: DeckState, *, frame_index: int, new_frame_tex: str
) -> DeckState:
    """Swap ONE frame at the given 0-based index.

    ``new_frame_tex`` must be a single ``\\begin{frame}…\\end{frame}`` env.
    """
    new_frame = _strip_single_frame_env(new_frame_tex)
    frames = _unique_frames(state.deck_tex)
    if not (0 <= frame_index < len(frames)):
        raise IndexError(
            f"frame_index {frame_index} out of range (deck has {len(frames)} frames)"
        )
    # beamer_helpers' replace_frame_in_beamer is 1-based + PDF-page-keyed.
    # F4.5 forbids overlays, so source-order index N maps to PDF page N+1.
    new_tex = replace_frame_in_beamer(state.deck_tex, frame_index + 1, new_frame)
    if new_tex is None:
        raise IndexError(f"replace_frame_in_beamer failed at index {frame_index}")
    return replace(state, deck_tex=new_tex, dirty=True)


def apply_insert_frame_after(
    state: DeckState, *, frame_index: int, new_frame_tex: str
) -> DeckState:
    """Insert ONE new frame AFTER the existing frame at ``frame_index``.

    ``frame_index = -1`` inserts BEFORE the first frame (at position 0,
    immediately after ``\\begin{document}``).
    """
    new_frame = _strip_single_frame_env(new_frame_tex)
    frames = _unique_frames(state.deck_tex)
    if not (-1 <= frame_index < len(frames)):
        raise IndexError(
            f"frame_index {frame_index} out of range (deck has {len(frames)} frames)"
        )

    if frame_index == -1:
        marker = _BEGIN_DOC_RE.search(state.deck_tex)
        if marker is None:
            raise ValueError("deck has no \\begin{document}")
        # Skip whitespace immediately after \begin{document} so the new frame
        # lands on its own line.
        cut = marker.end()
        ws = re.match(r"\s*", state.deck_tex[cut:])
        if ws is not None:
            cut += ws.end()
        new_tex = state.deck_tex[:cut] + new_frame + "\n" + state.deck_tex[cut:]
    else:
        # frames[i] is (frame_number, frame_content, start_pos, end_pos).
        end_pos = frames[frame_index][3]
        new_tex = state.deck_tex[:end_pos] + "\n" + new_frame + state.deck_tex[end_pos:]
    return replace(state, deck_tex=new_tex, dirty=True)


def apply_delete_frame(state: DeckState, *, frame_index: int) -> DeckState:
    """Remove ONE frame at the given 0-based index."""
    frames = _unique_frames(state.deck_tex)
    if not (0 <= frame_index < len(frames)):
        raise IndexError(
            f"frame_index {frame_index} out of range (deck has {len(frames)} frames)"
        )
    start_pos = frames[frame_index][2]
    end_pos = frames[frame_index][3]
    new_tex = state.deck_tex[:start_pos] + state.deck_tex[end_pos:]
    # Collapse runs of >=3 newlines the deletion left behind down to one blank line.
    new_tex = re.sub(r"\n{3,}", "\n\n", new_tex)
    return replace(state, deck_tex=new_tex, dirty=True)


def apply_replace_preamble(state: DeckState, *, new_preamble: str) -> DeckState:
    """Swap the preamble block (everything before ``\\begin{document}``).

    ``new_preamble`` MUST NOT itself contain ``\\begin{document}`` — the body
    (``\\begin{document}`` + frames + ``\\end{document}``) is preserved
    verbatim by this tool. To edit the title page itself, use the title-frame
    edit path (Phase 8).
    """
    if "\\begin{document}" in new_preamble:
        raise ValueError(
            "new preamble must not contain \\begin{document} — that lives in the body"
        )
    extracted = _extract_preamble_block(state.deck_tex)
    if extracted is None:
        raise ValueError("deck has no \\begin{document} — cannot locate preamble")
    _, cut = extracted
    new_tex = new_preamble + state.deck_tex[cut:]
    return replace(state, deck_tex=new_tex, preamble=new_preamble, dirty=True)
