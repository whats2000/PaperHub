"""Research agent — the core paper-QA node in the LangGraph pipeline.

Pipeline (per design §5)
------------------------
1. ``retriever.search(state["user_message"])`` — embed + vector search.
2. Format retrieved passages as ``[§{section}, p.{page}] {text}``.
3. ``prompts.render(slot="research_qa", version="v1", ...)`` — inject passages.
4. ``adapter.generate(slot="research_qa", model_tier="flagship", ...)`` — call
   LLM for the answer.
5. Return a *new* ``AgentState`` copy with ``retrieved_chunks`` and
   ``final_response`` populated (input state is never mutated).
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from paperhub.agents.state import AgentState
from paperhub.llm.adapter import LlmAdapter, LlmMessage
from paperhub.llm.prompts import PromptRegistry
from paperhub.rag.retriever import Retriever


class CitationRef(BaseModel):
    """A citation reference inside an :class:`AgentResponse`."""

    chunk_id: UUID
    section: str | None
    page: int | None


class AgentResponse(BaseModel):
    """Structured answer returned by the research LLM."""

    answer: str
    citations: list[CitationRef]


class ResearchAgent:
    """Answer a user question by retrieving relevant paper passages.

    Parameters
    ----------
    adapter:
        LLM adapter (production: ``LiteLlmAdapter``; tests: ``FakeAdapter``).
    prompts:
        Loaded :class:`~paperhub.llm.prompts.PromptRegistry`.
    retriever:
        Configured :class:`~paperhub.rag.retriever.Retriever`.
    """

    def __init__(
        self,
        adapter: LlmAdapter,
        prompts: PromptRegistry,
        retriever: Retriever,
    ) -> None:
        self._adapter = adapter
        self._prompts = prompts
        self._retriever = retriever

    async def answer(self, state: AgentState) -> AgentState:
        """Run the paper-QA pipeline and return an updated *state* copy.

        The returned dict is a *new* object — the input ``state`` is not
        mutated (pure state transformation for LangGraph compatibility).

        Parameters
        ----------
        state:
            Current agent state.  Must contain ``user_message``.

        Returns
        -------
        AgentState
            Copy of *state* with ``retrieved_chunks`` and ``final_response``
            set.
        """
        user_message = state["user_message"]
        project_id: UUID | None = state.get("project_id")

        # Stage 1: retrieve
        paper_ids = [project_id] if project_id is not None else None
        retrieved = self._retriever.search(
            user_message,
            top_k=5,
            paper_ids=paper_ids,
        )

        # Stage 2: format passages
        passage_parts: list[str] = []
        for rc in retrieved:
            section = rc.chunk.section or "body"
            page = rc.chunk.page if rc.chunk.page is not None else "?"
            passage_parts.append(f"[§{section}, p.{page}] {rc.chunk.text}")
        passages = "\n\n".join(passage_parts) if passage_parts else "(no passages retrieved)"

        # Stage 3: render prompt
        rendered = self._prompts.render(
            slot="research_qa",
            version="v1",
            question=user_message,
            passages=passages,
        )
        messages = [
            LlmMessage(role="system", content=rendered.system),
            LlmMessage(role="user", content=rendered.user),
        ]

        # Stage 4: generate
        agent_response = await self._adapter.generate(
            messages=messages,
            model_tier="flagship",
            response_model=AgentResponse,
            slot="research_qa",
        )

        # Stage 5: return new state (pure transformation).
        # Spread the existing state and override the two output fields.
        # TypedDict spread isn't supported by mypy directly; we use dict union instead.
        new_state: AgentState = dict(state)  # type: ignore[assignment]  # TypedDict from dict()
        new_state["retrieved_chunks"] = retrieved
        new_state["final_response"] = agent_response.answer
        return new_state
