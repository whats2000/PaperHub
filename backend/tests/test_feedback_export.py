"""Plan H2 Task 7: Hugging Face SFT/DPO export."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from paperhub.api.feedback import get_export_file
from paperhub.feedback.export import ExportPlan, build_export, write_export
from paperhub.feedback.models import ExportRequest

_N = 0


def _opts(**kw: Any) -> ExportRequest:
    kw.setdefault("cited_only", False)
    return ExportRequest(**kw)


async def _session(conn: aiosqlite.Connection, *, fork_of_run: int | None = None
                   ) -> int:
    cur = await conn.execute(
        "INSERT INTO chat_sessions (title, forked_from_run_id) VALUES ('c', ?)",
        (fork_of_run,),
    )
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


async def _paper(conn: aiosqlite.Connection, *, private: bool = False,
                 text: str = "passage") -> int:
    """A paper with one chunk; returns the chunk id."""
    global _N
    _N += 1
    cur = await conn.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, source_path, "
        "source_dir_path, html_path, is_private) VALUES (?, 'arxiv', ?, ?, '', '', "
        "'', ?)",
        (f"k{_N}", f"2401.{_N:05d}", f"Paper {_N}", int(private)),
    )
    assert cur.lastrowid is not None
    cur = await conn.execute(
        "INSERT INTO chunks (paper_content_id, char_start, char_end, text) "
        "VALUES (?, 0, 1, ?)", (int(cur.lastrowid), text),
    )
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


async def _turn(conn: aiosqlite.Connection, sid: int, question: str, answer: str,
                model: str | None = "gemini/flagship") -> int:
    cur = await conn.execute(
        "INSERT INTO runs (session_id, status) VALUES (?, 'ok')", (sid,),
    )
    assert cur.lastrowid is not None
    rid = int(cur.lastrowid)
    await conn.execute(
        "INSERT INTO messages (session_id, role, content, run_id) VALUES (?,?,?,?)",
        (sid, "user", question, rid),
    )
    await conn.execute(
        "INSERT INTO messages (session_id, role, content, run_id) VALUES (?,?,?,?)",
        (sid, "assistant", answer, rid),
    )
    await conn.execute(
        "INSERT INTO tool_calls (run_id, step_index, agent, tool, model, "
        "latency_ms, status) VALUES (?, 0, 'router', 'classify', 'gemini/small', "
        "1, 'ok')", (rid,),
    )
    if model:
        await conn.execute(
            "INSERT INTO tool_calls (run_id, step_index, agent, tool, model, "
            "latency_ms, status) VALUES (?, 1, 'research', 'paper_qa:finalize', ?, "
            "1, 'ok')", (rid, model),
        )
    return rid


async def _rate(conn: aiosqlite.Connection, run_id: int, polarity: str, *,
                correction: str | None = None,
                created: str = "2026-11-03 10:00:00") -> None:
    await conn.execute(
        "INSERT INTO feedback (run_id, kind, polarity, correction, created_at) "
        "VALUES (?, 'explicit', ?, ?, ?)", (run_id, polarity, correction, created),
    )


async def _fork(conn: aiosqlite.Connection, sid: int, orig_run: int,
                question: str, answer: str) -> int:
    """A fork of ``orig_run``: the history before its question copied, then a
    NEW turn asking ``question``. Returns the fork's first run."""
    fork = await _session(conn, fork_of_run=orig_run)
    async with conn.execute(
        "SELECT role, content FROM messages WHERE session_id = ? AND id < "
        "(SELECT MIN(id) FROM messages WHERE run_id = ?) ORDER BY id",
        (sid, orig_run),
    ) as cur:
        prior = await cur.fetchall()
    for role, content in prior:
        cur2 = await conn.execute(
            "INSERT INTO runs (session_id, status) VALUES (?, 'ok')", (fork,),
        )
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) "
            "VALUES (?,?,?,?)", (fork, role, content, cur2.lastrowid),
        )
    return await _turn(conn, fork, question, answer)


