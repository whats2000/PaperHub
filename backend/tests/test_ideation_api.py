"""Plan H3 Task 6: the ideation REST surface — graph scoping, the idea
lifecycle (prev_status, dismiss reasons, undo), idea chat sessions and manual
domain edits."""
from __future__ import annotations

import json
from typing import Any

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from paperhub.config import load_settings
from paperhub.ideation import agent as ideation_agent
from paperhub.ideation.domains import assign_color_indices, assign_domains


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _doc(
    conn: aiosqlite.Connection, n: int, *, private: bool = False,
    domains: list[str] | None = None,
) -> int:
    cur = await conn.execute(
        "INSERT INTO paper_content (content_key, kind, sha256, title, source_path, "
        "source_dir_path, html_path, is_private) "
        "VALUES (?, 'pdf_upload', ?, ?, '', '', '', ?)",
        (f"pdf:{n}", f"{n:064d}", f"Doc {n}", int(private)),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    pid = int(cur.lastrowid)
    if domains:  # what a card build does: tag, then colour
        await assign_domains(conn, pid, domains)
        await assign_color_indices(conn)
    return pid


async def _problem(conn: aiosqlite.Connection, text: str) -> int:
    cur = await conn.execute(
        "INSERT INTO problems (text, status) VALUES (?, 'ready')", (text,))
    await conn.commit()
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


async def _idea(
    conn: aiosqlite.Connection, *, source: int, problem: int | None = None,
    target: int | None = None, status: str = "new", title: str = "An idea",
) -> int:
    cur = await conn.execute(
        "INSERT INTO ideas (problem_id, source_doc, source_section, "
        "source_chunk_ids_json, target_doc, target_section, target_chunk_ids_json, "
        "title, rationale, status) VALUES (?, ?, 'Method', '[11, 12]', ?, ?, ?, ?, "
        "'Because anchors generalise.', ?)",
        (problem, source, target, "Results" if target is not None else None,
         "[21]" if target is not None else "[]", title, status),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


async def _doc_tags(conn: aiosqlite.Connection, pid: int) -> list[tuple[str, int]]:
    async with conn.execute(
        "SELECT d.name, dd.manual FROM document_domains dd JOIN domains d "
        "ON d.id = dd.domain_id WHERE dd.paper_content_id = ? ORDER BY d.name", (pid,),
    ) as cur:
        return [(str(r[0]), int(r[1])) for r in await cur.fetchall()]


async def _domain_id(conn: aiosqlite.Connection, name: str) -> int:
    async with conn.execute("SELECT id FROM domains WHERE name = ?", (name,)) as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


# ---------------------------------------------------------------- graph


async def test_graph_is_scoped_to_one_problem(app_with_db: tuple[Any, Any]) -> None:
    app, conn = app_with_db
    d1 = await _doc(conn, 1, domains=["Computer Vision"])
    d2 = await _doc(conn, 2, domains=["Robotics"])
    d3 = await _doc(conn, 3, domains=["NLP"])
    d4 = await _doc(conn, 4, domains=["Information Retrieval"])
    pa = await _problem(conn, "Rerank retrieved images")
    pb = await _problem(conn, "Prompt a VLA with images")
    a1 = await _idea(conn, problem=pa, source=d1)
    await _idea(conn, problem=pa, source=d1, status="dismissed")
    await _idea(conn, problem=pb, source=d2)
    ex = await _idea(conn, source=d3, target=d4)
    async with _client(app) as c:
        r = await c.get("/ideation/graph", params={"problem_id": pa})
        assert r.status_code == 200
        g = r.json()
        assert [d["id"] for d in g["documents"]] == [d1]
        assert g["documents"][0]["idea_count"] == 1  # the dismissed one is excluded
        assert sorted(i["id"] for i in g["ideas"]) == [a1, a1 + 1]
        assert g["problem"]["id"] == pa and g["problem"]["idea_count"] == 1
        assert [d["name"] for d in g["domains"]] == ["Computer Vision"]
        assert isinstance(g["domains"][0]["color_index"], int)  # server-assigned
        assert g["ideas"][0]["source_chunk_ids"] == [11, 12]

        r = await c.get("/ideation/graph", params={"explore": 1})
        g = r.json()
        assert sorted(d["id"] for d in g["documents"]) == [d1, d2, d3, d4]
        assert [i["id"] for i in g["ideas"]] == [ex]
        assert g["problem"] is None
        counts = {d["id"]: d["idea_count"] for d in g["documents"]}
        assert counts == {d1: 0, d2: 0, d3: 1, d4: 1}
        assert len(g["domains"]) == 4

        r = await c.get("/ideation/graph", params={"problem_id": 999})
        assert r.status_code == 404
        r = await c.get("/ideation/graph")
        assert r.status_code == 422


async def test_graph_hides_citation_edges_of_private_documents(
    app_with_db: tuple[Any, Any],
) -> None:
    app, conn = app_with_db
    d1 = await _doc(conn, 1)
    d2 = await _doc(conn, 2)
    d3 = await _doc(conn, 3, private=True)
    await conn.executemany(
        "INSERT INTO citation_edges (source_id, target_id) VALUES (?, ?)",
        [(d1, d2), (d3, d1), (d2, d3)],
    )
    await conn.commit()
    async with _client(app) as c:
        g = (await c.get("/ideation/graph", params={"explore": 1})).json()
    assert g["citations"] == [{"source_id": d1, "target_id": d2}]
    private = {d["id"]: d["is_private"] for d in g["documents"]}
    assert private == {d1: False, d2: False, d3: True}  # still a node (lock)


async def test_problem_graph_only_has_edges_between_its_documents(
    app_with_db: tuple[Any, Any],
) -> None:
    app, conn = app_with_db
    d1 = await _doc(conn, 1)
    d2 = await _doc(conn, 2)
    d3 = await _doc(conn, 3)
    await conn.executemany(
        "INSERT INTO citation_edges (source_id, target_id) VALUES (?, ?)",
        [(d1, d2), (d1, d3)],
    )
    await conn.commit()
    p = await _problem(conn, "x")
    await _idea(conn, problem=p, source=d1)
    await _idea(conn, problem=p, source=d2)
    async with _client(app) as c:
        g = (await c.get("/ideation/graph", params={"problem_id": p})).json()
    assert g["citations"] == [{"source_id": d1, "target_id": d2}]


# ---------------------------------------------------------------- lifecycle


async def test_undo_dismiss_restores_trying_exactly(app_with_db: tuple[Any, Any]) -> None:
    app, conn = app_with_db
    d1 = await _doc(conn, 1)
    i = await _idea(conn, source=d1)
    async with _client(app) as c:
        r = await c.patch(f"/ideation/ideas/{i}", json={"status": "saved"})
        assert r.json()["status"] == "saved" and r.json()["prev_status"] == "new"
        r = await c.patch(f"/ideation/ideas/{i}", json={"status": "trying",
                                                       "note": "prototype"})
        assert r.json()["prev_status"] == "saved" and r.json()["note"] == "prototype"
        r = await c.patch(f"/ideation/ideas/{i}", json={
            "status": "dismissed", "dismiss_reasons": ["not_feasible", "other"],
            "dismiss_note": "needs a GPU farm"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "dismissed" and body["prev_status"] == "trying"
        assert body["dismiss_reasons"] == ["not_feasible", "other"]
        r = await c.post(f"/ideation/ideas/{i}/undo-dismiss")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "trying"
        assert body["dismiss_reasons"] == []
        assert body["note"] == "prototype"
        r = await c.post(f"/ideation/ideas/{i}/undo-dismiss")
        assert r.status_code == 409  # not dismissed any more
    async with conn.execute(
        "SELECT dismiss_note FROM ideas WHERE id = ?", (i,)) as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] == ""


async def test_dismiss_needs_a_known_reason(app_with_db: tuple[Any, Any]) -> None:
    app, conn = app_with_db
    d1 = await _doc(conn, 1)
    i = await _idea(conn, source=d1)
    async with _client(app) as c:
        r = await c.patch(f"/ideation/ideas/{i}", json={"status": "dismissed"})
        assert r.status_code == 422
        r = await c.patch(f"/ideation/ideas/{i}", json={"status": "dismissed",
                                                       "dismiss_reasons": []})
        assert r.status_code == 422
        r = await c.patch(f"/ideation/ideas/{i}", json={"status": "dismissed",
                                                       "dismiss_reasons": ["boring"]})
        assert r.status_code == 422
        r = await c.patch(f"/ideation/ideas/{i}", json={"dismiss_reasons": ["other"]})
        assert r.status_code == 422  # reasons only describe a dismissal
        r = await c.patch("/ideation/ideas/999", json={"status": "saved"})
        assert r.status_code == 404
    async with conn.execute("SELECT status, prev_status FROM ideas WHERE id = ?",
                            (i,)) as cur:
        assert tuple(await cur.fetchone() or ()) == ("new", None)


async def test_same_status_keeps_prev_status(app_with_db: tuple[Any, Any]) -> None:
    """Re-sending the current status must not overwrite prev_status (or Undo
    would restore 'dismissed')."""
    app, conn = app_with_db
    i = await _idea(conn, source=await _doc(conn, 1), status="trying")
    async with _client(app) as c:
        await c.patch(f"/ideation/ideas/{i}", json={"status": "dismissed",
                                                   "dismiss_reasons": ["not_novel"]})
        r = await c.patch(f"/ideation/ideas/{i}", json={
            "status": "dismissed", "dismiss_reasons": ["already_tried"]})
        assert r.json()["prev_status"] == "trying"
        assert r.json()["dismiss_reasons"] == ["already_tried"]
        r = await c.post(f"/ideation/ideas/{i}/undo-dismiss")
        assert r.json()["status"] == "trying"


# ---------------------------------------------------------------- idea chat


async def test_idea_chat_opens_a_new_linked_session_each_time(
    app_with_db: tuple[Any, Any],
) -> None:
    app, conn = app_with_db
    d1 = await _doc(conn, 1)
    d2 = await _doc(conn, 2)
    i = await _idea(conn, source=d1, target=d2, title="Anchors for reranking")
    async with _client(app) as c:
        r1 = await c.post(f"/ideation/ideas/{i}/chat")
        assert r1.status_code == 200
        b1 = r1.json()
        assert b1["title"] == "Anchors for reranking"
        assert b1["paper_content_ids"] == [d1, d2]
        for part in ("Anchors for reranking", "Method", "Results",
                     "Because anchors generalise.", "Doc 1", "Doc 2"):
            assert part in b1["prefill"]
        b2 = (await c.post(f"/ideation/ideas/{i}/chat")).json()
        assert b2["session_id"] != b1["session_id"]
        g = (await c.get("/ideation/graph", params={"explore": 1})).json()
        assert g["ideas"][0]["session_ids"] == [b1["session_id"], b2["session_id"]]
        assert (await c.post("/ideation/ideas/999/chat")).status_code == 404
    for sid in (b1["session_id"], b2["session_id"]):
        async with conn.execute(
            "SELECT title FROM chat_sessions WHERE id = ?", (sid,)) as cur:
            assert (await cur.fetchone() or ("",))[0] == "Anchors for reranking"
        async with conn.execute(
            "SELECT paper_content_id FROM papers WHERE session_id = ? ORDER BY 1",
            (sid,)) as cur:
            assert [r[0] for r in await cur.fetchall()] == [d1, d2]
    async with conn.execute(
        "SELECT session_id FROM idea_sessions WHERE idea_id = ? ORDER BY 1", (i,),
    ) as cur:
        assert [r[0] for r in await cur.fetchall()] == [b1["session_id"],
                                                        b2["session_id"]]


async def test_problem_idea_chat_attaches_only_the_source(
    app_with_db: tuple[Any, Any],
) -> None:
    app, conn = app_with_db
    d1 = await _doc(conn, 1)
    p = await _problem(conn, "Rerank retrieved images")
    i = await _idea(conn, problem=p, source=d1)
    async with _client(app) as c:
        b = (await c.post(f"/ideation/ideas/{i}/chat")).json()
    assert b["paper_content_ids"] == [d1]
    assert "Rerank retrieved images" in b["prefill"] and "Method" in b["prefill"]


# ---------------------------------------------------------------- domains


async def test_manual_document_domains_survive_reanalysis(
    app_with_db: tuple[Any, Any],
) -> None:
    app, conn = app_with_db
    d1 = await _doc(conn, 1, domains=["Computer Vision"])
    await _doc(conn, 2, domains=["Robotics"])
    robotics = await _domain_id(conn, "Robotics")
    async with _client(app) as c:
        r = await c.put(f"/ideation/documents/{d1}/domains",
                        json={"domain_ids": [robotics], "new_names": ["Haptics", " "]})
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == d1 and body["title"] == "Doc 1"
        assert len(body["domains"]) == 2 and robotics in body["domains"]
        assert (await c.put(f"/ideation/documents/{d1}/domains",
                            json={"domain_ids": [9999]})).status_code == 422
        assert (await c.put(f"/ideation/documents/{d1}/domains",
                            json={})).status_code == 422
        assert (await c.put("/ideation/documents/999/domains",
                            json={"domain_ids": [robotics]})).status_code == 404
    assert await _doc_tags(conn, d1) == [("Haptics", 1), ("Robotics", 1)]
    async with conn.execute("SELECT manual FROM domains WHERE name = 'Haptics'") as cur:
        assert (await cur.fetchone() or (0,))[0] == 1
    async with conn.execute(
        "SELECT 1 FROM domains WHERE name = 'Computer Vision'") as cur:
        assert await cur.fetchone() is None  # an orphaned auto domain is cleaned up
    await assign_domains(conn, d1, ["NLP"])  # re-analysis
    assert await _doc_tags(conn, d1) == [("Haptics", 1), ("Robotics", 1)]


async def test_domain_merge_moves_tags_and_deletes_the_old_domain(
    app_with_db: tuple[Any, Any],
) -> None:
    app, conn = app_with_db
    d1 = await _doc(conn, 1, domains=["Vision Stuff"])
    d2 = await _doc(conn, 2, domains=["Computer Vision"])
    d3 = await _doc(conn, 3, domains=["Vision Stuff", "Computer Vision"])
    old = await _domain_id(conn, "Vision Stuff")
    cv = await _domain_id(conn, "Computer Vision")
    await conn.execute(
        "UPDATE document_domains SET manual = 1 WHERE paper_content_id = ?", (d1,))
    await conn.commit()
    async with _client(app) as c:
        r = await c.patch(f"/ideation/domains/{old}", json={"merge_into": cv})
        assert r.status_code == 200
        assert [d["id"] for d in r.json()] == [cv]
        assert (await c.patch(f"/ideation/domains/{cv}",
                              json={"merge_into": cv})).status_code == 422
        assert (await c.patch(f"/ideation/domains/{cv}",
                              json={"merge_into": 999})).status_code == 404
        assert (await c.patch("/ideation/domains/999",
                              json={"name": "x"})).status_code == 404
    assert await _doc_tags(conn, d1) == [("Computer Vision", 1)]  # manual kept
    assert await _doc_tags(conn, d2) == [("Computer Vision", 0)]
    assert await _doc_tags(conn, d3) == [("Computer Vision", 0)]
    async with conn.execute("SELECT manual FROM domains WHERE id = ?", (cv,)) as cur:
        assert (await cur.fetchone() or (0,))[0] == 1
    async with conn.execute("SELECT 1 FROM domains WHERE id = ?", (old,)) as cur:
        assert await cur.fetchone() is None
    # A new document (or a re-analysis) naming the merged-away domain lands on
    # the target instead of recreating it.
    d4 = await _doc(conn, 4, domains=["Vision  Stuff"])
    assert await _doc_tags(conn, d4) == [("Computer Vision", 0)]
    await assign_domains(conn, d2, ["vision stuff"])
    assert await _doc_tags(conn, d2) == [("Computer Vision", 0)]


async def test_merge_carries_the_old_domains_aliases(
    app_with_db: tuple[Any, Any],
) -> None:
    app, conn = app_with_db
    await _doc(conn, 1, domains=["Field A"])
    await _doc(conn, 2, domains=["Field C"])
    a = await _domain_id(conn, "Field A")
    c_id = await _domain_id(conn, "Field C")
    async with _client(app) as c:
        assert (await c.patch(f"/ideation/domains/{a}",
                              json={"name": "Field B"})).status_code == 200
        assert (await c.patch(f"/ideation/domains/{a}",
                              json={"merge_into": c_id})).status_code == 200
    d3 = await _doc(conn, 3, domains=["Field A"])  # the oldest name
    assert await _doc_tags(conn, d3) == [("Field C", 0)]
    async with conn.execute(
        "SELECT name_norm, domain_id FROM domain_aliases ORDER BY 1") as cur:
        assert [tuple(r) for r in await cur.fetchall()] == [
            ("field a", c_id), ("field b", c_id)]


async def test_domain_rename_marks_it_curated(app_with_db: tuple[Any, Any]) -> None:
    app, conn = app_with_db
    d1 = await _doc(conn, 1, domains=["Old Field"])
    await _doc(conn, 2, domains=["Robotics"])
    old = await _domain_id(conn, "Old Field")
    async with _client(app) as c:
        r = await c.patch(f"/ideation/domains/{old}", json={"name": "  My  Field "})
        assert r.status_code == 200
        assert {d["name"] for d in r.json()} == {"My Field", "Robotics"}
        assert (await c.patch(f"/ideation/domains/{old}",
                              json={"name": "robotics"})).status_code == 409
        assert (await c.patch(f"/ideation/domains/{old}",
                              json={"name": " "})).status_code == 422
        assert (await c.patch(f"/ideation/domains/{old}",
                              json={})).status_code == 422
    # Re-analysis still saying the old name keeps the tag on the renamed domain.
    await assign_domains(conn, d1, ["old  field"])
    assert await _doc_tags(conn, d1) == [("My Field", 0)]
    async with conn.execute("SELECT 1 FROM domains WHERE name = 'Old Field'") as cur:
        assert await cur.fetchone() is None
    await assign_domains(conn, d1, ["NLP"])  # re-analysis drops the auto tag
    async with conn.execute(
        "SELECT manual FROM domains WHERE name = 'My Field'") as cur:
        assert (await cur.fetchone() or (0,))[0] == 1  # never deleted


# ---------------------------------------------------------------- problems


async def test_problems_start_generation_in_the_background(
    app_with_db: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, conn = app_with_db
    calls: list[tuple[str, int | None, dict[str, Any]]] = []
    monkeypatch.setattr(ideation_agent, "run_problem",
                        lambda pid, **kw: calls.append(("problem", pid, kw)))
    monkeypatch.setattr(ideation_agent, "run_explore",
                        lambda **kw: calls.append(("explore", None, kw)))
    d1 = await _doc(conn, 1)
    async with _client(app) as c:
        r = await c.post("/ideation/problems",
                         json={"text": " Rerank images ", "tried": "BM25"})
        assert r.status_code == 200
        p = r.json()
        assert p["text"] == "Rerank images" and p["tried"] == "BM25"
        assert p["status"] == "generating" and p["idea_count"] == 0
        assert calls[0][:2] == ("problem", p["id"])
        assert {"db_path", "adapter", "model", "card_model"} <= set(calls[0][2])
        # problem mode passes the configured reasoning budget (0 = unbounded)
        assert calls[0][2]["thinking_budget"] == load_settings().ideation_problem_thinking_budget
        assert (await c.post("/ideation/problems", json={"text": "  "})).status_code == 422

        await _idea(conn, problem=p["id"], source=d1)
        await _idea(conn, problem=p["id"], source=d1, status="dismissed")
        got = (await c.get(f"/ideation/problems/{p['id']}")).json()
        assert got["idea_count"] == 1
        listed = (await c.get("/ideation/problems")).json()
        assert [x["id"] for x in listed] == [p["id"]]
        assert (await c.get("/ideation/problems/999")).status_code == 404

        r = await c.post("/ideation/explore")
        assert r.status_code == 200 and r.json() == {"started": True}
        assert calls[-1][0] == "explore"


async def test_idea_payload_parses_stored_json(app_with_db: tuple[Any, Any]) -> None:
    app, conn = app_with_db
    i = await _idea(conn, source=await _doc(conn, 1))
    await conn.execute(
        "UPDATE ideas SET dismiss_reasons_json = ?, status = 'dismissed', "
        "prev_status = 'saved' WHERE id = ?", (json.dumps(["not_novel"]), i))
    await conn.commit()
    async with _client(app) as c:
        g = (await c.get("/ideation/graph", params={"explore": 1})).json()
    idea = g["ideas"][0]
    assert idea["dismiss_reasons"] == ["not_novel"] and idea["prev_status"] == "saved"
    assert idea["target_doc"] is None and idea["target_chunk_ids"] == []


async def test_graph_get_is_read_only(app_with_db: tuple[Any, Any]) -> None:
    """The graph only reads colours; writes (card build, domain edits) assign them."""
    app, conn = app_with_db
    d1 = await _doc(conn, 1, domains=["Robotics"])
    await conn.execute("INSERT INTO domains (name) VALUES ('Uncoloured')")
    await conn.execute(
        "INSERT INTO document_domains (paper_content_id, domain_id) "
        "SELECT ?, id FROM domains WHERE name = 'Uncoloured'", (d1,))
    await conn.commit()
    p = await _problem(conn, "x")
    await _idea(conn, problem=p, source=d1)

    async def data_version() -> int:
        async with conn.execute("PRAGMA data_version") as cur:
            row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async with _client(app) as c:
        # Warm-up: the first open switches the file to WAL (a change by itself).
        await c.get("/ideation/graph", params={"explore": 1})
        before = await data_version()
        for params in ({"explore": 1}, {"problem_id": p}):
            g = (await c.get("/ideation/graph", params=params)).json()
            colours = {d["name"]: d["color_index"] for d in g["domains"]}
            assert colours["Uncoloured"] is None
            assert isinstance(colours["Robotics"], int)
    assert await data_version() == before  # no other connection committed


async def test_library_delete_frees_colour_slots(app_with_db: tuple[Any, Any]) -> None:
    app, conn = app_with_db
    d1 = await _doc(conn, 1, domains=["Robotics"])
    async with _client(app) as c:
        r = await c.delete(f"/papers/content/{d1}")
        assert r.status_code == 204
    async with conn.execute(
        "SELECT color_index FROM domains WHERE name = 'Robotics'") as cur:
        row = await cur.fetchone()
    assert row is None or row[0] is None


async def test_problem_exposes_card_progress_and_skipped_docs(
    app_with_db: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, conn = app_with_db
    cur = await conn.execute(
        "INSERT INTO problems (text, status) VALUES ('p', 'generating')")
    await conn.commit()
    pid = int(cur.lastrowid or 0)
    monkeypatch.setitem(ideation_agent._progress, pid,
                        ideation_agent.CardProgress(ready=4, total=49, skipped=1))
    async with _client(app) as c:
        got = (await c.get(f"/ideation/problems/{pid}")).json()
        assert (got["cards_ready"], got["cards_total"], got["skipped_docs"]) == (4, 49, 1)

        # Finished: progress is gone and the skipped count comes from the trace.
        ideation_agent._progress.pop(pid)
        await conn.execute("UPDATE problems SET status = 'ready' WHERE id = ?", (pid,))
        await conn.execute("INSERT INTO chat_sessions (title, internal) VALUES ('i', 1)")
        run = await conn.execute(
            "INSERT INTO runs (session_id, status, routing_decision_json) "
            "VALUES ((SELECT MAX(id) FROM chat_sessions), 'ok', ?)",
            (ideation_agent.run_scope_json(pid),))
        await conn.execute(
            "INSERT INTO tool_calls (run_id, branch, step_index, agent, tool, "
            "result_summary_json, status, latency_ms) VALUES (?, '', 0, 'ideation', "
            "'ideation:propose', ?, 'ok', 5)",
            (run.lastrowid, json.dumps({"skipped_docs": [7, 8], "new_count": 1})))
        await conn.commit()
        got = (await c.get(f"/ideation/problems/{pid}")).json()
        assert got["cards_ready"] is None and got["cards_total"] is None
        assert got["skipped_docs"] == 2
        listed = (await c.get("/ideation/problems")).json()
        assert listed[0]["skipped_docs"] == 2


async def test_retry_restarts_a_failed_problem(
    app_with_db: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, conn = app_with_db
    calls: list[int] = []
    monkeypatch.setattr(ideation_agent, "run_problem",
                        lambda pid, **kw: calls.append(pid))
    cur = await conn.execute("INSERT INTO problems (text, status) VALUES ('p', 'error')")
    await conn.commit()
    pid = int(cur.lastrowid or 0)
    ready = await _problem(conn, "done")
    async with _client(app) as c:
        r = await c.post(f"/ideation/problems/{pid}/retry")
        assert r.status_code == 200
        assert r.json()["status"] == "generating" and r.json()["id"] == pid
        assert calls == [pid]
        # Only a failed problem can be retried: generating / ready are 409.
        assert (await c.post(f"/ideation/problems/{pid}/retry")).status_code == 409
        assert (await c.post(f"/ideation/problems/{ready}/retry")).status_code == 409
        assert (await c.post("/ideation/problems/999/retry")).status_code == 404
    assert calls == [pid]


async def test_failed_start_marks_the_problem_error(
    app_with_db: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the background start raises after the insert committed, the
    problem is not left ``generating`` forever."""
    app, conn = app_with_db

    def boom(pid: int, **kw: Any) -> None:
        raise RuntimeError("no adapter")

    monkeypatch.setattr(ideation_agent, "run_problem", boom)
    async with _client(app) as c:
        with pytest.raises(RuntimeError):
            await c.post("/ideation/problems", json={"text": "p"})
        async with conn.execute("SELECT id, status FROM problems") as cur:
            rows = list(await cur.fetchall())
        assert [r[1] for r in rows] == ["error"]
        with pytest.raises(RuntimeError):
            await c.post(f"/ideation/problems/{rows[0][0]}/retry")
    async with conn.execute("SELECT status FROM problems") as cur:
        assert [r[0] for r in await cur.fetchall()] == ["error"]


async def test_graph_counts_the_whole_library_in_every_scope(
    app_with_db: tuple[Any, Any],
) -> None:
    app, conn = app_with_db
    d1 = await _doc(conn, 1, domains=["Computer Vision"])
    await _doc(conn, 2)
    await _doc(conn, 3, private=True)
    pa = await _problem(conn, "p")
    await _idea(conn, problem=pa, source=d1)
    async with _client(app) as c:
        for params in ({"problem_id": pa}, {"explore": 1}):
            g = (await c.get("/ideation/graph", params=params)).json()
            assert (g["library_count"], g["private_count"]) == (3, 1)
        g = (await c.get("/ideation/graph", params={"problem_id": pa})).json()
        assert len(g["documents"]) == 1


async def test_concurrent_patches_serialise_prev_status(
    app_with_db: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two status changes racing from ``new``: the read + validate happen
    inside the write transaction, so the second one sees the first's status
    as its ``prev_status`` (not a stale ``new``)."""
    import asyncio

    from paperhub.api import ideation as api

    real_get = api._get_idea

    async def slow_get(conn: Any, idea_id: int) -> Any:
        idea = await real_get(conn, idea_id)
        await asyncio.sleep(0.05)  # widen the read -> write window
        return idea

    monkeypatch.setattr(api, "_get_idea", slow_get)
    app, conn = app_with_db
    d1 = await _doc(conn, 1)
    iid = await _idea(conn, source=d1)
    async with _client(app) as c:
        a, b = await asyncio.gather(
            c.patch(f"/ideation/ideas/{iid}", json={"status": "saved"}),
            c.patch(f"/ideation/ideas/{iid}", json={"status": "trying"}),
        )
    assert a.status_code == b.status_code == 200
    async with conn.execute(
        "SELECT status, prev_status FROM ideas WHERE id = ?", (iid,),
    ) as cur:
        status, prev = tuple(await cur.fetchone() or ())
    assert {status, prev} == {"saved", "trying"}


# ---------------------------------------------------------------- QA fixes


async def test_idea_session_ids_skip_deleted_chats(app_with_db: tuple[Any, Any]) -> None:
    """I6: a soft-deleted chat is no longer linked to its idea."""
    app, conn = app_with_db
    d1 = await _doc(conn, 1)
    i = await _idea(conn, source=d1)
    async with _client(app) as c:
        kept = (await c.post(f"/ideation/ideas/{i}/chat")).json()["session_id"]
        gone = (await c.post(f"/ideation/ideas/{i}/chat")).json()["session_id"]
        await conn.execute(
            "UPDATE chat_sessions SET deleted_at = datetime('now') WHERE id = ?", (gone,))
        await conn.commit()
        g = (await c.get("/ideation/graph", params={"explore": 1})).json()
    assert g["ideas"][0]["session_ids"] == [kept]


async def test_problem_text_has_a_length_limit(
    app_with_db: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I7: an oversized problem (or tried) is rejected before any run starts."""
    from paperhub.ideation.models import PROBLEM_MAX_CHARS, TRIED_MAX_CHARS

    app, _conn = app_with_db
    calls: list[int] = []
    monkeypatch.setattr(ideation_agent, "run_problem",
                        lambda pid, **kw: calls.append(pid))
    async with _client(app) as c:
        r = await c.post("/ideation/problems", json={"text": "x" * (PROBLEM_MAX_CHARS + 1)})
        assert r.status_code == 422
        r = await c.post("/ideation/problems",
                         json={"text": "ok", "tried": "y" * (TRIED_MAX_CHARS + 1)})
        assert r.status_code == 422
        # whitespace padding is not counted
        r = await c.post("/ideation/problems",
                         json={"text": "  " + "x" * PROBLEM_MAX_CHARS + "  "})
        assert r.status_code == 200
    assert len(calls) == 1


async def test_explore_passes_the_ui_language(
    app_with_db: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I1: free exploration has no problem text, so it writes in the UI language
    the client sends (optional body; English without one)."""
    app, _conn = app_with_db
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(ideation_agent, "run_explore", lambda **kw: calls.append(kw))
    async with _client(app) as c:
        assert (await c.post("/ideation/explore", json={"language": "zh-TW"})).status_code == 200
        assert (await c.post("/ideation/explore")).status_code == 200
    assert calls[0]["language"] == "zh-TW"
    assert calls[1]["language"] == "en"


async def test_problem_reports_nothing_relevant(app_with_db: tuple[Any, Any]) -> None:
    """QA I2: the latest run's 'nothing in the library applies' outcome."""
    app, conn = app_with_db
    p = await _problem(conn, "Bake sourdough with an open crumb")
    await conn.execute("INSERT INTO chat_sessions (title, internal) VALUES ('i', 1)")
    cur = await conn.execute(
        "INSERT INTO runs (session_id, status, routing_decision_json) VALUES "
        "((SELECT MAX(id) FROM chat_sessions), 'ok', ?)",
        (ideation_agent.run_scope_json(p),))
    await conn.execute(
        "INSERT INTO tool_calls (run_id, step_index, agent, tool, status, latency_ms, "
        "args_redacted_json, result_summary_json) VALUES (?, 0, 'ideation', "
        "'ideation:propose', 'ok', 1, '{}', ?)",
        (cur.lastrowid, json.dumps({"outcome": "nothing_relevant",
                                    "no_fit_reason": "No document is about baking.",
                                    "skipped_docs": []})))
    await conn.commit()
    async with _client(app) as c:
        got = (await c.get(f"/ideation/problems/{p}")).json()
    assert got["nothing_relevant"] is True
    assert got["nothing_relevant_reason"] == "No document is about baking."
