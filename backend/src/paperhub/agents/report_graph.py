"""Report Agent subgraph (Plan F4.5 — flat monolithic-agent topology, SRS v2.25+).

GENERATE path. F4.5 collapsed the R1 7-node subgraph
(``sl_paper_brief → sl_plan_deck → sl_render_slide → sl_coherence →
sl_assemble → sl_verify_figures → sl_compile``) into a flat orchestrator.
F6.1-R reworked Stage 1: a cached per-section ``PaperDigest`` (small model)
+ a disk-probed figure inventory assemble a cheap ``PaperContextBundle`` per
paper (NO flagship full-paper gather); the ``sl_outline`` orchestrator then
structures the deck from the digests and fetches exact evidence via
deterministic ``read_section`` reads. Stages: digest/outline → ``slide_agent``
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
/ R1 ``coherence_pass`` / ``assemble_deck`` / R1 schemas — ``PaperBrief`` /
``OutlineSlide`` / ``TalkOutline`` / ``FrameDraft``) have been deleted in
F4.5 cleanup.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from paperhub.agents.memory_recall import build_active_memory_block
from paperhub.agents.paper_digest import get_or_build_digest
from paperhub.agents.report_pipeline import (
    author_deck_notes,
    classify_deck_command,
    detect_slide_language,
    edit_frame,
    edit_preamble_block,
    edit_title_block,
    parse_slide_budget,
    revise_tex,
)
from paperhub.agents.sl_emit import run_sl_emit
from paperhub.agents.sl_outline import run_sl_outline
from paperhub.agents.sl_read import ReadResult, read_section_chunks
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
from paperhub.models.slide_domain import (
    FigureDimensions,
    KeyEquationBundle,
    KeyFigureBundle,
    PaperContextBundle,
    PaperDigest,
    SectionExcerpt,
    SeedFigure,
)
from paperhub.pipelines.paper_asset import read_paper_asset
from paperhub.pipelines.slide_pipeline import compile as compile_mod
from paperhub.pipelines.slide_pipeline.beamer_helpers import (
    extract_frames_from_beamer,
    get_preamble,
    is_title_frame,
    replace_frame_in_beamer,
    replace_preamble,
)
from paperhub.pipelines.slide_pipeline.deck_slides_map import build_deck_slides
from paperhub.pipelines.slide_pipeline.figure_geometry import (
    probe_figure_dimensions,
)
from paperhub.pipelines.slide_pipeline.figure_inventory import (
    InventoryFigure,
    build_inventory,
    verify_and_fix_graphics,
)
from paperhub.pipelines.slide_pipeline.history import VersionHistory
from paperhub.pipelines.slide_pipeline.title_meta import build_title_metadata
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
_QA_UNAVAILABLE = (
    "I can answer questions about this slide, but the answerer isn't "
    "wired in this context. Please ask again as a normal question."
)


def _pdflatex_available() -> bool:
    """Return True if ``pdflatex`` is discoverable on PATH."""
    return bool(shutil.which("pdflatex"))


def _emit_stage(
    writer: Any,
    run_id: int | None,
    tool: str,
    *,
    elapsed_s: float = 0.0,
    step_index: int = -1,
) -> None:
    """Emit a synthetic 'stage' tool_step so the frontend trace tail keeps
    advancing during otherwise-silent phases (digest / planning / drafting /
    compile). Live-only — not persisted, not on replay. No-op without a writer."""
    if writer is None or run_id is None:
        return
    writer({"event": "tool_step", "record": {
        "run_id": run_id,
        "branch": "",
        "step_index": step_index,
        "parent_step": None,
        "agent": "report",
        "tool": tool,
        "model": "",
        "args_redacted_json": None,
        "result_summary_json": {"stage": True, "elapsed_s": round(elapsed_s, 1)},
        "latency_ms": 0,
        "token_in": 0,
        "token_out": 0,
        "status": "ok",
        "error": None,
    }})


@contextlib.asynccontextmanager
async def _stage_heartbeat(
    writer: Any,
    run_id: int | None,
    tool: str,
    *,
    every: float = 15.0,
) -> AsyncIterator[None]:
    """While the wrapped block runs, emit a ``tool`` stage event immediately and
    then every ``every`` seconds with an elapsed counter, so a long phase never
    goes silent. The beat task is cancelled + awaited on exit."""
    async def _beat() -> None:
        n = 0
        while True:
            _emit_stage(writer, run_id, tool, elapsed_s=n * every, step_index=-(n + 1))
            await asyncio.sleep(every)
            n += 1

    task = asyncio.create_task(_beat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


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


def _load_paper_context(
    slides_dir: Path, papers_fallback: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Load the persisted PaperContextBundles a previous ``_generate`` wrote
    to ``slides/context_bundles.json``. The bundles contain the same narrative
    + section excerpts (chunk text) + figure / equation metadata the
    slide_agent saw — so a notes / regen turn grounds each speaker note in
    the SAME source material the deck was built from, instead of re-fetching
    chunks or paraphrasing the abstract.

    Falls back to ``papers_fallback`` (title + abstract from the DB) when:
      * the file is absent (legacy deck pre-dating bundle persistence, or a
        deck that was restored from a snapshot without re-running generate)
      * the file is unreadable / malformed
      * the JSON is not a list
    Falling back keeps the notes flow runnable; the model gets the abstract
    instead of the rich context — degraded but not broken.
    """
    bundle_path = slides_dir / "context_bundles.json"
    if not bundle_path.exists():
        return papers_fallback
    try:
        raw = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return papers_fallback
    if not isinstance(raw, list) or not raw:
        return papers_fallback
    return raw


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
    workspace: Path
    # F1 model-tier names are reused as-is so chat.py / config.py need no
    # change. The F4.5 flat flow maps them: plan_model → slide_agent,
    # section_model → slide_agent revise + edit_frame, notes_model →
    # gather_context + author_deck_notes, resolve_model → classifier/budget.
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
    # v2.29 slide-aware QA: a qa deck-command delegates here (wired in chat.py
    # to run the paper_qa subgraph with the active-slide context). None → a
    # graceful fallback message.
    answer_slide_question: Callable[[AgentState], Awaitable[str]] | None = field(
        default=None
    )


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