async def test_sft_line_has_sources_system_message(
    app_with_db: tuple[Any, Any],
) -> None:
    _, conn = app_with_db
    ch = await _paper(conn, text="Transformers use attention.")
    sid = await _session(conn)
    await _turn(conn, sid, "hi", "hello")  # unrated history turn
    r = await _turn(conn, sid, "What do they use?", f"Attention [chunk:{ch}] (p.1).")
    await _rate(conn, r, "up")
    await conn.commit()
    plan = await build_export(conn, _opts())
    assert len(plan.sft) == 1 and plan.dpo == []
    msgs = plan.sft[0]["messages"]
    assert [m["role"] for m in msgs] == [
        "system", "user", "assistant", "user", "assistant"]
    assert msgs[0]["content"].startswith("<sources>")
    assert msgs[0]["content"].endswith("</sources>")
    assert "Transformers use attention." in msgs[0]["content"]
    assert f'chunk="{ch}"' in msgs[0]["content"]
    assert msgs[1:3] == [{"role": "user", "content": "hi"},
                         {"role": "assistant", "content": "hello"}]
    assert msgs[-1]["content"] == f"Attention [chunk:{ch}] (p.1)."


async def test_uncited_sft_has_no_system_and_cited_only_drops_it(
    app_with_db: tuple[Any, Any],
) -> None:
    _, conn = app_with_db
    sid = await _session(conn)
    await _rate(conn, await _turn(conn, sid, "q", "no citations"), "up")
    await conn.commit()
    plan = await build_export(conn, _opts())
    assert [m["role"] for m in plan.sft[0]["messages"]] == ["user", "assistant"]
    assert (await build_export(conn, _opts(cited_only=True))).sft == []


async def test_correction_produces_a_pair(app_with_db: tuple[Any, Any]) -> None:
    _, conn = app_with_db
    sid = await _session(conn)
    r = await _turn(conn, sid, "Year?", "2019")
    await _rate(conn, r, "down", correction="2017")
    r2 = await _turn(conn, sid, "Another?", "x")
    await _rate(conn, r2, "down")  # no correction -> no pair
    await conn.commit()
    plan = await build_export(conn, _opts())
    assert plan.sft == []
    assert plan.dpo == [{
        "prompt": [{"role": "user", "content": "Year?"}],
        "chosen": [{"role": "assistant", "content": "2017"}],
        "rejected": [{"role": "assistant", "content": "2019"}],
    }]


async def test_identical_fork_prompt_pairs_edited_one_does_not(
    app_with_db: tuple[Any, Any],
) -> None:
    _, conn = app_with_db
    sid = await _session(conn)
    await _turn(conn, sid, "context q", "context a")
    orig = await _turn(conn, sid, "Explain X", "bad answer")
    await _rate(conn, orig, "down")
    same = await _fork(conn, sid, orig, "  Explain X \n", "good answer")
    await _rate(conn, same, "up")
    edited = await _fork(conn, sid, orig, "Explain X in detail", "other answer")
    await _rate(conn, edited, "up")
    await conn.commit()
    plan = await build_export(conn, _opts())
    assert len(plan.dpo) == 1
    pair = plan.dpo[0]
    assert pair["chosen"] == [{"role": "assistant", "content": "good answer"}]
    assert pair["rejected"] == [{"role": "assistant", "content": "bad answer"}]
    assert pair["prompt"][-1] == {"role": "user", "content": "Explain X"}
    assert pair["prompt"][:2] == [{"role": "user", "content": "context q"},
                                  {"role": "assistant", "content": "context a"}]
    # both thumbs-up answers are SFT lines; the edited fork made no pair
    assert len(plan.sft) == 2


async def test_fork_with_same_polarity_makes_no_pair(
    app_with_db: tuple[Any, Any],
) -> None:
    _, conn = app_with_db
    sid = await _session(conn)
    orig = await _turn(conn, sid, "Explain X", "a1")
    await _rate(conn, orig, "up")
    await _rate(conn, await _fork(conn, sid, orig, "Explain X", "a2"), "up")
    await conn.commit()
    assert (await build_export(conn, _opts())).dpo == []


