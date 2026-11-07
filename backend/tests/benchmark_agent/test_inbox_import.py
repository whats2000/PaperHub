"""Plan H2 Task 8: import-inbox + export-steps CLI subcommands."""
import json
import sqlite3
from pathlib import Path

from benchmark.agent import cli, corpus

ARGS = {"user_message": "how many papers do I have", "enabled_refs_count": 1,
        "slide_attached": False}


def _line(case_id: str, expect: dict, hint: str = "router") -> str:
    return json.dumps({
        "case_id": case_id, "stage_hint": hint, "reasons": ["wrong_route"],
        "correction": "c", "source_run_id": 3, "variables": ARGS,
        "observed": {"intent": "paper_qa"}, "expect": expect})


def test_import_skips_unlabelled_and_imports_labelled(tmp_path: Path, capsys) -> None:
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_text("\n".join([
        _line("fb1-run3", {}),
        _line("fb2-run4", {"intent": "library_stats"}),
        _line("fb3-run5", {"intent": "x"}, hint="answer"),
        "not json",
    ]) + "\n", encoding="utf-8")
    out = tmp_path / "router.harvest.jsonl"
    rc = cli.main(["import-inbox", "--inbox", str(inbox), "--stage", "router",
                   "--out", str(out)])
    assert rc == 0
    cases = corpus.load_corpus(out)
    assert [c.case_id for c in cases] == ["fb2-run4"]
    assert cases[0].expect == {"intent": "library_stats"}
    assert cases[0].variables == ARGS and cases[0].source_run_id == 3
    assert "Imported 1" in capsys.readouterr().out


def test_export_steps_renders_recorded_user_message(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tool_calls (run_id INT, step_index INT, agent TEXT, "
                 "tool TEXT, args_redacted_json TEXT, result_summary_json TEXT)")
    conn.execute("INSERT INTO tool_calls VALUES (7, 0, 'router', 'classify', ?, ?)",
                 (json.dumps(ARGS), json.dumps({"intent": "paper_qa"})))
    conn.commit()
    conn.close()
    pdir = tmp_path / "prompts" / "router"
    pdir.mkdir(parents=True)
    (pdir / "v1.yaml").write_text(
        "system: |\n  SYS\nuser: |\n  refs {enabled_refs_count}\n  {user_message}\n",
        encoding="utf-8")
    out = tmp_path / "steps.jsonl"
    rc = cli.main(["export-steps", "--db", str(db), "--stage", "router",
                   "--out", str(out), "--prompts-dir", str(tmp_path / "prompts")])
    assert rc == 0
    rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and rows[0]["source_run_id"] == 7
    msgs = rows[0]["messages"]
    assert msgs[0]["role"] == "system"
    assert ARGS["user_message"] in msgs[1]["content"] and msgs[1]["role"] == "user"
    assert json.loads(msgs[2]["content"]) == {"intent": "paper_qa"}


def test_import_merges_into_existing_out_and_reports_counts(
    tmp_path: Path, capsys,
) -> None:
    out = tmp_path / "router.harvest.jsonl"
    keep = corpus.CorpusCase("run1-s0", "router", dict(ARGS), {"intent": "paper_qa"})
    old = corpus.CorpusCase("fb2-run4", "router", dict(ARGS), {"intent": "slides"})
    corpus.save_corpus(out, [keep, old])
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_text(
        _line("fb2-run4", {"intent": "library_stats"}) + "\n"
        + _line("fb9-run9", {"intent": "chitchat"}) + "\n", encoding="utf-8")
    assert cli.main(["import-inbox", "--inbox", str(inbox), "--stage", "router",
                     "--out", str(out)]) == 0
    got = {c.case_id: c.expect for c in corpus.load_corpus(out)}
    assert got == {"run1-s0": {"intent": "paper_qa"},
                   "fb2-run4": {"intent": "library_stats"},
                   "fb9-run9": {"intent": "chitchat"}}
    assert "1 added, 1 replaced" in capsys.readouterr().out


def test_import_none_hint_and_invalid_variables(tmp_path: Path, capsys) -> None:
    inbox = tmp_path / "inbox.jsonl"
    bad = json.loads(_line("fb5-run5", {"intent": "slides"}))
    bad["variables"] = {}
    inbox.write_text(
        json.dumps({**json.loads(_line("fb4-run4", {"intent": "slides"})),
                    "stage_hint": None}) + "\n" + json.dumps(bad) + "\n"
        + _line("fb6-run6", {}) + "\n", encoding="utf-8")
    cases, unlabelled, invalid = corpus.import_inbox(inbox, "router")
    assert [c.case_id for c in cases] == ["fb4-run4"]
    assert (unlabelled, invalid) == (1, 1)
    out = tmp_path / "o.jsonl"
    cli.main(["import-inbox", "--inbox", str(inbox), "--stage", "router",
              "--out", str(out)])
    assert "1 unlabelled, 1 invalid" in capsys.readouterr().out


REVIEW_ARGS = {"target_run_id": 3, "trigger": "keyword", "answer_message_id": 4,
               "question": "Q?", "answer": "A.", "user_followup": "wrong",
               "trace_summary": "route: paper_qa"}


def _review_db(tmp_path: Path) -> Path:
    db = tmp_path / "r.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tool_calls (run_id INT, step_index INT, agent TEXT, "
                 "tool TEXT, args_redacted_json TEXT, result_summary_json TEXT)")
    rows = [
        (10, {"verdict": "confirmed", "reasons": ["factual"], "doubt": "d",
              "guessed_reasons": ["citation"], "step_ids_read": [1, 2],
              "cited_chunk_ids": [5], "status": "final", "feedback_id": 9}),
        (11, {"skipped": "blank answer"}),
        (12, {"verdict": "maybe", "reasons": [], "doubt": ""}),  # fails validation
        (13, None),  # error step: no recorded output
    ]
    for run_id, result in rows:
        conn.execute(
            "INSERT INTO tool_calls VALUES (?, 0, 'feedback', 'feedback:review', ?, ?)",
            (run_id, json.dumps(REVIEW_ARGS),
             json.dumps(result) if result is not None else None))
    conn.commit()
    conn.close()
    return db


def test_harvest_ignores_skipped_review_steps(tmp_path: Path) -> None:
    cases = corpus.harvest(_review_db(tmp_path), "feedback_review")
    assert sorted(c.source_run_id for c in cases) == [10, 12, 13]


def test_export_steps_review_target_is_the_verdict_only(tmp_path: Path) -> None:
    pdir = tmp_path / "prompts" / "feedback_review"
    pdir.mkdir(parents=True)
    (pdir / "v1.yaml").write_text(
        "system: |\n  SYS {reasons}\nuser: |\n  {question}\n  {answer}\n"
        "  {user_followup}\n  {trace_summary}\n", encoding="utf-8")
    rows = corpus.export_steps(_review_db(tmp_path), "feedback_review", version="v1",
                               prompts_dir=tmp_path / "prompts")
    assert [r["source_run_id"] for r in rows] == [10]
    msgs = rows[0]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant"]
    assert "Q?" in msgs[1]["content"]
    assert json.loads(msgs[2]["content"]) == {
        "verdict": "confirmed", "reasons": ["factual"], "doubt": "d"}
