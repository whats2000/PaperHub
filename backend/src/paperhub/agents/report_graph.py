"""Report Agent subgraph (Plan F4.5 — flat monolithic-agent topology, SRS v2.25+).

GENERATE path. F4.5 collapsed the R1 7-node subgraph
(``sl_paper_brief → sl_plan_deck → sl_render_slide → sl_coherence →
sl_assemble → sl_verify_figures → sl_compile``) into a flat 3-step
orchestrator: ``gather_context`` (fan-out per paper) → ``slide_agent``
(monolithic agentic loop owning ``initial_draft``/``compile_check``/
``replace_frame``/``replace_preamble``) → ``sl_emit`` (deterministic figure
audit + deck/deck_slides persistence + version snapshot).

The session-scoped Beamer preamble is resolved via
``style_resolver.resolve_preamble`` (session override → global memory →
default file); the slide_agent's ``replace_preamble`` tool may persist a
new one back to ``slide_style_overrides``. sl_emit enforces the hard
no-hallucinated-figures contract by running ``verify_and_fix_graphics``
deterministically on every emitted deck — unknown ``\\includegraphics``
keys become ``\\textit{[figure omitted]}``.

NOTES + EDIT sub-flows (``_notes`` / ``_edit_slides`` / ``_edit_title`` /
``_edit_preamble``) are F4 surfaces preserved as-is — they target an
already-emitted deck.

The R1 modules (``sl_paper_brief`` / ``sl_plan_deck`` / ``sl_render_slide``
/ R1 ``coherence_pass`` / ``assemble_deck`` / R1 schemas) are no longer
wired here; Phase 14 of the F4.5 plan deletes them outright.
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

from paperhub.agents.gather_context import run_gather_context
from paperhub.agents.memory_recall import build_active_memory_block
from paperhub.agents.report_pipeline import (
    author_note,
    classify_deck_command,
    detect_slide_language,
    edit_frame,
    edit_preamble_block,
    edit_title_block,
    parse_slide_budget,
    revise_tex,
)
from paperhub.agents.sl_emit import run_sl_emit
from paperhub.agents.slide_agent import run_slide_agent
from paperhub.agents.state import effective_query, response_language
from paperhub.agents.style_resolver import resolve_preamble
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
)
from paperhub.models.slide_domain import KeyFigureBundle, PaperContextBundle
from paperhub.pipelines.paper_asset import PaperAsset, read_paper_asset
from paperhub.pipelines.slide_pipeline import compile as compile_mod
from paperhub.pipelines.slide_pipeline.beamer_helpers import (
    extract_frames_from_beamer,
    get_preamble,
    is_title_frame,
    replace_frame_in_beamer,
    replace_preamble,
)
from paperhub.pipelines.slide_pipeline.deck_slides_map import build_deck_slides
from paperhub.pipelines.slide_pipeline.figure_inventory import (
    build_inventory,
    verify_and_fix_graphics,
)
from paperhub.pipelines.slide_pipeline.history import VersionHistory
from paperhub.tracing.tracer import Tracer

# F4.5: only used by the NOTES/EDIT sub-flows' deck staging needs; the
# slide_agent owns figure-key audit internally + sl_emit re-audits.
_GRAPHICS_RE = re.compile(r"\\includegraphics(\[[^\]]*\])?\{([^}]+)\}")

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
    """Map a 0-based content ``slide_index`` to the 1-based frame number
    ``replace_frame_in_beamer`` expects, skipping a leading title frame
    (synthetic ``\\maketitle`` or a real ``\\titlepage`` frame)."""
    real = 0
    seen: set[int] = set()
    for i, (num, content, _s, _e) in enumerate(extract_frames_from_beamer(full_tex)):
        if i == 0 and is_title_frame(content):
            continue
        if num in seen:
            continue
        seen.add(num)
        if real == slide_index:
            return num
        real += 1
    return None


def _slide_language(state: AgentState) -> str:
    """Language for the SLIDE CONTENT: an explicit task request
    (``report_slide_language``, set by ``detect_slide_language``) wins, else the
    chat-reply language. So "把簡報換成英文" yields an English deck even though the
    user typed in Chinese, while a bare request stays in the user's language."""
    return state.get("report_slide_language") or response_language(state)


