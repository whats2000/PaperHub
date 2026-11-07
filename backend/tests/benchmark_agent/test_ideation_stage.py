from pathlib import Path

import pytest

from benchmark.agent import grade
from benchmark.agent.corpus import CorpusCase, load_corpus
from benchmark.agent.replay_types import ReplayOutput
from benchmark.agent.stages import CARDS_FROM_DB, get_stage
from paperhub.ideation.models import IdeationOutput, ProposeDraft

BACKEND = Path(__file__).resolve().parents[2]
CORPUS = BACKEND / "benchmark/agent/corpus/ideation.core.jsonl"
EXPLORE = BACKEND / "benchmark/agent/corpus/ideation.explore.jsonl"
EXPLORE_BOARD = BACKEND / "benchmark/agent/corpus/ideation.explore_board.jsonl"
VARIABLES = {"problem", "tried", "cards_xml", "rejected_xml", "mode"}


def _ideas(*pairs: tuple[int, int | None]) -> dict:
    return {"ideas": [
        {"source_doc": s, "source_section": "Method", "target_doc": t,
         "target_section": "Intro" if t is not None else None,
         "title": f"idea {i}", "rationale": "r"}
        for i, (s, t) in enumerate(pairs)]}


def test_ideation_spec_parses_and_summarizes():
    spec = get_stage("ideation")
    assert spec.response_model is ProposeDraft  # the deploy call's schema
    assert (spec.trace_agent, spec.trace_tool) == ("ideation", "ideation:propose")
    parsed = IdeationOutput.model_validate(_ideas((3, None)))
    out = spec.output_summary(parsed)
    assert out == {"ideas": [{"source_doc": 3, "source_section": "Method", "target_doc": None,
                              "target_section": None, "title": "idea 0", "rationale": "r"}],
                   "no_fit_reason": ""}


def test_ideation_output_keeps_the_best_eight_ideas():
    out = IdeationOutput.model_validate(_ideas(*[(i, None) for i in range(9)]))
    assert [i.source_doc for i in out.ideas] == list(range(8))  # truncated, not rejected
    assert IdeationOutput.model_json_schema()["properties"]["ideas"]["maxItems"] == 8


def test_recall_counts_the_source_at_position_five_not_six():
    score = get_stage("ideation").deterministic_score
    fifth = _ideas((1, None), (2, None), (3, None), (4, None), (68, None), (6, None))
    sixth = _ideas((1, None), (2, None), (3, None), (4, None), (5, None), (68, None))
    assert score({"source_doc": 68}, fifth) == 1.0
    assert score({"source_doc": 68}, sixth) == 0.0


def test_recall_accepts_any_listed_source():
    score = get_stage("ideation").deterministic_score
    assert score({"source_doc": [32, 36]}, _ideas((36, None))) == 1.0
    assert score({"source_doc": [32, 36]}, _ideas((31, None))) == 0.0


