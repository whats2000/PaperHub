"""Research Agent paper_qa streaming tests (SRS v2.3, FR-03, I-8 #3)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import aiosqlite
import pytest

from paperhub.agents.research import FinalOnlyMessage, paper_qa_stream
from paperhub.rag.retriever import RetrievedChunk, Retriever
from paperhub.tracing.tracer import Tracer

pytestmark = pytest.mark.asyncio


async def _make_session(conn: aiosqlite.Connection) -> int:
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _insert_paper_with_chunks(
    conn: aiosqlite.Connection,
    *,
    session_id: int,
    arxiv_id: str,
    title: str,
    chunk_texts: list[str],
) -> tuple[int, list[int]]:
    await conn.execute(
        "INSERT INTO paper_content "
        "(content_key, kind, arxiv_id, title, authors_json, year, abstract, "
        "source_path, source_dir_path, html_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"arxiv:{arxiv_id}", "arxiv", arxiv_id, title, "[]", 2024,
            "abs", "/tmp/x.tex", "/tmp", "/tmp/x.html",
        ),
    )
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    pcid = int(row[0])
    chunk_ids: list[int] = []
    for i, txt in enumerate(chunk_texts):
        await conn.execute(
            "INSERT INTO chunks (paper_content_id, section, char_start, "
            "char_end, text) VALUES (?, ?, ?, ?, ?)",
            (pcid, "Body", i * 100, (i + 1) * 100, txt),
        )
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            cr = await cur.fetchone()
        assert cr is not None
        chunk_ids.append(int(cr[0]))
    await conn.execute(
        "INSERT INTO papers (session_id, paper_content_id, enabled) "
        "VALUES (?, ?, 1)",
        (session_id, pcid),
    )
    await conn.commit()
    return pcid, chunk_ids


class _StubAdapter:
    """LlmAdapter stub whose ``stream`` yields a pre-canned token list."""

    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens
        self.last_variables: dict[str, Any] | None = None

    async def structured(self, **_: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    def stream(
        self,
        *,
        slot: str,  # noqa: ARG002
        variables: dict[str, Any],
        model: str,  # noqa: ARG002
        history: list[dict[str, str]] | None = None,  # noqa: ARG002
        **_: Any,
    ) -> AsyncIterator[str]:
        self.last_variables = variables
        tokens = list(self._tokens)

        async def _gen() -> AsyncIterator[str]:
            for t in tokens:
                yield t

        return _gen()


async def test_paper_qa_streams_tokens_with_citations(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
) -> None:
    """Happy path: 2 enabled papers, mocked retriever returns chunks from
    both, concatenated stream cites at least 2 distinct paper_content_ids
    (I-8 #3)."""
    session_id = await _make_session(migrated_db)
    pcid_a, chunks_a = await _insert_paper_with_chunks(
        migrated_db, session_id=session_id, arxiv_id="2401.0001",
        title="Paper A", chunk_texts=["A1 text", "A2 text"],
    )
    pcid_b, chunks_b = await _insert_paper_with_chunks(
        migrated_db, session_id=session_id, arxiv_id="2401.0002",
        title="Paper B", chunk_texts=["B1 text"],
    )

    canned_chunks = [
        RetrievedChunk(chunk_id=chunks_a[0], paper_content_id=pcid_a,
                       text="A1 text", score=0.9),
        RetrievedChunk(chunk_id=chunks_b[0], paper_content_id=pcid_b,
                       text="B1 text", score=0.85),
        RetrievedChunk(chunk_id=chunks_a[1], paper_content_id=pcid_a,
                       text="A2 text", score=0.7),
    ]

    retriever = MagicMock(spec=Retriever)
    retriever.retrieve.return_value = canned_chunks

    tokens = [
        "Both ", "papers ", "discuss ", "the topic ",
        f"[chunk:{chunks_a[0]}] ", "and ", f"[chunk:{chunks_b[0]}].",
    ]
    adapter = _StubAdapter(tokens)

    state = {
        "run_id": fake_tracer._run_id,  # noqa: SLF001
        "branch": "", "session_id": session_id,
        "user_message": "compare the two papers",
    }

    collected: list[str] = []
    async for tok in paper_qa_stream(
        state, adapter=adapter, tracer=fake_tracer,
        model="m", retriever=retriever, conn=migrated_db,
    ):
        collected.append(tok)

    assert collected == tokens
    body = "".join(collected)
    assert f"[chunk:{chunks_a[0]}]" in body
    assert f"[chunk:{chunks_b[0]}]" in body

    # I-8 #3: at least 2 distinct paper_content_ids cited
    cited_pcids = {c.paper_content_id for c in canned_chunks}
    assert len(cited_pcids) >= 2

    # variables fed to the LLM contained the chunks_context with both ids
    assert adapter.last_variables is not None
    ctx = adapter.last_variables["chunks_context"]
    assert f"[chunk:{chunks_a[0]}]" in ctx
    assert f"[chunk:{chunks_b[0]}]" in ctx
    assert f"(paper {pcid_a})" in ctx
    assert f"(paper {pcid_b})" in ctx


async def test_paper_qa_no_enabled_papers_short_circuits(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
) -> None:
    """If the session has no enabled papers, yield a FinalOnlyMessage and stop —
    do NOT call the retriever or adapter."""
    session_id = await _make_session(migrated_db)
    retriever = MagicMock(spec=Retriever)
    adapter = _StubAdapter(["should not stream"])

    state = {
        "run_id": fake_tracer._run_id,  # noqa: SLF001
        "branch": "", "session_id": session_id,
        "user_message": "anything",
    }
    out: list[str | FinalOnlyMessage] = []
    async for item in paper_qa_stream(
        state, adapter=adapter, tracer=fake_tracer,
        model="m", retriever=retriever, conn=migrated_db,
    ):
        out.append(item)
    assert len(out) == 1
    assert isinstance(out[0], FinalOnlyMessage)
    assert "No references are enabled" in out[0].content
    retriever.retrieve.assert_not_called()
    assert adapter.last_variables is None


async def test_paper_qa_no_chunks_short_circuits(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
) -> None:
    """Enabled paper but retriever returns no chunks → FinalOnlyMessage."""
    session_id = await _make_session(migrated_db)
    await _insert_paper_with_chunks(
        migrated_db, session_id=session_id, arxiv_id="2401.0099",
        title="Paper", chunk_texts=["text"],
    )

    retriever = MagicMock(spec=Retriever)
    retriever.retrieve.return_value = []
    adapter = _StubAdapter(["should not stream"])

    state = {
        "run_id": fake_tracer._run_id,  # noqa: SLF001
        "branch": "", "session_id": session_id,
        "user_message": "anything",
    }
    out: list[str | FinalOnlyMessage] = []
    async for item in paper_qa_stream(
        state, adapter=adapter, tracer=fake_tracer,
        model="m", retriever=retriever, conn=migrated_db,
    ):
        out.append(item)
    assert len(out) == 1
    assert isinstance(out[0], FinalOnlyMessage)
    assert "No relevant chunks" in out[0].content
    retriever.retrieve.assert_called_once()
    assert adapter.last_variables is None
