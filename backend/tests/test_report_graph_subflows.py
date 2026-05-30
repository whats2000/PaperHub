"""F4 Task 9 — NOTES + EDIT sub-flows + deck-command routing in the subgraph.

These tests seed a generated deck via the GENERATE happy path (the same fixture
shape as test_report_graph.py), then drive a follow-up turn that classifies into
a DeckCommand and routes to sl_notes / sl_edit_slides.
"""
from pathlib import Path
from typing import Any

import pytest

import paperhub.agents.report_graph as rg
from paperhub.agents.report_graph import ReportDeps, build_report_subgraph
from paperhub.db.deck_slides import get_deck_slides
from paperhub.db.decks import get_deck
from paperhub.models.domain import (
    DeckCommand,
    DeckOutline,
    FrameDraft,
    KeyEquation,
    KeyFigure,
    KeyResult,
    OutlineSlide,
    PaperBrief,
    PaperTalkBrief,
    PlannedSlide,
    RenderedSlide,
    RoutingDecision,
    TalkOutline,
    TargetLanguage,
)
from paperhub.pipelines.paper_asset import (
    FigureAsset,
    PaperAsset,
    SectionAsset,
    paper_asset_dir,
    write_paper_asset,
)
from paperhub.pipelines.slide_pipeline import compile as compile_mod


def _seed_asset(source_dir: Path) -> None:
    fig_dir = paper_asset_dir(source_dir) / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    (fig_dir / "fig-000.png").write_bytes(b"\x89PNG\r\n")
    write_paper_asset(
        PaperAsset(
            figures=[
                FigureAsset(
                    id="fig-000",
                    caption="The architecture diagram.",
                    page=1,
                    section="Method",
                    image_path="figures/fig-000.png",
                )
            ],
            sections=[SectionAsset(name="Method", order=0)],
        ),
        source_dir,
    )


# A 3-page deck: \maketitle (page 1) + two content frames (pages 2, 3). The
# first content frame is slide_index=0, the second slide_index=1.
_DECK_TEX = r"""\documentclass{beamer}
\usetheme{metropolis}
\title{MoE}
\begin{document}
\maketitle
\begin{frame}{Motivation}
Original motivation content.
\end{frame}
\begin{frame}{Method}
Original method content.
\end{frame}
\end{document}"""


class _CreateAdapter:
    """Drives the GENERATE path to seed a deck (2 content frames)."""

    def __init__(self) -> None:
        self._draft_calls = 0

    async def structured(self, *, response_model, **kw):  # type: ignore[no-untyped-def]
        if response_model is PaperBrief:
            return PaperBrief(
                paper_id=1,
                contribution="A new mechanism.",
                method="Scaled attention.",
                key_results=["SOTA"],
                key_figure_keys=["p0-fig-000"],
                key_equations=[],
            )
        if response_model is TalkOutline:
            return TalkOutline(
                title="MoE",
                slides=[
                    OutlineSlide(
                        title="Motivation", goal="why", key_points=["a"],
                        figure_key="p0-fig-000", paper_ids=[1],
                    ),
                    OutlineSlide(
                        title="Method", goal="how", key_points=["b"],
                        paper_ids=[1],
                    ),
                ],
            )
        if response_model is FrameDraft:
            self._draft_calls += 1
            if self._draft_calls == 1:
                return FrameDraft(
                    frame="\\begin{frame}{Motivation}Original motivation content.\\end{frame}"
                )
            return FrameDraft(
                frame="\\begin{frame}{Method}Original method content.\\end{frame}"
            )
        if response_model is TargetLanguage:
            return TargetLanguage(language=getattr(self, "slide_language", None))
        raise AssertionError(f"unexpected response_model {response_model!r}")

    def stream(self, *, slot, **kw):  # type: ignore[no-untyped-def]
        async def g():  # type: ignore[no-untyped-def]
            if slot == "slides_coherence/v1":
                yield kw["variables"]["frames_block"]

        return g()


