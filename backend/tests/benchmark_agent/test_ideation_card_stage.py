"""Plan H3 Task 3: the ``ideation_card`` eval stage, its corpus and the
shipped-equals-winner pin."""
from pathlib import Path
from xml.sax.saxutils import quoteattr

import pytest

from benchmark.agent.corpus import load_corpus
from benchmark.agent.prompts import list_variants, load_variant
from benchmark.agent.replay import render_messages
from benchmark.agent.stages import card_failures, domain_failures, get_stage
from paperhub.ideation.cards import CardDraft
from paperhub.llm.prompts.registry import PromptRegistry

BACKEND = Path(__file__).resolve().parents[2]
CORPUS = BACKEND / "benchmark/agent/corpus/ideation_card.core.jsonl"
PROMPTS = BACKEND / "benchmark/agent/prompts"
WINNER = "v7"  # see benchmark/agent/prompts/ideation_card/README.md (round 3)
EVAL_DOCS = {68, 32, 47, 42, 87, 94, 22, 61}

_GOOD = {
    "problem": {"text": "p", "sections": ["Introduction"]},
    "mechanism": {"text": "m", "sections": ["method"]},
    "assumptions": [], "limitations": [], "domains": ["Robotics"],
}


def test_card_spec_scores_the_contract() -> None:
    spec = get_stage("ideation_card")
    assert spec.response_model is CardDraft
    expect = {"sections": ["Introduction", "Method"]}
    out = spec.output_summary(CardDraft.model_validate(_GOOD))
    assert spec.deterministic_score(expect, out) == 1.0  # case/space tolerant
    bad = {**_GOOD, "mechanism": {"text": "m", "sections": ["Made Up"]}, "domains": []}
    assert card_failures(expect, bad) == [
        "mechanism cites unknown section 'Made Up'", "no domain"]
    assert card_failures(expect, {**_GOOD, "problem": {"text": "p", "sections": []}}) == [
        "problem cites no section"]
    assert spec.deterministic_score({}, out) is None


def test_corpus_is_the_eight_eval_documents() -> None:
    cases = load_corpus(CORPUS)
    assert {c.expect["paper_content_id"] for c in cases} == EVAL_DOCS
    for c in cases:
        assert c.stage == "ideation_card"
        assert set(c.variables) == {"title", "abstract", "sections_xml"}
        for name in c.expect["sections"]:
            assert f"<section name={quoteattr(name)}>" in c.variables["sections_xml"]


@pytest.mark.parametrize("version", ["v1", "v2", "v3", "v4", "v5", "v6", "v7"])
def test_every_variant_renders_every_slot_in_tags(version: str) -> None:
    assert version in list_variants("ideation_card", prompts_dir=PROMPTS)
    system, user = load_variant("ideation_card", version, prompts_dir=PROMPTS)
    for c in load_corpus(CORPUS):
        content = render_messages(system, user, c.variables)[-1]["content"]
        assert f"<title>{c.variables['title']}</title>" in content
        assert f"<abstract>\n{c.variables['abstract']}\n</abstract>" in content
        assert c.variables["sections_xml"] in content


def test_shipped_prompt_matches_the_eval_winner() -> None:
    slot = PromptRegistry().get("ideation_card/v2")
    assert (slot.system, slot.user_template) == load_variant(
        "ideation_card", WINNER, prompts_dir=PROMPTS)


def test_shipped_example_does_not_overlap_the_corpus() -> None:
    user = PromptRegistry().get("ideation_card/v2").user_template
    example = user.split("<title>")[0]
    corpus_text = CORPUS.read_text(encoding="utf-8")
    for probe in ("Deep Residual Learning", "ResNet", "Scaling Laws for Neural Language Models"):
        assert probe in example and probe not in corpus_text


def test_domain_labels_grade_the_stored_tags() -> None:
    """QA I8: graded on what assign_domains stores (canonical, capped)."""
    score = get_stage("ideation_card").deterministic_score
    expect = {"sections": ["Introduction", "Method"],
              "primary_domain": ["Machine Learning", "Natural Language Processing"],
              "allowed_domains": ["Machine Learning", "Natural Language Processing"]}
    ok = {**_GOOD, "domains": ["Deep Learning", "NLP", "Genomics"]}  # 3rd is capped off
    assert domain_failures(expect, ok) == [] and score(expect, ok) == 1.0
    bad = {**_GOOD, "domains": ["NLP", "Genomics"]}
    assert domain_failures(expect, bad) == ["domain 'Computational Biology' not allowed"]
    assert score(expect, bad) == 0.0
    wrong_primary = {**_GOOD, "domains": ["Reinforcement Learning", "NLP"]}
    assert domain_failures(expect, wrong_primary)[0].startswith("primary domain")


def test_domain_corpus_labels_canonical_fields() -> None:
    from paperhub.ideation.domains import display_domain

    cases = load_corpus(BACKEND / "benchmark/agent/corpus/ideation_card.domains.jsonl")
    assert len(cases) == 16
    for c in cases:
        for name in [*c.expect["primary_domain"], *c.expect["allowed_domains"]]:
            assert display_domain(name) == name
