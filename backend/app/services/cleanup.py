"""Manual job removal (T8.3).

Automatic TTL-based cleanup stays post-MVP; the user deletes what they no longer need.
"""

import logging
import shutil

from redis import Redis

from app.services.job_store import JobStore
from app.services.queue import cancel_job
from app.services.storage import JobStorage

logger = logging.getLogger(__name__)


def delete_job(redis: Redis, store: JobStore, storage: JobStorage, job_id: str) -> None:
    """Drop the queue entry, the Redis record and everything on disk.

    Order matters: the queue entry goes first so a queued job is not picked up between the
    record and the files disappearing. A job already running in the worker cannot be
    interrupted — it notices the record is gone on its next update and stops.
    """
    store.require(job_id)

    cancel_job(redis, job_id)
    store.delete(job_id)
    shutil.rmtree(storage.job_dir(job_id), ignore_errors=True)

    logger.info("job deleted", extra={"job_id": job_id})
