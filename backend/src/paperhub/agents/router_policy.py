"""Deterministic post-processing of the router's decision.

The router LLM names its pick (``intent``) plus every other intent the message
also asks for (``also_requested``). Choosing between them is a fixed rule, so
it is applied here instead of trusting a small model to follow it: a compound
"give me an overview, then build a deck" went to ``paper_qa`` in 1-4 of 15
reps although the prompt states ``slides > paper_qa``.
"""
from __future__ import annotations

import re
from typing import Any

from paperhub.models.domain import Intent, RoutingDecision

INTENT_PRIORITY: tuple[Intent, ...] = (
    "memory", "slides", "paper_qa", "library_stats",
    "paper_search", "paper_suggest", "chitchat", "clarify",
)
_RANK = {intent: i for i, intent in enumerate(INTENT_PRIORITY)}


def _eligible(intent: Intent, *, enabled_refs_count: int, slide_attached: bool) -> bool:
    # The prompt's overrides decide these; promotion must never undo them.
    if intent in ("slides", "paper_qa") and enabled_refs_count == 0:
        return False  # Override A: 0 refs -> search/suggest/clarify first
    if intent == "slides" and slide_attached:
        return False  # Override B: a question about the attached slide
    return intent != "clarify"


def apply_intent_priority(
    decision: RoutingDecision, *,
    enabled_refs_count: int, slide_attached: bool,
) -> tuple[RoutingDecision, dict[str, Any] | None]:
    """Promote ``intent`` to the highest-priority eligible ``also_requested``
    intent. Returns the (possibly new) decision and a trace note, or ``None``
    when nothing changed. Never demotes, and never moves off ``clarify``
    (its ``resolved_query`` is a question, not a task)."""
    if decision.intent == "clarify":
        return decision, None
    best: Intent = decision.intent
    for cand in decision.also_requested:
        if _RANK[cand] < _RANK[best] and _eligible(
            cand, enabled_refs_count=enabled_refs_count, slide_attached=slide_attached,
        ):
            best = cand
    if best == decision.intent:
        return decision, None
    # resolved_query was written for the losing intent; "" makes downstream
    # agents read the raw message, which carries the whole request.
    promoted = decision.model_copy(update={"intent": best, "resolved_query": ""})
    return promoted, {"llm_intent": decision.intent, "promoted_to": best}


# CJK ideographs, kana, hangul: scripts a translated rewrite loses entirely.
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿]")


# High-frequency Simplified/Traditional pairs whose two forms differ and are
# not used for the other script (identical forms such as 篇 are left out).
_SIMP_TRAD = (
    "这這个個问問么麼们們说說时時来來对對论論读讀书書题題几幾会會过過还還"
    "关關于於与與为為发發现現实實应應产產总總简簡报報话話吗嗎请請帮幫"
)
_SIMP = frozenset(_SIMP_TRAD[0::2])
_TRAD = frozenset(_SIMP_TRAD[1::2])
_KANA = re.compile("[぀-ヿ]")


def _chinese_script(text: str) -> str | None:
    """'simplified' / 'traditional' when the text clearly uses one script
    (script-exclusive characters of it and none of the other), else None."""
    chars = set(text)
    simp, trad = bool(chars & _SIMP), bool(chars & _TRAD)
    if simp and not trad:
        return "simplified"
    if trad and not simp:
        return "traditional"
    return None


def query_language_drift(decision: RoutingDecision, *, user_message: str) -> str | None:
    """How a rewrite left the user's language, for the trace: ``"translated"``
    (a CJK/kana/hangul message rewritten in a Latin-script language),
    ``"script_switch"`` (Chinese Simplified <-> Traditional) or None.

    Detection only: the rewrite is kept. ``resolved_query`` carries the
    context-resolved request (a bare "推薦幾篇" becomes a full query, SRS
    v2.11) and downstream agents answer in ``response_language``, so restoring
    the raw message would drop the resolution to fix a cosmetic slip."""
    rq = decision.resolved_query
    if decision.intent == "clarify" or not rq:
        return None  # a question for the user
    if _CJK.search(user_message) and not _CJK.search(rq):
        return "translated"
    if not _KANA.search(user_message):  # kanji overlap Traditional forms
        msg_script, rq_script = _chinese_script(user_message), _chinese_script(rq)
        if msg_script and rq_script and msg_script != rq_script:
            return "script_switch"
    return None


_WHICH_PAPER = {
    "Traditional Chinese": "你指的是哪一篇論文？請先加入論文，或告訴我它的標題。",
    "Simplified Chinese": "你指的是哪一篇论文？请先添加论文，或告诉我它的标题。",
    "Japanese": "どの論文のことでしょうか？論文を追加するか、タイトルを教えてください。",
}
_WHICH_PAPER_EN = "Which paper do you mean? Attach it first, or tell me its title."


def require_subject(
    decision: RoutingDecision, *, enabled_refs_count: int,
) -> tuple[RoutingDecision, dict[str, Any] | None]:
    """With no papers attached, ``paper_search`` / ``paper_suggest`` need a
    named paper or topic to look for. When the router reports none ("this
    paper" points at nothing), ask which paper instead of running a search
    on an empty request (run 837)."""
    if (
        enabled_refs_count > 0
        or decision.intent not in ("paper_search", "paper_suggest")
        or decision.subject is None
        or decision.subject.strip()
    ):
        return decision, None
    question = _WHICH_PAPER.get(decision.response_language, _WHICH_PAPER_EN)
    demoted = decision.model_copy(update={"intent": "clarify", "resolved_query": question})
    return demoted, {"llm_intent": decision.intent, "demoted_to": "clarify",
                     "reason": "no paper or topic named"}


def apply_router_policy(
    decision: RoutingDecision, *, user_message: str,
    enabled_refs_count: int, slide_attached: bool,
) -> tuple[RoutingDecision, dict[str, Any]]:
    """Every deterministic rule, in order; the router node and the eval both
    call this. Returns the final decision and the trace notes of the rules
    that changed it (``priority`` / ``subject_rule``)."""
    notes: dict[str, Any] = {}
    decision, priority = apply_intent_priority(
        decision, enabled_refs_count=enabled_refs_count, slide_attached=slide_attached,
    )
    if priority is not None:
        notes["priority"] = priority
    decision, subject_rule = require_subject(decision, enabled_refs_count=enabled_refs_count)
    if subject_rule is not None:
        notes["subject_rule"] = subject_rule
    drift = query_language_drift(decision, user_message=user_message)
    if drift is not None:
        notes["query_language_drift"] = drift
    return decision, notes
