"""``GET /notes/blocks/{id}``: which note a block is in (a ``[note:B]`` chip
in the chat opens the notes pane there)."""
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema
from paperhub.notes.ops import BlockIn, Op, apply_ops

pytestmark = pytest.mark.asyncio

EDITOR_ID = "5a070443-97b8-40ae-bbcc-ff555ee02d52"


@pytest_asyncio.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (id, title) VALUES (1, 'Chat')")
        await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=create_app()),
                           base_url="http://test") as c:
        yield c


async def test_block_lookup_names_the_note_and_its_target(
    client: AsyncClient, tmp_path: Path,
) -> None:
    nid = (await client.post("/notes/target", params={"kind": "session", "id": 1})
           ).json()["note"]["id"]
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_ops(conn, nid, base_revision=0, actor="user", ops=[
            Op(op="insert", block_id=EDITOR_ID,
               block=BlockIn(type="paragraph", content="mine"))])
    got = await client.get(f"/notes/blocks/{EDITOR_ID}")
    assert got.status_code == 200
    assert got.json() == {"note_id": nid, "target_kind": "session", "target_id": "1"}

    assert (await client.get("/notes/blocks/b_nosuchblock")).status_code == 404
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_ops(conn, nid, base_revision=1, actor="user",
                        ops=[Op(op="delete", block_id=EDITOR_ID)])
    assert (await client.get(f"/notes/blocks/{EDITOR_ID}")).status_code == 404


async def test_block_lookup_refuses_an_id_longer_than_any_block_id(
    client: AsyncClient,
) -> None:
    assert (await client.get(f"/notes/blocks/{'x' * 65}")).status_code == 422
    assert (await client.get(f"/notes/blocks/{'x' * 64}")).status_code == 404
