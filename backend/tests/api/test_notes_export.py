"""GET /notes/{id}/export and /notes/export/capabilities."""
from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema
from paperhub.notes import comments as note_comments
from paperhub.notes import pandoc as note_pandoc

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_schema(conn)
        await conn.execute(
            "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
            "authors_json, year, source_path, source_dir_path, html_path) "
            "VALUES (1, 'arxiv:1', 'arxiv', '1706.03762', 'Attention Is All You Need', "
            "'[\"Ashish Vaswani\"]', 2017, 's', ?, 'h')", (str(tmp_path / "none"),))
        await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=create_app()),
                           base_url="http://test") as c:
        yield c


async def _paper_note(client: AsyncClient) -> int:
    r = await client.get("/notes/target", params={"kind": "paper", "id": 1})
    return int(r.json()["note"]["id"])


async def _write(client: AsyncClient, nid: int, blocks: list[dict]) -> list[str]:
    r = await client.post(f"/notes/{nid}/ops", json={
        "base_revision": 0,
        "ops": [{"op": "insert", "block": b} for b in blocks]})
    assert r.status_code == 200, r.text
    return list(r.json()["applied"])


_QUOTE = {"type": "paperQuote", "section_key": "3 Model", "props": {
    "kind": "quote", "quote": "Attention maps a query to an output.",
    "pieces": [{"quote": "Attention maps a query to an output.", "page": 2,
                "rects": [[0.1, 0.1, 0.5, 0.2]]}],
    "source": {"page": 2, "section": "3 Model"}}}


