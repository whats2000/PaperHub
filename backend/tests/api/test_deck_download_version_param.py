"""F4.5 Phase 16: GET /deck/{pdf,tex}?version_id=<id> serves a cached version
PDF / the snapshot's tex_content; falls back to 404 with a specific detail when
the version has no cached PDF; omitted version_id keeps the existing behaviour."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient


def _write_snapshot(
    edit_history: Path,
    version_id: str,
    *,
    description: str,
    timestamp_iso: str,
    tex: str,
    speaker_notes: dict[str, str] | None = None,
    pdf_filename: str | None = None,
    pdf_bytes: bytes | None = None,
) -> None:
    payload: dict[str, Any] = {
        "tex_content": tex,
        "speaker_notes": speaker_notes if speaker_notes is not None else {},
        "description": description,
        "timestamp": timestamp_iso,
        "pdf_filename": pdf_filename,
    }
    (edit_history / f"{version_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if pdf_filename and pdf_bytes is not None:
        (edit_history / pdf_filename).write_bytes(pdf_bytes)


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
    pdf_path: Path | None,
    current_version_id: str,
    page_count: int = 1,
) -> None:
    await conn.execute(
        "INSERT INTO decks (session_id, tex_path, pdf_path, page_count, "
        "current_version_id, contributing_paper_ids_json) "
        "VALUES (?, ?, ?, ?, ?, '[]')",
        (
            session_id,
            str(tex_path),
            str(pdf_path) if pdf_path is not None else None,
            page_count,
            current_version_id,
        ),
    )
    await conn.commit()


@pytest.mark.asyncio
async def test_get_deck_pdf_with_version_id_serves_cached_bytes(
    tmp_path: Path,
    app_with_db: tuple[Any, aiosqlite.Connection],
) -> None:
    """?version_id=<v> with a cached PDF returns 200 + the cached bytes, and the
    Content-Disposition filename matches the version's own \\title{}."""
    app, conn = app_with_db
    session_id = 1
    slides_dir = tmp_path / "chat_session" / str(session_id) / "slides"
    edit_history = slides_dir / "edit_history"
    edit_history.mkdir(parents=True)
    older_id = "version_20260601_120000_000000"
    newer_id = "version_20260601_130000_000000"
    older_pdf = b"%PDF-1.4 older-bytes\n"
    newer_pdf = b"%PDF-1.4 newer-bytes\n"
    _write_snapshot(
        edit_history,
        older_id,
        description="older",
        timestamp_iso="2026-06-01T12:00:00",
        tex=(
            r"\documentclass{beamer}"
            r"\title{Older Talk Title}"
            r"\begin{document}\begin{frame}{X}y\end{frame}\end{document}"
        ),
        pdf_filename=f"{older_id}.pdf",
        pdf_bytes=older_pdf,
    )
    _write_snapshot(
        edit_history,
        newer_id,
        description="active",
        timestamp_iso="2026-06-01T13:00:00",
        tex=(
            r"\documentclass{beamer}"
            r"\title{Active Talk}"
            r"\begin{document}\begin{frame}{X}y\end{frame}\end{document}"
        ),
        pdf_filename=f"{newer_id}.pdf",
        pdf_bytes=newer_pdf,
    )
    # Seed the live deck.tex/deck.pdf pointing at the NEWER version.
    deck_tex = slides_dir / "deck.tex"
    deck_pdf = slides_dir / "deck.pdf"
    deck_tex.write_text(
        r"\documentclass{beamer}\title{Active Talk}\begin{document}"
        r"\begin{frame}{X}y\end{frame}\end{document}",
        encoding="utf-8",
    )
    deck_pdf.write_bytes(newer_pdf)

    await _seed_session(conn, session_id)
    await _seed_deck(
        conn,
        session_id=session_id,
        tex_path=deck_tex,
        pdf_path=deck_pdf,
        current_version_id=newer_id,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/sessions/{session_id}/deck/pdf",
            params={"version_id": older_id},
            headers={"X-Paperhub-Session-Id": str(session_id)},
        )

    assert resp.status_code == 200, resp.text
    assert resp.content == older_pdf
    # Filename comes from the OLDER version's own \title{}, not the active one.
    # Starlette emits non-ASCII / spaced filenames via RFC 5987 percent-encoding.
    disp = resp.headers.get("content-disposition", "")
    assert "Older%20Talk%20Title.pdf" in disp