class _SubflowAdapter:
    """Stub adapter for a follow-up turn on an existing deck.

    structured() returns the configured DeckCommand for slides_deck_command/v1.
    stream() serves note-author + edit-frame + edit-title + edit-preamble slots.
    """

    def __init__(self, command: DeckCommand) -> None:
        self.command = command
        self.note_author_calls = 0
        self.edit_frame_calls = 0
        self.note_split_calls = 0
        self.last_edit_slot: str | None = None

    async def structured(self, *, response_model, **kw):  # type: ignore[no-untyped-def]
        if response_model is DeckCommand:
            return self.command
        if response_model is TargetLanguage:
            return TargetLanguage(language=getattr(self, "slide_language", None))
        raise AssertionError(f"unexpected response_model {response_model!r}")

    def stream(self, *, slot, **kw):  # type: ignore[no-untyped-def]
        adapter = self

        async def g():  # type: ignore[no-untyped-def]
            if slot in ("slides_edit_title/v1", "slides_edit_preamble/v1", "slides_edit_frame/v1"):
                adapter.last_edit_slot = slot
            if slot == "slides_note_author/v1":
                adapter.note_author_calls += 1
                lang = kw["variables"]["note_language"]
                yield f"note in {lang}"
            elif slot == "slides_edit_frame/v1":
                adapter.edit_frame_calls += 1
                adapter.edit_frame_lang = kw["variables"]["response_language"]
                yield "\\begin{frame}{Method}EDITED method content.\\end{frame}"
            elif slot in ("slides_edit_title/v1", "slides_edit_preamble/v1"):
                yield kw["variables"]["page_block"]

        return g()


def _make_deps(adapter, fake_tracer, migrated_db, retriever, tmp_path) -> ReportDeps:  # type: ignore[no-untyped-def]
    return ReportDeps(
        adapter=adapter,
        tracer=fake_tracer,
        conn=migrated_db,
        retriever=retriever,
        workspace=tmp_path,
        plan_model="m",
        section_model="m",
        notes_model="m",
        resolve_model="m",
        recall_enabled=False,
    )


def _state(user_message: str = "follow up", **extra: Any) -> dict[str, Any]:
    s: dict[str, Any] = {
        "run_id": 0,
        "branch": "",
        "session_id": 1,
        "user_message": user_message,
        "effective_query": user_message,
        "response_language": "English",
        "routing_decision": RoutingDecision(
            intent="slides", model_tier="flagship", confidence=0.9, reasoning="x"
        ),
    }
    s.update(extra)
    return s


class _Retr:
    def retrieve(self, q, *, enabled_paper_content_ids, corpus_size, top_k=10):  # type: ignore[no-untyped-def]
        return []


async def _seed_paper(migrated_db, tmp_path, cache: str) -> Path:  # type: ignore[no-untyped-def]
    source_dir = tmp_path / cache / "source"
    _seed_asset(source_dir)
    await migrated_db.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, abstract, "
        "source_path, source_dir_path, html_path) "
        "VALUES ('arxiv:1', 'arxiv', '2403.01', 'Paper A', 'An abstract.', 'p', ?, 'h')",
        (str(source_dir),),
    )
    await migrated_db.execute(
        "INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (1, 1, 1)"
    )
    await migrated_db.commit()
    return source_dir


