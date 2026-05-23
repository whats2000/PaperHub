# CUDA-capable base so the host RTX GPU accelerates Marker when
# nvidia-container-toolkit + the compose `deploy.devices` block are enabled.
# Falls back to CPU automatically if no GPU is visible to the container.
FROM pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models \
    TORCH_HOME=/models/torch

WORKDIR /app

# System deps marker/pdftext may need at runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# torch already present in the base image; marker-pdf pulls the rest.
RUN pip install -r requirements.txt

# Pre-download Marker model weights into the image so the first /extract is
# fast (no cold network fetch at request time).
RUN python -c "from marker.models import create_model_dict; create_model_dict()"

COPY app.py .

EXPOSE 8002
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8002"]
