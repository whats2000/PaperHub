from collections import Counter
from pathlib import Path

import pytest

from benchmark.agent.corpus import load_corpus
from benchmark.agent.prompts import list_variants, load_variant
from benchmark.agent.replay import render_messages
from benchmark.agent.stages import get_stage
from paperhub.feedback.models import ReviewVerdict, format_reasons_block
from paperhub.feedback.trigger import DEFAULT_KEYWORDS, matches_trigger
from paperhub.llm.prompts.registry import PromptRegistry

BACKEND = Path(__file__).resolve().parents[2]
CORPUS = BACKEND / "benchmark/agent/corpus/feedback_review.core.jsonl"
PROMPTS = BACKEND / "benchmark/agent/prompts"
WINNER = "v10"  # see benchmark/agent/prompts/feedback_review/README.md
SLOTS = ("question", "answer", "user_followup", "trace_summary", "reasons")


def test_review_spec_parses_and_scores_verdict():
    spec = get_stage("feedback_review")
    assert spec.response_model is ReviewVerdict
    out = spec.output_summary(ReviewVerdict(verdict="confirmed", reasons=["factual"], doubt="d"))
    assert out == {"verdict": "confirmed", "reasons": ["factual"], "doubt": "d"}
    assert spec.deterministic_score({"verdict": "confirmed"}, out) == 1.0
    assert spec.deterministic_score({"verdict": "uncertain"}, out) == 0.0
    assert spec.deterministic_score({}, out) is None


def test_corpus_has_the_sixteen_labelled_turns():
    cases = load_corpus(CORPUS)
    assert len(cases) == 16
    assert Counter(c.expect["verdict"] for c in cases) == {
        "confirmed": 6, "rejected": 5, "uncertain": 5}
    followups = [c.variables["user_followup"] for c in cases]
    assert any("我是問第二篇論文" in f for f in followups)
    assert "wrong, I asked about the decoder" in followups
    # Every case reaches the reviewer only because the keyword trigger fired.
    assert all(matches_trigger(f, DEFAULT_KEYWORDS) for f in followups)


@pytest.mark.parametrize("version", ["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10"])
def test_every_variant_renders_every_slot_in_tags(version):
    assert version in list_variants("feedback_review", prompts_dir=PROMPTS)
    system, user = load_variant("feedback_review", version, prompts_dir=PROMPTS)
    for c in load_corpus(CORPUS):
        content = render_messages(system, user, c.variables)[-1]["content"]
        for slot in SLOTS:
            assert f"<{slot}>\n{c.variables[slot]}\n</{slot}>" in content


def test_shipped_prompt_matches_the_eval_winner():
    slot = PromptRegistry().get("feedback_review/v1")
    assert (slot.system, slot.user_template) == load_variant(
        "feedback_review", WINNER, prompts_dir=PROMPTS)
    rendered = slot.user_template.format(
        question="q", answer="a", user_followup="f", trace_summary="t",
        reasons=format_reasons_block())
    assert "<user_followup>\nf\n</user_followup>" in rendered
    assert "- wrong_route:" in rendered


def test_shipped_examples_do_not_overlap_the_corpus():
    """A worked example identical to a test case hides whether the model reads
    its input, so no example follow-up / answer may appear in the corpus."""
    user = PromptRegistry().get("feedback_review/v1").user_template
    examples = user.split("</examples>")[0]
    corpus_text = CORPUS.read_text(encoding="utf-8")
    for probe in ("incorrect, the paper says 1000", "違うデータセット", "好像錯了",
                  "DDPM", "ResNet", "batch size of 256"):
        assert probe in examples and probe not in corpus_text


def test_shipped_prompt_quotes_no_corpus_followup():
    slot = PromptRegistry().get("feedback_review/v1")
    prompt = slot.system + slot.user_template
    for c in load_corpus(CORPUS):
        assert c.variables["user_followup"] not in prompt
