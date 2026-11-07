import json
from pathlib import Path

import pytest

from benchmark.agent.corpus import CorpusCase, load_corpus
from benchmark.agent.pairwise import pairwise_compare
from benchmark.agent.prompts import load_variant
from benchmark.agent.replay import render_messages
from benchmark.agent.stages import get_stage, translate_failures
from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.translation.models import TranslatedBlocks

BACKEND = Path(__file__).resolve().parents[2]
CORPUS = BACKEND / "benchmark/agent/corpus/translate_blocks.core.jsonl"
SRC = {"h:1": "Loss ⟦0⟧ falls ⟦BR⟧ fast.", "h:2": "No math here."}


def _out(**texts: str) -> dict:
    return {"items": [{"key": k.replace("_", ":"), "text": t} for k, t in texts.items()]}


def test_translate_spec_parses_and_summarizes():
    spec = get_stage("translate_blocks")
    assert spec.response_model is TranslatedBlocks
    parsed = TranslatedBlocks.model_validate({"items": [{"key": "h:1", "text": "x"}]})
    assert spec.output_summary(parsed) == {"items": [{"key": "h:1", "text": "x"}]}


def test_translate_score_passes_intact_output():
    spec = get_stage("translate_blocks")
    out = _out(h_1="損失 ⟦0⟧ 快速 ⟦BR⟧ 下降。", h_2="這裡沒有數學。")
    assert translate_failures({"blocks": SRC}, out) == []
    assert spec.deterministic_score({"blocks": SRC}, out) == 1.0


@pytest.mark.parametrize("out", [
    _out(h_1="損失 ⟦0⟧ 快速下降。", h_2="無"),             # dropped ⟦BR⟧
    _out(h_1="損失 ⟦0⟧ ⟦BR⟧ 下降。", h_2="無 ⟦0⟧"),        # invented token
    _out(h_1="損失 ⟦0⟧ ⟦0⟧ ⟦BR⟧ 下降。", h_2="無"),        # duplicated token
    _out(h_1="損失 ⟦0⟧ ⟦BR⟧ 下降。"),                        # missing key
    {"items": [{"key": "h:1", "text": "⟦0⟧⟦BR⟧"}, {"key": "h:1", "text": "⟦0⟧⟦BR⟧"},
               {"key": "h:2", "text": "無"}]},                # duplicate key
    _out(h_1="損失 ⟦0⟧ ⟦BR⟧ 下降。", h_2="無", h_9="extra"),  # invented key
    _out(h_1="損失 ⟦0⟧ 快速下降。⟦BR⟧", h_2="無"),           # mid ⟦BR⟧ parked at the end
])
def test_translate_score_fails_contract_violations(out):
    assert get_stage("translate_blocks").deterministic_score({"blocks": SRC}, out) == 0.0


def test_translate_score_checks_required_and_forbidden_terms():
    expect = {"blocks": {"h:1": "Each token."}, "require": {"h:1": ["符元"]},
              "forbid": {"h:1": ["詞元"]}}
    assert translate_failures(expect, _out(h_1="每個符元。")) == []
    assert translate_failures(expect, _out(h_1="每個 token。")) == ["missing term '符元' in h:1"]
    assert translate_failures(expect, _out(h_1="每個符元（詞元）。")) == [
        "forbidden term '詞元' in h:1"]


def test_corpus_has_fourteen_blocks_and_renders_with_every_variant():
    cases = load_corpus(CORPUS)
    assert sum(len(c.expect["blocks"]) for c in cases) == 14
    prompts_dir = BACKEND / "benchmark/agent/prompts"
    for v in ("v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9"):
        system, user = load_variant("translate_blocks", v, prompts_dir=prompts_dir)
        for c in cases:
            msgs = render_messages(system, user, c.variables)
            for block_text in c.expect["blocks"].values():
                assert block_text in msgs[-1]["content"]


def test_shipped_prompt_matches_the_eval_winner():
    slot = PromptRegistry().get("translate_blocks/v1")
    system, user = load_variant("translate_blocks", "v9",
                                prompts_dir=BACKEND / "benchmark/agent/prompts")
    assert (slot.system, slot.user_template) == (system, user)
    rendered = slot.user_template.format(language="ja", terminology="", context="",
                                         blocks_xml='<block key="h:0">x</block>')
    assert "<target_language>ja</target_language>" in rendered
    assert '<block key="h:0">x</block>' in rendered


def test_shipped_example_term_is_not_in_the_corpus():
    """The prompt's worked example must not demonstrate a corpus terminology
    term, or copying the example is indistinguishable from reading <terminology>."""
    user = PromptRegistry().get("translate_blocks/v1").user_template
    example = user.split("</example>")[0]
    corpus_text = CORPUS.read_text(encoding="utf-8")
    for term in ("dropout", "隨機失活"):
        assert term in example and term not in corpus_text
    assert "詞元" not in example and "符元" not in example


def _row(rep: int, out: dict | None, error: str | None = None) -> dict:
    return {"case_id": "c", "rep": rep, "score": None, "tokens_in": 1, "rationale": "",
            "output_json": json.dumps(out or {}, ensure_ascii=False), "error": error}


@pytest.mark.asyncio
async def test_pairwise_deterministic_first_then_order_swapped_judge():
    spec = get_stage("translate_blocks")
    case = CorpusCase(case_id="c", stage="translate_blocks",
                      variables={"language": "ja"}, expect={"blocks": {"h:1": "a ⟦0⟧"}})
    good_a = {"items": [{"key": "h:1", "text": "A ⟦0⟧"}]}
    good_b = {"items": [{"key": "h:1", "text": "B ⟦0⟧"}]}
    bad = {"items": [{"key": "h:1", "text": "B"}]}
    calls: list[tuple[str, str]] = []

    async def judge(*, output_a: str, output_b: str, **_: object) -> str:
        calls.append((output_a, output_b))
        # Prefers whichever output contains "A ⟦0⟧" on rep 1; position-biased on rep 2.
        if '"B ⟦0⟧"' in output_a and len(calls) > 2:
            return "A"
        return "A" if '"A ⟦0⟧"' in output_a else "B"

    rep = await pairwise_compare(
        spec, [case], [_row(0, good_a), _row(1, good_a), _row(2, good_a), _row(3, None, "boom")],
        [_row(0, bad), _row(1, good_b), _row(2, good_b), _row(3, bad)],
        label_a="a", label_b="b", judge_model="m", judge=judge)
    got = [(r.rep, r.winner, r.how) for r in rep.rows]
    assert got == [(0, "A", "deterministic"), (1, "A", "judge"),
                   (2, "tie", "judge-split"), (3, "tie", "both-failed")]
    assert len(calls) == 4  # judge only runs when both pass, twice each
    assert "A wins: 2" in rep.to_markdown()
