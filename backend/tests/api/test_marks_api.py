from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema

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
            "sections_json, source_path, source_dir_path, html_path) "
            "VALUES (1, 'arxiv:1', 'arxiv', '1', 'A Paper', "
            "'[{\"name\": \"Intro\"}]', 's', 'd', 'h')")
        await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=create_app()),
                           base_url="http://test") as c:
        yield c


def _piece(q: str, start: int = 0) -> dict:
    return {"quote": q, "chunk_start": start, "chunk_end": start + len(q),
            "prefix": "", "suffix": ""}


def _body(**kw) -> dict:
    return {"paper_content_id": 1, "kind": "highlight", "color": "green",
            "view": "html", "pieces": [_piece("a key result")],
            "section_key": "Intro", **kw}


async def test_marks_roundtrip_and_saved_to(client: AsyncClient) -> None:
    r = await client.post("/marks", json=_body())
    assert r.status_code == 200, r.text
    out = r.json()
    mark = out["mark"]
    assert out["block"]["id"] == mark["block_id"]
    st = out["saved_to"]
    assert st["paper_title"] == "A Paper" and st["section"] == "Intro"
    assert st["part_index"] == 0 and st["part_count"] == 1
    listed = (await client.get("/marks", params={"paper_content_id": 1})).json()
    assert [m["id"] for m in listed] == [mark["id"]]
    assert listed[0]["part_label"] == "Intro" and listed[0]["note_id"] == st["note_id"]

    assert (await client.post("/marks", json=_body(kind="nonsense"))
            ).status_code == 422
    assert (await client.post("/marks", json=_body(paper_content_id=9))
            ).status_code == 404


async def test_mark_delete_restore_via_api(client: AsyncClient) -> None:
    mark = (await client.post("/marks", json=_body())).json()["mark"]
    mid = mark["id"]
    d = await client.delete(f"/marks/{mid}")
    assert d.status_code == 200 and d.json()["applied"] == [mark["block_id"]]
    assert (await client.get("/marks", params={"paper_content_id": 1})).json() == []
    assert (await client.post(f"/marks/{mid}/restore")).status_code == 200
    assert len((await client.get("/marks", params={"paper_content_id": 1})).json()) == 1
    assert (await client.delete("/marks/mk_nope")).status_code == 404


async def test_deleting_a_deleted_mark_is_a_200_not_a_409(client: AsyncClient) -> None:
    mark = (await client.post("/marks", json=_body())).json()["mark"]
    first = await client.delete(f"/marks/{mark['id']}")
    again = await client.delete(f"/marks/{mark['id']}")
    assert again.status_code == 200
    assert again.json()["applied"] == [mark["block_id"]]
    assert again.json()["revision"] == first.json()["revision"]
    assert again.json()["change_set_id"] is None


async def test_pieces_erase_restore_split(client: AsyncClient) -> None:
    mid = (await client.post("/marks", json=_body())).json()["mark"]["id"]
    joined = await client.post("/marks", json=_body(
        join_to=mid, pieces=[_piece("second", 50)]))
    assert joined.status_code == 200 and len(joined.json()["mark"]["pieces"]) == 2
    e = await client.delete(f"/marks/{mid}/pieces/0")
    assert e.status_code == 200 and e.json()["pieces"][0]["erased"] is True
    r = await client.post(f"/marks/{mid}/pieces/0/restore")
    assert "erased" not in r.json()["pieces"][0]
    assert (await client.delete(f"/marks/{mid}/pieces/7")).status_code == 422
    s = await client.post(f"/marks/{mid}/split")
    assert s.status_code == 200 and s.json()["id"] != mid
    # The original as it now is, so the reader needn't guess what moved.
    orig = s.json()["original"]
    assert orig["id"] == mid and orig["pieces"][1]["removed"] is True
    # One live piece left: the mark's state (a stale view), a conflict.
    assert (await client.post(f"/marks/{mid}/split")).status_code == 409


async def test_colors_patch(client: AsyncClient) -> None:
    cols = (await client.get("/marks/colors")).json()
    assert [c["color"] for c in cols] == ["green", "blue", "pink", "purple"]
    assert cols[0]["name"] == "Key result" and cols[0]["visible"] is True
    r = await client.patch("/marks/colors/green",
                           json={"name": "Result", "visible": False})
    assert r.status_code == 200
    assert (r.json()[0]["name"], r.json()[0]["visible"]) == ("Result", False)
    assert (await client.patch("/marks/colors/red", json={"name": "x"})
            ).status_code == 404


async def test_custom_colour_lifecycle(client: AsyncClient) -> None:
    r = await client.post("/marks/colors", json={"hex": "#1f8a8a", "name": ""})
    assert r.status_code == 422  # a label is required
    r = await client.post("/marks/colors", json={"hex": "#fde047", "name": "Y"})
    assert r.status_code == 422 and "yellow" in r.json()["detail"]
    r = await client.post("/marks/colors",
                          json={"hex": "#1f8a8a", "name": "To verify"})
    assert r.status_code == 200
    new = r.json()[-1]
    assert new["name"] == "To verify" and new["builtin"] is False
    order = [new["color"], "green", "blue", "pink", "purple"]
    r = await client.put("/marks/colors/order", json={"order": order})
    assert [c["color"] for c in r.json()] == order
    r = await client.delete(f"/marks/colors/{new['color']}")
    assert r.status_code == 200 and len(r.json()) == 4
    assert (await client.delete("/marks/colors/green")).status_code == 422


async def test_unexpected_store_error_is_a_500(
    client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paperhub.notes.marks as marks_mod

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("library bug")

    monkeypatch.setattr(marks_mod, "_validate", boom)
    async with AsyncClient(
        transport=ASGITransport(app=create_app(), raise_app_exceptions=False),
        base_url="http://test",
    ) as c:
        assert (await c.post("/marks", json=_body())).status_code == 500


async def test_short_mark_lands_as_an_inline_citation_and_undo_redo(
        client: AsyncClient) -> None:
    """[user, 2026-11-21] A short quote: an inline citation at the caret;
    Undo of its creation removes the chip, Redo puts it back."""
    r = await client.post("/marks", json=_body(
        pieces=[_piece("62%")], cite_at={"block_id": None, "offset": None}))
    assert r.status_code == 200, r.text
    out = r.json()
    mark, cite = out["mark"], out["cite"]
    assert out["block"] is None and cite["new_block"] is True
    assert mark["cite_block_id"] == cite["block_id"]
    assert mark["block_props"]["markId"] == mark["id"]
    r = await client.delete(f"/marks/{mark['id']}", params={
        "remove_cite": "true", "drop_block": cite["block_id"]})
    assert r.status_code == 200, r.text
    assert (await client.get("/marks", params={"paper_content_id": 1})
            ).json() == []
    r = await client.post(f"/marks/{mark['id']}/restore", json={
        "cite_at": {"block_id": cite["block_id"], "offset": 0}})
    assert r.status_code == 200, r.text
    [back] = (await client.get("/marks", params={"paper_content_id": 1})).json()
    assert back["cite_block_id"] == cite["block_id"]
    # A plain restore (no body) still works.
    await client.delete(f"/marks/{mark['id']}")
    assert (await client.post(f"/marks/{mark['id']}/restore")).status_code == 200
