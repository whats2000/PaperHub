# syntax=docker/dockerfile:1
# Clean slim base — let uv resolve marker-pdf's FULL, COHERENT torch ecosystem
# (torch + torchvision + transformers + surya) in one pass. (An earlier conda
# `pytorch/pytorch` base produced a torch ↔ torchvision ABI mismatch —
# `operator torchvision::nms does not exist` — when uv upgraded the companions.)
#
# GPU: the default PyPI torch wheel bundles CUDA userspace libs, so the host RTX
# GPU is used when nvidia-container-toolkit + the compose `deploy.devices` block
# are enabled; it falls back to CPU automatically otherwise.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1 \
    HF_HOME=/models \
    TORCH_HOME=/models/torch

WORKDIR /app

# Runtime libs marker/opencv (surya) need.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Fast installs with uv (project convention). The uv binary is copied from its
# official distroless image (no pip-install-uv step).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
COPY pyproject.toml .
# BuildKit cache mount: the downloaded wheels (incl. the ~2 GB torch) persist in
# the build cache across rebuilds, so a failed build does NOT re-download them.
# (Hence no UV_NO_CACHE.) --system installs into the base image's python.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system -r pyproject.toml

COPY app.py .

# Models are NOT baked into the image — they download ONCE into the /models
# named volume (see docker-compose.yml) on first /extract and persist across
# container restarts AND image rebuilds, so iterating on the image never
# re-downloads the Surya/marker weights. (HF_HOME/TORCH_HOME point at /models.)
EXPOSE 8002
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8002"]
