import pytest

from benchmark.agent import store, sweep
from benchmark.agent.eval_config import load_eval_config
from benchmark.agent.experiment import ExperimentMeta, ExperimentResult
from benchmark.agent.grade import CaseScore
from benchmark.agent.sweep import matrix_report, run_sweep


def _write(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_load_eval_config(tmp_path):
    core = tmp_path / "core.jsonl"
    _write(core, ['{"case_id":"a","stage":"router","variables":{"user_message":"q","enabled_refs_count":1,"slide_attached":false},"expect":{"intent":"paper_qa"}}'])
    toml = tmp_path / "router.eval.toml"
    toml.write_text('[eval]\nstage="router"\nmodel="m"\nreps=2\nbackend="auto"\nvariants=["v1","v2"]\n'
                    f'store="{(tmp_path / "e.jsonl").as_posix()}"\nprompts_dir="{tmp_path.as_posix()}"\n\n'
                    f'[[testsets]]\nname="core"\ncorpus="{core.as_posix()}"\n', encoding="utf-8")
    cfg = load_eval_config(toml)
    assert cfg.stage == "router" and cfg.reps == 2 and cfg.backend == "auto"
    assert cfg.variants == ["v1", "v2"] and cfg.testsets[0].name == "core"


@pytest.mark.asyncio
async def test_run_sweep_grid_and_matrix(tmp_path, monkeypatch):
    core = tmp_path / "core.jsonl"
    reg = tmp_path / "regression.jsonl"
    _write(core, ['{"case_id":"a","stage":"router","variables":{"user_message":"q","enabled_refs_count":1,"slide_attached":false},"expect":{"intent":"paper_qa"}}'])
    _write(reg, ['{"case_id":"b","stage":"router","variables":{"user_message":"d","enabled_refs_count":1,"slide_attached":false},"expect":{"intent":"slides"}}'])
    toml = tmp_path / "router.eval.toml"
    toml.write_text('[eval]\nstage="router"\nmodel="m"\nreps=1\nvariants=["v1","v2"]\n'
                    f'store="{(tmp_path / "e.jsonl").as_posix()}"\nprompts_dir="{tmp_path.as_posix()}"\n\n'
                    f'[[testsets]]\nname="core"\ncorpus="{core.as_posix()}"\n\n'
                    f'[[testsets]]\nname="regression"\ncorpus="{reg.as_posix()}"\n', encoding="utf-8")
    cfg = load_eval_config(toml)

    async def _fake_run_experiment(spec, version, cases, **kw):
        # v1 -> always paper_qa; v2 -> always slides. core expects paper_qa, regression expects slides.
        intent = "slides" if version == "v2" else "paper_qa"
        scores = [CaseScore(c.case_id, 0, 1.0 if c.expect["intent"] == intent else 0.0, 100, "", {}) for c in cases]
        mean = sum(s.score for s in scores) / len(scores)
        meta = ExperimentMeta("abc", "router", f"router/{version}", "m", kw.get("corpus_name", ""), 1, "")
        return ExperimentResult(meta=meta, scores=scores, mean_score=mean, mean_tokens_in=100.0)

    monkeypatch.setattr(sweep, "run_experiment", _fake_run_experiment)
    cells = await run_sweep(cfg, store_path=cfg.store, git_commit="abc", created_at="2026-06-29T10:00:00",
                            count_tokens=lambda m, msgs: 100)
    assert len(cells) == 4 and len(store.list_experiments(cfg.store)) == 4
    md = matrix_report(cfg, cells)
    assert "router/v1" in md and "router/v2" in md and "core" in md and "regression" in md
    assert "-1.00" in md and "⚠" in md   # core regressed v1->v2
    assert "+1.00" in md                  # regression improved v1->v2
