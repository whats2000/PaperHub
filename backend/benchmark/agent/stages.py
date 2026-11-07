"""Per-stage evaluation specs (SRS §III-9).

What a stage needs to be replayed + scored in isolation: how it appears in the
tool_calls trace, its structured output model, and three small callables. Plan G1
ships the router; downstream stages are added in G2. Read-only imports of the
production response model — NO deploy-code change.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from paperhub.feedback.models import ReviewVerdict, format_reasons_block
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
    confidence: float = 0.0
    reasoning: str = ""
    resolved_query: str | None = None
    response_language: str | None = None


@dataclass(frozen=True)
class StageSpec:
    key: str
    trace_agent: str
    trace_tool: str
    response_model: type[BaseModel] | None
    variables_from_args: Callable[[dict[str, Any]], dict[str, Any]]
    output_summary: Callable[[Any], dict[str, Any]]
    deterministic_score: Callable[[dict[str, Any], dict[str, Any]], float | None]


def _router_variables(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_message": args["user_message"],
        "enabled_refs_count": args.get("enabled_refs_count", 0),
        "slide_attached": args.get("slide_attached", False),
    }


def _router_output(obj: Any) -> dict[str, Any]:
    d = obj if isinstance(obj, RouterEvalOutput) else RouterEvalOutput.model_validate(obj)
    return {"intent": d.intent, "resolved_query": d.resolved_query,
            "response_language": d.response_language, "confidence": d.confidence}


def _router_score(expect: dict[str, Any], output: dict[str, Any]) -> float | None:
    want = expect.get("intent")
    if want is None:
        return None
    return 1.0 if output.get("intent") == want else 0.0


ROUTER = StageSpec(
    key="router", trace_agent="router", trace_tool="classify",
    response_model=RouterEvalOutput, variables_from_args=_router_variables,
    output_summary=_router_output, deterministic_score=_router_score,
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
)

STAGE_REGISTRY: dict[str, StageSpec] = {
    ROUTER.key: ROUTER, TRANSLATE_BLOCKS.key: TRANSLATE_BLOCKS,
    FEEDBACK_REVIEW.key: FEEDBACK_REVIEW,
}


def get_stage(key: str) -> StageSpec:
    if key not in STAGE_REGISTRY:
        raise KeyError(f"unknown stage {key!r}; known: {sorted(STAGE_REGISTRY)}")
    return STAGE_REGISTRY[key]
