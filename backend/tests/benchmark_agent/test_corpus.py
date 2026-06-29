import json
import sqlite3

import pytest

from benchmark.agent import corpus
from benchmark.agent.corpus import CorpusCase

_DDL = """
CREATE TABLE runs (id INTEGER PRIMARY KEY);
CREATE TABLE tool_calls (
    run_id INTEGER, step_index INTEGER, agent TEXT, tool TEXT, model TEXT,
    args_redacted_json TEXT, result_summary_json TEXT, status TEXT
);
"""


def _seed(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(_DDL)
    conn.execute("INSERT INTO runs (id) VALUES (7)")
    conn.execute(
        "INSERT INTO tool_calls (run_id, step_index, agent, tool, model, "
        "args_redacted_json, result_summary_json, status) VALUES (?,?,?,?,?,?,?,?)",
        (7, 0, "router", "classify", "gemini/gemini-2.5-flash",
         json.dumps({"user_message": "what is MHA?", "enabled_refs_count": 1, "slide_attached": False}),
         json.dumps({"intent": "paper_qa", "resolved_query": "what is MHA?", "response_language": "English", "confidence": 0.9}),
         "ok"))
    conn.execute("INSERT INTO tool_calls (run_id, step_index, agent, tool, status) "
                 "VALUES (7,1,'research','paper_qa:synthesize','ok')")
    conn.commit()
    conn.close()


def test_harvest_router(tmp_path):
    db = tmp_path / "paperhub.db"
    _seed(db)
    cases = corpus.harvest(db, "router")
    assert len(cases) == 1
    c = cases[0]
    assert c.variables == {"user_message": "what is MHA?", "enabled_refs_count": 1, "slide_attached": False}
    assert c.expect == {"intent": "paper_qa"}
    assert c.observed and c.observed["intent"] == "paper_qa"
    assert c.source_run_id == 7 and c.case_id == "run7-s0"


def test_save_load_roundtrip(tmp_path):
    cases = [CorpusCase(case_id="x1", stage="router",
                        variables={"user_message": "hi", "enabled_refs_count": 0, "slide_attached": False},
                        expect={"intent": "chitchat"}, rubric="greeting")]
    p = tmp_path / "router.core.jsonl"
    corpus.save_corpus(p, cases)
    assert corpus.load_corpus(p) == cases


def test_save_load_roundtrip_with_history(tmp_path):
    history = [{"role": "user", "content": "x"}]
    cases = [CorpusCase(case_id="x2", stage="router",
                        variables={"user_message": "推薦幾篇", "enabled_refs_count": 1, "slide_attached": False},
                        expect={"intent": "paper_suggest"}, rubric="anaphoric suggest",
                        history=history)]
    p = tmp_path / "router.edge.jsonl"
    corpus.save_corpus(p, cases)
    loaded = corpus.load_corpus(p)
    assert len(loaded) == 1
    assert loaded[0].history == history


def test_harvest_run_ids_filter(tmp_path):
    db = tmp_path / "paperhub.db"
    _seed(db)
    assert len(corpus.harvest(db, "router", run_ids=[7])) == 1
    assert corpus.harvest(db, "router", run_ids=[99]) == []
    assert corpus.harvest(db, "router", run_ids=[]) == []  # empty list = no matches, not "all"


def test_load_corpus_malformed_line_raises(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text(
        '{"case_id":"x","stage":"router","variables":{},"expect":{}}\nNOT JSON\n',
        encoding="utf-8")
    with pytest.raises(ValueError):
        corpus.load_corpus(p)
