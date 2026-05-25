"""Report Agent subgraph (Plan F3 — PhD-grade slide topology, SRS v2.19).

START → sl_resolve → {empty | no_latex | create}; the create path runs

    sl_understand → sl_narrate → sl_draft → sl_coherence → sl_assemble
    → sl_verify_figures → sl_compile → sl_notes_finalize → sl_emit → END

It consumes F2's ``PaperAsset`` (figures+captions, equations, sections) per
enabled paper, builds a deck-wide collision-free figure inventory, drafts
concise slide+note pairs grounded in retrieved chunks, deterministically
rejects any non-inventory figure (the hard no-hallucination guarantee), and
compiles with an Overfull-aware revise loop. The ``deck`` SSE event + the
``decks`` row shape are unchanged from F1.
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
    coherence_pass,
    draft_slide,
    finalize_notes,
    narrate_talk,
    revise_tex,
    understand_paper,
)
from paperhub.agents.state import response_language
from paperhub.db.decks import get_deck, upsert_deck
from paperhub.db.tool_calls import drain_tool_calls_since
from paperhub.llm.adapter import LlmAdapter
from paperhub.models.domain import AgentState, OutlineSlide, PaperBrief, SlideDraft
from paperhub.pipelines.paper_asset import read_paper_asset
from paperhub.pipelines.slide_pipeline import compile as compile_mod
from paperhub.pipelines.slide_pipeline.assemble import AssembleInput, assemble_deck
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
        return {**state, "report_papers": papers}

    def _route(state: AgentState) -> str:
        papers = state.get("report_papers")
        if not papers:
            return "empty"
        if not _pdflatex_available():
            return "no_latex"
        return "create"

    async def _empty(state: AgentState) -> AgentState:
        return {**state, "final_response": _EMPTY_MSG}

    async def _no_latex(state: AgentState) -> AgentState:
        return {**state, "final_response": _NO_LATEX_MSG}

    async def _generate(state: AgentState) -> AgentState:
        # get_stream_writer() returns a no-op outside an ``astream`` context
        # (e.g. ``.ainvoke`` in tests); if it raises, fall back to a no-op so
        # the non-streaming path still runs and produces the deck.
        writer: Any
        try:
            writer = get_stream_writer()
        except Exception:
            writer = None

        run_id = state.get("run_id")
        last_emitted = -1

        async def _flush_steps() -> None:
            """Emit each newly-written tool_calls row as a ``tool_step`` custom
            event so the Trace panel streams live (per-stage), not just at the
            end. The Tracer commits each row before its ``step`` block exits, so
            the row is readable here. No-op when there's no stream writer."""
            nonlocal last_emitted
            if writer is None or run_id is None:
                return
            recs = await drain_tool_calls_since(deps.conn, run_id, last_emitted)
            for rec in recs:
                writer({"event": "tool_step", "record": rec})
                last_emitted = rec["step_index"]

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
        )
        # Defensively drop any figure_key not in the deck inventory.
        slides: list[OutlineSlide] = []
        for s in outline.slides:
            if s.figure_key and s.figure_key not in inv_keys:
                s = s.model_copy(update={"figure_key": None})
            slides.append(s)
        await _flush_steps()

        # ---- sl_draft: per-slide frame+note pairs (fan-out, IN ORDER) ----
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

        drafts: list[SlideDraft] = list(
            await asyncio.gather(
                *[
                    draft_slide(
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

        # ---- sl_notes_finalize: layout-aware per-page notes (F3 T9) ----
        # Maps each PDF page to its logical slide (from the FINAL compiled tex)
        # and splits a frame's note into K coherent segments when the compile
        # loop spread it across K pages. Self-traced as report:notes_finalize.
        notes = (
            await finalize_notes(
                drafts=drafts,
                final_tex=result.tex,
                page_count=result.page_count,
                adapter=deps.adapter,
                tracer=deps.tracer,
                model=deps.notes_model,
                response_language=lang,
            )
            if result.ok
            else {}
        )
        await _flush_steps()

        # persist notes file + version snapshot (blocking IO off the loop).
        def _persist() -> None:
            slides_dir.mkdir(parents=True, exist_ok=True)
            (slides_dir / "speaker_notes.json").write_text(
                json.dumps(notes, ensure_ascii=False), encoding="utf-8"
            )
            if result.ok:
                VersionHistory(str(slides_dir)).save_version(
                    result.tex, "Generated deck", notes
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

        deck_event = {
            "event": "deck",
            "deck": {
                "deck_id": deck.id,
                "session_id": deck.session_id,
                "page_count": deck.page_count,
                "title": outline.title,
                "status": deck.status,
                "contributing_papers": [
                    {"id": p["id"], "title": p["title"]} for p in papers
                ],
                "has_notes": bool(notes),
            },
        }
        if writer is not None:
            writer(deck_event)

        final = (
            f'Generated a {deck.page_count}-slide deck — "{outline.title}".'
            if result.ok
            else (
                "I generated the deck but it failed to compile after retries — "
                "showing the last attempt. "
                "Check the Trace panel for the LaTeX error."
            )
        )
        return {**state, "final_response": final, "report_deck_id": deck.id}

    g: StateGraph[AgentState, Any] = StateGraph(AgentState)
    g.add_node("sl_resolve", _resolve)
    g.add_node("sl_empty", _empty)
    g.add_node("sl_no_latex", _no_latex)
    g.add_node("sl_generate", _generate)
    g.add_edge(START, "sl_resolve")
    g.add_conditional_edges(
        "sl_resolve",
        _route,
        {"empty": "sl_empty", "no_latex": "sl_no_latex", "create": "sl_generate"},
    )
    g.add_edge("sl_empty", END)
    g.add_edge("sl_no_latex", END)
    g.add_edge("sl_generate", END)
    return g.compile()
