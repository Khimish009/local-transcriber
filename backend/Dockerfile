FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg is required by the audio pipeline and by the worker at runtime.
# fonts-dejavu-core gives the PDF export a Cyrillic-capable TTF: the fonts built into PDF
# are Latin-only, so without it a Russian transcript renders as empty boxes. It is in the
# shared base because the API image regenerates exports after a speaker rename.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# Exact versions with hashes (T13.3): a clean build installs what was tested, and a file
# that does not match its hash fails the build. --no-deps because the lock already lists
# every dependency; requirements.txt holds only the ranges it is generated from.
COPY requirements.lock ./
RUN pip install --no-deps --require-hashes -r requirements.lock

# ---------------------------------------------------------------------------
FROM base AS dev

COPY requirements-dev.txt ./
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
# gigabytes to a build that must stay CPU-only and portable. requirements-torch.txt pins
# `+cpu` local versions, which resolve only from the PyTorch CPU index; everything else
# comes from PyPI. Every file is pinned by its hash, so mixing the two indexes cannot pull
# in anything else. See docker-compose.cuda.yml for the GPU story.
# GigaAM is a git dependency, which cannot carry a hash: it is installed separately from its
# exact line in requirements-worker.txt (the commit id pins the content), without
# dependencies — those are in the lock. git is only needed for that, so it is installed and
# removed inside a single layer instead of staying in the shipped image.
COPY requirements-worker.lock requirements-worker.txt ./
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && pip install --no-deps --require-hashes -r requirements-worker.lock \
        --index-url https://download.pytorch.org/whl/cpu \
        --extra-index-url https://pypi.org/simple \
    && pip install --no-deps "$(grep -E '^gigaam @ ' requirements-worker.txt)" \
    && apt-get purge -y --auto-remove git \
    && rm -rf /var/lib/apt/lists/*

FROM worker-base AS worker

# pyannote.audio 4 reports every model load and every processed file to otel.pyannote.ai
# unless this is off (T13.1). The worker forces it off again before loading models; the
# image default also covers ad-hoc `docker compose run worker python ...` sessions.
ENV PYANNOTE_METRICS_ENABLED=false \
    HF_HUB_DISABLE_TELEMETRY=1

COPY . /srv
ENV PYTHONPATH=/srv

RUN mkdir -p /data /models

CMD ["python", "-m", "worker.main"]
