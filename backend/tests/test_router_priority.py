"""Deterministic multi-intent priority + query-language guard (router policy)."""
import json

import aiosqlite

from paperhub.agents.router import router_node
from paperhub.agents.router_policy import (
    apply_intent_priority,
    apply_router_policy,
    query_language_drift,
    require_subject,
)
from paperhub.agents.state import AgentState
from paperhub.llm.litellm_adapter import LiteLlmAdapter
from paperhub.models.domain import RoutingDecision
from paperhub.tracing.tracer import Tracer

MSG = "give me an overview of this paper, then build a deck"


def _d(intent: str, also: list[str], query: str = "q") -> RoutingDecision:
    return RoutingDecision.model_validate({
        "intent": intent, "also_requested": also, "confidence": 0.9,
        "reasoning": "r", "resolved_query": query, "response_language": "English",
    })


def test_slides_beats_paper_qa_when_refs_present() -> None:
    d, note = apply_intent_priority(
        _d("paper_qa", ["slides"], "Give me an overview of the enabled paper."),
        enabled_refs_count=1, slide_attached=False)
    assert d.intent == "slides"
    # the rewrite covered only the dropped half, so fall back to the message
    assert d.resolved_query == ""
    assert note == {"llm_intent": "paper_qa", "promoted_to": "slides"}


def test_memory_beats_everything() -> None:
    d, note = apply_intent_priority(
        _d("paper_suggest", ["memory"]), enabled_refs_count=0, slide_attached=False)
    assert d.intent == "memory" and note is not None


def test_no_promotion_to_slides_or_qa_without_refs() -> None:
    """Override A: with 0 refs slides/paper_qa are redirected, never restored."""
    for also in (["slides"], ["paper_qa"]):
        d, note = apply_intent_priority(
            _d("paper_search", also, "Find X."), enabled_refs_count=0, slide_attached=False)
        assert d.intent == "paper_search" and d.resolved_query == "Find X."
        assert note is None


def test_no_promotion_to_slides_when_slide_attached() -> None:
    """Override B: a question about the attached slide stays paper_qa."""
    d, note = apply_intent_priority(
        _d("paper_qa", ["slides"]), enabled_refs_count=1, slide_attached=True)
    assert d.intent == "paper_qa" and note is None


def test_never_demotes_and_never_leaves_clarify() -> None:
    d, _ = apply_intent_priority(_d("slides", ["paper_qa"]), enabled_refs_count=1, slide_attached=False)
    assert d.intent == "slides" and d.resolved_query == "q"
    d, note = apply_intent_priority(_d("clarify", ["paper_suggest"]),
                                    enabled_refs_count=0,
                                    slide_attached=False)
    assert d.intent == "clarify" and note is None


def test_language_drift_is_detected_not_undone() -> None:
    d = _d("chitchat", [], "Thank you for the help!")
    assert query_language_drift(d, user_message="ありがとう、助かりました！") == "translated"
    assert d.resolved_query == "Thank you for the help!"


def test_policy_keeps_a_resolved_rewrite_in_another_language() -> None:
    """SRS v2.11: a bare follow-up is resolved into a full query; dropping the
    rewrite for language reasons would lose the resolution."""
    d = _d("paper_search", [], "recommend discrete diffusion distillation papers")
    out, notes = apply_router_policy(
        d, user_message="推薦幾篇", enabled_refs_count=0, slide_attached=False,
    )
    assert out.resolved_query == "recommend discrete diffusion distillation papers"
    assert notes["query_language_drift"] == "translated"


def test_query_language_guard_keeps_same_script_rewrites() -> None:
    zh = _d("paper_qa", [], "Paper X 是由哪個研究團隊完成的？")
    assert query_language_drift(zh, user_message="他是哪個團隊做的？") is None
    en = _d("paper_search", [], "Find the Mamba paper.")
    assert query_language_drift(en, user_message="find mamba") is None


