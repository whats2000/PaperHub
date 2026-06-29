from benchmark.agent import store


def _meta(**over):
    base = dict(
        created_at="2026-06-29T10:00:00", git_commit="abc123", stage="router",
        prompt_version="router/v1", model="gemini/gemini-2.5-flash",
        corpus="core", n_cases=2, reps=1, mean_score=0.5, mean_tokens_in=120.0, notes="",
    )
    base.update(over)
    return base


def _scores():
    return [
        {"case_id": "r1", "rep": 0, "score": 1.0, "tokens_in": 100, "rationale": "hit",
         "output_json": '{"intent":"paper_qa"}', "error": None},
        {"case_id": "r2", "rep": 0, "score": 0.0, "tokens_in": 140, "rationale": "miss",
         "output_json": '{"intent":"slides"}', "error": None},
    ]


def test_record_then_list_and_get(tmp_path):
    p = tmp_path / "experiments.jsonl"
    exp_id = store.record_experiment(p, meta=_meta(), scores=_scores())
    assert exp_id == 1
    exp2 = store.record_experiment(p, meta=_meta(prompt_version="router/v2", mean_score=1.0), scores=_scores())
    assert exp2 == 2  # monotonic id
    rows = store.list_experiments(p, stage="router")
    assert len(rows) == 2 and rows[0]["id"] == 2  # newest first
    assert {r["prompt_version"] for r in rows} == {"router/v1", "router/v2"}
    assert "scores" not in rows[0]  # summaries omit nested scores
    scores = store.get_scores(p, 1)
    assert {s["case_id"] for s in scores} == {"r1", "r2"}


def test_compare(tmp_path):
    p = tmp_path / "experiments.jsonl"
    a = store.record_experiment(p, meta=_meta(prompt_version="router/v1"), scores=[
        {"case_id": "r1", "rep": 0, "score": 0.0, "tokens_in": 100, "rationale": "", "output_json": "{}", "error": None}])
    b = store.record_experiment(p, meta=_meta(prompt_version="router/v2"), scores=[
        {"case_id": "r1", "rep": 0, "score": 1.0, "tokens_in": 80, "rationale": "", "output_json": "{}", "error": None}])
    cmp = store.compare(p, a, b)
    assert cmp["mean_delta"] == 1.0
    per = {x["case_id"]: x for x in cmp["per_case"]}
    assert per["r1"]["a_score"] == 0.0 and per["r1"]["b_score"] == 1.0 and per["r1"]["delta"] == 1.0


def test_empty_store_returns_empty(tmp_path):
    p = tmp_path / "missing.jsonl"
    assert store.list_experiments(p) == []
    assert store.get_scores(p, 1) == []


def test_list_experiments_unfiltered(tmp_path):
    p = tmp_path / "experiments.jsonl"
    store.record_experiment(p, meta=_meta(), scores=_scores())
    store.record_experiment(p, meta=_meta(prompt_version="router/v2"), scores=_scores())
    assert len(store.list_experiments(p)) == 2  # no stage filter
