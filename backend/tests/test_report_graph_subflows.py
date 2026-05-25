"""F4 Task 9 — NOTES + EDIT sub-flows + deck-command routing in the subgraph.

These tests seed a generated deck via the GENERATE happy path (the same fixture
shape as test_report_graph.py), then drive a follow-up turn that classifies into
a DeckCommand and routes to sl_notes / sl_edit_slides.
"""
from pathlib import Path
from typing import Any

import pytest

from paperhub.agents.report_graph import ReportDeps, build_report_subgraph
from paperhub.db.deck_slides import get_deck_slides
from paperhub.db.decks import get_deck
from paperhub.models.domain import (
    DeckCommand,
    FrameDraft,
    OutlineSlide,
    PaperBrief,
    RoutingDecision,
    TalkOutline,
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
        raise AssertionError(f"unexpected response_model {response_model!r}")

    def stream(self, *, slot, **kw):  # type: ignore[no-untyped-def]
        async def g():  # type: ignore[no-untyped-def]
            if slot == "slides_coherence/v1":
                yield kw["variables"]["frames_block"]

        return g()


class _SubflowAdapter:
    """Stub adapter for a follow-up turn on an existing deck.

    structured() returns the configured DeckCommand for slides_deck_command/v1.
    stream() serves note-author + edit-frame slots.
    """

    def __init__(self, command: DeckCommand) -> None:
        self.command = command
        self.note_author_calls = 0
        self.edit_frame_calls = 0
        self.note_split_calls = 0

    async def structured(self, *, response_model, **kw):  # type: ignore[no-untyped-def]
        if response_model is DeckCommand:
            return self.command
        raise AssertionError(f"unexpected response_model {response_model!r}")

    def stream(self, *, slot, **kw):  # type: ignore[no-untyped-def]
        adapter = self

        async def g():  # type: ignore[no-untyped-def]
            if slot == "slides_note_author/v1":
                adapter.note_author_calls += 1
                lang = kw["variables"]["note_language"]
                yield f"note in {lang}"
            elif slot == "slides_edit_frame/v1":
                adapter.edit_frame_calls += 1
                yield "\\begin{frame}{Method}EDITED method content.\\end{frame}"

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


async def _seed_deck(fake_tracer, migrated_db, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Run the GENERATE path once to produce a 2-frame deck on disk + in DB."""
    monkeypatch.setattr(
        "paperhub.agents.report_graph._pdflatex_available", lambda: True
    )
    await _seed_paper(migrated_db, tmp_path, "cacheGen")

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
