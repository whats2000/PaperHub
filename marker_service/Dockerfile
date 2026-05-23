# CUDA-capable base so the host RTX GPU accelerates Marker when
# nvidia-container-toolkit + the compose `deploy.devices` block are enabled.
# Falls back to CPU automatically if no GPU is visible to the container.
FROM pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime

ENV PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 \
    UV_SYSTEM_PYTHON=1 \
    HF_HOME=/models \
    TORCH_HOME=/models/torch

WORKDIR /app

# System deps marker/pdftext may need at runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Fast installs with uv (project convention — far faster resolver than pip for
# the marker-pdf + surya + transformers tree). The uv binary is copied from its
# official distroless image (no pip-install-uv step).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
COPY requirements.txt .
# torch is already in the base image; uv reuses the satisfied install rather
# than re-downloading the multi-GB CUDA wheel. --system installs into the base
# image's python (no venv).
RUN uv pip install --system -r requirements.txt

# Pre-download Marker model weights into the image so the first /extract is
# fast (no cold network fetch at request time).
RUN python -c "from marker.models import create_model_dict; create_model_dict()"

COPY app.py .

EXPOSE 8002
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8002"]
