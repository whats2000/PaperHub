# Clean slim base — let uv resolve marker-pdf's FULL, COHERENT torch ecosystem
# (torch + torchvision + transformers + surya) in one pass. An earlier attempt
# reused a conda `pytorch/pytorch` base's pinned torch 2.4.1 and let uv upgrade
# its companions (triton/torchvision/transformers) — that produced a torch ↔
# torchvision ABI mismatch (`operator torchvision::nms does not exist`). A clean
# base avoids the split-brain torch install.
#
# GPU: the default PyPI torch wheel bundles CUDA userspace libs, so the host RTX
# GPU is used when nvidia-container-toolkit + the compose `deploy.devices` block
# are enabled; it falls back to CPU automatically otherwise.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 \
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
# Install the coherent dependency set declared in pyproject.toml into the
# system python (--system; UV_SYSTEM_PYTHON keeps it venv-free).
RUN uv pip install --system -r pyproject.toml

# Pre-download Marker model weights into the image so the first /extract is
# fast (no cold network fetch at request time).
RUN python -c "from marker.models import create_model_dict; create_model_dict()"

COPY app.py .

EXPOSE 8002
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8002"]