async def test_include_private_false_drops_private_cited_samples(
    app_with_db: tuple[Any, Any],
) -> None:
    _, conn = app_with_db
    pub = await _paper(conn)
    priv = await _paper(conn, private=True, text="secret")
    sid = await _session(conn)
    await _rate(conn, await _turn(conn, sid, "p", f"pub [chunk:{pub}]"), "up")
    # private chunk cited alongside a public one: still dropped
    await _rate(conn, await _turn(
        conn, sid, "s", f"mix [chunk:{pub}] [chunk:{priv}]"), "up")
    # a correction citing a private chunk drops the pair too
    await _rate(conn, await _turn(conn, sid, "c", "wrong"), "down",
                correction=f"right [chunk:{priv}]")
    await conn.commit()
    full = await build_export(conn, _opts())
    assert (len(full.sft), len(full.dpo), full.includes_private) == (2, 1, True)
    safe = await build_export(conn, _opts(include_private=False))
    assert (len(safe.sft), len(safe.dpo), safe.includes_private) == (1, 0, False)
    assert "secret" not in json.dumps(safe.sft)


async def test_private_chunk_cited_in_history_drops_sample(
    app_with_db: tuple[Any, Any],
) -> None:
    _, conn = app_with_db
    priv = await _paper(conn, private=True)
    sid = await _session(conn)
    await _turn(conn, sid, "earlier", f"from the private doc [chunk:{priv}]")
    await _rate(conn, await _turn(conn, sid, "now", "plain"), "up")
    await conn.commit()
    assert (await build_export(conn, _opts(include_private=False))).sft == []


async def test_model_date_and_implicit_filters(app_with_db: tuple[Any, Any]) -> None:
    _, conn = app_with_db
    sid = await _session(conn)
    a = await _turn(conn, sid, "a", "a", model="gemini/pro")
    await _rate(conn, a, "up", created="2026-10-01 00:00:00")
    b = await _turn(conn, sid, "b", "b", model="gemini/flash")
    await _rate(conn, b, "up", created="2026-11-03 00:00:00")
    await conn.commit()
    assert len((await build_export(conn, _opts(model="gemini/pro"))).sft) == 1
    assert len((await build_export(conn, _opts(since="2026-11-01"))).sft) == 1
    assert len((await build_export(conn, _opts(until="2026-10-01"))).sft) == 1
    assert len((await build_export(conn, _opts(model="none"))).sft) == 0

    # a confirmed implicit flag pairs a fork only when implicit is included
    orig = await _turn(conn, sid, "Q", "bad")
    await conn.execute(
        "INSERT INTO feedback (run_id, kind, polarity, trigger, verdict, status) "
        "VALUES (?, 'implicit', 'down', 'fork', 'confirmed', 'final')", (orig,),
    )
    await _rate(conn, await _fork(conn, sid, orig, "Q", "good"), "up")
    await conn.commit()
    assert (await build_export(conn, _opts())).dpo == []
    assert len((await build_export(conn, _opts(include_implicit=True))).dpo) == 1


