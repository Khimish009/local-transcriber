"""Job entrypoint executed by the RQ worker.

Implemented so far: audio preparation (Phase 2) and speaker diarization (Phase 3).
ASR, alignment and exports are still placeholders that only walk the state machine —
they land in Phases 4-6.
"""

import logging
import time

from app.core.config import Settings, get_settings
from app.core.errors import AppError, ErrorCode
from app.core.redis import get_redis
from app.schemas.job import STAGE_PROGRESS_RANGE, JobStatus
from app.services.job_store import JobStore
from app.services.storage import JobStorage
from worker.pipeline.audio import normalize_audio, probe_audio
from worker.pipeline.diarization import run_diarization
from worker.pipeline.models import get_diarization_pipeline

logger = logging.getLogger(__name__)

# Stages that still have no real implementation, in pipeline order (SPEC.md §5).
PLACEHOLDER_STAGES: tuple[JobStatus, ...] = (
    JobStatus.TRANSCRIBING,
    JobStatus.ALIGNING,
    JobStatus.GENERATING_EXPORTS,
)
STAGE_STEPS = 4
DEFAULT_STAGE_SECONDS = 1.5


def stage_progress(stage: JobStatus, step: int, steps: int) -> int:
    """Map a step inside a stage onto the global 0..100 progress scale."""
    start, end = STAGE_PROGRESS_RANGE[stage]
    return start + round((end - start) * step / steps)


def prepare_audio(job_id: str, store: JobStore, storage: JobStorage, settings: Settings) -> None:
    """T2.1/T2.2 — read source metadata and produce work/normalized.wav."""
    stage = JobStatus.PREPARING_AUDIO
    store.update(job_id, status=stage, progress=stage_progress(stage, 0, 2))

    source = storage.find_source(job_id)
    if source is None:
        raise AppError(ErrorCode.FFMPEG_FAILED, "Source recording is missing on disk")

    metadata = probe_audio(source, settings.ffprobe_bin)
    job = store.require(job_id)
    job.audio = metadata
    store.replace(job)
    store.update(job_id, progress=stage_progress(stage, 1, 2))

    normalize_audio(source, storage.normalized_path(job_id), settings.ffmpeg_bin)
    store.update(job_id, progress=stage_progress(stage, 2, 2))


def diarize(job_id: str, store: JobStore, storage: JobStorage, settings: Settings) -> None:
    """T3.4/T3.5 — split the recording into anonymous speakers, save work/diarization.json."""
    stage = JobStatus.DIARIZING
    store.update(
        job_id,
        status=stage,
        progress=stage_progress(stage, 0, 3),
        message="Загрузка модели диаризации",
    )

    # Loaded once per worker process and reused by every later job.
    pipeline = get_diarization_pipeline(settings, settings.hf_token)
    store.update(job_id, progress=stage_progress(stage, 1, 3), message="Разделение по спикерам")

    job = store.require(job_id)
    result = run_diarization(
        storage.normalized_path(job_id),
        pipeline,
        speaker_count=job.speaker_count,
    )

    storage.diarization_path(job_id).write_text(result.model_dump_json(indent=2), encoding="utf-8")
    store.update(
        job_id,
        progress=stage_progress(stage, 3, 3),
        message=f"Найдено спикеров: {len(result.speakers)}",
    )


def process_job(job_id: str, stage_seconds: float | None = None) -> None:
    settings = get_settings()
    store = JobStore(get_redis())
    storage = JobStorage(settings.data_dir)
    delay = DEFAULT_STAGE_SECONDS if stage_seconds is None else stage_seconds

    store.require(job_id)
    logger.info("job started", extra={"job_id": job_id, "device": settings.device})
    started = time.monotonic()

    try:
        prepare_audio(job_id, store, storage, settings)
        diarize(job_id, store, storage, settings)

        for stage in PLACEHOLDER_STAGES:
            store.update(job_id, status=stage, progress=stage_progress(stage, 0, STAGE_STEPS))
            for step in range(1, STAGE_STEPS + 1):
                if delay:
                    time.sleep(delay / STAGE_STEPS)
                store.update(job_id, progress=stage_progress(stage, step, STAGE_STEPS))

        store.update(
            job_id,
            status=JobStatus.COMPLETED,
            progress=100,
            message="Placeholder pipeline finished (no transcript yet)",
        )
    except AppError as exc:
        # Expected pipeline failure with a stable error code (SPEC.md §12).
        logger.error("job failed", extra={"job_id": job_id, "error_code": exc.code.value})
        store.fail(job_id, message=exc.message, error_code=exc.code)
        raise
    except Exception:
        logger.exception("job crashed", extra={"job_id": job_id})
        store.fail(job_id, message="Internal error while processing the job")
        raise
    finally:
        logger.info(
            "job finished",
            extra={"job_id": job_id, "duration": round(time.monotonic() - started, 3)},
        )
