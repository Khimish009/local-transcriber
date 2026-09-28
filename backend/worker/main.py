"""RQ worker entrypoint.

Uses RQ's `SimpleWorker`, which runs jobs in this process instead of forking a child per
job. That is deliberate: models are loaded once at startup and reused by every job
(AGENTS.md, "ML integration rules"). Forking after loading a torch model deadlocks — the
child inherits the model memory but not the OpenMP thread pool backing it.

The trade-off is that a hard crash inside inference takes the worker down; Docker's
restart policy brings it back, and `recover_orphaned_jobs` fails the job it was running
so it does not stay frozen mid-stage forever.
"""

import logging

from rq import Queue, SimpleWorker

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.core.redis import get_redis
from app.services.recovery import recover_orphaned_jobs

logger = logging.getLogger(__name__)


def warm_up_models(settings: Settings) -> None:
    """Load models before the first job. A failure here is not fatal: the job that needs
    the model fails with a stable error code, and the worker keeps serving the queue."""
    from worker.pipeline.models import get_diarization_pipeline

    try:
        get_diarization_pipeline(settings, settings.hf_token)
    except AppError as exc:
        logger.warning(
            "model warm-up skipped",
            extra={"stage": "startup", "error_code": exc.code.value, "detail": exc.message},
        )
    except Exception:
        logger.exception("model warm-up failed", extra={"stage": "startup"})


def main() -> None:
    configure_logging()
    settings = get_settings()
    redis = get_redis()

    redis.ping()
    logger.info(
        "worker starting",
        extra={"stage": "startup", "queue": settings.queue_name, "device": settings.device},
    )

    # Before taking new work: whatever was running when this process last died can never be
    # resumed, so it must not stay stuck on a progress bar forever (T8.2).
    orphaned = recover_orphaned_jobs(redis)
    if orphaned:
        logger.warning(
            "orphaned jobs failed after restart",
            extra={"stage": "startup", "count": len(orphaned)},
        )

    warm_up_models(settings)

    queue = Queue(settings.queue_name, connection=redis)
    worker = SimpleWorker(
        [queue],
        connection=redis,
        default_worker_ttl=settings.worker_heartbeat_seconds,
    )
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
