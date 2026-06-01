from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from paperhub.agents.sl_emit import EmitResult, run_sl_emit
from paperhub.db.migrate import apply_schema
from paperhub.models.slide_domain import FigureDimensions, KeyFigureBundle


@pytest_asyncio.fixture
async def conn(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    db = tmp_path / "test.db"
    async with aiosqlite.connect(str(db)) as c:
        await c.execute("PRAGMA foreign_keys = ON")
        await apply_schema(c)
        await c.execute(
            "INSERT INTO chat_sessions (id, created_at, title) "
            "VALUES (1, datetime('now'), 't')"
        )
        await c.execute(
            "INSERT INTO runs (id, session_id, started_at, status) "
            "VALUES (1, 1, datetime('now'), 'running')"
        )
        await c.commit()
        yield c


_DECK = r"""\documentclass{beamer}
\begin{document}
\begin{frame}{A}body of A\end{frame}
\begin{frame}{B}body of B\end{frame}
\end{document}
"""


@pytest.mark.asyncio
async def test_emit_writes_decks_row_and_one_deck_slides_per_frame(
    conn: aiosqlite.Connection, tmp_path: Path
) -> None:
    workdir = tmp_path / "slides"
    workdir.mkdir()
    (workdir / "deck.tex").write_text(_DECK, encoding="utf-8")
    (workdir / "deck.pdf").write_bytes(b"%PDF-fake")  # smoke

    result = await run_sl_emit(
        session_id=1,
        run_id=1,
        deck_tex=_DECK,
        workdir=workdir,
        page_count=2,
        status="ok",
        contributing_paper_ids=[42, 43],
        figure_inventory={},
        conn=conn,
    )
    assert isinstance(result, EmitResult)
    assert result.deck_id > 0
    # decks row
    async with conn.execute(
        "SELECT page_count, current_version_id FROM decks WHERE session_id=1"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == 2
    assert row[1] is not None  # version_id was set
    # deck_slides one per frame
    async with conn.execute(
        "SELECT COUNT(*) FROM deck_slides WHERE deck_id=?", (result.deck_id,)
    ) as cur:
        n_row = await cur.fetchone()
    assert n_row is not None
    assert n_row[0] == 2


@pytest.mark.asyncio
async def test_emit_runs_figure_audit_and_replaces_unknown_keys(
    conn: aiosqlite.Connection, tmp_path: Path
) -> None:
    """Contract #1 enforcement: \\includegraphics{not_in_inventory} -> replaced."""
    deck = r"""\documentclass{beamer}
\begin{document}
\begin{frame}{X}\includegraphics{nonexistent-key}\end{frame}
\end{document}
"""
    workdir = tmp_path / "slides"
    workdir.mkdir()
    (workdir / "deck.tex").write_text(deck, encoding="utf-8")

    inventory = {
        "p0-fig-001": KeyFigureBundle(
            key="p0-fig-001",
            role="overview",
            one_line_interpretation="x",
            dimensions=FigureDimensions(width_px=1000, height_px=1000),
        )
    }
    result = await run_sl_emit(
        session_id=1,
        run_id=1,
        deck_tex=deck,
        workdir=workdir,
        page_count=1,
        status="ok",
        contributing_paper_ids=[],
        figure_inventory=inventory,
        conn=conn,
    )
    async with conn.execute(
        "SELECT frame_tex FROM deck_slides WHERE deck_id=?", (result.deck_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert "nonexistent-key" not in row[0]
    assert "\\textit{[figure omitted]}" in row[0] or "figure omitted" in row[0]


@pytest.mark.asyncio
async def test_emit_writes_version_snapshot_under_edit_history(
    conn: aiosqlite.Connection, tmp_path: Path
) -> None:
    workdir = tmp_path / "slides"
    workdir.mkdir()
    (workdir / "deck.tex").write_text(_DECK, encoding="utf-8")
    result = await run_sl_emit(
        session_id=1,
        run_id=1,
        deck_tex=_DECK,
        workdir=workdir,
        page_count=2,
        status="ok",
        contributing_paper_ids=[],
        figure_inventory={},
        conn=conn,
    )
    edit_history = workdir / "edit_history"
    assert edit_history.exists()
    snapshots = list(edit_history.glob("version_*.json"))
    assert len(snapshots) == 1
    # current_version_id in DB matches the snapshot file stem.
    async with conn.execute(
        "SELECT current_version_id FROM decks WHERE id=?", (result.deck_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    cv_id = row[0]
    assert cv_id == snapshots[0].stem
