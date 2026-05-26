# syntax=docker/dockerfile:1
#
# PaperHub backend image — shared by the `backend` (uvicorn) and
# `modelserver` (embedder + reranker) compose services. They run the SAME
# `paperhub` package, so one image with two commands avoids building torch +
# TeX Live twice.
#
# CPU vs GPU is a BUILD-TIME choice (the torch wheel is baked in): the default
# `TORCH_EXTRA=` resolves the CPU torch wheel from PyPI; pass
# `--build-arg TORCH_EXTRA=cu126` (see docker-compose.gpu.yml) to swap in the
# matching CUDA wheel via the project's `[project.optional-dependencies]`.
FROM python:3.11-slim

# `cu124` | `cu126` | `cu130` to install the CUDA torch wheel; empty = CPU.
ARG TORCH_EXTRA=

ENV PYTHONUNBUFFERED=1 \
    # uv installs into a project venv at /app/.venv; put it on PATH so the
    # console scripts (uvicorn, paperhub-modelserver) resolve without `uv run`.
    PATH="/app/.venv/bin:$PATH" \
    # HF weights (embedder/reranker) download into the mounted models volume.
    HF_HOME=/models \
    TORCH_HOME=/models/torch

WORKDIR /app

# System deps:
#   - Full TeX Live set (mirrors reference/paper2slides-plus/Dockerfile) — a
#     HARD requirement for the `slides` intent (Beamer → pdflatex). The
#     metropolis theme + Fira fonts live in texlive-latex-extra/fonts-extra;
#     fonts-firacode adds Fira for the best metropolis output.
#   - pandoc: optional LaTeX→HTML for the Citation Canvas (else pylatexenc).
#   - poppler-utils: PDF tooling used by the pipeline.
#   - libgomp1: OpenMP runtime the torch CPU wheel links against.
RUN apt-get update && apt-get install -y --no-install-recommends \
    texlive-latex-base \
    texlive-latex-recommended \
    texlive-latex-extra \
    texlive-fonts-recommended \
    texlive-fonts-extra \
    texlive-lang-all \
    texlive-science \
    latexmk \
    chktex \
    fonts-firacode \
    pandoc \
    poppler-utils \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# uv binary from its official distroless image (no pip-install-uv step).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Resolve dependencies first (cached across source edits). The BuildKit cache
# mount keeps the downloaded wheels (incl. the ~2 GB torch) across rebuilds so
# a failed build never re-downloads them.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "$TORCH_EXTRA" ]; then EXTRA="--extra $TORCH_EXTRA"; else EXTRA=""; fi; \
    uv sync --frozen --no-install-project --no-dev $EXTRA

# Now the source + install the project itself (uv_build).
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "$TORCH_EXTRA" ]; then EXTRA="--extra $TORCH_EXTRA"; else EXTRA=""; fi; \
    uv sync --frozen --no-dev $EXTRA

# Default command = backend API. The modelserver service overrides this.
EXPOSE 8000
CMD ["uvicorn", "paperhub.app:app", "--host", "0.0.0.0", "--port", "8000"]