async def test_write_export_files_and_card(
    app_with_db: tuple[Any, Any], tmp_path: Path,
) -> None:
    _, conn = app_with_db
    priv = await _paper(conn, private=True)
    sid = await _session(conn)
    await _rate(conn, await _turn(conn, sid, "q", f"a [chunk:{priv}]"), "up")
    await conn.commit()
    plan = await build_export(conn, _opts())
    out = write_export(plan, tmp_path / "exports", datetime(2026, 11, 6, 9, 8, 7))
    assert out.name == "20261106-090807"
    lines = (out / "sft.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["messages"][0]["role"] == "system"
    assert (out / "dpo.jsonl").read_text(encoding="utf-8") == ""
    card = (out / "README.md").read_text(encoding="utf-8")
    assert "contains document text" in card
    assert "Includes private documents" in card
    assert "include_private: true" in card
    # same second -> a fresh directory, never an overwrite
    again = write_export(plan, tmp_path / "exports", datetime(2026, 11, 6, 9, 8, 7))
    assert again != out and again.exists()
    clean = await build_export(conn, _opts(include_private=False))
    card2 = (write_export(clean, tmp_path / "e2") / "README.md").read_text("utf-8")
    assert "Includes private documents" not in card2


async def test_preview_counts_equal_written_and_download(
    app_with_db: tuple[Any, Any], tmp_path: Path,
) -> None:
    app, conn = app_with_db
    priv = await _paper(conn, private=True)
    sid = await _session(conn)
    await _rate(conn, await _turn(conn, sid, "q1", "a1"), "up")
    await _rate(conn, await _turn(conn, sid, "q2", f"a2 [chunk:{priv}]"), "up")
    sid2 = await _session(conn)  # its own history cites nothing private
    await _rate(conn, await _turn(conn, sid2, "q3", "a3"), "down", correction="fix")
    await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        pv = await c.get("/feedback/export/preview",
                         params={"cited_only": "false", "include_private": "false"})
        assert pv.status_code == 200
        assert pv.json() == {"sft": 1, "dpo": 1, "includes_private": False}
        assert not (tmp_path / "exports").exists()  # preview writes nothing
        ex = await c.post("/feedback/export", json={
            "cited_only": False, "include_private": False})
        assert ex.status_code == 200
        body = ex.json()
        assert (body["sft"], body["dpo"]) == (1, 1)
        assert sorted(body["files"]) == ["README.md", "dpo.jsonl", "sft.jsonl"]
        out = tmp_path / "exports" / body["dir"]
        assert len((out / "sft.jsonl").read_text("utf-8").splitlines()) == 1
        assert len((out / "dpo.jsonl").read_text("utf-8").splitlines()) == 1

        dl = await c.get(f"/feedback/exports/{body['dir']}/sft.jsonl")
        assert dl.status_code == 200
        assert dl.content == (out / "sft.jsonl").read_bytes()
        assert b"\r\n" not in dl.content
        assert (await c.get(f"/feedback/exports/{body['dir']}/nope.jsonl")
                ).status_code == 404

        # traversal: never serve anything outside exports_dir
        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        for path in (
            "/feedback/exports/%2E%2E/secret.txt",
            "/feedback/exports/%2E%2E/paperhub.db",
            f"/feedback/exports/{body['dir']}/%2E%2E",
            "/feedback/exports/..%2F..%2F/secret.txt",
            "/feedback/exports/../secret.txt",
            f"/feedback/exports/{body['dir']}/..%5C..%5Csecret.txt",
        ):
            r = await c.get(path)
            assert r.status_code in (400, 404), path
            assert r.text != "x"



# ---- fix round 1 -------------------------------------------------------------

async def test_dangling_citation_fails_closed(app_with_db: tuple[Any, Any]) -> None:
    """Re-ingest rebuilds chunks: an id that no longer resolves has an unknown
    source, so it counts as possibly private."""
    _, conn = app_with_db
    sid = await _session(conn)
    await _rate(conn, await _turn(conn, sid, "q", "a [chunk:999999]"), "up")
    await conn.commit()
    full = await build_export(conn, _opts())
    assert len(full.sft) == 1 and full.includes_private is True
    # the unresolved id is not a source
    assert [m["role"] for m in full.sft[0]["messages"]] == ["user", "assistant"]
    safe = await build_export(conn, _opts(include_private=False))
    assert safe.sft == [] and safe.includes_private is False


async def test_session_with_private_paper_attached_fails_closed(
    app_with_db: tuple[Any, Any],
) -> None:
    _, conn = app_with_db
    priv = await _paper(conn, private=True)
    async with conn.execute(
        "SELECT paper_content_id FROM chunks WHERE id = ?", (priv,),
    ) as cur:
        pcid = (await cur.fetchone())[0]
    sid = await _session(conn)
    await conn.execute(
        "INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (?, ?, 0)",
        (sid, pcid),
    )
    await _rate(conn, await _turn(conn, sid, "q", "uncited answer"), "up")
    await conn.commit()
    assert (await build_export(conn, _opts())).includes_private is True
    assert (await build_export(conn, _opts(include_private=False))).sft == []


async def test_sft_dropped_when_history_holds_a_down_rated_answer(
    app_with_db: tuple[Any, Any],
) -> None:
    _, conn = app_with_db
    sid = await _session(conn)
    bad = await _turn(conn, sid, "q1", "bad answer")
    await _rate(conn, bad, "down")
    await _rate(conn, await _turn(conn, sid, "q2", "good"), "up")
    # confirmed implicit down counts even with include_implicit off
    sid2 = await _session(conn)
    flagged = await _turn(conn, sid2, "q1", "flagged")
    await conn.execute(
        "INSERT INTO feedback (run_id, kind, polarity, trigger, verdict, status) "
        "VALUES (?, 'implicit', 'down', 'keyword', 'confirmed', 'final')", (flagged,),
    )
    await _rate(conn, await _turn(conn, sid2, "q2", "good"), "up")
    # a fork whose copied history carries the down-rated parent turn
    later = await _turn(conn, sid, "q3", "x")
    await _rate(conn, await _fork(conn, sid, later, "q3", "y"), "up")
    # an un-rated history turn is fine
    sid3 = await _session(conn)
    await _turn(conn, sid3, "q1", "meh")
    await _rate(conn, await _turn(conn, sid3, "q2", "kept"), "up")
    await conn.commit()
    plan = await build_export(conn, _opts())
    assert [s["messages"][-1]["content"] for s in plan.sft] == ["kept"]


async def test_card_says_only_final_turn_is_vetted(
    app_with_db: tuple[Any, Any], tmp_path: Path,
) -> None:
    _, conn = app_with_db
    plan = await build_export(conn, _opts(since="2026-11-01"))
    card = (write_export(plan, tmp_path) / "README.md").read_text("utf-8")
    assert "Only the FINAL assistant turn is a vetted target" in card
    assert "since (UTC day, inclusive): 2026-11-01" in card


async def test_dates_are_validated_utc_days(
    app_with_db: tuple[Any, Any],
) -> None:
    app, conn = app_with_db
    sid = await _session(conn)
    await _rate(conn, await _turn(conn, sid, "q", "a"), "up",
                created="2026-11-03 23:30:00")
    await conn.commit()
    # 2026-11-04T01:00+02:00 is 2026-11-03 23:00 UTC -> same UTC day
    assert len((await build_export(
        conn, _opts(since="2026-11-04T01:00:00+02:00"))).sft) == 1
    assert (await build_export(conn, _opts(since="2026-11-04"))).sft == []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        for params in ({"since": "yesterday"}, {"until": "2026-13-01"}):
            r = await c.get("/feedback/export/preview", params=params)
            assert r.status_code == 422, params
        r = await c.post("/feedback/export", json={"cited_only": False,
                                                   "until": "11/03/2026"})
        assert r.status_code == 422
        ok = await c.get("/feedback/export/preview", params={"until": "2026-11-03"})
        assert ok.status_code == 200 and ok.json()["sft"] == 1


def test_write_export_stamp_is_utc_and_never_reuses_a_dir(tmp_path: Path) -> None:
    plan = ExportPlan(options=_opts(), sft=[], dpo=[], includes_private=False)
    before = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    out = write_export(plan, tmp_path)
    after = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    assert before <= out.name[:13] <= after
    now = datetime(2026, 1, 1, tzinfo=UTC)
    dirs = {write_export(plan, tmp_path, now).name for _ in range(3)}
    assert dirs == {"20260101-000000", "20260101-000000-2", "20260101-000000-3"}


async def test_download_guard_branches_direct(
    app_with_db: tuple[Any, Any],
) -> None:
    for dir_name, file_name in (("..", "paperhub.db"), ("x", "..\\x"),
                                ("x", ".."), (".", "x"), ("x", "a:b")):
        with pytest.raises(HTTPException) as exc:
            await get_export_file(dir_name, file_name)
        assert exc.value.status_code == 400, (dir_name, file_name)


async def test_card_states_private_exclusion_fails_closed(
    app_with_db: tuple[Any, Any], tmp_path: Path,
) -> None:
    _, conn = app_with_db
    excluded = await build_export(conn, _opts(include_private=False))
    card = (write_export(excluded, tmp_path / "a") / "README.md").read_text("utf-8")
    assert "Private documents excluded (fail-closed)" in card
    assert "every sample from a chat session with a private paper attached" in card
    included = await build_export(conn, _opts())
    card2 = (write_export(included, tmp_path / "b") / "README.md").read_text("utf-8")
    assert "Private documents excluded" not in card2


async def test_multi_id_citations_build_sources(
    app_with_db: tuple[Any, Any],
) -> None:
    _, conn = app_with_db
    a = await _paper(conn, text="alpha")
    b = await _paper(conn, text="beta")
    sid = await _session(conn)
    r = await _turn(conn, sid, "q", f"x [chunk:{a}, chunk:{b}] y [chunk: {b}]")
    await _rate(conn, r, "up")
    await conn.commit()
    plan = await build_export(conn, _opts(cited_only=True))
    assert len(plan.sft) == 1
    system = plan.sft[0]["messages"][0]["content"]
    assert f'chunk="{a}"' in system and f'chunk="{b}"' in system


async def test_detached_private_paper_multi_id_citation_is_excluded(
    app_with_db: tuple[Any, Any],
) -> None:
    """A private paper detached before export has no ``papers`` row left, so
    only its cited chunk can reveal it; a multi-id marker must still be read."""
    _, conn = app_with_db
    pub = await _paper(conn, text="public")
    priv = await _paper(conn, private=True, text="secret")
    sid = await _session(conn)  # no papers rows: the private one was detached
    r = await _turn(conn, sid, "q", f"x [chunk:{pub},{priv}]")
    await _rate(conn, r, "up")
    r2 = await _turn(conn, sid, "q2", f"y [chunk: {pub}, {priv}]")
    await _rate(conn, r2, "up")
    await conn.commit()
    assert (await build_export(conn, _opts(include_private=False))).sft == []
    assert len((await build_export(conn, _opts(include_private=True))).sft) == 2


async def test_fork_copy_rated_like_its_original_exports_once(
    app_with_db: tuple[Any, Any], tmp_path: Path,
) -> None:
    """Fix M3: a thumbs-up on a fork's copied turn duplicates the original's."""
    from paperhub.db.fork import fork_session

    _, conn = app_with_db
    sid = await _session(conn)
    r1 = await _turn(conn, sid, "first", "answer one")
    r2 = await _turn(conn, sid, "second", "answer two")
    await _rate(conn, r1, "up")
    await conn.commit()
    fork = await fork_session(conn, source_session_id=sid, fork_run_id=r2,
                              workspace_dir=tmp_path)
    async with conn.execute(
        "SELECT run_id FROM messages WHERE session_id = ? AND role = 'assistant'",
        (fork.new_session_id,),
    ) as cur:
        (copy_run,), = await cur.fetchall()
    await _rate(conn, copy_run, "up")
    await conn.commit()
    plan = await build_export(conn, _opts())
    assert len(plan.sft) == 1


async def test_fork_of_fork_copies_of_an_unrated_original_export_once(
    app_with_db: tuple[Any, Any], tmp_path: Path,
) -> None:
    """Copies A (fork of O's session) and B (fork of A's session) both rated,
    O not rated: one answer, one SFT line."""
    from paperhub.db.fork import fork_session

    _, conn = app_with_db
    sid = await _session(conn)
    await _turn(conn, sid, "first", "answer one")
    r2 = await _turn(conn, sid, "second", "answer two")
    await conn.commit()
    f1 = await fork_session(conn, source_session_id=sid, fork_run_id=r2,
                            workspace_dir=tmp_path)
    a_run = (await (await conn.execute(
        "SELECT run_id FROM messages WHERE session_id = ? AND role = 'assistant'",
        (f1.new_session_id,))).fetchone())[0]
    f1_turn = await _turn(conn, f1.new_session_id, "second again", "answer three")
    await conn.commit()
    f2 = await fork_session(conn, source_session_id=f1.new_session_id,
                            fork_run_id=f1_turn, workspace_dir=tmp_path)
    b_run = (await (await conn.execute(
        "SELECT run_id FROM messages WHERE session_id = ? AND role = 'assistant'",
        (f2.new_session_id,))).fetchone())[0]
    assert a_run != b_run
    await _rate(conn, a_run, "up")
    await _rate(conn, b_run, "up")
    await conn.commit()
    plan = await build_export(conn, _opts())
    assert len(plan.sft) == 1
