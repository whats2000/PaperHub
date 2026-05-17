# PaperHub backend

## Run
    uv run uvicorn paperhub.app:app --reload

## Test
    uv run pytest -v

## Lint + typecheck
    uv run ruff check src tests
    uv run mypy src
