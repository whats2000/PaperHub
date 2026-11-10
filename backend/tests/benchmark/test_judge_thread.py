"""The LLM judge scores correctness, grounding AND thread continuity."""
from __future__ import annotations

import json
from typing import Any

import pytest

from benchmark import judge


def _fake_completion(verdict: dict[str, Any], seen: list[dict[str, Any]]) -> Any:
    async def acompletion(**kw: Any) -> dict[str, Any]:
        seen.append(kw)
        return {"choices": [{"message": {"content": json.dumps(verdict)}}]}
    return acompletion


CASE: dict[str, Any] = {
    "id": "s1-a",
    "prompt": "What about the second one's limitations?",
    "rubric": "ReMoE's limitations, cited.",
    "final": "ReMoE can have dead experts early on [chunk:202].",
    "turns": [{"user": "Compare Expert Choice and ReMoE.",
               "assistant": "Expert Choice picks tokens per expert [chunk:201]."}],
    "cited_chunks": [{"id": 202, "section": "Method", "text": "ReMoE ... dead experts ..."}],
}


async def test_judge_prompt_carries_the_prior_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr("benchmark.judge.litellm.acompletion", _fake_completion(
        {"correct": 1, "grounded": 1, "on_thread": 1, "confidence": 0.9,
         "rationale": "fine"}, seen))
    v = await judge.judge_case(CASE, model="m")
    assert v.score == 1
    [call] = seen
    assert call["temperature"] == 0.0
    system, user = (m["content"] for m in call["messages"])
    assert "on_thread" in system
    assert "<prior_turns>" in user
    assert "Compare Expert Choice and ReMoE." in user
    assert "Expert Choice picks tokens per expert" in user
    assert "<request>\nWhat about the second one's limitations?\n</request>" in user
    assert "[chunk:202]" in user


async def test_first_turn_case_says_there_are_no_prior_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr("benchmark.judge.litellm.acompletion", _fake_completion(
        {"correct": 1, "grounded": 1, "on_thread": 1, "confidence": 1.0,
         "rationale": "ok"}, seen))
    case = {k: v for k, v in CASE.items() if k != "turns"}
    await judge.judge_case(case, model="m")
    user = seen[0]["messages"][1]["content"]
    assert "(none — first turn)" in user


@pytest.mark.parametrize(("correct", "grounded", "on_thread", "score"), [
    (1, 1, 1, 1),
    (1, 1, 0, 0),   # correct + grounded but off the thread -> 0
    (1, 0, 1, 0),
    (0, 1, 1, 0),
])
def test_score_is_one_only_when_all_three_criteria_hold(
    correct: int, grounded: int, on_thread: int, score: int,
) -> None:
    v = judge.JudgeVerdict(correct=correct, grounded=grounded, on_thread=on_thread,
                           confidence=0.8, rationale="r")
    assert v.score == score


