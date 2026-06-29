import pytest

from benchmark.agent import replay
from benchmark.agent.corpus import CorpusCase
from benchmark.agent.execute import ExecResult
from benchmark.agent.replay import render_messages, replay_stage
from benchmark.agent.stages import get_stage
from paperhub.models.domain import RoutingDecision


def test_render_messages():
    msgs = render_messages("SYS", "MSG: {user_message}", {"user_message": "hi"})
    assert msgs == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "MSG: hi"}]


def test_render_messages_threads_history():
    history = [{"role": "user", "content": "prior q"},
               {"role": "assistant", "content": "prior a"},
               {"role": "system", "content": "DROP ME"},   # non-user/assistant -> filtered
               {"role": "user", "content": ""}]              # empty content -> filtered
    msgs = render_messages("SYS", "MSG: {user_message}", {"user_message": "hi"}, history)
    assert msgs == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "prior q"},
        {"role": "assistant", "content": "prior a"},
        {"role": "user", "content": "MSG: hi"},
    ]


def _seed_variant(tmp_path):
    d = tmp_path / "router"
    d.mkdir(parents=True)
    (d / "v1.yaml").write_text("system: |\n  classify\nuser: |\n  {user_message}\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_replay_stage_maps_execresult(tmp_path, monkeypatch):
    _seed_variant(tmp_path)

    async def _fake_execute(requests, **kw):
        d = RoutingDecision(intent="paper_qa", model_tier="small", confidence=0.8,
                            reasoning="x", resolved_query="q", response_language="English")
        return {requests[0].key: ExecResult(requests[0].key, d, 33, None, "concurrent")}

    monkeypatch.setattr(replay, "execute", _fake_execute)
    case = CorpusCase(case_id="c1", stage="router", expect={"intent": "paper_qa"},
                      variables={"user_message": "what is MHA?", "enabled_refs_count": 1, "slide_attached": False})
    out = await replay_stage(get_stage("router"), "v1", case, model="m", prompts_dir=tmp_path)
    assert out.output["intent"] == "paper_qa" and out.tokens_in == 33 and out.error is None


def test_replay_imports_standalone_before_corpus():
    # Regression: importing replay FIRST (before corpus) must not trip the
    # corpus<->replay cycle. A fresh interpreter that imports only replay proves
    # replay's CorpusCase import is TYPE_CHECKING-only (no runtime corpus import).
    import subprocess
    import sys

    code = (
        "import benchmark.agent.replay; "
        "from benchmark.agent.replay import render_messages, replay_stage, to_replay_output"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
