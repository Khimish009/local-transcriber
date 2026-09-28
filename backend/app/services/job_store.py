import logging
import uuid
from datetime import UTC, datetime

from redis import Redis

from app.core.errors import AppError, ErrorCode
from app.schemas.job import Job, JobSource, JobStatus

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
        if status is not None:
            job.status = status
            job.current_stage = status
        if progress is not None:
            job.progress = max(0, min(100, progress))
        if message is not None:
            job.message = message
        if error_code is not None:
            job.error_code = error_code
        job.updated_at = _now()
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