async def _stage_figures(
    papers: list[dict[str, Any]], workdir: Path
) -> list[str]:
    """Copy every paper's figure files into ``workdir`` under the
    inventory-key-matching name ``p{paper_idx}-{fig.id}{src.suffix}``.

    This is what the R1 ``sl_assemble.stage_inventory`` step used to do.
    Without it, the slide_agent's ``\\includegraphics{p{idx}-{stem}}``
    resolves to a missing file at compile time and the rendered PDF
    contains placeholders instead of images (F4.5 bug: every generated
    deck silently had placeholder rectangles). The key scheme MUST match
    :func:`gather_context._format_figure_inventory_block` and
    :func:`figure_inventory.build_inventory` exactly — the slide_agent
    writes those keys verbatim into the deck.

    Degrades gracefully: a paper without ``source_dir``, an unreadable
    ``PaperAsset``, or a figure whose source file doesn't exist is
    silently skipped (matches ``probe_figure_dimensions``'s soft-fail
    posture — a missing figure simply isn't staged, the deterministic
    ``verify_and_fix_graphics`` pass then rewrites any reference to it
    as ``[figure omitted]``).

    ``papers`` items match the shape produced by ``_enabled_papers``:
    a dict with at least ``source_dir`` (string path to the
    ``paper_content`` source dir; ``paper_asset_dir(source_dir)`` is the
    ``asset/`` subtree). Iteration order = ``paper_idx`` (must match
    gather_context's enumeration).

    Returns the list of staged filenames (for the trace).
    """

    def _stage_all() -> list[str]:
        workdir.mkdir(parents=True, exist_ok=True)
        staged: list[str] = []
        for paper_idx, p in enumerate(papers):
            source_dir_raw = p.get("source_dir")
            if not source_dir_raw:
                continue
            source_dir = Path(str(source_dir_raw))
            if not source_dir.exists():
                continue
            asset = read_paper_asset(source_dir)
            if asset is None:
                continue
            for fig in asset.figures:
                src = fig.abs_image_path(source_dir)
                if not src.exists():
                    continue
                stem = fig.id or src.stem
                dest = workdir / f"p{paper_idx}-{stem}{src.suffix or '.png'}"
                shutil.copy2(src, dest)
                staged.append(dest.name)
        return staged

    return await asyncio.to_thread(_stage_all)


def _route_deck_command(state: AgentState) -> str:
    """Map a resolved deck-command state to a report-graph node name.

    qa is checked BEFORE the no_latex guard so a content question is answered
    even on a host without pdflatex. An unknown action is answered (qa), NEVER
    routed to edit_slides — that default fallback is what rewrote the slide in
    run 412.
    """
    if not state.get("report_papers"):
        return "empty"
    cmd = state.get("report_command")
    if cmd is not None and cmd.action == "qa":
        return "qa"        # qa never needs latex
    if not _pdflatex_available():
        return "no_latex"  # every other path (create/edit/notes) needs latex
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
    if cmd.action == "edit_slides":
        return "edit_slides"
    return "qa"  # unknown/unhandled action is answered, never silently edited