def _install_t5_stubs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Patch the three T5 agentic nodes (sl_paper_brief / sl_plan_deck /
    sl_render_slide) on ``report_graph`` so the seed-deck GENERATE chain
    runs without a real LLM. The outline matches the OLD ``_CreateAdapter``
    shape: a single paper with two content slides ('Motivation' + 'Method'),
    one referencing the staged figure key. Frame text is provided by the
    fake compile (``_DECK_TEX``) — the render stubs just need to return
    schema-valid RenderedSlide rows so the chain reaches compile."""
    brief = PaperTalkBrief(
        paper_id=1,
        contribution="A new mechanism.",
        method_core="Scaled attention.",
        key_results=[
            KeyResult(description="SOTA", number="14%", benchmark="LIBERO"),
        ],
        key_figures=[
            KeyFigure(
                key="p0-fig-000", role="overview",
                one_line_interpretation="shows the diagram",
            ),
        ],
        key_equations=[
            KeyEquation(
                latex="E=mc^2", role="objective",
                notation_explanation="E is energy",
            ),
        ],
        paper_newcommands="",
        talk_shape_hint="concept+math",
    )
    outline = DeckOutline(
        talk_title="MoE",
        slides=[
            PlannedSlide(
                pattern_kind="concept_2col",
                title="Motivation", goal="why",
                paper_id=1, figure_key="p0-fig-000",
                key_points=["a"],
            ),
            PlannedSlide(
                pattern_kind="concept_2col",
                title="Method", goal="how",
                paper_id=1, figure_key=None,
                key_points=["b"],
            ),
        ],
        style_profile_name="default",
    )

    async def fake_paper_brief(*, paper_content_id, paper_idx, title,
                               tracer, model, conn, **kw):  # type: ignore[no-untyped-def]
        async with tracer.step(
            agent="report", tool="report:paper_brief", model=model,
        ) as step:
            step.record_args({"paper_content_id": paper_content_id})
            step.record_result({"stubbed": True})
        return brief

    async def fake_plan_deck(*, briefs, target_slide_count, talk_title_hint,
                             tracer, model, **kw):  # type: ignore[no-untyped-def]
        async with tracer.step(
            agent="report", tool="report:plan_deck", model=model,
        ) as step:
            step.record_args({"target": target_slide_count})
            step.record_result({"stubbed": True})
        return outline

    async def fake_render_slide(*, planned_slide, deck_outline, paper_brief,
                                all_briefs, tracer, model, **kw):  # type: ignore[no-untyped-def]
        slide_idx = next(
            i for i, s in enumerate(deck_outline.slides) if s is planned_slide
        )
        frame_tex = (
            "\\begin{frame}{" + planned_slide.title + "}"
            + (
                "\\includegraphics{" + planned_slide.figure_key + "}"
                if planned_slide.figure_key
                else ""
            )
            + "\\end{frame}"
        )
        figure_keys = (
            [planned_slide.figure_key] if planned_slide.figure_key else []
        )
        async with tracer.step(
            agent="report", tool="report:render_slide", model=model,
        ) as step:
            step.record_args({
                "slide_index": slide_idx,
                "pattern_kind": planned_slide.pattern_kind,
            })
            step.record_result({"stubbed": True})
        return RenderedSlide(
            slide_index=slide_idx,
            pattern_kind=planned_slide.pattern_kind,
            paper_id=planned_slide.paper_id,
            frame_tex=frame_tex,
            figure_keys_used=figure_keys,
            callback_reads=[],
        )

    monkeypatch.setattr(rg, "run_sl_paper_brief", fake_paper_brief)
    monkeypatch.setattr(rg, "run_sl_plan_deck", fake_plan_deck)
    monkeypatch.setattr(rg, "run_sl_render_slide", fake_render_slide)


async def _seed_deck(fake_tracer, migrated_db, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Run the GENERATE path once to produce a 2-frame deck on disk + in DB.

    F4.4 T5: the chain now runs the agentic-brief topology; we stub the three
    new nodes so the seed-deck path doesn't need a real LLM. ``_CreateAdapter``
    still serves the language-detection + coherence slots."""
    monkeypatch.setattr(
        "paperhub.agents.report_graph._pdflatex_available", lambda: True
    )
    await _seed_paper(migrated_db, tmp_path, "cacheGen")
    _install_t5_stubs(monkeypatch)

    async def fake_compile(*, tex, workdir, tex_name, revise, max_retries=3):  # type: ignore[no-untyped-def]
        Path(workdir).mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        (Path(workdir) / "deck.tex").write_text(_DECK_TEX)  # noqa: ASYNC240
        (Path(workdir) / "deck.pdf").write_bytes(b"%PDF-1.4")  # noqa: ASYNC240
        return compile_mod.CompileResult(True, 1, _DECK_TEX, "", 3)

    monkeypatch.setattr(compile_mod, "compile_with_revise", fake_compile)

    deps = _make_deps(_CreateAdapter(), fake_tracer, migrated_db, _Retr(), tmp_path)
    graph = build_report_subgraph(deps)
    state = _state("make slides")
    state["run_id"] = fake_tracer.run_id
    async for _m, _p in graph.astream(state, stream_mode=["custom", "values"]):
        pass


