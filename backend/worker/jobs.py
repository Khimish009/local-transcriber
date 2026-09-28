"""Job entrypoint executed by the RQ worker.

Phase 1: the pipeline is a placeholder that walks the real state machine and reports
progress. FFmpeg, diarization, ASR, alignment and exports land in Phases 2-6, replacing
the body of each stage below.
"""

import logging
import time

from app.core.config import get_settings
from app.core.redis import get_redis
from app.schemas.job import STAGE_PROGRESS_RANGE, JobStatus
from app.services.job_store import JobStore

logger = logging.getLogger(__name__)

# Placeholder stages, in pipeline order (SPEC.md §5).
PLACEHOLDER_STAGES: tuple[JobStatus, ...] = (
    JobStatus.PREPARING_AUDIO,
    JobStatus.DIARIZING,
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


def process_job(job_id: str, stage_seconds: float | None = None) -> None:
    settings = get_settings()
    store = JobStore(get_redis())
    delay = DEFAULT_STAGE_SECONDS if stage_seconds is None else stage_seconds

    job = store.require(job_id)
    logger.info("job started", extra={"job_id": job_id, "device": settings.device})
    started = time.monotonic()

    try:
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
    except Exception as exc:  # noqa: BLE001 - never leave a job stuck in a running state
        logger.exception("job failed", extra={"job_id": job_id, "stage": job.status.value})
        store.fail(job_id, message="Internal error while processing the job")
        raise exc
    finally:
        logger.info(
            "job finished",
            extra={"job_id": job_id, "duration": round(time.monotonic() - started, 3)},
        )