def build_report_subgraph(deps: ReportDeps) -> Any:
    async def _resolve(state: AgentState) -> AgentState:
        papers = await _enabled_papers(deps.conn, state["session_id"])
        out: AgentState = {**state, "report_papers": papers}
        # Guards run in _route_deck_command; only classify/budget when we will actually act.
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
                slide_attached=bool(state.get("slide_attached")),
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

    async def _sl_qa(state: AgentState) -> AgentState:
        """Answer a question about the on-screen slide via the shared paper_qa
        flow. NEVER recompiles and NEVER touches deck_slides."""
        if deps.answer_slide_question is None:
            return {**state, "final_response": _QA_UNAVAILABLE}
        return {**state, "final_response": await deps.answer_slide_question(state)}

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
                    # F4.5: which version snapshot the panel is now showing.
                    # Each per-turn DeckChip uses this to know "the version
                    # *I* produced" — the one to restore on Switch.
                    "version_id": deck.current_version_id,
                },
            }
        )

    async def _inventory_keys(papers: list[dict[str, Any]]) -> set[str]:
        """Rebuild the allowed figure-key set (same call ``_generate`` uses)."""
        inv = await asyncio.to_thread(build_inventory, papers)
        return {f.key for f in inv}


    def _deck_title(deck: Any) -> str:
        """Best-effort deck title from the persisted plan.

        Handles both the legacy outline shape (``title``) and the F4.4+
        ``DeckOutline`` shape (``talk_title``) so a deck generated by an
        earlier pipeline replays cleanly alongside any newer one.
        """
        plan = deck.plan or {}
        return str(plan.get("talk_title") or plan.get("title") or "Slides")

    async def _generate(state: AgentState) -> AgentState:
        """F6.1-R flat 3-step orchestrator.

        Stage 1 (CHEAP build): for each enabled paper, build a per-section
            :class:`PaperDigest` (cached, small model) + a disk-probed figure
            inventory, then assemble a :class:`PaperContextBundle` per paper
            from those cheap sources — NO flagship full-paper gather. The
            bundle keeps its exact interchange shape (narrative + key figures
            + equations + section excerpts + macros) so every downstream
            consumer (figure_inventory, bundle persistence/notes grounding,
            title synthesis, the drafter) works unchanged.

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

        run_id = state.get("run_id")

        # ---- Stage 1 (F6.1-R): cheap digest-derived bundles ----
        # No flagship full-paper gather. Build the PaperContextBundle
        # interchange from (a) a per-section PaperDigest (cached, small model)
        # and (b) a disk-probed figure inventory. The figure inventory + its
        # pixel-dimension probes (PIL) are kept OFF the event loop in one
        # to_thread call; the deck-namespaced keys (``p{idx}-{fig.id}``) match
        # the drafter's \includegraphics + verify_and_fix_graphics exactly.
        def _build_fig_inventory(
            paper_list: list[dict[str, Any]],
        ) -> tuple[list[InventoryFigure], dict[str, KeyFigureBundle]]:
            inv = build_inventory(paper_list)
            fig_inv: dict[str, KeyFigureBundle] = {}
            for f in inv:
                dims = probe_figure_dimensions(f.abs_path) or FigureDimensions(
                    width_px=1000, height_px=1000
                )
                fig_inv[f.key] = KeyFigureBundle(
                    key=f.key,
                    role="supporting",
                    one_line_interpretation=f.caption[:200],
                    dimensions=dims,
                )
            return inv, fig_inv

        async def _read_macros(source_dir: Path) -> list[str]:
            """ADDITIONAL.tex \\newcommand/\\providecommand lines (arXiv LaTeX
            path; PDF-only papers have no macros file → empty list). The notes
            math needs these, so they ride the cheap bundle just as gather did."""
            def _read() -> list[str]:
                add = source_dir / "ADDITIONAL.tex"
                if not add.exists():
                    return []
                raw = add.read_text(encoding="utf-8", errors="replace")
                return [
                    ln for ln in raw.splitlines()
                    if ln.strip().startswith(("\\newcommand", "\\providecommand"))
                ]

            return await asyncio.to_thread(_read)

        bundles: list[PaperContextBundle] = []
        async with _stage_heartbeat(writer, run_id, "report:reading"):
            inventory, figure_inventory = await asyncio.to_thread(
                _build_fig_inventory, papers
            )

            # Per-paper digest (full coverage of what the paper says). The
            # digest's figures are overridden from the deck-namespaced
            # inventory so the outline's figure keys match the drafter's
            # figure_inventory.
            _digests: list[PaperDigest] = []
            for idx, p in enumerate(papers):
                source_dir_raw = p.get("source_dir")
                if not source_dir_raw:
                    continue
                source_dir = Path(str(source_dir_raw))
                if not source_dir.exists():  # noqa: ASYNC240 — fast metadata check before the to_thread read
                    continue
                asset = await asyncio.to_thread(read_paper_asset, source_dir)
                if asset is None:
                    continue
                pid = int(p["id"])
                digest = await get_or_build_digest(
                    paper_id=pid,
                    conn=deps.conn,
                    asset=asset,
                    adapter=deps.adapter,
                    model=deps.section_model,
                )
                inv_figs = [f for f in inventory if f.paper_id == pid]
                digest = digest.model_copy(
                    update={
                        "figures": [
                            SeedFigure(key=f.key, caption=f.caption[:120])
                            for f in inv_figs
                        ]
                    }
                )
                _digests.append(digest)

                # Assemble the cheap interchange bundle for THIS paper.
                key_figures = [figure_inventory[f.key] for f in inv_figs]
                key_equations = [
                    KeyEquationBundle(
                        latex=e.latex, role=(e.role or "equation"), notation_legend=""
                    )
                    for e in digest.key_equations
                ]
                section_excerpts = [
                    SectionExcerpt(section_name=s.name, text=s.insight[:1000])
                    for s in digest.sections
                    if s.insight
                ]
                narrative_summary = (
                    " ".join(s.insight for s in digest.sections if s.insight)[:2000]
                    or str(p.get("abstract") or "")
                )
                paper_newcommands = await _read_macros(source_dir)
                bundles.append(
                    PaperContextBundle(
                        paper_id=pid,
                        paper_idx=idx,
                        title=str(p.get("title") or ""),
                        authors=list(p.get("authors") or []),
                        year=p.get("year"),
                        narrative_summary=narrative_summary,
                        key_figures=key_figures,
                        key_equations=key_equations,
                        section_excerpts=section_excerpts,
                        paper_newcommands=paper_newcommands,
                        read_chunk_ids=[],
                    )
                )
            await _flush_steps()

        if not bundles:
            return {
                **state,
                "final_response": (
                    "I couldn't load a usable PaperAsset for any enabled paper. "
                    "Re-ingest the paper(s) and try again."
                ),
            }

        # ---- Stage 2: slide_agent (monolithic agentic loop) ----
        slides_dir = (
            deps.workspace / "chat_session" / str(state["session_id"]) / "slides"
        )
        await asyncio.to_thread(lambda: slides_dir.mkdir(parents=True, exist_ok=True))

        # Persist the cheap digest-derived PaperContextBundles so a later
        # notes / regen turn can ground each slide's speaker note in the SAME
        # context the slide_agent saw (narrative summary + key figures +
        # equations + section excerpts), not just the bare paper abstract.
        # These are strictly richer than _load_paper_context's title+abstract
        # fallback, so the notes flow grounds on real per-section insight.
        def _persist_bundles() -> None:
            (slides_dir / "context_bundles.json").write_text(
                json.dumps(
                    [b.model_dump() for b in bundles], ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )

        await asyncio.to_thread(_persist_bundles)

        # Stage figure files into workdir under inventory-key-matching names
        # so the slide_agent's \includegraphics{p{idx}-{stem}} resolves via
        # pdflatex's default \graphicspath (= the document directory =
        # slides_dir). This replaces the deleted R1
        # ``sl_assemble.stage_inventory`` step — without it, every emitted
        # deck PDF rendered placeholders instead of figures (F4.5 bug).
        await _stage_figures(papers, slides_dir)

        resolved_preamble = await resolve_preamble(
            session_id=int(state["session_id"]), conn=deps.conn
        )

        # F4.5 fix (closes the empty-title-page bug): F4.2 wired
        # ``build_title_metadata`` into the deleted ``sl_assemble`` step so the
        # preamble had \title/\author/\date when the agent's \titlepage renders.
        # The Phase 10 rewrite dropped this; \titlepage now renders blank. Bake
        # the metadata into the resolved preamble BEFORE handing it to the
        # slide_agent so the agent's emitted \begin{frame}\titlepage\end{frame}
        # has the declarations it needs. Single paper -> paper's own
        # title/authors/arXiv-year; multi-paper -> user message as talk title
        # plus each lead-author surname.
        user_msg = effective_query(state) or state.get("user_message", "") or ""
        if len(papers) == 1:
            # Single-paper: build_title_metadata uses the paper's own title;
            # talk_title is unused.
            talk_title_arg = "Conference Talk"
        else:
            # Multi-paper: synthesize a concise talk title via a small LLM
            # call instead of dumping the user's prompt verbatim into \title{}.
            from paperhub.agents.title_synthesizer import synthesize_talk_title

            talk_title_arg = await synthesize_talk_title(
                bundles=bundles,
                user_message=user_msg,
                response_language=lang,
                model=deps.notes_model,
            )
        title_meta = build_title_metadata(
            [
                {
                    "title": p.get("title"),
                    "authors": p.get("authors") or [],
                    "year": p.get("year"),
                    "arxiv_id": p.get("arxiv_id"),
                }
                for p in papers
            ],
            talk_title=talk_title_arg,
        )
        preamble_with_title = resolved_preamble.rstrip() + "\n"
        preamble_with_title += f"\\title{{{title_meta.title}}}\n"
        if title_meta.author:
            preamble_with_title += f"\\author{{{title_meta.author}}}\n"
        if title_meta.date:
            preamble_with_title += f"\\date{{{title_meta.date}}}\n"

        # ``_digests`` (built in Stage 1 alongside the cheap bundles) feeds the
        # outline orchestrator below — each paper's per-section digest with its
        # figures already overridden from the deck-namespaced inventory.

        # Read closure — called by sl_outline when it needs a slide's exact
        # evidence. A cheap deterministic SQL fetch (no LLM); flush after each
        # so the SSE trace stays live during the orchestrator loop.
        async def _read_fn(paper_id: int, section_name: str) -> ReadResult:
            res = await read_section_chunks(
                paper_content_id=paper_id, section_name=section_name, conn=deps.conn
            )
            await _flush_steps()
            return res

        _budget = state.get("report_budget")
        _target_slides: int = (
            _budget.target_slide_count if _budget is not None else 15
        )

        async with _stage_heartbeat(writer, run_id, "report:planning"):
            outline_result = await run_sl_outline(
                digests=_digests,
                task_description=effective_query(state) or state.get("user_message", ""),
                response_language=lang,
                target_slides=_target_slides,
                adapter=deps.adapter,
                tracer=deps.tracer,
                model=deps.plan_model,
                read_fn=_read_fn,
            )
        outline = outline_result.outline
        await _flush_steps()

        async with _stage_heartbeat(writer, run_id, "report:drafting"):
            agent_result = await run_slide_agent(
                bundles=bundles,
                task_description=effective_query(state) or state.get("user_message", ""),
                response_language=lang,
                resolved_preamble=preamble_with_title,
                workdir=slides_dir,
                existing_deck_tex=None,  # GENERATE — no prior deck content
                figure_inventory=figure_inventory,
                memory_context=_mem,
                outline=outline,
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

        # sl_emit's figure-audit + center-wrap mutate deck.tex AFTER the
        # slide_agent's last compile, so deck.pdf must be regenerated or it
        # renders the pre-audit layout (figures uncentered, omitted-key
        # placeholders unreplaced). Mirror the EDIT path: recompile the audited
        # tex through the Overfull-aware revise loop. Injected as a callback so
        # sl_emit stays LLM-agnostic.
        async def _recompile(audited_tex: str) -> compile_mod.CompileResult:
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
                cstep.record_args({"description": "sl_emit recompile (post-audit)"})
                res = await compile_mod.compile_with_revise(
                    tex=audited_tex,
                    workdir=slides_dir,
                    tex_name="deck.tex",
                    revise=_revise,
                    max_retries=2,
                )
                cstep.record_result(
                    {
                        "ok": res.ok,
                        "attempts": res.attempts,
                        "page_count": res.page_count,
                        "log_tail": res.log[-500:] if not res.ok else "",
                    }
                )
                if not res.ok:
                    cstep.mark_error("sl_emit post-audit recompile failed")
            return res

        async with _stage_heartbeat(writer, run_id, "report:compiling"):
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
                recompile=_recompile,
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
        return {**state, "final_response": final, "report_deck_id": emit_result.deck_id, "report_outline": outline}

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

        # Deck-wide single-call notes author: the model sees ALL frames and
        # the source-paper context so notes have a real narrative arc
        # (foreshadow / callback) and stay grounded in the actual research.
        # ``wanted_indices`` is whichever subset of slides this turn covers;
        # ``existing_notes`` is the notes on slides we are NOT regenerating —
        # the model reads them for tone + through-line but does not touch them.
        # Title frames are skipped (they have no body content note).
        wanted_indices: list[int] = [
            r.slide_index for r in targets if not is_title_frame(r.frame_tex)
        ]
        # When edit_notes targets a subset, the surviving (non-target) notes
        # remain context. For generate_notes (no prior notes anyway) the map
        # is empty.
        target_idx_set: set[int] = set(wanted_indices)
        surviving_notes: dict[int, str] = {
            r.slide_index: r.note_text
            for r in rows
            if r.note_text and r.slide_index not in target_idx_set
        }
        frames_payload: list[tuple[int, int, str]] = [
            (r.slide_index, r.page_start, r.frame_tex)
            for r in rows
            if not is_title_frame(r.frame_tex)
        ]
        instruction = (
            state.get("user_message") if cmd.action == "edit_notes" else None
        )
        # Prefer the persisted PaperContextBundles (rich: narrative summary,
        # key figures + roles, equations + legends, section excerpts) so the
        # author grounds each note in the SAME context the slide_agent built
        # the deck from. Fall back to ``papers`` (title + abstract) on legacy
        # decks that pre-date bundle persistence.
        slides_dir = (
            deps.workspace / "chat_session" / str(state["session_id"]) / "slides"
        )
        paper_context = _load_paper_context(slides_dir, papers)
        authored = await author_deck_notes(
            adapter=deps.adapter,
            tracer=deps.tracer,
            model=deps.notes_model,
            papers=paper_context,
            frames=frames_payload,
            existing_notes=surviving_notes,
            wanted_indices=wanted_indices,
            note_language=lang,
            instruction=instruction,
        )
        for slide_index, note in authored.items():
            await update_slide_note(
                deps.conn,
                deck_id=deck.id,
                slide_index=slide_index,
                note_text=note,
                note_language=lang,
            )
        await _flush_steps()

        notes = await rebuild_speaker_notes_json(deps.conn, deck_id=deck.id)
        # ``notes`` is keyed by PDF page number (it is the SlidesPanel's
        # per-page lookup cache, written into decks.speaker_notes_json). The
        # snapshot bundle, however, is keyed by SLIDE_INDEX — sl_emit and
        # _recompile_and_emit both write it that way, and the restore
        # endpoint reads it back by slide_index. Build a slide_index-keyed
        # map from deck_slides for the snapshot so a later restore returns
        # the notes to the correct frames; persist the page-number map to
        # ``speaker_notes.json`` for the panel as before.
        # ``slides_dir`` was already defined above for the paper-context load.
        snapshot_rows = await get_deck_slides(deps.conn, deck_id=deck.id)
        snapshot_notes: dict[str, str] = {
            str(r.slide_index): r.note_text
            for r in snapshot_rows
            if r.note_text
        }

        def _persist_notes() -> None:
            slides_dir.mkdir(parents=True, exist_ok=True)
            (slides_dir / "speaker_notes.json").write_text(
                json.dumps(notes, ensure_ascii=False), encoding="utf-8"
            )
            # F4.5: notes are an addendum to the ACTIVE version, not a new
            # version of the deck content. Patch the current snapshot's
            # ``speaker_notes`` field in place so a later restore of THIS
            # version brings the notes back. We don't stamp a new version_id
            # (the deck-card replay correctly shows no per-turn card for
            # notes-only turns).
            if deck.current_version_id:
                VersionHistory(str(slides_dir)).patch_snapshot_notes(
                    deck.current_version_id,
                    snapshot_notes or None,
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

    async def _regenerate_notes_for_indices(
        state: AgentState,
        writer: Any,
        _flush_steps: Any,
        deck: Any,
        lang: str,
        indices: set[int],
    ) -> None:
        """Re-author notes for ONLY the given slide_indices in ``lang``, then
        rebuild the page-cache + patch the active snapshot.

        The user's mental model: a speaker note is bound to its page's
        content. If a frame's content changes (single-page edit / insert),
        that page's note gets regenerated; OTHER pages' notes stay
        untouched. Deck-wide edits don't call this — they wipe + leave
        notes empty so the user can decide whether to spend the LLM calls
        to regenerate everything.
        """
        if not indices:
            return
        new_rows = await get_deck_slides(deps.conn, deck_id=deck.id)
        # Re-author the targeted notes in ONE deck-wide call that sees every
        # frame and every surviving note plus the source-paper context. The
        # surviving notes (slides we are NOT regenerating) drive the talk's
        # voice + through-line so the regen'd notes flow with the rest; they
        # are NOT touched. Title frames have no spoken note.
        target_idx_set: set[int] = {i for i in indices}
        wanted_indices: list[int] = [
            r.slide_index
            for r in new_rows
            if r.slide_index in target_idx_set and not is_title_frame(r.frame_tex)
        ]
        if not wanted_indices:
            return
        surviving_notes: dict[int, str] = {
            r.slide_index: r.note_text
            for r in new_rows
            if r.note_text and r.slide_index not in target_idx_set
        }
        frames_payload: list[tuple[int, int, str]] = [
            (r.slide_index, r.page_start, r.frame_tex)
            for r in new_rows
            if not is_title_frame(r.frame_tex)
        ]
        # Reuse the chunk-derived context the slide_agent saw at generate
        # time so a regenerated note stays grounded in the SAME source
        # material the deck was built from (section excerpts → chunk text,
        # key figures, key equations). Falls back to title+abstract when
        # the bundles file is absent.
        slides_dir = (
            deps.workspace / "chat_session" / str(state["session_id"]) / "slides"
        )
        paper_context = _load_paper_context(slides_dir, state["report_papers"])
        authored = await author_deck_notes(
            adapter=deps.adapter,
            tracer=deps.tracer,
            model=deps.notes_model,
            papers=paper_context,
            frames=frames_payload,
            existing_notes=surviving_notes,
            wanted_indices=wanted_indices,
            note_language=lang,
            instruction=None,
        )
        for slide_index, note in authored.items():
            await update_slide_note(
                deps.conn,
                deck_id=deck.id,
                slide_index=slide_index,
                note_text=note,
                note_language=lang,
            )
        await _flush_steps()
        notes = await rebuild_speaker_notes_json(deps.conn, deck_id=deck.id)
        # ``notes`` is page-number-keyed (panel cache). The snapshot bundle
        # is slide_index-keyed (sl_emit / _recompile_and_emit / restore all
        # agree on that key). Build the slide_index-keyed map from the
        # post-regen deck_slides so restoring this version returns notes to
        # the right frames; persist the page-number map to disk as before.
        post_rows = await get_deck_slides(deps.conn, deck_id=deck.id)
        snapshot_notes: dict[str, str] = {
            str(r.slide_index): r.note_text
            for r in post_rows
            if r.note_text
        }
        # ``slides_dir`` was already defined above for the paper-context load.

        def _persist() -> None:
            slides_dir.mkdir(parents=True, exist_ok=True)
            (slides_dir / "speaker_notes.json").write_text(
                json.dumps(notes, ensure_ascii=False), encoding="utf-8"
            )
            # Re-bundle the full post-regen note map into THIS turn's
            # snapshot so a later restore brings the page-bound notes back
            # alongside the edited tex.
            fresh_active = deck.current_version_id
            if fresh_active:
                VersionHistory(str(slides_dir)).patch_snapshot_notes(
                    fresh_active,
                    snapshot_notes or None,
                )

        await asyncio.to_thread(_persist)

        fresh = await get_deck(deps.conn, session_id=state["session_id"])
        if fresh is not None:
            _emit_deck(
                writer, fresh, _deck_title(fresh),
                [{"id": p["id"], "title": p["title"]} for p in state["report_papers"]],
                has_notes=bool(notes),
            )
            await _flush_steps()

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
        wipe_indices: set[int] | None = None,
    ) -> str:
        """Verify graphics, recompile (Overfull-aware revise loop), snapshot the
        version, persist the deck + rebuild deck_slides, restore notes by
        slide_index, and emit the deck event. Returns the final-response text.

        ``wipe_indices`` controls which notes are dropped across the rewrite:
        the caller decides scope. Pass an empty set for a "preamble-only"
        edit (title / preamble — content frames untouched, all notes survive),
        a set of slide indices for a "targeted pages" edit (those notes drop,
        the rest survive), or the full set for a "whole deck" edit (every
        note drops because every frame was rewritten). ``None`` is treated as
        the empty set for backwards compatibility.
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

        # Page-scoped note invalidation: a frame whose content was rewritten
        # this turn (slide_index ∈ ``wipe_indices``) drops its note — the old
        # note described the OLD content. Untouched frames keep theirs.
        # ``None`` is treated as the empty set (preamble-only edits keep all
        # notes — title / preamble flows pass it that way).
        wipe = wipe_indices or set()
        preserved_notes: dict[str, str] = {
            str(idx): nt
            for idx, (nt, _nl) in old_notes.items()
            if nt is not None and idx not in wipe
        }

        # Version snapshot (blocking IO off the loop) — only when it compiled.
        # The snapshot bundles the post-invalidation note map so a later
        # restore of THIS version brings back exactly what's in the deck now:
        # untouched-frame notes survive; edited-frame slots are empty (and the
        # user can author fresh notes for them). We pass an EXPLICIT dict
        # rather than ``None`` — ``None`` means "auto-load from disk" in the
        # legacy paper2slides-plus signature, but our DB is the source of
        # truth and on-disk ``speaker_notes.json`` may still hold the previous
        # turn's state at this point in the flow.
        def _persist() -> str | None:
            if not result.ok:
                return None
            return VersionHistory(str(slides_dir)).save_version(
                result.tex, description, preserved_notes
            )

        new_version_id = await asyncio.to_thread(_persist)

        # F4.5: when the edit recompiled cleanly the new snapshot becomes the
        # active version (so the version-history endpoint + per-turn DeckChip
        # cards both reflect "this edit is what you're looking at"). Failed
        # recompiles keep the previously-active version pointer intact.
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
            current_version_id=new_version_id or deck.current_version_id,
        )
        # Record which version snapshot THIS run stamped, so message replay
        # can surface a per-turn DeckChip card pointing at it (FR-12).
        run_id_state = state.get("run_id")
        if new_version_id and run_id_state is not None:
            await deps.conn.execute(
                "UPDATE runs SET deck_version_id = ? WHERE id = ?",
                (new_version_id, run_id_state),
            )
            await deps.conn.commit()
        fresh = await get_deck(deps.conn, session_id=state["session_id"])
        assert fresh is not None

        if result.ok:
            await replace_deck_slides(
                deps.conn,
                deck_id=fresh.id,
                slides=build_deck_slides(result.tex, result.page_count),
            )
            # Restore notes onto the matching slide_index — but skip the
            # ``wipe`` set (slides whose content was rewritten this turn:
            # the old note no longer matches). Then rebuild the map.
            for r in await get_deck_slides(deps.conn, deck_id=fresh.id):
                if r.slide_index in wipe:
                    continue
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
        # The title row's body is just \titlepage — the rendered text lives in
        # the preamble \title{}/\author{}/\date{} macros, which edit_frame can't
        # reach. Skip it here; deck-wide instructions (scope=all) re-run
        # edit_title_block on the preamble below so the title stays consistent.
        targets = [r for r in targets if not is_title_frame(r.frame_tex)]
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

        # A deck-wide edit (e.g. "translate everything to English") must also
        # carry through to the title page; otherwise \title{}/\author{}/\date{}
        # in the preamble keeps its original language while every content frame
        # shifts. Only scope=all triggers this — page/current edits stay local.
        if cmd.target_scope == "all":
            block = get_preamble(new_tex)
            if block is not None:
                new_block = await edit_title_block(
                    adapter=deps.adapter,
                    tracer=deps.tracer,
                    model=deps.section_model,
                    page_block=block,
                    instruction=state.get("user_message", ""),
                    response_language=lang,
                )
                updated = replace_preamble(new_tex, new_block)
                if updated:
                    new_tex = updated
                await _flush_steps()

        # Page-scoped note invalidation: every targeted frame had its content
        # rewritten by ``edit_frame``, so its bundled note no longer matches.
        # Untouched frames keep their notes through _recompile_and_emit.
        edited_indices: set[int] = {r.slide_index for r in targets}
        msg = await _recompile_and_emit(
            state, writer, _flush_steps, deck, papers, papers_meta, old_notes,
            new_tex, description="Edited deck", wipe_indices=edited_indices,
        )
        fresh = await get_deck(deps.conn, session_id=state["session_id"])
        assert fresh is not None

        # Targeted single-page edits (scope=page/current) auto-regenerate the
        # ONE wiped note so the user doesn't have to ask "generate notes again
        # for this slide". Only when that slide HAD a note coming in — if it
        # was already noteless, an edit shouldn't conjure a note. Deck-wide
        # edits (scope=all) DON'T auto-regen: 12 LLM calls is a lot to spend
        # silently, and the user can trigger notes regeneration explicitly.
        if cmd.target_scope in ("page", "current") and len(targets) == 1:
            target = targets[0]
            had_note = old_notes.get(target.slide_index, (None, None))[0]
            if had_note:
                await _regenerate_notes_for_indices(
                    state, writer, _flush_steps, fresh, lang,
                    {target.slide_index},
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
    g.add_node("sl_qa", _sl_qa)
    g.add_edge(START, "sl_resolve")
    g.add_conditional_edges(
        "sl_resolve",
        _route_deck_command,
        {
            "empty": "sl_empty",
            "no_latex": "sl_no_latex",
            "create": "sl_generate",
            "notes": "sl_notes",
            "edit_slides": "sl_edit_slides",
            "edit_title": "sl_edit_title",
            "edit_preamble": "sl_edit_preamble",
            "qa": "sl_qa",
        },
    )
    g.add_edge("sl_empty", END)
    g.add_edge("sl_no_latex", END)
    g.add_edge("sl_generate", END)
    g.add_edge("sl_notes", END)
    g.add_edge("sl_edit_slides", END)
    g.add_edge("sl_edit_title", END)
    g.add_edge("sl_edit_preamble", END)
    g.add_edge("sl_qa", END)
    return g.compile()
