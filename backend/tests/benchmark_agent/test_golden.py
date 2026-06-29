import pytest

from benchmark.agent import corpus as corpus_mod
from benchmark.agent.corpus import CorpusCase, emit_golden
from benchmark.agent.replay_types import ReplayOutput
from benchmark.agent.stages import get_stage


@pytest.mark.asyncio
async def test_emit_golden(monkeypatch, tmp_path):
    async def _fake_replay(spec, version, case, **kw):
        return ReplayOutput(output={"intent": "paper_qa", "resolved_query": case.variables["user_message"],
                                    "response_language": "English", "confidence": 0.9}, tokens_in=0)
    monkeypatch.setattr(corpus_mod, "replay_stage", _fake_replay)
    corpus = [CorpusCase(case_id="c1", stage="router", expect={"intent": "paper_qa"}, source_run_id=7,
                         variables={"user_message": "what is MHA?", "enabled_refs_count": 1, "slide_attached": False})]
    golden = await emit_golden(get_stage("router"), "v2", corpus, model="m", prompts_dir=tmp_path)
    assert golden == [{
        "case_id": "c1", "source_run_id": 7,
        "variables": {"user_message": "what is MHA?", "enabled_refs_count": 1, "slide_attached": False},
        "output": {"intent": "paper_qa", "resolved_query": "what is MHA?",
                   "response_language": "English", "confidence": 0.9}}]
