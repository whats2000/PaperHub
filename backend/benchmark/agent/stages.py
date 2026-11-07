"""Per-stage evaluation specs (SRS §III-9).

What a stage needs to be replayed + scored in isolation: how it appears in the
tool_calls trace, its structured output model, and three small callables. Plan G1
ships the router; downstream stages are added in G2. Read-only imports of the
production response model — NO deploy-code change.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from paperhub.agents.router_policy import apply_router_policy
from paperhub.feedback.models import ReviewVerdict, format_reasons_block
from paperhub.feedback.review import REVIEW_MAX_TOKENS
from paperhub.ideation.agent import PROPOSE_MAX_TOKENS, cross_domain_reason
from paperhub.ideation.cards import CardDraft
from paperhub.ideation.domains import stored_tags
from paperhub.ideation.models import IdeationOutput, ProposeDraft
from paperhub.models.domain import RoutingDecision
from paperhub.translation.models import TranslatedBlocks
from paperhub.translation.placeholders import br_position_intact, placeholders_intact


class RouterEvalOutput(BaseModel):
    """Eval-local router output — the graded fields only, **tolerant** of
    ``model_tier`` being present (``v1``/``v2a`` emit it) OR absent (``v3``
    drops it).

    Deliberately decoupled from the production ``RoutingDecision`` (which is
    ``extra="forbid"`` and still REQUIRES ``model_tier`` until Plan G2's deploy
    cleanup lands): a strict prod-schema parse would reject ``v3`` (missing
    field) before G2 and reject ``v1``/``v2a`` (extra field) after, so the two
    sides of the model_tier-removal boundary could never be swept head-to-head.
    Pydantic v2 ignores unknown fields by default; ``extra="ignore"`` is set
    explicitly so an emitted ``model_tier`` is dropped, not an error. Router
    grading is exact-intent-match, so the dropped field never affects a score.
    """

    model_config = ConfigDict(extra="ignore")
    intent: str
    also_requested: list[str] = []
    confidence: float = 0.0
    reasoning: str = ""
    resolved_query: str | None = None
    response_language: str | None = None
    subject: str | None = None  # last, like RoutingDecision


@dataclass(frozen=True)
class StageSpec:
    key: str
    trace_agent: str
    trace_tool: str
    response_model: type[BaseModel] | None
    variables_from_args: Callable[[dict[str, Any]], dict[str, Any]]
    output_summary: Callable[[Any], dict[str, Any]]
    deterministic_score: Callable[[dict[str, Any], dict[str, Any]], float | None]
    max_tokens: int | None = None
    """The deploy call's output cap, so the replay truncates where prod does."""
    finalize: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None
    """Deploy post-processing of the LLM output, given the case variables, so
    the score reflects what production acts on."""


def _router_variables(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_message": args["user_message"],
        "enabled_refs_count": args.get("enabled_refs_count", 0),
        "slide_attached": args.get("slide_attached", False),
    }


def _router_output(obj: Any) -> dict[str, Any]:
    d = obj if isinstance(obj, RouterEvalOutput) else RouterEvalOutput.model_validate(obj)
    return {"intent": d.intent, "resolved_query": d.resolved_query,
            "response_language": d.response_language, "confidence": d.confidence,
            "reasoning": d.reasoning, "also_requested": d.also_requested,
            "subject": d.subject}