@pytest.mark.asyncio
async def test_generate_notes_fills_notes_without_touching_frames(  # type: ignore[no-untyped-def]
    fake_tracer, migrated_db, tmp_path, monkeypatch
) -> None:
    await _seed_deck(fake_tracer, migrated_db, tmp_path, monkeypatch)
    deck = await get_deck(migrated_db, session_id=1)
    assert deck is not None
    before = await get_deck_slides(migrated_db, deck_id=deck.id)
    frames_before = {r.slide_index: r.frame_tex for r in before}
    assert all(r.note_text is None for r in before)

    adapter = _SubflowAdapter(
        DeckCommand(action="generate_notes", target_scope="all", note_language="English")
    )
    deps = _make_deps(adapter, fake_tracer, migrated_db, _Retr(), tmp_path)
    graph = build_report_subgraph(deps)
    state = _state("generate speaker notes in English")
    state["run_id"] = fake_tracer.run_id

    events: list[Any] = []
    async for mode, payload in graph.astream(state, stream_mode=["custom", "values"]):
        if mode == "custom":
            events.append(payload)

    after = await get_deck_slides(migrated_db, deck_id=deck.id)
    assert all(r.note_text and "note in English" in r.note_text for r in after), (
        "every targeted slide must get a note"
    )
    assert {r.slide_index: r.frame_tex for r in after} == frames_before, (
        "frame_tex must be unchanged by NOTES"
    )
    fresh = await get_deck(migrated_db, session_id=1)
    assert fresh is not None and fresh.speaker_notes, "deck.speaker_notes filled"
    deck_evt = next(e for e in events if e.get("event") == "deck")
    assert deck_evt["deck"]["has_notes"] is True
    assert adapter.edit_frame_calls == 0, "NOTES must not edit frames"


@pytest.mark.asyncio
async def test_edit_notes_relanguages_only_notes(  # type: ignore[no-untyped-def]
    fake_tracer, migrated_db, tmp_path, monkeypatch
) -> None:
    await _seed_deck(fake_tracer, migrated_db, tmp_path, monkeypatch)
    deck = await get_deck(migrated_db, session_id=1)
    assert deck is not None

    # First fill English notes via generate_notes.
    gen = _SubflowAdapter(
        DeckCommand(action="generate_notes", target_scope="all", note_language="English")
    )
    g1 = build_report_subgraph(_make_deps(gen, fake_tracer, migrated_db, _Retr(), tmp_path))
    s1 = _state("notes please")
    s1["run_id"] = fake_tracer.run_id
    async for _m, _p in g1.astream(s1, stream_mode=["custom", "values"]):
        pass

    pre = await get_deck_slides(migrated_db, deck_id=deck.id)
    frames_before = {r.slide_index: r.frame_tex for r in pre}
    notes_before = {r.slide_index: r.note_text for r in pre}

    # Now relanguage to Traditional Chinese via edit_notes.
    edit = _SubflowAdapter(
        DeckCommand(
            action="edit_notes", target_scope="all",
            note_language="Traditional Chinese",
        )
    )
    g2 = build_report_subgraph(_make_deps(edit, fake_tracer, migrated_db, _Retr(), tmp_path))
    s2 = _state("translate the notes to Traditional Chinese")
    s2["run_id"] = fake_tracer.run_id
    async for _m, _p in g2.astream(s2, stream_mode=["custom", "values"]):
        pass

    post = await get_deck_slides(migrated_db, deck_id=deck.id)
    notes_after = {r.slide_index: r.note_text for r in post}
    assert notes_after != notes_before, "notes must change on relanguage"
    assert all("Traditional Chinese" in (v or "") for v in notes_after.values())
    assert {r.slide_index: r.frame_tex for r in post} == frames_before, (
        "edit_notes must not touch frames"
    )
    assert all(r.note_language == "Traditional Chinese" for r in post)
    assert edit.edit_frame_calls == 0, "edit_notes must NOT recompile / edit frames"


