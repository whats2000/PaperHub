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

STAGE_REGISTRY: dict[str, StageSpec] = {ROUTER.key: ROUTER}


def get_stage(key: str) -> StageSpec:
    if key not in STAGE_REGISTRY:
        raise KeyError(f"unknown stage {key!r}; known: {sorted(STAGE_REGISTRY)}")
    return STAGE_REGISTRY[key]
