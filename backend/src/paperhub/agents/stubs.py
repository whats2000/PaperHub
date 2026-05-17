from paperhub.agents.state import AgentState
from paperhub.models.domain import Intent

_STUB_TEMPLATE = (
    "I can see this is a `{intent}` request, but that agent is not yet wired up "
    "in Plan A — it'll arrive in a later implementation plan."
)


async def stub_response(state: AgentState, *, intent: Intent) -> str:  # noqa: ARG001
    return _STUB_TEMPLATE.format(intent=intent)
