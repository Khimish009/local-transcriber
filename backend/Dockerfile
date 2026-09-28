FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg is required by the audio pipeline (Phase 2) and by the worker at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements.txt

# ---------------------------------------------------------------------------
FROM base AS dev

RUN pip install -r requirements-dev.txt
COPY . /srv
ENV PYTHONPATH=/srv

# ---------------------------------------------------------------------------
FROM base AS runtime

COPY . /srv
ENV PYTHONPATH=/srv

# Runs as root on purpose: /data and /models are host bind mounts and a fixed in-image
# UID cannot match the host user on macOS/Windows/Linux alike. This is a local-only app.
RUN mkdir -p /data /models

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