def _zip(body: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        return {n: z.read(n) for n in z.namelist()}


async def test_markdown_zip_download(client: AsyncClient) -> None:
    nid = await _paper_note(client)
    await _write(client, nid, [_QUOTE, {
        "type": "paperFigure", "props": {
            "kind": "figure", "quote": "The Transformer",
            "pieces": [{"page": 1, "box": [0.1, 0.1, 0.5, 0.5]}],
            "source": {"page": 1}}}])
    r = await client.get(f"/notes/{nid}/export", params={"format": "md"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert ('filename="attention-is-all-you-need-notes.zip"'
            in r.headers["content-disposition"])
    files = _zip(r.content)
    assert set(files) == {"note.md", "references.bib"}  # the crop is unavailable
    md = files["note.md"].decode()
    assert "> Attention maps a query to an output." in md
    assert "[@vaswani2017attention]" in md
    assert "The Transformer\n\n[image unavailable]" in md
    assert b"@misc{vaswani2017attention," in files["references.bib"]


async def test_capabilities_reports_pandoc(client: AsyncClient,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(note_pandoc, "pandoc_available", lambda: False)
    r = await client.get("/notes/export/capabilities")
    assert r.json() == {"md": True, "docx": False, "docx_reason": "pandoc_missing"}
    monkeypatch.setattr(note_pandoc, "pandoc_available", lambda: True)
    monkeypatch.setattr(note_pandoc, "pandoc_version", lambda: (3, 1, 9))
    assert (await client.get("/notes/export/capabilities")).json() == {
        "md": True, "docx": True, "docx_reason": None}


async def test_docx_without_pandoc_409_reason(client: AsyncClient,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(note_pandoc, "pandoc_available", lambda: False)
    nid = await _paper_note(client)
    r = await client.get(f"/notes/{nid}/export", params={"format": "docx"})
    assert r.status_code == 409
    assert r.json() == {"detail": "pandoc_missing"}


async def test_docx_with_pandoc_without_sandbox_409(
        client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """pandoc older than 2.15 has no --sandbox: Word is refused, not run."""
    monkeypatch.setattr(note_pandoc, "pandoc_available", lambda: True)
    monkeypatch.setattr(note_pandoc, "pandoc_version", lambda: (2, 14, 2))
    caps = (await client.get("/notes/export/capabilities")).json()
    assert caps == {"md": True, "docx": False, "docx_reason": "pandoc_too_old"}
    nid = await _paper_note(client)
    r = await client.get(f"/notes/{nid}/export", params={"format": "docx"})
    assert r.status_code == 409
    assert r.json() == {"detail": "pandoc_too_old"}


async def test_download_name_keeps_a_cjk_title(client: AsyncClient,
                                               tmp_path: Path) -> None:
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await conn.execute("INSERT INTO chat_sessions (id, title) VALUES (5, '注意力 筆記')")
        await conn.commit()
    nid = (await client.get("/notes/target", params={"kind": "session", "id": 5})
           ).json()["note"]["id"]
    r = await client.get(f"/notes/{nid}/export", params={"format": "md"})
    cd = r.headers["content-disposition"]
    assert 'filename="note-notes.zip"' in cd
    assert ("filename*=UTF-8''%E6%B3%A8%E6%84%8F%E5%8A%9B%20%E7%AD%86%E8%A8%98"
            "-notes.zip") in cd


async def test_export_unknown_note_and_format(client: AsyncClient) -> None:
    assert (await client.get("/notes/999/export", params={"format": "md"})
            ).status_code == 404
    nid = await _paper_note(client)
    assert (await client.get(f"/notes/{nid}/export", params={"format": "pdf"})
            ).status_code == 422


async def test_export_recorded(client: AsyncClient, tmp_path: Path) -> None:
    nid = await _paper_note(client)
    await _write(client, nid, [_QUOTE])
    assert (await client.get(f"/notes/{nid}/export", params={"format": "md"})
            ).status_code == 200
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn, conn.execute(
        "SELECT owner_id, actor, note_id, format, created_at FROM note_exports"
    ) as cur:
        rows = await cur.fetchall()
    assert len(rows) == 1
    owner, actor, note_id, fmt, at = rows[0]
    assert (owner, actor, note_id, fmt) == (1, "user", nid, "md") and at


@pytest.mark.parametrize("fmt", [
    "md",
    pytest.param("docx", marks=pytest.mark.skipif(
        shutil.which("pandoc") is None, reason="pandoc not installed")),
])
async def test_comments_not_exported(client: AsyncClient, tmp_path: Path,
                                     fmt: str) -> None:
    nid = await _paper_note(client)
    [bid] = await _write(client, nid, [{"type": "paragraph", "content": [
        {"type": "text", "text": "We disagree with ", "styles": {}},
        {"type": "text", "text": "this claim", "styles": {"comment": "PLACEHOLDER"}},
        {"type": "text", "text": " entirely.", "styles": {}}]}])
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        thread = await note_comments.create_comment(
            conn, nid, block_id=bid, quote="this claim",
            text="ZebraThreadRemark about it")
    r = await client.get(f"/notes/{nid}/export", params={"format": fmt})
    assert r.status_code == 200
    if fmt == "md":
        text = _zip(r.content)["note.md"].decode()
    else:
        text = _zip(r.content)["word/document.xml"].decode()
        assert "w:comment" not in text
    assert "this claim" in text and "entirely." in text
    assert "ZebraThreadRemark" not in text
    assert thread["id"] not in text and "PLACEHOLDER" not in text


@pytest.mark.parametrize("fmt", [
    "md",
    pytest.param("docx", marks=pytest.mark.skipif(
        shutil.which("pandoc") is None, reason="pandoc not installed")),
])
async def test_lone_surrogate_in_json_pieces_exports(client: AsyncClient,
                                                     fmt: str) -> None:
    nid = await _paper_note(client)
    # The editor's shape: pieces and source as JSON strings.
    await _write(client, nid, [{"type": "paperQuote", "props": {
        "kind": "quote", "quote": "q",
        "pieces": json.dumps([{"quote": "a" + chr(0xD83D) + " b", "page": 0}]),
        "source": json.dumps({"section": "S" + chr(0xD83D), "page": 0})}}])
    r = await client.get(f"/notes/{nid}/export", params={"format": fmt})
    assert r.status_code == 200, r.text
    if fmt == "md":
        assert "> a b" in _zip(r.content)["note.md"].decode("utf-8")


async def test_unexpected_export_error_is_a_clean_500(
        client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    from paperhub.api import notes as notes_api

    def boom(*args: object, **kwargs: object) -> None:
        raise UnicodeEncodeError("utf-8", "x", 0, 1, "surrogates not allowed")
    monkeypatch.setattr(notes_api, "note_to_markdown", boom)
    nid = await _paper_note(client)
    r = await client.get(f"/notes/{nid}/export", params={"format": "md"})
    assert r.status_code == 500
    assert r.json() == {"detail": "export_failed"}
    assert "export failed" in caplog.text


async def test_download_name_drops_controls_and_bidi(client: AsyncClient,
                                                     tmp_path: Path) -> None:
    title = "a\u202egpj.exe\u2066b\r\nc\x85d\u2028e"
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await conn.execute("INSERT INTO chat_sessions (id, title) VALUES (6, ?)", (title,))
        await conn.commit()
    nid = (await client.get("/notes/target", params={"kind": "session", "id": 6})
           ).json()["note"]["id"]
    r = await client.get(f"/notes/{nid}/export", params={"format": "md"})
    cd = r.headers["content-disposition"]
    assert "filename*=UTF-8''agpj.exeb-cde-notes.zip" in cd, cd
