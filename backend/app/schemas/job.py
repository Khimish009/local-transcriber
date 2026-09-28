from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.core.errors import ErrorCode


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
    speaker_count: int | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


class JobCreatedResponse(BaseModel):
    job_id: str
    status: JobStatus