@pytest.mark.asyncio
async def test_edit_slides_page_rewrites_one_frame_and_recompiles(  # type: ignore[no-untyped-def]
    fake_tracer, migrated_db, tmp_path, monkeypatch
) -> None:
    await _seed_deck(fake_tracer, migrated_db, tmp_path, monkeypatch)
    deck = await get_deck(migrated_db, session_id=1)
    assert deck is not None

    # Give every slide a note so we can assert preservation by slide_index.
    gen = _SubflowAdapter(
        DeckCommand(action="generate_notes", target_scope="all", note_language="English")
    )
    g0 = build_report_subgraph(_make_deps(gen, fake_tracer, migrated_db, _Retr(), tmp_path))
    s0 = _state("notes")
    s0["run_id"] = fake_tracer.run_id
    async for _m, _p in g0.astream(s0, stream_mode=["custom", "values"]):
        pass

    pre = await get_deck_slides(migrated_db, deck_id=deck.id)
    notes_before = {r.slide_index: r.note_text for r in pre}
    frame0_before = next(r.frame_tex for r in pre if r.slide_index == 0)

    # edit_slides targeting page 3 (= slide_index 1, the "Method" frame).
    edited_tex_holder: dict[str, str] = {}

    async def fake_edit_compile(*, tex, workdir, tex_name, revise, max_retries=3):  # type: ignore[no-untyped-def]
        Path(workdir).mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        (Path(workdir) / "deck.tex").write_text(tex)  # noqa: ASYNC240
        (Path(workdir) / "deck.pdf").write_bytes(b"%PDF-1.4")  # noqa: ASYNC240
        edited_tex_holder["tex"] = tex
        return compile_mod.CompileResult(True, 1, tex, "", 3)

    monkeypatch.setattr(compile_mod, "compile_with_revise", fake_edit_compile)

    adapter = _SubflowAdapter(
        DeckCommand(action="edit_slides", target_scope="page", target_page=3)
    )
    deps = _make_deps(adapter, fake_tracer, migrated_db, _Retr(), tmp_path)
    graph = build_report_subgraph(deps)
    state = _state("make the method slide say EDITED")
    state["run_id"] = fake_tracer.run_id
    async for _m, _p in graph.astream(state, stream_mode=["custom", "values"]):
        pass

    assert adapter.edit_frame_calls == 1, "exactly one frame edited"
    # The compiled tex contains the edited Method frame, motivation untouched.
    tex = edited_tex_holder["tex"]
    assert "EDITED method content" in tex
    assert "Original motivation content" in tex
    assert "Original method content" not in tex

    post = await get_deck_slides(migrated_db, deck_id=deck.id)
    frame0_after = next(r.frame_tex for r in post if r.slide_index == 0)
    assert frame0_after == frame0_before, "untargeted frame unchanged"
    assert any("EDITED" in r.frame_tex for r in post), "target frame rewritten"
    # Notes preserved by slide_index.
    notes_after = {r.slide_index: r.note_text for r in post}
    assert notes_after == notes_before, "notes preserved by slide_index across edit"

    # A new "Edited deck" version snapshot exists on disk (alongside the
    # GENERATE "Generated deck" one).
    from paperhub.pipelines.slide_pipeline.history import VersionHistory

    slides_dir = tmp_path / "chat_session" / "1" / "slides"
    descs = [v["description"] for v in VersionHistory(str(slides_dir)).list_versions()]
    assert any(d.startswith("Edited deck") for d in descs), (
        f"an edit version snapshot must be written; got {descs}"
    )


@pytest.mark.asyncio
async def test_edit_slides_uses_detected_task_language_over_chat_language(  # type: ignore[no-untyped-def]
    fake_tracer, migrated_db, tmp_path, monkeypatch
) -> None:
    """The frame edit must be written in the TASK language the user asked for
    (detect_slide_language), NOT the router's chat-reply language. Regression:
    "把簡報換成英文" (typed in Chinese) was keeping the slides in Chinese because
    edit_frame received response_language instead of the requested target."""
    await _seed_deck(fake_tracer, migrated_db, tmp_path, monkeypatch)

    async def fake_edit_compile(*, tex, workdir, tex_name, revise, max_retries=3):  # type: ignore[no-untyped-def]
        Path(workdir).mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        (Path(workdir) / "deck.tex").write_text(tex)  # noqa: ASYNC240
        (Path(workdir) / "deck.pdf").write_bytes(b"%PDF-1.4")  # noqa: ASYNC240
        return compile_mod.CompileResult(True, 1, tex, "", 3)

    monkeypatch.setattr(compile_mod, "compile_with_revise", fake_edit_compile)

    adapter = _SubflowAdapter(
        DeckCommand(action="edit_slides", target_scope="all")
    )
    # The detector resolved an explicit target language for the SLIDES.
    adapter.slide_language = "English"
    deps = _make_deps(adapter, fake_tracer, migrated_db, _Retr(), tmp_path)
    graph = build_report_subgraph(deps)
    # User typed in Chinese → router response_language is Traditional Chinese.
    state = _state("能幫我把簡報換成英文嗎", response_language="Traditional Chinese")
    state["run_id"] = fake_tracer.run_id
    async for _m, _p in graph.astream(state, stream_mode=["custom", "values"]):
        pass

    assert adapter.edit_frame_calls >= 1, "edit_slides must edit at least one frame"
    assert adapter.edit_frame_lang == "English", (
        "edit_frame must receive the detected TASK language (English), not the "
        f"chat-reply language; got {adapter.edit_frame_lang!r}"
    )


