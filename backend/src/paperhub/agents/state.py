from paperhub.models.domain import AgentState

__all__ = ["AgentState", "effective_query", "response_language"]


def effective_query(state: AgentState) -> str:
    """The text downstream agents should act on: the router's
    anaphora-resolved brief when present, else the raw user_message
    (v2.11). One source of truth for the fallback semantics."""
    return state.get("effective_query") or state["user_message"]


def response_language(state: AgentState) -> str:
    """The language a final-response agent should write in: the router's
    detected language (v2.13) when present, else a neutral phrase that tells
    the model to mirror the user. One source of truth for the fallback."""
    return state.get("response_language") or "the user's language"
