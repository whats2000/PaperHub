"""F4.5 Task 12.1: deck.current_version_id plumbing + GET /deck/versions.

Pins three behaviours:

1. ``GET /sessions/{sid}/deck`` echoes ``current_version_id`` (Task 10.1
   already fixed this — the test guards against regression).
2. ``GET /sessions/{sid}/deck/versions`` reads ``edit_history/version_*.json``
   from the session's slides workdir and returns one entry per snapshot with
   ``{version_id, timestamp, description, page_count, is_active}``. The active
   flag matches the snapshot whose filename stem equals ``decks.current_version_id``.
3. ``POST /sessions/{sid}/deck/versions/{file}/restore`` writes the snapshot's
   tex/notes back to disk AND updates ``decks.current_version_id`` to point at
   the restored snapshot.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient


def _write_snapshot(
    edit_history: Path,
    version_id: str,
    *,
    description: str,
    timestamp_iso: str,
    tex: str = r"\documentclass{beamer}\begin{document}"
    r"\begin{frame}{X}y\end{frame}\end{document}",
    speaker_notes: dict[str, str] | None = None,
    pdf_filename: str | None = None,
    pdf_bytes: bytes | None = None,
) -> None:
    payload: dict[str, Any] = {
        "tex_content": tex,
        "speaker_notes": speaker_notes if speaker_notes is not None else {},
        "description": description,
        "timestamp": timestamp_iso,
    }
    if pdf_filename is not None:
        payload["pdf_filename"] = pdf_filename
        if pdf_bytes is not None:
            (edit_history / pdf_filename).write_bytes(pdf_bytes)
    (edit_history / f"{version_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def _seed_session(conn: aiosqlite.Connection, session_id: int) -> None:
    await conn.execute(
        "INSERT INTO chat_sessions (id, created_at, title) "
        "VALUES (?, datetime('now'), 't')",
        (session_id,),
    )


async def _seed_deck(
    conn: aiosqlite.Connection,
    *,
    session_id: int,
    tex_path: Path,
    current_version_id: str,
    page_count: int = 1,
) -> None:
    await conn.execute(
        "INSERT INTO decks (session_id, tex_path, page_count, current_version_id, "
        "contributing_paper_ids_json) VALUES (?, ?, ?, ?, '[]')",
        (session_id, str(tex_path), page_count, current_version_id),
    )
    await conn.commit()


@pytest.mark.asyncio
async def test_get_deck_returns_current_version_id_field(
    app_with_db: tuple[Any, aiosqlite.Connection],
) -> None:
    """GET /deck echoes ``current_version_id`` (Task 10.1 regression guard)."""
    app, conn = app_with_db
    await _seed_session(conn, 1)
    await _seed_deck(
        conn,
        session_id=1,
        tex_path=Path("/tmp/d.tex"),
        current_version_id="version_test",
        page_count=5,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/sessions/1/deck",
            headers={"X-Paperhub-Session-Id": "1"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("current_version_id") == "version_test"


@pytest.mark.asyncio
async def test_get_deck_versions_lists_snapshots_with_active_flag(
    tmp_path: Path,
    app_with_db: tuple[Any, aiosqlite.Connection],
) -> None:
    """GET /deck/versions returns both snapshots; is_active flags the matching one."""
    app, conn = app_with_db
    session_id = 1
    slides_dir = tmp_path / "chat_session" / str(session_id) / "slides"
    edit_history = slides_dir / "edit_history"
    edit_history.mkdir(parents=True)
    older_id = "version_20260601_120000_000000"
    newer_id = "version_20260601_130000_000000"
    _write_snapshot(
        edit_history, older_id,
        description="older",
        timestamp_iso="2026-06-01T12:00:00",
    )
    _write_snapshot(
        edit_history, newer_id,
        description="active",
        timestamp_iso="2026-06-01T13:00:00",
    )

    await _seed_session(conn, session_id)
    await _seed_deck(
        conn,
        session_id=session_id,
        tex_path=slides_dir / "deck.tex",
        current_version_id=newer_id,
        page_count=1,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/sessions/{session_id}/deck/versions",
            headers={"X-Paperhub-Session-Id": str(session_id)},
        )

    assert resp.status_code == 200, resp.text
    versions = resp.json()
    assert isinstance(versions, list)
    assert len(versions) == 2
    by_id = {v["version_id"]: v for v in versions}
    assert by_id[newer_id]["is_active"] is True
    assert by_id[older_id]["is_active"] is False
    # Shape check on every entry.
    for v in versions:
        assert "version_id" in v
        assert "timestamp" in v
        assert "description" in v
        assert "page_count" in v
        assert "is_active" in v
    # Newest-first ordering.
    assert versions[0]["version_id"] == newer_id
    assert versions[1]["version_id"] == older_id


@pytest.mark.asyncio
async def test_restore_endpoint_updates_current_version_id(
    tmp_path: Path,
    app_with_db: tuple[Any, aiosqlite.Connection],
) -> None:
    """Restoring an older version must update decks.current_version_id.

    The older snapshot carries a cached PDF so the endpoint takes the Phase 16
    hot path (file-copy, no pdflatex), keeping this test runnable on CI hosts
    without a LaTeX install.
    """
    app, conn = app_with_db
    session_id = 1
    slides_dir = tmp_path / "chat_session" / str(session_id) / "slides"
    edit_history = slides_dir / "edit_history"
    edit_history.mkdir(parents=True)
    older_id = "version_20260601_120000_000000"
    newer_id = "version_20260601_130000_000000"
    _write_snapshot(
        edit_history, older_id,
        description="older",
        timestamp_iso="2026-06-01T12:00:00",
        pdf_filename=f"{older_id}.pdf",
        pdf_bytes=b"%PDF-1.4 cached pdf\n",
    )
    _write_snapshot(
        edit_history, newer_id,
        description="active",
        timestamp_iso="2026-06-01T13:00:00",
    )

    await _seed_session(conn, session_id)
    await _seed_deck(
        conn,
        session_id=session_id,
        tex_path=slides_dir / "deck.tex",
        current_version_id=newer_id,
        page_count=1,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/sessions/{session_id}/deck/versions/{older_id}/restore",
            headers={"X-Paperhub-Session-Id": str(session_id)},
        )

    assert resp.status_code in (200, 201), resp.text

    async with conn.execute(
        "SELECT current_version_id FROM decks WHERE session_id = ?", (session_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == older_id


@pytest.mark.asyncio
async def test_restore_endpoint_uses_cached_pdf_when_available(
    tmp_path: Path,
    app_with_db: tuple[Any, aiosqlite.Connection],
) -> None:
    """F4.5 Task 16.2 hot path: snapshot has a cached PDF → restore skips the
    recompile, copies the cache to deck.pdf, and reports cache_hit=true."""
    app, conn = app_with_db
    session_id = 1
    slides_dir = tmp_path / "chat_session" / str(session_id) / "slides"
    edit_history = slides_dir / "edit_history"
    edit_history.mkdir(parents=True)
    older_id = "version_20260601_120000_000000"
    newer_id = "version_20260601_130000_000000"
    cached_pdf_bytes = b"%PDF-1.4 cached pdf for v1\n"
    _write_snapshot(
        edit_history, older_id,
        description="older",
        timestamp_iso="2026-06-01T12:00:00",
        pdf_filename=f"{older_id}.pdf",
        pdf_bytes=cached_pdf_bytes,
    )
    _write_snapshot(
        edit_history, newer_id,
        description="active",
        timestamp_iso="2026-06-01T13:00:00",
    )
    # Put a stale deck.pdf on disk so we can prove restore overwrites it.
    (slides_dir / "deck.pdf").write_bytes(b"%PDF-1.4 stale\n")

    await _seed_session(conn, session_id)
    await _seed_deck(
        conn,
        session_id=session_id,
        tex_path=slides_dir / "deck.tex",
        current_version_id=newer_id,
        page_count=1,
    )

    # Mock the recompile entrypoint so we can assert it was NOT called on the
    # hot path. Patching the module the endpoint imports it through.
    fake_compile = AsyncMock()
    transport = ASGITransport(app=app)
    with patch(
        "paperhub.api.decks.compile_mod.compile_with_revise", fake_compile
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/sessions/{session_id}/deck/versions/{older_id}/restore",
                headers={"X-Paperhub-Session-Id": str(session_id)},
            )

    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert body.get("cache_hit") is True
    assert body.get("status") == "ok"
    fake_compile.assert_not_called()
    # The cached PDF bytes are now on disk as deck.pdf.
    assert (slides_dir / "deck.pdf").read_bytes() == cached_pdf_bytes


@pytest.mark.asyncio
async def test_restore_endpoint_recompiles_when_legacy_snapshot(
    tmp_path: Path,
    app_with_db: tuple[Any, aiosqlite.Connection],
) -> None:
    """F4.5 Task 16.2 legacy path: snapshot without pdf_filename → existing
    recompile flow runs unchanged, response carries cache_hit=false."""
    app, conn = app_with_db
    session_id = 1
    slides_dir = tmp_path / "chat_session" / str(session_id) / "slides"
    edit_history = slides_dir / "edit_history"
    edit_history.mkdir(parents=True)
    older_id = "version_20260601_120000_000000"
    newer_id = "version_20260601_130000_000000"
    _write_snapshot(
        edit_history, older_id,
        description="older legacy (no pdf_filename)",
        timestamp_iso="2026-06-01T12:00:00",
    )
    _write_snapshot(
        edit_history, newer_id,
        description="active",
        timestamp_iso="2026-06-01T13:00:00",
    )

    await _seed_session(conn, session_id)
    await _seed_deck(
        conn,
        session_id=session_id,
        tex_path=slides_dir / "deck.tex",
        current_version_id=newer_id,
        page_count=1,
    )

    # Fake the recompile so we don't need a real pdflatex; the endpoint reads
    # ok / page_count / tex off the CompileResult.
    from paperhub.pipelines.slide_pipeline.compile import CompileResult
    fake_compile = AsyncMock(
        return_value=CompileResult(
            ok=True,
            attempts=1,
            tex=r"\documentclass{beamer}\begin{document}\end{document}",
            log="",
            page_count=1,
        )
    )
    transport = ASGITransport(app=app)
    with patch(
        "paperhub.api.decks.compile_mod.compile_with_revise", fake_compile
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/sessions/{session_id}/deck/versions/{older_id}/restore",
                headers={"X-Paperhub-Session-Id": str(session_id)},
            )

    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert body.get("cache_hit") is False
    fake_compile.assert_called_once()
