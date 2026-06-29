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

from pydantic import BaseModel

from paperhub.models.domain import RoutingDecision


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
    d = obj if isinstance(obj, RoutingDecision) else RoutingDecision.model_validate(obj)
    return {"intent": d.intent, "resolved_query": d.resolved_query,
            "response_language": d.response_language, "confidence": d.confidence}


def _router_score(expect: dict[str, Any], output: dict[str, Any]) -> float | None:
    want = expect.get("intent")
    if want is None:
        return None
    return 1.0 if output.get("intent") == want else 0.0


ROUTER = StageSpec(
    key="router", trace_agent="router", trace_tool="classify",
    response_model=RoutingDecision, variables_from_args=_router_variables,
    output_summary=_router_output, deterministic_score=_router_score,
)

STAGE_REGISTRY: dict[str, StageSpec] = {ROUTER.key: ROUTER}


def get_stage(key: str) -> StageSpec:
    if key not in STAGE_REGISTRY:
        raise KeyError(f"unknown stage {key!r}; known: {sorted(STAGE_REGISTRY)}")
    return STAGE_REGISTRY[key]
