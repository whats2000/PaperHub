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
WINNER = "v21"  # see benchmark/agent/prompts/feedback_review/README.md
SLOTS = ("question", "answer", "user_followup", "trace_summary", "reasons")


def test_review_spec_parses_and_scores_verdict():
    spec = get_stage("feedback_review")
    assert spec.response_model is ReviewVerdict
    out = spec.output_summary(ReviewVerdict(verdict="confirmed", reasons=["factual"], doubt="d"))
    assert out == {"verdict": "confirmed", "reasons": ["factual"], "doubt": "d"}
    assert spec.deterministic_score({"verdict": "confirmed"}, out) == 1.0
    assert spec.deterministic_score({"verdict": "uncertain"}, out) == 0.0
    assert spec.deterministic_score({}, out) is None


def test_corpus_has_the_labelled_turns():
    cases = load_corpus(CORPUS)
    assert len(cases) == 24
    assert Counter(c.expect["verdict"] for c in cases) == {
        "confirmed": 8, "rejected": 5, "uncertain": 11}
    followups = [c.variables["user_followup"] for c in cases]
    assert any("我是問第二篇論文" in f for f in followups)
    assert "wrong, I asked about the decoder" in followups
    # Run 782: a claimed number the cited text does not show is uncertain.
    assert "wrong, I think the paper says 16 heads" in followups
    # Runs 823/825/827: the whole cited passage, not a 200-char head.
    full = [c for c in cases if c.case_id.endswith("-full-passage")]
    assert len(full) == 3
    assert all(len(c.variables["trace_summary"]) > 2000 for c in full)
    # No control characters from a lost backslash (\begin -> backspace + "egin").
    assert not any(ch < " " and ch != "\n" for c in cases
                   for ch in c.variables["trace_summary"])
    # Every case reaches the reviewer only because the keyword trigger fired.
    assert all(matches_trigger(f, DEFAULT_KEYWORDS) for f in followups)


@pytest.mark.parametrize("version", [f"v{i}" for i in range(1, 23)])
def test_every_variant_renders_every_slot_in_tags(version):
    assert version in list_variants("feedback_review", prompts_dir=PROMPTS)
    system, user = load_variant("feedback_review", version, prompts_dir=PROMPTS)
    for c in load_corpus(CORPUS):
        content = render_messages(system, user, c.variables)[-1]["content"]
        for slot in SLOTS:
            assert f"<{slot}>\n{c.variables[slot]}\n</{slot}>" in content


def test_shipped_prompt_matches_the_eval_winner():
    slot = PromptRegistry().get("feedback_review/v3")
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
    user = PromptRegistry().get("feedback_review/v3").user_template
    examples = user.split("</examples>")[0]
    corpus_text = CORPUS.read_text(encoding="utf-8")
    for probe in ("incorrect, the paper says 1000", "違うデータセット", "好像錯了",
                  "DDPM", "ResNet", "batch size of 256"):
        assert probe in examples and probe not in corpus_text


def test_shipped_prompt_quotes_no_corpus_followup():
    slot = PromptRegistry().get("feedback_review/v3")
    prompt = slot.system + slot.user_template
    for c in load_corpus(CORPUS):
        assert c.variables["user_followup"] not in prompt


# --- fork reviewer (feedback_review_fork) ----------------------------------

FORK_CORPUS = BACKEND / "benchmark/agent/corpus/feedback_review.fork.jsonl"
FORK_WINNER = "v15"  # see benchmark/agent/prompts/feedback_review_fork/README.md
FORK_SLOTS = ("question", "answer", "trace_summary", "reasons")


def test_fork_corpus_has_no_followup_and_both_verdicts():
    cases = load_corpus(FORK_CORPUS)
    assert len(cases) == 18
    assert all(c.variables["user_followup"] == "" for c in cases)
    assert Counter(c.expect["verdict"] for c in cases) == {"confirmed": 7, "uncertain": 11}
    # Runs 825/827 and two wrong answers under the same full passage.
    assert sum(c.case_id.endswith("-full-passage") for c in cases) == 4
    assert {r for c in cases for r in c.expect["reasons"]} == {
        "wrong_source", "factual", "language_format", "wrong_route", "citation"}


def test_fork_stage_shares_the_review_contract():
    fork, keyword = get_stage("feedback_review_fork"), get_stage("feedback_review")
    assert fork.response_model is ReviewVerdict
    assert (fork.variables_from_args, fork.output_summary, fork.deterministic_score) == (
        keyword.variables_from_args, keyword.output_summary, keyword.deterministic_score)


@pytest.mark.parametrize("version", [f"v{i}" for i in range(1, 16)])
def test_every_fork_variant_renders_its_slots_in_tags(version):
    system, user = load_variant("feedback_review_fork", version, prompts_dir=PROMPTS)
    for c in load_corpus(FORK_CORPUS):
        content = render_messages(system, user, c.variables)[-1]["content"]
        for slot in FORK_SLOTS:
            assert f"<{slot}>\n{c.variables[slot]}\n</{slot}>" in content


def test_shipped_fork_prompt_matches_the_eval_winner():
    slot = PromptRegistry().get("feedback_review_fork/v3")
    assert (slot.system, slot.user_template) == load_variant(
        "feedback_review_fork", FORK_WINNER, prompts_dir=PROMPTS)
    # The review passes the keyword variables; the fork prompt ignores user_followup.
    rendered = slot.user_template.format(
        question="q", answer="a", user_followup="", trace_summary="t",
        reasons=format_reasons_block())
    assert "<question>\nq\n</question>" in rendered
    assert "user_followup" not in slot.system + slot.user_template


def test_shipped_fork_examples_do_not_overlap_the_corpus():
    user = PromptRegistry().get("feedback_review_fork/v3").user_template
    examples = user.split("</examples>")[0]
    corpus_text = FORK_CORPUS.read_text(encoding="utf-8")
    for probe in ("ViT-B/16", "CLIP", "400 million"):
        assert probe in examples and probe not in corpus_text


def test_review_stages_replay_under_the_deploy_output_cap():
    """The flagship reviewer spends ~150-950 reasoning tokens of the cap
    (fork-ja-thin-evidence: 959 of 1024), so the eval must replay under the
    deploy cap or it hides truncated verdicts."""
    from paperhub.feedback.review import REVIEW_MAX_TOKENS

    assert REVIEW_MAX_TOKENS >= 4096
    for key in ("feedback_review", "feedback_review_fork"):
        assert get_stage(key).max_tokens == REVIEW_MAX_TOKENS