def test_explore_recall_needs_the_ordered_pair_in_the_top_five():
    score = get_stage("ideation").deterministic_score
    expect = {"source_doc": 87, "target_doc": 63}
    assert score(expect, _ideas((1, 2), (87, 63))) == 1.0
    assert score(expect, _ideas((87, 61), (63, 87))) == 0.0  # right ends, wrong pairing
    late = _ideas((1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (87, 63))
    assert score(expect, late) == 0.0


def test_unlabelled_expect_scores_none():
    score = get_stage("ideation").deterministic_score
    assert score({}, _ideas((68, None))) is None
    # metadata without a source label (an unresolved case) is still unlabelled
    assert score({"problem_doc": 62, "exclude_docs": [62]}, _ideas((68, None))) is None


def test_variables_are_exactly_what_generate_ideas_records():
    v = get_stage("ideation").variables_from_args({"problem": "p", "mode": "problem", "x": 1})
    assert v == {"problem": "p", "tried": "", "cards_xml": "", "rejected_xml": "",
                 "existing_xml": "", "mode": "problem", "response_language": ""}


def test_corpus_has_ten_cases_from_srs_iii_10_3():
    cases = load_corpus(CORPUS)
    assert len(cases) == 10
    assert len({c.case_id for c in cases}) == 10
    for c in cases:
        assert c.stage == "ideation"
        assert set(c.variables) == VARIABLES
        assert c.variables["mode"] == "problem" and c.variables["problem"].strip()
        assert c.variables["cards_xml"] == CARDS_FROM_DB
        if c.expect.get("problem_doc") is not None:
            # the target paper never sees its own card, or the answer leaks
            assert c.expect["problem_doc"] in c.expect["exclude_docs"]
    labelled = {c.case_id: c.expect["source_doc"] for c in cases if "source_doc" in c.expect}
    assert len(labelled) == 10
    assert labelled["tokenization-to-faster"] == 94  # BPE subword units (Ruling Q3)
    assert labelled["user-anchor-reranker"] == 92  # Faster R-CNN
    assert labelled["user-icl-vla-prompts"] == 93  # GPT-3
    assert labelled["bert-masking-to-ijepa"] == 68


@pytest.mark.asyncio
async def test_judge_request_falls_back_to_the_problem_text():
    seen: dict = {}

    async def _judge(**kw):
        seen.update(kw)
        return 0.5, "ok"

    case = CorpusCase(case_id="c", stage="ideation", expect={},
                      variables={"problem": "slow reranker", "tried": "", "cards_xml": "",
                                 "rejected_xml": "", "mode": "problem"})
    s = await grade.score_case(get_stage("ideation"), case,
                               ReplayOutput(output=_ideas((1, None)), tokens_in=5), 0,
                               judge_model="m", judge_fn=_judge)
    assert s.score == 0.5 and seen["request"] == "slow reranker"


def test_eval_config_sweeps_the_card_filled_corpus():
    from benchmark.agent.eval_config import load_eval_config

    cfg = load_eval_config(BACKEND / "benchmark/agent/ideation.eval.toml")
    assert cfg.stage == "ideation"
    names = {t.name: str(t.corpus).replace("\\", "/") for t in cfg.testsets}
    assert names["core"].endswith("corpus/ideation.core.cards.jsonl")
    assert names["explore"].endswith("corpus/ideation.explore.cards.jsonl")
    assert names["explore_board"].endswith("corpus/ideation.explore_board.cards.jsonl")
    assert cfg.model == "gemini/gemini-pro-latest"  # the deploy flagship (settings)


def test_explore_bucket_is_the_whole_library_graded_on_yield():
    cases = load_corpus(EXPLORE)
    assert [c.case_id for c in cases] == ["explore-full-library"]
    c = cases[0]
    assert set(c.variables) == VARIABLES
    assert c.variables["mode"] == "explore" and c.variables["cards_xml"] == CARDS_FROM_DB
    assert c.expect == {"min_valid": 5}  # the acceptance bar for one explore run


def test_explore_board_bucket_has_nine_labelled_pairs_on_small_boards():
    score = get_stage("ideation").deterministic_score
    cases = load_corpus(EXPLORE_BOARD)
    assert len(cases) == 9
    for c in cases:
        assert set(c.variables) == VARIABLES
        assert c.variables["mode"] == "explore" and c.variables["cards_xml"] == CARDS_FROM_DB
        src, tgt, board = c.expect["source_doc"], c.expect["target_doc"], c.expect["board_docs"]
        first = src[0] if isinstance(src, list) else src
        assert {*(src if isinstance(src, list) else [src]), tgt} <= set(board)
        assert 11 <= len(board) <= 12
        assert 88 not in board  # the boards predate 88 becoming citable ("Full text")
        assert score(c.expect, _ideas((first, tgt))) == 1.0
        assert score(c.expect, _ideas((first, None))) == 0.0

def test_ideation_replays_under_the_deploy_output_cap():
    from paperhub.ideation.agent import PROPOSE_MAX_TOKENS

    assert get_stage("ideation").max_tokens == PROPOSE_MAX_TOKENS


def _board(**docs: list[str]) -> dict:
    """``board`` as the build step writes it: doc id -> domains + cited sections."""
    return {k.lstrip("d"): {"domains": v, "sections": ["Method", "Intro"]}
            for k, v in docs.items()}


BOARD = _board(d1=["Robotics"], d2=["Robotics", "Computer Vision"],
               d3=["Natural Language Processing"], d4=["Computer Vision"])


def test_with_a_board_only_ideas_the_agent_keeps_take_top_five_slots():
    score = get_stage("ideation").deterministic_score
    expect = {"source_doc": 3, "target_doc": 2, "board": BOARD}
    # five same-field pairs (2's primary, Robotics, is also 1's) are dropped in prod
    late = _ideas(*[(1, 2)] * 5, (3, 2))
    assert score(expect, late) == 1.0
    assert score({k: v for k, v in expect.items() if k != "board"}, late) == 0.0
    # the expected pair itself must survive validation
    bad = _ideas((3, 2))
    bad["ideas"][0]["source_section"] = "Not Cited"
    assert score(expect, bad) == 0.0
    unknown = _ideas((3, 99))
    assert score({"source_doc": 3, "target_doc": 99, "board": BOARD}, unknown) == 0.0


def test_yield_scores_kept_ideas_toward_min_valid():
    score = get_stage("ideation").deterministic_score
    expect = {"min_valid": 5, "board": BOARD}
    three = _ideas((3, 2), (3, 1), (4, 1), (1, 2), (2, 4), (3, 2))  # 2 dropped, 1 duplicate
    assert score(expect, three) == 0.6
    six = _ideas((3, 2), (3, 1), (4, 1), (4, 3), (1, 3), (1, 4))
    assert score(expect, six) == 1.0
    assert score(expect, {"ideas": []}) == 0.0


def test_problem_mode_yield_keeps_ideas_without_a_target():
    """A problem-mode repair case is graded on yield: its ideas have no
    target, and one with a target is the invalid kind the repair fixes."""
    score = get_stage("ideation").deterministic_score
    expect = {"min_valid": 2, "mode": "problem", "board": BOARD}
    assert score(expect, _ideas((3, None), (4, None))) == 1.0
    assert score(expect, _ideas((3, 2), (4, None))) == 0.5


def test_language_check() -> None:
    from benchmark.agent.stages import in_language

    assert in_language("Score readings against a decaying baseline", "en")
    assert in_language("借用 Faster R-CNN 的錨框，對每個候選產生位置假設", "zh-TW")
    assert not in_language("借用 Faster R-CNN 的锚框，对每个候选产生位置假设", "zh-TW")
    assert in_language("借用 Faster R-CNN 的锚框，对每个候选产生位置假设", "zh-CN")
    assert not in_language("Borrow anchor boxes from Faster R-CNN", "zh-TW")


def test_qa_scores_no_ideas_language_and_named_sources() -> None:
    score = get_stage("ideation").deterministic_score
    board = {"92": {"domains": ["Computer Vision"], "sections": ["Method"],
                    "title": "Faster R-CNN: Towards Real-Time Object Detection"}}
    # I2: nothing relevant -> no ideas plus a reason
    assert score({"no_ideas": True, "board": board},
                 {"ideas": [], "no_fit_reason": "Nothing applies."}) == 1.0
    assert score({"no_ideas": True, "board": board},
                 {"ideas": [], "no_fit_reason": ""}) == 0.0
    assert score({"no_ideas": True, "board": board}, _ideas((92, None))) == 0.0
    # I1 + I3: recall x language x named source
    named = {"ideas": [{"source_doc": 92, "source_section": "Method", "target_doc": None,
                        "target_section": None, "title": "用錨框產生位置假設",
                        "rationale": "借用 Faster R-CNN 的錨框機制，對每個候選產生多尺度位置假設。"}]}
    expect = {"source_doc": 92, "language": "zh-TW", "names_source": True, "board": board}
    assert score(expect, named) == 1.0
    english = _ideas((92, None))
    assert score(expect, english) == 0.0
