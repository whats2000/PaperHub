# Known upstream type-stub gaps

Per **SRS NFR-11 narrow exception**, every `# type: ignore[<code>]` comment
in PaperHub Python code must reference an entry here. Bare `# type: ignore`
fails CI via `warn_unused_ignores = true` in `backend/pyproject.toml`.

When upstream ships a fix, remove the `# type: ignore[...]` comment AND
delete the corresponding row below.

| Site | Upstream | mypy code | Tracked since | Why it's needed |
|---|---|---|---|---|
| `backend/paperhub/llm/adapter.py:LiteLlmAdapter.generate` (the `response.choices[0].message.content` chain) | LiteLLM `litellm.acompletion` is wrapped in `@client` / `@tracer.wrap()` decorators that strip return-type info; mypy infers `Any` for the entire response object | (none — implicit `Any`, not flagged) | 2026-05-17 (Phase A Task 4 review) | `acompletion` is typed `Any` upstream, so there's nothing for `# type: ignore` to suppress. Documented here so future readers know the LiteLLM boundary has NO static type guarantees, and any added `# type: ignore[union-attr]` would be `warn_unused_ignores`-rejected. Revisit when LiteLLM ships proper async return-type overloads. |
| `backend/paperhub/data/vectors.py:ChromaVectorStore.search` & `delete_by_paper` (`# type: ignore[arg-type]` on `where=` and on `embeddings=`/`query_embeddings=`) | `chromadb` 1.x typed stubs declare a heavy generic `Where`-shape that mypy can't narrow to our literal dicts; same for embedding-list shape | `arg-type` | 2026-05-17 (Phase A Task 5) | Each occurrence is a single-statement `# type: ignore[arg-type]` with a comment explaining the chromadb stub limitation. Remove when `chromadb>=1.x` ships tighter stubs or we switch to a typed wrapper. |
| `backend/paperhub/mcp/tools/grobid_server.py` (`from grobid_client.grobid_client import GrobidClient` — `# type: ignore[import-untyped]`) | `grobid-client-python` ships no PEP 561 stubs | `import-untyped` | 2026-05-17 (Phase A Task 6) | Library is untyped upstream; cannot satisfy strict mode without a stub package or a local `.pyi`. Remove when the package ships stubs or we maintain our own. |
| `backend/paperhub/agents/research.py` (the `dict(state)` → `AgentState` re-pack — `# type: ignore[assignment]`) | mypy doesn't narrow a TypedDict from a plain `dict(...)` literal | `assignment` | 2026-05-17 (Phase A Task 6) | TypedDict copy-and-mutate idiom: shallow copy via `dict(state)`, then re-typed back. Mypy treats the right-hand `dict[str, object]` as too wide to assign to `AgentState`. Acceptable for Phase A; consider `typing.cast(AgentState, dict(state))` or copy.copy with cast in Phase B. |

## Process for adding a new entry

1. The `# type: ignore[<specific-code>]` MUST cite the mypy error code
2. Add a row above with: the file:symbol location, the upstream package + reason, the mypy code, today's date, and a one-sentence "Why it's needed"
3. Review entries at every release; remove rows whose underlying ignore is no longer needed (CI's `warn_unused_ignores` will catch stale ignores automatically)
