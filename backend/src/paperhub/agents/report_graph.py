"""Report Agent subgraph (Plan F3/F4 — PhD-grade slide topology, SRS v2.19+).

GENERATE path (frame-only; F4 — speaker notes are opt-in, authored by a
separate NOTES sub-flow):

    sl_resolve → {empty | no_latex | create}; create runs:

    sl_understand → sl_narrate → sl_draft → sl_coherence → sl_assemble
    → sl_verify_figures → sl_compile → sl_emit → END

It consumes F2's ``PaperAsset`` (figures+captions, equations, sections) per
enabled paper, builds a deck-wide collision-free figure inventory, drafts
concise frames grounded in retrieved chunks, deterministically rejects any
non-inventory figure (the hard no-hallucination guarantee), and compiles with
an Overfull-aware revise loop. The ``deck`` SSE event + the ``decks`` row
shape are unchanged from F1.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from paperhub.agents.memory_recall import build_active_memory_block
from paperhub.agents.report_pipeline import (
    author_note,
    classify_deck_command,
    coherence_pass,
    draft_frame,
    edit_frame,
    narrate_talk,
    parse_slide_budget,
    revise_tex,
    understand_paper,
)
from paperhub.agents.state import effective_query, response_language
from paperhub.db.deck_slides import (
    DeckSlideRow,
    get_deck_slides,
    rebuild_speaker_notes_json,
    replace_deck_slides,
    update_slide_note,
)
from paperhub.db.decks import get_deck, upsert_deck
from paperhub.db.tool_calls import drain_tool_calls_since
from paperhub.llm.adapter import LlmAdapter
from paperhub.models.domain import (
    AgentState,
    DeckCommand,
    FrameDraft,
    OutlineSlide,
    PaperBrief,
    SlideBudget,
)
from paperhub.pipelines.paper_asset import read_paper_asset
from paperhub.pipelines.slide_pipeline import compile as compile_mod
from paperhub.pipelines.slide_pipeline.assemble import AssembleInput, assemble_deck
from paperhub.pipelines.slide_pipeline.beamer_helpers import (
    extract_frames_from_beamer,
    replace_frame_in_beamer,
)
from paperhub.pipelines.slide_pipeline.deck_slides_map import build_deck_slides
from paperhub.pipelines.slide_pipeline.figure_inventory import (
    InventoryFigure,
    build_inventory,
    stage_inventory,
    verify_and_fix_graphics,
)
from paperhub.pipelines.slide_pipeline.history import VersionHistory
from paperhub.tracing.tracer import Tracer

# Find the figures actually referenced across the drafted frames (mirrors the
# pattern figure_inventory uses) so only those are staged into the deck dir.
_GRAPHICS_RE = re.compile(r"\\includegraphics(\[[^\]]*\])?\{([^}]+)\}")

_THEME = "metropolis"
_EMPTY_MSG = (
    "I couldn't find any enabled reference papers in this chat. "
    "Add and enable at least one reference, then ask me to make slides."
)
_NO_LATEX_MSG = (
    "Slide generation needs a LaTeX distribution (TeX Live or MikTeX) with "
    "pdflatex on PATH. Install one and try again."
)


def _pdflatex_available() -> bool:
    """Return True if ``pdflatex`` is discoverable on PATH."""
    return bool(shutil.which("pdflatex"))


_FRAMETITLE_RE = re.compile(r"\\frametitle\{([^}]*)\}")
_BEGINFRAME_TITLE_RE = re.compile(r"\\begin\{frame\}\s*\{([^}]*)\}")
# The synthetic \maketitle tuple emitted by extract_frames_from_beamer when
# \maketitle precedes the first real frame (mirrors deck_slides_map).
_SYNTHETIC_MAKETITLE = r"\maketitle"


def _frame_title(frame_tex: str) -> str:
    """Best-effort human title for a Beamer frame (for the deck outline)."""
    m = _FRAMETITLE_RE.search(frame_tex)
    if m:
        return m.group(1).strip() or "slide"
    m = _BEGINFRAME_TITLE_RE.search(frame_tex)
    if m:
        return m.group(1).strip() or "slide"
    return "slide"


def _real_frame_number(full_tex: str, slide_index: int) -> int | None:
    """Map a 0-based ``deck_slides.slide_index`` to the 1-based frame number
    that ``replace_frame_in_beamer`` expects.

    ``build_deck_slides`` drops the synthetic ``\\maketitle`` tuple, so
    ``slide_index`` enumerates only the real ``\\begin{frame}`` blocks in
    document order. ``extract_frames_from_beamer`` numbers ALL frames including
    that synthetic ``\\maketitle`` (frame 1 when it precedes the first frame),
    so for a ``\\maketitle`` deck the first real frame is frame number 2, not 1.
    Walk the extracted frames, skipping synthetic ``\\maketitle`` tuples, and
    return the ``frame_number`` of the Nth real frame (N = ``slide_index``)."""
    real = 0
    seen: set[int] = set()
    for num, content, _s, _e in extract_frames_from_beamer(full_tex):
        if content.strip() == _SYNTHETIC_MAKETITLE:
            continue
        # extract_frames_from_beamer duplicates a frame tuple per overlay page;
        # count each distinct frame_number once.
        if num in seen:
            continue
        seen.add(num)
        if real == slide_index:
            return num
        real += 1
    return None


def _select_rows(
    rows: list[DeckSlideRow], cmd: DeckCommand, *, current_view_page: int
) -> list[DeckSlideRow]:
    """Select the deck_slides rows a NOTES/EDIT command targets.

    - ``all``     → every row.
    - ``current`` → the single row whose [page_start, page_end] contains the
      page on screen (fallback: the first row).
    - ``page``    → the row containing ``cmd.target_page`` (empty list if none —
      the caller surfaces a "page not found" message).
    Pure: ``current_view_page`` is passed in, not read from state."""
    if cmd.target_scope == "all":
        return list(rows)
    if cmd.target_scope == "current":
        for r in rows:
            if r.page_start <= current_view_page <= r.page_end:
                return [r]
        return rows[:1]
    # page
    if cmd.target_page is not None:
        for r in rows:
            if r.page_start <= cmd.target_page <= r.page_end:
                return [r]
    return []


@dataclass
class ReportDeps:
    adapter: LlmAdapter
    tracer: Tracer
    conn: aiosqlite.Connection
    retriever: Any
    workspace: Path
    # F1 model-tier names are reused as-is so chat.py / config.py need no
    # change. The PhD flow maps them onto its stages: plan_model → narrate,
    # section_model → draft + revise, notes_model → understand, coherence
    # reuses section_model.
    plan_model: str
    section_model: str
    notes_model: str
    resolve_model: str
    recall_enabled: bool = field(default=True)


async def _enabled_papers(
    conn: aiosqlite.Connection, session_id: int
) -> list[dict[str, Any]]:
    async with conn.execute(
        "SELECT pc.id, pc.title, pc.abstract, pc.sections_json, pc.source_dir_path "
        "FROM papers p JOIN paper_content pc ON pc.id = p.paper_content_id "
        "WHERE p.session_id = ? AND p.enabled = 1 ORDER BY p.added_at",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {
            "id": r[0],
            "title": r[1],
            "abstract": r[2],
            "sections_json": r[3],
            "source_dir": r[4],
        }
        for r in rows
    ]


def _inventory_lines(figs: list[InventoryFigure]) -> str:
    """Render ``key: caption`` lines for a figure inventory (or a placeholder)."""
    return "\n".join(f"{f.key}: {f.caption}" for f in figs) or "(no figures)"


def _paper_block(paper: dict[str, Any], figs: list[InventoryFigure]) -> str:
    """Assemble the understand prompt's per-paper block from its PaperAsset.

    title + abstract (from paper_content) + section names + this paper's slice
    of the deck figure inventory (``key: caption``) + equations (LaTeX).
    """
    source_dir = Path(str(paper["source_dir"])) if paper.get("source_dir") else None
    asset = read_paper_asset(source_dir) if source_dir else None
    section_names = [s.name for s in asset.sections] if asset else []
    equations = [e.latex for e in asset.equations] if asset else []
    lines = [
        f"Title: {paper['title']}",
        f"Abstract: {(paper['abstract'] or '').strip()}",
        "Sections: " + (", ".join(section_names) or "(none)"),
        "Figures:",
        _inventory_lines(figs),
        "Equations:",
        ("\n".join(equations) or "(none)"),
    ]
    return "\n".join(lines)


def _briefs_block(briefs: list[PaperBrief]) -> str:
    """Render the per-paper briefs into the narrate prompt's briefs block."""
    parts: list[str] = []
    for b in briefs:
        parts.append(
            f"paper_id={b.paper_id}\n"
            f"  contribution: {b.contribution}\n"
            f"  method: {b.method}\n"
            f"  key_results: {'; '.join(b.key_results)}\n"
            f"  key_figure_keys: {', '.join(b.key_figure_keys) or '(none)'}\n"
            f"  key_equations: {'; '.join(b.key_equations) or '(none)'}"
        )
    return "\n\n".join(parts)


