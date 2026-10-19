from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, computed_field

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

    # Processing time is computed here, once, and shipped with every serialization, so the
    # UI never re-derives it. Running jobs are measured against the server clock: the
    # browser only adds the time elapsed since it received the record, which keeps a skewed
    # client clock out of the figure. See docs/processing-time.md.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def processing_seconds(self) -> float | None:
        """Seconds the worker has spent on the job so far, or None when unknown."""
        return self._processing_time()[0]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def processing_includes_queue(self) -> bool:
        """True when `processing_seconds` is a legacy estimate that includes queue time."""
        return self._processing_time()[1]

    def _processing_time(self) -> tuple[float | None, bool]:
        if self.started_at is not None:
            end = self.finished_at or datetime.now(UTC)
            return max(0.0, (end - self.started_at).total_seconds()), False
        if self.finished_at is not None:
            return None, False  # failed while still queued: the worker never touched it
        if self.is_terminal:
            # Written before the timestamps existed: created → updated is the only span
            # left, and it also covers upload and time spent waiting in the queue.
            seconds = (self.updated_at - self.created_at).total_seconds()
            return (seconds, True) if seconds > 0 else (None, False)
        return None, False  # queued, not picked up yet


# Computed on serialization, never stored in Redis.
DERIVED_FIELDS = frozenset({"processing_seconds", "processing_includes_queue"})


class JobCreatedResponse(BaseModel):
    job_id: str
    status: JobStatus
