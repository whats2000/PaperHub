"""Tests for /memories REST surface (FR-11 — UI-driven memory curation).

Boot mechanism mirrors test_sessions_api.py:
  * monkeypatch PAPERHUB_PREWARM_MODELS=0 + PAPERHUB_WORKSPACE=tmp_path
  * seed the DB with aiosqlite before the app touches it
  * ASGI test client via httpx ASGITransport + AsyncClient
"""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema

pytestmark = pytest.mark.asyncio

_HDR = "X-Paperhub-Session-Id"


@pytest_asyncio.fixture
async def mem_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncClient:
    """ASGI test client with an empty DB bootstrapped, prewarm disabled."""
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed(tmp_path: Path) -> Path:
    """Return the DB path (already schema-applied by the fixture)."""
    return tmp_path / "paperhub.db"


async def _insert_global(db_path: Path, content: str) -> int:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO memories (scope, session_id, content) VALUES ('global', NULL, ?)",
            (content,),
        )
        await conn.commit()
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _insert_session(db_path: Path, session_id: int, content: str) -> int:
    async with aiosqlite.connect(db_path) as conn:
        # Ensure the chat_sessions row exists (memories FK on session_id ON
        # DELETE CASCADE, so we need the parent row).
        await conn.execute(
            "INSERT OR IGNORE INTO chat_sessions (id) VALUES (?)", (session_id,)
        )
        await conn.execute(
            "INSERT INTO memories (scope, session_id, content) VALUES ('session', ?, ?)",
            (session_id, content),
        )
        await conn.commit()
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            row = await cur.fetchone()
    assert row is not None
    return int(row[0])


# ---------------------------------------------------------------------------
# GET /memories
# ---------------------------------------------------------------------------


async def test_get_memories_returns_list_200(
    mem_client: AsyncClient, tmp_path: Path
) -> None:
    """GET /memories?session_id=1 returns 200 with a list (may be empty)."""
    resp = await mem_client.get("/memories", params={"session_id": "1"})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_get_memories_includes_global_and_session(
    mem_client: AsyncClient, tmp_path: Path
) -> None:
    """GET /memories?session_id=1 includes global rows + this session's rows,
    but NOT another session's rows."""
    db_path = tmp_path / "paperhub.db"
    gid = await _insert_global(db_path, "global fact")
    sid1 = await _insert_session(db_path, 1, "session 1 note")
    sid2 = await _insert_session(db_path, 2, "session 2 note")

    resp = await mem_client.get("/memories", params={"session_id": "1"})
    assert resp.status_code == 200
    items = resp.json()
    ids = {i["id"] for i in items}
    assert gid in ids, "global row must be included"
    assert sid1 in ids, "own session row must be included"
    assert sid2 not in ids, "other session row must be excluded"


async def test_get_memories_includes_superseded_rows(
    mem_client: AsyncClient, tmp_path: Path
) -> None:
    """Both active and superseded rows are returned (so the UI can show the
    full memory timeline, including replaced entries)."""
    db_path = tmp_path / "paperhub.db"
    old_id = await _insert_global(db_path, "old preference")
    new_id = await _insert_global(db_path, "new preference")
    # Mark old_id as superseded.
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE memories SET status='superseded', superseded_by=? WHERE id=?",
            (new_id, old_id),
        )
        await conn.execute(
            "UPDATE memories SET supersedes=? WHERE id=?",
            (old_id, new_id),
        )
        await conn.commit()

    resp = await mem_client.get("/memories", params={"session_id": "1"})
    assert resp.status_code == 200
    items = resp.json()
    ids = {i["id"] for i in items}
    assert old_id in ids, "superseded row must still appear"
    assert new_id in ids, "active row must appear"

    # supersedes / superseded_by fields are present.
    new_row = next(i for i in items if i["id"] == new_id)
    old_row = next(i for i in items if i["id"] == old_id)
    assert new_row["supersedes"] == old_id
    assert old_row["superseded_by"] == new_id


# ---------------------------------------------------------------------------
# PATCH /memories/{id}
# ---------------------------------------------------------------------------