@pytest.mark.asyncio
async def test_get_deck_pdf_with_version_id_404_when_pdf_not_cached(
    tmp_path: Path,
    app_with_db: tuple[Any, aiosqlite.Connection],
) -> None:
    """A legacy snapshot without pdf_filename → 404 with a helpful detail."""
    app, conn = app_with_db
    session_id = 1
    slides_dir = tmp_path / "chat_session" / str(session_id) / "slides"
    edit_history = slides_dir / "edit_history"
    edit_history.mkdir(parents=True)
    legacy_id = "version_20260601_110000_000000"
    active_id = "version_20260601_130000_000000"
    # Legacy snapshot: NO pdf_filename, NO cached .pdf on disk.
    (edit_history / f"{legacy_id}.json").write_text(
        json.dumps(
            {
                "tex_content": (
                    r"\documentclass{beamer}\title{Legacy}\begin{document}"
                    r"\begin{frame}{X}y\end{frame}\end{document}"
                ),
                "speaker_notes": {},
                "description": "legacy",
                "timestamp": "2026-06-01T11:00:00",
            }
        ),
        encoding="utf-8",
    )
    deck_tex = slides_dir / "deck.tex"
    deck_pdf = slides_dir / "deck.pdf"
    deck_tex.write_text("active tex", encoding="utf-8")
    deck_pdf.write_bytes(b"%PDF-1.4 active\n")

    await _seed_session(conn, session_id)
    await _seed_deck(
        conn,
        session_id=session_id,
        tex_path=deck_tex,
        pdf_path=deck_pdf,
        current_version_id=active_id,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/sessions/{session_id}/deck/pdf",
            params={"version_id": legacy_id},
            headers={"X-Paperhub-Session-Id": str(session_id)},
        )

    assert resp.status_code == 404, resp.text
    detail = resp.json().get("detail", "")
    assert "restore the version to recompile" in detail


@pytest.mark.asyncio
async def test_get_deck_pdf_without_version_id_serves_active(
    tmp_path: Path,
    app_with_db: tuple[Any, aiosqlite.Connection],
) -> None:
    """Omitted version_id keeps the existing behaviour: serve the active deck.pdf."""
    app, conn = app_with_db
    session_id = 1
    slides_dir = tmp_path / "chat_session" / str(session_id) / "slides"
    slides_dir.mkdir(parents=True)
    deck_tex = slides_dir / "deck.tex"
    deck_pdf = slides_dir / "deck.pdf"
    active_pdf = b"%PDF-1.4 active\n"
    deck_tex.write_text(
        r"\documentclass{beamer}\title{Active}\begin{document}"
        r"\begin{frame}{X}y\end{frame}\end{document}",
        encoding="utf-8",
    )
    deck_pdf.write_bytes(active_pdf)

    await _seed_session(conn, session_id)
    await _seed_deck(
        conn,
        session_id=session_id,
        tex_path=deck_tex,
        pdf_path=deck_pdf,
        current_version_id="version_20260601_130000_000000",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/sessions/{session_id}/deck/pdf",
            headers={"X-Paperhub-Session-Id": str(session_id)},
        )

    assert resp.status_code == 200, resp.text
    assert resp.content == active_pdf


@pytest.mark.asyncio
async def test_get_deck_pdf_invalid_version_id_400(
    tmp_path: Path,
    app_with_db: tuple[Any, aiosqlite.Connection],
) -> None:
    """A malformed version_id (path-traversal attempt) is rejected with 400
    before any file is opened."""
    app, conn = app_with_db
    session_id = 1
    slides_dir = tmp_path / "chat_session" / str(session_id) / "slides"
    slides_dir.mkdir(parents=True)
    deck_tex = slides_dir / "deck.tex"
    deck_pdf = slides_dir / "deck.pdf"
    deck_tex.write_text("x", encoding="utf-8")
    deck_pdf.write_bytes(b"x")

    await _seed_session(conn, session_id)
    await _seed_deck(
        conn,
        session_id=session_id,
        tex_path=deck_tex,
        pdf_path=deck_pdf,
        current_version_id="version_20260601_130000_000000",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/sessions/{session_id}/deck/pdf",
            params={"version_id": "../../../etc/passwd"},
            headers={"X-Paperhub-Session-Id": str(session_id)},
        )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_get_deck_tex_with_version_id_serves_snapshot_tex(
    tmp_path: Path,
    app_with_db: tuple[Any, aiosqlite.Connection],
) -> None:
    """?version_id=<v> on /deck/tex returns the snapshot's tex_content."""
    app, conn = app_with_db
    session_id = 1
    slides_dir = tmp_path / "chat_session" / str(session_id) / "slides"
    edit_history = slides_dir / "edit_history"
    edit_history.mkdir(parents=True)
    older_id = "version_20260601_120000_000000"
    older_tex = (
        r"\documentclass{beamer}\title{Older}\begin{document}"
        r"\begin{frame}{X}y\end{frame}\end{document}"
    )
    _write_snapshot(
        edit_history,
        older_id,
        description="older",
        timestamp_iso="2026-06-01T12:00:00",
        tex=older_tex,
        pdf_filename=f"{older_id}.pdf",
        pdf_bytes=b"x",
    )
    deck_tex = slides_dir / "deck.tex"
    deck_pdf = slides_dir / "deck.pdf"
    deck_tex.write_text("active tex", encoding="utf-8")
    deck_pdf.write_bytes(b"active pdf")

    await _seed_session(conn, session_id)
    await _seed_deck(
        conn,
        session_id=session_id,
        tex_path=deck_tex,
        pdf_path=deck_pdf,
        current_version_id="version_20260601_130000_000000",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/sessions/{session_id}/deck/tex",
            params={"version_id": older_id},
            headers={"X-Paperhub-Session-Id": str(session_id)},
        )

    assert resp.status_code == 200, resp.text
    assert resp.text == older_tex
    disp = resp.headers.get("content-disposition", "")
    assert "Older.tex" in disp
