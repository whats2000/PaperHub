from paperhub.models.domain import AgentState

__all__ = ["AgentState", "effective_query"]


def effective_query(state: AgentState) -> str:
    """The text downstream agents should act on: the router's
    anaphora-resolved brief when present, else the raw user_message
    (v2.11). One source of truth for the fallback semantics."""
    return state.get("effective_query") or state["user_message"]
