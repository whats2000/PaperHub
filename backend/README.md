# PaperHub backend

## Run
    uv run uvicorn paperhub.app:app --reload

## Test
    uv run pytest -v

## Lint + typecheck
    uv run ruff check src tests
    uv run mypy src

## Quality gates

All of these must pass before opening a PR for Plan B onward:

    uv run pytest -v
    uv run ruff check src tests
    uv run mypy src
