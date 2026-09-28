import logging

from redis import Redis
from rq import Queue
from rq.exceptions import InvalidJobOperation, NoSuchJobError
from rq.job import Job as RqJob
from rq.job import JobStatus as RqJobStatus

from app.core.config import Settings

logger = logging.getLogger(__name__)

# Import path resolved inside the worker process, not here — the API never loads the pipeline.
WORKER_ENTRYPOINT = "worker.jobs.process_job"

# RQ states in which the job has not started yet: it is still waiting for a worker.
PENDING_RQ_STATUSES = frozenset({RqJobStatus.QUEUED, RqJobStatus.DEFERRED, RqJobStatus.SCHEDULED})


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


def fetch_rq_job(redis: Redis, job_id: str) -> RqJob | None:
    """The RQ record for our job, or None once RQ has dropped it."""
    try:
        return RqJob.fetch(job_id, connection=redis)
    except NoSuchJobError:
        return None


def is_pending_in_queue(redis: Redis, job_id: str) -> bool:
    """True while the job is still waiting to be picked up by a worker."""
    rq_job = fetch_rq_job(redis, job_id)
    if rq_job is None:
        return False
    return rq_job.get_status(refresh=False) in PENDING_RQ_STATUSES


def cancel_job(redis: Redis, job_id: str) -> None:
    """Best-effort removal of the RQ record so the job is never picked up again.

    A job already running in a worker process cannot be interrupted — SimpleWorker executes
    it in-process. Deleting the record only guarantees it will not be retried or restarted.
    """
    rq_job = fetch_rq_job(redis, job_id)
    if rq_job is None:
        return
    try:
        rq_job.cancel()
    except InvalidJobOperation:
        # Already started, finished or canceled — nothing left to cancel.
        pass
    rq_job.delete()
    logger.info("rq job removed", extra={"job_id": job_id})