async def test_patch_memory_status_returns_200_and_superseded(
    mem_client: AsyncClient, tmp_path: Path
) -> None:
    """PATCH content/status → 200, returned row reflects the change."""
    db_path = tmp_path / "paperhub.db"
    mid = await _insert_global(db_path, "some global fact")

    resp = await mem_client.patch(
        f"/memories/{mid}",
        json={"status": "superseded"},
        headers={_HDR: "1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == mid
    assert data["status"] == "superseded"


async def test_patch_memory_content_returns_200_and_updated_content(
    mem_client: AsyncClient, tmp_path: Path
) -> None:
    """PATCH with new content → 200 with updated content."""
    db_path = tmp_path / "paperhub.db"
    mid = await _insert_global(db_path, "original content")

    resp = await mem_client.patch(
        f"/memories/{mid}",
        json={"content": "updated content"},
        headers={_HDR: "1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == mid
    assert data["content"] == "updated content"


async def test_patch_memory_404_for_missing(
    mem_client: AsyncClient,
) -> None:
    resp = await mem_client.patch(
        "/memories/99999",
        json={"status": "superseded"},
        headers={_HDR: "1"},
    )
    assert resp.status_code == 404


async def test_patch_memory_403_wrong_session(
    mem_client: AsyncClient, tmp_path: Path
) -> None:
    """Session-scoped memory cannot be patched from a different session."""
    db_path = tmp_path / "paperhub.db"
    mid = await _insert_session(db_path, 1, "session 1 note")

    resp = await mem_client.patch(
        f"/memories/{mid}",
        json={"content": "hijacked"},
        headers={_HDR: "2"},  # wrong session
    )
    assert resp.status_code == 403


async def test_patch_memory_global_accessible_from_any_session(
    mem_client: AsyncClient, tmp_path: Path
) -> None:
    """Global memories are editable from any session_id."""
    db_path = tmp_path / "paperhub.db"
    mid = await _insert_global(db_path, "global content")

    resp = await mem_client.patch(
        f"/memories/{mid}",
        json={"content": "edited by session 99"},
        headers={_HDR: "99"},
    )
    assert resp.status_code == 200
    assert resp.json()["content"] == "edited by session 99"


async def test_patch_memory_invalid_status_422(
    mem_client: AsyncClient, tmp_path: Path
) -> None:
    """PATCH with an invalid status value → 422."""
    db_path = tmp_path / "paperhub.db"
    mid = await _insert_global(db_path, "some fact")

    resp = await mem_client.patch(
        f"/memories/{mid}",
        json={"status": "deleted"},  # not a valid status
        headers={_HDR: "1"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /memories/{id}
# ---------------------------------------------------------------------------


async def test_delete_memory_returns_200_or_204(
    mem_client: AsyncClient, tmp_path: Path
) -> None:
    """DELETE /memories/{id} → 200 or 204 (match papers.py shape)."""
    db_path = tmp_path / "paperhub.db"
    mid = await _insert_global(db_path, "to be deleted")

    resp = await mem_client.delete(
        f"/memories/{mid}",
        headers={_HDR: "1"},
    )
    assert resp.status_code in (200, 204)


async def test_delete_memory_404_for_missing(
    mem_client: AsyncClient,
) -> None:
    resp = await mem_client.delete(
        "/memories/99999",
        headers={_HDR: "1"},
    )
    assert resp.status_code == 404


async def test_delete_memory_403_wrong_session(
    mem_client: AsyncClient, tmp_path: Path
) -> None:
    """Session-scoped memory cannot be deleted from a different session."""
    db_path = tmp_path / "paperhub.db"
    mid = await _insert_session(db_path, 1, "session 1 note")

    resp = await mem_client.delete(
        f"/memories/{mid}",
        headers={_HDR: "2"},
    )
    assert resp.status_code == 403


async def test_delete_memory_row_is_gone(
    mem_client: AsyncClient, tmp_path: Path
) -> None:
    """After DELETE the row should no longer appear in GET /memories."""
    db_path = tmp_path / "paperhub.db"
    mid = await _insert_global(db_path, "to be forgotten")

    await mem_client.delete(f"/memories/{mid}", headers={_HDR: "1"})

    resp = await mem_client.get("/memories", params={"session_id": "1"})
    ids = {i["id"] for i in resp.json()}
    assert mid not in ids, "deleted memory must not appear in listing"


# ---------------------------------------------------------------------------
# POST /memories
# ---------------------------------------------------------------------------


async def test_post_global_memory_returns_row_and_appears_in_get(
    mem_client: AsyncClient, tmp_path: Path
) -> None:
    """POST global memory → 200/201, scope=global, status=active, appears in GET.

    Conflict-detection short-circuits (no existing same-scope active rows) so
    no real LLM call is made — add_memory_with_supersede fails open without a key.
    """
    db_path = tmp_path / "paperhub.db"
    # Ensure the chat_sessions row exists for header session 1.
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO chat_sessions (id) VALUES (?)", (1,)
        )
        await conn.commit()

    resp = await mem_client.post(
        "/memories",
        json={"content": "always answer in English", "scope": "global"},
        headers={_HDR: "1"},
    )
    assert resp.status_code in (200, 201)
    data = resp.json()
    assert data["scope"] == "global"
    assert data["status"] == "active"
    assert data["content"] == "always answer in English"

    # Must appear in a subsequent GET.
    get_resp = await mem_client.get("/memories", params={"session_id": "1"})
    assert get_resp.status_code == 200
    ids = {i["id"] for i in get_resp.json()}
    assert data["id"] in ids


async def test_post_session_memory_returns_row_with_session_id(
    mem_client: AsyncClient, tmp_path: Path
) -> None:
    """POST session-scoped memory → 200/201, scope=session, session_id=1."""
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO chat_sessions (id) VALUES (?)", (1,)
        )
        await conn.commit()

    resp = await mem_client.post(
        "/memories",
        json={"content": "this project uses pytest", "scope": "session"},
        headers={_HDR: "1"},
    )
    assert resp.status_code in (200, 201)
    data = resp.json()
    assert data["scope"] == "session"
    assert data["session_id"] == 1
    assert data["status"] == "active"


async def test_post_sensitive_memory_returns_422_and_not_stored(
    mem_client: AsyncClient, tmp_path: Path
) -> None:
    """POST a sensitive memory (looks like an API key) → 422 (gate refusal).

    The row must NOT appear in a subsequent GET.
    """
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO chat_sessions (id) VALUES (?)", (1,)
        )
        await conn.commit()

    resp = await mem_client.post(
        "/memories",
        json={"content": "my key is sk-abc123longkeyvalue000", "scope": "global"},
        headers={_HDR: "1"},
    )
    assert resp.status_code == 422

    # Confirm not stored.
    get_resp = await mem_client.get("/memories", params={"session_id": "1"})
    contents = [i["content"] for i in get_resp.json()]
    assert not any("sk-abc123longkeyvalue000" in c for c in contents)


async def test_post_session_memory_without_session_header_returns_error(
    mem_client: AsyncClient,
) -> None:
    """POST scope=session with no X-Paperhub-Session-Id header → 400/422."""
    resp = await mem_client.post(
        "/memories",
        json={"content": "this project uses pytest", "scope": "session"},
        # No X-Paperhub-Session-Id header.
    )
    assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# CORS preflight regression
# ---------------------------------------------------------------------------


async def test_cors_preflight_allows_paperhub_session_id_header(
    mem_client: AsyncClient,
) -> None:
    """OPTIONS preflight for a memory mutation must include X-Paperhub-Session-Id
    in Access-Control-Allow-Headers.

    Guards the CORS fix in app.py: without it the browser preflight for
    PATCH/DELETE/POST with X-Paperhub-Session-Id is rejected before reaching us.
    """
    resp = await mem_client.options(
        "/memories/1",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "x-paperhub-session-id",
        },
    )
    # Starlette CORS middleware returns 200 for a valid preflight.
    assert resp.status_code == 200
    allow_headers = resp.headers.get("access-control-allow-headers", "").lower()
    assert "x-paperhub-session-id" in allow_headers, (
        f"X-Paperhub-Session-Id not in Access-Control-Allow-Headers: {allow_headers!r}"
    )
