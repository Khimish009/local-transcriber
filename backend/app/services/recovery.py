"""Recovery of jobs orphaned by a worker crash (T8.2).

`SimpleWorker` runs jobs inside the worker process, so a hard crash in inference (an OOM
kill, a segfault in a native ML library) takes the whole worker down. Docker restarts it,
but the job record stays frozen in whatever stage it reached and the UI shows a progress
bar that will never move again.

This module closes that gap: at worker startup, every non-terminal job that is not waiting
in the queue is marked FAILED with `WORKER_CRASHED`.

The job is failed, not requeued, on purpose. A crash caused by the recording itself (too
long for the available memory, a codec that kills a native library) would repeat on every
restart, and an automatic retry would turn it into an endless crash loop.

Assumes a single worker process, which is what SPEC.md §2 prescribes ("одна job
обрабатывается одним worker"). With several workers this check would have to be restricted
to the jobs of the worker that just died.
"""

import logging

from redis import Redis

from app.core.errors import ErrorCode
from app.services.job_store import JobStore
from app.services.queue import cancel_job, is_pending_in_queue

logger = logging.getLogger(__name__)

RECOVERY_MESSAGE = "Обработка прервана: worker перезапустился. Запустите задачу заново."


def recover_orphaned_jobs(redis: Redis) -> list[str]:
    """Fail every unfinished job that no longer has a worker behind it. Returns their ids."""
    store = JobStore(redis)
    recovered: list[str] = []

    # Materialized before the loop: failing a job rewrites its key, and mutating the key
    # space during a scan is not something to rely on.
    for job in list(store.iter_jobs()):
        job_id = job.job_id
        if job.is_terminal:
            continue
        if is_pending_in_queue(redis, job_id):
            continue  # still waiting for a worker — it will run normally

        store.fail(job_id, message=RECOVERY_MESSAGE, error_code=ErrorCode.WORKER_CRASHED)
        cancel_job(redis, job_id)
        recovered.append(job_id)
        logger.warning(
            "orphaned job recovered",
            extra={"job_id": job_id, "stage": job.status.value, "progress": job.progress},
        )

    return recovered
