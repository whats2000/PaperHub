# syntax=docker/dockerfile:1
#
# PaperHub backend image — uvicorn API server.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    # uv installs into a project venv at /app/.venv; put it on PATH so
    # console scripts (uvicorn, paperhub-*) resolve without `uv run`.
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# System deps:
#   - Full TeX Live set (mirrors reference/paper2slides-plus/Dockerfile) — a
#     HARD requirement for the `slides` intent (Beamer → pdflatex). The
#     metropolis theme + Fira fonts live in texlive-latex-extra/fonts-extra;
#     fonts-firacode adds Fira for the best metropolis output.
#   - pandoc: optional LaTeX→HTML for the Citation Canvas (else pylatexenc).
#   - poppler-utils: PDF tooling used by the pipeline.
# BuildKit cache mounts keep downloaded .deb files + apt indices across builds,
# so a retry after a dropped connection only fetches what's still missing
# (TeX Live is hundreds of MB). `keep-downloaded-packages` stops the base
# image's docker-clean from deleting the cached debs; we therefore do NOT
# `rm -rf` the lists (the cache mount owns them).
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache && \
    apt-get update && apt-get install -y --no-install-recommends \
    texlive-latex-base \
    texlive-latex-recommended \
    texlive-latex-extra \
    texlive-fonts-recommended \
    texlive-fonts-extra \
    texlive-lang-all \
    texlive-science \
    texlive-xetex \
    latexmk \
    chktex \
    fonts-firacode \
    fonts-noto-cjk \
    pandoc \
    poppler-utils

# uv binary from its official distroless image (no pip-install-uv step).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Resolve dependencies first (cached across source edits). The BuildKit cache
# mount keeps the downloaded wheels across rebuilds so a failed build never
# re-downloads them.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Now the source + install the project itself (uv_build).
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

EXPOSE 8000
CMD ["uvicorn", "paperhub.app:app", "--host", "0.0.0.0", "--port", "8000"]
