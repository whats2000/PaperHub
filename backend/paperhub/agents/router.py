"""Phase A binary intent router.

Design note (Phase A)
---------------------
The SRS-canonical ``Intent`` literal has 6 values: ``paper_qa``,
``library_stats``, ``research_suggest``, ``slides``, ``mcp_tool``,
``chitchat``.  Phase A only needs to distinguish *on-topic* (``paper_qa``)
from *off-topic* (everything else).

``BinaryRoutingDecision`` narrows ``RoutingDecision.intent`` to
``Literal["paper_qa", "chitchat"]`` using a subclass override.  Phase B will
widen this back to the full 6-way ``Intent`` literal.

The SRS prompt slot ``router/v1`` is already in ``prompts.yaml``.
"""

from __future__ import annotations

from typing import Literal

from paperhub.data.models import RoutingDecision
from paperhub.llm.adapter import LlmAdapter, LlmMessage
from paperhub.llm.prompts import PromptRegistry


class BinaryRoutingDecision(RoutingDecision):
    """Phase A two-class routing decision.

    Extends :class:`~paperhub.data.models.RoutingDecision` with a narrowed
    ``intent`` field.  Off-topic requests map to ``"chitchat"`` (the closest
    SRS canonical term — Phase B may introduce ``"out_of_scope"``).
    """

    intent: Literal["paper_qa", "chitchat"]  # intentional Literal narrowing vs parent


class Router:
    """Classifies a user message into a binary routing decision.

    Parameters
    ----------
    adapter:
        Any object satisfying the :class:`~paperhub.llm.adapter.LlmAdapter`
        Protocol (production: ``LiteLlmAdapter``; tests: ``FakeAdapter``).
    prompts:
        The loaded :class:`~paperhub.llm.prompts.PromptRegistry`.
    """

    def __init__(self, adapter: LlmAdapter, prompts: PromptRegistry) -> None:
        self._adapter = adapter
        self._prompts = prompts

    async def classify(self, user_message: str) -> BinaryRoutingDecision:
        """Return a :class:`BinaryRoutingDecision` for *user_message*.

        Parameters
        ----------
        user_message:
            The raw text typed by the user.
        """
        rendered = self._prompts.render(slot="router", version="v1", user_message=user_message)
        messages = [
            LlmMessage(role="system", content=rendered.system),
            LlmMessage(role="user", content=rendered.user),
        ]
        return await self._adapter.generate(
            messages=messages,
            model_tier="small",
            response_model=BinaryRoutingDecision,
            slot="router",
        )
