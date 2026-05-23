"""Report Agent subgraph (Plan F Phase 1 — create-only).

START → sl_resolve → {empty | no_latex | create} → sl_generate → END
"""
from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from paperhub.agents.memory_recall import build_active_memory_block
from paperhub.agents.report_pipeline import generate_notes, generate_section, plan_deck
from paperhub.agents.state import response_language
from paperhub.db.decks import get_deck, upsert_deck
from paperhub.llm.adapter import LlmAdapter
from paperhub.models.domain import AgentState, PlannedSection
from paperhub.pipelines.slide_pipeline import compile as compile_mod
from paperhub.pipelines.slide_pipeline.assemble import AssembleInput, assemble_deck
from paperhub.pipelines.slide_pipeline.figures import (
    FigureIndex,
    collect_figures,
    neutralize_unknown_graphics,
)
from paperhub.pipelines.slide_pipeline.history import VersionHistory
from paperhub.tracing.tracer import Tracer

_SECTION_CONCURRENCY = 4
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
        writer = get_stream_writer()
        papers: list[dict[str, Any]] = state["report_papers"]
        lang = response_language(state)
        mem = ""
        if deps.recall_enabled:
            mem = await build_active_memory_block(
                deps.conn, session_id=state.get("session_id")
            )

        papers_block = "\n".join(
            f"- id={p['id']} · {p['title']} · {(p['abstract'] or '')[:400]}"
            f" · sections={p['sections_json'] or '[]'}"
            for p in papers
        )
        plan = await plan_deck(
            adapter=deps.adapter,
            tracer=deps.tracer,
            model=deps.plan_model,
            papers_block=papers_block,
            response_language=lang,
            memory_context=mem,
        )

        retr = deps.retriever

        # Collect real figure dirs + stems BEFORE the section fan-out so every
        # section gets the same grounded list (avoids race / partial index).
        raw_cache_dirs = [p["source_dir"] for p in papers if p["source_dir"]]
        fig_index: FigureIndex = await asyncio.to_thread(
            collect_figures, raw_cache_dirs
        )

        async def _one_section(section: PlannedSection) -> str:
            chunks: list[Any] = []
            if retr is not None:
                chunks = retr.retrieve(
                    section.intent or section.title,
                    enabled_paper_content_ids=section.paper_content_ids,
                    corpus_size=1000,
                    top_k=6,
                )
            chunks_block = (
                "\n\n".join(c.text for c in chunks)
                or "(no retrieved chunks; use abstracts)"
            )
            return await generate_section(
                adapter=deps.adapter,
                tracer=deps.tracer,
                model=deps.section_model,
                deck_title=plan.title,
                section=section,
                chunks_block=chunks_block,
                response_language=lang,
                memory_context=mem,
                chunk_ids=[c.chunk_id for c in chunks],
                available_figures=sorted(fig_index.stems),
            )

        sem = asyncio.Semaphore(_SECTION_CONCURRENCY)

        async def _bounded(s: PlannedSection) -> str:
            async with sem:
                return await _one_section(s)

        frames = list(
            await asyncio.gather(*[_bounded(s) for s in plan.sections])
        )

        # Record figure index: dirs come from collect_figures (recursive walk,
        # forward-slashed), stems are the known-real figure basenames.
        async with deps.tracer.step(
            agent="report", tool="report:figure_path_rewrite", model=None
        ) as fstep:
            fstep.record_args({"cache_dirs": raw_cache_dirs})
            fstep.record_result(
                {"fig_dirs": fig_index.dirs, "fig_count": len(fig_index.stems)}
            )

        tex = assemble_deck(
            AssembleInput(
                title=plan.title,
                theme="metropolis",
                additional_tex_macros=[],
                cache_source_dirs=fig_index.dirs,  # recursive subdirs, posix
                frames=frames,
            )
        )

        # Safety net: replace any hallucinated \includegraphics{name} that
        # does not correspond to a real file on disk with a text placeholder,
        # guaranteeing no "File not found" fatal compile error.
        tex = neutralize_unknown_graphics(tex, fig_index.stems)

        slides_dir = (
            deps.workspace
            / "chat_session"
            / str(state["session_id"])
            / "slides"
        )

        async def _revise(log: str, cur_tex: str) -> str:
            # Phase-1 no-op stub: returns the tex unchanged.
            # A real revise prompt (slides_revise/v1) lands in Phase 2 Task 2.
            # Keep the trace step so the wiring is ready.
            async with deps.tracer.step(
                agent="report",
                tool="report:compile_revise",
                model=deps.section_model,
            ) as rstep:
                rstep.record_args({"log_tail": log[-500:]})
                rstep.record_result({"changed": False})
                return cur_tex

        async with deps.tracer.step(
            agent="report", tool="report:compile", model=None
        ) as cstep:
            cstep.record_args({"section_count": len(frames)})
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

        notes: dict[str, str] = {}
        if result.ok:
            notes = await generate_notes(
                adapter=deps.adapter,
                tracer=deps.tracer,
                model=deps.notes_model,
                beamer_code=result.tex,
                response_language=lang,
            )

        # persist notes file + version snapshot — blocking disk IO is pushed
        # to a worker thread so it never stalls the chat event loop.
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
            plan=plan.model_dump(),
            page_count=result.page_count,
            theme="metropolis",
            contributing_paper_ids=[p["id"] for p in papers],
            status="ok" if result.ok else "error",
        )
        deck = await get_deck(deps.conn, session_id=state["session_id"])
        assert deck is not None

        async with deps.tracer.step(
            agent="report", tool="report:emit", model=None
        ) as estep:
            estep.record_args({"deck_id": deck.id})
            estep.record_result({"page_count": deck.page_count, "status": deck.status})

        writer(
            {
                "event": "deck",
                "deck": {
                    "deck_id": deck.id,
                    "session_id": deck.session_id,
                    "page_count": deck.page_count,
                    "title": plan.title,
                    "status": deck.status,
                    "contributing_papers": [
                        {"id": p["id"], "title": p["title"]} for p in papers
                    ],
                    "has_notes": bool(notes),
                },
            }
        )

        final = (
            f"Generated a {deck.page_count}-slide deck — \"{plan.title}\"."
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
