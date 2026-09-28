import logging

from redis import Redis
from rq import Queue

from app.core.config import Settings

logger = logging.getLogger(__name__)

# Import path resolved inside the worker process, not here — the API never loads the pipeline.
WORKER_ENTRYPOINT = "worker.jobs.process_job"


def enqueue_job(redis: Redis, settings: Settings, job_id: str) -> None:
    queue = Queue(settings.queue_name, connection=redis)
    queue.enqueue(
        WORKER_ENTRYPOINT,
        job_id,
        job_id=job_id,
        job_timeout=settings.job_timeout_seconds,
        result_ttl=0,
    )
    logger.info("job enqueued", extra={"job_id": job_id, "queue": settings.queue_name})