async def test_judge_results_stores_each_criterion(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr("benchmark.judge.litellm.acompletion", _fake_completion(
        {"correct": 1, "grounded": 1, "on_thread": 0, "confidence": 0.7,
         "rationale": "answers about the first paper"}, seen))
    errored = {"id": "e", "prompt": "x", "error": "stream dropped"}
    out = await judge.judge_results([dict(CASE), errored], model="m")
    j = out[0]["judge"]
    assert (j["score"], j["correct"], j["grounded"], j["on_thread"]) == (0, 1, 1, 0)
    assert j["model"] == "m"
    assert out[1]["judge"]["score"] == 0
    assert len(seen) == 1   # the errored case never calls the model


def test_judge_cli_judges_both_sides_and_writes_the_gate(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from benchmark import runner

    seen: list[dict[str, Any]] = []
    monkeypatch.setattr("benchmark.judge.litellm.acompletion", _fake_completion(
        {"correct": 1, "grounded": 1, "on_thread": 1, "confidence": 1.0,
         "rationale": "ok"}, seen))

    async def fake_pairwise(**_: Any) -> str:
        return "tie"

    monkeypatch.setattr(runner, "judge_pairwise", fake_pairwise)
    row = {**CASE, "scenario": 1, "suite": "scenarios",
           "compare": {**CASE, "final": "baseline answer [chunk:202]."}}
    results = tmp_path / "r.json"
    results.write_text(json.dumps([row]), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["judge", "--results", str(results),
                                      "--env", str(tmp_path / "none.env")])
    judge.main()
    [out] = json.loads(results.read_text(encoding="utf-8"))
    assert out["judge"]["score"] == 1 and out["compare"]["judge"]["score"] == 1
    assert out["pairwise"]["orders"] == ["tie", "tie"]
    gate_json = json.loads((tmp_path / "r.gate.json").read_text(encoding="utf-8"))
    assert gate_json["overall"] == "FAIL"   # one scenario case is not the full set
    assert len(seen) == 2


async def test_judge_sees_the_whole_cited_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    # Supporting text deep in a long chunk must reach the judge (a 1200-char
    # cut made it rule supported claims ungrounded).
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr("benchmark.judge.litellm.acompletion", _fake_completion(
        {"correct": 1, "grounded": 1, "on_thread": 1, "confidence": 1.0,
         "rationale": "ok"}, seen))
    tail = "Due to the reduced dimension of each head, the total cost is similar."
    case = {**CASE, "cited_chunks": [{"id": 9, "section": "s", "text": "x" * 3000 + tail}]}
    await judge.judge_case(case, model="m")
    assert tail in seen[0]["messages"][1]["content"]


async def test_judge_sees_cited_figure_images(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    """A ``[fig:P/ID]`` citation reaches the judge as its caption plus the
    image itself, so a value read off a plot can be checked."""
    from PIL import Image

    img = tmp_path / "fig-003.png"
    Image.new("RGB", (8, 4), (0, 100, 0)).save(img)
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr("benchmark.judge.litellm.acompletion", _fake_completion(
        {"correct": 1, "grounded": 1, "on_thread": 1, "confidence": 1.0,
         "rationale": "ok"}, seen))
    case = {**CASE, "final": "About 77% [fig:20/fig-003].", "cited_figures": [
        {"key": "20/fig-003", "paper_content_id": 20, "figure_id": "fig-003",
         "caption": "Fig. 3 | Bar charts", "image_path": str(img)},
        {"key": "20/fig-009", "paper_content_id": 20, "figure_id": "fig-009",
         "caption": "", "image_path": None}]}
    await judge.judge_case(case, model="m")
    parts = seen[0]["messages"][1]["content"]
    assert isinstance(parts, list)
    text = parts[0]["text"]
    assert "[fig:20/fig-003] (image attached below): Fig. 3 | Bar charts" in text
    assert "[fig:20/fig-009] (image missing)" in text
    images = [p for p in parts if p["type"] == "image_url"]
    assert len(images) == 1 and images[0]["image_url"]["url"].startswith("data:image/png;base64,")
    # Without figures the user message stays plain text.
    seen.clear()
    await judge.judge_case(CASE, model="m")
    assert isinstance(seen[0]["messages"][1]["content"], str)


async def test_judge_sees_cited_page_regions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    import pymupdf

    pdf = tmp_path / "paper.pdf"
    doc = pymupdf.open()
    doc.new_page(width=612, height=792).insert_text((72, 100), "AP 0.732", fontsize=12)
    doc.save(pdf)
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr("benchmark.judge.litellm.acompletion", _fake_completion(
        {"correct": 1, "grounded": 1, "on_thread": 1, "confidence": 1.0,
         "rationale": "ok"}, seen))
    case = {**CASE, "final": "AP is 0.732 [view:7/1/0,0,500,200].", "cited_views": [
        {"key": "7/1/0,0,500,200", "paper_content_id": 7, "page": 1,
         "box": [0, 0, 500, 200], "pdf_path": str(pdf)}]}
    await judge.judge_case(case, model="m")
    parts = seen[0]["messages"][1]["content"]
    assert "[view:7/1/0,0,500,200] (region of page 1; image attached below)" in parts[0]["text"]
    assert sum(p["type"] == "image_url" for p in parts) == 1
