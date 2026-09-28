FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg is required by the audio pipeline and by the worker at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt requirements-dev.txt requirements-worker.txt ./
RUN pip install -r requirements.txt

# ---------------------------------------------------------------------------
FROM base AS dev

RUN pip install -r requirements-dev.txt
COPY . /srv
ENV PYTHONPATH=/srv

# ---------------------------------------------------------------------------
# API image: no ML dependencies at all — the API never loads a model.
FROM base AS runtime

COPY . /srv
ENV PYTHONPATH=/srv

# Runs as root on purpose: /data and /models are host bind mounts and a fixed in-image
# UID cannot match the host user on macOS/Windows/Linux alike. This is a local-only app.
RUN mkdir -p /data /models

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---------------------------------------------------------------------------
# Worker image: adds torch and pyannote on top of the API dependencies.
FROM base AS worker-base

# The PyPI torch wheels bundle CUDA libraries on both amd64 and arm64, which would add
# gigabytes to a build that must stay CPU-only and portable. requirements-worker.txt pins
# `+cpu` local versions, which resolve only from the PyTorch CPU index; everything else
# comes from PyPI. See docker-compose.cuda.yml for the GPU story.
RUN pip install -r requirements-worker.txt \
        --index-url https://download.pytorch.org/whl/cpu \
        --extra-index-url https://pypi.org/simple

FROM worker-base AS worker

COPY . /srv
ENV PYTHONPATH=/srv

RUN mkdir -p /data /models

CMD ["python", "-m", "worker.main"]
