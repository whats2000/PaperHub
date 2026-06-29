import pytest

from benchmark.agent import grade
from benchmark.agent.corpus import CorpusCase
from benchmark.agent.replay_types import ReplayOutput
from benchmark.agent.stages import get_stage


def _case(expect):
    return CorpusCase(case_id="c1", stage="router",
                      variables={"user_message": "q", "enabled_refs_count": 0, "slide_attached": False},
                      expect=expect, rubric="route correctly")


@pytest.mark.asyncio
async def test_deterministic_skips_judge():
    spec = get_stage("router")
    replay = ReplayOutput(output={"intent": "paper_qa"}, tokens_in=88)

    async def _boom(**kw):
        raise AssertionError("judge must not run for deterministic router")

    s = await grade.score_case(spec, _case({"intent": "paper_qa"}), replay, 0, judge_model="x", judge_fn=_boom)
    assert s.score == 1.0 and s.tokens_in == 88 and s.error is None
    miss = await grade.score_case(spec, _case({"intent": "slides"}), replay, 0, judge_model="x", judge_fn=_boom)
    assert miss.score == 0.0


@pytest.mark.asyncio
async def test_errored_replay_scores_zero():
    spec = get_stage("router")
    replay = ReplayOutput(output={}, tokens_in=12, error="provider 500")
    s = await grade.score_case(spec, _case({"intent": "paper_qa"}), replay, 1)
    assert s.score == 0.0 and s.error == "provider 500"


@pytest.mark.asyncio
async def test_judge_scalar_normalizes(monkeypatch):
    async def _fake(**kw):
        return {"choices": [{"message": {"content": '{"score": 8, "rationale": "good"}'}}]}
    monkeypatch.setattr(grade.litellm, "acompletion", _fake)
    score, rationale = await grade.judge_scalar(request="q", rubric="r", output_text="ans", model="m")
    assert score == 0.8 and rationale == "good"