def _select_rows(
    rows: list[DeckSlideRow], cmd: DeckCommand, *, current_view_page: int
) -> list[DeckSlideRow]:
    """Select the deck_slides rows a NOTES/EDIT command targets.

    - ``all``     → every row.
    - ``current`` → the single row whose [page_start, page_end] contains the
      page on screen (fallback: the first row).
    - ``page``    → the row containing ``cmd.target_page``; if the classifier
      chose page-scope but couldn't extract an explicit number (e.g. the
      Chinese ordinal "第三頁"), fall back to the on-screen page. Empty list
      only if neither resolves to a real row (the caller surfaces "page not
      found").
    Pure: ``current_view_page`` is passed in, not read from state."""
    if cmd.target_scope == "all":
        return list(rows)
    if cmd.target_scope == "current":
        for r in rows:
            if r.page_start <= current_view_page <= r.page_end:
                return [r]
        return rows[:1]
    # page — explicit target_page, else fall back to the on-screen page.
    page = cmd.target_page if cmd.target_page is not None else current_view_page
    for r in rows:
        if r.page_start <= page <= r.page_end:
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
    # change. The F4.5 flat flow maps them: plan_model → slide_agent,
    # section_model → slide_agent revise + edit_frame, notes_model →
    # gather_context + author_note, resolve_model → classifier/budget.
    plan_model: str
    section_model: str
    notes_model: str
    resolve_model: str
    recall_enabled: bool = field(default=True)
    # F4.5: the Beamer preamble is no longer a named profile — it is resolved
    # per turn via ``style_resolver.resolve_preamble`` (session override →
    # global memory → default file). Field kept for backward compat with
    # ``chat.py`` (still passed by callers) but unused by the F4.5 generate
    # path. Marked optional + defaulted so test harnesses can omit it.
    slide_style_profile_name: str = field(default="default")


