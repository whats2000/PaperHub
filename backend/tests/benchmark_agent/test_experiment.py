import pytest

from benchmark.agent import experiment, store
from benchmark.agent.corpus import CorpusCase
from benchmark.agent.execute import ExecResult
from benchmark.agent.experiment import run_experiment, to_store_payload
from benchmark.agent.stages import get_stage
from paperhub.models.domain import RoutingDecision


def _seed_variant(tmp_path):
    d = tmp_path / "router"
    d.mkdir(parents=True)
    (d / "v1.yaml").write_text("system: |\n  classify\nuser: |\n  {user_message}\n", encoding="utf-8")


def _corpus():
    return [
        CorpusCase(case_id="c1", stage="router", expect={"intent": "paper_qa"},
                   variables={"user_message": "what is MHA?", "enabled_refs_count": 1, "slide_attached": False}),
        CorpusCase(case_id="c2", stage="router", expect={"intent": "slides"},
                   variables={"user_message": "make slides", "enabled_refs_count": 1, "slide_attached": False}),
    ]


@pytest.mark.asyncio
async def test_run_experiment_aggregates_and_persists(tmp_path, monkeypatch):
    _seed_variant(tmp_path)

    async def _fake_execute(requests, **kw):
        d = RoutingDecision(intent="paper_qa", model_tier="small", confidence=0.9,
                            reasoning="x", resolved_query="q", response_language="English")
        return {r.key: ExecResult(r.key, d, 100, None, "concurrent") for r in requests}

    monkeypatch.setattr(experiment, "execute", _fake_execute)
    spec = get_stage("router")
    result = await run_experiment(spec, "v1", _corpus(), model="m", reps=2, prompts_dir=tmp_path,
                                  count_tokens=lambda m, msgs: 100, git_commit="abc",
                                  created_at="2026-06-29T10:00:00", corpus_name="core")
    assert result.mean_score == 0.5         # c1 hits paper_qa (1.0), c2 misses (0.0)
    assert result.mean_tokens_in == 100.0
    assert len(result.scores) == 4          # 2 cases × 2 reps

    p = tmp_path / "experiments.jsonl"
    meta, rows = to_store_payload(result)
    exp_id = store.record_experiment(p, meta=meta, scores=rows)
    assert store.list_experiments(p)[0]["mean_score"] == 0.5
    assert len(store.get_scores(p, exp_id)) == 4
    assert store.list_experiments(p)[0]["n_cases"] == 2


@pytest.mark.asyncio
async def test_run_experiment_missing_result_becomes_error(tmp_path, monkeypatch):
    _seed_variant(tmp_path)

    async def _partial_execute(requests, **kw):
        d = RoutingDecision(intent="paper_qa", model_tier="small", confidence=0.9,
                            reasoning="x", resolved_query="q", response_language="English")
        # Drop the LAST request's key to simulate a partial executor failure.
        return {r.key: ExecResult(r.key, d, 100, None, "concurrent") for r in requests[:-1]}

    monkeypatch.setattr(experiment, "execute", _partial_execute)
    result = await run_experiment(get_stage("router"), "v1", _corpus(), model="m", reps=1,
                                  prompts_dir=tmp_path, count_tokens=lambda m, msgs: 100)
    assert len(result.scores) == 2  # both cases scored, none dropped
    assert any(s.error and "missing result" in s.error for s in result.scores)
