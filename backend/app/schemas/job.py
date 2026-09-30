from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.core.errors import ErrorCode
from app.schemas.audio import AudioMetadata


class JobStatus(StrEnum):
    """Job state machine from SPEC.md §5."""

    QUEUED = "QUEUED"
    PREPARING_AUDIO = "PREPARING_AUDIO"
    DIARIZING = "DIARIZING"
    TRANSCRIBING = "TRANSCRIBING"
    ALIGNING = "ALIGNING"
    GENERATING_EXPORTS = "GENERATING_EXPORTS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


TERMINAL_STATUSES = frozenset({JobStatus.COMPLETED, JobStatus.FAILED})
# Statuses in which the worker is not doing anything yet or any more, so entering one of
# them must not count as "processing started".
RESTING_STATUSES = TERMINAL_STATUSES | {JobStatus.QUEUED}

# Progress range owned by each processing stage (SPEC.md §5).
STAGE_PROGRESS_RANGE: dict[JobStatus, tuple[int, int]] = {
    JobStatus.QUEUED: (0, 0),
    JobStatus.PREPARING_AUDIO: (0, 5),
    JobStatus.DIARIZING: (5, 30),
    JobStatus.TRANSCRIBING: (30, 85),
    JobStatus.ALIGNING: (85, 92),
    JobStatus.GENERATING_EXPORTS: (92, 100),
    JobStatus.COMPLETED: (100, 100),
}


class JobSource(BaseModel):
    filename: str
    size_bytes: int
    content_type: str | None = None


class Job(BaseModel):
    """Canonical job record. Serialized as JSON into Redis."""

    job_id: str
    status: JobStatus = JobStatus.QUEUED
    progress: int = Field(default=0, ge=0, le=100)
    current_stage: JobStatus = JobStatus.QUEUED
    message: str | None = None
    error_code: ErrorCode | None = None
    source: JobSource
    # Filled in by the audio preparation stage; None until the job reaches it.
    audio: AudioMetadata | None = None
    speaker_count: int | None = None
    created_at: datetime
    updated_at: datetime
    # When the worker actually picked the job up, and when it stopped. Both are None for
    # records written before these fields existed, so consumers must handle their absence.
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def processing_seconds(self) -> float | None:
        """How long the worker spent on this job, excluding time spent queued.

        None while the job is still running, and for jobs that failed before they started.
        """
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()


class JobCreatedResponse(BaseModel):
    job_id: str
    status: JobStatus
