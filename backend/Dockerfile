# The Debian release is part of the tag: a silent jump to the next one would also change
# the ffmpeg major version under the worker (torchcodec links its libraries).
FROM python:3.11-slim-trixie AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONPATH=/srv

# fonts-dejavu-core gives the PDF export a Cyrillic-capable TTF: the fonts built into PDF
# are Latin-only, so without it a Russian transcript renders as empty boxes. It is in the
# shared base because the API image regenerates exports after a speaker rename.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# Exact versions with hashes (T13.3): a clean build installs what was tested, and a file
# that does not match its hash fails the build. --no-deps because the lock already lists
# every dependency; requirements.txt holds only the ranges it is generated from.
# The pip cache is a BuildKit cache mount, outside every layer: a rebuild after a
# dependency change downloads only what changed.
COPY requirements.lock ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps --require-hashes -r requirements.lock

# ---------------------------------------------------------------------------
# API image: no ML dependencies and no ffmpeg — the API never decodes audio or loads a
# model. Only its own code: worker/ and benchmark/ stay out (AGENTS.md, image split).
FROM base AS runtime

COPY healthcheck.py ./
COPY app ./app

# Runs as root on purpose: /data and /models are host bind mounts and a fixed in-image
# UID cannot match the host user on macOS/Windows/Linux alike. This is a local-only app.
RUN mkdir -p /data /models

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---------------------------------------------------------------------------
# Worker image: ffmpeg, torch and pyannote on top of the API dependencies. Layers go from
# the one that changes least to the one that changes most.
FROM base AS worker

# ffmpeg decodes the uploads (worker/pipeline/audio.py), and its libraries are what
# torchcodec, and through it pyannote, reads audio with. The Debian package also pulls in
# ~200 MB of LLVM and mesa through libavdevice and libavfilter; torchcodec links both of
# those, so they cannot be left out.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# The PyPI torch wheels bundle CUDA libraries on both amd64 and arm64, which would add
# gigabytes to a build that must stay CPU-only and portable. requirements-torch.txt pins
# `+cpu` local versions, which resolve only from the PyTorch CPU index; everything else
# comes from PyPI. Every file is pinned by its hash, so mixing the two indexes cannot pull
# in anything else. See docker-compose.cuda.yml for the GPU story.
# torch has a layer of its own: it is the largest, and a pyannote or GigaAM bump must not
# rebuild it.
COPY requirements-torch.lock ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps --require-hashes -r requirements-torch.lock \
        --index-url https://download.pytorch.org/whl/cpu \
        --extra-index-url https://pypi.org/simple

# GigaAM is a git dependency, which cannot carry a hash: it is installed separately from its
# exact line in requirements-worker.txt (the commit id pins the content), without
# dependencies — those are in the lock. git is only needed for that, so it is installed and
# removed inside a single layer instead of staying in the shipped image.
COPY requirements-worker.lock requirements-worker.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    apt-get update \
    && apt-get install -y --no-install-recommends git \
    && pip install --no-deps --require-hashes -r requirements-worker.lock \
        --index-url https://download.pytorch.org/whl/cpu \
        --extra-index-url https://pypi.org/simple \
    && pip install --no-deps "$(grep -E '^gigaam @ ' requirements-worker.txt)" \
    && apt-get purge -y --auto-remove git \
    && rm -rf /var/lib/apt/lists/*

# pyannote.audio 4 reports every model load and every processed file to otel.pyannote.ai
# unless this is off (T13.1). The worker forces it off again before loading models; the
# image default also covers ad-hoc `docker compose run worker python ...` sessions.
ENV PYANNOTE_METRICS_ENABLED=false \
    HF_HUB_DISABLE_TELEMETRY=1

COPY app ./app
COPY worker ./worker
COPY benchmark ./benchmark

RUN mkdir -p /data /models

CMD ["python", "-m", "worker.main"]