async def _enabled_papers(
    conn: aiosqlite.Connection, session_id: int
) -> list[dict[str, Any]]:
    async with conn.execute(
        "SELECT pc.id, pc.title, pc.abstract, pc.sections_json, pc.source_dir_path, "
        "pc.authors_json, pc.year, pc.arxiv_id, pc.kind "
        "FROM papers p JOIN paper_content pc ON pc.id = p.paper_content_id "
        "WHERE p.session_id = ? AND p.enabled = 1 ORDER BY p.added_at",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            authors = list(json.loads(r[5] or "[]"))
        except (ValueError, TypeError):
            authors = []
        out.append({
            "id": r[0], "title": r[1], "abstract": r[2],
            "sections_json": r[3], "source_dir": r[4],
            "authors": [str(a) for a in authors],
            "year": r[6], "arxiv_id": r[7], "kind": r[8],
        })
    return out


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
            # GENERATE: detect an explicit slide-content language (else fall
            # back to response_language downstream) + the length budget.
            lang = await detect_slide_language(
                adapter=deps.adapter,
                tracer=deps.tracer,
                model=deps.resolve_model,
                instruction=instruction,
            )
            if lang:
                out["report_slide_language"] = lang
            out["report_budget"] = parse_slide_budget(instruction)
            return out

        rows = await get_deck_slides(deps.conn, deck_id=deck.id)
        outline = "\n".join(
            f"{r.page_start}. {_frame_title(r.frame_tex)}" for r in rows
        ) or "(no slides)"
        # Deck-scoped follow-up: classify the action AND detect a slide-content
        # language request concurrently (both read only the instruction).
        cmd, lang = await asyncio.gather(
            classify_deck_command(
                adapter=deps.adapter,
                tracer=deps.tracer,
                model=deps.resolve_model,
                instruction=instruction,
                current_view_page=state.get("current_view_page") or 1,
                deck_outline=outline,
            ),
            detect_slide_language(
                adapter=deps.adapter,
                tracer=deps.tracer,
                model=deps.resolve_model,
                instruction=instruction,
            ),
        )
        # Page 1 is the title page (no content slide row). An edit_slides command
        # that resolves to page 1 is really a title-page edit (F4.2).
        if cmd.action == "edit_slides":
            cvp = state.get("current_view_page") or 1
            tgt = cmd.target_page if cmd.target_scope == "page" else (
                cvp if cmd.target_scope == "current" else None
            )
            content_rows = [r for r in rows if not is_title_frame(r.frame_tex)]
            first_content_page = min((r.page_start for r in content_rows), default=2)
            if tgt is not None and tgt < first_content_page:
                cmd = cmd.model_copy(update={"action": "edit_title"})
        out["report_command"] = cmd
        if lang:
            out["report_slide_language"] = lang
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
        if cmd.action == "edit_title":
            return "edit_title"
        if cmd.action == "edit_preamble":
            return "edit_preamble"
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
        # Set-based dedup (NOT a monotonic watermark): the tracer assigns
        # step_index at OPEN time but commits at CLOSE time, so a fan-out task
        # that opened first (low index) can commit AFTER a sibling's higher
        # index. A monotonic watermark would advance past the low index before
        # it was read, permanently dropping that row from the stream. The set +
        # lock is robust against any commit-order interleaving and against
        # concurrent _flush_steps calls from sibling gather tasks. Same fix as
        # research_graph's ``_ps_process._emit_progress``.
        emitted_indices: set[int] = set()
        drain_lock = asyncio.Lock()

        async def _flush_steps() -> None:
            if writer is None or run_id is None:
                return
            async with drain_lock:
                recs = await drain_tool_calls_since(deps.conn, run_id, -1)
                for rec in recs:
                    if rec["step_index"] not in emitted_indices:
                        writer({"event": "tool_step", "record": rec})
                        emitted_indices.add(rec["step_index"])

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
        """Best-effort deck title from the persisted plan.

        Handles both the OLD ``TalkOutline`` shape (``title``) and the new
        ``DeckOutline`` shape (``talk_title``) so a deck generated by the
        F4.4 Round 1 chain replays cleanly alongside any pre-existing one.
        """
        plan = deck.plan or {}
        return str(plan.get("talk_title") or plan.get("title") or "Slides")

    async def _generate(state: AgentState) -> AgentState:
        """F4.5 flat 3-step orchestrator.

        Stage 1: ``gather_context`` (fan-out per enabled paper) — each call
            navigates ONE paper's F2 ``PaperAsset`` via bounded
            ``list_sections`` / ``read_section`` / ``read_figure_block``
            callbacks and emits a :class:`PaperContextBundle`
            (narrative + grounded asset inventory + dimension-probed figures).

        Stage 2: ``slide_agent`` (single monolithic agentic loop) — sees ALL
            bundles + the resolved Beamer preamble + the deck-wide figure
            inventory. Owns ``initial_draft`` / ``compile_check`` /
            ``replace_frame`` / ``replace_preamble`` / ``done`` tools; iterates
            until ``done(satisfied=True)`` or the tool-call budget is spent.

        Stage 3: ``sl_emit`` — deterministic ``verify_and_fix_graphics``
            (HARD CONTRACT: unknown figure keys become
            ``\\textit{[figure omitted]}``), persists ``decks`` +
            ``deck_slides`` rows, writes ``edit_history/version_*.json``,
            emits the ``deck`` SSE event.
        """
        writer, _flush_steps = _streaming(state)

        async def _then_flush(coro: Any) -> Any:
            """Await one fan-out task, then drain+stream its tool_step the
            instant it closes — so a finished bundle surfaces live rather
            than being batched until the whole ``gather`` resolves."""
            result = await coro
            await _flush_steps()
            return result

        papers: list[dict[str, Any]] = state["report_papers"]
        lang = _slide_language(state)
        # Active memory recall per the SRS v2.17 + CLAUDE.md contract — flows
        # into the slide_agent prompt so a remembered "always Traditional
        # Chinese" steers frame text + headings + bullets.
        _mem = ""
        if deps.recall_enabled:
            _mem = await build_active_memory_block(
                deps.conn, session_id=state.get("session_id")
            )

        # ---- Stage 1: gather_context fan-out ----
        # For each enabled paper, load the F2 PaperAsset + the paper-row
        # metadata + ADDITIONAL.tex macros, then call run_gather_context.
        # Papers whose asset is missing/unreadable are skipped (no usable
        # F2 ingest → cannot ground figures/equations safely).
        async def _gather_one(idx: int, p: dict[str, Any]) -> PaperContextBundle | None:
            source_dir_raw = p.get("source_dir")
            if not source_dir_raw:
                return None
            source_dir = Path(str(source_dir_raw))
            if not source_dir.exists():  # noqa: ASYNC240 — fast metadata check before the to_thread read
                return None

            asset: PaperAsset | None = await asyncio.to_thread(
                read_paper_asset, source_dir
            )
            if asset is None:
                return None

            # ADDITIONAL.tex macros from the paper's source dir (arXiv LaTeX
            # path; PDF-only papers have no macros file → empty list).
            def _read_macros() -> list[str]:
                add = source_dir / "ADDITIONAL.tex"
                if not add.exists():
                    return []
                raw = add.read_text(encoding="utf-8", errors="replace")
                return [
                    ln for ln in raw.splitlines()
                    if ln.strip().startswith(("\\newcommand", "\\providecommand"))
                ]

            paper_newcommands = await asyncio.to_thread(_read_macros)

            return await run_gather_context(
                paper_id=int(p["id"]),
                paper_idx=idx,
                source_dir=source_dir,
                paper_title=str(p["title"] or ""),
                paper_authors=list(p.get("authors") or []),
                paper_year=p.get("year"),
                paper_abstract=str(p.get("abstract") or ""),
                paper_newcommands=paper_newcommands,
                asset=asset,
                conn=deps.conn,
                tracer=deps.tracer,
                model=deps.notes_model,
                response_language=lang,
            )

        gathered: list[PaperContextBundle | None] = list(
            await asyncio.gather(
                *[_then_flush(_gather_one(idx, p)) for idx, p in enumerate(papers)]
            )
        )
        bundles: list[PaperContextBundle] = [b for b in gathered if b is not None]
        await _flush_steps()

        if not bundles:
            return {
                **state,
                "final_response": (
                    "I couldn't load a usable PaperAsset for any enabled paper. "
                    "Re-ingest the paper(s) and try again."
                ),
            }

        # Deck-wide figure inventory from the bundles (each bundle's
        # key_figures are already namespaced by paper_idx via the
        # gather_context formatter, so no collision risk).
        figure_inventory: dict[str, KeyFigureBundle] = {}
        for b in bundles:
            for f in b.key_figures:
                figure_inventory[f.key] = f

        # ---- Stage 2: slide_agent (monolithic agentic loop) ----
        slides_dir = (
            deps.workspace / "chat_session" / str(state["session_id"]) / "slides"
        )
        await asyncio.to_thread(lambda: slides_dir.mkdir(parents=True, exist_ok=True))

        resolved_preamble = await resolve_preamble(
            session_id=int(state["session_id"]), conn=deps.conn
        )

        agent_result = await run_slide_agent(
            bundles=bundles,
            task_description=effective_query(state) or state.get("user_message", ""),
            response_language=lang,
            resolved_preamble=resolved_preamble,
            workdir=slides_dir,
            existing_deck_tex=None,  # GENERATE — no prior deck content
            figure_inventory=figure_inventory,
            memory_context=_mem,
            tracer=deps.tracer,
            model=deps.plan_model,
            session_id=int(state["session_id"]),
            conn=deps.conn,
        )
        await _flush_steps()

        # ---- Stage 3: sl_emit (deterministic finalize) ----
        compile_check = agent_result.last_compile_check
        page_count = compile_check.page_count if compile_check else 0
        compile_ok = bool(compile_check and compile_check.ok)
        status = "ok" if (agent_result.satisfied and compile_ok) else "error"

        emit_result = await run_sl_emit(
            session_id=int(state["session_id"]),
            run_id=int(state.get("run_id") or 0),
            deck_tex=agent_result.deck_tex,
            workdir=slides_dir,
            page_count=page_count,
            status=status,
            contributing_paper_ids=[int(p["id"]) for p in papers],
            figure_inventory=figure_inventory,
            conn=deps.conn,
        )
        await _flush_steps()

        # Pull the persisted row back so the SSE event mirrors the DB shape
        # the rest of the app (frontend deck chip, replay) reads.
        deck = await get_deck(deps.conn, session_id=int(state["session_id"]))
        assert deck is not None
        title = _deck_title(deck) or (papers[0]["title"] if len(papers) == 1 else "Slides")
        _emit_deck(
            writer,
            deck,
            title,
            [{"id": p["id"], "title": p["title"]} for p in papers],
            has_notes=False,  # F4: notes are an opt-in NOTES sub-flow.
        )

        if status == "ok":
            final = (
                f'Generated a {emit_result.page_count}-slide deck. '
                "Want speaker notes? Say \"generate speaker notes\" "
                "(you can pick a language). I can also edit any slide — "
                "just tell me the page."
            )
        else:
            final = (
                "I shipped the deck but it didn't fully converge "
                f"(slide_agent used {agent_result.tool_calls_used} tool calls). "
                "Check the Trace panel for the last compile_check signals."
            )
        return {**state, "final_response": final, "report_deck_id": emit_result.deck_id}

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

    async def _recompile_and_emit(
        state: AgentState,
        writer: Any,
        _flush_steps: Any,
        deck: Any,
        papers: list[dict[str, Any]],
        papers_meta: list[dict[str, Any]],
        old_notes: dict[int, tuple[str | None, str | None]],
        new_tex: str,
        *,
        description: str,
    ) -> str:
        """Verify graphics, recompile (Overfull-aware revise loop), snapshot the
        version, persist the deck + rebuild deck_slides, restore notes by
        slide_index, and emit the deck event. Returns the final-response text.
        Shared by sl_edit_slides / sl_edit_title / sl_edit_preamble."""
        slides_dir = (
            deps.workspace / "chat_session" / str(state["session_id"]) / "slides"
        )

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
            cstep.record_args({"description": description})
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
                cstep.mark_error(f"{description} — deck failed to compile after retries")
        await _flush_steps()

        # version snapshot (blocking IO off the loop) — only when it compiled.
        def _persist() -> None:
            if result.ok:
                VersionHistory(str(slides_dir)).save_version(
                    result.tex, description, {}
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
            contributing_paper_ids=deck.contributing_paper_ids,
            status="ok" if result.ok else "error",
            current_version_id=deck.current_version_id,
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

        return (
            f"{description} and recompiled."
            if result.ok
            else f"{description} but it failed to compile — showing the last attempt."
        )

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
        lang = _slide_language(state)
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

        msg = await _recompile_and_emit(
            state, writer, _flush_steps, deck, papers, papers_meta, old_notes,
            new_tex, description="Edited deck",
        )
        fresh = await get_deck(deps.conn, session_id=state["session_id"])
        assert fresh is not None
        return {**state, "final_response": msg, "report_deck_id": fresh.id}

    async def _edit_title(state: AgentState) -> AgentState:
        """edit_title: rewrite the deck's page-1 block (preamble + title frame),
        recompile (Overfull-aware), and PRESERVE speaker notes by slide_index."""
        writer, _flush_steps = _streaming(state)
        deck = await get_deck(deps.conn, session_id=state["session_id"])
        assert deck is not None
        papers: list[dict[str, Any]] = state["report_papers"]
        papers_meta = [{"id": p["id"], "title": p["title"]} for p in papers]
        rows = await get_deck_slides(deps.conn, deck_id=deck.id)
        old_notes = {r.slide_index: (r.note_text, r.note_language) for r in rows}
        if not Path(deck.tex_path).exists():  # noqa: ASYNC240
            return {
                **state,
                "final_response": (
                    "I couldn't find the deck source to edit — "
                    "generate it again first."
                ),
                "report_deck_id": deck.id,
            }
        full_tex = await asyncio.to_thread(Path(deck.tex_path).read_text, encoding="utf-8")
        block = get_preamble(full_tex)
        if block is None:
            return {
                **state,
                "final_response": "I couldn't parse the deck's title page to edit it.",
                "report_deck_id": deck.id,
            }
        new_block = await edit_title_block(
            adapter=deps.adapter,
            tracer=deps.tracer,
            model=deps.section_model,
            page_block=block,
            instruction=state.get("user_message", ""),
            response_language=_slide_language(state),
        )
        await _flush_steps()
        new_tex = replace_preamble(full_tex, new_block) or full_tex
        msg = await _recompile_and_emit(
            state, writer, _flush_steps, deck, papers, papers_meta, old_notes,
            new_tex, description="Edited the title page",
        )
        fresh = await get_deck(deps.conn, session_id=state["session_id"])
        return {
            **state,
            "final_response": msg,
            "report_deck_id": fresh.id if fresh else deck.id,
        }

    async def _edit_preamble(state: AgentState) -> AgentState:
        """edit_preamble: restyle the deck via its preamble (theme/colors/fonts),
        recompile (Overfull-aware), and PRESERVE speaker notes by slide_index."""
        writer, _flush_steps = _streaming(state)
        deck = await get_deck(deps.conn, session_id=state["session_id"])
        assert deck is not None
        papers: list[dict[str, Any]] = state["report_papers"]
        papers_meta = [{"id": p["id"], "title": p["title"]} for p in papers]
        rows = await get_deck_slides(deps.conn, deck_id=deck.id)
        old_notes = {r.slide_index: (r.note_text, r.note_language) for r in rows}
        if not Path(deck.tex_path).exists():  # noqa: ASYNC240
            return {
                **state,
                "final_response": (
                    "I couldn't find the deck source to edit — "
                    "generate it again first."
                ),
                "report_deck_id": deck.id,
            }
        full_tex = await asyncio.to_thread(Path(deck.tex_path).read_text, encoding="utf-8")
        block = get_preamble(full_tex)
        if block is None:
            return {
                **state,
                "final_response": "I couldn't parse the deck's title page to edit it.",
                "report_deck_id": deck.id,
            }
        new_block = await edit_preamble_block(
            adapter=deps.adapter,
            tracer=deps.tracer,
            model=deps.section_model,
            page_block=block,
            instruction=state.get("user_message", ""),
            response_language=_slide_language(state),
        )
        await _flush_steps()
        new_tex = replace_preamble(full_tex, new_block) or full_tex
        msg = await _recompile_and_emit(
            state, writer, _flush_steps, deck, papers, papers_meta, old_notes,
            new_tex, description="Restyled the deck",
        )
        fresh = await get_deck(deps.conn, session_id=state["session_id"])
        return {
            **state,
            "final_response": msg,
            "report_deck_id": fresh.id if fresh else deck.id,
        }

    g: StateGraph[AgentState, Any] = StateGraph(AgentState)
    g.add_node("sl_resolve", _resolve)
    g.add_node("sl_empty", _empty)
    g.add_node("sl_no_latex", _no_latex)
    g.add_node("sl_generate", _generate)
    g.add_node("sl_notes", _notes)
    g.add_node("sl_edit_slides", _edit_slides)
    g.add_node("sl_edit_title", _edit_title)
    g.add_node("sl_edit_preamble", _edit_preamble)
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
            "edit_title": "sl_edit_title",
            "edit_preamble": "sl_edit_preamble",
        },
    )
    g.add_edge("sl_empty", END)
    g.add_edge("sl_no_latex", END)
    g.add_edge("sl_generate", END)
    g.add_edge("sl_notes", END)
    g.add_edge("sl_edit_slides", END)
    g.add_edge("sl_edit_title", END)
    g.add_edge("sl_edit_preamble", END)
    return g.compile()
