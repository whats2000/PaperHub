"""Module entry point for ``python -m paperhub.modelserver``.

Reads ``PAPERHUB_MODEL_SERVER_HOST`` / ``PAPERHUB_MODEL_SERVER_PORT``
(populated by ``load_settings()``) and launches uvicorn against the
FastAPI app. Logging level inherits from ``PAPERHUB_LOG_LEVEL``.

Note: NOT using ``uvicorn --reload`` here — the whole point of this
process is to survive backend reloads, so we want it static.
"""
from __future__ import annotations

import logging

import uvicorn

from paperhub.config import load_settings


def main() -> None:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level)
    uvicorn.run(
        "paperhub.modelserver.server:app",
        host=settings.model_server_host,
        port=settings.model_server_port,
        log_level=settings.log_level.lower(),
        reload=False,
        # Single worker is correct — the embedder/reranker keep heavy
        # state and we don't want N copies of the model in memory.
        workers=1,
    )


if __name__ == "__main__":
    main()
