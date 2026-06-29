import pytest
from pydantic import BaseModel

from benchmark.agent import execute
from benchmark.agent.execute import EvalRequest


class _Out(BaseModel):
    intent: str


def _reqs():
    return [EvalRequest(key="c1", messages=[{"role": "user", "content": "q"}], response_model=_Out)]


@pytest.mark.asyncio
async def test_concurrent_parses_structured(monkeypatch):
    async def _fake_acompletion(**kw):
        return {"choices": [{"message": {"content": '{"intent":"paper_qa"}'}}]}
    monkeypatch.setattr(execute.litellm, "acompletion", _fake_acompletion)
    res = await execute.execute(_reqs(), model="ollama/llama3", backend="concurrent",
                                count_tokens=lambda m, msgs: 42)
    assert res["c1"].parsed.intent == "paper_qa"
    assert res["c1"].tokens_in == 42 and res["c1"].error is None and res["c1"].backend == "concurrent"


@pytest.mark.asyncio
async def test_concurrent_records_error(monkeypatch):
    async def _boom(**kw):
        raise RuntimeError("provider 500")
    monkeypatch.setattr(execute.litellm, "acompletion", _boom)
    res = await execute.execute(_reqs(), model="ollama/llama3", backend="concurrent",
                                count_tokens=lambda m, msgs: 1)
    assert res["c1"].parsed is None and "provider 500" in res["c1"].error


@pytest.mark.asyncio
async def test_auto_uses_batch_for_capable_provider(monkeypatch):
    # Provider 'openai' is batch-capable -> the batch path runs.
    monkeypatch.setattr(execute.litellm, "get_llm_provider", lambda model: (model, "openai", None, None))
    calls = {}

    class _Obj:
        def __init__(self, **kw): self.__dict__.update(kw)

    async def _acreate_file(**kw):
        calls["file"] = True
        return _Obj(id="file-1")

    async def _acreate_batch(**kw):
        calls["batch"] = kw
        return _Obj(id="batch-1", status="validating", output_file_id=None)

    async def _aretrieve_batch(**kw):
        return _Obj(id="batch-1", status="completed", output_file_id="out-1")

    async def _afile_content(**kw):
        line = '{"custom_id":"c1","response":{"status_code":200,"body":{"choices":[{"message":{"content":"{\\"intent\\":\\"slides\\"}"}}]}}}'
        return _Obj(content=line.encode("utf-8"))

    monkeypatch.setattr(execute.litellm, "acreate_file", _acreate_file)
    monkeypatch.setattr(execute.litellm, "acreate_batch", _acreate_batch)
    monkeypatch.setattr(execute.litellm, "aretrieve_batch", _aretrieve_batch)
    monkeypatch.setattr(execute.litellm, "afile_content", _afile_content)
    res = await execute.execute(_reqs(), model="gpt-4o", backend="auto",
                                count_tokens=lambda m, msgs: 7, poll_interval=0)
    assert calls.get("file") and "batch" in calls
    assert res["c1"].parsed.intent == "slides" and res["c1"].backend == "batch_api"


@pytest.mark.asyncio
async def test_batch_failure_degrades_to_concurrent(monkeypatch):
    monkeypatch.setattr(execute.litellm, "get_llm_provider", lambda model: (model, "openai", None, None))

    async def _acreate_file(**kw):
        raise RuntimeError("batch upload unsupported")

    async def _fake_acompletion(**kw):
        return {"choices": [{"message": {"content": '{"intent":"paper_qa"}'}}]}
    monkeypatch.setattr(execute.litellm, "acreate_file", _acreate_file)
    monkeypatch.setattr(execute.litellm, "acompletion", _fake_acompletion)
    res = await execute.execute(_reqs(), model="gpt-4o", backend="auto",
                                count_tokens=lambda m, msgs: 1, poll_interval=0)
    assert res["c1"].parsed.intent == "paper_qa" and res["c1"].backend == "concurrent"
