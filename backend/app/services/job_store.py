import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

from redis import Redis

from app.core.errors import AppError, ErrorCode
from app.schemas.job import RESTING_STATUSES, TERMINAL_STATUSES, Job, JobSource, JobStatus

logger = logging.getLogger(__name__)

JOB_KEY_PREFIX = "job:"
EVENTS_CHANNEL_PREFIX = "job-events:"


def job_key(job_id: str) -> str:
    return f"{JOB_KEY_PREFIX}{job_id}"


def events_channel(job_id: str) -> str:
    return f"{EVENTS_CHANNEL_PREFIX}{job_id}"


def _now() -> datetime:
    return datetime.now(UTC)


class JobStore:
    """Redis-backed job records. Shared by the API and the worker."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def create(self, source: JobSource, speaker_count: int | None = None) -> Job:
        now = _now()
        job = Job(
            job_id=str(uuid.uuid4()),
            source=source,
            speaker_count=speaker_count,
            created_at=now,
            updated_at=now,
        )
        self._persist(job)
        return job

    def replace(self, job: Job) -> Job:
        """Persist a job record modified by the caller (e.g. after the upload is stored)."""
        job.updated_at = _now()
        self._persist(job)
        return job

    def get(self, job_id: str) -> Job | None:
        raw = self._redis.get(job_key(job_id))
        if raw is None:
            return None
        return Job.model_validate_json(raw)

    def iter_jobs(self) -> Iterator[Job]:
        """Every stored job, in no particular order.

        Scans the key space instead of keeping an index. A local instance holds tens of
        jobs, and a sorted-set index would have to be backfilled for every record written
        before it existed — not worth it at this scale. Records that no longer parse are
        skipped rather than crashing the listing.
        """
        for key in self._redis.scan_iter(match=f"{JOB_KEY_PREFIX}*", count=100):
            raw = self._redis.get(key)
            if raw is None:
                continue  # expired or deleted between the scan and the read
            try:
                yield Job.model_validate_json(raw)
            except ValueError:
                logger.warning("unreadable job record", extra={"key": str(key)})

    def list_jobs(self, limit: int | None = None) -> list[Job]:
        """Jobs newest first — the order the job list is shown in."""
        jobs = sorted(self.iter_jobs(), key=lambda job: job.created_at, reverse=True)
        return jobs[:limit] if limit is not None else jobs

    def require(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job is None:
            raise AppError(ErrorCode.JOB_NOT_FOUND, "Job not found", status_code=404)
        return job

    def update(
        self,
        job_id: str,
        *,
        status: JobStatus | None = None,
        progress: int | None = None,
        message: str | None = None,
        error_code: ErrorCode | None = None,
    ) -> Job:
        job = self.require(job_id)
        now = _now()
        if status is not None:
            job.status = status
            job.current_stage = status
            # Stamped here rather than in the worker so no code path can forget them:
            # every transition goes through this method, including a failure recorded by
            # crash recovery.
            if job.started_at is None and status not in RESTING_STATUSES:
                job.started_at = now
            if status in TERMINAL_STATUSES:
                job.finished_at = now
        if progress is not None:
            job.progress = max(0, min(100, progress))
        if message is not None:
            job.message = message
        if error_code is not None:
            job.error_code = error_code
        job.updated_at = now
        self._persist(job)
        logger.info(
            "job updated",
            extra={
                "job_id": job.job_id,
                "stage": job.status.value,
                "progress": job.progress,
            },
        )
        return job

    def fail(self, job_id: str, message: str, error_code: ErrorCode | None = None) -> Job:
        return self.update(job_id, status=JobStatus.FAILED, error_code=error_code, message=message)

    def delete(self, job_id: str) -> None:
        self._redis.delete(job_key(job_id))

    def _persist(self, job: Job) -> None:
        payload = job.model_dump_json()
        self._redis.set(job_key(job.job_id), payload)
        # Best-effort notification for SSE subscribers; polling remains the fallback.
        self._redis.publish(events_channel(job.job_id), payload)


def get_job_store(redis: Redis) -> JobStore:
    return JobStore(redis)