def build_report_subgraph(deps: ReportDeps) -> Any:
    async def _resolve(state: AgentState) -> AgentState:
        papers = await _enabled_papers(deps.conn, state["session_id"])
        out: AgentState = {**state, "report_papers": papers}
        # Guards run in _route; only classify/budget when we will actually act.
        if not papers or not _pdflatex_available():
            return out

        instruction = effective_query(state) or state.get("user_message", "")
        deck = await get_deck(deps.conn, session_id=state["session_id"])
        if deck is None:
            out["report_budget"] = parse_slide_budget(instruction)
            return out

        rows = await get_deck_slides(deps.conn, deck_id=deck.id)
        outline = "\n".join(
            f"{r.page_start}. {_frame_title(r.frame_tex)}" for r in rows
        ) or "(no slides)"
        cmd = await classify_deck_command(
            adapter=deps.adapter,
            tracer=deps.tracer,
            model=deps.resolve_model,
            instruction=instruction,
            current_view_page=state.get("current_view_page") or 1,
            deck_outline=outline,
        )
        out["report_command"] = cmd
        return out

    def _route(state: AgentState) -> str:
        if not state.get("report_papers"):
            return "empty"
        if not _pdflatex_available():
            return "no_latex"
        cmd = state.get("report_command")
        if cmd is None:
            return "create"
        if cmd.action == "regenerate":
            return "create"
        if cmd.action in ("generate_notes", "edit_notes"):
            return "notes"
        return "edit_slides"

    async def _empty(state: AgentState) -> AgentState:
        return {**state, "final_response": _EMPTY_MSG}

    async def _no_latex(state: AgentState) -> AgentState:
        return {**state, "final_response": _NO_LATEX_MSG}

    def _streaming(state: AgentState) -> tuple[Any, Any]:
        """Return ``(writer, flush_steps)`` for a node. ``writer`` streams custom
        events (no-op outside an ``astream`` context); ``flush_steps`` drains
        newly-written tool_calls rows as ``tool_step`` events. Each node gets its
        own pair so the per-stage trace streams live (per-stage), not at the end."""
        writer: Any
        try:
            writer = get_stream_writer()
        except Exception:
            writer = None
        run_id = state.get("run_id")
        last_emitted = -1

        async def _flush_steps() -> None:
            nonlocal last_emitted
            if writer is None or run_id is None:
                return
            recs = await drain_tool_calls_since(deps.conn, run_id, last_emitted)
            for rec in recs:
                writer({"event": "tool_step", "record": rec})
                last_emitted = rec["step_index"]

        return writer, _flush_steps

    def _emit_deck(
        writer: Any,
        deck: Any,
        title: str,
        papers_meta: list[dict[str, Any]],
        has_notes: bool,
    ) -> None:
        """Emit the ``deck`` SSE event (shape unchanged from F1)."""
        if writer is None:
            return
        writer(
            {
                "event": "deck",
                "deck": {
                    "deck_id": deck.id,
                    "session_id": deck.session_id,
                    "page_count": deck.page_count,
                    "title": title,
                    "status": deck.status,
                    "contributing_papers": papers_meta,
                    "has_notes": has_notes,
                },
            }
        )

    async def _inventory_keys(papers: list[dict[str, Any]]) -> set[str]:
        """Rebuild the allowed figure-key set (same call ``_generate`` uses)."""
        inv = await asyncio.to_thread(build_inventory, papers)
        return {f.key for f in inv}

    def _deck_title(deck: Any) -> str:
        """Best-effort deck title from the persisted plan (TalkOutline)."""
        plan = deck.plan or {}
        return str(plan.get("title") or "Slides")

    async def _generate(state: AgentState) -> AgentState:
        writer, _flush_steps = _streaming(state)

        budget: SlideBudget = state.get("report_budget") or SlideBudget()

        papers: list[dict[str, Any]] = state["report_papers"]
        lang = response_language(state)
        mem = ""
        if deps.recall_enabled:
            mem = await build_active_memory_block(
                deps.conn, session_id=state.get("session_id")
            )

        # ---- deck-wide figure inventory (built ONCE, collision-free keys) ----
        inv: list[InventoryFigure] = await asyncio.to_thread(build_inventory, papers)
        inv_keys = {f.key for f in inv}
        inv_by_key = {f.key: f for f in inv}
        # Per-paper inventory slices (paper enumeration index → "p{idx}-" prefix).
        per_paper_figs: dict[int, list[InventoryFigure]] = {idx: [] for idx in range(len(papers))}
        for f in inv:
            for idx in range(len(papers)):
                if f.key.startswith(f"p{idx}-"):
                    per_paper_figs[idx].append(f)
                    break

        # ---- sl_understand: per-paper briefs (fan-out) ----
        briefs: list[PaperBrief] = list(
            await asyncio.gather(
                *[
                    understand_paper(
                        paper_block=_paper_block(p, per_paper_figs.get(idx, [])),
                        adapter=deps.adapter,
                        tracer=deps.tracer,
                        model=deps.notes_model,
                        response_language=lang,
                    )
                    for idx, p in enumerate(papers)
                ]
            )
        )
        await _flush_steps()

        # ---- sl_narrate: one cross-paper TalkOutline ----
        outline = await narrate_talk(
            briefs_block=_briefs_block(briefs),
            figure_inventory=_inventory_lines(inv),
            adapter=deps.adapter,
            tracer=deps.tracer,
            model=deps.plan_model,
            response_language=lang,
            memory_context=mem,
            target_slide_count=budget.target_slide_count,
            depth=budget.depth,
        )
        # Defensively drop any figure_key not in the deck inventory.
        slides: list[OutlineSlide] = []
        for s in outline.slides:
            if s.figure_key and s.figure_key not in inv_keys:
                s = s.model_copy(update={"figure_key": None})
            slides.append(s)
        await _flush_steps()

        # ---- sl_draft: per-slide frame-only drafts (fan-out, IN ORDER) ----
        retr = deps.retriever

        def _chunks_block(chunk_ids: list[int]) -> str:
            if retr is None or not chunk_ids:
                return "(no retrieved chunks; ground in the brief)"
            chunks = retr.retrieve(
                "",
                enabled_paper_content_ids=[p["id"] for p in papers],
                corpus_size=1000,
                top_k=len(chunk_ids) or 6,
            )
            wanted = set(chunk_ids)
            text = "\n\n".join(
                c.text for c in chunks if getattr(c, "chunk_id", None) in wanted
            )
            return text or "(no retrieved chunks; ground in the brief)"

        def _assigned_figure(key: str | None) -> str | None:
            if key and key in inv_by_key:
                f = inv_by_key[key]
                return f"{f.key}: {f.caption}"
            return None

        drafts: list[FrameDraft] = list(
            await asyncio.gather(
                *[
                    draft_frame(
                        deck_title=outline.title,
                        slide=s,
                        assigned_figure=_assigned_figure(s.figure_key),
                        assigned_equation=s.equation,
                        chunks_block=_chunks_block(s.chunk_ids),
                        adapter=deps.adapter,
                        tracer=deps.tracer,
                        model=deps.section_model,
                        response_language=lang,
                        memory_context=mem,
                    )
                    for s in slides
                ]
            )
        )
        await _flush_steps()

        # ---- sl_coherence: smooth all frames together ----
        frames = await coherence_pass(
            frames=[d.frame for d in drafts],
            adapter=deps.adapter,
            tracer=deps.tracer,
            model=deps.section_model,
            response_language=lang,
        )
        await _flush_steps()

        slides_dir = (
            deps.workspace / "chat_session" / str(state["session_id"]) / "slides"
        )
        figures_dir = slides_dir / "figures"

        # ---- sl_assemble: stage referenced figures + build the deck tex ----
        referenced: set[str] = set()
        for frame in frames:
            for m in _GRAPHICS_RE.finditer(frame):
                referenced.add(Path(m.group(2)).stem)
        used = [f for f in inv if f.key in referenced]

        # ADDITIONAL.tex macros from each paper's source dir (arXiv LaTeX path).
        def _read_macros() -> list[str]:
            out: list[str] = []
            for p in papers:
                sd = p.get("source_dir")
                if not sd:
                    continue
                add = Path(str(sd)) / "ADDITIONAL.tex"
                if add.exists():
                    out.append(add.read_text(encoding="utf-8", errors="replace"))
            return out

        async with deps.tracer.step(
            agent="report", tool="report:assemble", model=None
        ) as astep:
            astep.record_args(
                {
                    "frame_count": len(frames),
                    "referenced_keys": sorted(referenced),
                }
            )
            macros = await asyncio.to_thread(_read_macros)
            await asyncio.to_thread(stage_inventory, used, figures_dir)
            tex = assemble_deck(
                AssembleInput(
                    title=outline.title,
                    theme=_THEME,
                    additional_tex_macros=macros,
                    # The staged figures dir is the single graphicspath root;
                    # \includegraphics{<key>} resolves to figures/<key>.<ext>.
                    cache_source_dirs=[figures_dir.as_posix()],
                    frames=frames,
                )
            )
            astep.record_result(
                {
                    "staged_keys": [f.key for f in used],
                    "macro_blocks": len(macros),
                }
            )
        await _flush_steps()

        # ---- sl_verify_figures: deterministic no-hallucination guard ----
        async with deps.tracer.step(
            agent="report", tool="report:verify_figures", model=None
        ) as vstep:
            vstep.record_args({"allowed_keys": sorted(inv_keys)})
            tex, rejected = verify_and_fix_graphics(tex, allowed_keys=inv_keys)
            vstep.record_result({"rejected": rejected})
        await _flush_steps()

        # ---- sl_compile: Overfull-aware revise loop ----
        async def _revise(log: str, cur_tex: str) -> str:
            return await revise_tex(
                pdflatex_log=log,
                tex=cur_tex,
                adapter=deps.adapter,
                tracer=deps.tracer,
                model=deps.section_model,
            )

        async with deps.tracer.step(
            agent="report", tool="report:compile", model=None
        ) as cstep:
            cstep.record_args({"frame_count": len(frames)})
            result = await compile_mod.compile_with_revise(
                tex=tex,
                workdir=slides_dir,
                tex_name="deck.tex",
                revise=_revise,
                max_retries=2,
            )
            cstep.record_result(
                {
                    "ok": result.ok,
                    "attempts": result.attempts,
                    "page_count": result.page_count,
                    "log_tail": result.log[-500:] if not result.ok else "",
                }
            )
            if not result.ok:
                cstep.mark_error("deck failed to compile after retries")
        # The compile loop may emit several report:revise rows; flush them all.
        await _flush_steps()

        # F4: notes are opt-in, authored by a later sub-flow — not produced here.
        notes: dict[str, str] = {}

        # persist notes file + version snapshot (blocking IO off the loop).
        def _persist() -> None:
            slides_dir.mkdir(parents=True, exist_ok=True)
            (slides_dir / "speaker_notes.json").write_text(
                json.dumps({}, ensure_ascii=False), encoding="utf-8"
            )
            if result.ok:
                VersionHistory(str(slides_dir)).save_version(
                    result.tex, "Generated deck (slides only)", {}
                )

        await asyncio.to_thread(_persist)

        await upsert_deck(
            deps.conn,
            session_id=state["session_id"],
            run_id=state.get("run_id"),
            tex_path=str(slides_dir / "deck.tex"),
            pdf_path=str(slides_dir / "deck.pdf") if result.ok else None,
            speaker_notes=notes,
            plan=outline.model_dump(),
            page_count=result.page_count,
            theme=_THEME,
            contributing_paper_ids=[p["id"] for p in papers],
            status="ok" if result.ok else "error",
        )
        deck = await get_deck(deps.conn, session_id=state["session_id"])
        assert deck is not None

        # ---- write per-frame deck_slides rows (F4) ----
        if result.ok:
            await replace_deck_slides(
                deps.conn,
                deck_id=deck.id,
                slides=build_deck_slides(result.tex, result.page_count),
            )

        # ---- sl_emit: deck event + row (UNCHANGED shape from F1) ----
        async with deps.tracer.step(
            agent="report", tool="report:emit", model=None
        ) as estep:
            estep.record_args({"deck_id": deck.id})
            estep.record_result(
                {"page_count": deck.page_count, "status": deck.status}
            )
        # Stream the emit row too so the Trace panel shows every stage before
        # the deck chip lands (chat.py's outer drain dedupes any straggler).
        await _flush_steps()

        _emit_deck(
            writer,
            deck,
            outline.title,
            [{"id": p["id"], "title": p["title"]} for p in papers],
            has_notes=bool(notes),
        )

        final = (
            f'Generated a {deck.page_count}-slide deck — "{outline.title}". '
            "Want speaker notes? Say \"generate speaker notes\" (you can pick a "
            "language). I can also edit any slide — just tell me the page."
            if result.ok
            else (
                "I generated the deck but it failed to compile after retries — "
                "showing the last attempt. Check the Trace panel for the LaTeX error."
            )
        )
        return {**state, "final_response": final, "report_deck_id": deck.id}

    async def _notes(state: AgentState) -> AgentState:
        """generate_notes / edit_notes: author or rewrite speaker notes for the
        target frame(s). NEVER recompiles and NEVER touches the frame LaTeX."""
        writer, _flush_steps = _streaming(state)
        cmd: DeckCommand = state["report_command"]
        papers: list[dict[str, Any]] = state["report_papers"]
        papers_meta = [{"id": p["id"], "title": p["title"]} for p in papers]

        deck = await get_deck(deps.conn, session_id=state["session_id"])
        assert deck is not None
        rows = await get_deck_slides(deps.conn, deck_id=deck.id)
        lang = cmd.note_language or response_language(state)
        targets = _select_rows(
            rows, cmd, current_view_page=state.get("current_view_page") or 1
        )

        if not targets:
            return {
                **state,
                "final_response": (
                    "I couldn't find that slide page in the deck. "
                    "Tell me a page number that exists."
                ),
            }

        for r in targets:
            note = await author_note(
                adapter=deps.adapter,
                tracer=deps.tracer,
                model=deps.notes_model,
                frame_tex=r.frame_tex,
                existing_note=r.note_text if cmd.action == "edit_notes" else None,
                instruction=(
                    state.get("user_message") if cmd.action == "edit_notes" else None
                ),
                note_language=lang,
            )
            await update_slide_note(
                deps.conn,
                deck_id=deck.id,
                slide_index=r.slide_index,
                note_text=note,
                note_language=lang,
            )
            await _flush_steps()

        notes = await rebuild_speaker_notes_json(deps.conn, deck_id=deck.id)
        # Mirror the on-disk speaker_notes.json so a later version snapshot is
        # consistent with the DB (DB remains authoritative via the rebuild).
        slides_dir = (
            deps.workspace / "chat_session" / str(state["session_id"]) / "slides"
        )

        def _persist_notes() -> None:
            slides_dir.mkdir(parents=True, exist_ok=True)
            (slides_dir / "speaker_notes.json").write_text(
                json.dumps(notes, ensure_ascii=False), encoding="utf-8"
            )

        await asyncio.to_thread(_persist_notes)

        fresh = await get_deck(deps.conn, session_id=state["session_id"])
        assert fresh is not None
        _emit_deck(writer, fresh, _deck_title(fresh), papers_meta, has_notes=bool(notes))
        await _flush_steps()

        verb = "Wrote" if cmd.action == "generate_notes" else "Updated"
        return {
            **state,
            "final_response": (
                f"{verb} speaker notes ({lang}). "
                "Open the Slides panel to read them."
            ),
            "report_deck_id": deck.id,
        }

    async def _edit_slides(state: AgentState) -> AgentState:
        """edit_slides: rewrite the targeted frame(s), recompile (Overfull-aware),
        and PRESERVE speaker notes by slide_index across the rebuild."""
        writer, _flush_steps = _streaming(state)
        cmd: DeckCommand = state["report_command"]
        papers: list[dict[str, Any]] = state["report_papers"]
        papers_meta = [{"id": p["id"], "title": p["title"]} for p in papers]

        deck = await get_deck(deps.conn, session_id=state["session_id"])
        assert deck is not None
        rows = await get_deck_slides(deps.conn, deck_id=deck.id)
        old_notes = {
            r.slide_index: (r.note_text, r.note_language) for r in rows
        }

        slides_dir = (
            deps.workspace / "chat_session" / str(state["session_id"]) / "slides"
        )
        if not Path(deck.tex_path).exists():  # noqa: ASYNC240 — fast metadata check before the to_thread read
            return {
                **state,
                "final_response": (
                    "I couldn't find the deck source to edit — "
                    "generate the deck again first."
                ),
                "report_deck_id": deck.id,
            }
        full_tex = await asyncio.to_thread(
            Path(deck.tex_path).read_text, encoding="utf-8"
        )
        targets = _select_rows(
            rows, cmd, current_view_page=state.get("current_view_page") or 1
        )
        if not targets:
            _emit_deck(
                writer, deck, _deck_title(deck), papers_meta,
                has_notes=bool(deck.speaker_notes),
            )
            return {
                **state,
                "final_response": (
                    "I couldn't find that slide page in the deck. "
                    "Tell me a page number that exists."
                ),
                "report_deck_id": deck.id,
            }

        new_tex = full_tex
        lang = response_language(state)
        # edit_frame returns exactly one frame per call (its prompt forbids
        # splitting), so slide_index→frame_number stays stable across the loop.
        for r in targets:
            new_frame = await edit_frame(
                adapter=deps.adapter,
                tracer=deps.tracer,
                model=deps.section_model,
                frame_tex=r.frame_tex,
                instruction=state.get("user_message", ""),
                response_language=lang,
            )
            frame_no = _real_frame_number(new_tex, r.slide_index)
            if frame_no is not None:
                replaced = replace_frame_in_beamer(new_tex, frame_no, new_frame)
                if replaced:
                    new_tex = replaced
            await _flush_steps()

        # ---- recompile (same verify + Overfull-aware revise as _generate) ----
        allowed = await _inventory_keys(papers)
        tex2, _rej = verify_and_fix_graphics(new_tex, allowed_keys=allowed)

        async def _revise(log: str, cur_tex: str) -> str:
            return await revise_tex(
                pdflatex_log=log,
                tex=cur_tex,
                adapter=deps.adapter,
                tracer=deps.tracer,
                model=deps.section_model,
            )

        async with deps.tracer.step(
            agent="report", tool="report:compile", model=None
        ) as cstep:
            cstep.record_args({"edited_slides": [r.slide_index for r in targets]})
            result = await compile_mod.compile_with_revise(
                tex=tex2,
                workdir=slides_dir,
                tex_name="deck.tex",
                revise=_revise,
                max_retries=2,
            )
            cstep.record_result(
                {
                    "ok": result.ok,
                    "attempts": result.attempts,
                    "page_count": result.page_count,
                    "log_tail": result.log[-500:] if not result.ok else "",
                }
            )
            if not result.ok:
                cstep.mark_error("edited deck failed to compile after retries")
        await _flush_steps()

        # version snapshot (blocking IO off the loop) — only when it compiled.
        def _persist() -> None:
            if result.ok:
                VersionHistory(str(slides_dir)).save_version(
                    result.tex, "Edited deck", {}
                )

        await asyncio.to_thread(_persist)

        await upsert_deck(
            deps.conn,
            session_id=state["session_id"],
            run_id=state.get("run_id"),
            tex_path=str(slides_dir / "deck.tex"),
            pdf_path=str(slides_dir / "deck.pdf") if result.ok else None,
            speaker_notes=deck.speaker_notes,
            plan=deck.plan,
            page_count=result.page_count,
            theme=deck.theme,
            contributing_paper_ids=deck.contributing_paper_ids,
            status="ok" if result.ok else "error",
        )
        fresh = await get_deck(deps.conn, session_id=state["session_id"])
        assert fresh is not None

        if result.ok:
            await replace_deck_slides(
                deps.conn,
                deck_id=fresh.id,
                slides=build_deck_slides(result.tex, result.page_count),
            )
            # restore notes onto the matching slide_index, then rebuild the map.
            for r in await get_deck_slides(deps.conn, deck_id=fresh.id):
                nt, nl = old_notes.get(r.slide_index, (None, None))
                if nt is not None:
                    await update_slide_note(
                        deps.conn,
                        deck_id=fresh.id,
                        slide_index=r.slide_index,
                        note_text=nt,
                        note_language=nl or "",
                    )
            await rebuild_speaker_notes_json(deps.conn, deck_id=fresh.id)

        fresh = await get_deck(deps.conn, session_id=state["session_id"])
        assert fresh is not None
        notes = fresh.speaker_notes
        _emit_deck(
            writer, fresh, _deck_title(fresh), papers_meta, has_notes=bool(notes)
        )
        await _flush_steps()

        msg = (
            "Edited the deck and recompiled."
            if result.ok
            else (
                "Edited the deck but it failed to compile — "
                "showing the last attempt."
            )
        )
        return {**state, "final_response": msg, "report_deck_id": fresh.id}

    g: StateGraph[AgentState, Any] = StateGraph(AgentState)
    g.add_node("sl_resolve", _resolve)
    g.add_node("sl_empty", _empty)
    g.add_node("sl_no_latex", _no_latex)
    g.add_node("sl_generate", _generate)
    g.add_node("sl_notes", _notes)
    g.add_node("sl_edit_slides", _edit_slides)
    g.add_edge(START, "sl_resolve")
    g.add_conditional_edges(
        "sl_resolve",
        _route,
        {
            "empty": "sl_empty",
            "no_latex": "sl_no_latex",
            "create": "sl_generate",
            "notes": "sl_notes",
            "edit_slides": "sl_edit_slides",
        },
    )
    g.add_edge("sl_empty", END)
    g.add_edge("sl_no_latex", END)
    g.add_edge("sl_generate", END)
    g.add_edge("sl_notes", END)
    g.add_edge("sl_edit_slides", END)
    return g.compile()
