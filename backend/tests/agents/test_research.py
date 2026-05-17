"""Tests for the ResearchAgent paper-QA pipeline."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from paperhub.agents.research import AgentResponse, CitationRef, ResearchAgent
from paperhub.agents.state import AgentState
from paperhub.data.vectors import ChromaVectorStore, ChunkVector
from paperhub.llm.adapter import FakeAdapter
from paperhub.llm.prompts import PromptRegistry
from paperhub.rag.embedder import FakeEmbedder
from paperhub.rag.retriever import Retriever


def _fake_embed(text: str) -> list[float]:
    h = hash(text) % FakeEmbedder.DIM
    vec = [0.01] * FakeEmbedder.DIM
    vec[h] = 1.0
    return vec


@pytest.fixture()
def store(tmp_path: Path) -> ChromaVectorStore:
    return ChromaVectorStore(path=tmp_path / "chroma")


@pytest.fixture()
def prompts() -> PromptRegistry:
    return PromptRegistry.load_default()


@pytest.mark.asyncio
async def test_research_agent_populates_state(
    store: ChromaVectorStore, prompts: PromptRegistry
) -> None:
    """ResearchAgent must populate retrieved_chunks and final_response."""
    paper_id = uuid4()
    chunk_id = uuid4()
    question = "What is the main contribution?"

    # Seed the store with 2 chunks for 1 paper
    store.add(
        [
            ChunkVector(
                chunk_id=chunk_id,
                paper_id=paper_id,
                embedding=_fake_embed(question),
                metadata={"text": "The main contribution is a novel architecture."},
            ),
            ChunkVector(
                chunk_id=uuid4(),
                paper_id=paper_id,
                embedding=_fake_embed("unrelated abc"),
                metadata={"text": "Unrelated section about experiments."},
            ),
        ]
    )

    canned_response = AgentResponse(
        answer="The main contribution is a novel architecture.",
        citations=[
            CitationRef(chunk_id=chunk_id, section="intro", page=1),
        ],
    )
    adapter = FakeAdapter({"research_qa": canned_response})
    retriever = Retriever(store, FakeEmbedder())

    agent = ResearchAgent(adapter, prompts, retriever)

    run_id = uuid4()
    initial_state: AgentState = {
        "run_id": run_id,
        "user_message": question,
    }

    new_state = await agent.answer(initial_state)

    # retrieved_chunks must be populated
    assert "retrieved_chunks" in new_state
    assert len(new_state["retrieved_chunks"]) > 0

    # final_response must equal the canned answer
    assert new_state["final_response"] == canned_response.answer

    # Input state must not have been mutated
    assert "retrieved_chunks" not in initial_state
    assert "final_response" not in initial_state


@pytest.mark.asyncio
async def test_research_agent_does_not_mutate_state(
    store: ChromaVectorStore, prompts: PromptRegistry
) -> None:
    """The input AgentState dict must not be modified in place."""
    paper_id = uuid4()
    store.add(
        [
            ChunkVector(
                chunk_id=uuid4(),
                paper_id=paper_id,
                embedding=_fake_embed("test"),
                metadata={"text": "Some content."},
            )
        ]
    )

    canned = AgentResponse(answer="Some answer.", citations=[])
    adapter = FakeAdapter({"research_qa": canned})
    retriever = Retriever(store, FakeEmbedder())
    agent = ResearchAgent(adapter, prompts, retriever)

    original: AgentState = {"run_id": uuid4(), "user_message": "test"}
    keys_before = set(original.keys())

    await agent.answer(original)

    assert set(original.keys()) == keys_before, "Input state was mutated"
