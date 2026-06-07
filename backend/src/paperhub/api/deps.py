"""Shared FastAPI dependency helpers."""
from __future__ import annotations

from fastapi import Request

from paperhub.llm.adapter import LlmAdapter
from paperhub.llm.litellm_adapter import LiteLlmAdapter


def get_llm(request: Request) -> LlmAdapter:
    """Return the ``LlmAdapter`` from ``app.state.llm`` if set, else build a
    fresh ``LiteLlmAdapter``.

    Tests inject a stub adapter by assigning to ``app.state.llm`` after
    ``create_app()`` returns. Production code doesn't set it and just gets
    the default LiteLLM adapter (stateless, cheap to construct per-request).
    """
    existing = getattr(request.app.state, "llm", None)
    if existing is not None:
        return existing  # type: ignore[no-any-return]
    return LiteLlmAdapter()