def _router_finalize(variables: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    """Apply the router node's deterministic policy (agents.router_policy)."""
    try:
        decision = RoutingDecision.model_validate({
            k: output.get(k) for k in ("intent", "also_requested", "subject",
                                       "confidence", "reasoning")
        } | {"resolved_query": output.get("resolved_query") or "",
             "response_language": output.get("response_language") or ""})
    except ValueError:
        return output  # an invalid intent fails the deterministic score as is
    msg = str(variables.get("user_message", ""))
    final, notes = apply_router_policy(
        decision, user_message=msg,
        enabled_refs_count=int(variables.get("enabled_refs_count") or 0),
        slide_attached=bool(variables.get("slide_attached")))
    return {**output, **notes, "llm_intent": output.get("intent"), "intent": final.intent,
            # what downstream reads: effective_query = resolved_query or message
            "resolved_query": final.resolved_query or msg}


def _router_score(expect: dict[str, Any], output: dict[str, Any]) -> float | None:
    """Exact intent match; a follow-up case may also grade the rewrite the
    subagents read: each ``query_has`` group needs one of its terms in
    ``resolved_query`` and no ``query_lacks`` term may appear (case-insensitive)."""
    want = expect.get("intent")
    if want is None:
        return None
    if output.get("intent") != want:
        return 0.0
    query = str(output.get("resolved_query") or "").casefold()
    for group in expect.get("query_has") or []:
        terms = [group] if isinstance(group, str) else group
        if not any(str(t).casefold() in query for t in terms):
            return 0.0
    if any(str(t).casefold() in query for t in expect.get("query_lacks") or []):
        return 0.0
    return 1.0


ROUTER = StageSpec(
    key="router", trace_agent="router", trace_tool="classify",
    response_model=RouterEvalOutput, variables_from_args=_router_variables,
    output_summary=_router_output, deterministic_score=_router_score,
    finalize=_router_finalize,
)



# --- translate_blocks (Plan H1) -------------------------------------------
# Production has no traced translate step (a translation batch is a pipeline
# call, not a chat turn), so this stage is driven by hand-authored corpus cases
# only. ``expect.blocks`` maps each input key to its source text.

def _translate_variables(args: dict[str, Any]) -> dict[str, Any]:
    return {k: args.get(k, "") for k in ("language", "terminology", "context", "blocks_xml")}


def _translate_output(obj: Any) -> dict[str, Any]:
    d = obj if isinstance(obj, TranslatedBlocks) else TranslatedBlocks.model_validate(obj)
    return {"items": [{"key": i.key, "text": i.text} for i in d.items]}


def translate_failures(expect: dict[str, Any], output: dict[str, Any]) -> list[str]:
    """Deterministic contract violations (empty list = pass): the key list must
    equal the input key set with no duplicates; every item must keep its
    source's placeholders exactly and keep a mid-paragraph ``⟦BR⟧`` off the
    edges. Optional ``expect.require`` / ``expect.forbid`` map a key to terms
    that must / must not appear in its translation (terminology adherence)."""
    source: dict[str, str] = expect.get("blocks") or {}
    require: dict[str, list[str]] = expect.get("require") or {}
    forbid: dict[str, list[str]] = expect.get("forbid") or {}
    items = output.get("items") or []
    keys = [str(i.get("key")) for i in items]
    fails: list[str] = []
    if len(keys) != len(set(keys)):
        fails.append("duplicate keys")
    if set(keys) != set(source):
        fails.append(f"key set {sorted(set(keys))} != {sorted(source)}")
    for i in items:
        k = str(i.get("key"))
        text = str(i.get("text", ""))
        if k in source and not placeholders_intact(source[k], text):
            fails.append(f"placeholders broken in {k}")
        if k in source and not br_position_intact(source[k], text):
            fails.append(f"BR moved to an edge in {k}")
        fails += [f"missing term {t!r} in {k}" for t in require.get(k, []) if t not in text]
        fails += [f"forbidden term {t!r} in {k}" for t in forbid.get(k, []) if t in text]
    return fails


def _translate_score(expect: dict[str, Any], output: dict[str, Any]) -> float | None:
    if "blocks" not in expect:
        return None
    return 0.0 if translate_failures(expect, output) else 1.0


TRANSLATE_BLOCKS = StageSpec(
    key="translate_blocks", trace_agent="translation", trace_tool="translate_blocks",
    response_model=TranslatedBlocks, variables_from_args=_translate_variables,
    output_summary=_translate_output, deterministic_score=_translate_score,
)

# --- feedback_review (Plan H2) --------------------------------------------
# The reviewer that confirms / rejects a keyword-flagged answer. Driven by the
# hand-authored labelled corpus (``expect.verdict``); the deterministic grade is
# verdict accuracy, and the ``doubt`` sentence is judged with ``pairwise``.

_REVIEW_KEYS = ("question", "answer", "user_followup", "trace_summary", "reasons")


def _review_variables(args: dict[str, Any]) -> dict[str, Any]:
    """Corpus rows carry every key; a recorded ``feedback:review`` step omits
    ``reasons``, which is the static ``format_reasons_block()``."""
    out = {k: args.get(k, "") for k in _REVIEW_KEYS}
    if not out["reasons"]:
        out["reasons"] = format_reasons_block()
    if args.get("doubt_language"):  # recorded since feedback_review v4
        out["doubt_language"] = args["doubt_language"]
    return out


def _review_output(obj: Any) -> dict[str, Any]:
    d = obj if isinstance(obj, ReviewVerdict) else ReviewVerdict.model_validate(obj)
    return {"verdict": d.verdict, "reasons": list(d.reasons), "doubt": d.doubt}


def _review_score(expect: dict[str, Any], output: dict[str, Any]) -> float | None:
    want = expect.get("verdict")
    if want is None:
        return None
    return 1.0 if output.get("verdict") == want else 0.0


FEEDBACK_REVIEW = StageSpec(
    key="feedback_review", trace_agent="feedback", trace_tool="feedback:review",
    response_model=ReviewVerdict, variables_from_args=_review_variables,
    output_summary=_review_output, deterministic_score=_review_score,
    max_tokens=REVIEW_MAX_TOKENS,
)

# The fork reviewer: forking at a turn flags its answer with no follow-up text,
# so a fork review has its own prompt (no <user_followup>). Same variables,
# output and verdict grade as the keyword reviewer; the corpus is
# ``feedback_review.fork.jsonl``. A recorded ``feedback:review`` step of either
# trigger replays here; ``trigger`` in its args tells them apart.
FEEDBACK_REVIEW_FORK = StageSpec(
    key="feedback_review_fork", trace_agent="feedback", trace_tool="feedback:review",
    response_model=ReviewVerdict, variables_from_args=_review_variables,
    output_summary=_review_output, deterministic_score=_review_score,
    max_tokens=REVIEW_MAX_TOKENS,
)

# --- ideation (Plan H3) ---------------------------------------------------
# The problem -> mechanism (or document -> document) proposer. Hand-authored
# corpus (SRS §III-10.3): ``expect.source_doc`` (an id, or a list of acceptable
# ids) is the paper whose mechanism should be suggested; a free-exploration case
# also sets ``expect.target_doc`` and is graded on the ordered pair.
# ``expect.problem_doc`` / ``exclude_docs`` are card-building metadata the score
# ignores. Corpus ``cards_xml`` holds the ``CARDS_FROM_DB`` sentinel: the cards
# are built from the live library before a sweep (see the agent README).

_IDEATION_KEYS = ("problem", "tried", "cards_xml", "rejected_xml", "existing_xml", "mode",
                  "response_language")
CARDS_FROM_DB = "@cards_from_db"
RECALL_K = 5


def _ideation_variables(args: dict[str, Any]) -> dict[str, Any]:
    return {k: args.get(k, "") for k in _IDEATION_KEYS}


def _ideation_output(obj: Any) -> dict[str, Any]:
    d = obj if isinstance(obj, IdeationOutput) else IdeationOutput.model_validate(obj)
    return {"ideas": [i.model_dump() for i in d.ideas], "no_fit_reason": d.no_fit_reason}


def _cited(name: Any, sections: list[str]) -> bool:
    """``generate_ideas``' section check: the name, or one part of a copied
    ``"A; B"`` cites list, is a section the card cites (normalized)."""
    if not isinstance(name, str) or not name:
        return False
    cited = {_norm(x) for x in sections}
    return any(_norm(part) in cited for part in (name, *name.split(";")))


def _norm(name: str) -> str:
    return " ".join(name.split()).casefold()


def _kept(expect: dict[str, Any], ideas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The ideas ``generate_ideas`` would keep, in rank order, judged against
    ``expect.board`` (doc id -> domains + cited sections, written by the build
    step): known documents, cited sections, a cross-domain explore pair
    (``cross_domain_reason``) and no duplicate. Chunk presence and earlier
    dismissals are not replayed."""
    board: dict[str, dict[str, Any]] = expect["board"]
    mode = expect.get("mode")  # written by the build step; older files guess from the labels
    explore = (mode == "explore" if mode is not None
               else "target_doc" in expect or "min_valid" in expect)
    kept: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for i in ideas:
        src = board.get(str(i.get("source_doc")))
        if src is None or not _cited(i.get("source_section"), src["sections"]):
            continue
        tgt_id = i.get("target_doc")
        if explore:
            tgt = board.get(str(tgt_id))
            if (tgt is None or tgt_id == i.get("source_doc")
                    or not _cited(i.get("target_section"), tgt["sections"])
                    or cross_domain_reason(src["domains"], tgt["domains"]) is not None):
                continue
        elif tgt_id is not None:
            continue
        key = (i.get("source_doc"), tgt_id)
        if key in seen:
            continue
        seen.add(key)
        kept.append(i)
    return kept


_SIMPLIFIED = set("这们来个说时为过还发对没问现样经应么会无与东车长门见间实头点产学开关电话动机让进练数据题")
_TRADITIONAL = set("這們來個說時為過還發對沒問現樣經應麼會無與東車長門見間實頭點產學開關電話動機讓進練數據題")
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")


def in_language(text: str, language: str) -> bool:
    """Whether ``text`` is written in ``language`` (``en``, ``zh-TW``, ``zh-CN``
    or ``ja``): the share of CJK characters among letters, and for Chinese the
    simplified-only vs traditional-only characters. English paper titles and
    terms inside a Chinese sentence are fine."""
    cjk = len(_CJK.findall(text))
    latin = len(_LATIN.findall(text))
    share = cjk / (cjk + latin) if cjk + latin else 0.0
    if language == "en":
        return share < 0.05
    if share < 0.3:
        return False
    simp = sum(ch in _SIMPLIFIED for ch in text)
    trad = sum(ch in _TRADITIONAL for ch in text)
    if language == "zh-TW":
        return trad >= simp
    if language == "zh-CN":
        return simp >= trad
    return True


_GENERIC_TITLE_WORDS = frozenset("""
learning models model neural network networks language large deep towards toward
scalable efficient survey using based with from data training vision robot robotics
transformer transformers simple effective general generalist foundation real world
action actions applications comprehensive review study analysis approach method
""".split())  # noqa: SIM905 — a word list reads better as text


def names_source(rationale: str, title: str) -> bool:
    """The rationale names the source paper: its short name (the part before
    ':'), or a distinctive word of its title."""
    low = rationale.casefold()
    short = title.split(":")[0].strip()
    if ":" in title and short and short.casefold() in low:
        return True
    words = {w.casefold() for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{3,}", title)}
    return any(w in low for w in words - _GENERIC_TITLE_WORDS)


def _idea_text(i: dict[str, Any]) -> str:
    return f"{i.get('title') or ''} {i.get('rationale') or ''}"


def _quality_factors(expect: dict[str, Any], ideas: list[dict[str, Any]]) -> list[float]:
    """QA I1/I3 factors: share of kept ideas in ``expect.language`` (1 when >=
    80%), and the share whose rationale names its source paper."""
    out: list[float] = []
    if not ideas:
        return out
    if expect.get("language"):
        ok = sum(in_language(_idea_text(i), expect["language"]) for i in ideas)
        out.append(1.0 if ok / len(ideas) >= 0.8 else 0.0)
    if expect.get("names_source"):
        board = expect.get("board") or {}
        named = sum(names_source(str(i.get("rationale") or ""),
                                 str((board.get(str(i.get("source_doc"))) or {}).get("title", "")))
                    for i in ideas)
        out.append(named / len(ideas))
    return out


def _recall_at_5(expect: dict[str, Any], output: dict[str, Any]) -> float | None:
    """1.0 when the expected source (problem mode) or the ordered
    ``(source_doc, target_doc)`` pair (free exploration) is among the first
    ``RECALL_K`` ideas; ``None`` for an unlabelled case. With ``expect.board``
    only the ideas the agent would keep count (so a dropped idea frees its
    slot and an invalid hit misses). ``expect.min_valid`` instead scores the
    yield: kept ideas / ``min_valid``, capped at 1."""
    ideas = list(output.get("ideas") or [])
    if "board" in expect:
        ideas = _kept(expect, ideas)
    if expect.get("no_ideas"):
        # QA I2: nothing applies -> no idea survives and the reason is given
        # (in the requested language when one is labelled).
        reason = str(output.get("no_fit_reason") or "").strip()
        lang = expect.get("language")
        ok = not ideas and bool(reason) and (not lang or in_language(reason, lang))
        return 1.0 if ok else 0.0
    factors = _quality_factors(expect, ideas)
    base = _base_score(expect, ideas)
    if base is None:
        if not factors:
            return None if not (expect.get("language") or expect.get("names_source")) else 0.0
        base = 1.0
    for f in factors:
        base *= f
    return base


def _base_score(expect: dict[str, Any], ideas: list[dict[str, Any]]) -> float | None:
    if "min_valid" in expect:
        return min(len(ideas), int(expect["min_valid"])) / int(expect["min_valid"])
    want = expect.get("source_doc")
    if want is None:
        return None
    sources = set(want) if isinstance(want, list) else {want}
    top = ideas[:RECALL_K]
    if "target_doc" in expect:
        hit = any(i.get("source_doc") in sources and i.get("target_doc") == expect["target_doc"]
                  for i in top)
    else:
        hit = any(i.get("source_doc") in sources for i in top)
    return 1.0 if hit else 0.0


IDEATION = StageSpec(
    key="ideation", trace_agent="ideation", trace_tool="ideation:propose",
    response_model=ProposeDraft, variables_from_args=_ideation_variables,
    output_summary=_ideation_output, deterministic_score=_recall_at_5,
    max_tokens=PROPOSE_MAX_TOKENS,
)

# --- ideation_card (Plan H3) ----------------------------------------------
# One document's PaperDigest -> a cited, domain-free card (``CardDraft``). Corpus cases
# carry the exact prompt variables ``build_card`` renders (``card_variables``)
# and ``expect.sections`` = the digest's section names. Deterministic gate: the
# problem and mechanism each cite >= 1 section, every cited name is a digest
# section, and at least one domain is tagged. Wording is judged with
# ``pairwise`` (faithfulness + domain-free phrasing).

_CARD_KEYS = ("title", "abstract", "sections_xml")


def _card_variables(args: dict[str, Any]) -> dict[str, Any]:
    return {k: args.get(k, "") for k in _CARD_KEYS}


def _card_output(obj: Any) -> dict[str, Any]:
    d = obj if isinstance(obj, CardDraft) else CardDraft.model_validate(obj)
    return d.model_dump()


def card_failures(expect: dict[str, Any], output: dict[str, Any]) -> list[str]:
    """Contract violations of one card (empty list = pass)."""
    known = {" ".join(str(n).split()).casefold() for n in expect.get("sections") or []}
    fails: list[str] = []
    fields: list[tuple[str, dict[str, Any]]] = [
        ("problem", output.get("problem") or {}),
        ("mechanism", output.get("mechanism") or {}),
    ]
    for key in ("assumptions", "limitations"):
        fields += [(f"{key}[{i}]", f) for i, f in enumerate(output.get(key) or [])]
    for name, f in fields:
        cited = list(f.get("sections") or [])
        if name in ("problem", "mechanism") and not cited:
            fails.append(f"{name} cites no section")
        if not str(f.get("text", "")).strip():
            fails.append(f"{name} is empty")
        fails += [f"{name} cites unknown section {c!r}" for c in cited
                  if " ".join(str(c).split()).casefold() not in known]
    if not [d for d in output.get("domains") or [] if str(d).strip()]:
        fails.append("no domain")
    return fails


def domain_failures(expect: dict[str, Any], output: dict[str, Any]) -> list[str]:
    """QA I8 domain labels: the tags as ``assign_domains`` stores them
    (``stored_tags``: canonical, a secondary "Machine Learning" dropped, at
    most two) must start with
    one of ``expect.primary_domain`` and stay within ``expect.allowed_domains``.
    Empty when the case has no domain labels."""
    if "primary_domain" not in expect:
        return []
    tags = stored_tags([str(d) for d in output.get("domains") or []])
    fails: list[str] = []
    if not tags or tags[0] not in expect["primary_domain"]:
        fails.append(f"primary domain {tags[:1]} not in {expect['primary_domain']}")
    fails += [f"domain {t!r} not allowed" for t in tags
              if t not in expect.get("allowed_domains", expect["primary_domain"])]
    return fails


def _card_score(expect: dict[str, Any], output: dict[str, Any]) -> float | None:
    if "sections" not in expect:
        return None
    return 0.0 if card_failures(expect, output) or domain_failures(expect, output) else 1.0


IDEATION_CARD = StageSpec(
    key="ideation_card", trace_agent="ideation", trace_tool="ideation:card",
    response_model=CardDraft, variables_from_args=_card_variables,
    output_summary=_card_output, deterministic_score=_card_score,
)

STAGE_REGISTRY: dict[str, StageSpec] = {
    ROUTER.key: ROUTER, TRANSLATE_BLOCKS.key: TRANSLATE_BLOCKS,
    FEEDBACK_REVIEW.key: FEEDBACK_REVIEW, FEEDBACK_REVIEW_FORK.key: FEEDBACK_REVIEW_FORK,
    IDEATION.key: IDEATION,
    IDEATION_CARD.key: IDEATION_CARD,
}


def get_stage(key: str) -> StageSpec:
    if key not in STAGE_REGISTRY:
        raise KeyError(f"unknown stage {key!r}; known: {sorted(STAGE_REGISTRY)}")
    return STAGE_REGISTRY[key]
