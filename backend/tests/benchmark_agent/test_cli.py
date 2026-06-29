from benchmark.agent import cli, store
from benchmark.agent.experiment import ExperimentMeta, ExperimentResult
from benchmark.agent.grade import CaseScore


def _write_corpus(path):
    path.write_text(
        '{"case_id":"c1","stage":"router","variables":{"user_message":"q1","enabled_refs_count":1,"slide_attached":false},"expect":{"intent":"paper_qa"},"rubric":"","source_run_id":null,"observed":null}\n'
        '{"case_id":"c2","stage":"router","variables":{"user_message":"q2","enabled_refs_count":1,"slide_attached":false},"expect":{"intent":"slides"},"rubric":"","source_run_id":null,"observed":null}\n',
        encoding="utf-8")


def test_run_then_list(tmp_path, monkeypatch, capsys):
    corpus = tmp_path / "router.core.jsonl"
    _write_corpus(corpus)
    db = tmp_path / "experiments.jsonl"

    async def _fake_run_experiment(spec, version, cases, **kw):
        scores = [CaseScore("c1", 0, 1.0, 100, "hit", {"intent": "paper_qa"}),
                  CaseScore("c2", 0, 0.0, 100, "miss", {"intent": "paper_qa"})]
        meta = ExperimentMeta(git_commit="abc", stage="router", prompt_version="router/v1",
                              model=kw["model"], corpus=kw.get("corpus_name", ""), reps=1,
                              created_at=kw.get("created_at", ""))
        return ExperimentResult(meta=meta, scores=scores, mean_score=0.5, mean_tokens_in=100.0)

    monkeypatch.setattr(cli, "_run_experiment", _fake_run_experiment)
    rc = cli.main(["run", "--stage", "router", "--version", "v1", "--corpus", str(corpus),
                   "--model", "gemini/gemini-2.5-flash", "--store", str(db), "--prompts-dir", str(tmp_path)])
    assert rc == 0
    exps = store.list_experiments(db, stage="router")
    assert len(exps) == 1 and exps[0]["mean_score"] == 0.5

    rc = cli.main(["list", "--store", str(db), "--stage", "router"])
    assert rc == 0 and "router/v1" in capsys.readouterr().out