async def test_router_node_applies_priority_and_records_it(
    migrated_db: aiosqlite.Connection,
) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.execute(
        "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
        "authors_json, source_path, source_dir_path, html_path) "
        "VALUES (1, 'arxiv:1', 'arxiv', '1', 't', '[]', '/s.tex', '/', '/s.html')")
    await migrated_db.execute(
        "INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (1, 1, 1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {"run_id": 1, "branch": "", "session_id": 1, "user_message": MSG}
    updated = await router_node(
        state, adapter=LiteLlmAdapter(), tracer=tracer, model="gpt-4o-mini",
        mock_response=json.dumps({
            "intent": "paper_qa", "also_requested": ["slides"], "confidence": 0.85,
            "reasoning": "overview", "resolved_query": "Give me an overview.",
            "response_language": "English"}),
    )
    assert updated["routing_decision"].intent == "slides"
    assert updated["effective_query"] == MSG
    async with migrated_db.execute(
        "SELECT result_summary_json FROM tool_calls WHERE run_id = 1 AND tool = 'classify'"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    rec = json.loads(row[0])
    assert rec["intent"] == "slides"
    assert rec["priority"] == {"llm_intent": "paper_qa", "promoted_to": "slides"}


def _s(intent: str, subject: str | None, lang: str = "English") -> RoutingDecision:
    return RoutingDecision.model_validate({
        "intent": intent, "subject": subject, "confidence": 0.9, "reasoning": "r",
        "resolved_query": "Find this paper.", "response_language": lang,
    })


def test_search_without_a_subject_and_no_refs_becomes_clarify() -> None:
    """Run 837: "give me an overview of this paper, then build a deck" with
    0 refs went to paper_search although nothing was named."""
    for intent in ("paper_search", "paper_suggest"):
        d, note = require_subject(_s(intent, ""), enabled_refs_count=0)
        assert d.intent == "clarify"
        assert "which paper" in d.resolved_query.lower()
        assert note == {"llm_intent": intent, "demoted_to": "clarify",
                        "reason": "no paper or topic named"}


def test_clarify_question_follows_the_response_language() -> None:
    d, _ = require_subject(_s("paper_search", " ", "Japanese"), enabled_refs_count=0)
    assert "論文" in d.resolved_query
    d, _ = require_subject(_s("paper_search", "", "Traditional Chinese"), enabled_refs_count=0)
    assert "論文" in d.resolved_query and "论" not in d.resolved_query
    d, _ = require_subject(_s("paper_search", "", "Simplified Chinese"), enabled_refs_count=0)
    assert "论文" in d.resolved_query


def test_subject_rule_keeps_named_unknown_and_refs_cases() -> None:
    named = _s("paper_search", "Attention Is All You Need")
    assert require_subject(named, enabled_refs_count=0) == (named, None)
    unknown = _s("paper_search", None)  # a prompt that does not ask for it
    assert require_subject(unknown, enabled_refs_count=0) == (unknown, None)
    with_refs = _s("paper_suggest", "")
    assert require_subject(with_refs, enabled_refs_count=2) == (with_refs, None)
    qa = _s("paper_qa", "")
    assert require_subject(qa, enabled_refs_count=0) == (qa, None)


async def test_router_node_demotes_an_empty_search_and_records_it(
    migrated_db: aiosqlite.Connection,
) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {"run_id": 1, "branch": "", "session_id": 1, "user_message": MSG}
    updated = await router_node(
        state, adapter=LiteLlmAdapter(), tracer=tracer, model="gpt-4o-mini",
        mock_response=json.dumps({
            "intent": "paper_search", "also_requested": ["paper_qa", "slides"],
            "subject": "", "confidence": 0.85, "reasoning": "0 refs",
            "resolved_query": MSG, "response_language": "English"}),
    )
    assert updated["routing_decision"].intent == "clarify"
    async with migrated_db.execute(
        "SELECT result_summary_json FROM tool_calls WHERE run_id = 1 AND tool = 'classify'"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert json.loads(row[0])["subject_rule"]["demoted_to"] == "clarify"


def test_query_language_guard_never_echoes_the_message_as_a_question() -> None:
    """A clarify question is shown to the user; echoing their message back
    would be worse than an English question."""
    d = _d("clarify", [], "Which paper do you mean?")
    assert query_language_drift(d, user_message="이 논문 요약해줘") is None


def test_script_switch_simplified_to_traditional_is_detected() -> None:
    msg = "这周我问了你多少个问题？"
    d = _d("library_stats", [], "這周我問了你多少個問題？")
    assert query_language_drift(d, user_message=msg) == "script_switch"


def test_script_switch_traditional_to_simplified_is_detected() -> None:
    msg = "我總共問過你幾次問題？"
    d = _d("library_stats", [], "我总共问过你几次问题？")
    assert query_language_drift(d, user_message=msg) == "script_switch"


def test_script_guard_leaves_japanese_alone() -> None:
    """Kanji overlap with Traditional forms; kana marks the message Japanese."""
    d = _d("paper_qa", [], "この論文の結論は何ですか？說明して")
    assert query_language_drift(d, user_message="この論文の結論は？") is None
    # 会 is the Simplified form in the table, 會 the Traditional one: a
    # script check without the kana skip would restore this rewrite.
    kanji = _d("paper_qa", [], "この會議の発表について")
    assert query_language_drift(kanji, user_message="この会議の発表について") is None


def test_script_guard_leaves_mixed_or_neutral_text_alone() -> None:
    neutral = _d("paper_qa", [], "這篇論文的方法是什麼？")
    # the message has no script-exclusive character: nothing to compare
    assert query_language_drift(neutral, user_message="方法") is None
    mixed = _d("paper_qa", [], "這个方法是什麼？")  # both scripts in the rewrite
    assert query_language_drift(mixed, user_message="这个方法是什么？") is None
    same = _d("paper_qa", [], "这篇论文的方法是什么？")
    assert query_language_drift(same, user_message="这个方法是什么？") is None