# ---------------------------------------------------------------------------
# Titlepage-style deck (NO \maketitle — first frame carries \titlepage).
#
# slide_index→page mapping for _TITLEPAGE_TEX (3 PDF pages, 2 content slides):
#   extract_frames_from_beamer: no bare \maketitle before frames → no synthetic
#   tuple → 3 real frames numbered 1, 2, 3.
#   map_pages_to_slides: frame 1 has \titlepage → is_title page 1;
#   frame 2 (Intro) → page 2; frame 3 (Method) → page 3.
#   build_deck_slides: leading title frame dropped by is_title_frame → 2 content
#   frames; groups=[1,2,3] (3), frames=2 → len(groups)==len(frames)+1 → groups[1:]:
#     slide_index 0 = Intro frame,  page 2
#     slide_index 1 = Method frame, page 3
#   _real_frame_number: i=0 title frame skipped; slide_index 0 → frame_number 2;
#   slide_index 1 → frame_number 3.
# ---------------------------------------------------------------------------

_TITLEPAGE_TEX = (
    "\\documentclass{beamer}\n\\begin{document}\n"
    "\\begin{frame}\\titlepage\\end{frame}\n"
    "\\begin{frame}{Intro}\\begin{itemize}\\item Original intro content."
    "\\end{itemize}\\end{frame}\n"
    "\\begin{frame}{Method}\\begin{itemize}\\item Original method content."
    "\\end{itemize}\\end{frame}\n"
    "\\end{document}\n"
)


class _TitlepageEditAdapter:
    """Stub adapter for edit_slides on the titlepage-style deck.

    stream() yields a recognisably-changed Intro frame so the test can verify
    only the targeted frame (slide_index=0, page 2) was replaced.
    """

    def __init__(self, command: DeckCommand) -> None:
        self.command = command
        self.edit_frame_calls = 0

    async def structured(self, *, response_model, **kw):  # type: ignore[no-untyped-def]
        if response_model is DeckCommand:
            return self.command
        if response_model is TargetLanguage:
            return TargetLanguage(language=getattr(self, "slide_language", None))
        raise AssertionError(f"unexpected response_model {response_model!r}")

    def stream(self, *, slot, **kw):  # type: ignore[no-untyped-def]
        adapter = self

        async def g():  # type: ignore[no-untyped-def]
            if slot == "slides_edit_frame/v1":
                adapter.edit_frame_calls += 1
                yield (
                    "\\begin{frame}{Intro}"
                    "\\begin{itemize}\\item EDITED intro content."
                    "\\end{itemize}\\end{frame}"
                )

        return g()


async def _seed_titlepage_deck(fake_tracer, migrated_db, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Seed a titlepage-style deck (NO \\maketitle) via the GENERATE path.

    F4.4 T5: stubs the new agentic-brief chain so the GENERATE path runs
    without a real LLM. The fake compile substitutes ``_TITLEPAGE_TEX`` for
    whatever the chain assembles, so the test is about the post-compile
    deck_slides mapping + edit routing, not the rendered frame text."""
    monkeypatch.setattr(
        "paperhub.agents.report_graph._pdflatex_available", lambda: True
    )
    await _seed_paper(migrated_db, tmp_path, "cacheTitlepage")
    _install_t5_stubs(monkeypatch)

    async def fake_compile(*, tex, workdir, tex_name, revise, max_retries=3):  # type: ignore[no-untyped-def]
        Path(workdir).mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        (Path(workdir) / "deck.tex").write_text(_TITLEPAGE_TEX)  # noqa: ASYNC240
        (Path(workdir) / "deck.pdf").write_bytes(b"%PDF-1.4")  # noqa: ASYNC240
        return compile_mod.CompileResult(True, 1, _TITLEPAGE_TEX, "", 3)

    monkeypatch.setattr(compile_mod, "compile_with_revise", fake_compile)

    deps = _make_deps(_CreateAdapter(), fake_tracer, migrated_db, _Retr(), tmp_path)
    graph = build_report_subgraph(deps)
    state = _state("make slides")
    state["run_id"] = fake_tracer.run_id
    async for _m, _p in graph.astream(state, stream_mode=["custom", "values"]):
        pass


@pytest.mark.asyncio
async def test_edit_slides_titlepage_deck_correct_frame_mapping(  # type: ignore[no-untyped-def]
    fake_tracer, migrated_db, tmp_path, monkeypatch
) -> None:
    """Editing page 2 on a titlepage-style deck must rewrite exactly the Intro
    frame (slide_index=0, frame_number=2) and leave the Method frame
    (slide_index=1) byte-identical.  The leading titlepage frame is NOT a
    content slide and must NOT appear in deck_slides.  This guards both
    build_deck_slides (title-frame exclusion) and _real_frame_number
    (is_title_frame skip at i=0)."""
    await _seed_titlepage_deck(fake_tracer, migrated_db, tmp_path, monkeypatch)
    deck = await get_deck(migrated_db, session_id=1)
    assert deck is not None

    pre = await get_deck_slides(migrated_db, deck_id=deck.id)
    # The titlepage frame (page 1) is NOT in deck_slides; only 2 content rows.
    assert len(pre) == 2, f"expected 2 content slides (no title), got {len(pre)}"
    page_map = {r.page_start: r.slide_index for r in pre}
    assert page_map.get(1) is None, f"page 1 (titlepage) must NOT be in deck_slides; got {page_map}"
    assert page_map.get(2) == 0, f"page 2 must be slide_index 0 (Intro); got {page_map}"
    assert page_map.get(3) == 1, f"page 3 must be slide_index 1 (Method); got {page_map}"

    frames_before = {r.slide_index: r.frame_tex for r in pre}

    edited_tex_holder: dict[str, str] = {}

    async def fake_edit_compile(*, tex, workdir, tex_name, revise, max_retries=3):  # type: ignore[no-untyped-def]
        Path(workdir).mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        (Path(workdir) / "deck.tex").write_text(tex)  # noqa: ASYNC240
        (Path(workdir) / "deck.pdf").write_bytes(b"%PDF-1.4")  # noqa: ASYNC240
        edited_tex_holder["tex"] = tex
        return compile_mod.CompileResult(True, 1, tex, "", 3)

    monkeypatch.setattr(compile_mod, "compile_with_revise", fake_edit_compile)

    # Edit page 2 = slide_index 0 = the Intro frame.  _real_frame_number skips
    # the leading titlepage frame (i=0, is_title_frame→True) and returns
    # frame_number 2 (not 1), so replace_frame_in_beamer targets the Intro, not
    # the titlepage.
    adapter = _TitlepageEditAdapter(
        DeckCommand(action="edit_slides", target_scope="page", target_page=2)
    )
    deps = _make_deps(adapter, fake_tracer, migrated_db, _Retr(), tmp_path)
    graph = build_report_subgraph(deps)
    state = _state("make the intro slide say EDITED")
    state["run_id"] = fake_tracer.run_id
    async for _m, _p in graph.astream(state, stream_mode=["custom", "values"]):
        pass

    assert adapter.edit_frame_calls == 1, "exactly one frame must be edited"

    tex = edited_tex_holder["tex"]
    assert "EDITED intro content" in tex, "targeted Intro frame must be rewritten"
    assert "Original method content" in tex, "untargeted Method frame must be unchanged"
    # The titlepage frame has no \frametitle so check its \titlepage marker.
    assert "\\titlepage" in tex, "titlepage frame must be preserved"
    assert "Original intro content" not in tex, "old Intro content must be replaced"

    post = await get_deck_slides(migrated_db, deck_id=deck.id)
    # Untargeted frame (slide_index 1 = Method) must be byte-identical to before.
    for r in post:
        if r.slide_index == 1:
            assert r.frame_tex == frames_before[r.slide_index], (
                f"slide_index {r.slide_index} must be unchanged"
            )
    assert any("EDITED" in r.frame_tex for r in post if r.slide_index == 0), (
        "targeted frame (slide_index=0) must show EDITED content in DB"
    )


# ---------------------------------------------------------------------------
# B7 — edit_title / edit_preamble routing + page-1 → title fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_title_routes_and_rewrites_preamble_keeps_frames(  # type: ignore[no-untyped-def]
    fake_tracer, migrated_db, tmp_path, monkeypatch
) -> None:
    """edit_title action must route to sl_edit_title (slot slides_edit_title/v1),
    must NOT invoke edit_frame, and must leave the content frames byte-identical."""
    await _seed_deck(fake_tracer, migrated_db, tmp_path, monkeypatch)
    deck = await get_deck(migrated_db, session_id=1)
    assert deck is not None
    pre = await get_deck_slides(migrated_db, deck_id=deck.id)
    frames_before = {r.slide_index: r.frame_tex for r in pre}

    async def fake_compile(*, tex, workdir, tex_name, revise, max_retries=3):  # type: ignore[no-untyped-def]
        Path(workdir).mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        (Path(workdir) / "deck.tex").write_text(tex)  # noqa: ASYNC240
        (Path(workdir) / "deck.pdf").write_bytes(b"%PDF-1.4")  # noqa: ASYNC240
        return compile_mod.CompileResult(True, 1, tex, "", 3)

    monkeypatch.setattr(compile_mod, "compile_with_revise", fake_compile)

    adapter = _SubflowAdapter(DeckCommand(action="edit_title", target_scope="all"))
    graph = build_report_subgraph(_make_deps(adapter, fake_tracer, migrated_db, _Retr(), tmp_path))
    state = _state("rename the title to FOO")
    state["run_id"] = fake_tracer.run_id
    async for _m, _p in graph.astream(state, stream_mode=["custom", "values"]):
        pass

    assert adapter.last_edit_slot == "slides_edit_title/v1", (
        f"must route to the title edit; got {adapter.last_edit_slot!r}"
    )
    assert adapter.edit_frame_calls == 0, "title flow must NOT edit a content frame"
    post = await get_deck_slides(migrated_db, deck_id=deck.id)
    assert {r.slide_index: r.frame_tex for r in post} == frames_before, (
        "content frames must be unchanged by edit_title"
    )


@pytest.mark.asyncio
async def test_edit_slides_on_page_one_falls_back_to_title(  # type: ignore[no-untyped-def]
    fake_tracer, migrated_db, tmp_path, monkeypatch
) -> None:
    """An edit_slides command targeting page 1 (the \\maketitle title page, which
    has no content row in deck_slides) must be transparently redirected to the
    title-edit flow (sl_edit_title / slides_edit_title/v1) by the page-1 fallback
    in _resolve, and must NOT invoke edit_frame."""
    # _seed_deck uses _DECK_TEX: \maketitle on page 1, content frames on pages 2+.
    await _seed_deck(fake_tracer, migrated_db, tmp_path, monkeypatch)

    async def fake_compile(*, tex, workdir, tex_name, revise, max_retries=3):  # type: ignore[no-untyped-def]
        Path(workdir).mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        (Path(workdir) / "deck.tex").write_text(tex)  # noqa: ASYNC240
        (Path(workdir) / "deck.pdf").write_bytes(b"%PDF-1.4")  # noqa: ASYNC240
        return compile_mod.CompileResult(True, 1, tex, "", 3)

    monkeypatch.setattr(compile_mod, "compile_with_revise", fake_compile)

    # Classifier says edit_slides on page 1 (the title page — no content row).
    adapter = _SubflowAdapter(
        DeckCommand(action="edit_slides", target_scope="page", target_page=1)
    )
    graph = build_report_subgraph(_make_deps(adapter, fake_tracer, migrated_db, _Retr(), tmp_path))
    state = _state("edit this slide", current_view_page=1)
    state["run_id"] = fake_tracer.run_id
    async for _m, _p in graph.astream(state, stream_mode=["custom", "values"]):
        pass

    # Page 1 is the title page → redirected to the title-edit flow.
    assert adapter.last_edit_slot == "slides_edit_title/v1", (
        f"page-1 edit_slides must fall back to title edit; got {adapter.last_edit_slot!r}"
    )
    assert adapter.edit_frame_calls == 0, "page-1 fallback must NOT edit a content frame"
